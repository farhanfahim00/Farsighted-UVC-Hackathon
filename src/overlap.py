#!/usr/bin/env python3
"""Stage 3b: overlapping regulations. After screening, one SCREEN_MODEL call sees every applying regulation (title,
cited version, scope) and flags pairs where an older regulation covers the same devices or functions as a newer,
consolidated one. The older one is dropped (config.DROP_OVERLAPPING) and the reason recorded.

    python src/overlap.py cases/dev-rear-lamp

Input:  build/cases/<case_id>/regulations.json (via regulation_set), build/regulation_scopes.json
Output: build/cases/<case_id>/overlap.json   {pairs (all the model flagged), dropped {older: {newer, reason}}, ignored}

Nothing here knows any regulation name. Pairs below config.OVERLAP_MIN_CONFIDENCE are recorded but not applied.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import config
from llm import LLMFailure, call_json, object_schema, usage_summary
from regulation_set import final_decisions

S = {"type": "string"}
OVERLAP_SCHEMA = object_schema({"pairs": {"type": "array", "items": object_schema({
    "newer": S, "older": S, "shared_scope": S, "reason": S,
    "confidence": {"type": "string", "enum": ["high", "medium", "low"]}})}})
SCOPE_CHARS = 1500
RANK = {"low": 0, "medium": 1, "high": 2}


def overlap_content(decisions: list[dict], scopes: dict) -> list[dict]:
    items = [{"regulation": d["regulation"], "title": d["title"], "version": d["version"],
              "scope": scopes.get(d["regulation"], {}).get("text", "")[:SCOPE_CHARS]} for d in decisions]
    text = (f"REGULATIONS THAT APPLY (JSON, {len(items)}):\n{json.dumps(items, ensure_ascii=False)}\n\n"
            "Flag the overlapping pairs.")
    return [{"type": "text", "text": text}]


def validate_pairs(raw_pairs: list[dict], names: set[str]) -> tuple[dict, list[dict]]:
    """({older: {newer, reason, confidence}} to apply, [ignored pairs with the reason])."""
    dropped, ignored = {}, []
    claims = {(p["newer"], p["older"]) for p in raw_pairs}
    for p in raw_pairs:
        why = None
        if p["newer"] not in names or p["older"] not in names:
            why = "name is not an applying regulation"
        elif p["newer"] == p["older"]:
            why = "same regulation"
        elif (p["older"], p["newer"]) in claims:
            why = "claimed in both directions"
        elif RANK[p["confidence"]] < RANK[config.OVERLAP_MIN_CONFIDENCE]:
            why = f"{p['confidence']} confidence (needs {config.OVERLAP_MIN_CONFIDENCE}); regulation kept"
        if why:
            ignored.append({**p, "ignored_because": why})
        else:
            dropped[p["older"]] = {"newer": p["newer"], "reason": " ".join(p["reason"].split()),
                                   "shared_scope": p["shared_scope"], "confidence": p["confidence"]}
    return dropped, ignored


def find_overlaps(case_id: str) -> dict:
    out_dir = config.CASES_BUILD / case_id
    decisions = [d for d in final_decisions(case_id, drop_overlap=False) if d["applies"]]
    scopes = json.loads(config.SCOPES_JSON.read_text(encoding="utf-8"))
    raw, _ = call_json(config.SCREEN_MODEL, "overlap_system.md", overlap_content(decisions, scopes), OVERLAP_SCHEMA,
                       label=f"overlap:{case_id}", max_tokens=6000)
    dropped, ignored = validate_pairs(raw["pairs"], {d["regulation"] for d in decisions})
    result = {"case_id": case_id, "considered": [d["regulation"] for d in decisions], "pairs": raw["pairs"],
              "dropped": dropped, "ignored": ignored}
    (out_dir / "overlap.json").write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return result


def print_result(res: dict) -> None:
    print(f"considered {len(res['considered'])} regulations; the model flagged {len(res['pairs'])} pairs")
    for older, info in res["dropped"].items():
        print(f"  DROP {older:<9} <- kept {info['newer']:<9} [{info['confidence']}] shared: {info['shared_scope']}")
        print(f"       {info['reason']}")
    for p in res["ignored"]:
        print(f"  ignored ({p['ignored_because']}): {p['older']} older than {p['newer']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("case_dir")
    args = ap.parse_args(argv)
    try:
        print_result(find_overlaps(Path(args.case_dir).name))
    except (OSError, LLMFailure) as exc:
        print(f"error: overlap step failed: {exc}", file=sys.stderr)
        return 1
    print(usage_summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
