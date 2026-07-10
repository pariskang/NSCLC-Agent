# Design & Architecture

This document explains how the NSCLC-Agent is structured, why the boundaries
sit where they do, and how to extend it.

## 1. Design goals

1. **Verifiable staging, not guessed staging.** The stage group is the pivot of
   the whole pipeline. Compute it symbolically and treat it as authoritative;
   the LLM must never re-derive or override it.
2. **Stage-specialized reasoning.** Each stage band has genuinely different
   decision logic (resectability gate, consolidation-agent-by-driver,
   oligometastatic LCT, performance-status gate …). Route to the module that
   encodes that logic rather than one monolithic prompt.
3. **Backend-agnostic.** Teaching and testing require swapping models freely,
   so clinical logic is fully decoupled from the inference backend.
4. **Runs anywhere, offline-first.** Core depends only on the standard library;
   an offline mock backend keeps the whole pipeline exercisable with no keys or
   network (essential for CI and classroom use).
5. **Auditable.** Every run emits staging provenance, migration notes, routing
   decisions and flags — the record that makes generated data trustworthy.

## 2. Design inspiration: active perception / POMDP (a framing, not a claim)

An active-perception / POMDP picture *inspired* the architecture — the true
stage as a hidden state, each investigation a costly/noisy observation, the
agent choosing what to check next before committing. This is a **design
metaphor to organize the code**, not a claim that a POMDP is solved here. The
table below is honest about which cells are actually implemented and which are
only clean seams:

| POMDP concept | Implemented? | Where / gap |
|---|---|---|
| Hidden state `S` (true TNM) | n/a | represented by `TNM` descriptors |
| Verifiable definitive staging | **yes** | `staging/tnm.py` symbolic engine |
| Guideline-consistent policy | partial | protocol modules + router (prompt-level, not machine-verified guidelines) |
| Observation model `P(o\|s,a)` | partial | `imaging.py` proposes descriptors; **no** calibrated sensitivity/specificity |
| Belief `b(s)` over stages | **no** | single point stage; a distribution is a roadmap seam |
| Action selection / VOI | minimal | `NEXT_STEP_SUGGESTED` is a fixed rule keyed to the missing descriptor — not a VOI planner |
| Uncertainty gate / escalation | partial | code gates (`safety.py`) + flags; no calibrated UQ |
| Audit trail | partial | `AgentResult.to_dict()` provenance; **not** a hash-chained, tamper-evident log |

The one property that is genuinely solid is that the **staging engine is a
deterministic, verifiable definitive-staging module** — the
highest-hallucination-risk step is symbolic, and everything else is layered on
top without contaminating it. Everything else is a prototype-grade
approximation; see §9 (safety gates) and §11 (what this is not).

## 2a. The perception layer (reading films)

Real staging starts from images, not from a T/N/M someone typed in. The
perception layer closes that gap while *preserving* the verifiable-staging
property. Its one inviolable rule:

> The vision model **proposes** radiographic descriptors. It **does not** assign
> the stage group. The deterministic engine still does that.

`imaging.py` sends the films to a vision-capable backend (Gemini via the Poe
API by default) under an extraction prompt that (a) forbids naming a stage
group, (b) forces the exact 9th-edition vocabulary, and (c) requires `null` +
an `uncertainties` note for anything indeterminate (e.g. single- vs
multi-station N2). The returned `ImagingFindings` are always stamped
`MODEL_PROPOSED_UNVERIFIED` and folded into the case by `agent._ingest_imaging`:

- **Case already has T/N/M** (human/pathologic) → the proposal is only a
  *cross-check*; a mismatch raises `IMAGING_DISCORDANCE[…]` and the case value
  is kept for staging.
- **Case is missing a descriptor** → it is *seeded* from the proposal and
  flagged `RADIOGRAPHIC_TNM_PROPOSED` (provisional cTNM), then the engine stages
  it exactly as any other input.
- **Descriptor still unresolved** → `NEXT_STEP_SUGGESTED` names the test that
  would resolve it (EBUS for N, PET-CT + brain MRI for M …) — the first,
  rule-based slice of value-of-information planning.

The reasoning model receives the findings as *labeled, unverified context* in
the user turn — never as the images themselves — so the reasoning backend need
not be multimodal, and perception stays cleanly separable from reasoning. A
failed read degrades gracefully (`IMAGING_READ_FAILED`) instead of aborting the
run. DICOM is out of scope for the stdlib core: export slices to PNG/JPEG (or
pass an `https` URL) first.

## 3. Module boundaries

