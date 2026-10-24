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
from regulation_set import kept_regulations
from select_sections import load_base_records

CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}

MAX_TEXT = 2000                  # README: text is at most 2,000 characters
DEV_CASE = "dev-rear-lamp"       # the case that has an expected.json for tools/score_local.py


def clip(text: str) -> str:
    """At most MAX_TEXT characters, cut at a word boundary."""
    if len(text) <= MAX_TEXT:
        return text
    return text[:MAX_TEXT - 1].rsplit(" ", 1)[0] + "…"


def build_submission(rows: list[dict], records: dict[str, dict], case_id: str, exclude: set[str] = frozenset(), *,
                     keep: set[str] | None = None, min_confidence: str = "low",
                     drop_categories: set[str] = frozenset(),
                     drop_unless_high: set[str] = frozenset()) -> tuple[dict, dict]:
    """(submission dict, stats). rows are extracted.jsonl lines; records maps paragraph id -> index record.
    keep: only these regulations (None = all). Paragraphs below min_confidence, in drop_categories, or in
    drop_unless_high (without high confidence) are left out;
    a row without confidence or category (older extraction) is never filtered."""
    stats = Counter()
    reqs, seen = [], set()
    floor = CONFIDENCE_RANK[min_confidence]
    for row in rows:
        if row["regulation"] in exclude or not row["requirements"] or (keep is not None and row["regulation"] not in keep):
            continue
        if CONFIDENCE_RANK.get(row.get("confidence"), 2) < floor:
            stats["left_out_by_confidence"] += len(row["requirements"])
            continue
        if row.get("category") in drop_categories or (row.get("category") in drop_unless_high
                                                       and row.get("confidence") != "high"):
            stats["left_out_by_category"] += len(row["requirements"])
            continue
        rec = records[row["id"]]
        for text in row["requirements"]:
            text = " ".join(text.split())
            if not text or (row["id"], text) in seen:
                stats["duplicates_removed"] += bool(text)
                continue
            seen.add((row["id"], text))
            if len(text) > MAX_TEXT:
                stats["truncated"] += 1
                text = clip(text)
            reqs.append({"id": f"R-{len(reqs) + 1:04d}", "text": text,
                         "source": {"regulation": rec["regulation"], "version": rec["version"], "page": rec["page"],
                                    "paragraph": rec["paragraph"]},
                         "evidence_id": rec["id"], "reason": row["reason"]})
            stats[rec["regulation"]] += 1
    return {"team_id": config.TEAM_ID, "case_id": case_id, "requirements": reqs}, dict(stats)


def make_submission(case_id: str, *, exclude: list[str] | None = None, unknown_policy: str | None = None,
                    drop_overlap: bool | None = None, min_confidence: str | None = None,
                    drop_categories: set[str] | None = None,
                    drop_unless_high: set[str] | None = None) -> tuple[dict, dict]:
    """The submission for a case under the given switches (None = the value in config.py). No API calls."""
    out_dir = config.CASES_BUILD / case_id
    rows = [json.loads(l) for l in (out_dir / "extracted.jsonl").read_text(encoding="utf-8").splitlines() if l]
    by_reg = load_base_records({r["regulation"] for r in rows})
    records = {r["id"]: r for recs in by_reg.values() for r in recs}
    keep = set(kept_regulations(case_id, unknown_policy=unknown_policy, drop_overlap=drop_overlap))
    return build_submission(rows, records, case_id, set(exclude or []), keep=keep,
                            min_confidence=min_confidence or config.MIN_CONFIDENCE,
                            drop_categories=config.DROP_CATEGORIES if drop_categories is None else drop_categories,
                            drop_unless_high=(config.DROP_CATEGORIES_UNLESS_HIGH if drop_unless_high is None
                                              else drop_unless_high))


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
    args = ap.parse_args(argv)
    try:
        export_case(Path(args.case_dir).name, args.exclude.split(",") if args.exclude else None, args.name,
                    min_confidence=args.min_confidence, unknown_policy=args.unknown_policy)
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
