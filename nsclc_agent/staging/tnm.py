"""Deterministic AJCC/UICC 9th-edition NSCLC staging engine.

This module is intentionally a *pure symbolic* component: given the TNM
descriptors it computes the stage group by table lookup. The language model
never invents a final stage — this engine does — which removes hallucination
risk from the highest-stakes step and makes staging fully auditable and
unit-testable.

Reference: AJCC/UICC 9th edition (effective 1 January 2025). The 9th edition
retains the 8th-edition T and M1a/M1b categories but:
  * splits N2 into N2a (single mediastinal station) and N2b (multi-station);
  * splits M1c into M1c1 (multiple extrathoracic metastases, single organ
    system) and M1c2 (multiple extrathoracic metastases, multiple organ
    systems);
which drives real stage-group migration for several T/N combinations.

Scope and safety notes (deliberate design choices):
  * Only the 9th edition is implemented. An input that declares a different
    edition is *refused*, not silently staged as 9th (version-pollution guard).
  * Unknown descriptors are never defaulted. In particular an unknown/empty M
    normalizes to ``MX`` and MX does NOT produce a curative stage group —
    "M0" is a *conclusion* reached after metastatic workup, not a default.
  * Original descriptors are preserved for audit even when a staging-equivalent
    substitution is applied (e.g. T1mi is staged in the T1a family but the
    ``T1mi`` input is retained).
  * A clinical/pathologic/post-therapy/recurrence basis prefix (c/p/yc/yp/r)
    may be attached to descriptors; it is recorded, never discarded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# --- Canonical descriptor vocabularies -------------------------------------

T_CATEGORIES = ("T0", "Tis", "T1a", "T1b", "T1c", "T2a", "T2b", "T3", "T4", "TX")
N_CATEGORIES = ("N0", "N1", "N2a", "N2b", "N3", "NX")
M_CATEGORIES = ("M0", "M1a", "M1b", "M1c1", "M1c2", "MX")

# Coarse T families used by the stage table.
_T_FAMILY = {
    "Tis": "Tis",
    "T1a": "T1", "T1b": "T1", "T1c": "T1",
    "T2a": "T2a", "T2b": "T2b",
    "T3": "T3", "T4": "T4",
    "TX": "TX",
    "T0": "T0",
}

# Recognised staging-basis prefixes (longest first for greedy matching).
#   c = clinical, p = pathologic, yc/yp = post-neoadjuvant, r/rp = recurrence.
_BASIS_PREFIXES = ("yp", "yc", "rp", "c", "p", "r")
_BASIS_LABEL = {
    "c": "clinical (cTNM)",
    "p": "pathologic (pTNM)",
    "yc": "post-neoadjuvant clinical (ycTNM)",
    "yp": "post-neoadjuvant pathologic (ypTNM)",
    "r": "recurrence (rTNM)",
    "rp": "recurrence pathologic (rpTNM)",
}

# Accepted edition identifiers (only the 9th edition is implemented).
_ACCEPTED_EDITIONS = {
    "ajcc9", "ajcc/uicc9", "ajcc/uicc 9th edition", "9", "9th", "uicc9",
    "ajcc 9", "ajcc9th", "ajcc-9", "9e",
}
EDITION_LABEL = "AJCC/UICC 9th edition"


class StagingError(ValueError):
    """Raised when TNM descriptors cannot be resolved to a stage group."""


def normalize_edition(value: Optional[str]) -> str:
    """Return the canonical edition label, or raise for an unsupported edition.

    Only the 9th edition is implemented; anything else is refused rather than
    silently staged as 9th (which would be a version-pollution safety bug).
    """
    if value is None:
        return EDITION_LABEL
    key = "".join(str(value).split()).lower().replace("uicc/", "").replace(
        "edition", "").strip("-_ ")
    key2 = str(value).strip().lower()
    if key in _ACCEPTED_EDITIONS or key2 in _ACCEPTED_EDITIONS \
            or key2 == EDITION_LABEL.lower():
        return EDITION_LABEL
    raise StagingError(
        f"Unsupported staging edition {value!r}: this engine implements only "
        f"the AJCC/UICC 9th edition. Refusing to stage to avoid mixing editions."
    )


@dataclass
class TNM:
    """A normalized TNM descriptor triple with preserved provenance."""

    t: str
    n: str
    m: str = "M0"
    #: staging basis (clinical/pathologic/…) inferred from c/p/yc/yp/r prefixes
    basis: Optional[str] = None
    #: original descriptor strings exactly as provided (for audit)
    raw_t: Optional[str] = None
    raw_n: Optional[str] = None
    raw_m: Optional[str] = None
    #: notes about any staging-equivalent substitution (e.g. T1mi → T1a)
    aliasing_notes: list[str] = field(default_factory=list)

    @classmethod
    def parse(cls, t: str, n: str, m: str = "M0") -> "TNM":
        ct, tnote, tpfx, traw = _normalize_t(t)
        cn, nnote, npfx, nraw = _normalize_n(n)
        cm, mnote, mpfx, mraw = _normalize_m(m)
        basis, basis_note = _resolve_basis(tpfx, npfx, mpfx)
        notes = [x for x in (tnote, nnote, mnote, basis_note) if x]
        return cls(ct, cn, cm, basis=basis,
                   raw_t=traw, raw_n=nraw, raw_m=mraw, aliasing_notes=notes)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.t}{self.n}{self.m}"


@dataclass
class StageResult:
    """Result of staging, with provenance for auditing."""

    tnm: TNM
    stage_group: str
    edition: str = EDITION_LABEL
    migration_notes: list[str] = field(default_factory=list)
    descriptor_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        out = {
            "t_category": self.tnm.t,
            "n_category": self.tnm.n,
            "m_category": self.tnm.m,
            "stage_group": self.stage_group,
            "edition": self.edition,
            "staging_basis": self.tnm.basis,
            "migration_notes": list(self.migration_notes),
            "descriptor_notes": list(self.descriptor_notes),
        }
        # Preserve original descriptors when they differ from the canonical ones.
        original = {}
        for canon, raw, key in (
            (self.tnm.t, self.tnm.raw_t, "t"),
            (self.tnm.n, self.tnm.raw_n, "n"),
            (self.tnm.m, self.tnm.raw_m, "m"),
        ):
            if raw is not None and raw != canon:
                original[key] = raw
        if original:
            out["original_descriptors"] = original
        return out


# --- Normalization ----------------------------------------------------------

def _clean(value: str) -> str:
    return "".join(str(value).split()).replace("–", "-")


def _split_basis_prefix(raw: str) -> tuple[str, str]:
    """Split a c/p/yc/yp/r basis prefix off a descriptor (``cT2a`` → c, T2a)."""
    low = raw.lower()
    for pfx in _BASIS_PREFIXES:
        if low.startswith(pfx) and len(raw) > len(pfx) \
                and raw[len(pfx)].upper() in ("T", "N", "M"):
            return pfx, raw[len(pfx):]
    return "", raw


def _resolve_basis(*prefixes: str) -> tuple[Optional[str], Optional[str]]:
    present = {p for p in prefixes if p}
    if not present:
        return None, None
    if len(present) == 1:
        p = next(iter(present))
        return _BASIS_LABEL.get(p, p), None
    labels = ", ".join(sorted(_BASIS_LABEL.get(p, p) for p in present))
    return (
        "mixed",
        f"MIXED staging basis across descriptors ({labels}); clinical and "
        f"pathologic descriptors should not be combined into one stage.",
    )


def _normalize_t(t: str) -> tuple[str, Optional[str], str, str]:
    raw_full = _clean(t)
    if not raw_full:
        raise StagingError("Empty T category")
    prefix, raw = _split_basis_prefix(raw_full)
    low = raw.lower()
    note = None
    if low == "t1mi":
        note = ("Input T1mi (minimally invasive adenocarcinoma) is staged in "
                "the T1a family; original T1mi descriptor retained for audit.")
        return "T1a", note, prefix, raw
    aliases = {"tis": "Tis", "tx": "TX", "t0": "T0"}
    if low in aliases:
        return aliases[low], note, prefix, raw
    canon = raw[0].upper() + raw[1:].lower() if raw else raw
    fix = {"T1": "T1a", "T1A": "T1a", "T1B": "T1b", "T1C": "T1c",
           "T2A": "T2a", "T2B": "T2b", "T3": "T3", "T4": "T4"}
    if canon in T_CATEGORIES:
        return canon, note, prefix, raw
    if raw.upper() in fix:
        return fix[raw.upper()], note, prefix, raw
    if raw.upper() == "T2":
        raise StagingError(
            "Ambiguous 'T2': specify T2a (>3-4 cm) or T2b (>4-5 cm) — "
            "they stage differently in the 9th edition"
        )
    raise StagingError(f"Unrecognized T category: {t!r}")


def _normalize_n(n: str) -> tuple[str, Optional[str], str, str]:
    raw_full = _clean(n)
    if not raw_full:
        raise StagingError("Empty N category")
    prefix, raw = _split_basis_prefix(raw_full)
    up = raw.upper()
    aliases = {"NX": "NX", "N0": "N0", "N1": "N1", "N3": "N3",
               "N2A": "N2a", "N2B": "N2b"}
    if up in aliases:
        return aliases[up], None, prefix, raw
    if up == "N2":
        raise StagingError(
            "Ambiguous 'N2': specify N2a (single-station) or N2b "
            "(multi-station) — the 9th edition splits N2 and they stage "
            "differently"
        )
    raise StagingError(f"Unrecognized N category: {n!r}")


def _normalize_m(m: str) -> tuple[str, Optional[str], str, str]:
    raw_full = _clean(m)
    if not raw_full:
        # Unknown/empty M is NOT M0. M0 is a conclusion after metastatic workup.
        return "MX", None, "", raw_full
    prefix, raw = _split_basis_prefix(raw_full)
    up = raw.upper()
    aliases = {"MX": "MX", "M0": "M0", "M1A": "M1a", "M1B": "M1b",
               "M1C1": "M1c1", "M1C2": "M1c2"}
    if up in aliases:
        return aliases[up], None, prefix, raw
    if up == "M1C":
        raise StagingError(
            "Ambiguous 'M1c': specify M1c1 (multiple mets, single organ "
            "system) or M1c2 (multiple mets, multiple organ systems) — the "
            "9th edition splits M1c"
        )
    if up == "M1":
        raise StagingError("Ambiguous 'M1': specify M1a / M1b / M1c1 / M1c2")
    raise StagingError(f"Unrecognized M category: {m!r}")


# --- The 9th-edition stage table -------------------------------------------
#
# Keyed by (T-family, N) for M0 disease. T-family collapses T1a/b/c to "T1"
# (they share stage rows) but keeps T2a and T2b distinct (they diverge).

_M0_TABLE: dict[tuple[str, str], str] = {
    # ---- N0 ----
    ("Tis", "N0"): "0",
    ("T1", "N0"): "I",   # refined to IA1/IA2/IA3 by sub-letter below
    ("T2a", "N0"): "IB",
    ("T2b", "N0"): "IIA",
    ("T3", "N0"): "IIB",
    ("T4", "N0"): "IIIA",
    # ---- N1 ----
    ("T1", "N1"): "IIA",   # T1N1 downstaged 8th IIB -> 9th IIA
    ("T2a", "N1"): "IIB",
    ("T2b", "N1"): "IIB",
    ("T3", "N1"): "IIIA",
    ("T4", "N1"): "IIIA",
    # ---- N2a (single-station) ----
    ("T1", "N2a"): "IIB",   # T1N2a downstaged 8th IIIA -> 9th IIB
    ("T2a", "N2a"): "IIIA",
    ("T2b", "N2a"): "IIIA",
    ("T3", "N2a"): "IIIA",  # T3N2a downstaged 8th IIIB -> 9th IIIA
    ("T4", "N2a"): "IIIB",
    # ---- N2b (multi-station) ----
    ("T1", "N2b"): "IIIA",
    ("T2a", "N2b"): "IIIB",  # T2N2b upstaged 8th IIIA -> 9th IIIB
    ("T2b", "N2b"): "IIIB",
    ("T3", "N2b"): "IIIB",
    ("T4", "N2b"): "IIIB",
    # ---- N3 ----
    ("T1", "N3"): "IIIB",
    ("T2a", "N3"): "IIIB",
    ("T2b", "N3"): "IIIB",
    ("T3", "N3"): "IIIC",
    ("T4", "N3"): "IIIC",
}

# 8th-edition -> 9th-edition migrations to surface for teaching/audit.
_MIGRATIONS: dict[tuple[str, str], str] = {
    ("T1", "N1"): "T1N1 downstaged from 8th-edition IIB to 9th-edition IIA.",
    ("T1", "N2a"): "T1N2a downstaged from 8th-edition IIIA to 9th-edition IIB "
                   "(still N2 mediastinal disease — manage with N2 discipline).",
    ("T3", "N2a"): "T3N2a downstaged from 8th-edition IIIB to 9th-edition IIIA.",
    ("T2a", "N2b"): "T2N2b upstaged from 8th-edition IIIA to 9th-edition IIIB.",
    ("T2b", "N2b"): "T2N2b upstaged from 8th-edition IIIA to 9th-edition IIIB.",
}

_IA_SUBSTAGE = {"T1a": "IA1", "T1b": "IA2", "T1c": "IA3"}


def stage(tnm: TNM) -> StageResult:
    """Compute the 9th-edition stage group for a normalized TNM triple."""
    t, n, m = tnm.t, tnm.n, tnm.m
    notes: list[str] = list(tnm.aliasing_notes)
    migrations: list[str] = []

    # Metastatic disease dominates the stage group regardless of T/N — but only
    # once M is actually established.
    if m in ("M1a", "M1b"):
        notes.append(
            "M1a (intrathoracic) / M1b (single extrathoracic metastasis) → "
            "Stage IVA. (IVA spans M1a pleural/pericardial/contralateral-lung "
            "disease and M1b single distant met — these are NOT interchangeable "
            "with 'oligometastatic'; oligometastatic status is a separate axis.)"
        )
        return StageResult(tnm, "IVA", descriptor_notes=notes)
    if m in ("M1c1", "M1c2"):
        detail = ("M1c1 = multiple extrathoracic metastases, single organ "
                  "system" if m == "M1c1" else
                  "M1c2 = multiple extrathoracic metastases, multiple organ "
                  "systems (independently poorer prognosis)")
        notes.append(
            f"{detail} → Stage IVB. (Stage IVB is a TNM label, not by itself a "
            f"treatment-intent verdict — lesion count/volume, oligo-state and "
            f"MDT judgement determine local-therapy candidacy.)"
        )
        return StageResult(tnm, "IVB", descriptor_notes=notes)
    if m == "MX":
        raise StagingError(
            "M category is unknown/indeterminate (MX): complete metastatic "
            "workup (contrast CT, PET/CT and brain MRI as indicated) before "
            "assigning a curative stage group. 'M0' is a conclusion, not a "
            "default."
        )

    # M0 disease.
    if n == "NX":
        raise StagingError(
            "N category is NX (indeterminate): nodal status must be "
            "established (invasive mediastinal staging where it changes intent)"
        )
    if t == "T0":
        raise StagingError(
            "T0 (no evidence of primary tumour): cannot assign a definitive "
            "stage group without identifying the primary — pursue occult-"
            "primary workup."
        )
    if t == "TX":
        if n == "N0":
            return StageResult(tnm, "Occult",
                               descriptor_notes=notes
                               + ["TX N0 M0 → occult carcinoma."])
        raise StagingError("TX with node-positive disease cannot be staged")

    family = _T_FAMILY[t]
    key = (family, n)
    if key not in _M0_TABLE:
        raise StagingError(f"No 9th-edition stage row for {t} {n} {m}")

    group = _M0_TABLE[key]

    # Refine Stage I into IA1/IA2/IA3/IB by T sub-letter.
    if group == "I":
        group = _IA_SUBSTAGE.get(t, "IB")

    if key in _MIGRATIONS:
        migrations.append(_MIGRATIONS[key])

    if n in ("N2a", "N2b"):
        notes.append(
            "N2 subcategory is decision-relevant in the 9th edition "
            "(N2a single-station vs N2b multi-station)."
        )

    return StageResult(tnm, group, migration_notes=migrations,
                       descriptor_notes=notes)


def stage_from_strings(t: str, n: str, m: str = "M0") -> StageResult:
    """Convenience: normalize raw descriptor strings and stage them."""
    return stage(TNM.parse(t, n, m))
