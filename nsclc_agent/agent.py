"""The NSCLC agent orchestrator.

Pipeline for one case:

    case ──▶ deterministic TNM-9 staging ──▶ module routing ──▶ prompt assembly
         ──▶ provider completion ──▶ structured AgentResult

The stage group is computed symbolically and *injected* into the system prompt
so the model reasons within a verified stage rather than re-deriving (and
possibly hallucinating) it. Every result carries full provenance for auditing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

from .case import Case
from .config import Config, load_config
from .prompts import PromptModule, load_module
from .providers.base import GenerationParams, LLMProvider, LLMResponse, Message
from .providers.registry import build_provider
from .staging import RouteResult, StageResult, StagingError, route, stage_from_strings
from .staging.tnm import TNM

_EDU_DISCLAIMER = (
    "EDUCATIONAL / RESEARCH USE ONLY. This system generates decision-support "
    "and training material for teaching and evaluation. It is not a medical "
    "device and its output must not be used for real patient care without "
    "review by a qualified multidisciplinary oncology team."
)


@dataclass
class AgentResult:
    case_id: Optional[str]
    staging: Optional[dict]
    routing: dict
    module_key: Optional[str]
    provider: Optional[str]
    response: Optional[LLMResponse]
    flags: list[str] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "staging": self.staging,
            "routing": self.routing,
            "module_key": self.module_key,
            "provider": self.provider,
            "response": self.response.to_dict() if self.response else None,
            "flags": self.flags,
            "error": self.error,
            "disclaimer": _EDU_DISCLAIMER,
        }


class NSCLCAgent:
    def __init__(
        self,
        config: Optional[Config] = None,
        *,
        allow_fallback_module: bool = True,
    ):
        self.config = config or load_config()
        self.allow_fallback_module = allow_fallback_module
        self._provider_cache: dict[str, LLMProvider] = {}

    # -- provider handling --------------------------------------------------

    def get_provider(self, name: Optional[str] = None) -> LLMProvider:
        name = name or self.config.default_provider
        if name not in self._provider_cache:
            cfg = self.config.provider_config(name)
            self._provider_cache[name] = build_provider(
                name, cfg, defaults=self.config.generation
            )
        return self._provider_cache[name]

    # -- staging + routing (no LLM) -----------------------------------------

    def resolve_stage(self, case: Case) -> tuple[Optional[StageResult], list[str]]:
        flags: list[str] = []
        if case.has_tnm():
            try:
                result = stage_from_strings(case.t, case.n, case.m or "M0")
            except StagingError as exc:
                flags.append(f"STAGING_ERROR: {exc}")
                return None, flags
            if case.stage_group and case.stage_group != result.stage_group:
                flags.append(
                    f"STAGE_MISMATCH: provided {case.stage_group} but TNM "
                    f"{result.tnm} computes to {result.stage_group} (using "
                    f"computed value)"
                )
            return result, flags
        if case.stage_group:
            flags.append("STAGE_FROM_LABEL: no TNM provided; using the given "
                         "stage_group without deterministic verification")
            return (
                StageResult(
                    tnm=TNM(t=case.t or "TX", n=case.n or "NX",
                            m=case.m or "MX"),
                    stage_group=case.stage_group,
                ),
                flags,
            )
        flags.append("STAGE_UNRESOLVED: no TNM and no stage_group provided")
        return None, flags

    def route_case(self, case: Case) -> tuple[
        Optional[StageResult], Optional[RouteResult], list[str]
    ]:
        stage_result, flags = self.resolve_stage(case)
        if stage_result is None:
            return None, None, flags
        route_result = route(stage_result.stage_group)
        if not route_result.available:
            flags.append(
                f"MODULE_UNAVAILABLE: no dedicated module for stage "
                f"{stage_result.stage_group}"
                + (f" — {route_result.note}" if route_result.note else "")
            )
        return stage_result, route_result, flags

    # -- prompt assembly ----------------------------------------------------

    def _staging_preamble(self, stage_result: StageResult) -> str:
        payload = stage_result.to_dict()
        lines = [
            "=== DETERMINISTIC STAGING (computed by a verified symbolic engine, "
            "not by you) ===",
            f"TNM: {stage_result.tnm}",
            f"Stage group: {stage_result.stage_group} ({stage_result.edition})",
        ]
        if stage_result.migration_notes:
            lines.append("8th→9th migration: "
                         + " ".join(stage_result.migration_notes))
        if stage_result.descriptor_notes:
            lines.append("Notes: " + " ".join(stage_result.descriptor_notes))
        lines.append(
            "Treat this stage assignment as authoritative. Do NOT re-derive or "
            "override the stage group; reason about management within it."
        )
        return "\n".join(lines)

    def build_messages(
        self, case: Case, module: PromptModule, stage_result: StageResult
    ) -> list[Message]:
        system = module.system_prompt + "\n\n" + self._staging_preamble(stage_result)
        return [
            Message("system", system),
            Message("user", case.render_user_message()),
        ]

    # -- full run -----------------------------------------------------------

    def run(
        self,
        case: Case,
        *,
        provider: Optional[str] = None,
        params: Optional[GenerationParams] = None,
        dry_run: bool = False,
    ) -> AgentResult:
        stage_result, route_result, flags = self.route_case(case)
        staging_dict = stage_result.to_dict() if stage_result else None
        routing_dict = route_result.__dict__ if route_result else {}

        if stage_result is None or route_result is None:
            return AgentResult(
                case.case_id, staging_dict, routing_dict, None, None, None,
                flags=flags, error="Could not resolve stage/routing for case",
            )

        module_key = route_result.module_key
        if module_key is None and self.allow_fallback_module \
                and route_result.fallback_module_key:
            module_key = route_result.fallback_module_key
            flags.append(
                f"USING_FALLBACK_MODULE: {module_key} (no exact module for "
                f"stage {stage_result.stage_group})"
            )

        if module_key is None:
            return AgentResult(
                case.case_id, staging_dict, routing_dict, None, None, None,
                flags=flags,
                error=(f"No protocol module available for stage "
                       f"{stage_result.stage_group}"),
            )

        module = load_module(module_key)
        messages = self.build_messages(case, module, stage_result)

        if dry_run:
            return AgentResult(
                case.case_id, staging_dict, routing_dict, module_key, None,
                LLMResponse(
                    content="[dry-run: prompt assembled, model not called]",
                    provider="(none)", model="(none)",
                ),
                flags=flags + ["DRY_RUN"],
            )

        try:
            prov = self.get_provider(provider)
            response = prov.complete(messages, params=params)
        except Exception as exc:  # provider config/transport errors → result.error
            prov_name = provider or self.config.default_provider
            return AgentResult(
                case.case_id, staging_dict, routing_dict, module_key,
                prov_name, None, flags=flags, error=str(exc),
            )
        return AgentResult(
            case.case_id, staging_dict, routing_dict, module_key, prov.name,
            response, flags=flags,
        )
