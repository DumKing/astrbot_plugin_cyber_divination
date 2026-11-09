"""每日运势：按（日期, 用户）稳定抽取塔罗 + 灵签 + 周易的综合运势卡。"""
from __future__ import annotations

import hashlib
import random
from datetime import date

from . import lingqian, tarot, zhouyi
from .ganzhi import day_ganzhi_index
from .extended import METHODS

HELP_NUDGE = "不知道如何占卜？请发送 占卜帮助 查看详细命令"
_EXTENDED_HELP = {
    "xiaoliuren": "按月、日、时起六宫掌诀",
    "meihua": "报数或按时间起本卦、互卦、变卦",
    "lenormand": "单张或三张，按顺序看牌面组合",
    "daliuren": "天地盘、四课、三传与天将",
    "taiyi": "金镜式排局，查看主算、客算与八门",
    "bazi": "公历出生时间排年、月、日、时四柱",
    "ziwei": "公历出生时间与性别排十二宫",
}
HELP_COMMANDS = (
    ("/占卜 六爻 [所问之事]", "三枚铜钱起卦，含六神、本卦与变卦"),
    ("/占卜 灵签 [所问之事]", "观音灵签一百签"),
    ("/占卜 塔罗 [单张|三张] [所问之事]", "单张指引或过去、现在、未来三张牌阵"),
    ("/占卜 塔罗 查 [关键词]", "查询牌意，例如 /占卜 塔罗 查 星星"),
    ("/占卜 奇门 [所问之事]", "默认按当前时刻起局，可在问题前指定 YYYY-MM-DD HH:MM"),
    ("/占卜 每日 [出生日期 时间 男|女]", "今日运势长图；出生信息用于八字和紫微"),
    ("/占卜 历史 [清除]", "查看或清除本会话占卜记录"),
    ("/占卜 帮助", "查看完整使用说明"),
    *((f"/占卜 {method[2]}", _EXTENDED_HELP[kind]) for kind, method in METHODS.items()),
)

# 按灵签吉凶给出一句综合建议（确定性）
_GRADE_ADVICE = {
    "上上": "诸事顺遂，适合主动出击、把握良机。",
    "上签": "运势不错，按部就班推进，多有好消息。",
    "中签": "平稳中有小波折，宜稳扎稳打、量力而行。",
    "中平": "平常心对待，守成蓄力，静待时机。",
    "下签": "今日宜低调谨慎，少做重大决定，养精蓄锐。",
}


def grade_advice(grade: str) -> str:
    return _GRADE_ADVICE.get(grade, "顺其自然，过好当下。")


def lucky_details(d: date, uid: str, grade: str) -> dict:
    """Deterministic entertainment details shared by both card renderers."""
    digest = hashlib.sha256(f"{d.isoformat()}|{uid}".encode()).digest()
    return {
        "color": ["桃花粉", "晨曦金", "青竹绿", "云雾蓝", "紫藤紫"][digest[0] % 5],
        "number": digest[1] % 9 + 1,
        "do": ["按计划推进", "整理思路", "联系朋友", "适当休息", "学习新知"][digest[2] % 5],
        "avoid": ["一时冲动", "拖延决定", "过度劳累", "争强好胜", "盲目跟风"][digest[3] % 5],
        "stars": {"上上": 5, "上签": 4, "中签": 3, "中平": 3, "下签": 2}.get(grade, 3),
    }


def daily_seed(d: date, uid: str) -> int:
    """稳定种子：同一（日期, 用户）结果固定，且跨进程一致。"""
    raw = f"{d.isoformat()}|{uid}".encode()
    return int.from_bytes(hashlib.md5(raw).digest()[:8], "big")


def draw_daily(d: date, uid: str) -> dict:
    """抽取当日运势数据：塔罗单张 + 灵签 + 周易卦象（含六神）。"""
    rng = random.Random(daily_seed(d, uid))
    card = tarot.draw_single(rng)
    number, grade, poem, explain = lingqian.draw(rng)
    cast = zhouyi.cast(rng=rng, day_index=day_ganzhi_index(d))
    return {
        "date": d,
        "card": card,
        "lingqian": (number, grade, poem, explain),
        "cast": cast,
    }


def format_daily(data: dict, uid_name: str = "") -> str:
    """把每日运势数据排版为文本。"""
    d: date = data["date"]
    name, is_reversed, meaning = data["card"]
    number, grade, poem, explain = data["lingqian"]
    cast: zhouyi.ZhouyiCast = data["cast"]

    parts = [f"🔮 每日运势 · {d:%Y-%m-%d}"]
    if uid_name:
        parts.append(f"求运人：{uid_name}")
    parts.append("")
    state = "逆位" if is_reversed else "正位"
    parts.append(f"塔罗指引：{name}（{state}）")
    parts.append(f"  → {meaning}")
    parts.append("")
    parts.append(f"灵签：第 {number} 签 · {grade}")
    parts.append(f"  签诗：{poem.replace(chr(10), ' ')}")
    parts.append(f"  → {explain}")
    parts.append("")
    parts.append(f"周易卦象：{zhouyi.hexagram_symbol(cast.number)} {cast.name}卦")
    parts.append(f"  → {cast.jie}")
    if cast.moving and cast.changed_number is not None:
        dyn = "、".join(str(i) for i in cast.moving)
        parts.append(f"  动爻第{dyn}爻，变{cast.changed_name}卦，事有变化之机")
    parts.append("")
    parts.append(f"综合建议：{grade_advice(grade)}")
    parts.append("")
    parts.append("※ 每日运势当天固定、明日刷新；仅供娱乐参考。")
    return "\n".join(parts)


def build_result(d: date | None = None, uid: str = "", uid_name: str = "") -> str:
    """生成每日运势文本（默认取今天）。"""
    d = d or date.today()
    return format_daily(draw_daily(d, uid), uid_name)
