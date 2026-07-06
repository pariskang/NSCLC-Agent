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

## 2. How it maps to an active-perception / POMDP framing

The broader research vision frames NSCLC staging as *active information
gathering under partial observability*: the true stage is a hidden state,
each investigation is a costly/noisy observation, and the agent decides what to
check next before committing. This repository implements the parts of that
vision that are concrete and verifiable today, and leaves clean seams for the
rest:

| POMDP concept | Realized here as | Extension seam |
|---|---|---|
| Hidden state `S` (true TNM) | `TNM` descriptors | — |
| Verifiable definitive staging | `staging/tnm.py` (symbolic engine) | stable |
| Guideline-consistent policy | protocol modules + router | add modules |
| Observation model `P(o\|s,a)` | (case fields provided as evidence) | per-test sensitivity/specificity metadata |
| Belief `b(s)` over stages | single computed stage today | replace point stage with a distribution |
| Action selection / VOI | not yet (staging assumed complete) | a "what to check next" planner over `data_quality_flags` |
| Uncertainty gate / escalation | `flags` + `MODULE_UNAVAILABLE` / MDT triggers in modules | calibrated UQ gate before `commit` |
| Audit trail | `AgentResult.to_dict()` provenance | hash-chained event log |

The important property is that the **staging engine is already the
"deterministic, verifiable definitive-staging module"** the vision calls for —
the highest-hallucination-risk step is handled symbolically, and everything
else is layered on top without contaminating it.

## 3. Module boundaries

```
nsclc_agent/
  staging/        pure, deterministic, no I/O, no LLM  ← verifiable core
    tnm.py          TNM normalization + 9th-ed. stage table
    router.py       stage group → module key
    selftest.py     authoritative expectation table (source of truth)
  prompts/        protocol modules (Markdown) + loader
  providers/      backend abstraction
    base.py         LLMProvider ABC, Message, LLMResponse, GenerationParams
    openai_compatible.py   stdlib HTTP for OpenAI-shaped APIs
    poe.py / minimax.py / azure.py    thin subclasses
    litellm_provider.py    optional SDK backend
    mock.py         offline deterministic stub
    registry.py     config dict → provider instance
  case.py         case input model + user-turn rendering
  config.py       YAML/JSON config loading (+ built-in mock default)
  agent.py        orchestration: resolve_stage → route → build_messages → complete
  cli.py          argparse front-end
```

Dependencies flow strictly downward: `staging` depends on nothing; `providers`
depends on nothing in the clinical layer; `agent` composes them. This is what
lets the staging engine be trusted and unit-tested in isolation, and lets
backends be added without touching clinical logic.

## 4. Why the staging engine rejects ambiguity

`N2`, `M1c`, and bare `T2` are **refused, not guessed**. In the 9th edition the
sub-distinction changes the stage group (`T2aN2a` = IIIA but `T2aN2b` = IIIB;
`M1c1`/`M1c2` are both IVB but carry different prognosis and were split for a
reason). Silently collapsing them would defeat the point of a *verifiable*
engine, so the engine raises `StagingError` with an actionable message and the
agent turns it into a `STAGING_ERROR` flag rather than proceeding.

## 5. Prompt assembly

`agent.build_messages()` produces two turns:

- **system** = the full protocol module (unmodified) **+** a
  `DETERMINISTIC STAGING` preamble stating the computed stage group, edition,
  migrations and the instruction *"treat this as authoritative; do not
  re-derive"*.
- **user** = the case: free-text presentation + any structured `fields` +
  the explicit question.

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

Adding a backend = subclass `LLMProvider` (or `OpenAICompatibleProvider`) and
register a branch in `providers/registry.py`.

## 7. Extending stage coverage

To add, e.g., a Stage I or Stage IIIA module:

1. Drop `prompts/stage1.md` (the system-prompt protocol) into `prompts/`.
2. Register it in `prompts/__init__.py::MODULES`.
3. Point the relevant stage groups at it in
   `staging/router.py::_STAGE_TO_MODULE` (e.g. `"IA1": "stage1"`).
4. Add a couple of example cases and a routing test.

No changes to the staging engine or provider layer are needed — that separation
is the whole point.

## 8. Testing strategy

- `test_staging.py` — the 9th-edition table, all migrations, ambiguity
  rejection, normalization. `selftest.py::EXPECTATIONS` is the single source of
  truth shared by the CLI `selftest` and pytest.
- `test_router.py` — routing availability and prompt loading.
- `test_providers.py` — factory, URL/header construction for each backend,
  env-var secret resolution, response parsing (no network).
- `test_agent.py` — end-to-end through the mock, including stage/label mismatch,
  IIIA fallback, and every shipped example case.

All 82 tests run fully offline.
