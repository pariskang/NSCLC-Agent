"""Perception layer — read radiographic films into candidate TNM descriptors.

This is the POMDP *observation function* `P(o|s)`: a vision-capable model (e.g.
Gemini via the Poe API) looks at CT / PET-CT / MRI images and **proposes**
candidate radiographic descriptors — a suggested cT, cN (with nodal stations),
cM, measurable lesions, effusion, metastatic sites.

The single most important design rule lives here:

    The vision model PROPOSES descriptors. It does NOT assign the stage group.

Every value it returns is explicitly ``MODEL-PROPOSED, UNVERIFIED``. The
descriptors are fed back into the deterministic 9th-edition staging engine
(``staging/tnm.py``) — they never bypass it — and, when the case already
carries human/pathologic TNM, the proposal is *cross-checked* against it and a
discordance is flagged rather than silently overriding either source. That is
what keeps "verifiable staging" true even with a fallible reader in the loop.

Stdlib only: images are base64-encoded into ``data:`` URLs with the standard
library, so no third-party imaging dependency is required. DICOM is not parsed
here — export slices to PNG/JPEG first (or pass an ``http(s)`` URL).
"""

from __future__ import annotations

import base64
import json
import mimetypes
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .providers.base import GenerationParams, LLMProvider, Message
from .staging.tnm import M_CATEGORIES, N_CATEGORIES, T_CATEGORIES

# Marker the offline mock keys on to recognise a film-reading request.
from .providers.mock import IMAGING_MARKER

_IMAGE_EXTS = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}

_MAX_INLINE_BYTES = 20 * 1024 * 1024  # 20 MB guard per image


class ImagingError(RuntimeError):
    """Raised when an image cannot be loaded or a reading cannot be parsed."""


# --------------------------------------------------------------------------- #
# Image loading (→ OpenAI-compatible image URL)                               #
# --------------------------------------------------------------------------- #

def load_image_ref(ref: str) -> str:
    """Resolve one image reference to a URL usable in a vision message.

    Accepts a local file path, an existing ``data:`` URL, or an ``http(s)``
    URL. Local files are base64-encoded into a ``data:`` URL with the standard
    library so no network fetch or extra dependency is needed.
    """
    if ref.startswith(("data:", "http://", "https://")):
        return ref
    path = Path(ref).expanduser()
    if not path.is_file():
        raise ImagingError(f"Image not found: {ref}")
    size = path.stat().st_size
    if size > _MAX_INLINE_BYTES:
        raise ImagingError(
            f"Image {path.name} is {size / 1e6:.1f} MB, over the "
            f"{_MAX_INLINE_BYTES / 1e6:.0f} MB inline limit. Downscale it or "
            f"host it and pass an https URL."
        )
    mime = _IMAGE_EXTS.get(path.suffix.lower())
    if mime is None:
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def load_image_refs(refs: list[str]) -> list[str]:
    return [load_image_ref(r) for r in refs]


# --------------------------------------------------------------------------- #
# Findings model                                                              #
# --------------------------------------------------------------------------- #

@dataclass
class ImagingFindings:
    """Model-PROPOSED, UNVERIFIED radiographic descriptors read from films."""

    modality: Optional[str] = None
    candidate_t: Optional[str] = None
    candidate_n: Optional[str] = None
    candidate_m: Optional[str] = None
    nodal_stations: list[str] = field(default_factory=list)
    measurable_lesions: list[dict[str, Any]] = field(default_factory=list)
    malignant_effusion: Optional[bool] = None
    metastatic_sites: list[str] = field(default_factory=list)
    confidence: Optional[str] = None
    uncertainties: list[str] = field(default_factory=list)
    provider: Optional[str] = None
    model: Optional[str] = None
    n_images: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    #: descriptors the reader could not determine — the seed for a
    #: "what to check next" (value-of-information) recommendation.
    def unresolved_descriptors(self) -> list[str]:
        out = []
        if not self.candidate_t:
            out.append("T")
        if not self.candidate_n:
            out.append("N")
        if not self.candidate_m:
            out.append("M")
        return out

    def proposed_tnm(self) -> tuple[Optional[str], Optional[str], Optional[str]]:
        return (self.candidate_t, self.candidate_n, self.candidate_m)

    def is_complete(self) -> bool:
        return not self.unresolved_descriptors()

    def to_dict(self) -> dict:
        return {
            "status": "MODEL_PROPOSED_UNVERIFIED",
            "modality": self.modality,
            "candidate_t": self.candidate_t,
            "candidate_n": self.candidate_n,
            "candidate_m": self.candidate_m,
            "nodal_stations": self.nodal_stations,
            "measurable_lesions": self.measurable_lesions,
            "malignant_effusion": self.malignant_effusion,
            "metastatic_sites": self.metastatic_sites,
            "confidence": self.confidence,
            "uncertainties": self.uncertainties,
            "unresolved_descriptors": self.unresolved_descriptors(),
            "read_by": {"provider": self.provider, "model": self.model,
                        "images": self.n_images},
        }


# --------------------------------------------------------------------------- #
# Extraction prompt                                                           #
# --------------------------------------------------------------------------- #

_T_VOCAB = ", ".join(t for t in T_CATEGORIES if t != "TX")
_N_VOCAB = ", ".join(n for n in N_CATEGORIES if n != "NX")
_M_VOCAB = ", ".join(m for m in M_CATEGORIES if m != "MX")

