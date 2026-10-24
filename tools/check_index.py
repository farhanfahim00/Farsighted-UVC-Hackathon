"""Evaluate source locators against dev gold, not semantic extraction quality.

Accepts the teammate's paragraph format, the first offline index and the evidence index.
Never searches original PDFs to inflate coverage of an incomplete index.
"""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

from score_local import _paragraph_key

ROOT = Path(__file__).resolve().parents[1]


def evaluate(records, expected):
    by_label = defaultdict(list)
    ids = Counter(r['id'] for r in records)
    for r in records:
        if r['role'] == 'base':
            by_label[(r['regulation'], r['version'], _paragraph_key(r.get('paragraph')))].append(r)
    anchors = defaultdict(set)
    for req in expected:
        s = req['source']
        anchors[(s['regulation'], s['version'], _paragraph_key(s.get('paragraph')))].add(s['page'])
    label_found = exact = near = 0
    misses = []
    for key, pages in sorted(anchors.items()):
        hits = by_label.get(key, [])
        spans = [(r.get('page', r.get('page_start')), r['page_end']) for r in hits]
        label_found += bool(hits)
        exact_ok = bool(hits) and all(any(lo <= page <= hi for lo, hi in spans) for page in pages)
        near_ok = bool(hits) and all(any(lo-1 <= page <= hi+1 for lo, hi in spans) for page in pages)
        exact += exact_ok
        near += near_ok
        if not exact_ok:
            misses.append({'regulation': key[0], 'version': key[1], 'paragraph': key[2],
                           'expected_pages': sorted(pages), 'indexed_spans': spans})
    return {'expected_requirements': len(expected), 'distinct_locators': len(anchors),
            'labels_found_in_correct_version': label_found, 'all_expected_pages_in_exact_span': exact,
            'within_one_page_diagnostic_only': near, 'duplicate_record_ids': sum(n-1 for n in ids.values()),
            'misses': misses,
            'warning': 'Not semantic recall or F2. A matching label/page does not prove preserved conditions, table values or correct obligations.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--paragraphs', type=Path, default=ROOT / 'runs/evidence-base/paragraphs.jsonl')
    parser.add_argument('--expected', type=Path, default=ROOT / 'cases/dev-rear-lamp/expected.json')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    records = [json.loads(line) for line in args.paragraphs.read_text(encoding='utf-8').splitlines() if line.strip()]
    expected = json.loads(args.expected.read_text(encoding='utf-8'))['requirements']
    result = evaluate(records, expected)
    serialized = json.dumps(result, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(serialized, encoding='utf-8')
    print(serialized)
    return int(bool(result['misses'] or result['duplicate_record_ids']))


if __name__ == '__main__':
    raise SystemExit(main())