```
nsclc_agent/
  staging/        pure, deterministic, no I/O, no LLM  ← verifiable core
    tnm.py          TNM normalization + 9th-ed. stage table
    router.py       stage group → module key
    selftest.py     authoritative expectation table (source of truth)
  prompts/        protocol modules (Markdown) + loader
  providers/      backend abstraction
    base.py         LLMProvider ABC, Message (+ multimodal), LLMResponse, params
    openai_compatible.py   stdlib HTTP for OpenAI-shaped APIs (text + vision)
    poe.py / minimax.py / azure.py    thin subclasses
    litellm_provider.py    optional SDK backend
    mock.py         offline deterministic stub (+ mock vision read)
    registry.py     config dict → provider instance
  imaging.py      perception layer: films → proposed descriptors (vision)
  safety.py       machine-enforced clinical gates (pre/post-inference)
  validation.py   output JSON/structure check + fabricated-evidence detection
  case.py         case input model (+ images, metastatic_state) + rendering
  config.py       YAML/JSON config loading (+ built-in mock default)
  agent.py        orchestration: (films →) stage → gates → route → LLM → validate
  cli.py          argparse front-end
```

Dependencies flow strictly downward: `staging` depends on nothing; `providers`
depends on nothing in the clinical layer; `agent` composes them. This is what
lets the staging engine be trusted and unit-tested in isolation, and lets
backends be added without touching clinical logic.

## 4. Why the staging engine rejects ambiguity and never defaults

`N2`, `M1c`, and bare `T2` are **refused, not guessed**. In the 9th edition the
sub-distinction changes the stage group (`T2aN2a` = IIIA but `T2aN2b` = IIIB;
`M1c1`/`M1c2` are both IVB but carry different prognosis and were split for a
reason). Silently collapsing them would defeat the point of a *verifiable*
engine, so the engine raises `StagingError` and the agent turns it into a
`STAGING_ERROR` flag rather than proceeding.

The same principle governs **missing** descriptors, which is where the most
dangerous silent failure lived:

- **Unknown M is not M0.** `M0` is a conclusion reached after a metastatic
  workup, not a default for an empty field. An empty/absent M normalizes to
  `MX`, and `MX` does **not** produce a curative stage group. At the agent
  level a case with T and N but no M returns `STAGING_INCOMPLETE_M_UNKNOWN` and
  never enters a treatment module. (The `stage` CLI *calculator* still assumes
  M0 for a bare triple, but prints a loud warning that it did.)
- **Edition is not assumed.** Only the 9th edition is implemented; a case that
  declares another edition is refused (`STAGING_EDITION_UNSUPPORTED`) rather
  than silently staged as 9th. A stage supplied as a bare label (no TNM) is
  marked `edition: "unverified (from provided label)"`.
- **Original descriptors are preserved.** `T1mi` is staged in the T1a family
  but the `T1mi` input is retained in `original_descriptors`; a c/p/yc/yp/r
  basis prefix is recorded as `staging_basis`, and a mixed basis is flagged.

## 5. Prompt assembly

`agent.build_messages()` produces two turns:

- **system** = the full protocol module (unmodified) **+** a staging preamble.
  When the stage was computed from confirmed descriptors the preamble is
  `DETERMINISTIC STAGING … treat this as authoritative`. When at least one
  descriptor came from an **unverified film reading**, the preamble instead
  reads `PROVISIONAL RADIOGRAPHIC STAGING … do NOT present it as definitive` —
  the authoritative-staging instruction is *not* emitted, resolving the former
  contradiction where an unverified input was declared authoritative.
- **user** = the case: free-text presentation + metastatic phenotype (a
  separate axis from the stage) + structured `fields` + the explicit question,
  plus the labeled `RADIOGRAPHIC FINDINGS` block when films were read.

Injecting the verified stage into the system prompt is the mechanism that keeps
the model reasoning inside a correct stage without letting it wander into a
staging decision it is bad at.

## 6. Provider layer

All backends implement `LLMProvider.complete(messages) -> LLMResponse`. Poe,
MiniMax and Azure are OpenAI-shaped and share `OpenAICompatibleProvider`, which
does request/parse over the Python standard library (`urllib`), honoring
`SSL_CERT_FILE`/`REQUESTS_CA_BUNDLE` and system proxies. LiteLLM is an optional
SDK-backed provider that unifies 100+ providers via the `model` string. The
`mock` provider returns a deterministic JSON stub so the pipeline runs offline.

**Vision** rides the same surface: a `Message` may carry `images`, and
`to_openai()` emits the multimodal *content-parts* array that OpenAI-compatible
vision endpoints expect — so no vision-specific transport is needed. A provider
declares itself multimodal with `vision: true` in config (`supports_vision`),
which lets the agent auto-select the film reader. The mock recognises the
imaging-extraction prompt and returns an empty, honest findings stub so the
whole perception path is testable offline.

