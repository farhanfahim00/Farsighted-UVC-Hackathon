#!/usr/bin/env python3
"""Stage 4: section selection. For each regulation kept in stage 3, build an outline of its CITE_ROLE version from
build/paragraphs.jsonl and ask SCREEN_MODEL which sections / annexes can hold requirements for the product.
Candidates are all paragraphs of the selected sections; obligation signals are hints, never a filter.

    python src/select_sections.py cases/dev-rear-lamp

Input:  build/cases/<case_id>/profile.json, regulations.json (+ overlap.json), build/paragraphs.jsonl
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
from regulation_set import final_decisions

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
    """Numbered paragraphs, table entries (labelled as in the index, e.g. "5.1.4.4.1. Table 3": they carry the table's
    cells), and title entries that carry text beyond their heading."""
    if r["kind"] in ("paragraph", "table"):
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


# ---------------------------------------------------------------------------------------------------------------------
# Second level: sub-sections of the kept sections and annexes
# ---------------------------------------------------------------------------------------------------------------------

SUB_SCHEMA = SELECT_SCHEMA            # same shape: [{id, relevant, reason}]
MIN_TITLED_SUBS = 2                   # a unit is asked about only when at least this many sub-sections have titles


def in_subsection(r: dict, sub: dict) -> bool:
    """r lies inside sub-section sub = {"record_id", "id" (the label)}: it is the sub-section, or its parent chain / label says so."""
    label = sub["id"]
    return (r["id"] == sub["record_id"] or sub["record_id"] in (r.get("parent_chain") or [])
            or r["paragraph"] == label or r["paragraph"].startswith(label + ".") or r["paragraph"].startswith(label + " "))


def build_subsections(records: list[dict], kept_units: set[str]) -> list[dict]:
    """[{unit, title, subs: [{id (label), title, paragraphs, record_id}]}] for the kept units whose direct sub-sections
    have titles: main-body "6.2" with a title, annex parts / appendices / numbered sections with a title."""
    units: dict[str, dict] = {}
    for r in records:
        u = unit_of(r)
        if u not in kept_units:
            continue
        e = units.setdefault(u, {"unit": u, "title": "", "subs": []})
        if r["annex"]:
            if r["kind"] == "title" and r["paragraph"] != u and r["title"] and r["parent_status"] in ("ok", "root"):
                if r["parent"] in (None, "", u) or r["parent"] == u:
                    e["subs"].append({"id": r["paragraph"], "title": r["title"], "record_id": r["id"], "paragraphs": 0})
        elif r["kind"] == "paragraph" and r["title"] and not r["appendix"] and r["number"].count(".") == 1 \
                and r["number"].split(".")[0] == u.split()[-1]:
            e["subs"].append({"id": r["paragraph"], "title": r["title"], "record_id": r["id"], "paragraphs": 0})
        elif r["kind"] == "paragraph" and r["number"] == u.split()[-1]:
            e["title"] = snippet(r, 100)
    out = []
    for e in units.values():
        if len(e["subs"]) < MIN_TITLED_SUBS:
            continue
        for sub in e["subs"]:
            sub["paragraphs"] = sum(1 for r in records if is_candidate(r) and unit_of(r) == e["unit"] and in_subsection(r, sub))
        out.append(e)
    return out


def subsection_content(profile: dict, decision: dict, outline: list[dict], lists_complete: bool) -> list[dict]:
    shown = [{"unit": e["unit"], "title": e["title"],
              "sub_sections": [{"id": s["id"], "title": s["title"], "paragraphs": s["paragraphs"]} for s in e["subs"]]}
             for e in outline]
    text = (f"PRODUCT PROFILE (JSON)\n{json.dumps(profile, ensure_ascii=False)}\n\n"
            f"REGULATION: {decision['regulation']}: {decision['title']}\n"
            f"Judged to apply because: {decision['reason']}\n\n"
            f"SPEC_LISTS_COMPLETE: {'true' if lists_complete else 'false'}\n\n"
            f"SUB-SECTIONS (JSON), grouped by chosen section or annex:\n{json.dumps(shown, ensure_ascii=False)}\n\n"
            "Decide, for every sub-section id, whether it can be dropped.")
    return [{"type": "text", "text": text}]


def choose_subsections(profile: dict, decision: dict, records: list[dict], kept_units: set[str],
                       lists_complete: bool) -> dict:
    """{"regulation", "subsections": {label: {unit, title, record_id, paragraphs, relevant, reason, source}}, "failure"}."""
    outline = build_subsections(records, kept_units)
    result = {"regulation": decision["regulation"], "subsections": {}, "failure": None}
    if not outline:
        return result
    answers: dict[str, dict] = {}
    try:
        raw, _ = call_json(config.SCREEN_MODEL, "subsection_system.md",
                           subsection_content(profile, decision, outline, lists_complete), SUB_SCHEMA,
                           label=f"subsections:{decision['regulation']}:{lists_complete}", max_tokens=8000)
        answers = {s["id"]: s for s in raw["sections"]}
    except LLMFailure as exc:
        result["failure"] = str(exc)
    for e in outline:
        for s in e["subs"]:
            a = answers.get(s["id"])
            base = {"unit": e["unit"], "title": s["title"], "record_id": s["record_id"], "paragraphs": s["paragraphs"]}
            if a is None:     # recall first
                result["subsections"][s["id"]] = {**base, "relevant": True, "source": "default_keep",
                                                  "reason": f"no answer ({result['failure']})" if result["failure"] else "not answered; kept"}
            else:
                result["subsections"][s["id"]] = {**base, "relevant": a["relevant"] == "yes", "source": "model",
                                                  "reason": " ".join(a["reason"].split())}
    return result


def select_subsections_case(case_id: str, out_dir: Path | None = None) -> dict:
    """Second-level selection for both SPEC_LISTS_COMPLETE settings -> build/cases/<case_id>/subsections.json
    {"spec_lists_complete_false": {regulation: {...}}, "spec_lists_complete_true": {...}}."""
    from regulation_set import kept_regulations
    out_dir = out_dir or config.CASES_BUILD / case_id
    profile = json.loads((out_dir / "profile.json").read_text(encoding="utf-8"))
    profile = {k: v for k, v in profile.items() if k != "meta"}
    sections = json.loads((out_dir / "sections.json").read_text(encoding="utf-8"))
    kept = set(kept_regulations(case_id))
    decisions = [d for d in final_decisions(case_id) if d["applies"] and d["regulation"] in kept]
    by_reg = load_base_records({d["regulation"] for d in decisions})
    kept_units = {s["regulation"]: {u for u, v in s["sections"].items() if v["relevant"]} for s in sections["regulations"]}
    result = {}
    for flag in (False, True):
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            chosen = list(pool.map(lambda d: choose_subsections(profile, d, by_reg[d["regulation"]],
                                                                kept_units.get(d["regulation"], set()), flag), decisions))
        result[f"spec_lists_complete_{str(flag).lower()}"] = {c["regulation"]: c for c in chosen}
    (out_dir / "subsections.json").write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return result


def select_case(case_id: str, out_dir: Path | None = None) -> dict:
    out_dir = out_dir or config.CASES_BUILD / case_id
    profile = json.loads((out_dir / "profile.json").read_text(encoding="utf-8"))
    profile = {k: v for k, v in profile.items() if k != "meta"}
    decisions = [d for d in final_decisions(case_id) if d["applies"]]     # after UNKNOWN_POLICY and overlap
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
    ap.add_argument("--subsections", action="store_true", help="second level only: sub-sections of the kept sections "
                    "(both SPEC_LISTS_COMPLETE settings -> subsections.json); sections.json must already exist")
    args = ap.parse_args(argv)
    if args.subsections:
        try:
            res = select_subsections_case(Path(args.case_dir).name)
        except (OSError, LLMFailure) as exc:
            print(f"error: sub-section selection failed: {exc}", file=sys.stderr)
            return 1
        print(usage_summary())
        return 0
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
