"""Case input model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Case:
    """A single NSCLC case to be staged, routed and reasoned about.

    Provide either explicit TNM descriptors (t/n/m) — which are staged by the
    deterministic engine — or a pre-computed ``stage_group``. A free-text
    ``presentation`` and an explicit ``question`` are passed to the model.
    """

    case_id: Optional[str] = None
    t: Optional[str] = None
    n: Optional[str] = None
    m: Optional[str] = None
    stage_group: Optional[str] = None
    staging_system: str = "AJCC9"
    presentation: str = ""
    question: str = ""
    #: radiology film references (file paths, data: URLs, or http(s) URLs) to
    #: be read by the vision/perception layer into candidate TNM descriptors.
    images: list[str] = field(default_factory=list)
    fields: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Case":
        known = {
            "case_id", "t", "n", "m", "stage_group", "staging_system",
            "presentation", "question", "images",
        }
        core = {k: data.get(k) for k in known if k in data}
        # accept common aliases
        if "id" in data and "case_id" not in core:
            core["case_id"] = data["id"]
        if "t_category" in data and "t" not in core:
            core["t"] = data["t_category"]
        if "n_category" in data and "n" not in core:
            core["n"] = data["n_category"]
        if "m_category" in data and "m" not in core:
            core["m"] = data["m_category"]
        extras = {k: v for k, v in data.items()
                  if k not in known and k not in ("id", "t_category",
                                                   "n_category", "m_category")}
        core.setdefault("staging_system", "AJCC9")
        if isinstance(core.get("images"), str):
            core["images"] = [core["images"]]
        elif core.get("images") is None and "images" in core:
            core["images"] = []
        return cls(fields=extras, **core)

    def has_images(self) -> bool:
        return bool(self.images)

    def has_tnm(self) -> bool:
        return bool(self.t and self.n)

    def render_user_message(self) -> str:
        """Compose the user turn from presentation, structured fields, question."""
        parts: list[str] = []
        if self.presentation:
            parts.append(self.presentation.strip())
        if self.fields:
            import json
            parts.append(
                "Structured case data:\n"
                + json.dumps(self.fields, ensure_ascii=False, indent=2)
            )
        if self.question:
            parts.append(self.question.strip())
        if not parts:
            parts.append(
                "Provide the evidence-based decision-support analysis for this "
                "case following the module's framework and JSON output schema."
            )
        return "\n\n".join(parts)
