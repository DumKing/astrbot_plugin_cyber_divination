"""Exercise the Images API contract without sending external requests."""
import asyncio
import base64
import json
import random
import unittest
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from PIL import Image

from core import daily, qimen, tarot, zhouyi
from core.image_api import ImageAPIError, build_prompt, generate_image
from core.image_styles import DEFAULT_STYLES, resolve_style
from core.keywords import match_keyword


def test_keyword_modes_and_overrides():
    assert match_keyword("塔罗", "exact", {}) == ("tarot", "")
    assert match_keyword("塔罗 三张", "exact", {}) is None
    assert match_keyword("塔罗牌：三张", "starts_with", {}) == ("tarot", "三张")
    assert match_keyword("想问奇门遁甲，出行", "contains", {}) == ("qimen", "出行")
    assert match_keyword("/占卜 塔罗", "contains", {}) is None
    assert match_keyword("塔罗", "contains", {"tarot": []}) is None
    assert match_keyword("问牌：感情", "starts_with", {"tarot": ["问牌"]}) == ("tarot", "感情")


def test_five_defaults_and_structured_data():
    schema = json.loads((Path(__file__).parents[1] / "_conf_schema.json").read_text())
    dt = datetime(2026, 11, 8, 13, 30)
    cast = zhouyi.cast(random.Random(5), day_index=0)
    drawn = daily.draw_daily(date(2026, 11, 8), "private-user")
    cards = tarot.draw_three(random.Random(7))
    fixtures = {
        "zhouyi": ({}, "lines_bottom_to_top", cast.lines),
        "lingqian": ({"number": 35, "poem": "真实签诗"}, "poem", "真实签诗"),
        "tarot": ({"cards": cards}, "cards", [
            {"position": p, "name": n, "reversed": r, "meaning": m}
            for p, n, r, m in cards
        ]),
        "qimen": ({"pan": qimen.make_pan(dt)}, "grid_order", [[4, 9, 2], [3, 5, 7], [8, 1, 6]]),
        "daily": ({"daily": drawn, "uid": "private-user"}, "date", "2026-11-08"),
    }
    assert len(set(DEFAULT_STYLES.values())) == 5
    for kind, (data, field, expected) in fixtures.items():
        style = DEFAULT_STYLES[kind]
        assert schema[f"ai_prompt_{kind}"]["default"] == style
        prompt = build_prompt(style, kind, "主题", {**data, "question": "private-question"}, cast)
        assert prompt.startswith(style)
        assert "private-" not in prompt
        payload = json.loads(prompt.split("构图参考数据：", 1)[1].splitlines()[0])
        assert payload[field] == expected


class ImageAPITests(unittest.IsolatedAsyncioTestCase):
    endpoint = "https://images.example.test/v1/images/generations"

    def setUp(self):
        buffer = BytesIO()
        Image.new("RGB", (8, 8)).save(buffer, "PNG")
        self.png = buffer.getvalue()
        self.session = MagicMock()
        factory = MagicMock()
        factory.return_value.__aenter__.return_value = self.session
        patcher = patch("core.image_api.aiohttp.ClientSession", factory)
        patcher.start()
        self.addCleanup(patcher.stop)

    def respond(self, method, body, *, status=200, content_type="application/json"):
        if isinstance(body, dict):
            body = json.dumps(body).encode()
        response = MagicMock(status=status, content_type=content_type, content_length=len(body))

        async def chunks(_):
            yield body

        response.content.iter_chunked = chunks
        getattr(self.session, method).return_value.__aenter__.return_value = response

    async def generate(self, **kwargs):
        return await generate_image("提示", endpoint=self.endpoint, model="test", **kwargs)

    async def test_base64_response_and_bearer_auth(self):
        self.respond("post", {"data": [{"b64_json": base64.b64encode(self.png).decode()}]})
        self.assertEqual(await self.generate(api_key="key", size="1024x1536"), self.png)
        args = self.session.post.call_args.kwargs
        self.assertEqual(args["json"], {"model": "test", "prompt": "提示", "n": 1, "size": "1024x1536"})
        self.assertEqual(args["headers"], {"Authorization": "Bearer key"})
        self.assertFalse(args["allow_redirects"])

    async def test_cdn_download_without_credentials(self):
        self.respond("post", {"data": [{"url": "https://cdn.example.test/img?sig=abc"}]})
        self.respond("get", self.png, content_type="image/png")
        self.assertEqual(
            await self.generate(api_key="key", auth_header="api-key", download_origin="https://cdn.example.test"),
            self.png,
        )
        self.assertEqual(self.session.post.call_args.kwargs["headers"], {"api-key": "key"})
        self.assertEqual(self.session.get.call_args.kwargs, {"allow_redirects": False})

    async def test_raw_image(self):
        self.respond("post", self.png, content_type="image/png")
        self.assertEqual(await self.generate(), self.png)
        self.session.get.assert_not_called()

    async def test_disallows_unknown_origin_and_redirects(self):
        self.respond("post", {"data": [{"url": "http://169.254.169.254/file"}]})
        with self.assertRaises(ImageAPIError):
            await self.generate()
        self.session.get.assert_not_called()
        self.respond("post", {"data": [{"url": "https://images.example.test/file"}]})
        self.respond("get", b"", status=302)
        with self.assertRaisesRegex(ImageAPIError, "302"):
            await self.generate()

    async def test_rejects_auth_failure_invalid_and_large_images(self):
        self.respond("post", {"error": "secret response"}, status=401)
        with self.assertRaisesRegex(ImageAPIError, "^HTTP 401$"):
            await self.generate()
        self.respond("post", {"data": []})
        with self.assertRaises(ImageAPIError):
            await self.generate()
        self.respond("post", b"not an image", content_type="image/png")
        with self.assertRaises(OSError):
            await self.generate()
        self.respond("post", self.png, content_type="image/png")
        with patch("core.image_api.MAX_IMAGE_BYTES", 5), self.assertRaises(ImageAPIError):
            await self.generate()

    async def test_total_timeout(self):
        async def stall():
            await asyncio.sleep(60)

        self.session.post.return_value.__aenter__ = AsyncMock(side_effect=stall)
        with self.assertRaises(asyncio.TimeoutError):
            await self.generate(timeout=1)


def test_prompt_selection_preserves_custom_text():
    assert resolve_style("zhouyi", "") == DEFAULT_STYLES["zhouyi"]
    assert resolve_style("zhouyi", "自定义版式") == "自定义版式"
