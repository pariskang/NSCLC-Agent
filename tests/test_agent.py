"""End-to-end tests for the agent orchestrator (offline, mock provider)."""

import json
from pathlib import Path

import pytest

from nsclc_agent import NSCLCAgent, Case, load_config

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "cases"


@pytest.fixture
def agent():
    return NSCLCAgent(load_config())  # built-in mock config


def test_run_stage3b_case(agent):
    case = Case(case_id="t", t="T2b", n="N2b", m="M0",
                presentation="test", question="plan?")
    result = agent.run(case)
    assert result.error is None
    assert result.staging["stage_group"] == "IIIB"
    assert result.module_key == "stage3b"
    assert result.provider == "mock"
    assert result.response is not None
    data = json.loads(result.response.content)
    assert data["_mock"] is True


def test_dry_run_assembles_prompt_without_model(agent):
    case = Case(t="T4", n="N3", m="M0")
    result = agent.run(case, dry_run=True)
    assert "DRY_RUN" in result.flags
    assert result.module_key == "stage3c"
    assert result.provider is None


def test_stage_mismatch_flagged(agent):
    case = Case(t="T2b", n="N2b", m="M0", stage_group="IIIA")
    result = agent.run(case, dry_run=True)
    assert any("STAGE_MISMATCH" in f for f in result.flags)
    # computed value wins
    assert result.staging["stage_group"] == "IIIB"


def test_iiia_routes_to_stage3a(agent):
    case = Case(t="T3", n="N2a", m="M0")  # stage IIIA
    result = agent.run(case, dry_run=True)
    assert result.staging["stage_group"] == "IIIA"
    assert result.module_key == "stage3a"
    assert not any("FALLBACK" in f for f in result.flags)


def test_stage1_routes(agent):
    case = Case(t="T1a", n="N0", m="M0")  # stage IA1
    result = agent.run(case, dry_run=True)
    assert result.staging["stage_group"] == "IA1"
    assert result.module_key == "stage1"


def test_occult_has_no_module(agent):
    case = Case(t="TX", n="N0", m="M0")  # occult carcinoma, no module
    result = agent.run(case, dry_run=True)
    assert result.staging["stage_group"] == "Occult"
    assert result.module_key is None
    assert result.error is not None


def test_unresolved_stage_errors(agent):
    case = Case(presentation="no staging info")
    result = agent.run(case)
    assert result.error is not None
    assert any("STAGE_UNRESOLVED" in f for f in result.flags)


def test_bad_tnm_flagged(agent):
    case = Case(t="T2", n="N2", m="M0")  # ambiguous
    result = agent.run(case)
    assert result.error is not None
    assert any("STAGING_ERROR" in f for f in result.flags)


def test_staging_preamble_injected(agent):
    case = Case(t="T2b", n="N2b", m="M0")
    stage_result, _ = agent.resolve_stage(case)
    from nsclc_agent.prompts import load_module
    module = load_module("stage3b")
    messages = agent.build_messages(case, module, stage_result)
    system = messages[0].content
    assert "DETERMINISTIC STAGING" in system
    assert "Stage group: IIIB" in system
    assert messages[1].role == "user"


@pytest.mark.parametrize("case_file", sorted(EXAMPLES.glob("*.json")))
def test_example_cases_route_and_run(agent, case_file):
    case = Case.from_dict(json.loads(case_file.read_text()))
    result = agent.run(case)
    assert result.error is None, f"{case_file.name}: {result.error}"
    assert result.module_key is not None
    assert result.response is not None
