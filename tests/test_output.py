"""Drive the real output handler with a minimal AstrBot test double."""
import asyncio
import importlib.util
import sys
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
        "astrbot.api.star": SimpleNamespace(Context=object, Star=object),
        "astrbot.api.message_components": SimpleNamespace(
            Image=SimpleNamespace(fromBytes=lambda data: ("image", data)),
            Plain=lambda text: ("plain", text),
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
    assert asyncio.run(output(ai)) == ([[("image", b"image"), ("plain", "实际占卜原文")]], 1, 0)
    assert asyncio.run(output(ai, image=TimeoutError())) == ([("html", "rendered")], 1, 1)
    assert asyncio.run(output(ai, image=TimeoutError(), html=None)) == ([("plain", "实际占卜原文")], 1, 1)
