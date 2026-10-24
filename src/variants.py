#!/usr/bin/env python3
"""Export and compare submission variants from the same extraction cache (no API calls).

    python src/variants.py cases/dev-rear-lamp

  v2_a  overlap dropped, applied category rules, confidence >= medium
  v2_b  v2_a with high confidence only
  v2_c  v2_a with UNKNOWN_POLICY = "exclude"
  v2_d  v2_b with UNKNOWN_POLICY = "exclude"
  v2_e  exploratory: v2_a that also leaves out test-method paragraphs unless high confidence (not rule-approved)

Each variant is written to build/cases/<case_id>/submission_<variant>.json and validated with tools/validate.py (a
problem stops the run). The table also scores each one with tools/score_local.py (regulation and page level) and,
when expected.json is available (dev case), eval_requirements' paragraph recall and precision. The expected file is read
for scoring only, never for building a variant.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import config
from export import make_submission, run_tool
from regulation_set import kept_regulations

sys.path.insert(0, str(config.ROOT / "tools"))
from score_local import score  # noqa: E402

from eval_requirements import paragraph_metrics  # noqa: E402

VARIANTS = {
    "v2_a": {"min_confidence": "medium", "unknown_policy": "include"},
    "v2_b": {"min_confidence": "high", "unknown_policy": "include"},
    "v2_c": {"min_confidence": "medium", "unknown_policy": "exclude"},
    "v2_d": {"min_confidence": "high", "unknown_policy": "exclude"},
    # exploratory, NOT approved by the rule thresholds (it removes 8 expected paragraphs on the dev case): v2_a without
    # medium/low-confidence test-method paragraphs; shows what a stricter cut would cost
    "v2_e": {"min_confidence": "medium", "unknown_policy": "include", "drop_unless_high": {"conformity_of_production", "test_method"}},
}
TARGET_REQS = (350, 450)
TARGET_PARA_RECALL = 0.90


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("case_dir")
    ap.add_argument("--expected", help="expected.json, for scoring only (default: cases/<case>/expected.json if present)")
    args = ap.parse_args(argv)
    case_id = Path(args.case_dir).name
    out_dir = config.CASES_BUILD / case_id
    expected_path = Path(args.expected) if args.expected else Path(args.case_dir) / "expected.json"
    expected = json.loads(expected_path.read_text(encoding="utf-8")) if expected_path.is_file() else None

    results = {}
    for name, sw in VARIANTS.items():
        sub, stats = make_submission(case_id, drop_overlap=True, drop_categories=config.DROP_CATEGORIES, faithfulness=False, **sw)
        path = out_dir / f"submission_{name}.json"
        path.write_text(json.dumps(sub, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        check = run_tool("validate.py", str(path))
        if check.returncode != 0:
            print(check.stdout, check.stderr, file=sys.stderr)
            raise SystemExit(f"error: tools/validate.py reported problems for {path}")
        row = {"requirements": len(sub["requirements"]), "per_regulation": {k: v for k, v in stats.items() if k.startswith("UN ")},
               "regulations_kept": kept_regulations(case_id, unknown_policy=sw["unknown_policy"], drop_overlap=True),
               "left_out_by_confidence": stats.get("left_out_by_confidence", 0),
               "left_out_by_category": stats.get("left_out_by_category", 0), "valid": True}
        if expected:
            s = score(sub, expected)
            row.update(reg_p=s["regulation_level"]["precision"], reg_r=s["regulation_level"]["recall"],
                       page_p=s["page_level"]["precision"], page_r=s["page_level"]["recall"],
                       para=paragraph_metrics(sub["requirements"], expected["requirements"]))
        results[name] = row
    (out_dir / "variants.json").write_text(json.dumps(results, indent=1) + "\n", encoding="utf-8")

    print(f"switches: overlap dropped, DROP_CATEGORIES={sorted(config.DROP_CATEGORIES)}; all files validated by tools/validate.py")
    head = f"  {'variant':<7} {'reqs':>5} {'regs':>4} | {'reg P':>6} {'reg R':>6} | {'page P':>6} {'page R':>6} | {'para R':>6} {'para P':>6} | left out (conf/cat)"
    print(head)
    for name, r in results.items():
        if expected:
            print(f"  {name:<7} {r['requirements']:>5} {len(r['regulations_kept']):>4} | {r['reg_p']:>6.3f} {r['reg_r']:>6.3f} | "
                  f"{r['page_p']:>6.3f} {r['page_r']:>6.3f} | {r['para']['recall']:>6.3f} {r['para']['precision']:>6.3f} | "
                  f"{r['left_out_by_confidence']}/{r['left_out_by_category']}")
        else:
            print(f"  {name:<7} {r['requirements']:>5} {len(r['regulations_kept']):>4}")
    print("\nrequirements per regulation:")
    regs = sorted({k for r in results.values() for k in r["per_regulation"]})
    print("  " + f"{'':<7}" + "".join(f"{k:>9}" for k in regs))
    for name, r in results.items():
        print("  " + f"{name:<7}" + "".join(f"{r['per_regulation'].get(k, 0):>9}" for k in regs))
    print("\nregulations kept: " + "; ".join(f"{n}: {', '.join(r['regulations_kept'])}" for n, r in results.items()))
    if expected:
        ok = [n for n, r in results.items()
              if TARGET_REQS[0] <= r["requirements"] <= TARGET_REQS[1] and r["para"]["recall"] > TARGET_PARA_RECALL]
        print(f"\nwithin {TARGET_REQS[0]}-{TARGET_REQS[1]} requirements and paragraph recall > {TARGET_PARA_RECALL:.0%}: "
              f"{ok or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
