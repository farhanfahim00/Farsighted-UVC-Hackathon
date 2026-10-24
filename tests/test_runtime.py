"""Offline regression checks with simulated provider responses. Never spends credits."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import unittest
import uuid
from unittest.mock import patch

from src.contracts import ROOT, EXTRACTION, PROFILE, SCREENING, save
from src.extraction import safe_extract
from src.guard import validate_rows, export
from src.llm import Client, BudgetExceeded, CacheMiss, IncompleteResponse
from src.packets import EvidenceIndex
from src.product import content
from src.pipeline import main
from src.selection import select
from tools.product_contract import prepare, validate_profile


def response(value, stop='end_turn'):
    return {'stop_reason': stop, 'content': [{'type': 'text', 'text': json.dumps(value)}],
            'usage': {'input_tokens': 20, 'output_tokens': 30}}


class RuntimeChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = EvidenceIndex(ROOT / 'runs/evidence-base')
        cls.packet = cls.index.packet([cls.index.target('UN R10::6.9.1')])

    def setUp(self):
        self.folder = ROOT / 'runs/runtime-tests' / uuid.uuid4().hex
        self.folder.mkdir(parents=True)
        self.config = json.loads((ROOT / 'config/runtime.json').read_text())

    def tearDown(self):
        # Only the exact UUID test folder created in setUp is removed.
        if self.folder.parent == ROOT / 'runs/runtime-tests' and len(self.folder.name) == 32:
            shutil.rmtree(self.folder)

    def client(self, send, online=True, **updates):
        return Client({**self.config, **updates}, self.folder / 'cache', self.folder / 'usage.jsonl',
                      online=online, send=send, sleep=lambda _: None)

    def row(self, packet=None):
        packet = packet or self.packet
        ref = packet['allowed_target_refs'][0]
        return {'text': 'The device shall satisfy the cited test condition.', 'applicability': 'conditional',
                'condition': 'if the device is fitted', 'reason': 'Conditional product feature', 'primary_ref': ref, 'refs': [ref]}

    def test_bad_row_does_not_drop_valid_rows_and_duplicate_is_removed(self):
        row = self.row()
        bad = {**row, 'refs': ['INVENTED']}
        result = validate_rows({'requirements': [row, bad, row], 'exclusion_reason': ''}, self.packet)
        self.assertEqual((len(result['valid']), len(result['quarantine']), len(result['duplicates'])), (1, 1, 1))
        self.assertIn(row['condition'], result['valid'][0]['text'])

    def test_source_page_is_evidence_page_not_packet_start(self):
        later = next(r for r in self.index.by_reg['UN R10'] if r['page'] == 13 and r['source_line_ids'])
        packet = self.index.packet([self.index.target('UN R10::6.9.1'), later['id']])
        row = self.row(packet)
        ref = next(alias for alias in packet['allowed_target_refs'] if packet['refs'][alias]['page'] == 13)
        row.update(primary_ref=ref, refs=[ref])
        result = validate_rows({'requirements': [row], 'exclusion_reason': ''}, packet)
        self.assertEqual(result['valid'][0]['source']['page'], 13)
        self.assertEqual(result['valid'][0]['source']['page'], packet['refs'][row['primary_ref']]['page'])
        self.assertEqual(result['valid'][0]['evidence'][0]['id'], packet['refs'][row['primary_ref']]['id'])

    def test_cache_replay_makes_no_new_call_even_concurrently(self):
        calls = []
        client = self.client(lambda *_: calls.append(1) or response({'requirements': [], 'exclusion_reason': 'control'}))
        def ask(_):
            return client.request('system', 'input', EXTRACTION, 'v1')
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(ask, range(4)))
        self.assertEqual(len(calls), 1)
        client.online = False
        ask(None)
        self.assertEqual(client.attempts, 1)

    def test_timeout_retries_count_against_global_budget(self):
        def timeout(*_):
            raise TimeoutError('simulated timeout')
        client = self.client(timeout, max_attempts=2)
        with self.assertRaises(BudgetExceeded):
            client.request('s', 'c', EXTRACTION, 'v')
        self.assertEqual(client.attempts, 2)
        self.assertGreater(client.reserved, 0)

    def test_concurrent_attempt_limit_cannot_overshoot(self):
        client = self.client(lambda *_: response({}), max_attempts=3)
        def ask(i):
            try:
                client.request('system', str(i), EXTRACTION, 'v')
                return 'sent'
            except BudgetExceeded:
                return 'blocked'
        result = client.parallel(ask, range(10))
        self.assertEqual((result.count('sent'), result.count('blocked'), client.attempts), (3, 7, 3))

    def test_unchanged_wire_request_reuses_cache_across_code_fingerprints(self):
        client = self.client(lambda *_: response({}))
        client.request('s', 'c', EXTRACTION, 'parser-v1')
        client.request('s', 'c', EXTRACTION, 'parser-v2')
        self.assertEqual(client.attempts, 1)
        client.request('changed prompt', 'c', EXTRACTION, 'parser-v2')
        self.assertEqual(client.attempts, 2)

    def test_uncertain_fact_cannot_justify_regulation_exclusion(self):
        class SmallIndex:
            rows = [{'id': 'r1', 'text': 'The component shall be tested.', 'is_table': False}]
            by_reg = {'TEST': rows}
            def outlines(self):
                return [{'regulation': 'TEST', 'sections': [{'id': 'r1'}]}]
            def descendants(self, ids):
                return ids
        def send(*_):
            return response({'decisions': [{'regulation': 'TEST', 'decision': 'exclude', 'reason': 'Uncertain feature',
                                            'fact_properties': ['electronics'], 'section_ids': []}]})
        product = {'profile': {'facts': [{'property': 'electronics', 'status': 'representative', 'value': 'LED'}]}}
        selected = select(self.client(send), SmallIndex(), product, 'v')
        self.assertEqual(selected['target_ids'], ['r1'])
        self.assertEqual(selected['decisions'][0]['decision'], 'conditional')

    def test_offline_and_money_limits_never_send(self):
        def never(*_):
            self.fail('Unexpected provider call')
        with self.assertRaises(CacheMiss):
            self.client(never, online=False).request('s', 'c', EXTRACTION, 'v')
        with self.assertRaises(BudgetExceeded):
            self.client(never, max_reserved_usd=0).request('s', 'c', EXTRACTION, 'v')

    def test_truncated_packet_splits_and_keeps_completed_children(self):
        calls = []
        def send(payload, _):
            calls.append(payload)
            if len(calls) == 1:
                return response({}, stop='max_tokens')
            data = json.loads(payload['messages'][0]['content'][0]['text'])['packet']
            ref = data['allowed_target_refs'][0]
            row = {**self.row(), 'primary_ref': ref, 'refs': [ref], 'text': 'Duty ' + ref + ' shall be verified.'}
            return response({'requirements': [row], 'exclusion_reason': ''})
        result = safe_extract(self.client(send), self.packet, {}, 'v', images=False)
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(result['valid']), 2)
        self.assertFalse(result['errors'])

    def test_one_repair_preserves_good_row_when_budget_runs_out(self):
        def send(*_):
            return response({'requirements': [self.row(), {**self.row(), 'refs': ['bad']}], 'exclusion_reason': ''})
        result = safe_extract(self.client(send, max_attempts=1), self.packet, {}, 'v', images=False)
        self.assertEqual(len(result['valid']), 1)
        self.assertEqual(len(result['quarantine']), 1)
        self.assertTrue(result['errors'])

    def test_drawings_are_sent_and_visual_assertions_stay_uncertain(self):
        packet = prepare(ROOT / 'cases/dev-rear-lamp', ['spec.md', 'exploded_view.png'])
        blocks = content(ROOT / 'cases/dev-rear-lamp', packet)
        self.assertTrue(any(b['type'] == 'image' and b['source']['data'] for b in blocks))
        image = next(a for a in packet['assets'] if a['kind'] == 'image')
        fact = {'property': 'part_shape', 'status': 'stated', 'value': 'rounded', 'evidence_ids': [image['id']]}
        profile = {'case_id': packet['case_id'], 'packet_id': packet['packet_id'], 'facts': [fact]}
        self.assertFalse(validate_profile(packet, profile)['valid'])
        fact['status'] = 'observed_uncertain'
        self.assertTrue(validate_profile(packet, profile)['valid'])

    def test_connected_cli_with_simulated_provider_passes_official_validator(self):
        def send(payload, _):
            schema = payload['output_config']['format']['schema']
            if schema == PROFILE:
                packet = prepare(ROOT / 'cases/dev-rear-lamp', ['spec.md', 'exploded_view.png'])
                eid = next(e['id'] for e in packet['evidence'] if 'M1' in e['text'])
                return response({'case_id': packet['case_id'], 'packet_id': packet['packet_id'], 'image_annotations': [], 'facts': [
                    {'property': 'vehicle_category', 'status': 'stated', 'value': 'M1', 'unit': None, 'evidence_ids': [eid]}]})
            data = json.loads(payload['messages'][0]['content'][0]['text'])['packet']
            ref = data['allowed_target_refs'][0]
            return response({'requirements': [{**self.row(), 'primary_ref': ref, 'refs': [ref]}], 'exclusion_reason': ''})
        original_init = Client.__init__
        def init(client, *args, **kwargs):
            kwargs['send'] = send
            original_init(client, *args, **kwargs)
            client.cache = self.folder / 'cli-cache'
        with patch.object(Client, '__init__', init):
            code = main(['run', '--case', str(ROOT / 'cases/dev-rear-lamp'), '--inputs', 'spec.md', 'exploded_view.png',
                         '--index', str(ROOT / 'runs/evidence-base'), '--targets', 'UN R10::6.9.1',
                         '--out', str(self.folder / 'connected'), '--online', '--text-only'])
        self.assertEqual(code, 0)
        status = json.loads((self.folder / 'connected/run-status.json').read_text())
        self.assertTrue(status['official_validator_passed'])
        self.assertEqual(status['state'], 'partial')
        self.assertEqual(status['api_attempts'], 2)


if __name__ == '__main__':
    unittest.main()
