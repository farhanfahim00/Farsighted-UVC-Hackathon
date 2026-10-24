#!/usr/bin/env python3
"""Measure build/paragraphs.jsonl (base role only) against a worked example's expected.json.

    python src/check_index.py
    python src/check_index.py --expected cases/dev-rear-lamp/expected.json --paragraphs build/paragraphs.jsonl

Reports, per distinct expected (regulation, paragraph) pair:
  * found by label (labels normalised the way the scorer does it);
  * of those, page within +/-1 of the expected page;
  * for pairs not found by label: does the expected page exist, and does a key phrase of the expected text appear
    on that page (separates labelling problems from text-extraction problems);
  * per regulation: requirements picked by the experts vs paragraphs with "shall" in our index;
  * every miss, with the closest label we produced on the expected page.
"""

from __future__ import annotations
import argparse
import difflib
import json
import re
from collections import defaultdict
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
PDF_DIR = ROOT / "regulations" / "pdf"


def paragraph_key(paragraph) -> str | None:
    """Same normalisation as tools/score_local.py: case, commas, brackets, extra spaces, a dot that ends a word and a
    leading 'paragraph' do not matter; dots inside a number do."""
    text = str(paragraph or "").casefold().replace("§", " ")
    text = re.sub(r"[,;:()\[\]]", " ", text)
    text = re.sub(r"(?<![0-9a-z])\.|\.(?![0-9a-z])", " ", text)
    text = " ".join(text.split())
    text = re.sub(r"^(paragraphs?|para|par|section|clause)\s+", "", text)
    return text or None


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.casefold())


def phrase_on_page(expected_text: str, page_text: str, n: int = 5) -> bool:
    """True if any run of n consecutive words of the expected text appears on the page.
    The experts paraphrase a little, so one matching n-gram is enough to show the text is there."""
    page = " " + " ".join(words(page_text)) + " "
    w = words(expected_text)
    if len(w) < n:
        return " ".join(w) in page
    return any(f" {' '.join(w[i:i + n])} " in page for i in range(len(w) - n + 1))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--expected", default=str(ROOT / "cases" / "dev-rear-lamp" / "expected.json"))
    ap.add_argument("--paragraphs", default=str(ROOT / "build" / "paragraphs.jsonl"))
    args = ap.parse_args(argv)

    expected = json.loads(Path(args.expected).read_text(encoding="utf-8"))["requirements"]
    records = [json.loads(l) for l in Path(args.paragraphs).read_text(encoding="utf-8").splitlines() if l.strip()]
    records = [r for r in records if r["role"] == "base"]

    # our index: (regulation, key) -> records; (regulation, page) -> records covering that page
    by_label: dict[tuple, list[dict]] = defaultdict(list)
    by_page: dict[tuple, list[dict]] = defaultdict(list)
    for r in records:
        by_label[(r["regulation"], paragraph_key(r["paragraph"]))].append(r)
        for p in range(r["page"], r["page_end"] + 1):
            by_page[(r["regulation"], p)].append(r)

    # distinct expected pairs: (regulation, key) -> expected page, label, texts, version
    pairs: dict[tuple, dict] = {}
    for req in expected:
        s = req["source"]
        key = (s["regulation"], paragraph_key(s.get("paragraph")))
        e = pairs.setdefault(key, {"label": s.get("paragraph"), "page": s["page"], "version": s.get("version"),
                                   "texts": []})
        e["page"] = min(e["page"], s["page"])
        e["texts"].append(req["text"])

    pdf_cache: dict[str, pymupdf.Document] = {}

    def page_text(reg: str, page: int) -> str | None:
        files = sorted((PDF_DIR / reg.replace(" ", "_") / "base").glob("*.pdf"))
        if not files:
            return None
        path = str(files[0])
        doc = pdf_cache.setdefault(path, pymupdf.open(path))
        return doc[page - 1].get_text() if 1 <= page <= len(doc) else None

    found = page_ok = 0
    misses = []
    for (reg, key), e in sorted(pairs.items()):
        hits = by_label.get((reg, key), [])
        if hits:
            found += 1
            if any(abs(h["page"] - e["page"]) <= 1 or h["page"] <= e["page"] <= h["page_end"] for h in hits):
                page_ok += 1
            else:
                misses.append((reg, e, "label found, page wrong: " + ", ".join(str(h["page"]) for h in hits), None))
            continue
        text = page_text(reg, e["page"])
        phrase = text is not None and any(phrase_on_page(t, text) for t in e["texts"])
        on_page = sorted({r["paragraph"] for r in by_page.get((reg, e["page"]), [])})
        closest = difflib.get_close_matches(e["label"], on_page, n=1, cutoff=0)
        status = ("page missing" if text is None else
                  "label miss, phrase on page" if phrase else "label miss, phrase NOT on page")
        misses.append((reg, e, status, closest[0] if closest else None))

    n = len(pairs)
    by_page_with_phrase = sum(1 for _, _, s, _ in misses if s == "label miss, phrase on page")
    print(f"Expected: {len(expected)} requirements, {n} distinct (regulation, paragraph) pairs")
    print(f"Found by label:                    {found:>4} / {n}  ({found / n:.1%})")
    print(f"  ... with page within +/-1:       {page_ok:>4} / {found}")
    print(f"Not found by label:                {n - found:>4}")
    print(f"  ... page exists + phrase on it:  {by_page_with_phrase:>4}")
    print(f"  ... page exists, phrase missing: {sum(1 for _, _, s, _ in misses if s.endswith('NOT on page')):>4}")
    print(f"  ... page missing:                {sum(1 for _, _, s, _ in misses if s == 'page missing'):>4}")
    ok = found + by_page_with_phrase
    print(f"Found by label OR by page+phrase:  {ok:>4} / {n}  ({ok / n:.1%})")

    print("\nPer regulation (base version):")
    print(f"  {'regulation':<10} {'expert reqs':>11} {'expert paras':>13} {'our paras':>10} {'our shall paras':>16}"
          f" {'found':>6}")
    for reg in sorted({k[0] for k in pairs}):
        reqs = sum(1 for q in expected if q["source"]["regulation"] == reg)
        exp_p = sum(1 for k in pairs if k[0] == reg)
        ours = [r for r in records if r["regulation"] == reg]
        shall = sum(1 for r in ours if r["shall_count"])
        f = sum(1 for k in pairs if k[0] == reg and by_label.get(k))
        print(f"  {reg:<10} {reqs:>11} {exp_p:>13} {len(ours):>10} {shall:>16} {f:>6}")

    print(f"\nMisses ({len(misses)}):")
    for reg, e, status, closest in misses:
        print(f"  {reg:<8} {e['label']!s:<28} page {e['page']:>3}  {status}"
              + (f"  | closest on page: {closest}" if closest else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
