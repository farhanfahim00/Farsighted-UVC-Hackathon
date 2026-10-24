#!/usr/bin/env python3
"""Run stages 2-6 for one case folder: profile, scope screening (3, then 3b overlap), section selection,
extraction, faithfulness check, export.

    python src/run_case.py cases/dev-rear-lamp            # skips a stage whose output already exists
    python src/run_case.py cases/dev-rear-lamp --force    # redo both (API responses still come from build/cache/)

Outputs (case_id = the folder name), in build/cases/<case_id>/: profile.json, regulations.json, overlap.json, sections.json,
candidates.jsonl, extracted.jsonl, faithfulness.jsonl, submission.json (validated with tools/validate.py).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import config
import llm
from llm import LLMFailure
from product_profile import build_profile
from overlap import find_overlaps, print_result
from screen_regulations import screen_case
from select_sections import print_counts, select_case
from extract import extract_case, summarize
from export import export_case
from faithfulness import check_case


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("case_dir")
    ap.add_argument("--force", action="store_true", help="rerun stages even if their output exists")
    args = ap.parse_args(argv)
    case_dir = Path(args.case_dir)
    if not case_dir.is_dir():
        print(f"error: {case_dir} is not a folder", file=sys.stderr)
        return 2
    out_dir = config.CASES_BUILD / case_dir.name
    try:
        if args.force or not (out_dir / "profile.json").is_file():
            p = build_profile(case_dir)
            print(f"[stage 2] profile written: {len(p['facts'])} facts, "
                  f"{len(p['meta']['validation_warnings'])} warnings")
        else:
            print("[stage 2] skipped (profile.json exists; use --force)")
        if args.force or not (out_dir / "regulations.json").is_file():
            r = screen_case(case_dir.name)
            print(f"[stage 3] regulations written: {r['counts']['applies']} of {r['counts']['regulations']} apply, "
                  f"{r['counts']['failures']} failed calls")
        else:
            print("[stage 3] skipped (regulations.json exists; use --force)")
        if config.DROP_OVERLAPPING and (args.force or not (out_dir / "overlap.json").is_file()):
            print("[stage 3b] overlapping regulations")
            print_result(find_overlaps(case_dir.name))
        elif config.DROP_OVERLAPPING:
            print("[stage 3b] skipped (overlap.json exists; use --force)")
        if args.force or not (out_dir / "candidates.jsonl").is_file():
            print("[stage 4] section selection")
            print_counts(select_case(case_dir.name))
        else:
            print("[stage 4] skipped (candidates.jsonl exists; use --force)")
        if args.force or not (out_dir / "extracted.jsonl").is_file():
            print("[stage 5] extraction")
            summarize(extract_case(case_dir.name))
        else:
            print("[stage 5] skipped (extracted.jsonl exists; use --force)")
        if config.FAITHFULNESS_CHECK and (args.force or not (out_dir / "faithfulness.jsonl").is_file()):
            print("[stage 5b] faithfulness check")
            res = check_case(case_dir.name)
            print(f"  checked {len(res['results'])} requirements, changed {sum(x['changed'] for x in res['results'])}")
        elif config.FAITHFULNESS_CHECK:
            print("[stage 5b] skipped (faithfulness.jsonl exists; use --force)")
        if args.force or not (out_dir / "submission.json").is_file():
            print("[stage 6] export")
            export_case(case_dir.name)
        else:
            print("[stage 6] skipped (submission.json exists; use --force)")
    except (LLMFailure, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(llm.usage_summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
