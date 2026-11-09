"""Cyber Divination v3 card renderer.

No external fonts, scripts, network calls or template engine required here.
All assets are loaded from this plugin and embedded inline for the remote AstrBot T2I renderer.
Card algorithms and interpretation remain in core/*.py.
"""
from __future__ import annotations

from datetime import date
from html import escape
from pathlib import Path
import re
from typing import Any

from .daily import HELP_COMMANDS, HELP_NUDGE, lucky_details
from .extended import METHODS

ROOT = Path(__file__).resolve().parent.parent
CARD_KINDS = {"zhouyi", "lingqian", "tarot", "qimen", "daily"}


def e(value: Any) -> str:
    return escape(str(value if value is not None else ""), quote=True)


def asset(filename: str) -> str:
    """Load only hard-coded trusted SVG/CSS paths from the plugin package."""
    return (ROOT / filename).read_text(encoding="utf-8")


def kind_for(title: str) -> str:
    for key, (name, _, _, _) in METHODS.items():
        if title == name:
            return key
    for key, needle in (
        ("zhouyi", ("周易", "六爻")),
        ("lingqian", ("灵签",)),
        ("tarot", ("塔罗",)),
        ("qimen", ("奇门",)),
        ("daily", ("每日", "运势")),
    ):
        if any(part in title for part in needle):
            return key
    return "zhouyi"


def split_ai(lines: list[str]) -> tuple[list[str], str]:
    left: list[str] = []
    right: list[str] = []
    active = False
    for line in lines:
        if not active and "🔮 AI 解读：" in line:
            before, after = line.split("🔮 AI 解读：", 1)
            if before.strip():
                left.append(before.strip())
            right.append(after.strip())
            active = True
        elif active:
            right.append(line)
        else:
            left.append(line)
    return left, "\n".join(right).strip()


def field(lines: list[str], key: str) -> str:
    for line in lines:
        if line.strip().startswith(key):
            return line.strip()[len(key):].strip()
    return ""


def first_sentence(value: str, max_length: int = 115) -> str:
    source = (value or "").strip()
    s = re.split(r"(?<=[。！？])", source, maxsplit=1)[0]
    if len(s) > max_length:
        return s[:max_length].rstrip("，、； ") + "……"
    return s


def labeled(label: str, text: Any, *, cls: str = "") -> str:
    if text is None or str(text) == "":
        return ""
    return (f'<div class="labeled {cls}">'
            f'<span class="label">{e(label)}</span>'
            f'<span class="value">{e(text)}</span></div>')


def ai_block(text: str, label: str = "AI 解读") -> str:
    if not text:
        return ""
    return ('<section class="ai-block">'
            f'<div class="ai-head">✧ {e(label)}</div>'
            f'<div class="ai-copy">{e(text)}</div></section>')


def svg_hexagram(lines: list[int], size: str = "large") -> str:
    """Input is always bottom-to-top (original ZhouyiCast convention)."""
    if not lines or len(lines) != 6:
        return ""
    bars = []
    for v in reversed(lines):
        if v in (7, 9):
            bars.append('<div class="glyph-bar whole"></div>')
        else:
            bars.append('<div class="glyph-bar split"><i></i><i></i></div>')
    return f'<div class="hex-glyph {size}" aria-label="六爻卦象">' + "".join(bars) + '</div>'


def yaos(cast: Any) -> str:
    name = {6: "老阴", 7: "少阳", 8: "少阴", 9: "老阳"}
    rows = []
    for i, v in enumerate(cast.lines):
        glyph = '<span class="yaobar whole"></span>' if v in (7, 9) else '<span class="yaobar half"></span><span class="yaospace"></span><span class="yaobar half"></span>'
        marker = "○" if v == 9 else "×" if v == 6 else ""
        rows.append('<div class="yao-item' + (' changing' if marker else '') + '">'
                    + f'<span>第{i + 1}爻</span><span>{e(cast.gods[i])}</span>'
                    + f'<span>{name[v]}</span>'
                    + '<span class="yao-graphic">' + glyph + '</span>'
                    + f'<span class="change-mark">{marker}</span></div>')
    return '<div class="yao-list">' + "".join(rows) + '</div>'


