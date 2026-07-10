"""Tests for the machine-enforced clinical safety gates."""

import json

import pytest

from nsclc_agent import Case, NSCLCAgent, load_config
from nsclc_agent.safety import (
    positive_drivers,
    post_inference_gates,
    pre_inference_gates,
)
from nsclc_agent.staging import stage_from_strings
from nsclc_agent.providers.base import GenerationParams, LLMProvider, LLMResponse


class TextProvider(LLMProvider):
    """A provider that returns a fixed text body (for post-gate e2e tests)."""
    kind = "text"

    def __init__(self, body: str):
        super().__init__("text", "text-1", GenerationParams())
        self._body = body

    def complete(self, messages, *, params=None):
        return LLMResponse(self._body, "text", "text-1", finish_reason="stop")


def _driver_case(**drivers):
    return Case(t="T3", n="N1", m="M0",
                fields={"driver_mutations": drivers})


# --- driver extraction -----------------------------------------------------

def test_positive_drivers_named_variant():
    c = _driver_case(egfr="L858R", alk="negative")
    assert positive_drivers(c) == ["EGFR"]


def test_positive_drivers_negative_and_pending():
    c = _driver_case(egfr="negative", alk="pending", ros1="not tested")
    assert positive_drivers(c) == []


# --- pre-inference gates ---------------------------------------------------

def test_pathology_unconfirmed_blocks():
    case = Case(t="T2a", n="N0", m="M0",
                fields={"diagnosis_confirmed": False})
    sr = stage_from_strings("T2a", "N0", "M0")
    gates = pre_inference_gates(case, sr)
    block = [g for g in gates if g.severity == "block"]
    assert block and block[0].code == "PATHOLOGY_UNCONFIRMED"


def test_pathology_unconfirmed_blocks_end_to_end():
    agent = NSCLCAgent(load_config())
    case = Case(t="T2a", n="N0", m="M0",
                fields={"diagnosis_confirmed": False})
    result = agent.run(case)
    assert result.error is not None
    assert any("PATHOLOGY_UNCONFIRMED" in f for f in result.flags)
    assert result.response is None  # model never called


def test_n2_imaging_only_warns():
    case = Case(t="T2a", n="N2a", m="M0",
                fields={"nodal_confirmation": "imaging-only"})
    sr = stage_from_strings("T2a", "N2a", "M0")
    gates = pre_inference_gates(case, sr)
    assert any(g.code == "N2_UNCONFIRMED" for g in gates)


# --- post-inference gates --------------------------------------------------

def test_driver_io_conflict_detected():
    case = _driver_case(egfr="exon 19 del")
    sr = stage_from_strings("T3", "N1", "M0")
    out = json.dumps({"recommendation": "adjuvant pembrolizumab per KEYNOTE-091"})
    gates = post_inference_gates(case, sr, out)
    assert any(g.code == "DRIVER_IO_CONFLICT" and g.severity == "hard"
               for g in gates)


def test_no_driver_io_conflict_when_driver_negative():
    case = _driver_case(egfr="negative", alk="negative")
    sr = stage_from_strings("T3", "N1", "M0")
    out = json.dumps({"recommendation": "adjuvant pembrolizumab"})
    gates = post_inference_gates(case, sr, out)
    assert not any(g.code == "DRIVER_IO_CONFLICT" for g in gates)


def test_n3_surgery_flagged():
    case = Case(t="T4", n="N3", m="M0")
    sr = stage_from_strings("T4", "N3", "M0")  # IIIC
    out = json.dumps({"plan": "proceed to lobectomy after induction"})
    gates = post_inference_gates(case, sr, out)
    assert any(g.code == "N3_SURGERY" for g in gates)


def test_driver_io_conflict_end_to_end():
    agent = NSCLCAgent(load_config())
    agent._provider_cache["text"] = TextProvider(
        json.dumps({"plan": "neoadjuvant nivolumab + chemo"}))
    case = Case(t="T3", n="N1", m="M0",
                fields={"driver_mutations": {"alk": "EML4-ALK"}})
    result = agent.run(case, provider="text")
    assert any("DRIVER_IO_CONFLICT" in f for f in result.flags)
