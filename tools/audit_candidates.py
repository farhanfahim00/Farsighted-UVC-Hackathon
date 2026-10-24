"""Offline development evaluation. Never imported by the model runtime."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.contracts import save
from src.packets import EvidenceIndex
from tools.score_local import _paragraph_key


def audit(index, selection, expected):
    selected = set(selection['target_ids']) if selection.get('exact_targets') else index.descendants(selection['target_ids'])
    lookup = defaultdict(list)
    for row in index.rows:
        lookup[(row['regulation'], row['version'], _paragraph_key(row['paragraph']))].append(row)
    details = []
    for req in expected['requirements']:
        src = req['source']
        rows = lookup[(src['regulation'], src['version'], _paragraph_key(src.get('paragraph'))) ]
        found = any(r['id'] in selected for r in rows)
        details.append({'id': req['id'], 'source': src, 'covered': found,
            'gap': None if found else ('index_missing' if not rows else 'selection_miss'),
            'matching_ids': [r['id'] for r in rows],
            'text': req['text']})
    covered = sum(r['covered'] for r in details)
    gold_keys = {(r['source']['regulation'], r['source']['version'], _paragraph_key(r['source'].get('paragraph'))) for r in details}
    matched_keys = {(r['source']['regulation'], r['source']['version'], _paragraph_key(r['source'].get('paragraph'))) for r in details if r['covered']}
    return {'expected_requirements': len(details), 'requirements_with_selected_source': covered,
        'source_coverage': covered / len(details) if details else None,
        'expected_distinct_paragraphs': len(gold_keys), 'selected_expected_paragraphs': len(matched_keys),
        'selected_paragraphs': len(selected), 'gaps': dict(Counter(r['gap'] for r in details if not r['covered'])),
        'by_regulation': {reg: {'expected': sum(r['source']['regulation'] == reg for r in details),
            'covered': sum(r['source']['regulation'] == reg and r['covered'] for r in details)}
            for reg in sorted({r['source']['regulation'] for r in details})},
        'details': details,
        'warning': 'Source selection coverage is an upper bound on extractable gold coverage, not semantic recall or F2.'}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--index', default='runs/shared-index')
    p.add_argument('--selection', required=True)
    p.add_argument('--expected', default='cases/dev-rear-lamp/expected.json')
    p.add_argument('--out', required=True)
    args = p.parse_args()
    result = audit(EvidenceIndex(args.index), json.loads(Path(args.selection).read_text(encoding='utf-8')),
                   json.loads(Path(args.expected).read_text(encoding='utf-8')))
    save(args.out, result)
    print(json.dumps({k: v for k, v in result.items() if k != 'details'}, indent=2))
