#!/usr/bin/env python3
"""DEVELOPMENT-ONLY paragraph coverage audit against the supplied reference.

Never imported by inference. Not semantic accuracy: seeing the reference
paragraph does not prove that its atomic obligation will be recovered.
"""
import argparse
import json
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--evidence-packets', required=True)
    p.add_argument('--expected', default='subgoal/dev/expected.json')
    p.add_argument('--requirements-old', default='subgoal/dev/requirements_old.json')
    a = p.parse_args()
    packets = json.loads(Path(a.evidence_packets).read_text(encoding='utf-8'))
    expected = json.loads(Path(a.expected).read_text(encoding='utf-8'))['changes']
    old = json.loads(Path(a.requirements_old).read_text(encoding='utf-8'))
    reference_locations = {c['source']['paragraph'] for c in expected if c.get('source')}
    candidate_locations = {e['new']['label'] for b in packets
                           for e in b['changed_provisions'] if e.get('new')}
    reference_old = {c['requirement_id'] for c in expected
                     if c['change'] in ('modified', 'removed')}
    seen_ids = {r['id'] for b in packets for r in b['old_requirements']}
    result = {'candidate_locations': len(candidate_locations),
              'reference_locations': len(reference_locations),
              'reference_locations_found': len(reference_locations & candidate_locations),
              'missing_reference_locations': sorted(reference_locations-candidate_locations),
              'reference_old_ids': len(reference_old),
              'old_ids_in_model_context': len(reference_old & seen_ids),
              'missing_old_ids': sorted(reference_old - seen_ids),
              'warning': 'Offline development reference used ONLY by this auditing tool; source discovery is NOT semantic matching or F2.'}
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
