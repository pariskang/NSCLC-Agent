"""Tests for the perception (film-reading) layer — fully offline.

A tiny fake vision provider stands in for a real backend (Gemini via Poe) so
the propose→verify contract can be exercised without a network or key: the
vision model PROPOSES descriptors, the deterministic engine STAGES, and
discordance with human/pathologic TNM is flagged rather than silently applied.
"""

import base64
import json
from pathlib import Path

import pytest

from nsclc_agent import Case, NSCLCAgent, load_config
from nsclc_agent.config import Config
from nsclc_agent.imaging import (
    ImagingError,
    ImagingReader,
    _extract_json,
    load_image_ref,
)
from nsclc_agent.providers.base import (
    GenerationParams,
    LLMProvider,
    LLMResponse,
    Message,
)

# 1x1 PNG
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQDwAE"
    "hQGAhKmMIQAAAABJRU5ErkJggg=="
)


class FakeVisionProvider(LLMProvider):
    """Returns a canned findings JSON, echoing how many images it received."""

    kind = "fake-vision"

    def __init__(self, findings: dict, name: str = "fake"):
        super().__init__(name, "fake-vision-1", GenerationParams())
        self.supports_vision = True
        self._findings = findings
        self.last_messages = None

    def complete(self, messages, *, params=None):
        self.last_messages = messages
        return LLMResponse(
            content=json.dumps(self._findings),
            provider=self.name, model=self.model, finish_reason="stop",
        )


def _agent_with_vision(findings: dict) -> tuple[NSCLCAgent, FakeVisionProvider]:
    agent = NSCLCAgent(load_config())
    prov = FakeVisionProvider(findings)
    agent._provider_cache["fakevision"] = prov  # inject
    agent.vision_provider = "fakevision"
    return agent, prov


# --- message / image plumbing ---------------------------------------------

def test_message_multimodal_shape():
    m = Message("user", "look", images=["data:image/png;base64,AAAA"])
    payload = m.to_openai()
    assert isinstance(payload["content"], list)
    assert payload["content"][0] == {"type": "text", "text": "look"}
    assert payload["content"][1]["type"] == "image_url"


def test_message_textonly_shape_unchanged():
    m = Message("user", "hi")
    assert m.to_openai() == {"role": "user", "content": "hi"}


def test_load_image_ref_file_to_data_url(tmp_path):
    p = tmp_path / "scan.png"
    p.write_bytes(_PNG)
    url = load_image_ref(str(p))
    assert url.startswith("data:image/png;base64,")


def test_load_image_ref_passthrough():
    assert load_image_ref("https://x/y.png") == "https://x/y.png"
    assert load_image_ref("data:image/png;base64,Zm9v").startswith("data:")


def test_load_image_ref_missing():
    with pytest.raises(ImagingError):
        load_image_ref("/no/such/file.png")


# --- JSON extraction -------------------------------------------------------

def test_extract_json_plain():
    assert _extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced():
    txt = 'Here you go:\n```json\n{"candidate_t": "T2a"}\n```\nthanks'
    assert _extract_json(txt)["candidate_t"] == "T2a"


def test_extract_json_embedded():
    assert _extract_json('noise {"x": 2} tail')["x"] == 2


def test_extract_json_failure():
    with pytest.raises(ImagingError):
        _extract_json("no json here")


# --- reader ---------------------------------------------------------------

def test_reader_parses_findings(tmp_path):
    p = tmp_path / "s.png"
    p.write_bytes(_PNG)
    prov = FakeVisionProvider({
        "modality": "PET-CT", "candidate_t": "T2b", "candidate_n": "N2b",
        "candidate_m": "M0", "nodal_stations": ["4R", "7"],
        "malignant_effusion": False, "confidence": "moderate",
        "uncertainties": [],
    })
    findings = ImagingReader(prov).read([str(p)], context="68F LUL mass")
    assert findings.candidate_t == "T2b"
    assert findings.candidate_n == "N2b"
    assert findings.nodal_stations == ["4R", "7"]
    assert findings.is_complete()
    # image actually attached to the user turn
    user = [m for m in prov.last_messages if m.role == "user"][0]
    assert len(user.images) == 1


def test_reader_normalises_nullish():
    prov = FakeVisionProvider({
        "candidate_t": "T1a", "candidate_n": "null", "candidate_m": "unknown",
    })
    findings = ImagingReader(prov).read(["data:image/png;base64,Zm9v"])
    assert findings.candidate_t == "T1a"
    assert findings.candidate_n is None
    assert findings.candidate_m is None
    assert set(findings.unresolved_descriptors()) == {"N", "M"}


# --- agent integration: propose → verify ----------------------------------

