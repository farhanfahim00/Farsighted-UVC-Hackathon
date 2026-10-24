"""Single entry point: prepare (offline) or run (cache-only unless --online)."""
import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys

from tools.product_contract import prepare
from .contracts import ROOT, EXTRACTION, digest, prompt, runtime_fingerprint, save
from .extraction import safe_extract
from .guard import export
from .llm import Client
from .packets import EvidenceIndex
from .product import build_profile
from .selection import select
from .verification import obligation_checklist


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'candidates', 'run'))
    parser.add_argument('--case', type=Path, required=True)
    parser.add_argument('--inputs', nargs='+', required=True, help='Explicit product files only, never evaluation answers')
    parser.add_argument('--index', type=Path, default=ROOT / 'runs/shared-index')
    parser.add_argument('--config', type=Path, default=ROOT / 'config/runtime.json')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--targets', nargs='+', help='Explicit Regulation::Paragraph targets for a partial diagnostic run')
    parser.add_argument('--online', action='store_true', help='Allow bounded paid requests on cache misses')
    parser.add_argument('--max-attempts', type=int)
    parser.add_argument('--max-usd', type=float)
    parser.add_argument('--text-only', action='store_true', help='Controlled extraction comparison; product images are still supplied')
    parser.add_argument('--reuse-candidates', type=Path, help='Reuse a checked product profile and screening result from a prior candidate run')
    parser.add_argument('--legacy-cache-fingerprint', action='append', default=[],
                        help='Previous run-status runtime_fingerprint for exact-payload legacy cache reuse')
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    fingerprint = runtime_fingerprint()
    status = {'state': 'preparing', 'partial_targets': bool(args.targets), 'online': args.online,
              'runtime_fingerprint': fingerprint, 'semantic_score': None}
    save(args.out / 'run-status.json', status)
    client = None
    def stage(name, **details):
        status.update(stage=name, **details)
        save(args.out / 'run-status.json', status)
        print('Stage: ' + name, flush=True)
    try:
        packet = prepare(args.case, args.inputs)
        save(args.out / 'product-input.json', packet)
        index = EvidenceIndex(args.index)
        index_identity = getattr(index, 'identity', digest([index.format, [r['id'] for r in index.rows]]))
        status.update(index_format=index.format, index_folder=str(args.index.resolve()),
                      evidence_index_identity=index_identity, input_packet_id=packet['packet_id'])
        targets = [index.target(s) for s in args.targets] if args.targets else []
        packets = index.chunks(targets) if targets else []
        save(args.out / 'scope-input.json', index.outlines())
        save(args.out / 'packets.json', packets)
        status['unresolved_reference_count'] = sum(len(p['unresolved_references']) for p in packets)
        if args.command == 'prepare':
            status.update(state='prepared', case_id=packet['case_id'], packets=len(packets), api_attempts=0)
            return 0
        config = json.loads(args.config.read_text(encoding='utf-8'))
        if not config.get('team_id'):
            raise ValueError('team_id is required in runtime config')
        if args.max_attempts is not None:
            config['max_attempts'] = args.max_attempts
        if args.max_usd is not None:
            config['max_reserved_usd'] = args.max_usd
        config['legacy_cache_fingerprints'] = list(dict.fromkeys(
            [*config.get('legacy_cache_fingerprints', []), *args.legacy_cache_fingerprint]))
        # This file can contain local configuration, not credentials. Do not
        # include any API key in runtime.json or the exported audit ZIP.
        save(args.out / 'effective-config.json', config)
        client = Client(config, ROOT / 'runs/runtime-cache', args.out / 'usage.jsonl', online=args.online)
        if args.reuse_candidates:
            prior = args.reuse_candidates
            prior_status = json.loads((prior / 'run-status.json').read_text(encoding='utf-8'))
            if prior_status.get('input_packet_id') != packet['packet_id'] or \
               prior_status.get('index_folder') != str(args.index.resolve()) or \
               prior_status.get('index_format') != index.format:
                raise ValueError('Candidate run input or index differs; cannot reuse its screening')
            previous_identity = prior_status.get('evidence_index_identity')
            if previous_identity and previous_identity != index_identity:
                raise ValueError('Evidence index was rebuilt; cannot reuse screening from a different index')
            if not previous_identity:
                status['legacy_index_identity_unverified'] = True
                status['legacy_index_warning'] = 'Prior status has no content hash; verify index was not rebuilt in place'
            if prior_status.get('runtime_fingerprint'):
                client.legacy_fingerprints.append(prior_status['runtime_fingerprint'])
            product = json.loads((prior / 'profile.json').read_text(encoding='utf-8'))
            if product.get('profile', {}).get('packet_id') != packet['packet_id']:
                raise ValueError('Reused product profile does not match input packet')
            status['reused_candidate_run'] = str(prior.resolve())
        else:
            stage('product_profile')
            product = build_profile(client, args.case, packet, fingerprint)
            product['original_text_evidence'] = packet['evidence']
        save(args.out / 'profile.json', product)
        if not targets:
            if args.reuse_candidates:
                choice = args.reuse_candidates / 'selection-tiered.json'
                if not choice.is_file():
                    choice = args.reuse_candidates / 'selection.json'
                selection = json.loads(choice.read_text(encoding='utf-8'))
                if not selection.get('target_ids') or any(rid not in index.by_id for rid in selection['target_ids']):
                    raise ValueError('Reused candidate IDs are missing from the current index')
                if any(rid not in selection['target_ids'] for k in ('primary_target_ids', 'review_target_ids')
                       for rid in selection.get(k, [])):
                    raise ValueError('Reused primary/review candidates must be contained in the selected IDs')
            else:
                stage('regulation_screening', indexed_regulations=len(index.by_reg))
                def checkpoint(batch, decisions):
                    key = digest([r['regulation'] for r in batch])
                    save(args.out / 'screening-checkpoints' / (key + '.json'),
                         {'runtime_fingerprint': fingerprint, 'input_packet_id': packet['packet_id'], 'decisions': decisions})
                selection = select(client, index, product, fingerprint, checkpoint=checkpoint)
            save(args.out / 'selection.json', selection)
            if args.command == 'candidates':
                selected_ids = index.descendants(selection['target_ids'])
                selected_rows = [r for r in index.rows if r['id'] in selected_ids]
                save(args.out / 'candidate-summary.json', {
                    'indexed_regulations': len(index.by_reg), 'selected_paragraphs': len(selected_rows),
                    'by_regulation': {reg: sum(r['regulation'] == reg for r in selected_rows)
                                      for reg in index.by_reg}})
                status.update(state='candidates_ready', stage='selection_saved', case_id=packet['case_id'],
                              selected_paragraphs=len(selected_rows))
                return 0
            # Spend extraction capacity on supported candidates before disputed exclusions.
            packets = index.chunks(selection.get('primary_target_ids', selection['target_ids']), expand_descendants=False)
            if selection.get('review_target_ids'):
                packets.extend(index.chunks(selection['review_target_ids'], expand_descendants=False))
            save(args.out / 'packets.json', packets)
            status['unresolved_reference_count'] = sum(len(p['unresolved_references']) for p in packets)
        if not packets:
            raise ValueError('No extraction packets selected; inspect screening before treating this run as complete')
        stage('requirement_extraction', packets=len(packets))
        def extract_checkpoint(p):
            location = args.out / 'extraction-checkpoints' / (digest(p['packet_id']) + '.json')
            # A checkpoint may only be replayed when the exact evidence, product,
            # extraction instructions and model configuration are unchanged.
            checkpoint_key = digest({'packet_id': p['packet_id'], 'product': product,
                                     'input_packet_id': packet['packet_id'],
                                     'index_identity': index_identity, 'schema': EXTRACTION,
                                     'prompt': prompt('extract'), 'model': config['model'],
                                     'max_output_tokens': config['max_output_tokens'],
                                     'text_only': args.text_only, 'format_version': 2})
            if location.exists():
                previous = json.loads(location.read_text(encoding='utf-8'))
                old_result = previous.get('result', {})
                if previous.get('checkpoint_key') == checkpoint_key and \
                   old_result.get('packet_id') == p['packet_id'] and \
                   not old_result.get('errors') and not old_result.get('quarantine') and \
                   (old_result.get('valid') or old_result.get('exclusion_reason')):
                    return old_result
            result = safe_extract(client, p, product, fingerprint, images=not args.text_only)
            save(location, {'checkpoint_key': checkpoint_key, 'runtime_fingerprint': fingerprint,
                            'input_packet_id': packet['packet_id'], 'evidence_index_identity': index_identity,
                            'result': result})
            return result
        results = client.parallel(extract_checkpoint, packets)
        save(args.out / 'ledger.json', results)
        if len(results) != len(packets) or any(r.get('packet_id') != p['packet_id'] for p, r in zip(packets, results)):
            raise ValueError('Extraction results do not account for every selected packet in order')
        accounting = []
        for p, r in zip(packets, results):
            if p.get('context_over_budget'):
                state = 'blocked'
            elif any(str(e).startswith('BudgetExceeded:') for e in r.get('errors', [])):
                state = 'deferred'
            elif r.get('errors') or r.get('quarantine'):
                state = 'failed'
            elif r.get('valid'):
                state = 'completed'
            elif r.get('exclusion_reason'):
                state = 'no_applicable_duty'
            else:
                state = 'failed'
            accounting.append({'packet_id': p['packet_id'], 'target_ids': [t['id'] for t in p['targets']],
                               'state': state, 'accepted_rows': len(r.get('valid', [])),
                               'errors': r.get('errors', [])})
        save(args.out / 'target-accounting.json', accounting)
        checklist = obligation_checklist(results, index, product)
        save(args.out / 'obligation-checklist.json', checklist)
        flagged = [ob for group in checklist for ob in group['obligations'] if ob['review_flags']]
        flag_counts = Counter(flag for ob in flagged for flag in ob['review_flags'])
        save(args.out / 'review-flags-summary.json', {
            'review_flagged_duties': len(flagged),
            'flag_counts': dict(flag_counts.most_common()),
            'note': 'Flags are heuristic review aids, not automatic proof of incorrect requirements.'})
        submission = export(config['team_id'], packet['case_id'], results)
        save(args.out / 'submission.json', submission)
        checked = subprocess.run([sys.executable, str(ROOT / 'tools/validate.py'), str(args.out / 'submission.json'), '--strict'],
                                 capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
        save(args.out / 'official-validation.json', {'returncode': checked.returncode, 'output': checked.stdout + checked.stderr})
        problems = sum(a['state'] in ('blocked', 'deferred', 'failed') for a in accounting)
        coverage = {'indexed_regulations': len(index.by_reg), 'packets_selected': len(packets),
                    'packets_returned': len(results), 'packets_with_errors': sum(bool(r['errors']) for r in results),
                    'packet_states': {state: sum(a['state'] == state for a in accounting)
                                      for state in ('completed', 'no_applicable_duty', 'blocked', 'deferred', 'failed')},
                    'targets_accounted': sum(len(a['target_ids']) for a in accounting),
                    'unique_targets_accounted': len({tid for a in accounting for tid in a['target_ids']}),
                    'reduced_context_packets': sum(bool(p.get('context_reduced')) for p in packets),
                    'quarantined_rows': sum(len(r['quarantine']) for r in results),
                    'review_flagged_duties': len(flagged),
                    'duplicate_rows_reported': sum(len(r.get('duplicates', [])) for r in results),
                    'exported_requirements': len(submission['requirements']),
                    'regulations_with_output': sorted({r['source']['regulation'] for r in submission['requirements']}),
                    'unresolved_references': status['unresolved_reference_count'],
                    'semantic_recall': None, 'note': 'Execution coverage only; correctness requires separate evaluation.'}
        save(args.out / 'coverage.json', coverage)
        incomplete = bool(problems or args.targets or not submission['requirements'] or checked.returncode)
        status.update(stage='exported', state='partial' if incomplete else 'complete',
                      case_id=packet['case_id'], packets=len(packets), packets_needing_review=problems,
                      requirements=len(submission['requirements']), official_validator_passed=checked.returncode == 0,
                      submission_sha256=__import__('hashlib').sha256((args.out / 'submission.json').read_bytes()).hexdigest())
        return int(checked.returncode != 0 or problems > 0)
    except (RuntimeError, ValueError, OSError, KeyError, TypeError) as exc:
        status.update(state='failed', error=type(exc).__name__ + ': ' + str(exc))
        return 1
    finally:
        if client:
            status.update(api_attempts=client.attempts, estimated_usd=client.spent,
                          reserved_usd_after_failures=client.reserved)
        status['warning'] = 'Inspect this status and ledger before using a submission. No automatic portal upload. Source integrity is not semantic accuracy.'
        save(args.out / 'run-status.json', status)
        print(json.dumps(status, indent=2))


if __name__ == '__main__':
    raise SystemExit(main())
