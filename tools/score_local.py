#!/usr/bin/env python3
"""Score your output on the worked example (the rear lamp) without uploading anything.

    python tools/score_local.py my_submission.json
    python tools/score_local.py my_submission.json --expected cases/dev-rear-lamp/expected.json --json

This checks two things only, and makes no model calls:

  * regulation level: which UN Regulations you cite, compared with the expected file
  * page level:       which (regulation, version, page) you cite, compared with the expected file; either the page OR
                      the paragraph number may be right (a wrong page number is not held against you when the paragraph is right)

Whether each requirement says the same thing as an expected one (the main score) is judged by a
language model on the submission platform, so it is not shown here. Upload to the platform to see it.

The expected file is treated as the answer, so its own cited pages are the "gold" pages. The
platform's page score uses the full page range of each expected paragraph, so the numbers can
differ slightly from the ones here: use this to catch gross mistakes, not to tune a score.

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


def _div(numerator: float, denominator: float) -> float:
    return 0.0 if denominator == 0 else numerator / denominator


def _prf(matched: int, n_submitted: int, n_expected: int) -> dict[str, float | int]:
    precision, recall = _div(matched, n_submitted), _div(matched, n_expected)
    return {
        "precision": precision,
        "recall": recall,
        "f1": _div(2 * precision * recall, precision + recall),
        "n_submitted": n_submitted,
        "n_expected": n_expected,
        "n_matched": matched,
    }


def _prf_two(correct: int, n_submitted: int, found: int, n_expected: int) -> dict[str, float | int]:
    precision, recall = _div(correct, n_submitted), _div(found, n_expected)
    return {"precision": precision, "recall": recall, "f1": _div(2 * precision * recall, precision + recall),
            "n_submitted": n_submitted, "n_expected": n_expected, "n_matched": correct}


def _cited(doc: dict[str, Any]) -> list[tuple[str, str, int]]:
    return [(r["source"]["regulation"], r["source"].get("version", ""), r["source"]["page"]) for r in doc.get("requirements", [])]


def _paragraph_key(paragraph: Any) -> str | None:
    """The same paragraph written in the usual different ways gives the same key: "5.4.2.3." / "5.4.2.3", "Annex 8 2.1" /
    "Annex 8, 2.1" / "annex 8  2.1.", "Annex 4, Part 1, 1.2" / "Annex 4 Part 1 1.2", "paragraph 5.4.2.3" / "5.4.2.3".
    Case, commas, semicolons, colons, brackets, extra spaces, a dot closing a word and a leading "paragraph" / "para" / "§" do not
    matter. The dots INSIDE a number do ("5.4.2.3" is not "5.4.23"), and so does the word "Annex" ("Annex 8 2.1" is not "8 2.1")."""
    text = str(paragraph or "").casefold().replace("§", " ")
    text = re.sub(r"[,;:()\[\]]", " ", text)
    text = re.sub(r"(?<![0-9a-z])\.|\.(?![0-9a-z])", " ", text)  # a dot that is not between two letters or digits; those inside "5.4.2" stay
    text = " ".join(text.split())
    text = re.sub(r"^(paragraphs?|para|par|section|clause)\s+", "", text)
    return text or None


def _paragraphs(doc: dict[str, Any]) -> tuple[dict, dict]:
    """(paragraphs by (regulation, version, page), paragraphs by (regulation, version))."""
    on_page: dict[tuple, set[str]] = {}
    anywhere: dict[tuple, set[str]] = {}
    for r in doc.get("requirements", []):
        s, key = r["source"], _paragraph_key(r["source"].get("paragraph"))
        if key:
            on_page.setdefault((s["regulation"], s.get("version", ""), s["page"]), set()).add(key)
            anywhere.setdefault((s["regulation"], s.get("version", "")), set()).add(key)
    return on_page, anywhere


def score(submission: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    """Regulation-level and page-level precision, recall and F1 of `submission` against `expected`.

    S is the set of distinct regulations cited, G the expected ones: precision is |S ∩ G| / |S|
    and recall is |S ∩ G| / |G|. The same with distinct (regulation, version, page) for pages: a page of the
    latest version is not a page of the base version the expected file is built on.
    """
    cited, wanted = _cited(submission), _cited(expected)
    sub_regs, gold_regs = {r for r, _, _ in cited}, {r for r, _, _ in wanted}
    sub_pages, gold_pages = set(cited), set(wanted)
    sub_on_page, sub_anywhere = _paragraphs(submission)
    gold_on_page, gold_anywhere = _paragraphs(expected)
    # Either the page or the paragraph number may be right: a cited page is correct if it is a gold page or a paragraph
    # cited on it is a gold paragraph of that version; a gold page is found if it is cited or a gold paragraph on it is cited.
    correct = {p for p in sub_pages if p in gold_pages or sub_on_page.get(p, set()) & gold_anywhere.get(p[:2], set())}
    found = {p for p in gold_pages if p in sub_pages or gold_on_page.get(p, set()) & sub_anywhere.get(p[:2], set())}
    return {
        "regulation_level": _prf(len(sub_regs & gold_regs), len(sub_regs), len(gold_regs)),
        "page_level": _prf_two(len(correct), len(sub_pages), len(found), len(gold_pages)),
    }


def _default_expected() -> Path | None:
    root = Path(__file__).resolve().parent.parent
    for candidate in (root / "cases" / "dev-rear-lamp" / "expected.json", Path("cases/dev-rear-lamp/expected.json")):
        if candidate.is_file():
            return candidate
    return None


def _load(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"error: cannot read {path}: {exc}")
    if not isinstance(data, dict) or not isinstance(data.get("requirements"), list):
        raise SystemExit(f"error: {path} is not a main-goal file (no 'requirements' list)")
    try:
        _cited(data)
    except (KeyError, TypeError):
        raise SystemExit(f"error: {path} has requirements without source.regulation / source.page; run tools/validate.py first")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score regulation and page level on the dev case, offline.")
    parser.add_argument("submission", help="your main-goal submission JSON")
    parser.add_argument("--expected", help="the expected output (default: cases/dev-rear-lamp/expected.json)")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    args = parser.parse_args(argv)

    expected_path = Path(args.expected) if args.expected else _default_expected()
    if expected_path is None:
        print("error: cases/dev-rear-lamp/expected.json not found; pass --expected", file=sys.stderr)
        return 2
    result = score(_load(Path(args.submission)), _load(expected_path))

    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    print(f"Scored against {expected_path.name} (offline: regulation and page level only)\n")
    print(f"{'level':<12}{'precision':>10}{'recall':>9}{'F1':>8}   cited / expected / matched")
    for name, label in (("regulation_level", "regulation"), ("page_level", "page")):
        r = result[name]
        print(f"{label:<12}{r['precision']:>10.3f}{r['recall']:>9.3f}{r['f1']:>8.3f}   "
              f"{r['n_submitted']} / {r['n_expected']} / {r['n_matched']}")
    print("\nThe requirement-level score (do your requirements say what the expected ones say?) is "
          "computed on the submission platform.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