def test_imaging_seeds_missing_tnm_then_engine_stages(tmp_path):
    p = tmp_path / "s.png"; p.write_bytes(_PNG)
    agent, _ = _agent_with_vision({
        "candidate_t": "T2b", "candidate_n": "N2b", "candidate_m": "M0",
    })
    case = Case(images=[str(p)], question="plan?")
    result = agent.run(case)
    # deterministic engine still computes the stage from the seeded descriptors
    assert result.staging["stage_group"] == "IIIB"
    assert result.module_key == "stage3b"
    assert any("RADIOGRAPHIC_TNM_PROPOSED" in f for f in result.flags)
    assert result.imaging["candidate_n"] == "N2b"
    assert result.error is None


def test_imaging_discordance_flagged_case_value_wins(tmp_path):
    p = tmp_path / "s.png"; p.write_bytes(_PNG)
    agent, _ = _agent_with_vision({
        "candidate_t": "T2b", "candidate_n": "N3", "candidate_m": "M0",
    })
    # human/path says N2b; reader proposes N3 → discordance, human wins
    case = Case(t="T2b", n="N2b", m="M0", images=[str(p)], question="plan?")
    result = agent.run(case)
    assert result.staging["stage_group"] == "IIIB"  # from N2b, not N3
    assert any("IMAGING_DISCORDANCE[N]" in f for f in result.flags)


def test_imaging_concordant_no_discordance(tmp_path):
    p = tmp_path / "s.png"; p.write_bytes(_PNG)
    agent, _ = _agent_with_vision({
        "candidate_t": "T2b", "candidate_n": "N2b", "candidate_m": "M0",
    })
    case = Case(t="T2b", n="N2b", m="M0", images=[str(p)])
    result = agent.run(case)
    assert not any("DISCORDANCE" in f for f in result.flags)


def test_imaging_incomplete_emits_next_step(tmp_path):
    p = tmp_path / "s.png"; p.write_bytes(_PNG)
    agent, _ = _agent_with_vision({
        "candidate_t": "T2b", "candidate_n": None, "candidate_m": "M0",
    })
    case = Case(images=[str(p)])
    result = agent.run(case)
    # N unresolved → cannot stage, but a value-of-information hint is emitted
    assert any("NEXT_STEP_SUGGESTED" in f and "EBUS" in f for f in result.flags)


def test_imaging_findings_injected_into_reasoning_prompt(tmp_path):
    p = tmp_path / "s.png"; p.write_bytes(_PNG)
    agent, _ = _agent_with_vision({
        "candidate_t": "T2b", "candidate_n": "N2b", "candidate_m": "M0",
    })
    case = Case(images=[str(p)])
    findings = agent.read_imaging(case)
    case2, _ = agent._ingest_imaging(case, findings)
    stage_result, _ = agent.resolve_stage(case2)
    from nsclc_agent.prompts import load_module
    msgs = agent.build_messages(case2, load_module("stage3b"), stage_result,
                                imaging_findings=findings)
    assert "RADIOGRAPHIC FINDINGS" in msgs[1].content
    assert "UNVERIFIED" in msgs[1].content


def test_read_failure_is_graceful(tmp_path):
    p = tmp_path / "s.png"; p.write_bytes(_PNG)

    class Boom(FakeVisionProvider):
        def complete(self, messages, *, params=None):
            raise RuntimeError("vision backend down")

    agent = NSCLCAgent(load_config())
    agent._provider_cache["fakevision"] = Boom({})
    agent.vision_provider = "fakevision"
    # with human TNM present, a failed read must not abort the run
    case = Case(t="T2b", n="N2b", m="M0", images=[str(p)])
    result = agent.run(case)
    assert result.staging["stage_group"] == "IIIB"
    assert any("IMAGING_READ_FAILED" in f for f in result.flags)


# --- config / provider vision flag ----------------------------------------

def test_vision_provider_autopicked_from_flag():
    cfg = Config(
        default_provider="mock",
        providers={
            "mock": {"type": "mock"},
            "eye": {"type": "poe", "model": "Gemini-3.1-Pro", "vision": True,
                    "api_key_env": "POE_API_KEY"},
        },
        generation=GenerationParams(),
    )
    # load_config path does the autopick; here assert build wiring via agent
    agent = NSCLCAgent(cfg)
    # no explicit vision_provider on cfg → resolver scans for vision flag
    assert agent.resolve_vision_provider_name() == "eye"


def test_mock_reports_vision_capability():
    from nsclc_agent.providers import build_provider
    prov = build_provider("v", {"type": "poe", "model": "Gemini-3.1-Pro",
                                 "vision": True, "api_key": "x"})
    assert prov.supports_vision is True
    assert prov.describe()["supports_vision"] is True
