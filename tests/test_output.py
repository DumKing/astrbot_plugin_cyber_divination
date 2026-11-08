"""Drive the real output handler with a minimal AstrBot test double."""
import asyncio
import importlib.util
import sys
from datetime import date
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


def load_plugin():
    # AstrBot can load plugins under different directory names.
    root = Path(__file__).parents[1]
    name = "_cyber_divination_test"
    package = ModuleType(name)
    package.__path__ = [str(root)]
    spec = importlib.util.spec_from_file_location(f"{name}.main", root / "main.py")
    plugin = importlib.util.module_from_spec(spec)

    def command_group(*args, **kwargs):
        def decorate(handler):
            handler.command = lambda *a, **k: lambda target: target
            return handler
        return decorate

    class Star:
        def __init__(self, context):
            self.context = context

    modules = {
        name: package,
        "astrbot": ModuleType("astrbot"),
        "astrbot.api": SimpleNamespace(AstrBotConfig=dict, logger=Mock()),
        "astrbot.api.event": SimpleNamespace(
            AstrMessageEvent=object,
            filter=SimpleNamespace(
                command_group=command_group,
                EventMessageType=SimpleNamespace(ALL=0),
                event_message_type=lambda *a: lambda target: target,
            ),
        ),
        "astrbot.api.star": SimpleNamespace(Context=object, Star=Star),
        "astrbot.api.message_components": SimpleNamespace(
            Image=SimpleNamespace(fromBytes=lambda data: ("image", data)),
            Reply=lambda *, id: ("reply", id),
            At=lambda *, qq: ("at", qq),
        ),
    }
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(plugin)
        return plugin.CyberDivination


async def output(config, *, image=b"image", html="rendered"):
    plugin_type = load_plugin()
    plugin = object.__new__(plugin_type)
    plugin.config = config
    plugin._generate_ai_card = AsyncMock(return_value=image)
    plugin.html_render = AsyncMock(return_value=html)
    if isinstance(image, Exception):
        plugin._generate_ai_card.side_effect = image
    if isinstance(html, Exception):
        plugin.html_render.side_effect = html
    event = SimpleNamespace(
        chain_result=lambda chain: chain,
        image_result=lambda url: ("html", url),
        plain_result=lambda text: ("plain", text),
    )
    result = [item async for item in plugin._finish(event, "每日运势", "实际占卜原文")]
    return result, plugin._generate_ai_card.await_count, plugin.html_render.await_count


def test_output_modes_and_fallback():
    assert asyncio.run(output({})) == ([("plain", "实际占卜原文")], 0, 0)
    assert asyncio.run(output({"use_image": True})) == ([("html", "rendered")], 0, 1)
    ai = {"use_image": True, "image_mode": "ai"}
    assert asyncio.run(output(ai)) == ([[("image", b"image")]], 1, 0)
    assert asyncio.run(output(ai, image=TimeoutError())) == ([("html", "rendered")], 1, 1)
    assert asyncio.run(output(ai, image=TimeoutError(), html=None)) == ([("plain", "实际占卜原文")], 1, 1)



def test_help_command_and_bare_alias_send_the_bundled_image():
    plugin = object.__new__(load_plugin())
    plugin.config = {"keyword_trigger_enabled": False, "use_image": False}
    event = SimpleNamespace(
        get_message_str=lambda: " 占卜帮助 ",
        is_at_or_wake_command=False,
        set_extra=Mock(),
        stop_event=Mock(),
        image_result=lambda path: ("image", Path(path)),
        plain_result=Mock(),
    )

    async def collect(handler):
        return [result async for result in handler(event)]

    expected = [("image", Path(__file__).resolve().parents[1] / "assets" / "help.png")]
    assert expected[0][1].is_file()
    assert asyncio.run(collect(plugin.help_cmd)) == expected
    assert asyncio.run(collect(plugin.keyword_trigger)) == expected
    event.plain_result.assert_not_called()

    event.get_message_str = lambda: "塔罗"
    assert asyncio.run(collect(plugin.keyword_trigger)) == []



def test_divination_replies_address_the_original_sender():
    plugin = object.__new__(load_plugin())
    plugin.config = {"use_image": True, "image_mode": "ai"}
    plugin._generate_ai_card = AsyncMock(return_value=b"card")
    plugin.html_render = AsyncMock(return_value="rendered")
    event = SimpleNamespace(
        message_obj=SimpleNamespace(message_id="message-123"),
        get_group_id=lambda: "group-456",
        get_sender_id=lambda: "sender-789",
        chain_result=lambda chain: SimpleNamespace(chain=chain),
        image_result=lambda url: SimpleNamespace(chain=[("html", url)]),
        plain_result=lambda text: SimpleNamespace(chain=[("plain", text)]),
    )

    async def collect():
        return [item.chain async for item in plugin._run(event, "每日运势", "", "占卜原文")]

    group = [("reply", "message-123"), ("at", "sender-789")]
    assert asyncio.run(collect()) == [group + [("image", b"card")]]
    plugin._generate_ai_card.side_effect = TimeoutError()
    assert asyncio.run(collect()) == [group + [("html", "rendered")]]
    plugin.html_render.return_value = None
    assert asyncio.run(collect()) == [group + [("plain", "占卜原文")]]

    event.get_group_id = lambda: ""
    assert asyncio.run(collect()) == [[("reply", "message-123"), ("plain", "占卜原文")]]
    event.message_obj.message_id = None
    assert asyncio.run(collect()) == [[("plain", "占卜原文")]]



def test_daily_ai_image_cache_reuses_success_by_sender_and_date():
    plugin = load_plugin()(None, {})
    generate = AsyncMock(side_effect=[b"first", b"second", b"tomorrow", TimeoutError(), b"retry"])
    plugin._generate_ai_card = generate
    alice = SimpleNamespace(get_sender_id=lambda: "alice")
    bob = SimpleNamespace(get_sender_id=lambda: "bob")
    today = {"daily": {"date": date(2026, 11, 8)}}
    tomorrow = {"daily": {"date": date(2026, 11, 9)}}
    failure = {"daily": {"date": date(2026, 11, 10)}}

    async def fetch(event, draw):
        return await plugin._get_ai_image(event, "每日运势", draw, None)

    async def verify():
        assert await asyncio.gather(fetch(alice, today), fetch(alice, today)) == [b"first", b"first"]
        assert generate.await_count == 1
        assert await fetch(bob, today) == b"second"
        assert await fetch(alice, tomorrow) == b"tomorrow"
        try:
            await fetch(alice, failure)
        except TimeoutError:
            pass
        else:
            raise AssertionError("expected generation to fail")
        assert await fetch(alice, failure) == b"retry"
        assert generate.await_count == 5

    asyncio.run(verify())
