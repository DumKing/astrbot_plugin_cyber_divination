"""赛博占卜插件：周易六爻 / 观音灵签 / 塔罗牌 / 奇门遁甲 / 每日运势。"""
from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import asdict
from datetime import date, datetime, time as clock_time
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from weakref import WeakValueDictionary

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import At, Image, Node, Nodes, Plain, Reply
from astrbot.api.star import Context, Star, StarTools

from .core import daily, extended, lingqian, qimen, tarot, zhouyi
from .core.keywords import match_keyword
from .core.render import build_card_html, kind_for

_GROUP_WORDS = {"divine", "占卜", "zhanbu"}
_SUB_WORDS = {
    "zhouyi", "六爻", "周易", "摇卦",
    "lingqian", "灵签", "观音灵签", "签",
    "tarot", "塔罗", "塔罗牌", "牌",
    "qimen", "奇门", "奇门遁甲", "遁甲",
    "daily", "每日", "运势", "每日运势", "今日运势",
    "history", "历史", "记录",
    "help", "帮助", "菜单", "usage",
}

_SUB_WORDS.update(extended.METHODS)
_SUB_WORDS.update(alias for method in extended.METHODS.values() for alias in method[1])

_ACK_TIMEOUT_SECONDS = 2
_ACK_TEXT = "收到收到，正在为你生成{title}卡片，请稍等～"
DailyImageKey = tuple[date, str]

_DAILY_CACHE_FILENAME = re.compile(r"(\d{4}-\d{2}-\d{2})-[0-9a-f]{64}\.(?:png|jpg|webp)")
_MAX_QUESTION = 120
_LLM_FAIL_COOLDOWN = 60
_KEYWORD_REMAINDER = "cyber_divination_keyword_remainder"

_HELP_IMAGE = Path(__file__).resolve().parent / "assets" / "help.png"

_HELP_TEXT = """🔮 赛博占卜使用说明

/占卜 六爻 [所问之事]
    三枚铜钱起卦，含六神、本卦与变卦
/占卜 灵签 [所问之事]
    观音灵签一百签
/占卜 塔罗 [单张|三张] [所问之事]
    单张指引或过去/现在/未来牌阵（不重复抽牌）
/占卜 塔罗 查 [关键词]
    查询塔罗牌牌意，如：/占卜 塔罗 查 星星
/占卜 奇门 [所问之事]
    简式时家奇门排盘；不填时间自动用当前时刻，
    也可指定：/占卜 奇门 YYYY-MM-DD HH:MM [所问之事]
/占卜 每日 [出生日期 时间 男|女]
    每日占卜长图；提供出生信息时加入八字和紫微
/占卜 历史 [清除]
    查看或清除本会话占卜记录
/占卜 帮助
    查看本说明；新增排盘用 /占卜 新玩法

示例：
/占卜 六爻 明天适合出门吗
/占卜 塔罗 三张 感情运
/占卜 奇门 出行吉凶
/占卜 塔罗 查 星星"""


class AIImageRestricted(Exception):
    """The current policy prevents an AI image response."""


