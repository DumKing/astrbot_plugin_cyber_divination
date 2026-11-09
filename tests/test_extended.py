from datetime import datetime
from html import escape
import json
from random import Random

from core.extended import BUILDERS, METHODS
from core.image_api import build_prompt
from core.image_styles import resolve_style
from core.keywords import match_keyword
from core.render import build_card_html

NOW = datetime(2026, 11, 9, 12)
CASES = {
    "xiaoliuren": ("今天适合出门吗", "时宫"),
    "meihua": ("1 1 工作", "互卦"),
    "lenormand": ("三张 工作", "第 1 张"),
    "daliuren": ("2026-11-09 12:00 出行", "初傳"),
    "taiyi": ("日计 2026-11-09 12:00", "局式"),
    "bazi": ("2000-08-16 12:00", "时柱"),
    "ziwei": ("2000-08-16 12:00 女", "命宫"),
}


def test_seven_methods_generate_results_and_card_data():
    for kind, (query, field) in CASES.items():
        title, aliases, _, _ = METHODS[kind]
        reading = BUILDERS[kind](query, NOW)
        assert reading.summary and any(field in label for label, _ in reading.rows), kind
        assert match_keyword(f"{aliases[0]} {query}", "starts_with", {}) == (kind, query)
        data = {"extended_rows": reading.rows}
        html = build_card_html(title, reading.summary, reading.text(title).splitlines(),
                               "", question=reading.question, card_data=data)
        assert f"class=\"page extended {kind}\"" in html
        assert escape(reading.rows[0][1]) in html
        prompt = build_prompt(resolve_style(kind, ""), kind, title, data)
        assert '"results"' in prompt and field in prompt
        if kind in {"bazi", "ziwei"}:
            assert "2000-08-16" not in prompt


def test_meihua_numbers_and_lenormand_card_count():
    reading = BUILDERS["meihua"]("1 1 工作", NOW)
    assert reading.summary == "乾"
    assert dict(reading.rows)["动爻"] == "第 2 爻"
    assert reading.question == "工作"
    lenormand = BUILDERS["lenormand"]
    assert len(lenormand("单张 工作", NOW, Random(5)).rows) == 1
    three = lenormand("三张 工作", NOW, Random(5))
    assert len(three.rows) == 5
    assert len(set(three.summary.split(" · "))) == 3
    assert three.question == "工作"


def test_daily_long_card_contains_all_sections_and_optional_birth_charts():
    from core.daily import draw_daily, format_daily
    from core.extended import daily_panels

    panels = daily_panels(NOW, "alice")
    assert [panel["title"] for panel in panels] == ["奇门遁甲"] + [
        method[0] for method in METHODS.values()
    ]
    assert all(panel["rows"] for panel in panels)
    assert len(panels[-1]["rows"]) == 14
    assert panels[-2:] == daily_panels(NOW, "alice", "2026-11-09 12:00 男")[-2:]
    assert daily_panels(NOW, "alice")[3] == panels[3]

    panels = daily_panels(NOW, "alice", "2000-08-16 12:00 女")
    assert any(label == "时柱" for label, _ in panels[-2]["rows"])
    assert len(panels[-1]["rows"]) == 14
    draw = draw_daily(NOW.date(), "alice")
    panels[0]["ai_note"] = "先核实细节。<示例>"
    data = {"daily": draw, "daily_panels": panels}
    html = build_card_html("每日运势", "", format_daily(draw).splitlines(), "", card_data=data)
    prompt = build_prompt(resolve_style("daily", ""), "daily", "每日运势", data)
    assert "daily-panels" in html and "竖向超长画布" in prompt
    assert "AI 简解" in html and "先核实细节。&lt;示例&gt;" in html
    assert "先核实细节。<示例>" in prompt and "3840 × 21333" in prompt
    fields = json.loads(prompt.split("构图参考数据：", 1)[1].splitlines()[0])
    cast = draw["cast"]
    assert fields["hexagram_text"] == cast.guaci
    assert fields["upper_trigram"] == cast.upper and fields["lower_trigram"] == cast.lower
    assert fields["six_gods_bottom_to_top"] == cast.gods
    assert fields["moving_lines"] == cast.moving
    assert fields["changed_hexagram"] == cast.changed_name
    assert fields["changed_hexagram_text"] == cast.changed_guaci
    assert fields["changed_hexagram_interpretation"] == cast.changed_jie
    assert "左栏第 1–8 条，右栏第 9–15 条" in prompt
    assert all(f"{index}. /占卜 " in prompt for index in range(1, 16))
    assert "2000-08-16" not in prompt
    for panel in panels:
        assert panel["title"] in html and panel["title"] in prompt


def test_qq_birthday_defaults_to_noon_and_keeps_explicit_birth_time():
    from datetime import date
    from core.extended import daily_panels

    birthday = date(2000, 8, 16)
    default = daily_panels(NOW, "alice", birthday=birthday, profile_gender="女")
    noon = daily_panels(NOW, "alice", "2000-08-16 12:00 女")
    explicit = daily_panels(NOW, "alice", "2000-08-16 08:30 女")
    assert default[-2:] == noon[-2:]
    assert dict(default[-2]["rows"])["时柱"] != dict(explicit[-2]["rows"])["时柱"]
    assert daily_panels(NOW, "alice", "08:30", birthday=birthday, profile_gender="女")[-2:] == explicit[-2:]
    assert daily_panels(NOW, "alice", "08:30 女")[-2:] == daily_panels(NOW, "alice", "2026-11-09 08:30 女")[-2:]
    unknown_gender = daily_panels(NOW, "alice", birthday=birthday)
    assert "时柱" in dict(unknown_gender[-2]["rows"])
    male = daily_panels(NOW, "alice", "2000-08-16 12:00 男")
    assert unknown_gender[-2:] == male[-2:]
    explicit_female = daily_panels(NOW, "alice", "2000-08-16 12:00 女", profile_gender="男")
    assert explicit_female[-1]["rows"] == noon[-1]["rows"]
