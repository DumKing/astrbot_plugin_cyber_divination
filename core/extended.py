"""Additional divination methods with a shared, structured card result."""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from datetime import date, datetime

from . import qimen, zhouyi

# Labels, aliases, usage and visual direction are shared by commands and renderers.
METHODS = {
    "xiaoliuren": ("小六壬", ("小六壬",), "小六壬 [时间] [问题]", "朱砂、竹简与六宫掌诀"),
    "meihua": ("梅花易数", ("梅花易数", "梅花"), "梅花 [两个正整数或时间] [问题]", "水墨梅枝、月白与青金"),
    "lenormand": ("雷诺曼", ("雷诺曼",), "雷诺曼 [单张|三张] [问题]", "法式植物版画、墨绿与暖金"),
    "daliuren": ("大六壬", ("大六壬",), "大六壬 [时间] [问题]", "古铜天地盘、墨蓝与四课三传"),
    "taiyi": ("太乙神数", ("太乙神数", "太乙"), "太乙 [年计|日计|时计] [时间] [问题]", "靛蓝天文星图与金色星轨"),
    "bazi": ("八字排盘", ("八字排盘", "八字"), "八字 [YYYY-MM-DD] [HH:MM] [问题]", "暖白宣纸、四柱与五行色标"),
    "ziwei": ("紫微斗数", ("紫微斗数", "紫微"), "紫微 [YYYY-MM-DD] [HH:MM] [男|女] [问题]", "紫金星图与十二宫格"),
}

_BRANCHES = "子丑寅卯辰巳午未申酉戌亥"
_MONTHS = ("正", "二", "三", "四", "五", "六", "七", "八", "九", "十", "十一", "十二")
_TERM_TRADITIONAL = str.maketrans("惊蛰满种处谷", "驚蟄滿種處穀")
_PALACES = (
    ("大安", "先求稳定，把手上的事理顺。"),
    ("留连", "可能遇到反复，给事情留出余量。"),
    ("速喜", "适合及时沟通，抓住眼前的小机会。"),
    ("赤口", "注意措辞，遇到争论先缓一缓。"),
    ("小吉", "从容易完成的一步开始，稳步推进。"),
    ("空亡", "信息还不够明确，适合先核实。"),
)
_TRIGRAMS = ("乾", "兑", "离", "震", "巽", "坎", "艮", "坤")
_PATTERNS = ((7, 7, 7), (7, 7, 8), (7, 8, 7), (7, 8, 8),
             (8, 7, 7), (8, 7, 8), (8, 8, 7), (8, 8, 8))

# Original, short keywords for the traditional 36-card sequence.
_LENORMAND = (
    ("骑士", "消息、到来"), ("四叶草", "机会、轻松"), ("船", "远方、探索"),
    ("房屋", "家庭、根基"), ("树", "成长、积累"), ("云", "模糊、疑虑"),
    ("蛇", "迂回、复杂"), ("棺材", "结束、停顿"), ("花束", "礼物、喜悦"),
    ("镰刀", "决断、切割"), ("鞭子", "争论、反复"), ("鸟", "交谈、紧张"),
    ("孩子", "开始、好奇"), ("狐狸", "策略、警觉"), ("熊", "力量、庇护"),
    ("星星", "方向、愿景"), ("鹳", "变化、迁移"), ("狗", "朋友、信任"),
    ("塔", "边界、机构"), ("花园", "社交、公开"), ("山", "障碍、延迟"),
    ("岔路", "选择、分歧"), ("老鼠", "消耗、担忧"), ("心", "感情、热情"),
    ("戒指", "承诺、循环"), ("书", "知识、未揭晓"), ("信", "文字、通知"),
    ("男人", "人物、当事人"), ("女人", "人物、当事人"), ("百合", "平和、成熟"),
    ("太阳", "明朗、活力"), ("月亮", "感受、声誉"), ("钥匙", "线索、解决"),
    ("鱼", "资源、流动"), ("锚", "稳定、坚持"), ("十字架", "责任、负担"),
)