IMAGING_EXTRACTION_PROMPT = f"""\
=== {IMAGING_MARKER} (NSCLC, AJCC/UICC 9th edition) ===

You are a thoracic-imaging description assistant supporting an educational NSCLC
staging pipeline. You are shown one or more radiology images (CT, PET-CT, MRI,
or reconstructions) for a single patient.

YOUR ROLE — and its hard limits:
  • PROPOSE candidate radiographic TNM *descriptors* and measurements.
  • You DO NOT assign a stage group. A separate deterministic engine does that.
  • You must NOT invent findings you cannot see. If a descriptor cannot be
    determined from the image(s), return null for it and add a line to
    "uncertainties" naming what is missing and what would resolve it.
  • Everything you output is treated as MODEL-PROPOSED and UNVERIFIED, pending
    confirmation by the reporting radiologist / MDT and by pathology.

Use the exact 9th-edition vocabulary:
  cT ∈ {{{_T_VOCAB}}} (or null)
  cN ∈ {{{_N_VOCAB}}} (or null)
      N2a = a single mediastinal (ipsilateral) station; N2b = multiple
      mediastinal stations. If you cannot tell single vs multi-station, use
      null and say so — do NOT guess "N2".
  cM ∈ {{{_M_VOCAB}}} (or null)
      M1a = intrathoracic (contralateral lung nodules, pleural/pericardial
      nodules or malignant effusion); M1b = single extrathoracic metastasis;
      M1c1 = multiple extrathoracic mets in ONE organ system; M1c2 = multiple
      extrathoracic mets in MULTIPLE organ systems.

Report nodal involvement by IASLC station number (e.g. "4R", "7", "10L").

Respond with ONLY a JSON object, no prose outside it, in exactly this shape:
{{
  "modality": "CT | PET-CT | MRI | other",
  "candidate_t": "<cT or null>",
  "candidate_n": "<cN or null>",
  "candidate_m": "<cM or null>",
  "nodal_stations": ["<IASLC station>", ...],
  "measurable_lesions": [
    {{"site": "<location>", "long_axis_mm": <number>, "note": "<optional>"}}
  ],
  "malignant_effusion": true | false | null,
  "metastatic_sites": ["<organ/site>", ...],
  "confidence": "high | moderate | low | none",
  "uncertainties": ["<what is indeterminate and the test that would resolve it>"]
}}
"""


# --------------------------------------------------------------------------- #
# Reader                                                                      #
# --------------------------------------------------------------------------- #

_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _extract_json(text: str) -> dict:
    """Pull the JSON object out of a model reply, tolerating code fences/prose."""
    text = text.strip()
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
    # last resort: first balanced-looking {...} span
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    raise ImagingError(
        f"Could not parse imaging findings as JSON from model reply: "
        f"{text[:300]}"
    )


def _norm(value: Any) -> Optional[str]:
    """Normalise a proposed descriptor: null-ish → None, else stripped string."""
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() in ("null", "none", "n/a", "na", "unknown", "?"):
        return None
    return s


class ImagingReader:
    """Reads films through a vision provider into proposed descriptors."""

    def __init__(self, provider: LLMProvider):
        self.provider = provider

    def read(
        self,
        images: list[str],
        *,
        context: str = "",
        params: Optional[GenerationParams] = None,
    ) -> ImagingFindings:
        if not images:
            raise ImagingError("No images provided to the imaging reader.")
        urls = load_image_refs(images)
        user_text = (
            "Read the attached film(s) and return the JSON described above."
        )
        if context:
            user_text += (
                "\n\nClinical context provided by the referrer (may be "
                "incomplete; do not over-read):\n" + context.strip()
            )
        messages = [
            Message("system", IMAGING_EXTRACTION_PROMPT),
            Message("user", user_text, images=urls),
        ]
        # Low temperature: descriptor extraction should be as deterministic as
        # the backend allows.
        p = (params or self.provider.params).merged(temperature=0.0)
        response = self.provider.complete(messages, params=p)
        data = _extract_json(response.content)
        return self._to_findings(data, response.provider, response.model,
                                 len(urls))

    @staticmethod
    def _to_findings(
        data: dict, provider: str, model: str, n_images: int
    ) -> ImagingFindings:
        eff = data.get("malignant_effusion")
        if isinstance(eff, str):
            eff = {"true": True, "false": False}.get(eff.strip().lower())
        return ImagingFindings(
            modality=_norm(data.get("modality")),
            candidate_t=_norm(data.get("candidate_t")),
            candidate_n=_norm(data.get("candidate_n")),
            candidate_m=_norm(data.get("candidate_m")),
            nodal_stations=[str(s) for s in data.get("nodal_stations") or []],
            measurable_lesions=[
                d for d in (data.get("measurable_lesions") or [])
                if isinstance(d, dict)
            ],
            malignant_effusion=eff if isinstance(eff, bool) else None,
            metastatic_sites=[str(s) for s in data.get("metastatic_sites") or []],
            confidence=_norm(data.get("confidence")),
            uncertainties=[str(u) for u in data.get("uncertainties") or []],
            provider=provider,
            model=model,
            n_images=n_images,
            raw=data,
        )
