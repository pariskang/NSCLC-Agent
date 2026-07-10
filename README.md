# NSCLC-Agent

An **evidence-based, stage-aware decision-support agent for non-small cell lung
cancer (NSCLC)**, built for **teaching, training-data generation, and model
testing**. It pairs a **deterministic AJCC/UICC 9th-edition staging engine**
with the stage-specific clinical protocol modules (v3.3, 2026-06) and a
**pluggable LLM backend** that runs on **LiteLLM, Azure OpenAI, Poe, or
MiniMax** (plus an offline mock). An optional **perception layer reads radiology
films (读片)** through a vision model (e.g. **Gemini via the Poe API**) — it
*proposes* radiographic TNM descriptors that flow into the deterministic engine,
and never assigns the stage itself.

> ⚠️ **Educational / research use only.** This is not a medical device. Output
> must never be used for real patient care without review by a qualified
> multidisciplinary oncology team.

---

## Why it is built this way

Staging is the highest-stakes step in the pipeline and the one most prone to
hallucination — a single mis-stage cascades into the wrong treatment protocol.
So the design **removes the model from that decision**:

```
          ┌──────────────────────────────────────────────────────────┐
 films ─▶ │ 0. Perception (optional) ── vision model (Gemini/Poe)     │
 (读片)   │    reads films ──▶ PROPOSES cT/cN/cM  (never the stage)   │
          │    cross-checks or seeds the descriptors, flags mismatch  │
          └───────────────────────────┬──────────────────────────────┘
                                       ▼
          ┌──────────────────────────────────────────────────────────┐
   case ─▶ │ 1. Deterministic TNM-9 staging engine (pure Python)      │
          │    (T,N,M) ───▶ stage group   — verifiable, unit-tested   │
          └───────────────────────────┬──────────────────────────────┘
                                       ▼
          ┌──────────────────────────────────────────────────────────┐
          │ 2. Stage router  ── stage group ──▶ protocol module       │
          │    I · II · IIIA · IIIB · IIIC · IVA · IVB                │
          └───────────────────────────┬──────────────────────────────┘
                                       ▼
          ┌──────────────────────────────────────────────────────────┐
          │ 3. Prompt assembly: module system prompt                  │
          │    + injected, authoritative stage (model does not        │
          │      re-derive it)  +  case + proposed findings (labeled) │
          └───────────────────────────┬──────────────────────────────┘
                                       ▼
          ┌──────────────────────────────────────────────────────────┐
          │ 4. Provider layer ── LiteLLM / Azure / Poe / MiniMax /    │
          │    mock ──▶ structured, auditable AgentResult             │
          └──────────────────────────────────────────────────────────┘
```

The staging engine computes the stage symbolically and **injects it into the
system prompt as authoritative**, so the model reasons *within* a verified
stage rather than guessing it. Every run returns full provenance (staging,
migrations, routing, flags) for auditing — the property that makes generated
teaching/RLHF data trustworthy.

This is a concrete, runnable realization of the "stage NSCLC as a verifiable
symbolic step, then reason with evidence" idea: the deterministic TNM-9 engine
is the *verifiable definitive-staging module*, the router + protocol modules
are the *guideline-consistent reasoning layer*, and the provider abstraction is
the *swappable inference backend* for teaching and evaluation.

---

## What's included

| Component | Location | Notes |
|---|---|---|
| TNM-9 staging engine | `nsclc_agent/staging/tnm.py` | 9th edition incl. N2a/N2b, M1c1/M1c2, all migrations |
| Stage router | `nsclc_agent/staging/router.py` | maps stage group → protocol module |
| Protocol modules | `nsclc_agent/prompts/*.md` | Stage I, II, IIIA, IIIB, IIIC, IVA, IVB (v3.3) |
| Perception layer | `nsclc_agent/imaging.py` | reads films → proposed cTNM (vision, e.g. Gemini/Poe) |
| Provider layer | `nsclc_agent/providers/` | LiteLLM · Azure · Poe · MiniMax · mock (text + vision) |
| Agent orchestrator | `nsclc_agent/agent.py` | (read films →) case → stage → route → prompt → LLM |
| CLI | `nsclc_agent/cli.py` | `stage`, `route`, `read`, `run`, `batch`, `selftest`, … |
| Example cases | `examples/cases/*.json` | one per stage band + an imaging cross-check case |
| Tests | `tests/` | 106 tests, offline |

**Stage coverage.** The engine stages *all* groups (0/I through IVB), and a
dedicated protocol module ships for **every treatment-bearing stage**: Stage I
(incl. Tis/AIS-MIA), II, IIIA, IIIB, IIIC, IVA and IVB. The only unrouted state
is **occult carcinoma** (TX N0 M0), where the primary is not localized — the
router flags it and asks for the localization workup rather than guessing. Drop
new modules into `prompts/` to extend or specialize further.

---

## Quickstart (zero dependencies, offline)

The core runs on the Python standard library alone. No key, no network:

```bash
# 1. Deterministically stage a TNM triple
python -m nsclc_agent stage T2b N2b M0
#   TNM T2bN2bM0  →  Stage IIIB (AJCC/UICC 9th edition)
#     • migration: T2N2b upstaged from 8th-edition IIIA to 9th-edition IIIB.
#     → module: stage3b

# 2. Verify the staging engine against the built-in expectation table
python -m nsclc_agent selftest          # 34/34 passed

# 3. Run a full case through the offline mock backend
python -m nsclc_agent run --t T2b --n N2b --m M0 \
    --presentation "68F adenocarcinoma, contralateral mediastinal nodes, EGFR-, PD-L1 40%." \
    --question "Recommended treatment pathway?"

# 4. Run a JSON case file
python -m nsclc_agent run --case examples/cases/stage4a_oligometastatic.json

# 5. Batch a folder of cases, writing per-case result JSON
python -m nsclc_agent batch examples/cases -o out/
```

Install as a package (adds the `nsclc-agent` console script):

```bash
pip install -e .            # core only
pip install -e ".[all]"     # + pyyaml (YAML config) + litellm backend
```

---

## Connecting a real backend

Copy `config.example.yaml` → `config.yaml` and pick a `default_provider` (or
override per run with `-p/--provider`). Secrets are **referenced by environment
variable name** (`*_env`), never written into the config file.

```bash
python -m nsclc_agent providers -c config.yaml     # list configured backends
python -m nsclc_agent run -c config.yaml -p poe --case examples/cases/stage3b_unresectable_egfr.json
```

### LiteLLM  (`pip install litellm`)
One backend that routes to 100+ providers by changing the `model` string.
```yaml
litellm:
  type: litellm
  model: gpt-4o            # or azure/<deployment>, anthropic/claude-..., etc.
  api_key_env: OPENAI_API_KEY
  # api_base: https://your-litellm-proxy/v1   # optional
```

### Azure OpenAI  (direct, no extra deps)
Deployment lives in the URL; uses the `api-key` header + `api-version`.
```yaml
azure:
  type: azure
  endpoint: https://YOUR-RESOURCE.openai.azure.com
  deployment: gpt-4o
  api_version: "2024-10-21"
  api_key_env: AZURE_OPENAI_API_KEY
```

### Poe  (direct, no extra deps)
OpenAI-compatible at `https://api.poe.com/v1`; `model` is the Poe bot name.
```yaml
poe:
  type: poe
  model: GPT-4o           # e.g. Claude-Sonnet-4, Gemini-2.5-Pro, ...
  api_key_env: POE_API_KEY

# A vision backend for reading films — Gemini via Poe. `vision: true` marks it
# multimodal so it can be auto-selected as the film reader.
gemini_vision:
  type: poe
  model: Gemini-3.1-Pro   # the Gemini bot name your Poe account exposes
  vision: true
  api_key_env: POE_API_KEY
```

### MiniMax  (direct, no extra deps)
OpenAI-shaped `/text/chatcompletion_v2`. Use `api.minimaxi.com` (CN) or
`api.minimax.io` (international).
```yaml
minimax:
  type: minimax
  model: MiniMax-Text-01
  base_url: https://api.minimaxi.com/v1
  api_key_env: MINIMAX_API_KEY
  group_id_env: MINIMAX_GROUP_ID    # optional, tenant-dependent
```

> Behind a proxy with a custom CA (common in managed environments), set
> `SSL_CERT_FILE` or `REQUESTS_CA_BUNDLE` — the stdlib HTTP client honors them.

---

## AJCC/UICC 9th-edition staging (the verifiable core)

Effective 1 Jan 2025. T and M1a/M1b are unchanged from the 8th edition, but
**N2 splits into N2a (single-station) / N2b (multi-station)** and **M1c splits
into M1c1 (multiple mets, single organ system) / M1c2 (multiple mets, multiple
organ systems)**, driving real stage migration. Key migrations the engine
surfaces automatically:

| TNM | 8th ed. | 9th ed. |
|---|---|---|
| T1 N1 | IIB | **IIA** (down) |
| T1 N2a | IIIA | **IIB** (down — still N2 disease) |
| T3 N2a | IIIB | **IIIA** (down) |
| T2 N2b | IIIA | **IIIB** (up) |

The engine **rejects ambiguous input** (`N2`, `M1c`, `T2` without a sub-letter)
rather than guessing, because those distinctions change the stage. The full
expectation table lives in `nsclc_agent/staging/selftest.py` and is the source
of truth for both `selftest` and the pytest suite.

---

## Reading films (读片) — the perception layer

Real staging starts from imaging. A vision-capable backend (Gemini via Poe by
default) can read CT / PET-CT / MRI slices and **propose** candidate
radiographic descriptors — but the contract is strict:

> The vision model **proposes** descriptors; it **never** assigns the stage
> group. The deterministic engine still does that.

