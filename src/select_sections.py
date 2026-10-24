#!/usr/bin/env python3
"""Stage 4: section selection. For each regulation kept in stage 3, build an outline of its CITE_ROLE version from
build/paragraphs.jsonl and ask SCREEN_MODEL which sections / annexes can hold requirements for the product.
Candidates are all paragraphs of the selected sections; obligation signals are hints, never a filter.

    python src/select_sections.py cases/dev-rear-lamp

Input:  build/cases/<case_id>/profile.json, regulations.json, build/paragraphs.jsonl
Output: build/cases/<case_id>/candidates.jsonl   one line per candidate paragraph (id, regulation, unit, hints)
        build/cases/<case_id>/sections.json      every section decision with its reason (also for dropped ones)

A "unit" is a top-level section of the main body ("5") or a whole annex ("Annex 6"). Communication-form annexes are
skipped without asking the model. Sections the model does not answer for are kept (recall first).
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

WORKERS = 6
FORM_SHARE = 0.8             # an annex is a form when this share of its records is a communication form
MAX_CHILDREN = 30
S = {"type": "string"}
SELECT_SCHEMA = object_schema({"sections": {"type": "array", "items": object_schema({
    "id": S, "relevant": {"type": "string", "enum": ["yes", "no"]}, "reason": S})}})


# ---------------------------------------------------------------------------------------------------------------------
# Records and units
# ---------------------------------------------------------------------------------------------------------------------

def load_base_records(regulations: set[str], path: Path | None = None) -> dict[str, list[dict]]:
    """CITE_ROLE records of the given regulations from paragraphs.jsonl, in document order, by regulation."""
    out: dict[str, list[dict]] = {r: [] for r in regulations}
    with open(path or config.BUILD / "paragraphs.jsonl", encoding="utf-8") as fh:
        for line in fh:
            if '"role": "' + config.CITE_ROLE + '"' not in line:      # cheap pre-filter before parsing
                continue
            r = json.loads(line)
            if r["role"] == config.CITE_ROLE and r["regulation"] in out:
                out[r["regulation"]].append(r)
    return out


def unit_of(r: dict) -> str:
    """Top-level section of the main body ("5"), a restarting section ("I 5"), or an annex ("Annex 6")."""
    if r["annex"]:
        return f"Annex {r['annex']}"
    top = r["number"].split(".")[0]
    return " ".join(x for x in (r["section"], top) if x)


def is_candidate(r: dict) -> bool:
    """Numbered paragraphs, and title entries that carry text beyond their heading. Table entries are not candidates:
    their cells travel with the paragraph that owns the table."""
    if r["kind"] == "paragraph":
        return True
    return r["kind"] == "title" and len(r["text"]) > len(r["title"] or "") + 60


def snippet(r: dict, n: int = 70) -> str:
    return r["title"] or (r["text"][:n] + ("…" if len(r["text"]) > n else ""))


def build_outline(records: list[dict]) -> tuple[list[dict], list[str]]:
    """([unit outline dicts in document order], [ids of skipped form annexes])."""
    units: dict[str, dict] = {}
    for r in records:
        u = units.setdefault(unit_of(r), {"id": unit_of(r), "kind": "annex" if r["annex"] else "section", "title": "",
                                          "paragraphs": 0, "with_obligation": 0, "sub_sections": [], "annex_kinds": Counter()})
        if r["annex"]:
            u["annex_kinds"][r["annex_kind"]] += 1
            if r["kind"] == "title":
                label = r["paragraph"].replace(u["id"], "").strip()
                if label and r["title"] and len(u["sub_sections"]) < MAX_CHILDREN:
                    u["sub_sections"].append(f"{label}: {r['title']}")
                elif not label:
                    u["title"] = r["title"] or u["title"]
        else:
            if r["kind"] == "paragraph" and r["number"] == u["id"].split()[-1]:
                u["title"] = snippet(r, 100)
            elif r["kind"] == "paragraph" and r["number"].count(".") == 1 and len(u["sub_sections"]) < MAX_CHILDREN:
                u["sub_sections"].append(f"{r['number']}: {snippet(r, 60)}")
        if r["kind"] == "paragraph":
            u["paragraphs"] += 1
            u["with_obligation"] += bool(r["has_obligation"])
    outline, forms = [], []
    for u in units.values():
        kinds = u.pop("annex_kinds")
        total = sum(kinds.values())
        if total and kinds["communication_form"] / total >= FORM_SHARE:
            forms.append(u["id"])
            continue
        if kinds:
            u["annex_kind"] = kinds.most_common(1)[0][0]
        if u["paragraphs"] or u["sub_sections"]:
            outline.append(u)
    return outline, forms


# ---------------------------------------------------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------------------------------------------------

def select_content(profile: dict, decision: dict, outline: list[dict]) -> list[dict]:
    text = (f"PRODUCT PROFILE (JSON)\n{json.dumps(profile, ensure_ascii=False)}\n\n"
            f"REGULATION: {decision['regulation']}: {decision['title']}\n"
            f"Judged to apply because: {decision['reason']}\n(level: {decision['level']})\n\n"
            f"OUTLINE (JSON), one entry per section id:\n{json.dumps(outline, ensure_ascii=False)}\n\n"
            "Decide which sections can hold requirements for this product.")
    return [{"type": "text", "text": text}]


def choose_sections(profile: dict, decision: dict, records: list[dict]) -> dict:
    """Section decisions for one regulation: {"sections": {id: {relevant, reason, source}}, "skipped_forms": [...]}."""
    outline, forms = build_outline(records)
    result = {"regulation": decision["regulation"], "outline": outline, "skipped_forms": forms, "sections": {},
              "failure": None}
    answers: dict[str, dict] = {}
    try:
        raw, _ = call_json(config.SCREEN_MODEL, "select_system.md", select_content(profile, decision, outline),
                           SELECT_SCHEMA, label=f"select:{decision['regulation']}", max_tokens=6000)
        answers = {s["id"]: s for s in raw["sections"]}
    except LLMFailure as exc:
        result["failure"] = str(exc)
    for u in outline:
        a = answers.get(u["id"])
        if a is None:       # recall first: a section the model did not answer for is kept
            reason = f"no answer from the model ({result['failure']})" if result["failure"] else "not answered; kept"
            result["sections"][u["id"]] = {"relevant": True, "reason": reason, "source": "default_keep"}
        else:
            result["sections"][u["id"]] = {"relevant": a["relevant"] == "yes", "reason": " ".join(a["reason"].split()),
                                           "source": "model"}
    result["unknown_ids"] = sorted(set(answers) - {u["id"] for u in outline})
    return result


def select_case(case_id: str, out_dir: Path | None = None) -> dict:
    out_dir = out_dir or config.CASES_BUILD / case_id
    profile = json.loads((out_dir / "profile.json").read_text(encoding="utf-8"))
    profile = {k: v for k, v in profile.items() if k != "meta"}
    decisions = [d for d in json.loads((out_dir / "regulations.json").read_text(encoding="utf-8"))["decisions"]
                 if d["applies"]]
    by_reg = load_base_records({d["regulation"] for d in decisions})
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        chosen = list(pool.map(lambda d: choose_sections(profile, d, by_reg[d["regulation"]]), decisions))

    counts, candidates = {}, []
    for d, sel in zip(decisions, chosen):
        keep = {u for u, s in sel["sections"].items() if s["relevant"]}
        n_in = n_out = 0
        for r in by_reg[d["regulation"]]:
            if not is_candidate(r):
                continue
            u = unit_of(r)
            if u in keep:
                n_in += 1
                candidates.append({"id": r["id"], "regulation": r["regulation"], "version": r["version"], "unit": u,
                                   "paragraph": r["paragraph"], "page": r["page"], "has_obligation": r["has_obligation"],
                                   "selection_reason": sel["sections"][u]["reason"]})
            else:
                n_out += 1
        counts[d["regulation"]] = {"candidates": n_in, "not_selected": n_out, "sections_kept": len(keep),
                                   "sections_dropped": len(sel["sections"]) - len(keep),
                                   "skipped_forms": len(sel["skipped_forms"]), "failure": sel["failure"]}
    with open(out_dir / "candidates.jsonl", "w", encoding="utf-8") as fh:
        for c in candidates:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")
    (out_dir / "sections.json").write_text(json.dumps({"counts": counts, "regulations": chosen},
                                                      ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return {"counts": counts, "candidates": len(candidates)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("case_dir")
    args = ap.parse_args(argv)
    try:
        res = select_case(Path(args.case_dir).name)
    except (OSError, LLMFailure) as exc:
        print(f"error: section selection failed: {exc}", file=sys.stderr)
        return 1
    print_counts(res)
    print(usage_summary())
    return 0


def print_counts(res: dict) -> None:
    print(f"{'regulation':<10} {'candidates':>10} {'not selected':>12} {'sections kept/dropped':>22} {'forms skipped':>13}")
    for reg, c in res["counts"].items():
        print(f"{reg:<10} {c['candidates']:>10} {c['not_selected']:>12} {c['sections_kept']:>11}/{c['sections_dropped']:<10} "
              f"{c['skipped_forms']:>13}" + (f"  FAILED: {c['failure']}" if c["failure"] else ""))
    print(f"total candidates: {res['candidates']}")


if __name__ == "__main__":
    raise SystemExit(main())