def zhouyi_body(lines: list[str], ai: str, cast: Any | None, question: str, sender: str) -> str:
    q = question or field(lines, "所问之事：")
    s = sender or field(lines, "求卦人：")
    top = f'<div class="query">{labeled("所问之事", q)}{labeled("求卦人", s)}</div>'
    if cast is None:
        # Safety fallback for external callers that still use text-only API.
        text = '<div class="plain-output">' + '<div>'.join(e(line) for line in lines if line.strip()) + '</div></div>'
        return top + text + ai_block(ai) + '<p class="safety">※ 周易占断仅供参考，重大决策请理性判断。</p>'
    summary = ('<div class="hex-summary">' + svg_hexagram(cast.lines)
               + '<div class="hex-title"><div class="eyebrow">本卦 · 第' + e(cast.number) + '卦</div>'
               + f'<div class="name">{e(cast.upper)}{e(cast.lower)}{e(cast.name)}</div>'
               + f'<div class="trigram">{e(cast.upper)} 上 · {e(cast.lower)} 下</div></div></div>')
    detail = ('<div class="guaci">'
              + labeled("卦辞", cast.guaci) + labeled("简解", cast.jie)
              + '</div>')
    if cast.moving:
        nums = "、".join(str(n) for n in cast.moving)
        shift = '<div class="move-banner">动爻：第' + e(nums) + '爻 · 卦象有变</div>'
        change_detail = ('<div class="changed-detail">'
                         f'<h3>变卦 · {e(cast.changed_name)}卦</h3>'
                         + labeled('变卦卦辞', cast.changed_guaci)
                         + labeled('变卦简解', cast.changed_jie)
                         + '</div>')
        conclusion = first_sentence(cast.changed_jie or cast.jie)
    else:
        shift = '<div class="move-banner">六爻安静 · 暂无动象 · 宜静观其变</div>'
        change_detail = ""
        conclusion = first_sentence(cast.jie)
    return ('<div class="section-knot">◇</div>' + top
            + '<div class="gold-rule"></div>'
            + '<section class="gua-box">' + summary + detail + '</section>'
            + '<div class="yao-heading">六爻排盘 <small>由下至上 · 动爻以 ○ / × 标识</small></div>'
            + yaos(cast) + shift + change_detail + ai_block(ai)
            + '<div class="verdict"><span>结 论</span>' + e(conclusion) + '</div>'
            + '<p class="safety">※ 占卜仅供娱乐参考，重大决定仍需结合现实情况理性判断。</p>')


def lingqian_body(lines: list[str], ai: str, data: dict, question: str, sender: str) -> str:
    num = data.get("number")
    grade = data.get("grade")
    poem = data.get("poem")
    explain = data.get("explain")
    if num is None:
        m = re.search(r"第\s*(\d+)\s*签\s*·\s*(\S+)", "\n".join(lines))
        if m:
            num, grade = m.groups()
    if not poem:
        raw = "\n".join(lines)
        m = re.search(r"签诗：\s*\n(.*?)\n\s*解曰：", raw, flags=re.S)
        if m:
            poem = m.group(1).strip()
    explain = explain or field(lines, "解曰：")
    poem = poem or "签诗请以原文为准"
    verse = [x.strip() for x in str(poem).splitlines() if x.strip()]
    if len(verse) == 1 and len(verse[0]) >= 28 and ("，" in verse[0] or "。" in verse[0]):
        verse = [x.strip() for x in re.split(r"(?<=[。；])", verse[0]) if x.strip()]
    verse_html = "".join(f'<div class="verse-line">{e(x)}</div>' for x in verse)
    return ('<div class="scroll-crown">✧ 莲 心 如 镜 ✧</div>'
            '<div class="seal-row"><div class="sign-num">第 <b>' + e(num or "—") + '</b> 签</div>'
            '<div class="vermillion-seal"><span>' + e(grade or "灵签") + '</span></div></div>'
            f'<div class="wish-meta">{labeled("所问",question or field(lines,"所问之事："))}'
            + labeled("求签人", sender or field(lines,"求签人：")) + '</div>'
            '<div class="scroll-divider">❁</div>'
            '<div class="verse-wrap"><div class="verse-tag">签 诗</div>' + verse_html + '</div>'
            '<section class="explain"><h3>解曰</h3><p>' + e(explain or "请结合签诗作理性解读。") + '</p></section>'
            + ai_block(ai, "AI 解签")
            + '<div class="red-mark">以签观心 · 诸事从容</div>'
            + '<p class="safety">仅供娱乐 · 请勿以签代替现实判断</p>')


