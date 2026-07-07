"""Command-line interface for the NSCLC agent.

Subcommands:
  stage      Deterministically stage a TNM triple (no LLM).
  route      Show which protocol module a stage/case maps to.
  modules    List available protocol modules.
  providers  List configured providers.
  read       Read radiology films into proposed TNM descriptors (vision).
  run        (optional read films →) stage → route → prompt → LLM for one case.
  batch      Run every case file in a directory.
  selftest   Run the built-in staging self-test.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from . import __version__
from .agent import NSCLCAgent
from .case import Case
from .config import ConfigError, load_config
from .prompts import list_modules, load_module
from .providers.base import GenerationParams
from .staging import StagingError, available_modules, route, stage_from_strings
from .staging.selftest import run_selftest


def _load_config_or_exit(path: Optional[str]):
    try:
        return load_config(path)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        sys.exit(2)


def _read_case(path: str) -> Case:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return Case.from_dict(data)


def cmd_stage(args) -> int:
    try:
        result = stage_from_strings(args.t, args.n, args.m)
    except StagingError as exc:
        print(f"Staging error: {exc}", file=sys.stderr)
        return 1
    r = route(result.stage_group)
    out = result.to_dict()
    out["module"] = {"key": r.module_key, "available": r.available,
                     "note": r.note}
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"TNM {result.tnm}  →  Stage {result.stage_group} "
              f"({result.edition})")
        for note in result.migration_notes:
            print(f"  • migration: {note}")
        for note in result.descriptor_notes:
            print(f"  • note: {note}")
        if r.available:
            print(f"  → module: {r.module_key}")
        else:
            print(f"  → no dedicated module ({r.note})")
    return 0


def cmd_route(args) -> int:
    r = route(args.stage_group)
    print(json.dumps({
        "stage_group": r.stage_group,
        "module_key": r.module_key,
        "available": r.available,
        "fallback_module_key": r.fallback_module_key,
        "note": r.note,
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_modules(args) -> int:
    for m in list_modules():
        groups = ", ".join(m.stage_groups)
        print(f"{m.key:10s}  {m.label}")
        print(f"{'':10s}  stages: {groups}  |  {len(m.system_prompt):,} chars"
              f"  |  {m.path.name}")
    print(f"\nAvailable module keys: {', '.join(available_modules())}")
    return 0


def cmd_providers(args) -> int:
    cfg = _load_config_or_exit(args.config)
    print(f"Config source: {cfg.source}")
    print(f"Default provider: {cfg.default_provider}")
    print(f"Vision provider: {cfg.vision_provider or '(none configured)'}")
    print(f"Generation: temperature={cfg.generation.temperature}, "
          f"max_tokens={cfg.generation.max_tokens}\n")
    for name, pcfg in cfg.providers.items():
        marker = "*" if name == cfg.default_provider else " "
        kind = pcfg.get("type", "?")
        model = pcfg.get("model") or pcfg.get("deployment") or ""
        vis = " [vision]" if pcfg.get("vision") else ""
        print(f" {marker} {name:12s} type={kind:10s} model={model}{vis}")
    return 0


def _print_result(result, as_json: bool, show_prompt: bool = False) -> None:
    if as_json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return
    print(f"Case: {result.case_id or '(unnamed)'}")
    if result.imaging:
        img = result.imaging
        print(f"  Imaging (proposed, UNVERIFIED): "
              f"cT={img.get('candidate_t')} cN={img.get('candidate_n')} "
              f"cM={img.get('candidate_m')} "
              f"[{img.get('read_by', {}).get('model')}]")
    if result.staging:
        print(f"  Stage: {result.staging['stage_group']} "
              f"({result.staging['edition']})")
        for note in result.staging.get("migration_notes", []):
            print(f"    • migration: {note}")
    print(f"  Module: {result.module_key or '(none)'}")
    print(f"  Provider: {result.provider or '(none)'}")
    for flag in result.flags:
        print(f"  ⚑ {flag}")
    if result.error:
        print(f"  ERROR: {result.error}")
    if result.response:
        print("  --- response ---")
        print(result.response.content)


def cmd_read(args) -> int:
    """Read radiology films into model-proposed descriptors (perception layer)."""
    cfg = _load_config_or_exit(args.config)
    agent = NSCLCAgent(cfg, vision_provider=args.provider)
    case = Case(images=list(args.images), presentation=args.context or "")
    try:
        findings = agent.read_imaging(case, provider=args.provider)
    except Exception as exc:  # ImagingError / ProviderError / etc.
        print(f"Imaging read failed: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(findings.to_dict(), ensure_ascii=False, indent=2))
    else:
        print("Radiographic findings (MODEL-PROPOSED, UNVERIFIED):")
        print(f"  modality:  {findings.modality}")
        print(f"  cT:        {findings.candidate_t}")
        print(f"  cN:        {findings.candidate_n}  "
              f"stations={findings.nodal_stations}")
        print(f"  cM:        {findings.candidate_m}  "
              f"sites={findings.metastatic_sites}")
        print(f"  effusion:  {findings.malignant_effusion}")
        print(f"  confidence:{findings.confidence}")
        for u in findings.uncertainties:
            print(f"  ⚑ uncertainty: {u}")
        print(f"  read by:   {findings.provider} / {findings.model} "
              f"({findings.n_images} image(s))")
    return 0


def cmd_run(args) -> int:
    cfg = _load_config_or_exit(args.config)
    agent = NSCLCAgent(cfg)
    if args.case:
        case = _read_case(args.case)
        if args.images:
            case.images = list(args.images)
    else:
        case = Case(
            case_id=args.id, t=args.t, n=args.n, m=args.m,
            stage_group=args.stage_group,
            presentation=args.presentation or "",
            question=args.question or "",
            images=list(args.images) if args.images else [],
        )
    if args.vision_provider:
        agent.vision_provider = args.vision_provider
    params = None
    if args.temperature is not None or args.max_tokens is not None:
        params = cfg.generation.merged(
            temperature=args.temperature, max_tokens=args.max_tokens
        )
    result = agent.run(case, provider=args.provider, params=params,
                       dry_run=args.dry_run, read_films=not args.no_read_films)
    _print_result(result, args.json)
    return 0 if not result.error else 1


def cmd_batch(args) -> int:
    cfg = _load_config_or_exit(args.config)
    agent = NSCLCAgent(cfg)
    in_dir = Path(args.directory)
    files = sorted(in_dir.glob("*.json"))
    if not files:
        print(f"No .json case files in {in_dir}", file=sys.stderr)
        return 1
    out_dir = Path(args.out) if args.out else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
    exit_code = 0
    for f in files:
        case = _read_case(str(f))
        result = agent.run(case, provider=args.provider, dry_run=args.dry_run)
        if result.error:
            exit_code = 1
        if out_dir:
            (out_dir / f"{f.stem}.result.json").write_text(
                json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"{f.name}: stage="
                  f"{(result.staging or {}).get('stage_group', '?')} "
                  f"module={result.module_key} "
                  f"{'ERROR' if result.error else 'ok'}")
        else:
            _print_result(result, args.json)
            print()
    return exit_code


def cmd_selftest(args) -> int:
    ok, total, failures = run_selftest()
    for f in failures:
        print(f"FAIL: {f}")
    print(f"\nStaging self-test: {ok}/{total} passed")
    return 0 if ok == total else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="nsclc-agent",
        description="Evidence-based, stage-aware NSCLC decision-support agent "
                    "(educational/research use only).",
    )
    p.add_argument("--version", action="version",
                   version=f"nsclc-agent {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("stage", help="Deterministically stage a TNM triple")
    sp.add_argument("t"); sp.add_argument("n")
    sp.add_argument("m", nargs="?", default="M0")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_stage)

    rp = sub.add_parser("route", help="Show the module for a stage group")
    rp.add_argument("stage_group")
    rp.set_defaults(func=cmd_route)

    mp = sub.add_parser("modules", help="List protocol modules")
    mp.set_defaults(func=cmd_modules)

    pp = sub.add_parser("providers", help="List configured providers")
    pp.add_argument("-c", "--config")
    pp.set_defaults(func=cmd_providers)

    run = sub.add_parser("run", help="Run one case through the agent")
    run.add_argument("-c", "--config")
    run.add_argument("--case", help="Path to a JSON case file")
    run.add_argument("--id")
    run.add_argument("--t"); run.add_argument("--n"); run.add_argument("--m")
    run.add_argument("--stage-group", dest="stage_group")
    run.add_argument("--presentation")
    run.add_argument("--question")
    run.add_argument("--images", nargs="+",
                     help="Radiology film(s) to read (file paths or URLs)")
    run.add_argument("--vision-provider", dest="vision_provider",
                     help="Provider used to read films (default: config "
                          "vision_provider)")
    run.add_argument("--no-read-films", action="store_true",
                     dest="no_read_films",
                     help="Do not read attached films (skip the vision step)")
    run.add_argument("-p", "--provider")
    run.add_argument("--temperature", type=float)
    run.add_argument("--max-tokens", type=int, dest="max_tokens")
    run.add_argument("--dry-run", action="store_true",
                     help="Assemble the prompt but do not call the model")
    run.add_argument("--json", action="store_true")
    run.set_defaults(func=cmd_run)

    rd = sub.add_parser("read", help="Read films into proposed TNM descriptors")
    rd.add_argument("--images", nargs="+", required=True,
                    help="Radiology film(s): file paths, data: or http(s) URLs")
    rd.add_argument("-c", "--config")
    rd.add_argument("-p", "--provider",
                    help="Vision provider (default: config vision_provider)")
    rd.add_argument("--context", help="Optional clinical context for the reader")
    rd.add_argument("--json", action="store_true")
    rd.set_defaults(func=cmd_read)

    b = sub.add_parser("batch", help="Run all case files in a directory")
    b.add_argument("directory")
    b.add_argument("-c", "--config")
    b.add_argument("-p", "--provider")
    b.add_argument("-o", "--out", help="Write per-case result JSON here")
    b.add_argument("--dry-run", action="store_true")
    b.add_argument("--json", action="store_true")
    b.set_defaults(func=cmd_batch)

    st = sub.add_parser("selftest", help="Run the staging engine self-test")
    st.set_defaults(func=cmd_selftest)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
