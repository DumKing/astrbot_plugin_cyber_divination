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
            MessageChain=list,
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
            Plain=lambda text: ("plain", text),
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


def test_image_acknowledgement_is_independent_and_precedes_generation():
    plugin = load_plugin()(None, {
        "use_image": True,
        "image_mode": "ai",
        "image_ack_text_enabled": True,
        "image_ack_text": "收到，正在生成{title}",
    })
    reaction = AsyncMock(side_effect=RuntimeError("unsupported"))
    send = AsyncMock()
    event = SimpleNamespace(
        message_obj=SimpleNamespace(message_id="42"),
        get_sender_id=lambda: "sender",
        bot=SimpleNamespace(call_action=reaction),
        send=send,
        chain_result=lambda chain: chain,
        plain_result=lambda text: ("plain", text),
    )

    async def generate(*args):
        reaction.assert_awaited_once_with(
            "set_msg_emoji_like", message_id=42, emoji_id="124", set=True,
        )
        send.assert_awaited_once_with([("reply", "42"), ("plain", "收到，正在生成每日运势")])
        return b"image"

    plugin._generate_ai_card = AsyncMock(side_effect=generate)

    async def verify():
        card_data = {"daily": {"date": date(2026, 11, 8)}}

        async def collect():
            return [item async for item in plugin._finish(
                event, "每日运势", "占卜原文", card_data=card_data,
            )]

        assert await collect() == [[("image", b"image")]]
        assert await collect() == [[("image", b"image")]]
        plugin.config["use_image"] = False
        assert await collect() == [("plain", "占卜原文")]
        plugin._generate_ai_card.assert_awaited_once()
        reaction.assert_awaited_once()
        send.assert_awaited_once()

        plugin.config["image_ack_reaction_enabled"] = False
        send.reset_mock()
        await plugin._acknowledge_image(event, "周易六爻")
        reaction.assert_awaited_once()
        send.assert_awaited_once_with([("reply", "42"), ("plain", "收到，正在生成周易六爻")])

    asyncio.run(verify())


def test_prefix_question_reaches_ai_image_only_when_enabled():
    plugin = load_plugin()(None, {"use_image": True, "image_mode": "ai"})
    extras = {}
    event = SimpleNamespace(
        get_message_str=lambda: "六爻 今日财运",
        is_at_or_wake_command=False,
        get_extra=extras.get,
        set_extra=extras.__setitem__,
        stop_event=Mock(),
        chain_result=lambda chain: chain,
    )
    plugin.config.update(keyword_trigger_enabled=True, keyword_trigger_mode="starts_with")
    plugin._generate_ai_card = AsyncMock(return_value=b"card")

    async def command(event):
        async for result in plugin._finish(
            event, "周易六爻", "占卜原文", question=plugin._remainder(event),
        ):
            yield result

    plugin.zhouyi_cmd = command

    async def verify():
        assert [item async for item in plugin.keyword_trigger(event)] == [[("image", b"card")]]
        assert plugin._generate_ai_card.await_args.args[1] == {}
        plugin.config["ai_image_include_question"] = True
        assert [item async for item in plugin.keyword_trigger(event)] == [[("image", b"card")]]
        assert plugin._generate_ai_card.await_args.args[1] == {"question": "今日财运"}

    asyncio.run(verify())


def test_clearing_history_evicts_only_the_senders_daily_image():
    plugin = load_plugin()(None, {})
    plugin.put_kv_data = AsyncMock()
    plugin._generate_ai_card = AsyncMock(return_value=b"fresh")
    day = date.today()
    plugin._daily_images.update({(day, "alice"): b"old", (day, "bob"): b"other"})
    event = SimpleNamespace(
        get_extra=lambda key: None,
        get_message_str=lambda: "/占卜 历史 清除",
        unified_msg_origin="group",
        get_sender_id=lambda: "alice",
        plain_result=lambda text: text,
    )

    async def verify():
        result = [item async for item in plugin.history_cmd(event)]
        assert result == ["占卜历史和你的今日运势图片缓存已清除。"]
        plugin.put_kv_data.assert_awaited_once_with("history_group", [])
        assert dict(plugin._daily_images) == {(day, "bob"): b"other"}
        assert await plugin._get_ai_image(event, "每日运势", {"daily": {"date": day}}, None) == b"fresh"
        plugin._generate_ai_card.assert_awaited_once()

    asyncio.run(verify())