def tarot_motif(name: str, idx: int) -> str:
    """Custom non-copyright illustrative emblems; no claim to reproduce canonical Rider-Waite cards."""
    motifs = ['sun', 'moon', 'star', 'flower', 'key', 'tower', 'cup', 'wave']
    choice = sum(ord(c) for c in name) % len(motifs)
    motif = motifs[choice]
    common = '<circle cx="160" cy="142" r="83" fill="none" stroke="#ebcf9c" stroke-width="1" opacity=".75"/><circle cx="160" cy="142" r="65" fill="none" stroke="#d2b3cf" opacity=".4"/>'
    if motif == 'sun':
        inner = '<circle cx="160" cy="139" r="36" fill="#ffe9a7"/><circle cx="160" cy="139" r="28" fill="#e9b773"/>'
        inner += ''.join(f'<path d="M160 83 L154 101 L166 101Z" fill="#e9d4a9" transform="rotate({n*30} 160 139)"/>' for n in range(12))
    elif motif == 'moon':
        inner = '<circle cx="160" cy="142" r="57" fill="#e7d2ae"/><circle cx="178" cy="122" r="54" fill="#27133f"/><g stroke="#ebcf9c" stroke-width="2"><path d="M85 146l8 -10 8 10-8 10zM230 118l8 -10 8 10-8 10z" fill="#e7c98f"/></g>'
    elif motif == 'star':
        inner = '<polygon points="160,78 174,121 220,121 183,148 197,191 160,166 123,191 137,148 100,121 146,121" fill="#e8c992" stroke="#fff0c3" stroke-width="2"/>'
    elif motif == 'flower':
        inner = ''.join(f'<ellipse cx="160" cy="102" rx="20" ry="47" fill="#d3aac9" opacity=".8" transform="rotate({i*60} 160 143)"/>' for i in range(6)) + '<circle cx="160" cy="143" r="21" fill="#ffe3a7"/>'
    elif motif == 'key':
        inner = '<circle cx="160" cy="103" r="30" fill="none" stroke="#f4d8a4" stroke-width="11"/><path d="M160 130v90m0-17h35v-13h-35m0-15h24" stroke="#f4d8a4" stroke-width="13" fill="none" stroke-linecap="round"/>'
    elif motif == 'tower':
        inner = '<path d="M120 212L126 120h68l7 92z" fill="#ceb5d2" stroke="#e5c989" stroke-width="3"/><path d="M114 118l21-29 15 18 13-31 24 31 13-18 19 29z" fill="#e7d2a1"/><rect x="152" y="160" width="18" height="52" rx="9" fill="#412c58"/>'
    elif motif == 'cup':
        inner = '<path d="M112 95h96c0 52-19 69-48 72-30-3-48-20-48-72Z" fill="#e0c38e" stroke="#fff1c5" stroke-width="3"/><path d="M160 167v35m-35 0h70" stroke="#f7d9a4" stroke-width="12" stroke-linecap="round"/><path d="M112 109C76 98 88 151 132 150m76-41c35-11 24 42-19 41" fill="none" stroke="#ddc29c" stroke-width="7"/>'
    else:
        inner = '<path d="M60 165q50-42 100 0t100 0m-200 28q50-42 100 0t100 0m-200 28q50-42 100 0t100 0" fill="none" stroke="#d4bddf" stroke-width="8" opacity=".86"/>'
    return f'<svg viewBox="0 0 320 270" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">{common}{inner}<path d="M64 238Q160 218 256 238" stroke="#f4dcb2" opacity=".6" fill="none"/></svg>'


