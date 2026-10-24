#!/usr/bin/env python3
"""Identify source targets with no generated duty, without using gold answers.

Source-coverage warnings are triage hints, not proof that every target must
produce a requirement. This tool never changes a submission or calls an LLM.
"""
import argparse
from collections import Counter
import json
from pathlib import Path

from src.contracts import save


def source_key(obj):
    return obj.get('regulation'), obj.get('version'), obj.get('paragraph')


def classify_target(target):
    if target.get('is_table') or target.get('kind') == 'table':
        return 'table'
    if target.get('heading_value_item') or target.get('inherits_obligation'):
        return 'inherited_or_list'
    if target.get('has_obligation'):
        return 'explicit_obligation'
    return 'other'


def audit(packets, ledger, accounting):
    if len(packets) != len(ledger) or len(ledger) != len(accounting):
        raise ValueError('Packets, results and accounting must match in length')
    flagged = []
    stats = Counter()
    found = set()
    for p, result, a in zip(packets, ledger, accounting):
        if p['packet_id'] != result.get('packet_id') or p['packet_id'] != a.get('packet_id'):
            raise ValueError('Misaligned extraction packet and accounting')
        states = a['state']
        stats['packets_' + states] += 1
        emission = {source_key(row['source']) for row in result.get('valid', [])}
        # A table pseudo paragraph can contribute evidence to its governing
        # numbered clause; the exported clause citation is authoritative here.
        for target in p['targets']:
            key = source_key(target)
            category = classify_target(target)
            if key in emission:
                stats['targets_with_direct_source'] += 1
                continue
            stats['targets_without_direct_source'] += 1
            if category == 'other':
                continue
            pid = target.get('id')
            if pid in found:
                continue
            found.add(pid)
            priority = ('high' if states == 'completed' and category != 'other' else
                        'medium' if states == 'no_applicable_duty' else 'blocked_or_deferred')
            flagged.append({'target_id': pid, 'regulation': target['regulation'],
                            'version': target['version'], 'paragraph': target['paragraph'],
                            'page': target.get('page'), 'category': category,
                            'packet_state': states, 'triage_priority': priority,
                            'sample_text': target.get('text', '')[:300]})
            stats['flagged_' + priority] += 1
    return {'summary': dict(stats),
            'candidates': sorted(flagged, key=lambda r: (
                {'high': 0, 'medium': 1, 'blocked_or_deferred': 2}[r['triage_priority']],
                r['regulation'], str(r['paragraph']))),
            'warning': 'No gold data used; no direct source output is not proof that a requirement was missed.'}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args(argv)
    try:
        files = [args.run / name for name in ('packets.json', 'ledger.json', 'target-accounting.json')]
        packets, results, accounting = [json.loads(file.read_text(encoding='utf-8')) for file in files]
        report = audit(packets, results, accounting)
        save(args.out, report)
        print(json.dumps({'summary': report['summary'],
                          'top_high_priority': [r['regulation'] + '::' + r['paragraph']
                                                for r in report['candidates'] if r['triage_priority'] == 'high'][:25],
                          'report': str(args.out)}, indent=2))
        return 0
    except (OSError, ValueError, KeyError) as exc:
        p.error(str(exc))


if __name__ == '__main__':
    raise SystemExit(main())
