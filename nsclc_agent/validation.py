"""Structured-output validation for model responses.

The protocol modules ask the model for a large JSON object, but a prompt asking
for JSON does not guarantee JSON. This layer parses the reply and reports what
is wrong instead of passing raw text through as if it were a validated result:

  * it flags output that is not valid JSON (`OUTPUT_NOT_JSON`);
  * it flags **fabricated evidence** — `tool_call` / `sources` / PubMed / DOI
    style fields the model wrote to *look* retrieved, when this toolkit performs
    no real literature or label retrieval (`EVIDENCE_UNVERIFIED`). In a clinical
    context an unverifiable citation is more dangerous than none, because it
    manufactures the appearance of having checked the source.

It intentionally does NOT invent a schema the uploaded modules never agreed to;
it checks structural validity and the specific high-risk patterns above.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)

# Keys whose presence implies a (non-existent) retrieval step happened.
_EVIDENCE_KEYS = {
    "tool_call", "tool_calls", "tool_result_summary", "tool_results",
    "sources", "citations", "references", "pubmed_search", "web_search",
    "guideline_search", "regulatory_label_search", "retrieved_evidence",
}
_CITATION_RE = re.compile(r"\b(pmid|doi|pubmed|nct\d{6,})\b", re.IGNORECASE)


@dataclass
class ValidationResult:
    parsed: Optional[Any] = None
    is_json: bool = False
    flags: list[str] = field(default_factory=list)


def _try_parse(text: str) -> Optional[Any]:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = _FENCE_RE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    return None


def _find_evidence_keys(obj: Any, hits: set[str]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() in _EVIDENCE_KEYS and v not in (None, "", [], {}):
                hits.add(k.lower())
            _find_evidence_keys(v, hits)
    elif isinstance(obj, list):
        for item in obj:
            _find_evidence_keys(item, hits)


def validate_output(content: str) -> ValidationResult:
    """Parse and check a model reply; never raises."""
    result = ValidationResult()
    parsed = _try_parse(content)
    if parsed is None:
        result.flags.append(
            "OUTPUT_NOT_JSON: model reply is not valid JSON — downstream "
            "consumers must not assume a structured result.")
        # still scan the raw text for fabricated citations
        if _CITATION_RE.search(content or ""):
            result.flags.append(
                "EVIDENCE_UNVERIFIED: reply cites literature (PMID/DOI/NCT) but "
                "this toolkit performs no real retrieval — treat every citation "
                "as model-generated and unverified.")
        return result

    result.parsed = parsed
    result.is_json = True

    hits: set[str] = set()
    _find_evidence_keys(parsed, hits)
    if hits or _CITATION_RE.search(content or ""):
        detail = ", ".join(sorted(hits)) if hits else "inline citations"
        result.flags.append(
            f"EVIDENCE_UNVERIFIED: output presents retrieved-looking evidence "
            f"({detail}) but no literature/label retrieval was performed — the "
            f"'tool calls', 'sources' and citations are model-generated and "
            f"must be independently verified before any clinical use.")
    return result
