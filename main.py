"""赛博占卜插件：周易六爻 / 观音灵签 / 塔罗牌 / 奇门遁甲 / 每日运势。"""
from __future__ import annotations

import asyncio
import re
import time
from datetime import date, datetime
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image
from astrbot.api.star import Context, Star

from .core import daily, lingqian, qimen, tarot, zhouyi
from .core.keywords import match_keyword
from .core.render import build_card_html, kind_for

_GROUP_WORDS = {"divine", "占卜", "zhanbu"}
_SUB_WORDS = {
    "zhouyi", "六爻", "周易", "摇卦",
    "lingqian", "灵签", "观音灵签", "签",
    "tarot", "塔罗", "塔罗牌", "牌",
    "qimen", "奇门", "奇门遁甲", "遁甲",
    "daily", "每日", "运势",
    "history", "历史", "记录",
    "help", "帮助", "菜单", "usage",
}

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
/占卜 每日
    每日运势（塔罗+灵签+周易），当天固定、明日刷新
/占卜 历史 [清除]
    查看或清除本会话占卜记录
/占卜 帮助
    查看本说明

示例：
/占卜 六爻 明天适合出门吗
/占卜 塔罗 三张 感情运
/占卜 奇门 出行吉凶
/占卜 塔罗 查 星星"""


class CyberDivination(Star):
    """赛博占卜：传统占卜体系的娱乐实现。"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

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

    async def _maybe_llm(self, event: AstrMessageEvent, title: str, body: str) -> str:
        """可选 LLM 润色：开启 + 冷却通过时调用模型，任何失败都回退原文。"""
        if not self._cfg("llm_enabled", False):
            return body
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
                return body
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
                    system_prompt=(
                        "你是一位精通传统术数的占卜师，风格温和幽默、点到为止。"
                        "请根据基础占卜结果，用 80-150 字给出个性化解读与建议，"
                        "并提醒仅供参考娱乐，不要给出绝对化结论。"
                    ),
                    prompt=f"以下是【{title}】的占卜结果：\n\n{body}\n\n请给出个性化解读。",
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
                return body + "\n\n🔮 AI 解读：" + text
        except Exception as e:
            logger.warning(f"cyber_divination LLM 润色失败，回退内置解读: {e}")
            try:
                await self.put_kv_data(f"llm_fail_cd_{umo}", int(now))
            except Exception:
                pass
        return body

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
        body = await self._maybe_llm(event, title, body)
        if not self._cfg("use_image", False):
            yield event.plain_result(body)
            return
        if self._cfg("image_mode", "html") == "ai":
            try:
                image = await self._generate_ai_card(title, card_data or {}, zhouyi_cast)
                yield event.chain_result([Image.fromBytes(image)])
                return
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
                    card_data=card_data,
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
            return
        except Exception as exc:
            logger.warning(f"cyber_divination HTML 渲染失败，回退纯文本: {type(exc).__name__}")
        yield event.plain_result(body)

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
        return await generate_image(
            prompt,
            endpoint=reference_endpoint or str(self._cfg("ai_image_endpoint", "") or "").strip(),
            reference=reference,
            reference_field=self._cfg("ai_image_reference_field", "image[]"),
            model=str(self._cfg("ai_image_model", "") or "").strip(),
            api_key=str(self._cfg("ai_image_api_key", "") or "").strip(),
            auth_header=self._cfg("ai_image_auth_header", "bearer"),
            size=str(self._cfg("ai_image_size", "") or "").strip(),
            download_origin=str(self._cfg("ai_image_download_origin", "") or "").strip(),
            timeout=int(self._cfg("ai_image_timeout_seconds", 180)),
        )

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
                yield result
        except Exception as e:
            logger.error(f"cyber_divination {title} 处理异常: {e}")
            yield event.plain_result("占卜出了点小问题，请稍后再试～")

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
        """赛博占卜：/占卜 六爻|灵签|塔罗|奇门|每日|历史|帮助"""

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

    @divine.command("daily", alias={"每日", "运势"})
    async def daily_cmd(self, event: AstrMessageEvent):
        """每日运势：/占卜 每日。当天结果固定，明日刷新"""
        uid = self._sender_id(event) or event.unified_msg_origin
        daily_data = daily.draw_daily(date.today(), uid)
        body = daily.format_daily(daily_data, uid_name=self._sender(event))
        await self._record_history(event, "每日", "每日运势")
        async for result in self._run(
            event, "每日运势", "塔罗 · 灵签 · 周易", body,
            sender=self._sender(event), card_data={"daily": daily_data, "uid": uid},
        ):
            yield result

    @divine.command("history", alias={"历史", "记录"})
    async def history_cmd(self, event: AstrMessageEvent):
        """占卜历史：/占卜 历史 [清除]"""
        remainder = self._remainder(event)
        key = f"history_{event.unified_msg_origin}"
        if remainder.strip() in {"清除", "clear"}:
            try:
                await self.put_kv_data(key, [])
                yield event.plain_result("占卜历史已清除。")
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
