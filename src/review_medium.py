#!/usr/bin/env python3
"""Stage 7: second opinion on requirements that are not high confidence.

    python src/review_medium.py cases/dev-rear-lamp                     # prompt = config.REVIEW_PROMPT
    python src/review_medium.py cases/dev-rear-lamp --prompt criteria   # or basic / both; --dry-run only estimates

Input:  every requirement of a kept regulation, outside the deselected sub-sections, whose paragraph has medium or low
        confidence (what any export setting could send to review: medium, medium in the category-drop set, low).
        High-confidence items are never reviewed.
Output: build/cases/<case_id>/review_medium.jsonl (basic prompt) or review_medium_criteria.jsonl (criteria prompt),
        one line per requirement (probability 0-100, reason). Items already in the file are not asked again, so a
        second run only pays for new items.

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
from export import REVIEW_FILES, make_submission
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


SYSTEM_FILES = {"basic": "review_system.md", "criteria": "review_criteria_system.md",
                "criteria_pass2": "review_criteria_system.md"}      # pass 2: same prompt, request differs by one line


def review_batch(profile: dict, decision: dict, items: list[dict], label: str, prompt: str = "basic") -> dict[int, dict]:
    shown = [{"n": k, "breadcrumb": it["breadcrumb"], "title": it["title"], "paragraph": it["paragraph"],
              "paragraph_text": clip(it["paragraph_text"], CLIP_TEXT), "parent_text": clip(it["parent_text"], CLIP_PARENT),
              "requirement": it["text"]} for k, it in enumerate(items)]
    text = (("Review pass 2.\n\n" if prompt == "criteria_pass2" else "")
            + f"PRODUCT PROFILE (JSON)\n{json.dumps(compact_profile(profile), ensure_ascii=False)}\n\n"
            f"REGULATION: {decision['regulation']}: {decision['title']}\nApplies because: {decision['reason']}\n\n"
            f"ITEMS (JSON):\n{json.dumps(shown, ensure_ascii=False)}\n\nGive probability and reason for every n.")
    raw, _ = call_json(config.SCREEN_MODEL, SYSTEM_FILES[prompt], [{"type": "text", "text": text}], REVIEW_SCHEMA,
                       label=label, max_tokens=4000)
    return {a["n"]: a for a in raw["items"]}


def collect(case_id: str, everything: bool = False) -> list[dict]:
    """Requirements the current config sends to review (medium confidence; medium in the category-drop set only with
    REVIEW_INSTEAD_OF_CATEGORY_DROP; low only with REVIEW_LOW_CONFIDENCE), among kept regulations and outside the
    deselected sub-sections. everything=True: every medium and low item, whatever the switches (to compare variants)."""
    trace: list = []
    make_submission(case_id, subsection_filter=True, review_medium=False, review_instead=False, review_low=False,
                    review_all=False, review_twice=False,
                    min_confidence="low", drop_unless_high=set(), trace=trace)
    return [{"id": pid, "index": i, "text": text, "confidence": conf, "category": cat}
            for pid, i, text, conf, cat in trace
            if config.REVIEW_ALL or conf != "high" and (everything or (conf == "medium" and (config.REVIEW_MEDIUM or config.REVIEW_INSTEAD_OF_CATEGORY_DROP)
                                                  and (cat not in config.DROP_CATEGORIES_UNLESS_HIGH
                                                       or config.REVIEW_INSTEAD_OF_CATEGORY_DROP))
                                   or (conf == "low" and config.REVIEW_LOW_CONFIDENCE))]


def read_done(path: Path) -> dict[tuple[str, int], dict]:
    if not path.is_file():
        return {}
    rows = (json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip())
    return {(r["id"], r["index"]): r for r in rows if not r.get("unanswered")}


def plan_case(case_id: str, prompt: str, everything: bool = False) -> tuple[list[dict], list[tuple], dict, dict, Path]:
    out_dir = config.CASES_BUILD / case_id
    profile = {k: v for k, v in json.loads((out_dir / "profile.json").read_text(encoding="utf-8")).items() if k != "meta"}
    decisions = {d["regulation"]: d for d in final_decisions(case_id) if d["applies"]}
    path = out_dir / REVIEW_FILES[prompt]
    done = read_done(path)
    items = collect(case_id, everything)
    by_reg = load_base_records({i["id"].split("|")[0] for i in items})
    records = {r["id"]: r for v in by_reg.values() for r in v}
    for it in items:
        rec = records[it["id"]]
        parent = records.get(rec.get("parent_id") or "")
        it.update(regulation=rec["regulation"], paragraph=rec["paragraph"], title=rec["title"] or "",
                  breadcrumb=rec["breadcrumb"], paragraph_text=rec["text"], parent_text=parent["text"] if parent else "")
    if prompt == "criteria_pass2":           # only the borderline items of pass 1
        first = read_done(out_dir / REVIEW_FILES["criteria"])
        lo, hi = config.BORDERLINE_RANGE
        items = [i for i in items if (i["id"], i["index"]) in first and lo <= first[(i["id"], i["index"])]["probability"] <= hi]
    todo = [i for i in items if (i["id"], i["index"]) not in done]
    batches = []
    for reg in sorted({i["regulation"] for i in todo}):
        mine = [i for i in todo if i["regulation"] == reg]
        batches += [(reg, mine[k:k + BATCH]) for k in range(0, len(mine), BATCH)]
    return items, batches, profile, decisions, path


def review_case(case_id: str, prompt: str = "basic", dry_run: bool = False, everything: bool = False) -> dict:
    items, batches, profile, decisions, path = plan_case(case_id, prompt, everything)
    done = read_done(path)
    info = {"prompt": prompt, "eligible_items": len(items), "already_reviewed": len(items) - sum(len(c) for _, c in batches),
            "new_items": sum(len(c) for _, c in batches), "batches": len(batches)}
    if dry_run:
        chars = sum(len(json.dumps(c, ensure_ascii=False)) for _, c in batches)
        info["est_input_tokens"] = int(chars / 3.2) + len(batches) * 2800        # items + profile + system prompt
        info["est_output_tokens"] = info["new_items"] * 55
        info["est_usd"] = round(info["est_input_tokens"] * 3 / 1e6 + info["est_output_tokens"] * 15 / 1e6, 2)
        return info

    def run(b):
        reg, chunk = b
        try:
            return review_batch(profile, decisions[reg], chunk, f"review[{prompt}]:{reg}:{chunk[0]['id'][-30:]}", prompt), None
        except LLMFailure as exc:
            return {}, str(exc)
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(run, batches))
    new_rows, failed = {}, 0
    for (reg, chunk), (answers, err) in zip(batches, results):
        for k, it in enumerate(chunk):
            a = answers.get(k)
            if a is None:       # recall first: an item without an answer is kept, and asked again next run
                failed += 1
                prob, reason = 100, f"no answer; kept ({err})"
            else:
                prob, reason = max(0, min(100, a["probability"])), " ".join(a["reason"].split())
            new_rows[(it["id"], it["index"])] = {"id": it["id"], "index": it["index"], "regulation": reg,
                                                 "paragraph": it["paragraph"], "text": it["text"],
                                                 "confidence": it["confidence"], "category": it["category"],
                                                 "probability": prob, "reason": reason, "unanswered": a is None}
    listed = {(it["id"], it["index"]) for it in items}
    with open(path, "w", encoding="utf-8") as fh:        # this run's items in export order, then earlier results kept
        for it in items:
            row = done.get((it["id"], it["index"])) or new_rows.get((it["id"], it["index"]))
            if row is not None:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        for key, row in done.items():
            if key not in listed:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    info["unanswered"] = failed
    return info


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("case_dir")
    ap.add_argument("--prompt", choices=["basic", "criteria", "criteria_pass2", "both"], help="default: config.REVIEW_PROMPT")
    ap.add_argument("--everything", action="store_true", help="review every medium and low item, whatever the switches")
    ap.add_argument("--dry-run", action="store_true", help="only count the items and batches and estimate the cost")
    args = ap.parse_args(argv)
    try:
        for prompt in (["basic", "criteria"] if args.prompt == "both" else [args.prompt or config.REVIEW_PROMPT]):
            print(review_case(Path(args.case_dir).name, prompt, args.dry_run, args.everything))
    except (OSError, LLMFailure) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(usage_summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
