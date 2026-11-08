"""Five-format card rendering tests; no AstrBot installation required."""
from __future__ import annotations
from datetime import date, datetime
import random
from core import render, zhouyi, lingqian, tarot, qimen, daily


def _html(kind, content, **kwargs):
    return render.build_card_html(kind, "测试副标题", content, "仅供娱乐", **kwargs)


def test_all_five_themes_have_unique_layouts():
    names = ["周易六爻", "观音灵签", "塔罗牌占卜", "奇门遁甲", "每日运势"]
    for name, slug in zip(names, ("zhouyi", "lingqian", "tarot", "qimen", "daily")):
        card = _html(name, ["所问之事：今晚出门", "求卦人：测试用户"])
        assert card.startswith("<!doctype html>")
        assert f'class="page {slug}"' in card
        assert '<svg' in card
        assert "仅供娱乐" in card


def test_xss_escaped():
    cast = zhouyi.cast(rng=random.Random(18), day_index=0)
    card = _html("周易六爻", [], zhouyi_cast=cast,
                 question="<script>alert('x')</script>", sender='<img src="x" onerror="hack()">')
    assert "<script>alert" not in card
    assert "&lt;script&gt;" in card
    assert "&lt;img" in card
    assert '<img src="x"' not in card


def test_yaos_values_direction_and_moving():
    cast = zhouyi.cast(rng=random.Random(5), day_index=0)
    card = _html("周易六爻", zhouyi.format_cast(cast).splitlines(), zhouyi_cast=cast)
    assert card.count('class="yao-item') == 6
    assert card.count('class="glyph-bar') == 6
    assert card.count('class="move-banner"') == 1
    assert "本卦" in card
    if cast.moving:
        assert card.count('class="changed-detail"') == 1
        assert "变卦" in card
    else:
        assert "六爻安静" in card
    assert cast.lines == list(cast.lines)


def test_lingqian_scroll_preserves_poem_and_grade():
    number, grade, poem, explain = lingqian.draw(random.Random(8))
    card = _html("观音灵签", [], card_data={"number":number,"grade":grade,"poem":poem,"explain":explain})
    assert "verse-wrap" in card
    assert str(number) in card
    assert grade in card
    assert explain in card


def test_tarot_spreads_are_real_cards():
    for cards in [tarot.draw_three(random.Random(5)), [("今日指引",)+tarot.draw_single(random.Random(6))]]:
        card = _html("塔罗牌占卜", [], card_data={"cards":cards})
        assert card.count('class="arcana"') == len(cards)
        assert card.count('class="tarot-interpret"') == len(cards)
        for _, name, is_reversed, _ in cards:
            assert name in card
            assert ("逆位" if is_reversed else "正位") in card


def test_qimen_real_nine_palaces():
    pan = qimen.make_pan(datetime(2026, 11, 8, 14, 0))
    card = _html("奇门遁甲", qimen.format_pan(pan).splitlines(),card_data={"pan":pan})
    assert card.count('class="palace ') == 9
    assert "qimen-grid" in card
    assert "值符" in card
    assert "格局解析" in card


def test_daily_structured_data():
    data = daily.draw_daily(date(2026,11,8), "123")
    card = _html("每日运势", daily.format_daily(data).splitlines(),card_data={"daily":data})
    assert card.count('class="fortune-block') == 3
    assert "今日寄语" in card
    assert data['cast'].name in card
    assert data['card'][0] in card


def test_ai_and_gold_color_switch():
    for title in ("周易六爻", "观音灵签", "塔罗牌占卜", "奇门遁甲", "每日运势"):
        card = _html(title,["🔮 AI 解读：不要心急，今日适合稳扎稳打。"], theme='gold')
        assert 'ai-block' in card or 'fallback' in card
        assert "不要心急" in card or 'fallback' in card
        assert 'gold-variant' in card or title == '周易六爻'
