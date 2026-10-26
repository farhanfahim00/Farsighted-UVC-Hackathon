#!/usr/bin/env python3
"""Stage 6: export the submission file, validate it with Certivity's validator, and (for the dev case) score it.

    python src/export.py cases/dev-rear-lamp
    python src/export.py cases/dev-rear-lamp --exclude "UN R3,UN R7,UN R23"     # variant, no new API calls

Input:  build/cases/<case_id>/extracted.jsonl, build/paragraphs.jsonl (+ regulations.json, overlap.json)
Output: build/cases/<case_id>/submission.json, or submission_<name>.json for a variant

v2 filters (defaults in src/config.py, all applied here so that variants need no new API calls):
  * the regulations kept after UNKNOWN_POLICY and overlap (regulation_set.py);
  * MIN_CONFIDENCE: paragraphs whose confidence is below it are left out;
  * DROP_CATEGORIES / DROP_CATEGORIES_UNLESS_HIGH: paragraphs of these categories are left out (the second only
    when their confidence is not high); both chosen with src/analyze_extras.py --rules.

The source of every requirement (regulation, version, page, paragraph) is copied from the index record of the
paragraph it came from, never from the model. Exact duplicate texts within one paragraph are removed. Extra fields
`evidence_id` (the paragraph id in the index) and `reason` (why the paragraph applies) are allowed by the format.
tools/validate.py runs on the file and the command fails loudly (exit 1) if it reports a problem.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import config
from faithfulness import load_corrections
from regulation_set import kept_regulations
from select_sections import in_subsection, load_base_records

CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}

MAX_TEXT = 2000                  # README: text is at most 2,000 characters
DEV_CASE = "dev-rear-lamp"       # the case that has an expected.json for tools/score_local.py


RATIONALE_HEADING = re.compile(r"\b(?:technical\s+)?rationale\b|\bjustification\b", re.I)


def rationale_ids(by_reg: dict[str, list[dict]]) -> set[str]:
    """Ids of paragraphs that belong to a part of a regulation that only explains it (no requirements), in document order.
    A paragraph counts when a heading above it (breadcrumb) says rationale / justification, or when it follows a
    root section title that does, up to the last paragraph that carries a section label of its own (the sibling
    sections A, B, VII, ... of that part). Normal paragraph text is never looked at."""
    out: set[str] = set()
    for recs in by_reg.values():
        for i, r in enumerate(recs):
            if any(RATIONALE_HEADING.search(b) for b in (r.get("breadcrumb") or [])):
                out.add(r["id"])
            elif r["kind"] == "title" and r.get("parent_status") == "root" and RATIONALE_HEADING.search(r.get("title") or ""):
                end = i
                for k in range(i + 1, len(recs)):
                    if recs[k]["doc_id"] != r["doc_id"]:
                        break
                    if recs[k].get("section"):
                        end = k
                out.update(x["id"] for x in recs[i:end + 1])
    return out


_TOKEN = re.compile(r"[^\W_]+(?:[.,][^\W_]+)*", re.U)
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_LIMIT_WORDS = {"not", "no", "min", "minimum", "max", "maximum", "least", "most", "less", "more", "below", "above",
                "exceed", "exceeds", "exceeding", "within", "between", "only", "all", "any", "each", "every"}


_STOP = {"the", "a", "an", "of", "to", "in", "on", "at", "by", "for", "and", "or", "be", "is", "are", "shall", "must", "that",
         "this", "these", "those", "it", "its", "with", "as", "any", "every", "each", "all", "when", "where", "which", "if",
         "than", "then", "also", "their", "such", "may"}


def _norm_tokens(text: str) -> list[str]:
    toks = [t.replace(",", ".") for t in _TOKEN.findall(text.lower())]
    return [t[:-1] if len(t) > 3 and t.endswith("s") and not t.endswith("ss") else t for t in toks]


def _fingerprint(text: str) -> dict:
    toks = _norm_tokens(text)
    # numbers with the unit token that follows them; limit words as a set
    nums = []
    for i, t in enumerate(toks):
        if _NUMBER.fullmatch(t):
            nxt = toks[i + 1] if i + 1 < len(toks) and not _NUMBER.fullmatch(toks[i + 1]) else ""
            nums.append((t, nxt if len(nxt) <= 4 else ""))
    return {"toks": toks, "set": set(toks), "nums": sorted(nums), "limits": {t for t in toks if t in _LIMIT_WORDS}}


def near_duplicates(a: dict, b: dict, threshold: float) -> bool:
    if a["nums"] != b["nums"] or a["limits"] != b["limits"] or not a["set"] or not b["set"]:
        return False
    # more than one content word on one side only (stop lamp vs rear position lamp, narrowband vs broadband) makes it a different requirement
    small, large = (a, b) if len(a["set"]) <= len(b["set"]) else (b, a)
    if sum(t not in _STOP and not _NUMBER.fullmatch(t) for t in small["set"] ^ large["set"]) > 1:
        return False
    inter = len(a["set"] & b["set"])
    return inter / len(a["set"] | b["set"]) >= threshold or inter / len(small["set"]) >= threshold


def dedup_near_duplicates(entries: list[dict], threshold: float, log: list | None = None) -> list[dict]:
    """entries: dicts with req, prob, conf, seq. Per regulation, drop near-duplicates of a better entry (probability, then
    confidence, then earliest paragraph / position). Returns the survivors in original order."""
    by_reg: dict[str, list[dict]] = {}
    for e in entries:
        e["fp"] = _fingerprint(e["req"]["text"])
        by_reg.setdefault(e["req"]["source"]["regulation"], []).append(e)
    dropped: set[int] = set()
    for group in by_reg.values():
        kept: list[dict] = []
        for e in sorted(group, key=lambda e: (-e["prob"], -e["conf"], e["seq"])):
            twin = next((k for k in kept if near_duplicates(e["fp"], k["fp"], threshold)), None)
            if twin is None:
                kept.append(e)
            else:
                dropped.add(e["seq"])
                if log is not None:
                    log.append({"removed": e["req"], "removed_prob": e["prob"], "kept": twin["req"], "kept_prob": twin["prob"]})
    return [e for e in entries if e["seq"] not in dropped]


def clip(text: str) -> str:
    """At most MAX_TEXT characters, cut at a word boundary."""
    if len(text) <= MAX_TEXT:
        return text
    return text[:MAX_TEXT - 1].rsplit(" ", 1)[0] + "…"


def load_deselected(case_id: str, spec_lists_complete: bool) -> dict[str, list[dict]]:
    """{regulation: [sub-section {id, record_id, ...}]} that subsections.json deselects for the given setting."""
    path = config.CASES_BUILD / case_id / "subsections.json"
    data = json.loads(path.read_text(encoding="utf-8"))[f"spec_lists_complete_{str(spec_lists_complete).lower()}"]
    return {reg: [{"id": i, **s} for i, s in c["subsections"].items() if not s["relevant"]] for reg, c in data.items()}


REVIEW_FILES = {"basic": "review_medium.jsonl", "criteria": "review_medium_criteria.jsonl",
                "criteria_pass2": "review_medium_criteria_pass2.jsonl"}


def read_review_file(path: Path) -> dict[tuple[str, int], int]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line:
            r = json.loads(line)
            out[(r["id"], r["index"])] = r["probability"]
    return out


def load_review(case_id: str, prompt: str | None = None, twice: bool = False) -> dict[tuple[str, int], float]:
    """{(paragraph id, requirement index): probability 0-100} from the review file of the prompt (config.REVIEW_PROMPT).
    twice: an item that also has a second review (criteria_pass2.jsonl) gets the mean of the two."""
    d = config.CASES_BUILD / case_id
    out: dict = read_review_file(d / REVIEW_FILES[prompt or config.REVIEW_PROMPT])
    if twice:
        for key, p2 in read_review_file(d / REVIEW_FILES["criteria_pass2"]).items():
            if key in out:
                out[key] = (out[key] + p2) / 2
    return out


def build_submission(rows: list[dict], records: dict[str, dict], case_id: str, exclude: set[str] = frozenset(), *,
                     keep: set[str] | None = None, min_confidence: str = "low",
                     drop_categories: set[str] = frozenset(),
                     drop_unless_high: set[str] = frozenset(),
                     corrections: dict[tuple[str, int], str] | None = None,
                     deselected: dict[str, list[dict]] | None = None,
                     review: dict[tuple[str, int], int] | None = None, review_below: int = 0,
                     trace: list | None = None, review_category_medium: bool = False,
                     review_low: bool = False, review_log: list | None = None, review_all: bool = False,
                     rationale: set[str] | None = None, dedup: float | None = None,
                     dedup_log: list | None = None) -> tuple[dict, dict]:
    """(submission dict, stats). rows are extracted.jsonl lines; records maps paragraph id -> index record.
    corrections: {(paragraph id, requirement index): corrected text} from faithfulness.py, replacing the extracted text.
    keep: only these regulations (None = all). Paragraphs below min_confidence, in drop_categories, or in
    drop_unless_high (without high confidence) are left out;
    a row without confidence or category (older extraction) is never filtered.
    deselected: sub-sections (select_sections.in_subsection) whose requirements are left out.
    review: {(paragraph id, index): probability}; a reviewed requirement below review_below is left out.
    trace: if a list, gets (paragraph id, requirement index, exported text, confidence, category) for every requirement kept.
    review_category_medium: medium-confidence rows in drop_unless_high are reviewed instead of dropped.
    review_low: rows below min_confidence are reviewed instead of dropped (and are then not dropped by drop_unless_high).
    review_all: every row is reviewed; no confidence floor, no category rules.
    rationale: ids of paragraphs in rationale / justification parts (rationale_ids); their requirements are left out.
    A row is "reviewed" when it is one of those two kinds or (review given) has medium confidence; a requirement without a
    review for a row of the first two kinds raises KeyError (run review_medium.py). review_log gets one dict per
    reviewed requirement: id, index, category, confidence, probability, kept."""
    stats = Counter()
    entries, seen = [], set()
    floor = CONFIDENCE_RANK[min_confidence]
    for row in rows:
        if row["regulation"] in exclude or not row["requirements"] or (keep is not None and row["regulation"] not in keep):
            continue
        if rationale and row["id"] in rationale:
            stats["left_out_by_rationale"] += len(row["requirements"])
            continue
        must_review = review_all             # a row that would have been dropped but goes to the second opinion
        if review_all:
            pass
        elif CONFIDENCE_RANK.get(row.get("confidence"), 2) < floor:
            if not review_low:
                stats["left_out_by_confidence"] += len(row["requirements"])
                continue
            must_review = True
        elif row.get("category") in drop_unless_high and row.get("confidence") != "high":
            if not (review_category_medium and row.get("confidence") == "medium"):
                stats["left_out_by_category"] += len(row["requirements"])
                continue
            must_review = True
        if not review_all and row.get("category") in drop_categories:
            stats["left_out_by_category"] += len(row["requirements"])
            continue
        rec = records[row["id"]]
        if deselected and any(in_subsection(rec, sub) for sub in deselected.get(row["regulation"], ())):
            stats["left_out_by_subsection"] += len(row["requirements"])
            continue
        for i, text in enumerate(row["requirements"]):
            if corrections and (row["id"], i) in corrections:
                stats["faithfulness_corrected"] += 1
                text = corrections[(row["id"], i)]
            prob_used = 100
            if review is not None and (must_review or row.get("confidence") == "medium"):
                if must_review and (row["id"], i) not in review:
                    raise KeyError(f"no review for {row['id']} #{i}; run review_medium.py")
                prob = review.get((row["id"], i), 100)
                prob_used = prob
                if review_log is not None:
                    review_log.append({"id": row["id"], "index": i, "category": row.get("category"),
                                       "confidence": row.get("confidence"), "probability": prob,
                                       "kept": prob >= review_below})
                if prob < review_below:
                    stats["left_out_by_review"] += 1
                    continue
            text = " ".join(text.split())
            if not text or (row["id"], text) in seen:
                stats["duplicates_removed"] += bool(text)
                continue
            seen.add((row["id"], text))
            if trace is not None:
                trace.append((row["id"], i, text, row.get("confidence"), row.get("category")))
            if len(text) > MAX_TEXT:
                stats["truncated"] += 1
                text = clip(text)
            entries.append({"seq": len(entries), "prob": prob_used, "conf": CONFIDENCE_RANK.get(row.get("confidence"), 2),
                            "req": {"id": "", "text": text,
                         "source": {"regulation": rec["regulation"], "version": rec["version"], "page": rec["page"],
                                    "paragraph": rec["paragraph"]},
                         "evidence_id": rec["id"], "reason": row["reason"]}})
    if dedup:
        before = len(entries)
        entries = dedup_near_duplicates(entries, dedup, dedup_log)
        stats["near_duplicates_removed"] = before - len(entries)
    reqs = []
    for e in entries:
        e["req"]["id"] = f"R-{len(reqs) + 1:04d}"
        reqs.append(e["req"])
        stats[e["req"]["source"]["regulation"]] += 1
    return {"team_id": config.TEAM_ID, "case_id": case_id, "requirements": reqs}, dict(stats)


def make_submission(case_id: str, *, exclude: list[str] | None = None, unknown_policy: str | None = None,
                    drop_overlap: bool | None = None, min_confidence: str | None = None,
                    drop_categories: set[str] | None = None,
                    drop_unless_high: set[str] | None = None,
                    faithfulness: bool | None = None, subsection_filter: bool | None = None,
                    spec_lists_complete: bool | None = None, review_medium: bool | None = None,
                    exclude_vehicle_level: bool | None = None, vehicle_category_check: bool | None = None,
                    review_instead: bool | None = None, review_low: bool | None = None,
                    review_prompt: str | None = None, trace: list | None = None,
                    review_log: list | None = None, review_all: bool | None = None,
                    review_twice: bool | None = None, dedup_log: list | None = None) -> tuple[dict, dict]:
    """The submission for a case under the given switches (None = the value in config.py). No API calls.
    faithfulness: use the corrected texts of faithfulness.jsonl (default config.FAITHFULNESS_CHECK)."""
    out_dir = config.CASES_BUILD / case_id
    rows = [json.loads(l) for l in (out_dir / "extracted.jsonl").read_text(encoding="utf-8").splitlines() if l]
    by_reg = load_base_records({r["regulation"] for r in rows})
    records = {r["id"]: r for recs in by_reg.values() for r in recs}
    keep = set(kept_regulations(case_id, unknown_policy=unknown_policy, drop_overlap=drop_overlap,
                                 exclude_vehicle_level=exclude_vehicle_level,
                                 vehicle_category_check=vehicle_category_check))
    if config.EXPORT_REGULATION_ALLOWLIST:
        # manual, case-specific selection of regulations (empty = off)
        allowed = {r if r.startswith("UN ") else f"UN {r}" for r in config.EXPORT_REGULATION_ALLOWLIST}
        keep &= allowed
    use_faithfulness = config.FAITHFULNESS_CHECK if faithfulness is None else faithfulness
    corrections = load_corrections(case_id, rows) if use_faithfulness else None
    complete = config.SPEC_LISTS_COMPLETE if spec_lists_complete is None else spec_lists_complete
    deselected = (load_deselected(case_id, complete)
                  if (config.SUBSECTION_FILTER if subsection_filter is None else subsection_filter) else None)
    instead = config.REVIEW_INSTEAD_OF_CATEGORY_DROP if review_instead is None else review_instead
    low = config.REVIEW_LOW_CONFIDENCE if review_low is None else review_low
    every = config.REVIEW_ALL if review_all is None else review_all
    twice = every and (config.REVIEW_BORDERLINE_TWICE if review_twice is None else review_twice)
    use_review = (config.REVIEW_MEDIUM if review_medium is None else review_medium) or instead or low or every
    review = load_review(case_id, "criteria" if every else review_prompt, twice) if use_review else None
    return build_submission(rows, records, case_id, set(exclude or []), keep=keep,
                            min_confidence=min_confidence or config.MIN_CONFIDENCE,
                            drop_categories=config.DROP_CATEGORIES if drop_categories is None else drop_categories,
                            drop_unless_high=(config.DROP_CATEGORIES_UNLESS_HIGH if drop_unless_high is None
                                              else drop_unless_high), corrections=corrections,
                            deselected=deselected, review=review, review_below=config.REVIEW_DROP_BELOW, trace=trace,
                            review_category_medium=instead, review_low=low, review_log=review_log,
                            review_all=every,
                            rationale=rationale_ids(by_reg) if config.DROP_RATIONALE_PARTS else None,
                            dedup=config.DEDUP_SIMILARITY if config.DEDUP_NEAR_DUPLICATES else None, dedup_log=dedup_log)


def run_tool(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(config.ROOT / "tools" / script), *args], capture_output=True,
                          text=True, cwd=config.ROOT)


def export_case(case_id: str, exclude: list[str] | None = None, name: str | None = None, **switches) -> Path:
    out_dir = config.CASES_BUILD / case_id
    exclude = [e.strip() for e in (exclude or []) if e.strip()]
    submission, stats = make_submission(case_id, exclude=exclude, **switches)
    if exclude or name:
        name = name or "no_" + re.sub(r"\W+", "_", "_".join(exclude)).strip("_")
        path = out_dir / f"submission_{name}.json"
    else:
        path = out_dir / "submission.json"
    path.write_text(json.dumps(submission, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    print(f"wrote {path} : {len(submission['requirements'])} requirements"
          + (f" (without {', '.join(exclude)})" if exclude else ""))
    print("  per regulation: " + ", ".join(f"{k} {v}" for k, v in stats.items() if k.startswith("UN ")))
    extra = {k: v for k, v in stats.items() if not k.startswith("UN ")}
    if extra:
        print(f"  {extra}")

    check = run_tool("validate.py", str(path))
    print(check.stdout.strip())
    if check.returncode != 0:
        print(check.stderr.strip(), file=sys.stderr)
        raise SystemExit(f"error: tools/validate.py reported problems for {path}")
    if case_id == DEV_CASE:
        score = run_tool("score_local.py", str(path))
        print(score.stdout.strip() or score.stderr.strip())
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("case_dir")
    ap.add_argument("--exclude", help='comma-separated regulations to leave out, e.g. "UN R3,UN R7"')
    ap.add_argument("--name", help="variant name (default: no_<regulations>)")
    ap.add_argument("--min-confidence", choices=list(CONFIDENCE_RANK), help="default: config.MIN_CONFIDENCE")
    ap.add_argument("--unknown-policy", choices=["include", "exclude"], help="default: config.UNKNOWN_POLICY")
    ap.add_argument("--drop-unless-high", help="comma-separated categories left out unless confidence is high "
                                               "(default: config.DROP_CATEGORIES_UNLESS_HIGH)")
    ap.add_argument("--no-faithfulness", action="store_true", help="export the extracted texts, not the corrected ones")
    args = ap.parse_args(argv)
    try:
        export_case(Path(args.case_dir).name, args.exclude.split(",") if args.exclude else None, args.name,
                    min_confidence=args.min_confidence, unknown_policy=args.unknown_policy,
                    drop_unless_high=set(args.drop_unless_high.split(",")) if args.drop_unless_high else None,
                    faithfulness=False if args.no_faithfulness else None)
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
