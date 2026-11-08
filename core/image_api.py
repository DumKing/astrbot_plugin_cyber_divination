"""Generate result cards through a synchronous Images API endpoint."""
from __future__ import annotations

import asyncio
import base64
import json
from io import BytesIO

import aiohttp
from PIL import Image
from yarl import URL

from . import daily

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_JSON_BYTES = 4 * ((MAX_IMAGE_BYTES + 2) // 3) + 65536
IMAGE_FORMATS = ("PNG", "JPEG", "WEBP")


class ImageAPIError(ValueError):
    """An endpoint or response did not satisfy the image contract."""


def build_prompt(style: str, kind: str, title: str, data: dict, cast=None) -> str:
    # Only fixed labels and algorithm data leave the bot.
    if kind == "zhouyi" and cast:
        context = {
            "hexagram": cast.name, "lines_bottom_to_top": cast.lines,
            "moving_lines": cast.moving, "changed_hexagram": cast.changed_name,
            "six_gods_bottom_to_top": cast.gods, "hexagram_text": cast.guaci,
            "interpretation": cast.jie,
        }
    elif kind == "lingqian":
        context = {key: data[key] for key in ("number", "grade", "poem", "explain") if key in data}
    elif kind == "tarot":
        context = {"cards": [
            {"position": position, "name": name, "reversed": reversed_, "meaning": meaning}
            for position, name, reversed_, meaning in data.get("cards", [])
        ]}
    elif kind == "qimen" and (pan := data.get("pan")):
        context = {key: pan[key] for key in (
            "phase", "ju", "tian_pan", "men_pan", "shen_pan", "zhifu_star", "zhishi_door",
        )}
        context["time"] = pan["dt"].strftime("%Y-%m-%d %H:%M")
        context["earth_stems_by_palace"] = {palace: stem for stem, palace in pan["di_pan"].items()}
        context["grid_order"] = [[4, 9, 2], [3, 5, 7], [8, 1, 6]]
    elif kind == "daily" and (draw := data.get("daily")):
        name, reversed_, meaning = draw["card"]
        number, grade, poem, explain = draw["lingqian"]
        context = {
            "date": draw["date"].isoformat(),
            "weekday": "星期" + "一二三四五六日"[draw["date"].weekday()],
            "tarot": {"name": name, "reversed": reversed_, "meaning": meaning},
            "lot": {"number": number, "grade": grade, "poem": poem, "explain": explain},
            "hexagram": draw["cast"].name,
            "hexagram_interpretation": draw["cast"].jie,
            "advice": daily.grade_advice(grade),
            "lucky_details": daily.lucky_details(draw["date"], data.get("uid", ""), grade),
        }
    else:
        context = {}
    return (
        f"{style}\n\n主题：{title}\n"
        f"构图参考数据：{json.dumps(context, ensure_ascii=False)}\n"
        "生成一张完整的结果卡。示例或风格提示中的日期、卦名、签号和牌名仅是占位，"
        "以本次 JSON 数据为准；缺失字段直接省略。牌位和卦爻逐项核对，不要编造新结果。"
        "中文须清晰准确，底部写「仅供娱乐」，不要添加水印。"
    )


def _http_url(value: str) -> URL:
    url = URL(value)
    if (
        url.scheme not in {"https", "http"} or not url.host
        or url.user is not None or url.password is not None or url.fragment
    ):
        raise ImageAPIError("需要无内嵌凭据的 HTTP(S) 地址")
    return url


async def _read(response: aiohttp.ClientResponse, limit: int) -> bytes:
    if not 200 <= response.status < 300:
        raise ImageAPIError(f"HTTP {response.status}")
    if response.content_length is not None and response.content_length > limit:
        raise ImageAPIError("响应超过大小限制")
    data = bytearray()
    async for chunk in response.content.iter_chunked(65536):
        data.extend(chunk)
        if len(data) > limit:
            raise ImageAPIError("响应超过大小限制")
    return bytes(data)


def _validate_image(data: bytes) -> bytes:
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ImageAPIError("图片为空或超过 10 MiB")
    with Image.open(BytesIO(data), formats=IMAGE_FORMATS) as image:
        if image.width * image.height > 16_000_000 or getattr(image, "is_animated", False):
            raise ImageAPIError("图片尺寸过大或包含多帧")
        image.verify()
    with Image.open(BytesIO(data), formats=IMAGE_FORMATS) as image:
        image.load()
    return data


async def generate_image(
    prompt: str,
    *,
    endpoint: str,
    model: str,
    api_key: str = "",
    auth_header: str = "bearer",
    size: str = "",
    download_origin: str = "",
    timeout: int = 180,
) -> bytes:
    """Return validated image bytes without forwarding credentials to image URLs."""
    url = _http_url(endpoint)
    if not model:
        raise ImageAPIError("需要填写绘图模型")
    origins = {url.origin()}
    if download_origin:
        origin = _http_url(download_origin)
        if origin.path != "/" or origin.query_string:
            raise ImageAPIError("下载来源只填写协议、主机和端口")
        origins.add(origin.origin())
    headers = {}
    if api_key:
        if auth_header == "api-key":
            headers["api-key"] = api_key
        else:
            headers["Authorization"] = f"Bearer {api_key}"
    payload = {"model": model, "prompt": prompt, "n": 1}
    if size:
        payload["size"] = size

    return await asyncio.wait_for(
        _request_image(url, origins, payload, headers),
        timeout=max(1, min(timeout, 600)),
    )


async def _request_image(
    url: URL, origins: set[URL], payload: dict, headers: dict,
) -> bytes:
    async with aiohttp.ClientSession(
        cookie_jar=aiohttp.DummyCookieJar(),
        trust_env=False,
        timeout=aiohttp.ClientTimeout(total=None),
    ) as session:
        async with session.post(
            url, json=payload, headers=headers, allow_redirects=False,
        ) as response:
            if response.content_type in {"image/png", "image/jpeg", "image/webp"}:
                return await asyncio.to_thread(_validate_image, await _read(response, MAX_IMAGE_BYTES))
            result = json.loads(await _read(response, MAX_JSON_BYTES))
        try:
            item = result["data"][0]
        except (TypeError, KeyError, IndexError) as exc:
            raise ImageAPIError("缺少 data[0]") from exc
        if not isinstance(item, dict):
            raise ImageAPIError("无效的 data[0]")
        encoded = item.get("b64_json")
        if isinstance(encoded, str) and encoded:
            data = base64.b64decode(encoded, validate=True)
        elif isinstance(item.get("url"), str) and item["url"]:
            image_url = _http_url(item["url"])
            if image_url.origin() not in origins:
                raise ImageAPIError("图片下载来源未配置")
            async with session.get(image_url, allow_redirects=False) as response:
                data = await _read(response, MAX_IMAGE_BYTES)
        else:
            raise ImageAPIError("响应未返回图片")
    return await asyncio.to_thread(_validate_image, data)
