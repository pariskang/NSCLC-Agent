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
from dataclasses import dataclass, field, replace
from typing import Any, Optional

from .case import Case
from .config import Config, load_config
from .imaging import ImagingFindings, ImagingReader
from .prompts import PromptModule, load_module
from .providers.base import GenerationParams, LLMProvider, LLMResponse, Message
from .providers.registry import build_provider
from .safety import evaluate_gates
from .staging import (
    RouteResult, StageResult, StagingError, normalize_edition, route,
    stage_from_strings,
)
from .staging.tnm import TNM
from .validation import validate_output

#: imaging confidence levels required before proposed descriptors are allowed
#: to seed the deterministic stage. Anything else (low/none/absent) stays
#: advisory only — a mechanical mapping cannot make an unreliable input reliable.
_ADEQUATE_CONFIDENCE = {"high", "moderate"}

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
    #: model-proposed radiographic descriptors, if films were read (perception
    #: layer). Always recorded as UNVERIFIED provenance, never as ground truth.
    imaging: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "staging": self.staging,
            "routing": self.routing,
            "module_key": self.module_key,
            "provider": self.provider,
            "imaging": self.imaging,
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
        vision_provider: Optional[str] = None,
    ):
        self.config = config or load_config()
        self.allow_fallback_module = allow_fallback_module
        self.vision_provider = vision_provider or self.config.vision_provider
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

    def resolve_vision_provider_name(
        self, override: Optional[str] = None
    ) -> str:
        """Pick the provider that reads films.

        Preference: explicit override → configured ``vision_provider`` → any
        provider flagged vision-capable → the default provider.
        """
        if override:
            return override
        if self.vision_provider:
            return self.vision_provider
        for name in self.config.provider_names():
            cfg = self.config.provider_config(name)
            if isinstance(cfg, dict) and cfg.get("vision"):
                return name
        return self.config.default_provider

    # -- perception / imaging (vision) --------------------------------------

    def read_imaging(
        self,
        case: Case,
        *,
        provider: Optional[str] = None,
        params: Optional[GenerationParams] = None,
    ) -> ImagingFindings:
        """Read the case's films into model-PROPOSED radiographic descriptors.

        The returned descriptors are UNVERIFIED observations; they are fed back
        into the deterministic staging engine and cross-checked — they never
        assign the stage group themselves.
        """
        vname = self.resolve_vision_provider_name(provider)
        prov = self.get_provider(vname)
        reader = ImagingReader(prov)
        return reader.read(case.images, context=case.presentation, params=params)

    def _ingest_imaging(
        self, case: Case, findings: ImagingFindings
    ) -> tuple[Case, list[str], bool]:
        """Fold model-proposed descriptors into the case, safely.

        Human/pathologic descriptors stay authoritative and are only
        *cross-checked* (discordance is flagged, never overridden). Descriptors
        the case is missing may be *seeded* from the proposal so the engine can
        still stage — but only when the reader's confidence is adequate, and the
        resulting stage is marked PROVISIONAL (not authoritative). Returns the
        (possibly updated) case, flags, and whether staging is now provisional.
        """
        flags: list[str] = []
        proposed = {
            "t": findings.candidate_t,
            "n": findings.candidate_n,
            "m": findings.candidate_m,
        }
        # Cross-check any human descriptors regardless of confidence.
        for desc in ("t", "n", "m"):
            human = getattr(case, desc)
            prop = proposed[desc]
            if human and prop and str(human).strip() != str(prop).strip():
                flags.append(
                    f"IMAGING_DISCORDANCE[{desc.upper()}]: case says "
                    f"{human} but the film reader proposed {prop} — the "
                    f"case value is used for staging; reconcile before use."
                )

        conf = (findings.confidence or "").strip().lower()
        adequate = conf in _ADEQUATE_CONFIDENCE
        seedable = {d: proposed[d] for d in ("t", "n", "m")
                    if not getattr(case, d) and proposed[d]}

        provisional = False
        new_case = case
        if seedable and not adequate:
            # Do NOT let low/unspecified-confidence reads drive the deterministic
            # stage: a mechanical mapping cannot turn an unreliable input into a
            # reliable stage. Keep them advisory only.
            flags.append(
                "IMAGING_LOW_CONFIDENCE_NOT_STAGED: reader confidence is "
                f"'{findings.confidence or 'unspecified'}', so proposed "
                "descriptor(s) "
                + ", ".join(f"c{d.upper()}={v}" for d, v in seedable.items())
                + " were NOT used to compute a stage — obtain confirmatory "
                "imaging/tissue before staging."
            )
        elif seedable:
            new_case = replace(case, **seedable)
            provisional = True
            flags.append(
                "RADIOGRAPHIC_TNM_PROPOSED: stage computed from model-proposed "
                "descriptors ("
                + ", ".join(f"c{d.upper()}={v}" for d, v in seedable.items())
                + ") — PROVISIONAL cTNM, NOT authoritative, pending "
                "radiologist/pathology confirmation."
            )

        hint = self._next_step_hint(new_case, findings)
        if hint:
            flags.append(hint)
        return new_case, flags, provisional

    def _next_step_hint(
        self, case: Case, findings: ImagingFindings
    ) -> Optional[str]:
        """A minimal value-of-information seed: name the test that resolves the
        descriptor left unresolved after film reading."""
        missing = []
        if not case.t:
            missing.append("T")
        if not case.n:
            missing.append("N")
        if not case.m:
            missing.append("M")
        suggestions = {
            "T": "dedicated contrast CT / bronchoscopy to fix the T descriptor",
            "N": "EBUS-TBNA of suspicious stations to resolve N (single- vs "
                 "multi-station changes IIIA↔IIIB)",
            "M": "PET-CT + brain MRI to confirm/exclude distant metastasis (M)",
        }
        if not missing:
            return None
        steps = "; ".join(suggestions[d] for d in missing)
        return (
            f"NEXT_STEP_SUGGESTED: descriptor(s) {', '.join(missing)} not "
            f"established from the films — {steps}."
        )

    # -- staging + routing (no LLM) -----------------------------------------

    def resolve_stage(self, case: Case) -> tuple[Optional[StageResult], list[str]]:
        flags: list[str] = []

        # Refuse to stage under an edition this engine does not implement,
        # rather than silently applying the 9th-edition table (version pollution).
        try:
            edition = normalize_edition(case.staging_system)
        except StagingError as exc:
            flags.append(f"STAGING_EDITION_UNSUPPORTED: {exc}")
            return None, flags

        if case.has_tnm():
            # M is required. 'M0' is a conclusion after metastatic workup, never
            # assumed from a missing field.
            if not case.m:
                flags.append(
                    "STAGING_INCOMPLETE_M_UNKNOWN: T and N provided but M is "
                    "missing. M is not defaulted to M0 — complete the "
                    "metastatic workup (contrast CT, PET/CT, brain MRI as "
                    "indicated) and provide M before staging."
                )
                return None, flags
            try:
                result = stage_from_strings(case.t, case.n, case.m)
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
            flags.append(
                "STAGE_FROM_LABEL: no TNM provided; using the given "
                "stage_group WITHOUT deterministic verification and WITHOUT a "
                "confirmed edition — the label's edition is unknown, so 8th/9th "
                "migration cannot be checked."
            )
            return (
                StageResult(
                    tnm=TNM(t=case.t or "TX", n=case.n or "NX",
                            m=case.m or "MX"),
                    stage_group=case.stage_group,
                    edition="unverified (from provided label)",
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

    def _staging_preamble(
        self, stage_result: StageResult, *, provisional: bool = False
    ) -> str:
        header = (
            "=== PROVISIONAL RADIOGRAPHIC STAGING (computed by the symbolic "
            "engine from MODEL-PROPOSED, UNVERIFIED descriptors) ==="
            if provisional else
            "=== DETERMINISTIC STAGING (computed by a verified symbolic engine, "
            "not by you) ==="
        )
        lines = [
            header,
            f"TNM: {stage_result.tnm}",
            f"Stage group: {stage_result.stage_group} ({stage_result.edition})",
        ]
        if stage_result.tnm.basis:
            lines.append(f"Staging basis: {stage_result.tnm.basis}")
        if stage_result.migration_notes:
            lines.append("8th→9th migration: "
                         + " ".join(stage_result.migration_notes))
        if stage_result.descriptor_notes:
            lines.append("Notes: " + " ".join(stage_result.descriptor_notes))
        if provisional:
            lines.append(
                "This stage is PROVISIONAL: at least one descriptor came from an "
                "unverified film reading, NOT from a radiologist's report or "
                "pathology. Do NOT present it as definitive. State explicitly "
                "that it is pending confirmation and name the confirmatory step "
                "before any treatment commitment."
            )
        else:
            lines.append(
                "Treat this stage assignment as authoritative. Do NOT re-derive "
                "or override the stage group; reason about management within it."
            )
        return "\n".join(lines)

    def _imaging_block(self, findings: ImagingFindings) -> str:
        return (
            "=== RADIOGRAPHIC FINDINGS (proposed by the imaging reader, "
            "UNVERIFIED) ===\n"
            "These candidate descriptors were read from the films by a vision "
            "model. They are NOT the radiologist's report and NOT pathology; do "
            "not treat them as confirmed. The authoritative stage above already "
            "reflects the verified descriptors — use these only as supporting "
            "context.\n"
            + json.dumps(findings.to_dict(), ensure_ascii=False, indent=2)
        )

    def build_messages(
        self,
        case: Case,
        module: PromptModule,
        stage_result: StageResult,
        *,
        imaging_findings: Optional[ImagingFindings] = None,
        provisional: bool = False,
    ) -> list[Message]:
        system = module.system_prompt + "\n\n" + self._staging_preamble(
            stage_result, provisional=provisional)
        user = case.render_user_message()
        if imaging_findings is not None:
            user += "\n\n" + self._imaging_block(imaging_findings)
        return [
            Message("system", system),
            Message("user", user),
        ]

    # -- full run -----------------------------------------------------------

    def run(
        self,
        case: Case,
        *,
        provider: Optional[str] = None,
        params: Optional[GenerationParams] = None,
        dry_run: bool = False,
        read_films: bool = True,
    ) -> AgentResult:
        case_id = case.case_id
        imaging_flags: list[str] = []
        imaging_findings: Optional[ImagingFindings] = None
        provisional = False

        # -- perception layer: read films → proposed descriptors ------------
        if case.has_images() and read_films and not dry_run:
            try:
                imaging_findings = self.read_imaging(case, params=params)
            except Exception as exc:  # graceful: fall back to any given TNM
                imaging_flags.append(f"IMAGING_READ_FAILED: {exc}")
            else:
                case, ingest_flags, provisional = self._ingest_imaging(
                    case, imaging_findings)
                imaging_flags.extend(ingest_flags)
        elif case.has_images() and dry_run:
            imaging_flags.append(
                "IMAGING_SKIPPED_DRY_RUN: films attached but not read "
                "(dry-run makes no model calls)"
            )

        imaging_dict = imaging_findings.to_dict() if imaging_findings else None

        stage_result, route_result, flags = self.route_case(case)
        flags = imaging_flags + flags
        staging_dict = stage_result.to_dict() if stage_result else None
        routing_dict = route_result.__dict__ if route_result else {}

        if stage_result is None or route_result is None:
            return AgentResult(
                case_id, staging_dict, routing_dict, None, None, None,
                flags=flags, error="Could not resolve stage/routing for case",
                imaging=imaging_dict,
            )

        # -- pre-inference safety gates -------------------------------------
        pre_gates = evaluate_gates(case, stage_result, phase="pre")
        flags.extend(g.as_flag() for g in pre_gates)
        blocking = [g for g in pre_gates if g.severity == "block"]
        if blocking:
            return AgentResult(
                case_id, staging_dict, routing_dict, None, None, None,
                flags=flags, imaging=imaging_dict,
                error="Blocked by clinical safety gate: "
                      + "; ".join(g.code for g in blocking),
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
                case_id, staging_dict, routing_dict, None, None, None,
                flags=flags,
                error=(f"No protocol module available for stage "
                       f"{stage_result.stage_group}"),
                imaging=imaging_dict,
            )

        module = load_module(module_key)
        messages = self.build_messages(
            case, module, stage_result, imaging_findings=imaging_findings,
            provisional=provisional,
        )

        if dry_run:
            return AgentResult(
                case_id, staging_dict, routing_dict, module_key, None,
                LLMResponse(
                    content="[dry-run: prompt assembled, model not called]",
                    provider="(none)", model="(none)",
                ),
                flags=flags + ["DRY_RUN"],
                imaging=imaging_dict,
            )

        try:
            prov = self.get_provider(provider)
            response = prov.complete(messages, params=params)
        except Exception as exc:  # provider config/transport errors → result.error
            prov_name = provider or self.config.default_provider
            return AgentResult(
                case_id, staging_dict, routing_dict, module_key,
                prov_name, None, flags=flags, error=str(exc),
                imaging=imaging_dict,
            )

        # -- post-inference validation + safety gates -----------------------
        validation = validate_output(response.content)
        flags.extend(validation.flags)
        post_gates = evaluate_gates(
            case, stage_result, output_text=response.content, phase="post")
        flags.extend(g.as_flag() for g in post_gates)

        return AgentResult(
            case_id, staging_dict, routing_dict, module_key, prov.name,
            response, flags=flags, imaging=imaging_dict,
        )
