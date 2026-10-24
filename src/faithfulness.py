#!/usr/bin/env python3
"""Stage 5b: check every extracted requirement against the paragraph it came from, before export.

    python src/faithfulness.py cases/dev-rear-lamp                # estimate the cost, no API call
    python src/faithfulness.py cases/dev-rear-lamp --run          # check, write faithfulness.jsonl

Input:  build/cases/<case_id>/extracted.jsonl, regulations.json, overlap.json, build/paragraphs.jsonl, build/tables.jsonl
Output: build/cases/<case_id>/faithfulness.jsonl   one line per checked requirement:
        id (paragraph id), index (position in that paragraph's requirement list), original, text (corrected, equal to
        original when unchanged), changed, note, status (answered | no_answer | failed)

Requirements of the regulations kept after UNKNOWN_POLICY and overlap are checked, whatever their confidence or category,
so that every export variant can use the result. The model (SCREEN_MODEL, prompts/faithfulness_system.md) sees the
paragraph text, its headings, the parent text when the requirement may use it, the table cells, and the requirements of
that paragraph, in batches of about BATCH_REQS requirements. It does not extract again: it only removes what the source
does not say and restores exceptions, tolerances, alternatives and conditions the requirement left out.
export.py replaces the text of a requirement with the corrected one when FAITHFULNESS_CHECK is on.

A batch that fails is split in half; a single paragraph that still fails, or that the model skips, keeps its original
text (status failed / no_answer, counted in the log, never silently dropped).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import config
import llm
from extract import PARENT_CHARS, TABLE_CHARS, load_tables
from llm import LLMFailure, object_schema
from regulation_set import kept_regulations
from select_sections import load_base_records

BATCH_REQS = 20
WORKERS = 4
PRICE_IN, PRICE_OUT = 2.00, 10.00      # USD per million tokens, claude-sonnet-5-5
BUDGET_USD = 2.00                      # a little under EUR 2
EST_OVERHEAD_OUT = 400                 # per batch: thinking and structure
S = {"type": "string"}
SCHEMA = object_schema({"results": {"type": "array", "items": object_schema({
    "id": S, "text": S, "changed": {"type": "string", "enum": ["yes", "no"]}, "note": S})}})


def norm(text: str) -> str:
    return " ".join(text.split())


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


# ---------------------------------------------------------------------------------------------------------------------
# Batches
# ---------------------------------------------------------------------------------------------------------------------

def uses_parent(r: dict, parent: dict | None) -> bool:
    """The parent text is sent when the paragraph cannot be read alone."""
    if parent is None:
        return False
    return bool(r["kind"] == "table" or r["inherits_obligation"] or r["heading_value_item"] or not r["has_obligation"]
                or parent["text"].rstrip().endswith(":"))


def paragraph_payload(row: dict, r: dict, by_id: dict, tables: dict, counter: list[int]) -> tuple[dict, dict]:
    """(payload for the model, {local id: (row id, index)})."""
    parent = by_id.get(r["parent_id"]) if r["parent_id"] else None
    ids, reqs = {}, []
    for i, text in enumerate(row["requirements"]):
        counter[0] += 1
        qid = f"q{counter[0]}"
        ids[qid] = (row["id"], i)
        reqs.append({"id": qid, "text": text})
    p = {"paragraph": r["paragraph"], "breadcrumb": r["breadcrumb"], "title": r["title"] or "", "source_text": r["text"]}
    if uses_parent(r, parent):
        p["parent_text"] = parent["text"][:PARENT_CHARS]
    owns_entry = any(by_id[c]["kind"] == "table" for c in r["children_ids"] if c in by_id)
    owned = [tables[t] for t in r["table_ids"] if t in tables]
    if owned and not owns_entry:
        cells = json.dumps([{"caption": t["caption"], "cells": t["cells"]} for t in owned], ensure_ascii=False)
        p["table"] = cells[:TABLE_CHARS] + ("…(truncated)" if len(cells) > TABLE_CHARS else "")
    p["requirements"] = reqs
    return p, ids


def make_batches(rows: list[dict], by_id: dict, tables: dict) -> list[dict]:
    """Consecutive paragraphs of one regulation, about BATCH_REQS requirements per batch; a paragraph is never split."""
    batches, cur = [], None
    for row in rows:
        n = len(row["requirements"])
        if cur is None or cur["regulation"] != row["regulation"] or (cur["n"] and cur["n"] + n > BATCH_REQS):
            cur = {"regulation": row["regulation"], "rows": [], "n": 0}
            batches.append(cur)
        cur["rows"].append(row)
        cur["n"] += n
    return batches


def batch_text(rows: list[dict], by_id: dict, tables: dict) -> tuple[str, dict]:
    counter, ids, items = [0], {}, []
    for row in rows:
        p, i = paragraph_payload(row, by_id[row["id"]], by_id, tables, counter)
        items.append(p)
        ids.update(i)
    text = (f"REGULATION: {rows[0]['regulation']}\nPARAGRAPHS WITH THEIR REQUIREMENTS (JSON, {len(ids)} requirements):\n"
            f"{json.dumps(items, ensure_ascii=False)}\n\nAnswer for every requirement id.")
    return text, ids


# ---------------------------------------------------------------------------------------------------------------------
# One batch
# ---------------------------------------------------------------------------------------------------------------------

class Run:
    def __init__(self, by_id: dict, tables: dict, budget: float):
        self.by_id, self.tables, self.budget = by_id, tables, budget
        self.log = Counter()

    def spent(self) -> float:
        return spent_usd()

    def ask(self, rows: list[dict]) -> list[dict]:
        if self.spent() >= self.budget:
            raise SystemExit(f"stopped: spend {self.spent():.3f} USD reached the budget of {self.budget:.2f} USD "
                             f"(finished calls are cached; run again to continue)")
        text, ids = batch_text(rows, self.by_id, self.tables)
        raw, _ = llm.call_json(config.SCREEN_MODEL, "faithfulness_system.md", [{"type": "text", "text": text}], SCHEMA,
                               label=f"faithfulness:{rows[0]['regulation']}:{rows[0]['paragraph']}", max_tokens=12000)
        by_qid = {a["id"]: a for a in raw["results"]}
        out, originals = [], {}
        for row in rows:
            for i, orig in enumerate(row["requirements"]):
                originals[(row["id"], i)] = orig
        for qid, (rid, i) in ids.items():
            orig = originals[(rid, i)]
            a = by_qid.get(qid)
            if a is None:
                self.log["no_answer"] += 1
                out.append({"id": rid, "index": i, "original": orig, "text": orig, "changed": False,
                            "note": "the model returned no entry", "status": "no_answer"})
                continue
            new = norm(a["text"])
            changed = a["changed"] == "yes" and bool(new) and new != norm(orig)
            self.log["changed" if changed else "unchanged"] += 1
            out.append({"id": rid, "index": i, "original": orig, "text": new if changed else orig, "changed": changed,
                        "note": " ".join(a["note"].split()), "status": "answered"})
        return out

    def run(self, rows: list[dict]) -> list[dict]:
        try:
            return self.ask(rows)
        except LLMFailure as exc:
            if len(rows) == 1:
                self.log["failed"] += len(rows[0]["requirements"])
                return [{"id": rows[0]["id"], "index": i, "original": t, "text": t, "changed": False, "note": str(exc),
                         "status": "failed"} for i, t in enumerate(rows[0]["requirements"])]
            self.log["splits"] += 1
            mid = len(rows) // 2
            return self.run(rows[:mid]) + self.run(rows[mid:])


def spent_usd() -> float:
    s = llm.STATS
    return s["input_tokens"] * PRICE_IN / 1e6 + s["output_tokens"] * PRICE_OUT / 1e6


# ---------------------------------------------------------------------------------------------------------------------
# Stage
# ---------------------------------------------------------------------------------------------------------------------

def load_inputs(case_id: str):
    out_dir = config.CASES_BUILD / case_id
    rows = read_jsonl(out_dir / "extracted.jsonl")
    keep = set(kept_regulations(case_id))
    rows = [r for r in rows if r["requirements"] and r["regulation"] in keep]
    by_reg = load_base_records({r["regulation"] for r in rows})
    by_id = {r["id"]: r for recs in by_reg.values() for r in recs}
    tables = load_tables({t for r in rows for t in by_id[r["id"]]["table_ids"]})
    return out_dir, rows, by_id, tables


def estimate(batches: list[dict], by_id: dict, tables: dict) -> dict:
    system = llm.prompt_text("faithfulness_system.md")
    in_chars = out_chars = 0
    cached = 0
    for b in batches:
        text, _ = batch_text(b["rows"], by_id, tables)
        in_chars += len(system) + len(json.dumps(SCHEMA)) + len(text)
        out_chars += sum(len(t) for r in b["rows"] for t in r["requirements"]) + 60 * b["n"]
        key = llm.cache_key(config.SCREEN_MODEL, system, [{"type": "text", "text": text}], SCHEMA)
        cached += (config.CACHE_DIR / f"{key}.json").is_file()
    in_tok = int(in_chars / 3.5)                                   # deliberately high
    out_tok = int(out_chars / 3.5) + EST_OVERHEAD_OUT * len(batches)
    usd = in_tok * PRICE_IN / 1e6 + out_tok * PRICE_OUT / 1e6
    return {"batches": len(batches), "requirements": sum(b["n"] for b in batches), "already_cached": cached,
            "input_tokens": in_tok, "output_tokens": out_tok, "estimated_usd": round(usd, 3)}


def check_case(case_id: str, budget: float = BUDGET_USD) -> dict:
    out_dir, rows, by_id, tables = load_inputs(case_id)
    batches = make_batches(rows, by_id, tables)
    run = Run(by_id, tables, budget)
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(lambda b: run.run(b["rows"]), batches))
    flat = [x for res in results for x in res]
    with open(out_dir / "faithfulness.jsonl", "w", encoding="utf-8") as fh:
        for x in flat:
            fh.write(json.dumps(x, ensure_ascii=False) + "\n")
    return {"results": flat, "log": dict(run.log), "batches": len(batches)}


def load_corrections(case_id: str, extracted_rows: list[dict]) -> dict[tuple[str, int], str]:
    """{(paragraph id, requirement index): corrected text} for the changed requirements. Fails loudly when the file is
    missing or was made from a different extraction (an original text no longer matches)."""
    path = config.CASES_BUILD / case_id / "faithfulness.jsonl"
    if not path.is_file():
        raise SystemExit(f"error: {path} is missing; run `python src/faithfulness.py cases/{case_id} --run` "
                         f"or set FAITHFULNESS_CHECK = False in src/config.py")
    texts = {(r["id"], i): t for r in extracted_rows for i, t in enumerate(r["requirements"])}
    out = {}
    for x in read_jsonl(path):
        if texts.get((x["id"], x["index"])) != x["original"]:
            raise SystemExit(f"error: {path} does not match extracted.jsonl (paragraph {x['id']}, requirement "
                             f"{x['index']}); run src/faithfulness.py again")
        if x["changed"]:
            out[(x["id"], x["index"])] = x["text"]
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("case_dir")
    ap.add_argument("--run", action="store_true", help="make the API calls (without it only the estimate is printed)")
    ap.add_argument("--budget", type=float, default=BUDGET_USD, help="stop when real spend reaches this many USD")
    args = ap.parse_args(argv)
    case_id = Path(args.case_dir).name
    _, rows, by_id, tables = load_inputs(case_id)
    est = estimate(make_batches(rows, by_id, tables), by_id, tables)
    print(f"{est['requirements']} requirements in {est['batches']} batches ({est['already_cached']} already cached): "
          f"~{est['input_tokens']} input + ~{est['output_tokens']} output tokens = ~{est['estimated_usd']} USD with "
          f"{config.SCREEN_MODEL} (${PRICE_IN}/${PRICE_OUT} per MTok); budget {args.budget:.2f} USD")
    if not args.run:
        print("no API call made; add --run to check")
        return 0
    if est["estimated_usd"] > args.budget:
        raise SystemExit(f"error: the estimate is above the budget of {args.budget:.2f} USD; nothing was called")
    try:
        res = check_case(case_id, args.budget)
    except LLMFailure as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    changed = sum(x["changed"] for x in res["results"])
    print(f"checked {len(res['results'])} requirements, changed {changed}; log {res['log']}")
    print(f"{llm.usage_summary()}  real spend ~{spent_usd():.3f} USD")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
