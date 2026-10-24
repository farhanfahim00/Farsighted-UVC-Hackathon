#!/usr/bin/env python3
"""Evaluate stage 3 against a worked example's expected.json (dev only: this is the one place that reads the answer).

    python src/eval_regulations.py build/cases/dev-rear-lamp/regulations.json
    python src/eval_regulations.py A/regulations.json --compare B/regulations.json    # what changed between two runs

Prints regulation recall and precision, the full decision table, every missed or extra regulation with the model's
reason, and how many expected requirements each missed regulation carried. With --compare, also the decisions that
differ between the two runs and the profile fact counts of each run (profile.json next to each regulations.json).
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import config

DEFAULT_EXPECTED = config.ROOT / "cases" / "dev-rear-lamp" / "expected.json"


def load_run(path: Path) -> dict:
    run = json.loads(path.read_text(encoding="utf-8"))
    run["_path"] = path
    run["_applies"] = {d["regulation"] for d in run["decisions"] if d["applies"]}
    profile = path.parent / "profile.json"
    run["_profile"] = json.loads(profile.read_text(encoding="utf-8")) if profile.is_file() else None
    return run


def expected_counts(path: Path) -> Counter:
    reqs = json.loads(path.read_text(encoding="utf-8"))["requirements"]
    return Counter(r["source"]["regulation"] for r in reqs)


def prf(hit: int, predicted: int, expected: int, beta: float = 1.0) -> tuple[float, float, float]:
    p, r = hit / predicted if predicted else 0.0, hit / expected if expected else 0.0
    f = (1 + beta ** 2) * p * r / (beta ** 2 * p + r) if p + r else 0.0
    return p, r, f


def short(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n - 1] + "…"


def report(run: dict, exp: Counter, label: str) -> None:
    gold, pred = set(exp), run["_applies"]
    hit = gold & pred
    p, r, f1 = prf(len(hit), len(pred), len(gold))
    f2 = prf(len(hit), len(pred), len(gold), beta=2)[2]
    total = sum(exp.values())
    got = sum(exp[g] for g in hit)
    print(f"\n==== {label}: {run['_path']} ====")
    print(f"policy={run['unknown_policy']} cite_role={run['cite_role']} model={run['model']} "
          f"failed_calls={run['counts']['failures']}")
    print(f"Expected regulations: {len(gold)} ({total} requirements)   Predicted: {len(pred)}")
    print(f"Regulation recall    {len(hit)}/{len(gold)} = {r:.1%}")
    print(f"Regulation precision {len(hit)}/{len(pred)} = {p:.1%}")
    print(f"F1 {f1:.3f}   F2 {f2:.3f} (F2 is the brief's metric, unconfirmed)")
    print(f"Expected requirements carried by the regulations we kept: {got}/{total} = {got / total:.1%}")

    print("\nDecision table (verdict: HIT = expected and kept, MISS = expected but dropped, EXTRA = kept but not "
          "expected, ok = dropped and not expected)")
    print(f"  {'regulation':<10} {'applies':<7} {'conf':<6} {'level':<24} {'src':<14} {'exp reqs':>8} {'verdict':<7} reason")
    for d in run["decisions"]:
        n = exp.get(d["regulation"], 0)
        verdict = ("HIT" if d["applies"] else "MISS") if d["regulation"] in gold else ("EXTRA" if d["applies"] else "ok")
        print(f"  {d['regulation']:<10} {'yes' if d['applies'] else 'no':<7} {d['confidence']:<6} {d['level']:<24} "
              f"{d['decision_source']:<14} {n:>8} {verdict:<7} {short(d['reason'], 70)}")

    by_name = {d["regulation"]: d for d in run["decisions"]}
    for title, names in (("Missed regulations", sorted(gold - pred)), ("Extra regulations", sorted(pred - gold))):
        print(f"\n{title} ({len(names)}):")
        for name in names:
            d = by_name.get(name)
            if d is None:
                print(f"  {name}: not in the decisions (not in the index?)")
                continue
            print(f"  {name} ({exp.get(name, 0)} expected requirements): model said "
                  f"{'yes' if d['model_applies'] else 'no'}, final {'yes' if d['applies'] else 'no'} "
                  f"[{d['confidence']}, {d['decision_source']}]")
            print(f"    reason: {d['reason']}")
            if d["depends_on"]:
                print(f"    depends_on: {d['depends_on']}  only_uncertain={d['depends_only_on_uncertain']}")
            for note in d["notes"]:
                print(f"    note: {note}")
    profile = run["_profile"]
    if profile:
        counts = Counter(f["status"] for f in profile["facts"])
        print(f"\nProfile: {len(profile['parts'])} parts, {len(profile['facts'])} facts {dict(counts)}, "
              f"{len(profile['drawing_annotations'])} drawing annotations, "
              f"{len(profile['meta']['validation_warnings'])} validation warnings")


def compare(a: dict, b: dict, exp: Counter) -> None:
    print(f"\n==== difference: A={a['_path'].parent.name}  B={b['_path'].parent.name} ====")
    da = {d["regulation"]: d for d in a["decisions"]}
    db = {d["regulation"]: d for d in b["decisions"]}
    changed = [n for n in da if n in db and (da[n]["applies"] != db[n]["applies"]
                                              or da[n]["confidence"] != db[n]["confidence"])]
    gold = set(exp)
    print(f"Decisions that differ ({len(changed)}):")
    for n in sorted(changed):
        mark = "expected" if n in gold else "not expected"
        print(f"  {n:<10} A: {'yes' if da[n]['applies'] else 'no'}/{da[n]['confidence']}   "
              f"B: {'yes' if db[n]['applies'] else 'no'}/{db[n]['confidence']}   ({mark}, {exp.get(n, 0)} reqs)")
        print(f"      A: {short(da[n]['reason'], 110)}")
        print(f"      B: {short(db[n]['reason'], 110)}")
    pa, pb = a["_profile"], b["_profile"]
    if pa and pb:
        fa = {f["feature"].lower(): f["status"] for f in pa["facts"]}
        fb = {f["feature"].lower(): f["status"] for f in pb["facts"]}
        print(f"\nProfile facts: A {len(fa)} features, B {len(fb)} features; "
              f"annotations A {len(pa['drawing_annotations'])}, B {len(pb['drawing_annotations'])}")
        print(f"  status counts A {dict(Counter(fa.values()))}")
        print(f"  status counts B {dict(Counter(fb.values()))}")
        moved = [(k, fa[k], fb[k]) for k in sorted(fa.keys() & fb.keys()) if fa[k] != fb[k]]
        print(f"  features with a different status ({len(moved)}), first 25:")
        for k, x, y in moved[:25]:
            print(f"    {k}: {x} -> {y}")
        print(f"  only in A: {len(fa.keys() - fb.keys())}   only in B: {len(fb.keys() - fa.keys())}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("regulations", help="build/cases/<case_id>/regulations.json")
    ap.add_argument("--compare", help="a second regulations.json to compare with")
    ap.add_argument("--expected", default=str(DEFAULT_EXPECTED))
    args = ap.parse_args(argv)
    exp = expected_counts(Path(args.expected))
    a = load_run(Path(args.regulations))
    report(a, exp, "run A")
    if args.compare:
        b = load_run(Path(args.compare))
        report(b, exp, "run B")
        compare(a, b, exp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
