"""NSCLC Agent — evidence-based, stage-aware decision-support for teaching.

A stage-aware NSCLC decision-support agent built around four ideas:

1. A **perception layer** (optional) reads radiology films through a
   vision-capable backend (e.g. Gemini via the Poe API) and *proposes* candidate
   radiographic TNM descriptors — it never assigns the stage group.
2. A **deterministic AJCC/UICC 9th-edition staging engine** computes the stage
   group symbolically from the verified descriptors (the model never invents it).
3. A **stage router** dispatches each case to the matching evidence-based
   protocol module (Stage I / II / IIIA / IIIB / IIIC / IVA / IVB).
4. A **pluggable provider layer** runs the reasoning on any of LiteLLM, Azure
   OpenAI, Poe or MiniMax (plus an offline mock) for teaching and testing.

For educational / research use only — not a medical device.
"""

from .agent import AgentResult, NSCLCAgent
from .case import Case
from .config import Config, load_config
from .imaging import ImagingFindings, ImagingReader, load_image_ref
from .staging import stage_from_strings, route

__version__ = "0.1.0"

__all__ = [
    "NSCLCAgent",
    "AgentResult",
    "Case",
    "Config",
    "load_config",
    "ImagingFindings",
    "ImagingReader",
    "load_image_ref",
    "stage_from_strings",
    "route",
    "__version__",
]