def tarot_body(lines: list[str], ai: str, data: dict, question: str, sender: str) -> str:
    raw_cards = data.get("cards") or []
    cards: list[tuple[str, str, bool, str]] = []
    for c in raw_cards:
        if len(c) >= 4:
            cards.append((str(c[0]), str(c[1]), bool(c[2]), str(c[3])))
    if not cards:
        for line in lines:
            m = re.match(r"【(.+?)】(.+?)（(正位|逆位)）", line.strip())
            if m:
                cards.append((m.group(1), m.group(2), m.group(3) == "逆位", ""))
            elif cards and line.strip().startswith("→"):
                p, n, r, _ = cards[-1]
                cards[-1] = p, n, r, line.strip().lstrip("→ ")
    if not cards:
        return '<div class="tarot-fallback">' + '<p>'.join(e(x) for x in lines if x.strip()) + '</div>'
    three = len(cards) > 1
    card_html = []
    meaning_html = []
    for i, (pos, name, rev, meaning) in enumerate(cards):
        state = "逆位" if rev else "正位"
        card_html.append(
            '<div class="arcana"><div class="arcana-outer"><div class="arcana-frame">'
            f'<div class="arcana-number">{e(str(i+1).zfill(2))} · {e(pos)}</div>'
            + '<div class="arcana-picture">' + tarot_motif(name, i) + '</div>'
            + f'<div class="arcana-name">{e(name)}</div>'
            + f'<div class="arcana-state">✦ {e(state)} ✦</div>'
            + '</div></div></div>'
        )
        meaning_html.append(f'<div class="tarot-interpret"><div class="reading-title">{e(pos)} · {e(name)} <span>{state}</span></div><p>{e(meaning)}</p></div>')
    return ('<div class="tarot-intro">'
            + labeled('所问之事', question or field(lines,'所问之事：'))
            + labeled('问卜人', sender or field(lines,'问卜人：'))
            + '</div><div class="tarot-spread ' + ('three' if three else 'single') + '">' + ''.join(card_html) + '</div>'
            + '<h3 class="tarot-reading-heading">✧ 牌阵解读 ✧</h3>'
            + '<div class="tarot-meanings">' + ''.join(meaning_html) + '</div>'
            + ai_block(ai, '星月指引')
            + '<div class="tarot-closing">AS ABOVE · SO BELOW</div>'
            + '<p class="safety">占卜仅供娱乐参考 · 未来仍由行动决定</p>')


def qimen_body(lines: list[str], ai: str, data: dict, question: str, sender: str) -> str:
    pan = data.get('pan')
    if pan is None:
        return '<div class="qimen-fallback">' + '<p>'.join(e(x) for x in lines if x.strip()) + '</div>' + ai_block(ai)
    dt = pan['dt']
    pillars = pan['pillars']
    infobox = ('<div class="qimen-data">'
               + labeled('起局时刻', dt.strftime('%Y-%m-%d %H:%M'))
               + labeled('遁局', f"{pan['phase']}{pan['ju']}局 · {pan['yuan']} · {pan['term']}")
               + labeled('四柱', ' · '.join(pillars))
               + labeled('值符 / 值使', f"{pan['zhifu_star']} · {pan['zhishi_door']}门")
               + '</div>')
    nodes = []
    names = {1:'坎一',2:'坤二',3:'震三',4:'巽四',5:'中五',6:'乾六',7:'兑七',8:'艮八',9:'离九'}
    for idx in [4,9,2,3,5,7,8,1,6]:
        instrument = next((k for k, v in pan['di_pan'].items() if v == idx), '—')
        star = pan['tian_pan'].get(idx, '—')
        door = pan['men_pan'].get(idx, '—')
        god = pan['shen_pan'].get(idx, '—')
        cn = 'center' if idx == 5 else ''
        nodes.append(f'<div class="palace {cn}"><div class="palace-head"><b>{names[idx]}</b><span>{e(str(idx).zfill(2))}</span></div>'
                     f'<div class="palace-star">{e(star)} <small>九星</small></div>'
                     f'<div class="palace-meta"><span>{e(door)}门</span><span>{e(god)}</span><span>地盘 {e(instrument)}</span></div>'
                     + ('<div class="palace-foot">寄坤二</div>' if idx == 5 else '') + '</div>')
    advise = []
    section = False
    for line in lines:
        st = line.strip()
        if st.startswith('简断：'):
            section = True
            continue
        if section and st and not st.startswith('※'):
            advise.append(st.lstrip('· '))
    return ('<div class="qimen-inquiry">' + labeled('所问之事', question or field(lines,'所问之事：')) + labeled('问卜人', sender or field(lines,'问卜人：')) + '</div>'
            + infobox
            + '<div class="qimen-disc"><div class="disc-caption">北斗 · 九宫 · 八门</div><div class="qimen-grid">'
            + ''.join(nodes) + '</div><div class="disc-caption bottom">天 · 地 · 人 · 神</div></div>'
            + '<div class="qimen-advice"><h3>格局解析</h3>' + ''.join(f'<p>✦ {e(x)}</p>' for x in advise) + '</div>'
            + ai_block(ai, 'AI 断局')
            + '<p class="safety">简式时家奇门 · 未含超接置闰等细则 · 仅供娱乐</p>')


