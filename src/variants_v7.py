#!/usr/bin/env python3
"""Export and compare the v7 variants from cache (no extraction, no API calls). Evaluation side: reads expected.json.

    python src/variants_v7.py cases/dev-rear-lamp [--desktop]

  v5_a  reference (the default settings)
  v7_a  v5_a + REVIEW_INSTEAD_OF_CATEGORY_DROP
  v7_b  v7_a + REVIEW_LOW_CONFIDENCE
  v7_c  v7_b with REVIEW_PROMPT = "criteria" (every reviewed item uses the criteria review)

Each file is written to build/cases/<case_id>/submission_<variant>.json, validated with tools/validate.py, checked for
case_id and team_id, and (with --desktop) copied to ~/Desktop/UPLOAD_<variant>.json. Needs review_medium.jsonl and
review_medium_criteria.jsonl (src/review_medium.py --prompt both).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

import config
from export import make_submission, run_tool

sys.path.insert(0, str(config.ROOT / "tools"))
from score_local import _paragraph_key, score  # noqa: E402

from eval_requirements import paragraph_metrics  # noqa: E402

BASE = {"subsection_filter": True, "spec_lists_complete": False, "review_medium": True,
        "exclude_vehicle_level": False, "vehicle_category_check": False}
VARIANTS = {
    "v5_a": {**BASE, "review_instead": False, "review_low": False, "review_prompt": "basic"},
    "v7_a": {**BASE, "review_instead": True, "review_low": False, "review_prompt": "basic"},
    "v7_b": {**BASE, "review_instead": True, "review_low": True, "review_prompt": "basic"},
    "v7_c": {**BASE, "review_instead": True, "review_low": True, "review_prompt": "criteria"},
}


def paragraphs(sub: dict) -> set[tuple]:
    return {(r["source"]["regulation"], _paragraph_key(r["source"].get("paragraph"))) for r in sub["requirements"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("case_dir")
    ap.add_argument("--desktop", action="store_true", help="copy v7_* to ~/Desktop/UPLOAD_<variant>.json")
    args = ap.parse_args(argv)
    case_id = Path(args.case_dir).name
    out_dir = config.CASES_BUILD / case_id
    expected = json.loads((Path(args.case_dir) / "expected.json").read_text(encoding="utf-8"))
    gold = {(r["source"]["regulation"], _paragraph_key(r["source"].get("paragraph"))): r["source"].get("paragraph")
            for r in expected["requirements"]}

    results, subs = {}, {}
    for name, sw in VARIANTS.items():
        log: list = []
        sub, stats = make_submission(case_id, review_log=log, **sw)
        assert sub["case_id"] == "dev-rear-lamp" and sub["team_id"] == config.TEAM_ID, "wrong case_id / team_id"
        path = out_dir / f"submission_{name}.json"
        path.write_text(json.dumps(sub, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        check = run_tool("validate.py", str(path))
        if check.returncode != 0:
            print(check.stdout, check.stderr, file=sys.stderr)
            raise SystemExit(f"error: tools/validate.py reported problems for {path}")
        s = score(sub, expected)
        by = Counter((r["category"], r["confidence"]) for r in log)
        kept = Counter((r["category"], r["confidence"]) for r in log if r["kept"])
        subs[name] = sub
        results[name] = {"requirements": len(sub["requirements"]),
                         "per_regulation": {k: v for k, v in stats.items() if k.startswith("UN ")},
                         "reviewed": len(log), "review_kept": sum(r["kept"] for r in log),
                         "reviewed_by_category_confidence": {f"{c}/{f}": [by[(c, f)], kept[(c, f)]] for c, f in sorted(by, key=str)},
                         "reg_p": s["regulation_level"]["precision"], "reg_r": s["regulation_level"]["recall"],
                         "page_p": s["page_level"]["precision"], "page_r": s["page_level"]["recall"],
                         "para": paragraph_metrics(sub["requirements"], expected["requirements"])}
        if args.desktop and name.startswith("v7"):
            shutil.copy(path, Path.home() / "Desktop" / f"UPLOAD_{name}.json")
    ref = paragraphs(subs["v5_a"]) & set(gold)
    for name in VARIANTS:
        now = paragraphs(subs[name]) & set(gold)
        results[name]["gained_vs_v5_a"] = [f"{r} {gold[(r, k)]}" for r, k in sorted(now - ref)]
        results[name]["lost_vs_v5_a"] = [f"{r} {gold[(r, k)]}" for r, k in sorted(ref - now)]
    (out_dir / "variants_v7.json").write_text(json.dumps(results, indent=1) + "\n", encoding="utf-8")

    print("all files validated by tools/validate.py; case_id dev-rear-lamp, team_id " + config.TEAM_ID + "\n")
    print(f"  {'variant':<6} {'reqs':>5} | {'reg P':>6} {'reg R':>6} | {'page P':>6} {'page R':>6} | {'para R':>6} {'para P':>6} | "
          f"{'reviewed/kept':>13} | gained/lost expected paras vs v5_a")
    for n, r in results.items():
        print(f"  {n:<6} {r['requirements']:>5} | {r['reg_p']:>6.3f} {r['reg_r']:>6.3f} | {r['page_p']:>6.3f} {r['page_r']:>6.3f} | "
              f"{r['para']['recall']:>6.3f} {r['para']['precision']:>6.3f} | {r['reviewed']:>6}/{r['review_kept']:<6} | "
              f"+{len(r['gained_vs_v5_a'])} / -{len(r['lost_vs_v5_a'])}")
    regs = sorted({k for r in results.values() for k in r["per_regulation"]})
    print("\nrequirements per regulation:\n  " + f"{'':<6}" + "".join(f"{k:>9}" for k in regs))
    for n, r in results.items():
        print("  " + f"{n:<6}" + "".join(f"{r['per_regulation'].get(k, 0):>9}" for k in regs))
    print("\nreviewed items [reviewed, kept] by category/confidence:")
    for n, r in results.items():
        print(f"  {n}: {r['reviewed_by_category_confidence']}")
    for n, r in results.items():
        if n != "v5_a":
            print(f"\n{n} expected paragraphs gained: {r['gained_vs_v5_a']}\n{n} expected paragraphs lost: {r['lost_vs_v5_a']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
