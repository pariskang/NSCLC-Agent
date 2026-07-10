"""Loader for the stage-specific NSCLC protocol modules.

Each module is a self-contained Markdown system prompt (v3.3, 2026-06) that
encodes the evidence-based decision framework, JSON output schema and safety
rules for one stage band. The Markdown *is* the protocol; this loader just
reads it and exposes light metadata.
"""

from __future__ import annotations

import functools
from pathlib import Path
from dataclasses import dataclass

_PROMPT_DIR = Path(__file__).resolve().parent

# module key -> (filename, human label, stage groups covered)
MODULES: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "stage1": ("stage1.md", "Stage I (early-stage curative intent)",
               ("0", "IA1", "IA2", "IA3", "IB")),
    "stage2": ("stage2.md", "Stage II (resectable node-positive, curative)",
               ("IIA", "IIB")),
    "stage3a": ("stage3a.md",
                "Stage IIIA (resectable / perioperative-adjuvant)", ("IIIA",)),
    "stage3b": ("stage3b.md", "Stage IIIB (locally advanced)", ("IIIB",)),
    "stage3c": ("stage3c.md", "Stage IIIC (N3 locally advanced)", ("IIIC",)),
    "stage4a": ("stage4a.md", "Stage IVA (M1a/M1b metastatic)", ("IVA",)),
    "stage4b": ("stage4b.md", "Stage IVB (M1c1/M1c2 polymetastatic)", ("IVB",)),
}


@dataclass
class PromptModule:
    key: str
    label: str
    stage_groups: tuple[str, ...]
    path: Path
    system_prompt: str


class PromptNotFound(FileNotFoundError):
    pass


@functools.lru_cache(maxsize=None)
def load_module(key: str) -> PromptModule:
    """Load and cache a protocol module by key (e.g. 'stage3b')."""
    if key not in MODULES:
        raise PromptNotFound(
            f"Unknown module {key!r}. Available: {', '.join(sorted(MODULES))}"
        )
    filename, label, groups = MODULES[key]
    path = _PROMPT_DIR / filename
    if not path.is_file():
        raise PromptNotFound(f"Protocol file missing: {path}")
    text = path.read_text(encoding="utf-8")
    return PromptModule(key, label, groups, path, text)


def list_modules() -> list[PromptModule]:
    return [load_module(k) for k in MODULES]