def test_daily_image_quota_counts_per_sender_and_bypasses_whitelisted_umo():
    plugin = load_plugin()(None, {
        "use_image": True, "image_mode": "ai", "ai_image_daily_limit": 1,
        "ai_image_daily_limit_enabled": True,
        "ai_image_umo_whitelist": ["onebot:GroupMessage:vip"],
        "ai_image_umo_blacklist": ["onebot:GroupMessage:vip", "onebot:GroupMessage:blocked"],
    })
    records = {}

    async def get(key, default):
        return records.get(key, default)

    async def put(key, value):
        records[key] = value

    plugin.get_kv_data = get
    plugin.put_kv_data = put
    plugin._generate_ai_card = AsyncMock(return_value=b"ai")
    plugin.html_render = AsyncMock(return_value="html")

    def event(sender, group):
        return SimpleNamespace(
            get_sender_id=lambda: sender,
            unified_msg_origin=f"onebot:GroupMessage:{group}",
            chain_result=lambda chain: chain,
            image_result=lambda url: url,
        )

    async def finish(message, data=None):
        return [result async for result in plugin._finish(
            message, "每日运势" if data else "周易六爻", "", card_data=data,
        )]

    async def verify():
        from core.daily import draw_daily

        daily = {"daily": draw_daily(date.today(), "alice"), "uid": "alice"}
        ai = [[("image", b"ai")]]
        alice = event("alice", "regular")
        assert await finish(alice, daily) == ai
        assert await finish(alice, daily) == ai
        assert await finish(event("alice", "another"), {}) == ["html"]
        assert await finish(event("bob", "regular")) == ai
        assert await finish(event("alice", "vip")) == ai
        assert await finish(event("alice", "vip-extra")) == ["html"]
        assert records["ai_image_quota_user_alice"]["count"] == 1
        records["ai_image_quota_user_alice"]["date"] = "2000-01-01"
        assert await finish(alice) == ai
        assert plugin._generate_ai_card.await_count == 4

        plugin.config["ai_image_umo_default"] = "unlimited"
        plugin.config["ai_image_daily_limit"] = 0
        blocked = event("alice", "blocked")
        assert await finish(blocked, daily) == ["html"]
        assert await finish(event("charlie", "blocked")) == ["html"]
        assert "ai_image_quota_user_charlie" not in records
        assert plugin._generate_ai_card.await_count == 4
        assert await finish(event("alice", "another")) == ai
        assert await finish(event("alice", "vip")) == ai
        plugin.config["ai_image_daily_limit_enabled"] = False
        assert await finish(blocked) == ai
        assert records["ai_image_quota_user_alice"]["count"] == 1

    asyncio.run(verify())


def test_new_commands_reach_the_shared_reply_and_history_flow():
    plugin = load_plugin()(None, {
        "keyword_trigger_enabled": True, "keyword_trigger_mode": "starts_with",
    })
    plugin._record_history = AsyncMock()
    extras = {}
    event = SimpleNamespace(
        unified_msg_origin="test:GroupMessage:group",
        is_at_or_wake_command=False,
        message_obj=SimpleNamespace(message_id="42"),
        get_sender_id=lambda: "sender",
        get_group_id=lambda: "group",
        get_extra=extras.get,
        set_extra=extras.__setitem__,
        stop_event=Mock(),
        plain_result=lambda text: SimpleNamespace(chain=[text]),
    )
    cases = (
        ("小六壬 2026-11-09 12:00 工作", "小六壬"),
        ("梅花 1 1 工作", "梅花易数"),
        ("雷诺曼 单张 工作", "雷诺曼"),
        ("大六壬 2026-11-09 12:00", "大六壬"),
        ("太乙 时计 2026-11-09 12:00", "太乙神数"),
        ("八字 2000-08-16 12:00", "八字排盘"),
        ("紫微 2000-08-16 12:00 女", "紫微斗数"),
    )

    async def verify():
        for message, title in cases:
            event.get_message_str = lambda: message
            result = [item async for item in plugin.keyword_trigger(event)]
            assert len(result) == 1
            assert result[0].chain[:2] == [("reply", "42"), ("at", "sender")]
            assert title in result[0].chain[2]
            assert plugin._record_history.await_args.args[1] == title
        event.get_message_str = lambda: "/占卜 紫微 2000-02-30 12:00 女"
        result = [item async for item in plugin.ziwei_cmd(event)]
        assert "用法：/占卜 紫微" in result[0].chain[-1]
        assert plugin._record_history.await_count == len(cases)

    asyncio.run(verify())


