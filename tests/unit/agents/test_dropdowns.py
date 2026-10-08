"""Dropdown exploration stores behavior, not one screen per option."""

from core.agents.application_discovery.crawler import CrawlBudget
from core.agents.application_discovery.dropdowns import (
    behavior_groups,
    classify_dropdown,
    meaningful_change,
    option_signature,
    representative_option,
    reuse_dropdown,
    sample_options,
)


def test_classifies_native_listbox_and_custom_dropdowns():
    assert classify_dropdown({"role": "combobox", "name": "Country", "options": ["IN", "JP"]}) == "native"
    assert classify_dropdown({"role": "combobox", "name": "Search city"}) == "searchable"
    assert classify_dropdown({"role": "listbox", "name": "Status"}) == "listbox"
    assert classify_dropdown({"role": "button", "name": "Choose office"}) == "custom"
    assert classify_dropdown({"role": "combobox", "name": "CAPTCHA answer", "options": ["1"]}) is None
    assert classify_dropdown({"role": "button", "name": "Delete record"}) is None


def test_same_structure_is_not_a_new_screen():
    before = [{"role": "combobox", "name": "Country", "selected": True}]
    after = [{"role": "combobox", "name": "Country", "selected": False}]
    assert not meaningful_change(before, after, "https://app.test/form", "https://app.test/form")
    revealed = [*after, {"role": "textbox", "name": "State", "required": True}]
    assert meaningful_change(before, revealed, "https://app.test/form", "https://app.test/form")


def test_limits_and_reuse_skip_repeated_exploration():
    options = [{"label": str(index), "value": str(index), "enabled": True} for index in range(20)]
    assert len(sample_options(options, CrawlBudget().max_dropdown_options)) == 8
    signature = option_signature(options)
    previous = {"options": options, "option_signature": signature}
    assert reuse_dropdown(previous, options)
    changed = [*options, {"label": "new", "value": "new", "enabled": True}]
    assert not reuse_dropdown(previous, changed)


def test_distinct_behavior_is_not_one_case_per_option():
    dependencies = [
        {"option": "Retail", "reveals": ["textbox:store"], "navigates_to": None},
        {"option": "Shop", "reveals": ["textbox:store"], "navigates_to": None},
        {"option": "Wholesale", "reveals": ["textbox:account"], "navigates_to": "/wholesale"},
    ]
    groups = behavior_groups(dependencies)
    assert len(groups) == 2
    assert representative_option(
        [{"label": "Retail", "enabled": True}, {"label": "Wholesale", "enabled": True}],
        "Use the Wholesale account",
    ) == "Wholesale"