@dataclass
class Reading:
    summary: str
    rows: list[tuple[str, str]]
    question: str = ""
    hexagram_lines: dict[str, list[str]] | None = None

    def text(self, title: str) -> str:
        lines = [f"🔮 {title}"]
        if self.question:
            lines.append(f"所问之事：{self.question}")
        lines.extend(f"{label}：{value}" for label, value in self.rows)
        lines.append("※ 仅供娱乐参考。")
        return "\n".join(lines)


def _lunar(moment: datetime):
    from lunar_python import Solar

    return Solar.fromYmdHms(moment.year, moment.month, moment.day, moment.hour, moment.minute, 0).getLunar()


def _timed(text: str, now: datetime) -> tuple[datetime, str]:
    raw, question = qimen.split_time_question(text)
    if not raw:
        return now, question
    moment = qimen.parse_time(raw)
    if moment is None:
        raise ValueError("时间格式有误，请使用 YYYY-MM-DD HH:MM。")
    return moment, question


def _birth(text: str) -> tuple[datetime, str]:
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})(?:\s+(男|女))?", text.strip())
    if not match:
        raise ValueError("请输入公历出生日期及时间，格式 YYYY-MM-DD HH:MM。")
    moment = qimen.parse_time(match[1])
    if moment is None or not 1901 <= moment.year <= 2099:
        raise ValueError("请输入有效的公历出生日期，年份范围为 1901–2099。")
    return moment, match[2] or ""


def xiaoliuren(text: str, now: datetime) -> Reading:
    moment, question = _timed(text, now)
    lunar = _lunar(moment)
    month, day = abs(lunar.getMonth()), lunar.getDay()
    branch = lunar.getTimeZhi()
    month_palace = (month - 1) % 6
    day_palace = (month_palace + day - 1) % 6
    name, advice = _PALACES[(day_palace + _BRANCHES.index(branch)) % 6]
    return Reading(name, [
        ("起课时间", f"{moment:%Y-%m-%d %H:%M}"),
        ("农历", f"{month}月{day}日 · {branch}时"),
        ("月宫 / 日宫", f"{_PALACES[month_palace][0]} / {_PALACES[day_palace][0]}"),
        ("时宫", name), ("掌诀六宫", " · ".join(label for label, _ in _PALACES)),
        ("趣味指引", advice),
    ], question)


def meihua(text: str, now: datetime) -> Reading:
    numbers = re.match(r"^(\d{1,12})[\s,，]+(\d{1,12})(?=\s|$)", text)
    if numbers:
        first, second = map(int, numbers.groups())
        if min(first, second) < 1:
            raise ValueError("报数请使用两个正整数。")
        source = f"报数 {first}、{second}"
        question = text[numbers.end():].strip()
    else:
        moment, question = _timed(text, now)
        lunar = _lunar(moment)
        first = _BRANCHES.index(lunar.getYearZhi()) + 1 + abs(lunar.getMonth()) + lunar.getDay()
        second = first + _BRANCHES.index(lunar.getTimeZhi()) + 1
        source = f"时间起卦 · {moment:%Y-%m-%d %H:%M}"
    upper_sum = first
    lower_sum = second
    move_sum = first + second if numbers else second
    upper, lower = (upper_sum - 1) % 8, (lower_sum - 1) % 8
    moving = (move_sum - 1) % 6
    lines = list(_PATTERNS[lower] + _PATTERNS[upper])
    base_number, _, _ = zhouyi.resolve_hexagram(lines)
    base_lines = lines.copy()
    nuclear_lines = lines[1:4] + lines[2:5]
    nuclear, _, _ = zhouyi.resolve_hexagram(nuclear_lines)
    lines[moving] = 9 if lines[moving] == 7 else 6
    changed, changed_lines = zhouyi.changed_hexagram(lines)
    name, classic, reading = zhouyi.HEXAGRAMS[base_number]
    body, use = (upper, lower) if moving < 3 else (lower, upper)
    return Reading(name, [
        ("起卦", source), ("本卦", f"{zhouyi.hexagram_symbol(base_number)} {name}"),
        ("互卦", zhouyi.HEXAGRAMS[nuclear][0]), ("变卦", zhouyi.HEXAGRAMS[changed][0]),
        ("动爻", f"第 {moving + 1} 爻"), ("体卦 / 用卦", f"{_TRIGRAMS[body]} / {_TRIGRAMS[use]}"),
        ("卦辞", classic), ("内置简解", reading),
    ], question, hexagram_lines={
        label: ["阳" if line in (7, 9) else "阴" for line in reversed(hexagram)]
        for label, hexagram in (
            ("本卦", base_lines), ("互卦", nuclear_lines), ("变卦", changed_lines),
        )
    })


