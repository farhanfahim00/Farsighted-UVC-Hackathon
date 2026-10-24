#!/usr/bin/env python3
"""Stage 7: second opinion on medium-confidence requirements.

    python src/review_medium.py cases/dev-rear-lamp

Input:  the requirements that survive the v3 filters and the sub-section filter (SPEC_LISTS_COMPLETE False and True;
        the True set is normally a subset of the False set, so its results are reused) and whose paragraph has
        confidence "medium". High-confidence items are never reviewed.
Output: build/cases/<case_id>/review_medium.jsonl   one line per reviewed requirement (probability 0-100, reason)

Each batch (about BATCH items, one regulation) goes to SCREEN_MODEL once; responses are cached by src/llm.py.
Export drops a reviewed requirement when its probability is below config.REVIEW_DROP_BELOW (see export.py).
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import config
from export import make_submission
from llm import LLMFailure, call_json, object_schema, usage_summary
from regulation_set import final_decisions
from select_sections import load_base_records

BATCH = 10
WORKERS = 6
CLIP_TEXT, CLIP_PARENT = 1200, 600
S = {"type": "string"}
REVIEW_SCHEMA = object_schema({"items": {"type": "array", "items": object_schema(
    {"n": {"type": "integer"}, "probability": {"type": "integer"}, "reason": S})}})


def compact_profile(profile: dict) -> dict:
    facts = profile.get("facts", [])
    return {"product": profile.get("product_name"), "kind": profile.get("product_kind"),
            "vehicle_categories": profile.get("vehicle_categories"),
            "parts": [{"name": p["name"], "function": p["function"]} for p in profile.get("parts", [])],
            "stated": [f"{f['feature']}: {f['evidence'][:100]}" for f in facts if f["status"] == "stated"],
            "explicitly_absent": [f["feature"] for f in facts if f["status"] == "explicitly_absent"],
            "uncertain_facts": [f"{f['feature']} ({f['status']})" for f in facts
                                if f["status"] in ("representative", "observed_uncertain")],
            "unknown_features": [f["feature"] for f in facts if f["status"] == "unknown"]}


def clip(text: str | None, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[:n - 1].rsplit(" ", 1)[0] + "…"


def review_batch(profile: dict, decision: dict, items: list[dict], label: str) -> dict[int, dict]:
    shown = [{"n": k, "breadcrumb": it["breadcrumb"], "title": it["title"], "paragraph": it["paragraph"],
              "paragraph_text": clip(it["paragraph_text"], CLIP_TEXT), "parent_text": clip(it["parent_text"], CLIP_PARENT),
              "requirement": it["text"]} for k, it in enumerate(items)]
    text = (f"PRODUCT PROFILE (JSON)\n{json.dumps(compact_profile(profile), ensure_ascii=False)}\n\n"
            f"REGULATION: {decision['regulation']}: {decision['title']}\nApplies because: {decision['reason']}\n\n"
            f"ITEMS (JSON):\n{json.dumps(shown, ensure_ascii=False)}\n\nGive probability and reason for every n.")
    raw, _ = call_json(config.SCREEN_MODEL, "review_system.md", [{"type": "text", "text": text}], REVIEW_SCHEMA,
                       label=label, max_tokens=4000)
    return {a["n"]: a for a in raw["items"]}


def collect(case_id: str) -> list[dict]:
    """Medium-confidence requirements surviving the filters, in either SPEC_LISTS_COMPLETE setting."""
    seen: dict[tuple[str, int], dict] = {}
    for complete in (False, True):
        trace: list = []
        make_submission(case_id, subsection_filter=True, spec_lists_complete=complete, review_medium=False, trace=trace)
        for pid, i, text, conf in trace:
            if conf == "medium":
                e = seen.setdefault((pid, i), {"id": pid, "index": i, "text": text, "in_false": False, "in_true": False})
                e["in_" + str(complete).lower()] = True
    return list(seen.values())


def review_case(case_id: str, dry_run: bool = False) -> dict:
    out_dir = config.CASES_BUILD / case_id
    profile = {k: v for k, v in json.loads((out_dir / "profile.json").read_text(encoding="utf-8")).items() if k != "meta"}
    decisions = {d["regulation"]: d for d in final_decisions(case_id) if d["applies"]}
    items = collect(case_id)
    by_reg = load_base_records({i["id"].split("|")[0] for i in items})
    records = {r["id"]: r for v in by_reg.values() for r in v}
    for it in items:
        rec = records[it["id"]]
        parent = records.get(rec.get("parent_id") or "")
        it.update(regulation=rec["regulation"], paragraph=rec["paragraph"], title=rec["title"] or "",
                  breadcrumb=rec["breadcrumb"], paragraph_text=rec["text"], parent_text=parent["text"] if parent else "")
    batches = []
    for reg in sorted({i["regulation"] for i in items}):
        mine = [i for i in items if i["regulation"] == reg]
        batches += [(reg, mine[k:k + BATCH]) for k in range(0, len(mine), BATCH)]
    info = {"items": len(items), "batches": len(batches), "only_in_true": sum(1 for i in items if not i["in_false"])}
    if dry_run:
        return info
    def run(b):
        reg, chunk = b
        try:
            return review_batch(profile, decisions[reg], chunk, f"review:{reg}:{chunk[0]['id'][-30:]}"), None
        except LLMFailure as exc:
            return {}, str(exc)
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(run, batches))
    failed = 0
    with open(out_dir / "review_medium.jsonl", "w", encoding="utf-8") as fh:
        for (reg, chunk), (answers, err) in zip(batches, results):
            for k, it in enumerate(chunk):
                a = answers.get(k)
                if a is None:       # recall first: an item without an answer is kept
                    failed += 1
                    prob, reason = 100, f"no answer; kept ({err})"
                else:
                    prob, reason = max(0, min(100, a["probability"])), " ".join(a["reason"].split())
                fh.write(json.dumps({"id": it["id"], "index": it["index"], "regulation": reg, "paragraph": it["paragraph"],
                                     "text": it["text"], "probability": prob, "reason": reason, "unanswered": a is None,
                                     "in_false": it["in_false"], "in_true": it["in_true"]}, ensure_ascii=False) + "\n")
    info["unanswered"] = failed
    return info


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("case_dir")
    ap.add_argument("--dry-run", action="store_true", help="only count the items and batches")
    args = ap.parse_args(argv)
    try:
        print(review_case(Path(args.case_dir).name, args.dry_run))
    except (OSError, LLMFailure) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(usage_summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
