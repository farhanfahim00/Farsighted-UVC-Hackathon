#!/usr/bin/env python3
"""Diagnosis of a submission against expected.json (evaluation side only; changes no rule).

    python src/diagnose.py cases/dev-rear-lamp --submission v8_b

Uses the judge reports of the submission and of the "diag_all" submission (every reviewed item, threshold off) written
by src/local_judge.py, the extraction rows and the review files. Each missed expected requirement and each wrong pick
of the submission gets one reason code (SCREEN_MODEL, batches of BATCH, cached). Prints the tables and writes
build/cases/<case_id>/diagnosis_<submission>.json.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import config
from export import load_review
from llm import LLMFailure, call_json, object_schema, usage_summary
from select_sections import load_base_records

BATCH = 15
CODES = ["duplicate_or_subsidiary", "test_setup", "admin_or_cop", "wrong_product_scope", "citation_or_split",
         "extraction_miss", "threshold_drop", "paraphrase"]
SCHEMA = object_schema({"items": {"type": "array", "items": object_schema({
    "n": {"type": "integer"}, "code": {"type": "string", "enum": CODES}, "note": {"type": "string"}})}})
BANDS = [(0, 15, "0-15"), (15, 30, "15-30"), (30, 50, "30-50"), (50, 70, "50-70"), (70, 101, "70+")]


def band(p: float | None) -> str:
    if p is None:
        return "no item"
    return next(label for lo, hi, label in BANDS if lo <= p < hi)


def jl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def common_prefix(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("case_dir")
    ap.add_argument("--submission", default="v8_b")
    args = ap.parse_args(argv)
    case = Path(args.case_dir).name
    d = config.CASES_BUILD / case
    sub = json.loads((d / f"submission_{args.submission}.json").read_text(encoding="utf-8"))["requirements"]
    diag_sub = json.loads((d / "submission_diag_all.json").read_text(encoding="utf-8"))["requirements"]
    rep = json.loads((d / f"judge_report_{args.submission}.json").read_text(encoding="utf-8"))
    drep = json.loads((d / "judge_report_diag_all.json").read_text(encoding="utf-8"))
    rows = {r["id"]: r for r in jl(d / "extracted.jsonl")}
    prob = load_review(case, "criteria", twice=config.REVIEW_BORDERLINE_TWICE)
    reasons = {(r["id"], r["index"]): r["reason"] for r in jl(d / "review_medium_criteria.jsonl")}
    para = {r["id"]: r for recs in load_base_records({q["source"]["regulation"] for q in diag_sub}).values() for r in recs}

    def key(q): return (q["evidence_id"], q["text"])
    index_of = {}                       # (evidence id, text) -> requirement index within its paragraph
    for r in rows.values():
        for i, t in enumerate(r["requirements"]):
            index_of.setdefault((r["id"], " ".join(t.split())), i)
    def p_of(q):
        i = index_of.get(key(q))
        return prob.get((q["evidence_id"], i)) if i is not None else None

    ours_ids = {key(q) for q in sub}
    matched_ours = {i for e in rep["expected_requirements"] if e["bucket"] == "matched" for i in e["our_ids"]}
    by_id = {q["id"]: q for q in sub}
    diag_by_id = {q["id"]: q for q in diag_sub}
    wrong = [q for q in sub if q["id"] not in matched_ours]

    # missed expected requirements: dropped by the review when the diag_all run matched them with a dropped item
    missed = []
    dexp = {e["expected_id"]: e for e in drep["expected_requirements"]}
    for e in rep["expected_requirements"]:
        if e["bucket"] == "matched":
            continue
        de = dexp.get(e["expected_id"], {})
        dropped = [diag_by_id[i] for i in de.get("our_ids", []) if de.get("bucket") == "matched"
                   and key(diag_by_id[i]) not in ours_ids]
        missed.append({**e, "dropped_item": dropped[0] if dropped else None})
    for m in missed:
        m["final_bucket"] = "dropped_by_review" if m["dropped_item"] else m["bucket"]

    # ---- labelling -------------------------------------------------------------------------------------------------
    def siblings(q):
        rec = para[q["evidence_id"]]
        cand = [o for o in sub if o["id"] != q["id"] and o["source"]["regulation"] == q["source"]["regulation"]]
        cand.sort(key=lambda o: -common_prefix(para[o["evidence_id"]]["paragraph"], rec["paragraph"]))
        return [{"paragraph": para[o["evidence_id"]]["paragraph"], "text": o["text"][:150],
                 "matches_expert": o["id"] in matched_ours} for o in cand[:6]]
    items = []
    for q in wrong:
        rec, row = para[q["evidence_id"]], rows[q["evidence_id"]]
        i = index_of.get(key(q))
        items.append({"kind": "wrong", "regulation": q["source"]["regulation"],
                      "section": (rec["breadcrumb"] or [rec["paragraph"]])[0], "paragraph": rec["paragraph"],
                      "category": row["category"], "confidence": row["confidence"], "text": q["text"],
                      "review_probability": p_of(q), "review_reason": reasons.get((q["evidence_id"], i), ""),
                      "nearby_listed": siblings(q), "_id": q["id"]})
    for m in missed:
        di = m["dropped_item"]
        items.append({"kind": "missed", "regulation": m["regulation"], "paragraph": m["paragraph"], "expected": m["expected"],
                      "judge_bucket": m["final_bucket"], "judge_note": m["detail"][:200],
                      "our_items_in_paragraph": m["ours"][:4],
                      "dropped_by_review": ({"text": di["text"], "probability": p_of(di)} if di else None),
                      "_id": m["expected_id"]})
    for k, it in enumerate(items, 1):
        it["n"] = k
    codes = {}
    for s in range(0, len(items), BATCH):
        chunk = items[s:s + BATCH]
        shown = [{k: v for k, v in it.items() if k != "_id"} for it in chunk]
        text = ("ITEMS (JSON):\n" + json.dumps(shown, ensure_ascii=False) + "\n\nGive one reason code for every n.")
        try:
            raw, _ = call_json(config.SCREEN_MODEL, "diagnose_system.md", [{"type": "text", "text": text}], SCHEMA,
                               label=f"diagnose:{s}", max_tokens=6000)
            codes.update({a["n"]: a for a in raw["items"]})
        except LLMFailure as exc:
            print("labelling failed for a batch:", exc)
    for it in items:
        a = codes.get(it["n"], {"code": "unlabelled", "note": ""})
        it["code"], it["note"] = a["code"], a["note"]
        if it["kind"] == "missed" and it["judge_bucket"] == "dropped_by_review":
            it["code"] = "threshold_drop"           # deterministic: pipeline had it, the review dropped it

    # ---- tables ----------------------------------------------------------------------------------------------------
    miss = [i for i in items if i["kind"] == "missed"]
    bad = [i for i in items if i["kind"] == "wrong"]
    print(f"submission {args.submission}: {len(sub)} requirements, {len(bad)} wrong picks; {len(rep['expected_requirements'])} expected, {len(miss)} missed\n")
    print("MISSED expected requirements by bucket:")
    for b, n in Counter(m["judge_bucket"] for m in miss).most_common():
        print(f"  {b:<24}{n:>4}")
    print("\nreason codes (missed | wrong):")
    cm, cw = Counter(i["code"] for i in miss), Counter(i["code"] for i in bad)
    for c in CODES:
        print(f"  {c:<26}{cm[c]:>4} | {cw[c]:>4}")
    print("\nthreshold_drop misses by review probability band:", dict(Counter(band(m["dropped_by_review"]["probability"]) for m in miss if m["dropped_by_review"])))
    print("\nwrong picks: reason code x review probability band")
    for c in CODES:
        sub_c = [i for i in bad if i["code"] == c]
        if sub_c:
            print(f"  {c:<26}" + "  ".join(f"{b[2]}:{sum(1 for i in sub_c if band(i['review_probability']) == b[2])}" for b in BANDS))
    print("\nwrong picks: reason code x extraction confidence")
    for c in CODES:
        sub_c = [i for i in bad if i["code"] == c]
        if sub_c:
            print(f"  {c:<26}" + "  ".join(f"{k}:{sum(1 for i in sub_c if i['confidence'] == k)}" for k in ("high", "medium", "low")))
    print("\nwrong picks and misses by regulation + top-level section (top 10):")
    grp = defaultdict(Counter)
    for i in items:
        grp[(i["regulation"], i.get("section", "-"))][i["code"]] += 1
    for (reg, sec), c in sorted(grp.items(), key=lambda kv: -sum(kv[1].values()))[:10]:
        print(f"  {sum(c.values()):>3}  {reg} | {str(sec)[:50]:<50} {dict(c.most_common(3))}")
    out = {"submission": args.submission, "missed": len(miss), "wrong": len(bad),
           "codes_missed": dict(cm), "codes_wrong": dict(cw), "items": items}
    (d / f"diagnosis_{args.submission}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("\n" + usage_summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