def lenormand(text: str, now: datetime, rng: random.Random | None = None) -> Reading:
    parts = text.split(maxsplit=1)
    first, rest = (parts + ["", ""])[:2]
    count = 1 if first == "单张" else 3
    question = rest.strip() if first in {"单张", "三张"} else text
    cards = (rng or random.SystemRandom()).sample(range(36), count)
    rows = []
    for position, index in enumerate(cards, start=1):
        name, words = _LENORMAND[index]
        rows.append((f"第 {position} 张 · {index + 1} {name}", words))
    rows.extend(
        (f"组合 · {_LENORMAND[a][0]} → {_LENORMAND[b][0]}",
         f"先看「{_LENORMAND[a][1]}」的主题，再用「{_LENORMAND[b][1]}」补充方向。")
        for a, b in zip(cards, cards[1:])
    )
    return Reading(" · ".join(_LENORMAND[i][0] for i in cards), rows, question)


def daliuren(text: str, now: datetime) -> Reading:
    from kinliuren.kinliuren import Liuren

    moment, question = _timed(text, now)
    lunar = _lunar(moment)
    term = lunar.getPrevJieQi().getName().translate(_TERM_TRADITIONAL)
    chart = Liuren(term, _MONTHS[abs(lunar.getMonth()) - 1],
                   lunar.getDayInGanZhi(), lunar.getTimeInGanZhi()).result(0)
    if not isinstance(chart, dict):
        raise ValueError("上游排盘库未能生成此时间的课盘，请尝试其他时间。")
    shape = " · ".join(chart["格局"])
    rows = [("起课", f"{moment:%Y-%m-%d %H:%M} · {chart['日期']}"), ("格局", shape)]
    for field in ("四課", "三傳"):
        rows.extend((label, " · ".join(map(str, values))) for label, values in chart[field].items())
    rows.extend((label, " ".join(values)) for label, values in chart["天地盤"].items())
    return Reading(shape, rows, question)


def taiyi(text: str, now: datetime) -> Reading:
    from kintaiyi.kintaiyi import Taiyi

    modes = {"年计": 0, "日计": 2, "时计": 3}
    parts = text.split(maxsplit=1)
    first, rest = (parts + ["", ""])[:2]
    mode = first if first in modes else "日计"
    moment, question = _timed(rest if first in modes else text, now)
    chart = Taiyi(moment.year, moment.month, moment.day, moment.hour, moment.minute).pan(modes[mode], 1)
    formation = chart["局式"]["文"]
    rows = [("起局", f"{moment:%Y-%m-%d %H:%M} · {mode} · 金镜式"), ("局式", formation)]
    for key in ("太乙落宮", "文昌", "始擊", "定目", "主算", "客算", "定算",
                "主將", "客將", "八門分佈"):
        if key in chart:
            value = chart[key]
            if isinstance(value, dict):
                value = " · ".join(f"{name} {item}" for name, item in value.items())
            elif isinstance(value, (tuple, list)):
                value = " · ".join(map(str, value))
            rows.append((key, str(value)))
    return Reading(f"{mode} · {formation}", rows, question)


