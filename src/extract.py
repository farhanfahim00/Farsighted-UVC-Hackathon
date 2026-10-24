#!/usr/bin/env python3
"""Stage 5: applicability and atomic obligations. Candidates are batched by section (about 15-25 paragraphs, a
paragraph is never split), and one SCREEN_MODEL call per batch decides for every paragraph whether it applies and
writes its requirements. Citations never come from the model: it answers with batch-local keys that Python maps back
to index records.

    python src/extract.py cases/dev-rear-lamp

Input:  build/cases/<case_id>/profile.json, candidates.jsonl, build/paragraphs.jsonl, build/tables.jsonl
Output: build/cases/<case_id>/extracted.jsonl   one line per candidate paragraph:
        id, regulation, version, paragraph, unit, status (answered | no_answer | failed), applies, reason,
        confidence (high | medium | low), category (see prompts/extract_system.md), requirements (list of texts), error

Second look: a paragraph that applied, contains obligation words, and still has no requirement is asked once more
(SECOND_LOOK) so that conditional obligations are not lost to an empty list.

Failure handling: unknown keys in an answer are dropped and logged; paragraphs the model skipped are asked again once;
a batch that fails or is cut off is split in half and retried; only a single paragraph that still fails is recorded as
"failed" (never dropped silently).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import config
from llm import LLMFailure, call_json, object_schema, usage_summary
from select_sections import load_base_records

WORKERS = 6
BATCH_MIN, BATCH_MAX = 15, 25
BATCH_CHARS = 36_000          # soft cap on the paragraph text of one batch
PARENT_CHARS = 700
TABLE_CHARS = 6_000
S = {"type": "string"}
CATEGORIES = ["device_requirement", "installation_requirement", "test_requirement", "test_method",
              "conformity_of_production", "administrative", "other_device_type", "other"]
CONFIDENCES = ["high", "medium", "low"]
EXTRACT_SCHEMA = object_schema({"paragraphs": {"type": "array", "items": object_schema({
    "key": S, "applies": {"type": "string", "enum": ["yes", "no"]},
    "confidence": {"type": "string", "enum": CONFIDENCES}, "category": {"type": "string", "enum": CATEGORIES},
    "reason": S, "requirements": {"type": "array", "items": object_schema({"text": S})}})}})


# ---------------------------------------------------------------------------------------------------------------------
# Batching and payload
# ---------------------------------------------------------------------------------------------------------------------

def make_batches(items: list[dict], size_of=lambda it: len(it["text"])) -> list[list[dict]]:
    """Split one section's paragraphs (in document order) into batches of about 15-25 paragraphs of similar size.
    A paragraph is never split; a batch also ends when its text would exceed BATCH_CHARS."""
    n = len(items)
    if n == 0:
        return []
    parts = max(1, round(n / 20))
    target = -(-n // parts)                          # items per batch, rounded up (<= 25 when n <= 25 * parts)
    batches, cur, chars = [], [], 0
    for it in items:
        size = size_of(it)
        if cur and (len(cur) >= max(target, 1) or len(cur) >= BATCH_MAX or chars + size > BATCH_CHARS):
            batches.append(cur)
            cur, chars = [], 0
        cur.append(it)
        chars += size
    if cur:
        batches.append(cur)
    return batches


def signals_of(r: dict) -> dict:
    return {k: v for k, v in r["obligation_signals"].items() if v}


def hints_of(r: dict, parent: dict | None) -> list[str]:
    hints = []
    if r["heading_value_item"]:
        hints.append("heading_value_item: a label with its value; the obligation is implicit")
    if r["inherits_obligation"]:
        hints.append(f"inherits_obligation ({r['inherits_obligation_reason']}): the parent carries the obligation")
    if parent is not None and parent["text"].rstrip().endswith(":"):
        hints.append("the parent text ends with ':' and introduces this item")
    if r["annex_kind"] == "marking_example":
        hints.append("annex of approval-mark examples")
    return hints


def payload(r: dict, key: str, by_id: dict, tables: dict) -> dict:
    parent = by_id.get(r["parent_id"]) if r["parent_id"] else None
    p = {"key": key, "label": r["paragraph"], "breadcrumb": r["breadcrumb"], "title": r["title"] or "",
         "text": r["text"], "parent_text": (parent["text"][:PARENT_CHARS] if parent else ""),
         "signals": signals_of(r), "conditions": sorted({c["signal"] for c in r["condition_signals"]}),
         "hints": hints_of(r, parent)}
    owns_entry = any(by_id[c]["kind"] == "table" for c in r["children_ids"] if c in by_id)
    owned = [tables[t] for t in r["table_ids"] if t in tables]
    if r["kind"] == "table":
        p["kind"] = "table (belongs to the paragraph in parent_text)"
    if owns_entry:                     # its table is a separate paragraph in the batch (or a neighbouring batch)
        p["hints"].append("its table is extracted as a separate table paragraph; do not copy the table values here")
    elif owned:
        cells = json.dumps([{"caption": t["caption"], "cells": t["cells"]} for t in owned], ensure_ascii=False)
        p["table"] = cells[:TABLE_CHARS] + ("…(truncated)" if len(cells) > TABLE_CHARS else "")
    return p


def load_tables(wanted: set[str]) -> dict[str, dict]:
    out = {}
    path = config.BUILD / "tables.jsonl"
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            t = json.loads(line)
            if t["id"] in wanted:
                out[t["id"]] = t
    return out


# ---------------------------------------------------------------------------------------------------------------------
# One batch
# ---------------------------------------------------------------------------------------------------------------------

SECOND_LOOK = ("SECOND LOOK: in a first pass you decided that each of these paragraphs applies but wrote no requirement, "
               "although its text contains obligation words. Look again. If the paragraph states an obligation, even one "
               "that holds only under a condition (an option, or a case the profile leaves open), write it as a requirement "
               "that states the condition, with confidence medium or low. If the paragraph only introduces the items "
               "below it (their own paragraphs carry the obligation) or states nothing for the product, leave the list "
               "empty.\n\n")


class Run:
    """Shared state of one extraction run: records, profile, and counters for the log."""

    def __init__(self, profile: dict, by_id: dict, tables: dict, second_look: bool = False):
        self.profile, self.by_id, self.tables = profile, by_id, tables
        self.second_look = second_look      # re-ask for paragraphs that applied but gave no requirement
        self.log = Counter()

    def ask(self, batch: list[dict]) -> dict[str, dict]:
        """One API call for a batch of candidates. Returns {candidate id: answer} for the paragraphs that were
        answered; raises LLMFailure when the call fails or is truncated."""
        keys = {f"p{i}": c for i, c in enumerate(batch, start=1)}
        items = [payload(self.by_id[c["id"]], k, self.by_id, self.tables) for k, c in keys.items()]
        text = (f"PRODUCT PROFILE (JSON)\n{json.dumps(self.profile, ensure_ascii=False)}\n\n"
                f"REGULATION: {batch[0]['regulation']}   SECTION: {batch[0]['unit']}\n"
                f"PARAGRAPHS (JSON, {len(items)}):\n{json.dumps(items, ensure_ascii=False)}\n\n"
                + (SECOND_LOOK if self.second_look else "") + "Answer for every key.")
        raw, _ = call_json(config.SCREEN_MODEL, "extract_system.md", [{"type": "text", "text": text}],
                           EXTRACT_SCHEMA, label=f"{'second_look' if self.second_look else 'extract'}:{batch[0]['regulation']}:{batch[0]['unit']}",
                           max_tokens=12000)
        out: dict[str, dict] = {}
        for ans in raw["paragraphs"]:
            c = keys.get(ans["key"])
            if c is None:
                self.log["unknown_keys_dropped"] += 1
                continue
            reqs = [" ".join(r["text"].split()) for r in ans["requirements"] if r["text"].strip()]
            if ans["applies"] == "no" and reqs:
                self.log["no_with_requirements_kept"] += 1      # recall first: the requirements are kept
            out[c["id"]] = {"applies": ans["applies"] == "yes" or bool(reqs), "reason": " ".join(ans["reason"].split()),
                            "confidence": ans["confidence"], "category": ans["category"], "requirements": reqs}
        return out

    def run_batch(self, batch: list[dict], retry_missing: bool = True) -> dict[str, dict]:
        """Answers for every paragraph of the batch: split on failure, ask again for skipped paragraphs."""
        if not batch:
            return {}
        self.log["batches"] += 1
        try:
            answers = self.ask(batch)
        except LLMFailure as exc:
            if len(batch) == 1:
                self.log["paragraphs_failed"] += 1
                return {batch[0]["id"]: {"status": "failed", "error": str(exc)}}
            self.log["splits"] += 1
            mid = len(batch) // 2
            return {**self.run_batch(batch[:mid]), **self.run_batch(batch[mid:])}
        missing = [c for c in batch if c["id"] not in answers]
        if missing and retry_missing and len(missing) < len(batch):
            self.log["retried_missing"] += len(missing)
            answers.update({k: v for k, v in self.run_batch(missing, retry_missing=False).items()})
        elif missing and retry_missing and len(batch) > 1:    # nothing came back at all: split rather than repeat
            self.log["splits"] += 1
            mid = len(batch) // 2
            answers.update(self.run_batch(batch[:mid]))
            answers.update(self.run_batch(batch[mid:]))
        for c in batch:
            if c["id"] not in answers:
                self.log["no_answer"] += 1
                answers[c["id"]] = {"status": "no_answer", "error": "the model returned no entry for this paragraph"}
        return answers


def extract_case(case_id: str, out_dir: Path | None = None, only: set[str] | None = None) -> dict:
    """only: extract just the candidates of these regulations (partial run; extracted.jsonl then holds only those)."""
    out_dir = out_dir or config.CASES_BUILD / case_id
    profile = json.loads((out_dir / "profile.json").read_text(encoding="utf-8"))
    profile = {k: v for k, v in profile.items() if k != "meta"}
    candidates = [json.loads(l) for l in (out_dir / "candidates.jsonl").read_text(encoding="utf-8").splitlines() if l]
    if only:
        candidates = [c for c in candidates if c["regulation"] in only]
    regs = {c["regulation"] for c in candidates}
    by_reg = load_base_records(regs)
    by_id = {r["id"]: r for recs in by_reg.values() for r in recs}
    skipped = 0
    if config.SKIP_DESELECTED_BEFORE_EXTRACTION:        # export would drop these anyway (same sub-section filter)
        from export import load_deselected
        from select_sections import in_subsection
        des = load_deselected(case_id, config.SPEC_LISTS_COMPLETE)
        kept = [c for c in candidates if not any(in_subsection(by_id[c["id"]], s) for s in des.get(c["regulation"], ()))]
        skipped, candidates = len(candidates) - len(kept), kept
    tables = load_tables({t for c in candidates for t in by_id[c["id"]]["table_ids"]})
    run = Run(profile, by_id, tables)

    # sections in candidate order, batches inside each section
    sections: dict[tuple, list[dict]] = {}
    for c in candidates:
        sections.setdefault((c["regulation"], c["unit"]), []).append(c)
    batches = [b for items in sections.values() for b in make_batches(items, lambda it: len(by_id[it["id"]]["text"]))]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(run.run_batch, batches))
    answers = {k: v for res in results for k, v in res.items()}

    # second look: paragraphs that applied, contain obligation words, and still have no requirement (one more ask)
    again = [c for c in candidates if answers[c["id"]].get("applies") and not answers[c["id"]].get("requirements")
             and by_id[c["id"]]["has_obligation"]]
    second = Run(profile, by_id, tables, second_look=True)
    gained = set()
    for items in (again[i:i + BATCH_MAX] for i in range(0, len(again), BATCH_MAX)):
        for cid, a in second.run_batch(items).items():
            if a.get("requirements"):
                answers[cid] = {**a, "second_look": True}
                gained.add(cid)
    run.log.update({"second_look_asked": len(again), "second_look_gained": len(gained)})

    rows = []
    for c in candidates:
        a = answers[c["id"]]
        status = a.get("status", "answered")
        rows.append({"id": c["id"], "regulation": c["regulation"], "version": c["version"], "paragraph": c["paragraph"],
                     "unit": c["unit"], "status": status, "applies": a.get("applies"), "reason": a.get("reason", ""),
                     "confidence": a.get("confidence"), "category": a.get("category"),
                     "second_look": bool(a.get("second_look")),
                     "requirements": a.get("requirements", []), "error": a.get("error", "")})
    with open(out_dir / "extracted.jsonl", "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    run.log["candidates_skipped_deselected"] = skipped
    return {"rows": rows, "log": dict(run.log), "batches_planned": len(batches)}


def summarize(res: dict) -> None:
    rows = res["rows"]
    per = {}
    for r in rows:
        d = per.setdefault(r["regulation"], Counter())
        d["candidates"] += 1
        d["applies"] += bool(r["applies"])
        d["with_requirements"] += bool(r["requirements"])
        d["requirements"] += len(r["requirements"])
        d["not_answered"] += r["status"] != "answered"
    print(f"{'regulation':<10} {'candidates':>10} {'applies':>8} {'with reqs':>10} {'requirements':>13} {'not answered':>13}")
    for reg, d in per.items():
        print(f"{reg:<10} {d['candidates']:>10} {d['applies']:>8} {d['with_requirements']:>10} {d['requirements']:>13} "
              f"{d['not_answered']:>13}")
    print(f"total requirements: {sum(d['requirements'] for d in per.values())}   "
          f"batches planned {res['batches_planned']}   log {res['log']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("case_dir")
    ap.add_argument("--only", help="comma-separated regulations: extract just these (partial run)")
    args = ap.parse_args(argv)
    try:
        res = extract_case(Path(args.case_dir).name, only=set(args.only.split(",")) if args.only else None)
    except (OSError, LLMFailure) as exc:
        print(f"error: extraction failed: {exc}", file=sys.stderr)
        return 1
    summarize(res)
    print(usage_summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
