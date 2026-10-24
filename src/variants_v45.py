#!/usr/bin/env python3
"""Export and compare the v4/v5 variants from cache (no API calls). Evaluation side: reads expected.json for scoring only.

    python src/variants_v45.py cases/dev-rear-lamp

  v3    reference: v3 filters only
  v4_a  v3 + sub-section filter, SPEC_LISTS_COMPLETE = False
  v4_b  v3 + sub-section filter, SPEC_LISTS_COMPLETE = True
  v5_a  v4_a + second-opinion drop (probability < config.REVIEW_DROP_BELOW)
  v5_b  v4_b + second-opinion drop

Writes build/cases/<case_id>/submission_<variant>.json (v3 is only compared, not rewritten), validates each with
tools/validate.py, and prints requirement counts, scores, and the expected paragraphs each variant loses against v3.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import config
from export import make_submission, run_tool

sys.path.insert(0, str(config.ROOT / "tools"))
from score_local import _paragraph_key, score  # noqa: E402

from eval_requirements import paragraph_metrics  # noqa: E402

VARIANTS = {
    "v3": {"subsection_filter": False, "review_medium": False},
    "v4_a": {"subsection_filter": True, "spec_lists_complete": False, "review_medium": False},
    "v4_b": {"subsection_filter": True, "spec_lists_complete": True, "review_medium": False},
    "v5_a": {"subsection_filter": True, "spec_lists_complete": False, "review_medium": True},
    "v5_b": {"subsection_filter": True, "spec_lists_complete": True, "review_medium": True},
}


def paragraphs(sub: dict) -> set[tuple]:
    return {(r["source"]["regulation"], _paragraph_key(r["source"].get("paragraph"))) for r in sub["requirements"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("case_dir")
    args = ap.parse_args(argv)
    case_id = Path(args.case_dir).name
    out_dir = config.CASES_BUILD / case_id
    expected = json.loads((Path(args.case_dir) / "expected.json").read_text(encoding="utf-8"))
    gold = {(r["source"]["regulation"], _paragraph_key(r["source"].get("paragraph"))): r["source"].get("paragraph")
            for r in expected["requirements"]}

    results, subs = {}, {}
    for name, sw in VARIANTS.items():
        sub, stats = make_submission(case_id, **sw)
        path = out_dir / f"submission_{name}.json"
        path.write_text(json.dumps(sub, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        check = run_tool("validate.py", str(path))
        if check.returncode != 0:
            print(check.stdout, check.stderr, file=sys.stderr)
            raise SystemExit(f"error: tools/validate.py reported problems for {path}")
        s = score(sub, expected)
        subs[name] = sub
        results[name] = {"requirements": len(sub["requirements"]), "valid": True,
                         "per_regulation": {k: v for k, v in stats.items() if k.startswith("UN ")},
                         "left_out_by_subsection": stats.get("left_out_by_subsection", 0),
                         "left_out_by_review": stats.get("left_out_by_review", 0),
                         "reg_p": s["regulation_level"]["precision"], "reg_r": s["regulation_level"]["recall"],
                         "page_p": s["page_level"]["precision"], "page_r": s["page_level"]["recall"],
                         "para": paragraph_metrics(sub["requirements"], expected["requirements"])}
    ref = paragraphs(subs["v3"]) & set(gold)
    for name in VARIANTS:
        lost = sorted(ref - paragraphs(subs[name]))
        results[name]["lost_vs_v3"] = [f"{r} {gold[(r, k)]}" for r, k in lost]
    (out_dir / "variants_v45.json").write_text(json.dumps(results, indent=1) + "\n", encoding="utf-8")

    print("all files validated by tools/validate.py\n")
    print(f"  {'variant':<6} {'reqs':>5} | {'reg P':>6} {'reg R':>6} | {'page P':>6} {'page R':>6} | {'para R':>6} {'para P':>6} | "
          f"{'cut sub/rev':>11} | lost vs v3")
    for n, r in results.items():
        print(f"  {n:<6} {r['requirements']:>5} | {r['reg_p']:>6.3f} {r['reg_r']:>6.3f} | {r['page_p']:>6.3f} {r['page_r']:>6.3f} | "
              f"{r['para']['recall']:>6.3f} {r['para']['precision']:>6.3f} | "
              f"{r['left_out_by_subsection']:>5}/{r['left_out_by_review']:<5} | {len(r['lost_vs_v3'])}")
    regs = sorted({k for r in results.values() for k in r["per_regulation"]})
    print("\nrequirements per regulation:\n  " + f"{'':<6}" + "".join(f"{k:>9}" for k in regs))
    for n, r in results.items():
        print("  " + f"{n:<6}" + "".join(f"{r['per_regulation'].get(k, 0):>9}" for k in regs))
    print("\nexpected paragraphs lost versus v3:")
    for n, r in results.items():
        if n != "v3":
            print(f"  {n}: {r['lost_vs_v3'] or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
