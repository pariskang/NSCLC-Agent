"""Tests for stage → module routing and prompt loading."""

import pytest

from nsclc_agent.staging import route, available_modules
from nsclc_agent.prompts import load_module, list_modules, PromptNotFound


@pytest.mark.parametrize("stage_group,module", [
    ("0", "stage1"),
    ("IA1", "stage1"),
    ("IA3", "stage1"),
    ("IB", "stage1"),
    ("IIA", "stage2"),
    ("IIB", "stage2"),
    ("IIIA", "stage3a"),
    ("IIIB", "stage3b"),
    ("IIIC", "stage3c"),
    ("IVA", "stage4a"),
    ("IVB", "stage4b"),
])
def test_routing_available(stage_group, module):
    r = route(stage_group)
    assert r.available
    assert r.module_key == module


@pytest.mark.parametrize("stage_group", ["Occult"])
def test_routing_unavailable(stage_group):
    r = route(stage_group)
    assert not r.available
    assert r.module_key is None
    assert r.note


def test_all_modules_loadable():
    for m in list_modules():
        assert m.system_prompt
        assert m.system_prompt.startswith("=")  # banner


def test_module_content_matches_stage():
    assert "STAGE I" in load_module("stage1").system_prompt
    assert "Perioperative" in load_module("stage3a").system_prompt
    assert "STAGE IIIB" in load_module("stage3b").system_prompt
    assert "M1c1" in load_module("stage4b").system_prompt


def test_available_modules_list():
    assert set(available_modules()) == {
        "stage1", "stage2", "stage3a", "stage3b", "stage3c", "stage4a",
        "stage4b"
    }


def test_unknown_module_raises():
    with pytest.raises(PromptNotFound):
        load_module("stage99")