def daily_body(lines: list[str], ai: str, data: dict, sender: str) -> str:
    d = data.get('daily')
    if not d:
        return '<div class="daily-fallback">' + '<p>'.join(e(x) for x in lines if x.strip()) + '</div>' + ai_block(ai)
    today: date = d['date']
    card_name, is_reversed, meaning = d['card']
    number, grade, poem, explain = d['lingqian']
    cast = d['cast']
    weekday = '一二三四五六日'[today.weekday()]
    move = '' if not cast.moving else ' · 动爻第' + '、'.join(str(n) for n in cast.moving) + '爻'
    advice = field(lines, '综合建议：')
    lucky = lucky_details(today, data.get("uid", ""), grade)
    stars = lucky["stars"]
    lucky_html = ('<section class="daily-lucky"><div class="lucky-head">✧ 今日趣味幸运小贴士</div>'
                  '<div class="lucky-row">'
                  f'<span><b>幸运指数</b><i>{"★"*stars}{"☆"*(5-stars)}</i></span>'
                  f'<span><b>幸运色</b>{e(lucky["color"])}</span>'
                  f'<span><b>幸运数字</b>{lucky["number"]}</span>'
                  '</div><div class="lucky-row last">'
                  f'<span><b>宜</b>{e(lucky["do"])}</span><span><b>忌</b>{e(lucky["avoid"])}</span>'
                  '</div></section>')
    blocks = (
        '<article class="fortune-block tarot"><div class="fortune-icon">✦</div><div class="fortune-title">塔罗指引 <small>TAROT</small></div>'
        f'<div class="fortune-result">{e(card_name)} <em>{"逆位" if is_reversed else "正位"}</em></div><p>{e(meaning)}</p></article>'
        '<article class="fortune-block ling"><div class="fortune-icon">❀</div><div class="fortune-title">今日灵签 <small>LOTUS</small></div>'
        f'<div class="fortune-result">第 {e(number)} 签 <em>{e(grade)}</em></div><p>{e(explain)}</p></article>'
        '<article class="fortune-block gua"><div class="fortune-icon">☰</div><div class="fortune-title">周易卦象 <small>I CHING</small></div>'
        f'<div class="fortune-result">{e(cast.name)}卦 <em>{e(cast.upper)}上 · {e(cast.lower)}下</em></div><p>{e(cast.jie)}{e(move)}</p></article>'
    )
    panels = []
    for panel in data.get("daily_panels", ()):
        fields = "".join(
            f'<div><dt>{e(label)}</dt><dd>{e(value)}</dd></div>'
            for label, value in panel["rows"]
        )
        note = panel.get("ai_note")
        note_html = (
            f'<aside class="daily-panel-ai"><b>AI 简解</b><p>{e(note)}</p></aside>' if note else ""
        )
        panels.append(
            f'<section class="daily-panel"><h2>{e(panel["title"])}</h2>'
            f'<dl>{fields}</dl>{note_html}</section>'
        )
    help_entries = ''.join(
        f'<li><b>{e(command)}</b><span>{e(description)}</span></li>'
        for command, description in HELP_COMMANDS
    )
    return (f'<div class="daily-date"><span class="day">{today:%m}.{today:%d}</span><span class="weekday">星期{weekday} · {today:%Y}</span></div>'
            f'<div class="daily-help-nudge">{e(HELP_NUDGE)}</div>'
            + (f'<div class="daily-user">致 · {e(sender or field(lines, "求运人："))}</div>' if sender or field(lines,'求运人：') else '')
            + '<div class="daily-trio">' + blocks + '</div>'
            + lucky_html
            + '<div class="today-note"><div class="today-label">✦ 今日寄语</div><div>' + e(advice or '顺其自然，过好当下。') + '</div></div>'
            + ai_block(ai,'AI 今日解读')
            + (f'<div class="daily-panels">{"".join(panels)}</div>' if panels else "")
            + '<section class="daily-help"><h2>赛博占卜 · 使用说明</h2>'
            + f'<ul>{help_entries}</ul></section>'
            + '<p class="safety">每日结果当天固定 · 仅供娱乐参考</p>')