def test_daily_reads_only_the_triggering_users_qq_birth_profile():
    plugin = load_plugin()(None, {})
    action = AsyncMock(return_value={
        "birthday_year": 2000, "birthday_month": 8, "birthday_day": 16,
        "sex": "female", "nickname": "never returned",
    })
    event = SimpleNamespace(get_sender_id=lambda: "123", bot=SimpleNamespace(call_action=action))

    async def verify():
        assert await plugin._daily_birth_profile(event) == (date(2000, 8, 16), "女")
        action.assert_awaited_once_with("get_stranger_info", user_id=123)
        action.return_value = {"data": {"birthday_year": 0, "sex": "unknown"}}
        assert await plugin._daily_birth_profile(event) == (None, "")
        action.side_effect = RuntimeError("unsupported")
        assert await plugin._daily_birth_profile(event) == (None, "")

    asyncio.run(verify())


def test_extended_ai_cards_use_their_own_reference_without_a_text_endpoint():
    from core.extended import METHODS

    plugin_type = load_plugin()
    plugin = plugin_type(None, {
        "use_image": True, "image_mode": "ai",
        "ai_image_reference_endpoint": "https://example.test/v1/images/edits",
    })
    package = plugin_type.__module__.rsplit(".", 1)[0]
    generate = AsyncMock(return_value=b"image")
    event = SimpleNamespace(chain_result=lambda parts: parts)

    async def verify():
        for kind, (title, _, _, _) in METHODS.items():
            result = [item async for item in plugin._finish(
                event, title, "", card_data={"extended_rows": [("示例字段", "本次数据")]},
            )]
            assert result == [[("image", b"image")]], kind
            call = generate.await_args
            assert call.kwargs["endpoint"] == "https://example.test/v1/images/edits"
            assert call.kwargs["reference"].name == f"{kind}.jpg"
            assert call.kwargs["reference"].is_file()
            assert "本次数据" in call.args[0]

    from core.image_api import build_prompt
    from core.image_styles import resolve_style

    with patch.dict(sys.modules, {
        f"{package}.core.image_api": SimpleNamespace(build_prompt=build_prompt, generate_image=generate),
        f"{package}.core.image_styles": SimpleNamespace(resolve_style=resolve_style),
    }):
        asyncio.run(verify())


def test_daily_ai_notes_keep_original_rows_and_skip_unmatched_notes():
    plugin = load_plugin()(None, {})
    plugin._llm_text = AsyncMock(return_value='{"小六壬":"先核实事实。","其他":"不采用"}')
    original = [
        {"title": "小六壬", "rows": [("时宫", "赤口")]},
        {"title": "梅花易数", "rows": [("本卦", "履")]},
    ]
    updated = asyncio.run(plugin._daily_panel_notes(SimpleNamespace(), original))
    assert updated == [{**original[0], "ai_note": "先核实事实。"}, original[1]]
    assert "ai_note" not in original[0]
    assert "赤口" in plugin._llm_text.await_args.args[2]
    plugin._llm_text.return_value = "无效响应"
    assert asyncio.run(plugin._daily_panel_notes(SimpleNamespace(), original)) == original
