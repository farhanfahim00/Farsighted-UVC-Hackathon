"""Single entry point: prepare (offline) or run (cache-only unless --online)."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from tools.product_contract import prepare
from .contracts import ROOT, runtime_fingerprint, save
from .extraction import safe_extract
from .guard import export
from .llm import Client
from .packets import EvidenceIndex
from .product import build_profile
from .selection import select


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'run'))
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
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    fingerprint = runtime_fingerprint()
    status = {'state': 'preparing', 'partial_targets': bool(args.targets), 'online': args.online,
              'runtime_fingerprint': fingerprint, 'semantic_score': None}
    save(args.out / 'run-status.json', status)
    client = None
    try:
        packet = prepare(args.case, args.inputs)
        save(args.out / 'product-input.json', packet)
        index = EvidenceIndex(args.index)
        status.update(index_format=index.format, index_folder=str(args.index.resolve()),
                      input_packet_id=packet['packet_id'])
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
        save(args.out / 'effective-config.json', config)
        client = Client(config, ROOT / 'runs/runtime-cache', args.out / 'usage.jsonl', online=args.online)
        product = build_profile(client, args.case, packet, fingerprint)
        # Preserve the complete text evidence for screening/extraction as well as the proposed facts.
        product['original_text_evidence'] = packet['evidence']
        save(args.out / 'profile.json', product)
        if not targets:
            selection = select(client, index, product, fingerprint)
            save(args.out / 'selection.json', selection)
            packets = index.chunks(selection['target_ids'])
            save(args.out / 'packets.json', packets)
            status['unresolved_reference_count'] = sum(len(p['unresolved_references']) for p in packets)
        results = client.parallel(lambda p: safe_extract(client, p, product, fingerprint, images=not args.text_only), packets)
        save(args.out / 'ledger.json', results)
        submission = export(config['team_id'], packet['case_id'], results)
        save(args.out / 'submission.json', submission)
        checked = subprocess.run([sys.executable, str(ROOT / 'tools/validate.py'), str(args.out / 'submission.json'), '--strict'],
                                 capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
        save(args.out / 'official-validation.json', {'returncode': checked.returncode, 'output': checked.stdout + checked.stderr})
        problems = sum(bool(r['quarantine'] or r['errors']) for r in results)
        status.update(state='partial' if problems or args.targets else 'complete',
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
