#!/usr/bin/env python3
"""Local judge for the dev case (evaluation side only, never used to build a submission).

    python src/local_judge.py cases/dev-rear-lamp                 # estimate the cost, no API call
    python src/local_judge.py cases/dev-rear-lamp --run           # judge, then print buckets
    python src/local_judge.py cases/dev-rear-lamp --submission submission_v3.json --run   # another submission

For every expected paragraph that has requirements in the submission, one SCREEN_MODEL call asks which of our
requirements states each expected one. Every expected requirement that is not matched is put in one bucket:

  not_a_candidate       the paragraph was never a candidate for extraction
  judged_not_applicable the extraction said the paragraph does not apply (or its regulation was judged not to apply)
  filtered_by_export    extracted with requirements, then left out by an export filter (the filter is named)
  empty_output          applies = yes but no requirement written
  written_but_different we have requirements for the paragraph, none states the same obligation

Our requirements that match no expected one are grouped by regulation and top-level section title.
Calls are cached by llm.call_json. The expected file is read here only, never by the pipeline.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import config
from export import CONFIDENCE_RANK
from regulation_set import final_decisions

sys.path.insert(0, str(config.ROOT / "tools"))
from score_local import _paragraph_key  # noqa: E402

import llm  # noqa: E402

DEFAULT_SUBMISSION = "submission_v2_e.json"      # its report is judge_report.json, others judge_report_<name>.json
VARIANT_SWITCHES = {"min_confidence": "medium", "unknown_policy": "include",
                    "drop_unless_high": {"conformity_of_production", "test_method"}}   # v2_e, see variants.py
PRICE_IN, PRICE_OUT = 2.00, 10.00      # USD per million tokens, claude-sonnet-5-5
EST_OUT_TOKENS = 600                   # per call, thinking included; the run is stopped on real spend, not on this
BUDGET_USD = 2.00                      # a little under the EUR 2 limit
SCHEMA = llm.object_schema({"matches": {"type": "array", "items": llm.object_schema({
    "expected_id": {"type": "string"},
    "our_id": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    "note": {"type": "string"}})}})


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def key_of(regulation: str, paragraph) -> tuple[str, str | None]:
    return regulation, _paragraph_key(paragraph)


def group(items: list[dict], regulation, paragraph) -> dict:
    out = defaultdict(list)
    for it in items:
        out[key_of(regulation(it), paragraph(it))].append(it)
    return out


def build_calls(expected_by_par: dict, ours_by_par: dict) -> list[dict]:
    calls = []
    for k, exp in expected_by_par.items():
        ours = ours_by_par.get(k)
        if not ours:
            continue
        eids = {f"E{i}": r for i, r in enumerate(exp, 1)}
        oids = {f"O{i}": r for i, r in enumerate(ours, 1)}
        content = [{"type": "text", "text": json.dumps({
            "regulation": k[0], "paragraph": exp[0]["source"]["paragraph"],
            "EXPECTED": [{"id": i, "text": r["text"]} for i, r in eids.items()],
            "OURS": [{"id": i, "text": r["text"]} for i, r in oids.items()]}, ensure_ascii=False, indent=1)}]
        calls.append({"key": k, "content": content, "eids": eids, "oids": oids})
    return calls


def estimate(calls: list[dict]) -> dict:
    """Cost of the calls that are not in the cache yet."""
    system = llm.prompt_text("judge_system.md")
    chars, cached = 0, 0
    for c in calls:
        if (config.CACHE_DIR / f"{llm.cache_key(config.SCREEN_MODEL, system, c['content'], SCHEMA)}.json").is_file():
            cached += 1
            continue
        chars += len(system) + len(json.dumps(SCHEMA)) + sum(len(b["text"]) for b in c["content"])
    new = len(calls) - cached
    in_tok = int(chars / 3.5)                                     # deliberately high
    out_tok = EST_OUT_TOKENS * new
    usd = in_tok * PRICE_IN / 1e6 + out_tok * PRICE_OUT / 1e6
    return {"calls": len(calls), "already_cached": cached, "input_tokens": in_tok, "output_tokens": out_tok,
            "estimated_usd": round(usd, 3)}


def spent_usd() -> float:
    s = llm.STATS
    return s["input_tokens"] * PRICE_IN / 1e6 + s["output_tokens"] * PRICE_OUT / 1e6


def judge(call: dict, budget: float) -> dict:
    if spent_usd() >= budget:
        raise SystemExit(f"stopped: spend {spent_usd():.3f} USD reached the budget of {budget:.2f} USD (calls so far are cached)")
    data, info = llm.call_json(config.SCREEN_MODEL, "judge_system.md", call["content"], SCHEMA,
                               label=f"judge {call['key'][0]} {call['key'][1]}", max_tokens=4000)
    got = {m["expected_id"]: m for m in data["matches"]}
    out = {}
    for eid in call["eids"]:
        m = got.get(eid) or {}
        oid = m.get("our_id")
        out[eid] = {"our_id": oid if oid in call["oids"] else None, "note": m.get("note", "")}
    return out


def export_filter(row: dict, kept: set[str]) -> str | None:
    """Why build_submission leaves out a row that has requirements, using the v2_e switches (same order as export.py)."""
    if row["regulation"] not in kept:
        return "regulation_not_kept"
    if CONFIDENCE_RANK.get(row.get("confidence"), 2) < CONFIDENCE_RANK[VARIANT_SWITCHES["min_confidence"]]:
        return f"min_confidence:{row.get('confidence')}"
    if row.get("category") in config.DROP_CATEGORIES:
        return f"drop_categories:{row.get('category')}"
    if row.get("category") in VARIANT_SWITCHES["drop_unless_high"] and row.get("confidence") != "high":
        return f"drop_unless_high:{row.get('category')}/{row.get('confidence')}"
    return None


def classify(k, extracted: dict, candidates: set, decisions: dict, kept: set[str]) -> tuple[str, str]:
    """(bucket, detail) for an expected paragraph where the submission has no requirement at all."""
    if k not in candidates:
        d = decisions.get(k[0])
        if d is None:
            return "not_a_candidate", "regulation was not screened"
        if not d["applies"]:
            return ("judged_not_applicable", "regulation judged not to apply") if d["decision_source"] != "overlap" \
                else ("filtered_by_export", "regulation_dropped_by_overlap")
        return "not_a_candidate", "paragraph not selected for extraction"
    row = extracted.get(k)
    if row is None:
        return "not_a_candidate", "no extraction row"
    if not row["applies"] and not row["requirements"]:
        return "judged_not_applicable", f"{row.get('category')}/{row.get('confidence')}: {row.get('reason', '')}"
    if row["requirements"]:
        f = export_filter(row, kept)
        return "filtered_by_export", f or f"excluded:{row.get('category')}/{row.get('confidence')}"
    return "empty_output", f"{row.get('category')}/{row.get('confidence')}: {row.get('reason', '')}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("case_dir")
    ap.add_argument("--submission", default=DEFAULT_SUBMISSION, help="file in build/cases/<case>/ (v3 uses the v2_e filters)")
    ap.add_argument("--run", action="store_true", help="make the API calls (without it only the estimate is printed)")
    ap.add_argument("--budget", type=float, default=BUDGET_USD, help="stop when real spend reaches this many USD")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args(argv)
    case_id = Path(args.case_dir).name
    out_dir = config.CASES_BUILD / case_id

    expected = json.loads((Path(args.case_dir) / "expected.json").read_text(encoding="utf-8"))["requirements"]
    ours = json.loads((out_dir / args.submission).read_text(encoding="utf-8"))["requirements"]
    extracted = {key_of(r["regulation"], r["paragraph"]): r for r in read_jsonl(out_dir / "extracted.jsonl")}
    candidates = {key_of(r["regulation"], r["paragraph"]) for r in read_jsonl(out_dir / "candidates.jsonl")}
    decisions = {d["regulation"]: d for d in final_decisions(case_id, unknown_policy=VARIANT_SWITCHES["unknown_policy"],
                                                             drop_overlap=True)}
    kept = {r for r, d in decisions.items() if d["applies"]}
    expected_by_par = group(expected, lambda r: r["source"]["regulation"], lambda r: r["source"]["paragraph"])
    ours_by_par = group(ours, lambda r: r["source"]["regulation"], lambda r: r["source"]["paragraph"])

    calls = build_calls(expected_by_par, ours_by_par)
    est = estimate(calls)
    print(f"expected paragraphs {len(expected_by_par)} ({len(expected)} requirements); our requirements {len(ours)}")
    print(f"estimate: {est['calls']} calls ({est['already_cached']} already cached; only the rest is paid), ~{est['input_tokens']} input + "
          f"~{est['output_tokens']} output tokens = ~{est['estimated_usd']} USD with {config.SCREEN_MODEL} "
          f"(${PRICE_IN}/${PRICE_OUT} per MTok); budget {args.budget:.2f} USD")
    if not args.run:
        print("no API call made; add --run to judge")
        return 0
    if est["estimated_usd"] > args.budget:
        raise SystemExit(f"error: the estimate is above the budget of {args.budget:.2f} USD; nothing was called")

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(lambda c: judge(c, args.budget), calls))
    print(f"judged: {llm.usage_summary()}  real spend ~{spent_usd():.3f} USD")

    # ---- expected requirements: matched or bucketed ---------------------------------------------------------
    matched_ours: set[str] = set()                       # ids of our requirements claimed by some expected one
    details, buckets = [], Counter()
    by_key = {c["key"]: (c, res) for c, res in zip(calls, results)}
    for k, exp in expected_by_par.items():
        if k in by_key:
            call, res = by_key[k]
            for eid, e in call["eids"].items():
                m = res[eid]
                if m["our_id"]:
                    o = call["oids"][m["our_id"]]
                    matched_ours.add(o["id"])
                    bucket, detail = "matched", m["note"]
                    rec_ours = [o["text"]]
                else:
                    bucket, detail = "written_but_different", m["note"]
                    rec_ours = [o["text"] for o in call["oids"].values()]
                buckets[bucket] += 1
                details.append({"expected_id": e["id"], "regulation": k[0], "paragraph": e["source"]["paragraph"],
                                "bucket": bucket, "detail": detail, "expected": e["text"], "ours": rec_ours,
                                "our_ids": [o["id"] for o in (call["oids"].values() if not m["our_id"] else [call["oids"][m["our_id"]]])]})
        else:
            bucket, detail = classify(k, extracted, candidates, decisions, kept)
            for e in exp:
                buckets[bucket] += 1
                details.append({"expected_id": e["id"], "regulation": k[0], "paragraph": e["source"]["paragraph"],
                                "bucket": bucket, "detail": detail, "expected": e["text"], "ours": [], "our_ids": []})
    filters = Counter(d["detail"].split(":")[0] + (":" + d["detail"].split(":")[1] if d["detail"].startswith("drop_unless_high") else "")
                      for d in details if d["bucket"] == "filtered_by_export")

    # ---- our requirements that match nothing, grouped by regulation and top-level section --------------------
    paragraphs = {r["id"]: r for r in read_jsonl(config.ROOT / "build" / "paragraphs.jsonl") if r["role"] == "base"}
    groups = defaultdict(list)
    for o in ours:
        if o["id"] in matched_ours:
            continue
        rec = paragraphs.get(o["evidence_id"], {})
        crumbs = rec.get("breadcrumb") or []
        top = crumbs[0] if crumbs else (rec.get("title") or rec.get("paragraph") or "?")
        groups[(o["source"]["regulation"], top)].append(o["id"])
    unmatched_total = sum(len(v) for v in groups.values())
    top_groups = [{"regulation": r, "section": s, "unmatched": len(v), "example_ids": v[:3]}
                  for (r, s), v in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:10]]

    total = len(expected)
    print(f"\nexpected requirements {total}:")
    for name in ("matched", "written_but_different", "empty_output", "filtered_by_export", "judged_not_applicable",
                 "not_a_candidate"):
        print(f"  {name:<22} {buckets[name]:>4}  {buckets[name] / total:6.1%}")
    if filters:
        print("  filtered_by_export by filter: " + ", ".join(f"{k} {v}" for k, v in filters.most_common()))
    print(f"\nour requirements {len(ours)}: matched {len(matched_ours)}, unmatched {unmatched_total}")
    print("top groups of unmatched requirements (regulation | top-level section):")
    for g in top_groups:
        print(f"  {g['unmatched']:>4}  {g['regulation']} | {g['section']}")

    # 10 side-by-side examples of written_but_different, spread over regulations
    pool = [d for d in details if d["bucket"] == "written_but_different"]
    by_reg = defaultdict(list)
    for d in pool:
        by_reg[d["regulation"]].append(d)
    examples = []
    while len(examples) < min(10, len(pool)):
        for reg in sorted(by_reg):
            if by_reg[reg] and len(examples) < 10:
                examples.append(by_reg[reg].pop(0))
    print(f"\nwritten_but_different, {len(examples)} of {len(pool)} examples:")
    for i, d in enumerate(examples, 1):
        print(f"\n[{i}] {d['regulation']} {d['paragraph']}  ({d['detail']})")
        print(f"  EXPECTED: {d['expected']}")
        for t in d["ours"]:
            print(f"  OURS:     {t}")

    report = {"case_id": case_id, "submission": args.submission, "model": config.SCREEN_MODEL,
              "cost": {"estimated_usd": est["estimated_usd"], "real_spend_usd": round(spent_usd(), 4), **llm.STATS},
              "buckets": {k: buckets[k] for k in ("matched", "written_but_different", "empty_output",
                                                  "filtered_by_export", "judged_not_applicable", "not_a_candidate")},
              "filtered_by_export_by_filter": dict(filters),
              "our_requirements": {"total": len(ours), "matched": len(matched_ours), "unmatched": unmatched_total},
              "top_unmatched_groups": top_groups, "written_but_different_examples": examples,
              "expected_requirements": details}
    path = out_dir / ("judge_report.json" if args.submission == DEFAULT_SUBMISSION
                      else f"judge_report_{Path(args.submission).stem.removeprefix('submission_')}.json")
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