def bazi(text: str, now: datetime) -> Reading:
    parts = text.split(maxsplit=2)
    moment, _ = _birth(" ".join(parts[:2]))
    question = parts[2].strip() if len(parts) == 3 else ""
    chart = _lunar(moment).getEightChar()
    rows = [
        ("年柱", f"{chart.getYear()} · {chart.getYearWuXing()} · {chart.getYearShiShenGan()}"),
        ("月柱", f"{chart.getMonth()} · {chart.getMonthWuXing()} · {chart.getMonthShiShenGan()}"),
        ("日柱", f"{chart.getDay()} · {chart.getDayWuXing()} · 日主"),
        ("时柱", f"{chart.getTime()} · {chart.getTimeWuXing()} · {chart.getTimeShiShenGan()}"),
        ("日主", chart.getDayGan()), ("命宫 / 身宫", f"{chart.getMingGong()} / {chart.getShenGong()}"),
    ]
    return Reading(" · ".join((chart.getYear(), chart.getMonth(), chart.getDay(), chart.getTime())), rows, question)


def ziwei(text: str, now: datetime) -> Reading:
    from iztro_py import astro

    parts = text.split(maxsplit=3)
    moment, gender = _birth(" ".join(parts[:3]))
    question = parts[3].strip() if len(parts) == 4 else ""
    if not gender:
        raise ValueError("请在出生日期和时间后加上 男 或 女，以便按排盘规则计算。")
    chart = astro.by_solar_hour(moment.date().isoformat(), moment.hour, gender).to_iztro_dict()
    rows = [("五行局", chart["fiveElementsClass"]), ("命主 / 身主", f"{chart['soul']} / {chart['body']}")]
    for palace in chart["palaces"]:
        stars = "、".join(
            star["name"] + (f"化{star['mutagen']}" if star["mutagen"] else "")
            for star in palace["majorStars"] + palace["minorStars"]
        )
        label = f"{palace['name']} · {palace['heavenlyStem']}{palace['earthlyBranch']}"
        rows.append((label, stars or "无列示主辅星"))
    return Reading(chart["fiveElementsClass"], rows, question)


BUILDERS = {
    "xiaoliuren": xiaoliuren, "meihua": meihua, "lenormand": lenormand,
    "daliuren": daliuren, "taiyi": taiyi, "bazi": bazi, "ziwei": ziwei,
}



def daily_panels(
    day: datetime, uid: str, birth: str = "", *,
    birthday: date | None = None, profile_gender: str = "",
) -> list[dict]:
    """Calculate stable daily extras at noon, with optional personal charts."""
    from .daily import daily_seed

    profile_date = birthday or day.date()
    hour_only = re.fullmatch(r"(\d{2}:\d{2})(?:\s+(男|女))?", birth)
    if not birth:
        birth = f"{profile_date.isoformat()} 12:00"
    elif hour_only:
        birth = f"{profile_date.isoformat()} {hour_only[0]}"
    moment, gender = _birth(birth)
    gender = gender or profile_gender or "男"

    panels = []
    for kind, (title, _, _, _) in METHODS.items():
        try:
            if kind == "lenormand":
                reading = lenormand("三张", day, random.Random(daily_seed(day.date(), uid + "|lenormand")))
            elif kind == "bazi":
                reading = bazi(moment.strftime("%Y-%m-%d %H:%M"), day)
            else:
                reading = BUILDERS[kind](f"{moment:%Y-%m-%d %H:%M} {gender}" if kind == "ziwei" else "", day)
            rows = reading.rows
        except (ImportError, KeyError, TypeError, ValueError):
            rows = [("排盘状态", "此项暂时无法排盘，可尝试单独查询。")]
        panels.append({"title": title, "rows": rows})
    chart = qimen.make_pan(day)
    panels.insert(0, {"title": "奇门遁甲", "rows": [
        ("起局时间", f"{day:%Y-%m-%d %H:%M}"), ("简式排盘", qimen.summarize(chart)),
        *[(f"第 {index} 宫", qimen.display_palace(chart, index)) for index in (4, 9, 2, 3, 5, 7, 8, 1, 6)],
    ]})
    return panels
