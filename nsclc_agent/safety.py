"""Machine-enforced clinical safety gates.

The protocol modules state many safety rules in prose, but a prose instruction
inside a system prompt is not a control — a model can ignore it. This module
turns the highest-consequence rules into *code-level gates* that run outside the
model:

  * **pre-inference gates** can BLOCK the run before any model is called
    (e.g. a case that is not a confirmed NSCLC has no business entering an NSCLC
    treatment module);
  * **post-inference gates** inspect the model's output and raise HARD flags
    when it appears to violate a hard rule (driver-positive + immunotherapy in a
    curative setting, upfront surgery for N3, …).

These are deliberately conservative *detectors*, not a substitute for the MDT.
They surface as flags (`GATE_BLOCK:` / `GATE_HARD:` / `GATE_WARN:`) so a reviewer
sees them immediately; nothing here silently rewrites a recommendation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

from .case import Case
from .staging import StageResult

# Immune-checkpoint agents / phrases that signal an immunotherapy recommendation.
_IO_TERMS = (
    "pembrolizumab", "nivolumab", "durvalumab", "atezolizumab", "cemiplimab",
    "ipilimumab", "tremelimumab", "toripalimab", "tislelizumab", "sintilimab",
    "camrelizumab", "immunotherapy", "checkpoint inhibitor", "anti-pd-1",
    "anti-pd-l1", "pd-1 inhibitor", "pd-l1 inhibitor", "io consolidation",
)
_SURGERY_TERMS = (
    "lobectomy", "pneumonectomy", "segmentectomy", "wedge resection",
    "sleeve resection", "surgical resection", "upfront surgery", "resection",
    "bilobectomy",
)
_ACTIONABLE_DRIVERS = ("egfr", "alk", "ros1", "ret", "met", "braf", "ntrk",
                       "kras g12c", "her2", "erbb2")


@dataclass
class Gate:
    code: str
    severity: str  # "block" | "hard" | "warn"
    message: str

    def as_flag(self) -> str:
        prefix = {"block": "GATE_BLOCK", "hard": "GATE_HARD",
                  "warn": "GATE_WARN"}.get(self.severity, "GATE")
        return f"{prefix}[{self.code}]: {self.message}"


# --- case fact extraction ---------------------------------------------------

def _truthy_positive(value: Any) -> bool:
    if value is None:
        return False
    s = str(value).strip().lower()
    if s in ("positive", "detected", "mutant", "mutated", "present", "yes",
             "true", "rearranged", "fusion", "amplified"):
        return True
    # e.g. "L858R", "exon 19 del", "EML4-ALK" → a named alteration is positive
    if s in ("negative", "wild-type", "wildtype", "wt", "none", "not detected",
             "absent", "no", "false", "unknown", "pending", "not tested", ""):
        return False
    return True  # a specific named variant


def positive_drivers(case: Case) -> list[str]:
    """Actionable driver alterations recorded as positive on the case."""
    found: list[str] = []
    dm = case.fields.get("driver_mutations") or case.fields.get("drivers") or {}
    if isinstance(dm, dict):
        for k, v in dm.items():
            if k.lower() in _ACTIONABLE_DRIVERS and _truthy_positive(v):
                found.append(k.upper())
    # also accept a flat list of positive drivers
    for key in ("positive_drivers", "actionable_drivers"):
        seq = case.fields.get(key)
        if isinstance(seq, (list, tuple)):
            found.extend(str(x).upper() for x in seq)
    return sorted(set(found))


def _diagnosis_state(case: Case) -> Optional[bool]:
    """True/False if the case explicitly states NSCLC diagnosis (un)confirmed."""
    for key in ("diagnosis_confirmed", "histology_confirmed",
                "pathology_confirmed"):
        if key in case.fields:
            return bool(case.fields[key])
    return None  # unknown


def _nodal_confirmed(case: Case) -> Optional[bool]:
    for key in ("nodal_confirmation", "n_confirmed", "mediastinal_staging"):
        if key in case.fields:
            v = case.fields[key]
            if isinstance(v, bool):
                return v
            s = str(v).strip().lower()
            if s in ("ebus", "ebus-tbna", "mediastinoscopy", "confirmed",
                     "pathologic", "yes", "true"):
                return True
            if s in ("none", "no", "imaging-only", "unconfirmed", "false"):
                return False
    return None


# --- gates ------------------------------------------------------------------

def pre_inference_gates(
    case: Case, stage_result: Optional[StageResult]
) -> list[Gate]:
    gates: list[Gate] = []

    diag = _diagnosis_state(case)
    if diag is False:
        gates.append(Gate(
            "PATHOLOGY_UNCONFIRMED", "block",
            "NSCLC diagnosis is explicitly not confirmed — obtain a tissue "
            "diagnosis before entering a treatment module."))

    if stage_result is not None and stage_result.tnm.n in ("N2a", "N2b"):
        if _nodal_confirmed(case) is False:
            gates.append(Gate(
                "N2_UNCONFIRMED", "warn",
                "N2 disease is imaging-only (not pathologically confirmed): a "
                "resectability/curative conclusion needs invasive nodal staging "
                "(EBUS-TBNA / mediastinoscopy)."))

    if stage_result is not None and stage_result.tnm.basis == "mixed":
        gates.append(Gate(
            "MIXED_STAGING_BASIS", "warn",
            "Descriptors mix clinical and pathologic basis; do not combine them "
            "into a single stage without reconciling the source."))

    return gates


def post_inference_gates(
    case: Case, stage_result: Optional[StageResult], output_text: str
) -> list[Gate]:
    gates: list[Gate] = []
    text = (output_text or "").lower()

    drivers = positive_drivers(case)
    io_hits = [t for t in _IO_TERMS if t in text]
    if drivers and io_hits:
        gates.append(Gate(
            "DRIVER_IO_CONFLICT", "hard",
            f"Actionable driver(s) {', '.join(drivers)} are positive but the "
            f"output mentions immunotherapy ({', '.join(sorted(set(io_hits)))}). "
            f"Verify this is not a curative-intent perioperative/adjuvant or "
            f"first-line palliative IO recommendation, where driver-positive "
            f"disease is generally treated with targeted therapy first."))

    if stage_result is not None:
        n = stage_result.tnm.n
        grp = stage_result.stage_group
        if (n == "N3" or grp == "IIIC"):
            surg_hits = [t for t in _SURGERY_TERMS if t in text]
            if surg_hits:
                gates.append(Gate(
                    "N3_SURGERY", "hard",
                    f"Stage {grp} / {n} with a surgical term in the output "
                    f"({', '.join(sorted(set(surg_hits)))}): N3 disease is "
                    f"generally not resected upfront — confirm this is not an "
                    f"upfront-surgery recommendation."))

    return gates


def evaluate_gates(
    case: Case,
    stage_result: Optional[StageResult],
    *,
    output_text: str = "",
    phase: str = "pre",
) -> list[Gate]:
    if phase == "post":
        return post_inference_gates(case, stage_result, output_text)
    return pre_inference_gates(case, stage_result)