class CyberDivination(Star):
    """赛博占卜：传统占卜体系的娱乐实现。"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._image_quota_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
        self._daily_image_cleanup_day: date | None = None
        self._daily_image_locks: WeakValueDictionary[tuple[date, str], asyncio.Lock] = (
            WeakValueDictionary()
        )

    # ---------- 工具 ----------

    def _cfg(self, key: str, default):
        try:
            return self.config.get(key, default)
        except Exception:
            return default

    def _sender(self, event: AstrMessageEvent) -> str:
        try:
            return event.get_sender_name() or ""
        except Exception:
            return ""

    def _sender_id(self, event: AstrMessageEvent) -> str:
        try:
            return str(event.get_sender_id() or "")
        except Exception:
            return ""

    def _remainder(self, event: AstrMessageEvent) -> str:
        """去掉唤醒前缀、命令组和子命令，返回剩余内容（所问之事等）。"""
        keyword_remainder = event.get_extra(_KEYWORD_REMAINDER)
        if keyword_remainder is not None:
            return keyword_remainder[:_MAX_QUESTION]
        try:
            text = event.get_message_str().strip()
        except Exception:
            text = ""
        text = re.sub(r"\[CQ:[^\]]*\]", "", text).strip()
        tokens = text.split()
        if tokens and (tokens[0].startswith(("/", "／", "@"))):
            tokens = tokens[1:]
        dropped = 0
        all_words = _GROUP_WORDS | _SUB_WORDS
        while tokens and dropped < 2:
            token = tokens[0].strip("，,。.！!？?")
            if token.lower() not in all_words:
                break
            tokens = tokens[1:]
            dropped += 1
        return " ".join(tokens).strip()[:_MAX_QUESTION]

    async def _record_history(self, event: AstrMessageEvent, kind: str, summary: str):
        """记录一条占卜历史（kv 列表，超出上限截断；失败静默不影响主流程）。"""
        try:
            key = f"history_{event.unified_msg_origin}"
            items = await self.get_kv_data(key, []) or []
            if not isinstance(items, list):
                items = []
            try:
                limit = max(1, int(self._cfg("history_max", 10)))
            except Exception:
                limit = 10
            items.append({
                "t": time.strftime("%m-%d %H:%M"),
                "kind": kind,
                "summary": summary,
            })
            await self.put_kv_data(key, items[-limit:])
        except Exception:
            pass

    async def _llm_text(self, event: AstrMessageEvent, system_prompt: str, prompt: str) -> str:
        """可选 LLM 润色：开启 + 冷却通过时调用模型，任何失败都回退原文。"""
        if not self._cfg("llm_enabled", False):
            return ""
        try:
            cooldown = max(0, int(self._cfg("llm_cooldown_seconds", 300)))
        except Exception:
            cooldown = 300
        umo = event.unified_msg_origin
        now = time.time()
        try:
            last = float(await self.get_kv_data(f"llm_cd_{umo}", 0) or 0)
            fail = float(await self.get_kv_data(f"llm_fail_cd_{umo}", 0) or 0)
            if now - last < cooldown or now - fail < _LLM_FAIL_COOLDOWN:
                return ""
        except Exception:
            pass
        try:
            try:
                timeout = max(5, int(self._cfg("llm_timeout_seconds", 30)))
            except Exception:
                timeout = 30
            provider_id = await self.context.get_current_chat_provider_id(umo)
            resp = await asyncio.wait_for(
                self.context.llm_generate(
                    chat_provider_id=provider_id,
                    system_prompt=system_prompt,
                    prompt=prompt,
                    temperature=0.85,
                ),
                timeout=timeout,
            )
            text = (resp.completion_text or "").strip()
            text = re.sub(r"\[CQ:[^\]]*\]", "", text)
            text = re.sub(r'^["「『“]|["」』”]$', "", text).strip()
            if text:
                try:
                    await self.put_kv_data(f"llm_cd_{umo}", int(now))
                except Exception:
                    pass
                return text
        except Exception as e:
            logger.warning(f"cyber_divination LLM 润色失败，回退内置解读: {e}")
            try:
                await self.put_kv_data(f"llm_fail_cd_{umo}", int(now))
            except Exception:
                pass
        return ""

    async def _maybe_llm(self, event: AstrMessageEvent, title: str, body: str) -> str:
        text = await self._llm_text(
            event,
            "你是一位精通传统术数的占卜师，风格温和幽默、点到为止。"
            "根据基础占卜结果，用 80-150 字给出个性化解读与建议。"
            "提醒仅供娱乐，不给出绝对化结论。",
            f"以下是【{title}】的占卜结果：\n\n{body}\n\n请给出个性化解读。",
        )
        return body + "\n\n🔮 AI 解读：" + text if text else body

    async def _daily_panel_notes(self, event: AstrMessageEvent, panels: list[dict]) -> list[dict]:
        text = await self._llm_text(
            event,
            "你在为每日运势撰写八段娱乐性质的简解。每段约40–85字，只根据对应排盘给出"
            "温和建议，不改动原始数据，不做绝对判断。无法解读的项省略。只返回一个 JSON 对象，"
            "键必须是原样的术数标题，值是简解文字，不要使用 Markdown。",
            json.dumps(panels, ensure_ascii=False),
        )
        if not text:
            return panels
        try:
            notes = json.loads(text.removeprefix("```json").removesuffix("```").strip())
        except ValueError:
            return panels
        if not isinstance(notes, dict):
            return panels
        return [
            {**panel, "ai_note": note.strip()[:180]}
            if isinstance(note := notes.get(panel["title"]), str) and note.strip()
            else panel
            for panel in panels
        ]

    async def _acknowledge_image(self, event: AstrMessageEvent, title: str):
        actions = []
        message_id = getattr(getattr(event, "message_obj", None), "message_id", None)
        if self._cfg("image_ack_reaction_enabled", True):
            call_action = getattr(getattr(event, "bot", None), "call_action", None)
            if callable(call_action) and str(message_id).lstrip("-").isdigit():
                actions.append(call_action(
                    "set_msg_emoji_like", message_id=int(message_id), emoji_id="124", set=True,
                ))
        if self._cfg("image_ack_text_enabled", False):
            template = str(self._cfg("image_ack_text", _ACK_TEXT) or "").strip()
            if template:
                parts = [Reply(id=message_id)] if message_id else []
                parts.append(Plain(template.replace("{title}", title)))
                actions.append(event.send(MessageChain(parts)))
        if actions:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*actions, return_exceptions=True),
                    timeout=_ACK_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.debug("cyber_divination 出图确认超时")

    async def _send_daily_forward(self, event: AstrMessageEvent, body: str) -> bool:
        """Send the daily reading as one folded QQ message."""
        if getattr(event, "get_platform_name", lambda: "")() != "aiocqhttp":
            return False
        try:
            bot_id = str(event.get_self_id())
            sections = re.split(r"\n\n(?=【)", body)
            nodes = [
                Node(name="今日运势详情", uin=bot_id, content=[Plain(section)])
                for section in sections if section.strip()
            ]
            await event.send(MessageChain([Nodes(nodes)]))
            return True
        except Exception as exc:
            logger.warning(f"cyber_divination 每日运势合并转发失败: {type(exc).__name__}")
            return False

    async def _finish(
        self,
        event: AstrMessageEvent,
        title: str,
        body: str,
        subtitle: str = "",
        *,
        zhouyi_cast=None,
        question: str = "",
        sender: str = "",
        card_data: dict | None = None,
    ):
        """统一输出：先尝试 LLM 润色，再按配置输出精美卡片或文本。"""
        use_image = self._cfg("use_image", False)
        data = card_data or {}
        forward_daily = bool(data.get("daily")) and self._cfg("daily_forward_enabled", False)
        ai_mode = self._cfg("image_mode", "html") == "ai"
        cache_key = self._daily_image_key(event, data) if use_image and ai_mode else None
        cached = False
        if cache_key is not None and self._ai_image_policy(event) != "blocked":
            try:
                cached = self._cached_daily_image(cache_key) is not None
            except OSError as exc:
                logger.warning(f"cyber_divination 检查运势图片缓存失败: {type(exc).__name__}")
        if use_image and not cached and not data.get("image_acknowledged"):
            await self._acknowledge_image(event, title)
        if panels := data.get("daily_panels"):
            notes = [f"{panel['title']}：{panel['ai_note']}" for panel in panels if panel.get("ai_note")]
            if notes:
                body += "\n\n【AI 简解】\n" + "\n".join(notes)
        else:
            body = await self._maybe_llm(event, title, body)
        if not use_image:
            if forward_daily:
                if not await self._send_daily_forward(event, body):
                    yield event.plain_result("今日运势详情暂时无法以合并转发发送。")
                return
            yield event.plain_result(body)
            return
        if ai_mode:
            try:
                if question and self._cfg("ai_image_include_question", False):
                    data = {**data, "question": question}
                image = await self._get_ai_image(event, title, data, zhouyi_cast)
                component = (
                    Image.fromFileSystem(str(image)) if isinstance(image, Path) else Image.fromBytes(image)
                )
                yield event.chain_result([component])
                if forward_daily:
                    await self._send_daily_forward(event, body)
                return
            except AIImageRestricted as exc:
                logger.info(f"cyber_divination {exc}，回退 HTML")
            except Exception as exc:
                logger.warning(f"cyber_divination API 生图失败: {type(exc).__name__}")
        try:
            url = await self.html_render(
                build_card_html(
                    title,
                    subtitle,
                    body.splitlines(),
                    "赛博占卜 · 仅供娱乐",
                    zhouyi_cast=zhouyi_cast,
                    question=question,
                    sender=sender,
                    theme=self._cfg("card_theme", "auto"),
                    card_data=data,
                ),
                {},
                options={
                    "type": "png",
                    "full_page": True,
                    "viewport_width": 900,
                    "animations": "disabled",
                    "device_scale_factor_level": "high" if self._cfg("card_hd", True) else "normal",
                },
            )
            if not url:
                raise ValueError("HTML 渲染未返回图片")
            yield event.image_result(url)
            if forward_daily:
                await self._send_daily_forward(event, body)
            return
        except Exception as exc:
            logger.warning(f"cyber_divination HTML 渲染失败: {type(exc).__name__}")
        if forward_daily:
            if not await self._send_daily_forward(event, body):
                yield event.plain_result("今日运势图片和合并转发暂时无法发送。")
            return
        yield event.plain_result(body)

    def _daily_image_key(self, event: AstrMessageEvent, data: dict) -> DailyImageKey | None:
        if draw := data.get("daily"):
            uid = data.get("uid") or self._sender_id(event)
            if uid:
                return draw["date"], uid
        return None

    def _daily_cache_dir(self) -> Path:
        shared = str(self._cfg("daily_image_cache_dir", "") or "").strip()
        if shared:
            return Path(shared).expanduser().resolve() / "cyber-divination-daily"
        return Path(StarTools.get_data_dir("astrbot_plugin_cyber_divination_cards")) / "daily-images"

    def _daily_cache_stem(self, key: DailyImageKey) -> Path:
        day, uid = key
        return self._daily_cache_dir() / f"{day.isoformat()}-{sha256(uid.encode()).hexdigest()}"

    def _cached_daily_image(self, key: DailyImageKey) -> Path | None:
        stem = self._daily_cache_stem(key)
        for ext in (".png", ".jpg", ".webp"):
            path = stem.with_suffix(ext)
            if path.is_file():
                return path
        return None

    def _clean_daily_images(self, today: date) -> None:
        directory = self._daily_cache_dir()
        if not directory.is_dir():
            return
        for path in directory.iterdir():
            match = _DAILY_CACHE_FILENAME.fullmatch(path.name)
            if match and match[1] < today.isoformat() and path.is_file():
                path.unlink(missing_ok=True)

    def _store_daily_image(self, key: DailyImageKey, image: bytes) -> Path:
        stem = self._daily_cache_stem(key)
        suffix = ".jpg" if image.startswith(b"\xff\xd8") else ".png"
        if image[:4] == b"RIFF" and image[8:12] == b"WEBP":
            suffix = ".webp"
        path = stem.with_suffix(suffix)
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=path.parent, delete=False) as temp:
            temp_path = Path(temp.name)
            try:
                temp.write(image)
                temp.close()
                temp_path.replace(path)
            finally:
                temp_path.unlink(missing_ok=True)
        return path

    def _ai_image_policy(self, event: AstrMessageEvent) -> str:
        if not self._cfg("ai_image_daily_limit_enabled", False):
            return "unlimited"
        umo = event.unified_msg_origin
        whitelist = self._cfg("ai_image_umo_whitelist", []) or []
        if any(isinstance(entry, str) and entry.strip() == umo for entry in whitelist):
            return "unlimited"
        blacklist = self._cfg("ai_image_umo_blacklist", []) or []
        if any(isinstance(entry, str) and entry.strip() == umo for entry in blacklist):
            return "blocked"
        return self._cfg("ai_image_umo_default", "limited")

    async def _reserve_image_quota(self, event: AstrMessageEvent):
        limit = int(self._cfg("ai_image_daily_limit", 0) or 0)
        if limit <= 0:
            return
        umo = event.unified_msg_origin
        sender_id = self._sender_id(event)
        key = f"ai_image_quota_user_{sender_id}" if sender_id else f"ai_image_quota_session_{umo}"
        lock = self._image_quota_locks.setdefault(key, asyncio.Lock())
        async with lock:
            today = date.today().isoformat()
            record = await self.get_kv_data(key, {}) or {}
            count = record.get("count", 0) if record.get("date") == today else 0
            if count >= limit:
                raise AIImageRestricted("达到每日 AI 生图上限")
            await self.put_kv_data(key, {"date": today, "count": count + 1})

    async def _get_ai_image(self, event: AstrMessageEvent, title: str, data: dict, cast) -> bytes | Path:
        policy = self._ai_image_policy(event)
        if policy == "blocked":
            raise AIImageRestricted("命中 AI 生图黑名单")
        key = self._daily_image_key(event, data)
        if key is None:
            if policy == "limited":
                await self._reserve_image_quota(event)
            return await self._generate_ai_card(title, data, cast)

        lock = self._daily_image_locks.setdefault(key, asyncio.Lock())
        async with lock:
            day = key[0]
            if self._daily_image_cleanup_day != day:
                try:
                    await asyncio.to_thread(self._clean_daily_images, day)
                    self._daily_image_cleanup_day = day
                except OSError as exc:
                    logger.warning(f"cyber_divination 清理过期运势图片失败: {type(exc).__name__}")
            if cached := self._cached_daily_image(key):
                return cached

            if policy == "limited":
                await self._reserve_image_quota(event)
            image = await self._generate_ai_card(title, data, cast)
            try:
                return await asyncio.to_thread(self._store_daily_image, key, image)
            except OSError as exc:
                logger.warning(f"cyber_divination 保存运势图片失败: {type(exc).__name__}")
                return image

    async def _generate_ai_card(self, title: str, card_data: dict, cast) -> bytes:
        from .core.image_api import build_prompt, generate_image
        from .core.image_styles import resolve_style

        kind = kind_for(title)
        style = str(self._cfg(f"ai_prompt_{kind}", "") or "").strip()
        prompt = build_prompt(resolve_style(kind, style), kind, title, card_data, cast)
        reference_endpoint = str(self._cfg("ai_image_reference_endpoint", "") or "").strip()
        reference = None
        if reference_endpoint:
            reference = Path(__file__).resolve().parent / "references" / f"{kind}.jpg"
            prompt += (
                "\n随请求上传的图片是视觉参考。尽量沿用它的版式、配色、边框、字体气质与装饰。"
                "先清除示例中的卦名、爻线、签号、牌名、日期、人名和结论，再按本次 JSON 排版。"
                "参考图与本次数据冲突时，以本次数据为准；不要复刻参考图上的旧文字。"
            )
            if kind == "daily":
                prompt += "请保留提示词规定的固定帮助文字，并按本次数据扩展长图。"
        return await generate_image(
            prompt,
            endpoint=(reference_endpoint if reference else "") or str(self._cfg("ai_image_endpoint", "") or "").strip(),
            reference=reference,
            reference_field=self._cfg("ai_image_reference_field", "image[]"),
            model=str(self._cfg("ai_image_model", "") or "").strip(),
            api_key=str(self._cfg("ai_image_api_key", "") or "").strip(),
            auth_header=self._cfg("ai_image_auth_header", "bearer"),
            size=(
                str(self._cfg("ai_daily_image_size", "") or "").strip() if kind == "daily"
                else str(self._cfg("ai_image_size", "") or "").strip()
            ),
            output_format="png" if kind == "daily" else "",
            download_origin=str(self._cfg("ai_image_download_origin", "") or "").strip(),
            timeout=int(self._cfg("ai_image_timeout_seconds", 180)),
        )

    def _address_result(self, event: AstrMessageEvent, result):
        prefix = []
        message_id = getattr(event.message_obj, "message_id", None)
        if message_id:
            prefix.append(Reply(id=message_id))
        if event.get_group_id() and (sender_id := self._sender_id(event)):
            prefix.append(At(qq=sender_id))
        result.chain[:0] = prefix
        return result

    async def _run(
        self, event: AstrMessageEvent, title: str, subtitle: str, body: str,
        *, zhouyi_cast=None, question: str = "", sender: str = "",
        card_data: dict | None = None,
    ):
        try:
            async for result in self._finish(
                event, title, body, subtitle,
                zhouyi_cast=zhouyi_cast, question=question, sender=sender,
                card_data=card_data,
            ):
                yield self._address_result(event, result)
        except Exception as e:
            logger.error(f"cyber_divination {title} 处理异常: {e}")
            yield self._address_result(event, event.plain_result("占卜出了点小问题，请稍后再试～"))

    # ---------- 命令组 ----------

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def keyword_trigger(self, event: AstrMessageEvent):
        """让无前缀关键词复用原有占卜指令。"""
        text = event.get_message_str()
        if text.strip() == "占卜帮助":
            match = ("help", "")
        else:
            if not self._cfg("keyword_trigger_enabled", False) or event.is_at_or_wake_command:
                return
            match = match_keyword(
                text,
                self._cfg("keyword_trigger_mode", "exact"),
                self._cfg("keyword_aliases", {}),
            )
        if match is None:
            return
        action, remainder = match
        handler = getattr(self, f"{action}_cmd")
        event.set_extra(_KEYWORD_REMAINDER, remainder)
        event.stop_event()
        try:
            async for result in handler(event):
                yield result
        finally:
            event.set_extra(_KEYWORD_REMAINDER, None)
            event.stop_event()

    @filter.command_group("divine", alias={"占卜", "zhanbu"})
    def divine():
        """赛博占卜。支持六爻、灵签、塔罗、奇门、每日运势及七种扩展排盘，可查牌和管理历史。发送 /占卜 帮助 查看完整用法。"""

    @divine.command("help", alias={"帮助", "菜单"})
    async def help_cmd(self, event: AstrMessageEvent):
        """查看赛博占卜使用说明"""
        if _HELP_IMAGE.is_file():
            yield event.image_result(str(_HELP_IMAGE))
        else:
            yield event.plain_result(_HELP_TEXT)

    @divine.command("zhouyi", alias={"六爻", "周易", "摇卦"})
    async def zhouyi_cmd(self, event: AstrMessageEvent):
        """周易六爻：铜钱起卦（含六神）。用法：/占卜 六爻 [所问之事]"""
        cast = zhouyi.cast()
        question = self._remainder(event)
        sender = self._sender(event)
        body = zhouyi.format_cast(cast, question=question, sender=sender)
        await self._record_history(event, "六爻", zhouyi.summarize(cast))
        async for result in self._run(
            event, "周易六爻", "三枚铜钱 · 六爻成卦", body,
            zhouyi_cast=cast, question=question, sender=sender,
        ):
            yield result

    @divine.command("lingqian", alias={"灵签", "观音灵签", "签"})
    async def lingqian_cmd(self, event: AstrMessageEvent):
        """观音灵签。用法：/占卜 灵签 [所问之事]"""
        number, grade, poem, explain = lingqian.draw()
        question = self._remainder(event)
        sender = self._sender(event)
        body = lingqian.format_draw(
            number, grade, poem, explain,
            question=question,
            sender=sender,
        )
        await self._record_history(event, "灵签", f"第{number}签 · {grade}")
        # 今日求签次数（按天重置，纯趣味，失败不影响）
        try:
            key = f"daily_lingqian_{event.unified_msg_origin}_{time.strftime('%Y%m%d')}"
            count = await self.get_kv_data(key, 0) or 0
            count += 1
            await self.put_kv_data(key, count)
            body += f"\n（今日第 {count} 次求签）"
        except Exception:
            pass
        async for result in self._run(
            event, "观音灵签", "灵签一签 · 心诚则灵", body,
            question=question, sender=sender,
            card_data={"number": number, "grade": grade, "poem": poem, "explain": explain},
        ):
            yield result

    @divine.command("tarot", alias={"塔罗", "塔罗牌", "牌"})
    async def tarot_cmd(self, event: AstrMessageEvent):
        """塔罗牌。用法：/占卜 塔罗 [单张|三张] [所问之事] ｜ /占卜 塔罗 查 [关键词]"""
        remainder = self._remainder(event)
        search = re.match(r"^(查询|牌意|查)\b", remainder)
        if search:
            keyword = remainder[search.end():].strip()
            if not keyword:
                yield event.plain_result(
                    "用法：/占卜 塔罗 查 [关键词]，例如 /占卜 塔罗 查 星星"
                )
                return
            hits = tarot.search_cards(keyword)
            if not hits:
                yield event.plain_result(
                    "没有找到相关牌，试试中文牌名或英文关键词，例如「星星」或「star」。"
                )
                return
            lines = ["🔮 塔罗牌查牌结果："]
            shown = hits[:10]
            for name, upright, reversed_ in shown:
                lines.append(f"【{name}】")
                lines.append(f"  正位：{upright}")
                lines.append(f"  逆位：{reversed_}")
                lines.append("")
            if len(hits) > len(shown):
                lines.append(f"……共 {len(hits)} 张匹配，请缩小关键词范围。")
            yield event.plain_result("\n".join(lines))
            return

        spread, question = tarot.parse_spread(remainder)
        if spread == "three":
            cards = tarot.draw_three()
            card_items = cards
            body = tarot.format_three(cards, question, self._sender(event))
            summary = " · ".join(
                tarot.card_summary(name, is_reversed)
                for _pos, name, is_reversed, _meaning in cards
            )
        else:
            name, is_reversed, meaning = tarot.draw_single()
            card_items = [("今日指引", name, is_reversed, meaning)]
            body = tarot.format_single(name, is_reversed, meaning, question, self._sender(event))
            summary = tarot.card_summary(name, is_reversed)
        await self._record_history(event, "塔罗", summary)
        subtitle = "过去 · 现在 · 未来" if spread == "three" else "单张指引"
        async for result in self._run(
            event, "塔罗牌占卜", subtitle, body,
            question=question, sender=self._sender(event),
            card_data={"cards": card_items},
        ):
            yield result

    @divine.command("qimen", alias={"奇门", "奇门遁甲", "遁甲"})
    async def qimen_cmd(self, event: AstrMessageEvent):
        """奇门遁甲（简式时家）。用法：/占卜 奇门 [所问之事]，不填时间自动用当前时刻"""
        remainder = self._remainder(event)
        time_str, question = qimen.split_time_question(remainder)
        provided = bool(time_str.strip())
        parsed = qimen.parse_time(time_str)
        invalid = provided and parsed is None
        dt = parsed or datetime.now()
        pan = qimen.make_pan(dt)
        if invalid:
            note = "⚠ 时间格式无法识别，已按当前时刻起局。"
        elif not provided:
            note = "（自动检测当前时刻起局）"
        elif qimen.is_date_only(time_str):
            note = "（仅提供日期，按当日正午起局）"
        else:
            note = ""
        body = qimen.format_pan(pan, question, self._sender(event), note=note)
        await self._record_history(event, "奇门", qimen.summarize(pan))
        subtitle = f"自动起局 · {dt:%m-%d %H:%M}" if not provided else f"{dt:%m-%d %H:%M}"
        async for result in self._run(
            event, "奇门遁甲", subtitle, body,
            question=question, sender=self._sender(event),
            card_data={"pan": pan, "time_note": note},
        ):
            yield result

    async def _daily_birth_profile(self, event: AstrMessageEvent) -> tuple[date | None, str]:
        sender_id = self._sender_id(event)
        call_action = getattr(getattr(event, "bot", None), "call_action", None)
        if not sender_id.isascii() or not sender_id.isdecimal() or not callable(call_action):
            return None, ""
        gender = ""
        try:
            profile = await asyncio.wait_for(
                call_action("get_stranger_info", user_id=int(sender_id)), timeout=2,
            )
            if isinstance(profile.get("data"), dict):
                profile = profile["data"]
            gender = {"male": "男", "female": "女"}.get(profile.get("sex"), "")
            birthday = date(*(int(profile[f"birthday_{part}"]) for part in ("year", "month", "day")))
            if not 1901 <= birthday.year <= 2099 or birthday > date.today():
                return None, gender
            return birthday, gender
        except Exception:
            return None, gender

    async def _daily_reading(self, event: AstrMessageEvent, day: date, uid: str):
        key = f"daily_reading_{uid}"
        lock = self._daily_image_locks.setdefault((day, uid), asyncio.Lock())
        async with lock:
            record = await self.get_kv_data(key, {})
            if record and record.get("date") == day.isoformat():
                saved = record["reading"]
                return {**saved, "date": day, "cast": zhouyi.ZhouyiCast(**saved["cast"])}, record["panels"], False

            birth = self._remainder(event)
            birthday, gender = None, ""
            hour_only = re.fullmatch(r"(\d{2}:\d{2})(?:\s+(男|女))?", birth)
            if not birth or hour_only:
                birthday, gender = await self._daily_birth_profile(event)
            panels = await asyncio.to_thread(
                extended.daily_panels, datetime.combine(day, clock_time(12)), uid, birth,
                birthday=birthday, profile_gender=gender,
            )
            acknowledged = self._cfg("use_image", False)
            if acknowledged:
                await self._acknowledge_image(event, "每日运势")
            panels = await self._daily_panel_notes(event, panels)
            reading = daily.draw_daily(day, uid)
            saved = {name: reading[name] for name in ("card", "lingqian")}
            saved["cast"] = asdict(reading["cast"])
            await self.put_kv_data(key, {"date": day.isoformat(), "reading": saved, "panels": panels})
            return reading, panels, acknowledged

    @divine.command("daily", alias={"每日", "运势", "每日运势", "今日运势"})
    async def daily_cmd(self, event: AstrMessageEvent):
        """每日运势：/占卜 每日。当天结果固定，明日刷新"""
        uid = self._sender_id(event) or event.unified_msg_origin
        try:
            daily_data, panels, acknowledged = await self._daily_reading(event, date.today(), uid)
        except ValueError as exc:
            yield self._address_result(event, event.plain_result(
                f"{exc}\n示例：/占卜 每日 2000-08-16 12:00 女",
            ))
            return
        except Exception as exc:
            logger.warning(f"cyber_divination 每日运势结果保存或读取失败: {type(exc).__name__}")
            yield self._address_result(event, event.plain_result("今日运势暂时无法读取或保存，请稍后再试。"))
            return
        sender = self._sender(event)
        body = daily.format_daily(daily_data, uid_name=sender)
        for panel in panels:
            fields = "\n".join(f"{label}：{value}" for label, value in panel["rows"])
            body += f"\n\n【{panel['title']}】\n{fields}"
        await self._record_history(event, "每日", "每日运势")
        async for result in self._run(
            event, "每日运势", "每日占卜合辑", body,
            sender=sender, card_data={
                "daily": daily_data, "uid": uid, "daily_panels": panels,
                "image_acknowledged": acknowledged,
            },
        ):
            yield result

    async def _run_extended(self, event: AstrMessageEvent, kind: str):
        title, _, usage, _ = extended.METHODS[kind]
        try:
            reading = await asyncio.to_thread(
                extended.BUILDERS[kind], self._remainder(event), datetime.now(),
            )
        except ValueError as exc:
            yield self._address_result(event, event.plain_result(f"{exc}\n用法：/占卜 {usage}"))
            return
        except Exception as exc:
            logger.warning(f"cyber_divination {title} 排盘失败: {type(exc).__name__}")
            yield self._address_result(event, event.plain_result(
                f"{title}暂时无法排盘，请检查插件依赖或更换有效日期后重试。",
            ))
            return
        await self._record_history(event, title, reading.summary)
        card_data = {"extended_rows": reading.rows}
        if reading.hexagram_lines is not None:
            card_data["hexagram_lines"] = reading.hexagram_lines
        async for result in self._run(
            event, title, reading.summary, reading.text(title),
            question=reading.question, card_data=card_data,
        ):
            yield result

    @divine.command("methods", alias={"新玩法", "进阶帮助"})
    async def methods_cmd(self, event: AstrMessageEvent):
        lines = ["🔮 赛博占卜 · 新玩法"]
        lines.extend(f"/占卜 {method[2]}" for method in extended.METHODS.values())
        yield event.plain_result("\n".join(lines))

    @divine.command("xiaoliuren", alias=set(extended.METHODS["xiaoliuren"][1]))
    async def xiaoliuren_cmd(self, event: AstrMessageEvent):
        async for result in self._run_extended(event, "xiaoliuren"):
            yield result

    @divine.command("meihua", alias=set(extended.METHODS["meihua"][1]))
    async def meihua_cmd(self, event: AstrMessageEvent):
        async for result in self._run_extended(event, "meihua"):
            yield result

    @divine.command("lenormand", alias=set(extended.METHODS["lenormand"][1]))
    async def lenormand_cmd(self, event: AstrMessageEvent):
        async for result in self._run_extended(event, "lenormand"):
            yield result

    @divine.command("daliuren", alias=set(extended.METHODS["daliuren"][1]))
    async def daliuren_cmd(self, event: AstrMessageEvent):
        async for result in self._run_extended(event, "daliuren"):
            yield result

    @divine.command("taiyi", alias=set(extended.METHODS["taiyi"][1]))
    async def taiyi_cmd(self, event: AstrMessageEvent):
        async for result in self._run_extended(event, "taiyi"):
            yield result

    @divine.command("bazi", alias=set(extended.METHODS["bazi"][1]))
    async def bazi_cmd(self, event: AstrMessageEvent):
        async for result in self._run_extended(event, "bazi"):
            yield result

    @divine.command("ziwei", alias=set(extended.METHODS["ziwei"][1]))
    async def ziwei_cmd(self, event: AstrMessageEvent):
        async for result in self._run_extended(event, "ziwei"):
            yield result

    @divine.command("history", alias={"历史", "记录"})
    async def history_cmd(self, event: AstrMessageEvent):
        """占卜历史：/占卜 历史 [清除]"""
        remainder = self._remainder(event)
        key = f"history_{event.unified_msg_origin}"
        if remainder.strip() in {"清除", "clear"}:
            try:
                await self.put_kv_data(key, [])
                uid = self._sender_id(event) or event.unified_msg_origin
                image_key = (date.today(), uid)
                lock = self._daily_image_locks.setdefault(image_key, asyncio.Lock())
                async with lock:
                    if cached := self._cached_daily_image(image_key):
                        cached.unlink(missing_ok=True)
                yield event.plain_result("占卜历史和你的今日运势图片缓存已清除。")
            except Exception:
                yield event.plain_result("历史清除失败，请稍后再试～")
            return
        try:
            items = await self.get_kv_data(key, []) or []
        except Exception:
            items = []
        if not isinstance(items, list) or not items:
            yield event.plain_result("还没有占卜记录，先来一卦吧～")
            return
        lines = ["🔮 占卜历史（最近优先）："]
        for i, item in enumerate(items, 1):
            try:
                lines.append(
                    f"{i}. [{item['t']}] {item['kind']} · {item['summary']}"
                )
            except Exception:
                continue
        lines.append("")
        lines.append("清除记录：/占卜 历史 清除")
        yield event.plain_result("\n".join(lines))

    async def terminate(self):
        logger.info("cyber_divination 插件已停止")