```bash
# Read films into proposed descriptors (no staging)
python -m nsclc_agent read -c config.yaml -p gemini_vision \
    --images scan1.png scan2.png --context "68F, LUL mass, staging PET-CT"

# Attach films to a full run — the reader seeds/cross-checks TNM, the engine stages
python -m nsclc_agent run -c config.yaml --images scan1.png scan2.png \
    --question "Recommended pathway?"
```

What the perception layer does with the proposal:

| Situation | Behavior | Flag |
|---|---|---|
| Case already has T/N/M (path/human) | proposal is a **cross-check** only; case value stays authoritative | `IMAGING_DISCORDANCE[…]` on mismatch |
| Case missing a descriptor | descriptor is **seeded** from the proposal, then staged | `RADIOGRAPHIC_TNM_PROPOSED` |
| Descriptor unresolved on film | names the test that would resolve it (EBUS for N, PET-CT+brain MRI for M) | `NEXT_STEP_SUGGESTED` |
| Vision backend errors | run continues on whatever TNM the case has | `IMAGING_READ_FAILED` |

Findings are recorded under `result.imaging` as `MODEL_PROPOSED_UNVERIFIED` and
passed to the reasoning model as *labeled, unverified context* — the reasoning
backend need not be multimodal. Images are base64-encoded with the standard
library; **DICOM is not parsed** — export slices to PNG/JPEG (or pass an
`https` URL). Only use **de-identified** images. Behind the offline mock (which
cannot read images) the read returns empty on purpose, so the wiring runs
everywhere while real reading needs a configured vision backend.

---

## Programmatic use

```python
from nsclc_agent import NSCLCAgent, Case, load_config

agent = NSCLCAgent(load_config("config.yaml"))
case = Case(t="T4", n="N3", m="M0",
            presentation="66M squamous, contralateral + supraclavicular nodes, "
                         "encompassable, ECOG 1, EGFR-, PD-L1 15%.",
            question="Definitive management and consolidation?")
result = agent.run(case, provider="poe")

print(result.staging["stage_group"])   # 'IIIC'
print(result.module_key)               # 'stage3c'
print(result.response.content)         # model's structured decision-support JSON
```

Deterministic staging on its own:

```python
from nsclc_agent import stage_from_strings
r = stage_from_strings("T2b", "N2b", "M0")
print(r.stage_group, r.migration_notes)   # IIIB  ['T2N2b upstaged ...']
```

Reading films, then running with the proposal folded in:

```python
from nsclc_agent import NSCLCAgent, Case, load_config

agent = NSCLCAgent(load_config("config.yaml"))       # vision_provider set in config
case = Case(images=["pet_ct_slice.png"],
            presentation="64M, LUL mass, staging PET-CT + brain MRI",
            question="Management pathway?")
result = agent.run(case)                              # reads films → stages → routes
print(result.imaging["candidate_n"])                 # model-proposed cN (UNVERIFIED)
print(result.staging["stage_group"])                 # engine-computed stage
print([f for f in result.flags if "IMAGING" in f or "NEXT_STEP" in f])
```

---

## CLI reference

| Command | Purpose |
|---|---|
| `stage T N [M]` | Deterministically stage a TNM triple (`--json` for machine output) |
| `route STAGE` | Show the module a stage group maps to |
| `modules` | List protocol modules and coverage |
| `providers [-c cfg]` | List configured backends (marks `[vision]` ones) |
| `read --images … [-p provider]` | Read films into proposed TNM descriptors (no staging) |
| `run [--case f.json \| --t --n --m …] [--images …] [-p provider] [--dry-run]` | Run one case |
| `batch DIR [-o OUT]` | Run every `*.json` case in a directory |
| `selftest` | Validate the staging engine |

`--dry-run` assembles the full prompt and prints routing without calling any
model — useful for inspecting exactly what a backend would receive (it also
skips film reading, since that is a model call). Attach films to `run` with
`--images`; choose the reader with `--vision-provider` or disable it with
`--no-read-films`.

---

## Testing

```bash
pip install pytest
python -m pytest -q        # 106 tests, fully offline
```

See [`docs/DESIGN.md`](docs/DESIGN.md) for the architecture, the mapping to the
active-perception / verifiable-staging design goals, and extension points.

## Safety & scope

- Educational / research only; not a medical device; no patient data included.
- The protocol modules enforce their own safety rules (trial-boundary
  discipline, driver exclusions, no-surgery-for-N3, biomarker-first, etc.).
- The agent never overrides the deterministic stage and flags every ambiguity
  (`STAGE_MISMATCH`, `MODULE_UNAVAILABLE`, `STAGING_ERROR`, …) instead of
  silently proceeding.
- Film reading is **advisory**: descriptors are labeled
  `MODEL_PROPOSED_UNVERIFIED`, cross-checked against human/pathologic TNM
  (`IMAGING_DISCORDANCE`), and never used to assign the stage directly. Use
  only de-identified images.

## License

MIT — see [`LICENSE`](LICENSE).
