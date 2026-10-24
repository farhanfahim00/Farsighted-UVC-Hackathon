#!/usr/bin/env python3
"""Review an existing Farsighted run without repeating regulation extraction.

By default no paid API requests are sent. --online requires explicit local
request and spend caps. Original submission/ledger/caches remain untouched.
No development expected.json is read by this command.

PowerShell:
 python -m tools.refine_submission --run runs/dev-full-updated-v1 --out runs/dev-review-v2 --dry-run
 python -m tools.refine_submission --run runs/dev-full-updated-v1 --out runs/dev-review-v2 --config config/runtime.json --online --max-attempts 400 --max-usd 45
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
from collections import Counter
import threading

import jsonschema

from src.contracts import ROOT, digest, save
from src.llm import Client
from src.refinement import (apply_review, batches, confirmed_rejections, review_batch,
                            review_context, review_summary)


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def prepare_run(folder, max_evidence_chars):
    required = ('submission.json', 'ledger.json', 'profile.json', 'selection.json', 'scope-input.json')
    missing = [filename for filename in required if not (folder / filename).is_file()]
    if missing:
        raise ValueError('Run is missing required evidence inputs: ' + ', '.join(missing))
    submission = read(folder / 'submission.json')
    rows, facts = review_context(
        submission, read(folder / 'ledger.json'), read(folder / 'profile.json'),
        read(folder / 'selection.json'), read(folder / 'scope-input.json'),
        max_evidence_chars=max_evidence_chars)
    return submission, rows, facts


def validate_submission(path):
    checked = subprocess.run([sys.executable, str(ROOT / 'tools/validate.py'), str(path), '--strict'],
                             capture_output=True, text=True, timeout=60)
    if checked.returncode:
        raise ValueError('Organizer validation failed for ' + str(path) + ': ' +
                         (checked.stdout + checked.stderr)[-1200:])
    return (checked.stdout + checked.stderr).strip()


def execute(args):
    args.out.mkdir(parents=True, exist_ok=True)
    submission, rows, facts = prepare_run(args.run, args.max_evidence_chars)
    work = batches(rows, args.batch_size)
    status = {'state': 'prepared', 'source_run': str(args.run.resolve()),
              'source_submission_sha256': __import__('hashlib').sha256((args.run / 'submission.json').read_bytes()).hexdigest(),
              'requirements_input': len(rows), 'batches': len(work),
              'missing_or_reduced_evidence': sum(not r['evidence_complete'] for r in rows),
              'has_development_gold_access': False}
    save(args.out / 'review-status.json', status)
    if args.dry_run:
        by_reg = Counter(r['source']['regulation'] for r in rows)
        report = {'review_status': status, 'by_regulation': dict(by_reg),
                  'approval_variants_preview': {
                      policy: apply_review(submission, {}, policy)[1]
                      for policy in ('keep_all', 'prefer_consolidated')},
                  'warning': 'No model review performed; approval-regime option is not a proven applicability decision.'}
        save(args.out / 'preflight.json', report)
        print(json.dumps(report, indent=2))
        return 0

    config = read(args.config)
    config.update(max_attempts=args.max_attempts if args.max_attempts is not None else config['max_attempts'],
                  max_reserved_usd=args.max_usd if args.max_usd is not None else config['max_reserved_usd'])
    if args.model:
        config['model'] = args.model
    if args.online and (args.max_attempts is None or args.max_usd is None):
        raise ValueError('--online requires explicit --max-attempts and --max-usd')
    # Both passes use a stable request-content cache. Successes are recovered
    # across interruptions; failed/missing decisions NEVER trigger deletion.
    client = Client(config, ROOT / 'runs/runtime-cache', args.out / 'usage.jsonl', online=args.online)
    status.update(state='reviewing_first_pass', model=config['model'])
    save(args.out / 'review-status.json', status)

    def run_batches(groups, second=False):
        progress_lock = threading.Lock()
        progress = {'done': 0, 'failed': 0}
        label = 'confirmation' if second else 'first-pass'
        def one(group):
            try:
                result = {'ids': [r['id'] for r in group],
                          'decisions': review_batch(client, group, facts, second=second), 'error': None}
            except (RuntimeError, ValueError, OSError, KeyError, TypeError,
                    json.JSONDecodeError, jsonschema.ValidationError) as exc:
                result = {'ids': [r['id'] for r in group], 'decisions': {},
                          'error': type(exc).__name__ + ': ' + str(exc)}
            with progress_lock:
                progress['done'] += 1
                progress['failed'] += bool(result['error'])
                if progress['done'] == len(groups) or progress['done'] % 10 == 0:
                    print(f'Review {label}: {progress["done"]}/{len(groups)} batches; '
                          f'errors={progress["failed"]}; estimated USD={client.spent:.2f}', flush=True)
            return result
        return client.parallel(one, groups)

    first_results = run_batches(work)
    first = {rid: d for result in first_results for rid, d in result['decisions'].items()}
    first_errors = [r for r in first_results if r['error']]
    save(args.out / 'review-pass-1.json', {'decisions': first, 'errors': first_errors})
    status.update(first_pass_decisions=len(first), first_pass_failures=len(first_errors),
                  api_attempts=client.attempts, estimated_usd=client.spent)
    save(args.out / 'review-status.json', status)
    if first_errors or len(first) != len(rows):
        status['state'] = 'partial_no_submission_written'
        save(args.out / 'review-status.json', status)
        print(json.dumps(status, indent=2))
        return 2

    candidates = [r for r in rows if r['evidence_complete'] and
                  first[r['id']]['verdict'] == 'reject' and
                  first[r['id']]['confidence'] == 'high' and
                  first[r['id']]['issue'] in (
                      'wrong_device_scope', 'administrative_only',
                      'unsupported_by_source', 'contradicted_by_product')]
    confirmation_batches = batches(candidates, args.batch_size)
    status.update(state='confirming_deletions', possible_rejections=len(candidates),
                  confirmation_batches=len(confirmation_batches))
    save(args.out / 'review-status.json', status)
    second_results = run_batches(confirmation_batches, second=True)
    second = {rid: d for result in second_results for rid, d in result['decisions'].items()}
    second_errors = [r for r in second_results if r['error']]
    save(args.out / 'review-pass-2.json', {'decisions': second, 'errors': second_errors})
    status.update(second_pass_decisions=len(second), second_pass_failures=len(second_errors),
                  api_attempts=client.attempts, estimated_usd=client.spent)
    if second_errors or len(second) != len(candidates):
        status['state'] = 'partial_no_submission_written'
        save(args.out / 'review-status.json', status)
        print(json.dumps(status, indent=2))
        return 2

    rejected_safe = confirmed_rejections(rows, first, second)
    rejected_extended = confirmed_rejections(rows, first, second, allow_unsupported=True)
    summary = review_summary(rows, first, second, rejected_extended)
    summary['rejected_ids_safe'] = rejected_safe
    summary['rejected_ids_extended'] = rejected_extended
    summary['variants'] = {}
    variants = [
        ('conservative', 'keep_all', rejected_safe),
        ('evidence_filtered', 'keep_all', rejected_extended),
        ('consolidated_safe', 'prefer_consolidated', rejected_safe),
        ('consolidated_evidence', 'prefer_consolidated', rejected_extended),
    ]
    for name, policy, rejected in variants:
        variant, stats = apply_review(submission, rejected, policy=policy)
        path = args.out / ('submission-reviewed-' + name + '.json')
        save(path, variant)
        validation = validate_submission(path)
        summary['variants'][name] = {**stats, 'file': path.name,
                                     'organizer_validator_passed': True,
                                     'validator_output': validation[-300:]}
    save(args.out / 'review-summary.json', summary)
    status.update(state='review_complete', confirmed_rejections_safe=len(rejected_safe),
                  confirmed_rejections_extended=len(rejected_extended),
                  variants={k: v['kept_rows'] for k, v in summary['variants'].items()})
    save(args.out / 'review-status.json', status)
    print(json.dumps({'status': status, 'summary': summary['variants']}, indent=2))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run', type=Path, required=True, help='Existing extraction run containing ledger and submission')
    p.add_argument('--out', type=Path, required=True, help='New folder; existing extraction is never overwritten')
    p.add_argument('--config', type=Path, default=ROOT / 'config/runtime.json')
    p.add_argument('--model', help='Optional independent review model from your API account')
    p.add_argument('--batch-size', type=int, default=6)
    p.add_argument('--max-evidence-chars', type=int, default=6500)
    p.add_argument('--dry-run', action='store_true', help='No API; show candidate counts and family-policy preview')
    p.add_argument('--online', action='store_true', help='Allow paid Claude calls with explicit caps')
    p.add_argument('--max-attempts', type=int)
    p.add_argument('--max-usd', type=float)
    args = p.parse_args(argv)
    try:
        return execute(args)
    except (OSError, ValueError, RuntimeError) as exc:
        print(type(exc).__name__ + ': ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