Adding a backend = subclass `LLMProvider` (or `OpenAICompatibleProvider`) and
register a branch in `providers/registry.py`.

## 7. Extending stage coverage

Every treatment-bearing stage group (I → IVB) ships with a module today; only
occult carcinoma (TX N0) is intentionally unrouted. Adding or specializing a
module (e.g. a dedicated Stage 0 / AIS module, or splitting resectable vs
unresectable IIIA) is a four-step, localized change:

1. Drop `prompts/<key>.md` (the system-prompt protocol) into `prompts/`.
2. Register it in `prompts/__init__.py::MODULES` with its stage groups.
3. Point the relevant stage groups at it in
   `staging/router.py::_STAGE_TO_MODULE` (e.g. `"IIIA": "stage3a"`).
4. Add an example case and a routing test.

No changes to the staging engine or provider layer are needed — that separation
is the whole point. (Stages I and IIIA were added exactly this way, touching
only the prompt loader, router, tests and examples.)

## 9. Machine-enforced safety gates

A rule written in a system prompt is a *request*, not a control. `safety.py`
re-encodes the highest-consequence rules so they run **outside** the model and
cannot be argued away:

- **pre-inference** (`pre_inference_gates`) can BLOCK before any model call —
  e.g. a case explicitly marked not-a-confirmed-NSCLC
  (`GATE_BLOCK[PATHOLOGY_UNCONFIRMED]`) never reaches a treatment module; N2
  disease that is imaging-only draws `GATE_WARN[N2_UNCONFIRMED]`.
- **post-inference** (`post_inference_gates`) inspect the model's *output*:
  driver-positive (EGFR/ALK/…) + an immunotherapy recommendation →
  `GATE_HARD[DRIVER_IO_CONFLICT]`; N3/IIIC + a surgical term →
  `GATE_HARD[N3_SURGERY]`.

These are deliberately conservative keyword/field **detectors**, not a
substitute for the MDT — they surface prominently (`GATE_BLOCK/HARD/WARN`) so a
reviewer sees them; they never silently rewrite a recommendation.

`validation.py` then parses the reply: non-JSON output is flagged
(`OUTPUT_NOT_JSON`), and — importantly — `tool_call` / `sources` / PMID-style
fields are flagged `EVIDENCE_UNVERIFIED`, because **this toolkit performs no
real retrieval**. The protocol modules *ask* the model to "search PubMed / FDA /
guidelines", but there is no such tool wired up; any retrieved-looking evidence
is model-generated. Flagging it is safer than letting it wear the appearance of
having been checked.

## 10. Testing strategy

- `test_staging.py` — the 9th-edition table, all migrations, ambiguity
  rejection, the no-default guards (MX/edition/T0), T1mi provenance, basis
  prefixes. `selftest.py::EXPECTATIONS` is the shared source of truth.
- `test_router.py` — routing availability and prompt loading.
- `test_providers.py` — factory, URL/header construction, secret resolution,
  response parsing (no network).
- `test_agent.py` — end-to-end through the mock, incl. stage/label mismatch,
  the M-unknown block, edition refusal, and every shipped example case.
- `test_imaging.py` — the perception layer: loading, JSON extraction, the
  propose→verify contract, the confidence gate, provisional staging, next-step
  hint, graceful failure — via a fake in-process vision provider.
- `test_safety.py` — driver extraction and the pre/post gates (block, hard,
  warn) both directly and end-to-end.
- `test_validation.py` — JSON recovery, non-JSON flagging, fabricated-evidence
  detection.

All 134 tests run fully offline. **They validate the code against the project's
own expectations — that is not clinical validation** (see §11).

## 11. What this is NOT

Being explicit so the prototype is not mistaken for a product:

- **No real evidence retrieval / RAG.** No PubMed/FDA/NMPA/guideline/trial
  lookup exists; citations in output are unverified and flagged.
- **Not a clinical imaging system.** PNG/JPEG (or `https`) into a general vision
  model; no DICOM, pixel spacing, SUV, registration or prior-comparison.
- **One edition only** (9th); 8th is refused, not approximated.
- **Prompt-level guidelines**, not a versioned, machine-checkable guideline/drug-
  label knowledge base; no dose validation.
- **No clinical governance**: no PHI de-identification, consent/data-residency
  controls, access control, or tamper-evident audit chain.
- **No independent validation**: no external gold-standard, multi-reader,
  subgroup-fairness, or prospective testing. Not for patient care.

These are roadmap seams, called out honestly rather than implied to exist.
