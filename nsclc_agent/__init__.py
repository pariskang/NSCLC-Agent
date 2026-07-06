"""NSCLC Agent — evidence-based, stage-aware decision-support for teaching.

A stage-aware NSCLC decision-support agent built around three ideas:

1. A **deterministic AJCC/UICC 9th-edition staging engine** computes the stage
   group symbolically (the model never invents it).
2. A **stage router** dispatches each case to the matching evidence-based
   protocol module (Stage II / IIIB / IIIC / IVA / IVB).
3. A **pluggable provider layer** runs the reasoning on any of LiteLLM, Azure
   OpenAI, Poe or MiniMax (plus an offline mock) for teaching and testing.

For educational / research use only — not a medical device.
"""

from .agent import AgentResult, NSCLCAgent
from .case import Case
from .config import Config, load_config
from .staging import stage_from_strings, route

__version__ = "0.1.0"

__all__ = [
    "NSCLCAgent",
    "AgentResult",
    "Case",
    "Config",
    "load_config",
    "stage_from_strings",
    "route",
    "__version__",
]
