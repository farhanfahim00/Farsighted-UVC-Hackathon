#!/usr/bin/env python3
"""Export and compare v8_a / v8_b with v5_a and v7_c from cache (no extraction, no API calls). Evaluation side.

    python src/variants_v8.py cases/dev-rear-lamp [--desktop]

  v8_a  REVIEW_ALL: every item reviewed with the criteria prompt, kept at probability >= 15
  v8_b  v8_a + REVIEW_BORDERLINE_TWICE: items with a first probability of 10-40 get a second review; the mean is used
Writes submission_<variant>.json (validated by tools/validate.py, case_id and team_id checked); --desktop copies the v8
files to ~/Desktop/UPLOAD_<variant>.json. Needs review_medium_criteria.jsonl (+ _pass2) from src/review_medium.py.
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
from variants_v7 import BASE, VARIANTS as V7, paragraphs

sys.path.insert(0, str(config.ROOT / "tools"))
from score_local import _paragraph_key, score  # noqa: E402

from eval_requirements import paragraph_metrics  # noqa: E402

OFF = {"review_all": False, "review_twice": False}
VARIANTS = {
    "v5_a": {**V7["v5_a"], **OFF},
    "v7_c": {**V7["v7_c"], **OFF},
    "v8_a": {**BASE, "review_instead": False, "review_low": False, "review_prompt": "criteria", "review_all": True,
             "review_twice": False},
    "v8_b": {**BASE, "review_instead": False, "review_low": False, "review_prompt": "criteria", "review_all": True,
             "review_twice": True},
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("case_dir")
    ap.add_argument("--desktop", action="store_true")
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
        assert sub["case_id"] == "dev-rear-lamp" and sub["team_id"] == config.TEAM_ID
        path = out_dir / f"submission_{name}.json"
        path.write_text(json.dumps(sub, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        check = run_tool("validate.py", str(path))
        if check.returncode != 0:
            print(check.stdout, check.stderr, file=sys.stderr)
            raise SystemExit(f"error: tools/validate.py reported problems for {path}")
        s = score(sub, expected)
        dropped_high = Counter(r["category"] for r in log if r["confidence"] == "high" and not r["kept"])
        subs[name] = sub
        results[name] = {"requirements": len(sub["requirements"]),
                         "per_regulation": {k: v for k, v in stats.items() if k.startswith("UN ")},
                         "reviewed": len(log), "review_kept": sum(r["kept"] for r in log),
                         "high_confidence_dropped": dict(dropped_high),
                         "reg_p": s["regulation_level"]["precision"], "reg_r": s["regulation_level"]["recall"],
                         "page_p": s["page_level"]["precision"], "page_r": s["page_level"]["recall"],
                         "para": paragraph_metrics(sub["requirements"], expected["requirements"])}
        if args.desktop and name.startswith("v8"):
            shutil.copy(path, Path.home() / "Desktop" / f"UPLOAD_{name}.json")
    for base in ("v5_a", "v7_c"):
        ref = paragraphs(subs[base]) & set(gold)
        for name in VARIANTS:
            now = paragraphs(subs[name]) & set(gold)
            results[name][f"gained_vs_{base}"] = [f"{r} {gold[(r, k)]}" for r, k in sorted(now - ref)]
            results[name][f"lost_vs_{base}"] = [f"{r} {gold[(r, k)]}" for r, k in sorted(ref - now)]
    (out_dir / "variants_v8.json").write_text(json.dumps(results, indent=1) + "\n", encoding="utf-8")

    print(f"validated; case_id dev-rear-lamp, team_id {config.TEAM_ID}\n")
    print(f"  {'variant':<6} {'reqs':>5} | {'reg P':>6} {'reg R':>6} | {'page P':>6} {'page R':>6} | {'para R':>6} {'para P':>6} | "
          f"{'reviewed/kept':>13} | vs v5_a +/-  vs v7_c +/-")
    for n, r in results.items():
        print(f"  {n:<6} {r['requirements']:>5} | {r['reg_p']:>6.3f} {r['reg_r']:>6.3f} | {r['page_p']:>6.3f} {r['page_r']:>6.3f} | "
              f"{r['para']['recall']:>6.3f} {r['para']['precision']:>6.3f} | {r['reviewed']:>6}/{r['review_kept']:<6} | "
              f"+{len(r['gained_vs_v5_a'])}/-{len(r['lost_vs_v5_a'])}        +{len(r['gained_vs_v7_c'])}/-{len(r['lost_vs_v7_c'])}")
    regs = sorted({k for r in results.values() for k in r["per_regulation"]})
    print("\nrequirements per regulation:\n  " + f"{'':<6}" + "".join(f"{k:>9}" for k in regs))
    for n, r in results.items():
        print("  " + f"{n:<6}" + "".join(f"{r['per_regulation'].get(k, 0):>9}" for k in regs))
    for n in ("v8_a", "v8_b"):
        r = results[n]
        print(f"\n{n}: high-confidence items dropped {sum(r['high_confidence_dropped'].values())} {r['high_confidence_dropped']}")
        print(f"{n} expected paragraphs lost vs v5_a: {r['lost_vs_v5_a']}\n{n} gained vs v5_a: {r['gained_vs_v5_a']}")
        print(f"{n} lost vs v7_c: {r['lost_vs_v7_c']}\n{n} gained vs v7_c: {r['gained_vs_v7_c']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
