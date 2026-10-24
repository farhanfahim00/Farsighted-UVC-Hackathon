#!/usr/bin/env python3
"""Validate a manually reviewed change-list and export the organizer subgoal JSON.

Use only if the model run produced needs-review.json or you edited proposals.
No API calls; re-checks old IDs and actual PDF paragraph/page citations.
"""
import argparse
import json
from pathlib import Path
import sys

from src.contracts import save
from src.subgoal import parse_pdf
from tools.subgoal_pipeline import _verify_changes
from tools.validate import validate


def finalize(rows, old_reqs, old_pdf, new_pdf, team_id, case_id):
    clean, issues = _verify_changes(rows, old_reqs, parse_pdf(new_pdf), parse_pdf(old_pdf))
    data = {'team_id': team_id, 'case_id': case_id, 'changes': clean}
    issues += [str(x) for x in validate(data, requirements_old=old_reqs, strict=True)]
    if not clean:
        issues.append('No reviewed change proposals; cannot silently export an empty candidate.')
    return data, issues


def main():
    a = argparse.ArgumentParser(description=__doc__)
    a.add_argument('proposals', help='JSON list, object with changes[], or needs-review.json with proposals[]')
    a.add_argument('--old-pdf', default='subgoal/dev/old.pdf')
    a.add_argument('--new-pdf', default='subgoal/dev/new.pdf')
    a.add_argument('--requirements-old', default='subgoal/dev/requirements_old.json')
    a.add_argument('--team-id', default='team-07')
    a.add_argument('--case-id', default='subgoal-dev')
    a.add_argument('--out', required=True)
    args = a.parse_args()
    raw = json.loads(Path(args.proposals).read_text(encoding='utf-8'))
    rows = raw if isinstance(raw, list) else raw.get('changes', raw.get('proposals'))
    if not isinstance(rows, list):
        a.error('proposals must be a list, or an object containing changes[] or proposals[]')
    old_reqs = json.loads(Path(args.requirements_old).read_text(encoding='utf-8'))
    data, issues = finalize(rows, old_reqs, args.old_pdf, args.new_pdf, args.team_id, args.case_id)
    if issues:
        print('NOT EXPORTED: corrections needed:', file=sys.stderr)
        for issue in issues:print(' - '+issue, file=sys.stderr)
        return 1
    save(Path(args.out), data)
    print(f'Validated {len(data["changes"])} changes: {args.out}')
    return 0

if __name__ == '__main__':
    sys.exit(main())
