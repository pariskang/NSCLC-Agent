"""Tests for structured-output validation and fabricated-evidence detection."""

import json

from nsclc_agent.validation import validate_output


def test_valid_json_passes():
    r = validate_output(json.dumps({"stage": "IIIB", "plan": "cCRT"}))
    assert r.is_json is True
    assert r.parsed["stage"] == "IIIB"
    assert not any("OUTPUT_NOT_JSON" in f for f in r.flags)


def test_non_json_flagged():
    r = validate_output("Here is my clinical advice in prose, no JSON at all.")
    assert r.is_json is False
    assert any("OUTPUT_NOT_JSON" in f for f in r.flags)


def test_fenced_json_recovered():
    r = validate_output("```json\n{\"a\": 1}\n```")
    assert r.is_json is True
    assert r.parsed["a"] == 1


def test_fabricated_tool_call_flagged():
    body = json.dumps({
        "tool_call": {"name": "pubmed_search", "query": "NSCLC IIIB"},
        "sources": [{"title": "A trial", "pmid": "12345678"}],
        "plan": "cCRT + durvalumab",
    })
    r = validate_output(body)
    assert any("EVIDENCE_UNVERIFIED" in f for f in r.flags)


def test_inline_citation_flagged_even_without_keys():
    r = validate_output(json.dumps({"note": "per PMID 31756231 this is standard"}))
    assert any("EVIDENCE_UNVERIFIED" in f for f in r.flags)


def test_clean_output_no_evidence_flag():
    r = validate_output(json.dumps({"stage": "IB", "plan": "lobectomy"}))
    assert not any("EVIDENCE_UNVERIFIED" in f for f in r.flags)


def test_empty_sources_not_flagged():
    # an empty sources array is not a fabricated citation
    r = validate_output(json.dumps({"plan": "SBRT", "sources": []}))
    assert not any("EVIDENCE_UNVERIFIED" in f for f in r.flags)
