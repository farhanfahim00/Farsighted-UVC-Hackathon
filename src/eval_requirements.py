#!/usr/bin/env python3
"""Evaluate stages 4-6 against a worked example's expected.json (dev only: the evaluation side reads the answer).

    python src/eval_requirements.py build/cases/dev-rear-lamp/submission.json

Reports paragraph-level recall and precision (an expected paragraph counts when we produced at least one requirement
for it), requirement counts per regulation (ours vs expected), and every expected paragraph we missed with the reason:
the model's own reason, or "not a candidate" with the stage that dropped it (regulation or section).
Paragraph labels are compared with the scorer's own normalisation (tools/score_local.py, imported, not copied).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import config

sys.path.insert(0, str(config.ROOT / "tools"))
from score_local import _paragraph_key  # noqa: E402

from select_sections import load_base_records, unit_of  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("submission", nargs="?", default=str(config.CASES_BUILD / "dev-rear-lamp" / "submission.json"))
    ap.add_argument("--expected", default=str(config.ROOT / "cases" / "dev-rear-lamp" / "expected.json"))
    args = ap.parse_args(argv)
    case_dir = Path(args.submission).parent
    sub = json.loads(Path(args.submission).read_text(encoding="utf-8"))["requirements"]
    exp = json.loads(Path(args.expected).read_text(encoding="utf-8"))["requirements"]

    ours: dict[tuple, list] = defaultdict(list)
    for r in sub:
        ours[(r["source"]["regulation"], _paragraph_key(r["source"].get("paragraph")))].append(r)
    gold: dict[tuple, dict] = {}
    for r in exp:
        s = r["source"]
        g = gold.setdefault((s["regulation"], _paragraph_key(s.get("paragraph"))), {"label": s.get("paragraph"), "n": 0})
        g["n"] += 1

    hit = set(gold) & set(ours)
    print(f"Expected: {len(exp)} requirements in {len(gold)} paragraphs.  Ours: {len(sub)} requirements in {len(ours)} paragraphs.")
    print(f"Paragraph recall    {len(hit)}/{len(gold)} = {len(hit) / len(gold):.1%}")
    print(f"Paragraph precision {len(hit)}/{len(ours)} = {len(hit) / len(ours):.1%}")

    print("\nPer regulation:")
    print(f"  {'regulation':<10} {'ours reqs':>9} {'exp reqs':>9} {'ours paras':>11} {'exp paras':>10} {'exp paras found':>16}")
    regs = sorted({k[0] for k in gold} | {k[0] for k in ours}, key=lambda x: (x not in {k[0] for k in gold}, x))
    for reg in regs:
        og = [k for k in ours if k[0] == reg]
        eg = [k for k in gold if k[0] == reg]
        print(f"  {reg:<10} {sum(len(ours[k]) for k in og):>9} {sum(gold[k]['n'] for k in eg):>9} {len(og):>11} "
              f"{len(eg):>10} {sum(1 for k in eg if k in ours):>16}")

    # why was an expected paragraph missed?
    extracted = {}
    ep = case_dir / "extracted.jsonl"
    if ep.is_file():
        extracted = {r["id"]: r for r in (json.loads(l) for l in ep.read_text(encoding="utf-8").splitlines() if l)}
    decisions = {}
    rp = case_dir / "regulations.json"
    if rp.is_file():
        decisions = {d["regulation"]: d for d in json.loads(rp.read_text(encoding="utf-8"))["decisions"]}
    sections = {}
    sp = case_dir / "sections.json"
    if sp.is_file():
        sections = {r["regulation"]: r for r in json.loads(sp.read_text(encoding="utf-8"))["regulations"]}
    base = load_base_records({k[0] for k in gold})

    missed = sorted(set(gold) - set(ours))
    print(f"\nMissed expected paragraphs ({len(missed)}):")
    reasons = Counter()
    for reg, key in missed:
        label = gold[(reg, key)]["label"]
        recs = [r for r in base[reg] if _paragraph_key(r["paragraph"]) == key]
        if not recs:
            why, kind = "no index record with this label", "label"
        else:
            r = recs[0]
            row = extracted.get(r["id"])
            if row is not None:
                if row["status"] != "answered":
                    why, kind = f"{row['status']}: {row['error']}", "no_answer"
                elif not row["applies"]:
                    why, kind = f"model said no: {row['reason']}", "model_no"
                else:
                    why, kind = f"model said yes but wrote no requirement: {row['reason']}", "model_no_req"
            elif reg not in decisions or not decisions[reg]["applies"]:
                why, kind = f"not a candidate: regulation not kept in stage 3 ({decisions.get(reg, {}).get('reason', '')[:120]})", "regulation"
            else:
                u = unit_of(r)
                sel = sections.get(reg, {}).get("sections", {}).get(u)
                if sel and not sel["relevant"]:
                    why, kind = f"not a candidate: section {u} dropped in stage 4 ({sel['reason']})", "section"
                else:
                    why, kind = f"not a candidate: {r['kind']} record in section {u} (not a numbered paragraph)", "kind"
        reasons[kind] += 1
        print(f"  {reg:<8} {label!s:<22} ({gold[(reg, key)]['n']} exp reqs)  {why}")
    print(f"\nMissed by cause: {dict(reasons)}")

    extra = sorted(set(ours) - set(gold))
    print(f"\nParagraphs we produced that are not expected: {len(extra)} "
          f"({sum(len(ours[k]) for k in extra)} requirements); per regulation: "
          f"{dict(Counter(k[0] for k in extra))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