def build_card_html(
    title: str,
    subtitle: str,
    body_lines: list[str],
    footer: str,
    *,
    zhouyi_cast: Any | None = None,
    question: str = "",
    sender: str = "",
    theme: str = "auto",
    card_data: dict | None = None,
) -> str:
    """Build self-contained HTML from original plugin data.

    Original 4 positional arguments and v2 kwargs remain accepted.
    `card_data` carries original algorithm outputs, never AI invented data.
    """
    kind = kind_for(title)
    data = card_data or {}
    base, ai = split_ai(body_lines)
    if kind in METHODS:
        fields = "".join(
            f'<li><b>{e(label)}</b><span>{e(value)}</span></li>'
            for label, value in data.get("extended_rows", ())
        )
        body = (labeled("所问之事", question) + f'<ul class="method-fields">{fields}</ul>'
                + ai_block(ai))
        css = asset("templates/base.css") + "\n" + asset("templates/extended.css")
        return (
            '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=900">'
            f'<style>{css}</style></head><body><div class="page extended {kind}">'
            f'<header><h1>{e(title)}</h1><p>{e(subtitle)}</p></header>'
            f'<main>{body}</main><footer>✦　{e(footer or "仅供娱乐")}　✦</footer>'
            '</div></body></html>'
        )
    if kind == 'zhouyi':
        body = zhouyi_body(base, ai, zhouyi_cast or data.get('cast'), question, sender)
    elif kind == 'lingqian':
        body = lingqian_body(base, ai, data, question, sender)
    elif kind == 'tarot':
        body = tarot_body(base, ai, data, question, sender)
    elif kind == 'qimen':
        body = qimen_body(base, ai, data, question, sender)
    else:
        body = daily_body(base, ai, data, sender)

    kind_css = kind
    # Preserve each distinct layout; gold only swaps the palette without changing structure.
    css = asset('templates/base.css') + '\n' + asset(f'templates/{kind_css}.css')
    if theme == 'gold' and kind != 'zhouyi':
        css += '\n' + asset('templates/gold-variant.css')
    # Dedicated theme illustrations are bundled as SVG and embedded inline to work with remote T2I.
    arts = {
        'zhouyi': asset('assets/plum.svg') + asset('assets/taiji.svg') + asset('assets/mountains.svg'),
        'lingqian': asset('assets/lotus.svg'),
        'tarot': asset('assets/celestial.svg'),
        'qimen': asset('assets/luopan.svg'),
        'daily': asset('assets/sunrise.svg'),
    }
    page_top = {
        'zhouyi': '<div class="preface">东方术数 · 六爻占断</div><h1>周易六爻</h1><div class="subheading">三枚铜钱 · 六爻成卦</div>',
        'lingqian': '<div class="preface">LOTUS ORACLE · 壹佰灵签</div><h1>观音灵签</h1><div class="subheading">一签一愿 · 静听天机</div>',
        'tarot': '<div class="preface">THE CELESTIAL ORACLE</div><h1>塔罗占卜</h1><div class="subheading">✦ 星辰为引 · 牌面映心 ✦</div>',
        'qimen': '<div class="preface">天 · 地 · 人 · 神</div><h1>奇门遁甲</h1><div class="subheading">天地为盘 · 九宫演机</div>',
        'daily': '<div class="preface">a little fortune for you</div><h1>今日运势</h1><div class="subheading">DAILY FORTUNE JOURNAL</div>',
    }[kind]
    if title not in {'周易六爻', '观音灵签', '塔罗牌占卜', '奇门遁甲', '每日运势'}:
        display_title = e(title or '赛博占卜')
        page_top = re.sub(r'<h1>.*?</h1>', f'<h1>{display_title}</h1>', page_top, count=1)
    html_body = ('<div class="page ' + kind_css + (' gold-variant' if theme == 'gold' else '') + '"><div class="art-layer">' + arts[kind] + '</div>'
                 + '<div class="frame-outer"></div><div class="frame-inner"></div>'
                 + '<header>' + page_top + '</header><main>' + body + '</main>'
                 + '<footer>✦　' + e(footer or '仅供娱乐') + '　✦</footer></div>')
    return ("<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=900\">"
            "<style>" + css + "</style></head><body>" + html_body + "</body></html>")
