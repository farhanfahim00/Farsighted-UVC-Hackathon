#!/usr/bin/env python3
"""Strict check of the evidence index in build/ against a worked example's expected.json.

    python src/check_index.py
    python src/check_index.py --expected cases/dev-rear-lamp/expected.json --build build

Coverage comes from the index alone (no PDF is opened). Labels are normalised with the scorer's own
tools/score_local.py:_paragraph_key (imported, not copied). A distinct expected (regulation, version, paragraph) is
  * found          when a paragraph record has the same regulation, version and key;
  * page exact     when, in addition, every page the experts cite for it lies within page..page_end of one such record;
  * within +/-1    (diagnostic only, not counted as found) when each cited page is at most one page outside.

Integrity checks (must all be 0): duplicate ids, broken source_line_ids / footnote_context_ids (not in pages.jsonl),
dangling links (parent_id, children_ids, parent_chain, table_ids, cross_refs, tables' owner / entry ids).

Exit code 1 when coverage is below 100% of the expected pairs or any integrity check fails.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from score_local import _paragraph_key  # noqa: E402  (the scorer's normalisation, used as is)


def read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def integrity(records: list[dict], pages: list[dict], tables: list[dict], defs: list[dict]) -> dict[str, int]:
    """Counts of integrity problems; every value must be 0."""
    line_ids = [ln["id"] for p in pages for ln in p["lines"]]
    known_lines = set(line_ids)
    ids = {r["id"] for r in records}
    table_ids = {t["id"] for t in tables}
    problems: Counter = Counter()
    for name, rows in (("paragraphs", records), ("tables", tables), ("definitions", defs)):
        problems[f"duplicate {name} ids"] = sum(n - 1 for n in Counter(r["id"] for r in rows).values() if n > 1)
    problems["duplicate line ids"] = len(line_ids) - len(known_lines)
    for r in records:
        problems["broken source_line_ids"] += sum(1 for i in r["source_line_ids"] if i not in known_lines)
        problems["paragraphs without source lines"] += not r["source_line_ids"]
        problems["broken footnote_context_ids"] += sum(1 for i in r["footnote_context_ids"] if i not in known_lines)
        links = [r["parent_id"]] if r["parent_id"] else []
        links += r["children_ids"] + r["parent_chain"] + [x["target_id"] for x in r["cross_refs"] if x["target_id"]]
        problems["dangling paragraph links"] += sum(1 for i in links if i not in ids)
        problems["dangling table_ids"] += sum(1 for i in r["table_ids"] if i not in table_ids)
    for t in tables:
        problems["dangling table owner / entry ids"] += sum(
            1 for i in (t["owner_paragraph_id"], t["table_entry_id"]) if i and i not in ids)
        problems["broken table source_line_ids"] += sum(1 for i in t["source_line_ids"] if i not in known_lines)
    for d in defs:
        problems["dangling definition paragraph ids"] += d["paragraph_id"] not in ids
    return dict(problems)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--expected", default=str(ROOT / "cases" / "dev-rear-lamp" / "expected.json"))
    ap.add_argument("--build", default=str(ROOT / "build"), help="folder with paragraphs.jsonl, pages.jsonl, ...")
    args = ap.parse_args(argv)
    build = Path(args.build)

    expected = json.loads(Path(args.expected).read_text(encoding="utf-8"))["requirements"]
    records = read_jsonl(build / "paragraphs.jsonl")
    pages = read_jsonl(build / "pages.jsonl")
    tables = read_jsonl(build / "tables.jsonl")
    defs = read_jsonl(build / "definitions.jsonl")
    if not records or not pages:
        print(f"error: {build}/paragraphs.jsonl or pages.jsonl missing or empty; run src/indexer.py", file=sys.stderr)
        return 1

    by_key: dict[tuple, list[dict]] = defaultdict(list)
    for r in records:
        if r['role'] == 'base':
            by_key[(r["regulation"], r["version"], _paragraph_key(r["paragraph"]))].append(r)

    # distinct expected (regulation, version, key) -> label, cited pages
    pairs: dict[tuple, dict] = {}
    for req in expected:
        s = req["source"]
        key = (s["regulation"], s.get("version", ""), _paragraph_key(s.get("paragraph")))
        e = pairs.setdefault(key, {"label": s.get("paragraph"), "pages": set(), "reqs": 0})
        e["pages"].add(s["page"])
        e["reqs"] += 1

    found = exact = near = 0
    misses, page_misses, found_recs = [], [], []
    for key, e in sorted(pairs.items()):
        hits = by_key.get(key, [])
        if not hits:
            misses.append((key, e))
            continue
        found += 1
        best = next((h for h in hits if all(h["page"] <= pg <= h["page_end"] for pg in e["pages"])), None)
        if best is not None:
            exact += 1
        elif any(all(h["page"] - 1 <= pg <= h["page_end"] + 1 for pg in e["pages"]) for h in hits):
            near += 1
            page_misses.append((key, e, hits, "within +/-1"))
        else:
            page_misses.append((key, e, hits, "page differs"))
        found_recs.append(best or hits[0])

    n = len(pairs)
    print(f"Expected: {len(expected)} requirements, {n} distinct (regulation, version, paragraph) pairs")
    print(f"Found (regulation + version + key):  {found:>4} / {n}  ({found / n:.1%})")
    print(f"  ... page exact (within page..page_end): {exact:>4} / {found}")
    print(f"  ... only within +/-1 (diagnostic):      {near:>4}")
    print(f"  ... page differs:                       {found - exact - near:>4}")

    no_ob = [r for r in found_recs if not r["has_obligation"]]
    print("\nStructure of the found expected paragraphs:")
    print(f"  with a title:                           {sum(1 for r in found_recs if r['title']):>4} / {found}")
    print(f"  with a non-empty breadcrumb:            {sum(1 for r in found_recs if r['breadcrumb']):>4} / {found}")
    print(f"  with a title or breadcrumb:             "
          f"{sum(1 for r in found_recs if r['title'] or r['breadcrumb']):>4} / {found}")
    print(f"  without an obligation signal:           {len(no_ob):>4}")
    print(f"    ... inherits_obligation:              {sum(1 for r in no_ob if r['inherits_obligation']):>4}")
    print(f"    ... heading_value_item:               {sum(1 for r in no_ob if r['heading_value_item']):>4}")
    print(f"    ... either of the two:                "
          f"{sum(1 for r in no_ob if r['inherits_obligation'] or r['heading_value_item']):>4}")
    print(f"    ... neither:                          "
          f"{sum(1 for r in no_ob if not (r['inherits_obligation'] or r['heading_value_item'])):>4}"
          + ("  " + ", ".join(f"{r['regulation']} {r['paragraph']}" for r in no_ob
                              if not (r['inherits_obligation'] or r['heading_value_item'])) if no_ob else ""))

    problems = integrity(records, pages, tables, defs)
    print("\nIntegrity (all must be 0):")
    for name, count in problems.items():
        print(f"  {name:<40} {count:>6}{'' if count == 0 else '   <-- FAIL'}")

    print("\nPer regulation:")
    print(f"  {'regulation':<10} {'version':<8} {'expert reqs':>11} {'expert paras':>13} {'our paras':>10}"
          f" {'our shall paras':>16} {'found':>6} {'exact':>6}")
    for reg, ver in sorted({k[:2] for k in pairs}):
        keys = [k for k in pairs if k[:2] == (reg, ver)]
        ours = [r for r in records if r["regulation"] == reg and r["version"] == ver]
        f = [k for k in keys if by_key.get(k)]
        x = sum(1 for k in f if any(all(h["page"] <= pg <= h["page_end"] for pg in pairs[k]["pages"])
                                    for h in by_key[k]))
        print(f"  {reg:<10} {ver:<8} {sum(pairs[k]['reqs'] for k in keys):>11} {len(keys):>13} {len(ours):>10}"
              f" {sum(1 for r in ours if r['shall_count']):>16} {len(f):>6} {x:>6}")

    if misses:
        print(f"\nNot found ({len(misses)}):")
        for (reg, ver, _), e in misses:
            print(f"  {reg} {ver}  {e['label']!s:<28} pages {sorted(e['pages'])}")
    if page_misses:
        print(f"\nFound, page not exact ({len(page_misses)}):")
        for (reg, ver, _), e, hits, why in page_misses:
            spans = ", ".join(f"{h['page']}-{h['page_end']}" for h in hits)
            print(f"  {reg} {ver}  {e['label']!s:<28} expected {sorted(e['pages'])}  ours {spans}  ({why})")

    failed = found < n or exact < n or any(problems.values())
    print(f"\n{'FAIL' if failed else 'OK'}: coverage {found}/{n}, exact pages {exact}/{n}, integrity problems {sum(problems.values())}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
