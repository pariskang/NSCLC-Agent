"""Route a computed stage group to the correct protocol module.

The shipped protocol library covers Stage II, IIIB, IIIC, IVA and IVB. Stages
0/I/IIIA are staged correctly by the engine but do not yet have a dedicated
module in this repository; the router surfaces that explicitly rather than
silently mis-routing (a category error would be a safety issue).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Maps a stage group -> protocol module key (the filename stem in prompts/).
_STAGE_TO_MODULE: dict[str, Optional[str]] = {
    "0": "stage1",        # Tis / AIS-MIA — handled by the Stage I module
    "Occult": None,
    "IA1": "stage1",
    "IA2": "stage1",
    "IA3": "stage1",
    "IB": "stage1",
    "IIA": "stage2",
    "IIB": "stage2",
    "IIIA": "stage3a",
    "IIIB": "stage3b",
    "IIIC": "stage3c",
    "IVA": "stage4a",
    "IVB": "stage4b",
}

# Human-readable guidance for stages without a dedicated module.
_UNROUTED_GUIDANCE: dict[str, str] = {
    "Occult": "Occult carcinoma (TX N0 M0) — the primary is not localized; "
              "complete the localization/staging workup before selecting a "
              "treatment module.",
}


@dataclass
class RouteResult:
    stage_group: str
    module_key: Optional[str]
    available: bool
    note: Optional[str] = None
    fallback_module_key: Optional[str] = None


def route(stage_group: str) -> RouteResult:
    """Return the protocol module for a stage group."""
    if stage_group not in _STAGE_TO_MODULE:
        return RouteResult(stage_group, None, False,
                           note=f"Unknown stage group {stage_group!r}.")
    module = _STAGE_TO_MODULE[stage_group]
    if module is not None:
        return RouteResult(stage_group, module, True)
    return RouteResult(
        stage_group,
        None,
        False,
        note=_UNROUTED_GUIDANCE.get(stage_group, "No module available."),
        fallback_module_key=None,
    )


def available_modules() -> list[str]:
    return sorted({m for m in _STAGE_TO_MODULE.values() if m})
