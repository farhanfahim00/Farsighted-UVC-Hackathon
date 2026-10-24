"""Real shared-index integration with a simulated provider; no paid/network calls."""
import json
import unittest
from unittest.mock import patch

from src.contracts import ROOT, PROFILE
from src.guard import validate_rows
from src.llm import Client
from src.packets import EvidenceIndex, model_packet
from src.pipeline import main
from tools.product_contract import prepare
from test_image_evidence import scratch


class SharedIndexChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = EvidenceIndex(ROOT / 'runs/shared-index')

    def test_canonical_base_index_has_verified_source_identity(self):
        self.assertEqual(self.index.format, 'farhan-evidence')
        self.assertEqual({r['role'] for r in self.index.rows}, {'base'})
        self.assertTrue(all(len(r['sha256']) == 64 for r in self.index.rows))
        self.assertEqual(len(self.index.by_reg), 56)

    def test_two_duties_from_one_clause_and_table_cites_governing_clause(self):
        clause = self.index.target('UN R10::6.9.1')
        packet = next(p for p in self.index.chunks([clause]) if any(
            t['paragraph'] == '6.9.1. Table 3b' for t in p['targets']))
        alias = next(a for a in packet['allowed_target_refs'] if any(
            p['paragraph'] == '6.9.1. Table 3b' for p in packet['targets']
            if p['id'] in packet['refs'][a]['target_paragraph_ids']))
        rows = [{'text': text, 'applicability': 'conditional', 'condition': 'if ESA tested',
                 'reason': 'Independent test duty', 'primary_ref': alias, 'refs': [alias]}
                for text in ('For pulse 1, the ESA shall meet level A.',
                             'For pulse 2a, the ESA shall meet level B.')]
        checked = validate_rows({'requirements': rows, 'exclusion_reason': ''}, packet)
        self.assertEqual(len(checked['valid']), 2)
        self.assertTrue(all(r['source']['paragraph'] == '6.9.1' for r in checked['valid']))

    def test_table_cells_risks_and_line_aliases_reach_model_packet(self):
        row = next(r for r in self.index.rows if r['table_ids'] and any(self.index.tables[t]['cells'] for t in r['table_ids']))
        packet = self.index.packet([row['id']])
        wire = model_packet(packet)
        self.assertTrue(wire['tables'])
        self.assertTrue(any(t['cells'] for t in wire['tables']))
        self.assertTrue(all(ref in wire['source_lines'] for t in wire['tables'] for ref in t['refs']))
        self.assertTrue(all('risk_flags' in t for t in wire['tables']))

    def test_resolved_reference_adds_evidence_without_becoming_target(self):
        row, ref = next((r, x) for r in self.index.rows for x in r['cross_refs']
                       if x['status'] == 'resolved' and x['target_id'] != r['id']
                       and self.index.by_id[x['target_id']]['source_line_ids'])
        packet = self.index.packet([row['id']])
        target_lines = set(row['source_line_ids'])
        linked_lines = set(self.index.by_id[ref['target_id']]['source_line_ids'])
        self.assertTrue(linked_lines <= {l['id'] for l in packet['refs'].values()})
        self.assertEqual({packet['refs'][a]['id'] for a in packet['allowed_target_refs']}, target_lines)

    def test_requirement_citation_round_trips_through_canonical_line_ids(self):
        row_id = self.index.target('UN R148::3.3.3.5')
        packet = self.index.packet([row_id])
        ref = packet['allowed_target_refs'][0]
        result = validate_rows({'requirements': [{'text': 'The module shall carry the required identification.',
            'applicability': 'conditional', 'condition': 'if a light-source module is fitted', 'reason': 'Test fixture',
            'primary_ref': ref, 'refs': [ref]}], 'exclusion_reason': ''}, packet)
        self.assertFalse(result['quarantine'])
        self.assertEqual(result['valid'][0]['source']['paragraph'], '3.3.3.5')
        self.assertEqual(result['valid'][0]['evidence'][0]['id'], packet['refs'][ref]['id'])

    def test_shared_pipeline_with_updated_drawing_simulated_provider(self):
        case = ROOT / 'cases/dev-rear-lamp'
        packet = prepare(case, ['spec.md', 'exploded_view.updated.png'])
        def send(payload, _):
            if payload['output_config']['format']['schema'] == PROFILE:
                eid = next(e['id'] for e in packet['evidence'] if 'M1' in e['text'])
                value = {'case_id': packet['case_id'], 'packet_id': packet['packet_id'], 'image_annotations': [],
                    'facts': [{'property': 'vehicle_category', 'status': 'stated', 'value': 'M1', 'unit': None, 'evidence_ids': [eid]}]}
            else:
                data = json.loads(payload['messages'][0]['content'][0]['text'])['packet']
                ref = data['allowed_target_refs'][0]
                value = {'requirements': [{'text': 'The module shall carry the required identification.',
                    'applicability': 'conditional', 'condition': 'if a light-source module is fitted',
                    'reason': 'Simulated integration fixture', 'primary_ref': ref, 'refs': [ref]}], 'exclusion_reason': ''}
            return {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': json.dumps(value)}], 'usage': {}}
        original = Client.__init__
        with scratch() as folder:
            def init(client, *args, **kwargs):
                kwargs['send'] = send
                original(client, *args, **kwargs)
                client.cache = folder / 'cache'
            with patch.object(Client, '__init__', init):
                code = main(['run', '--case', str(case), '--inputs', 'spec.md', 'exploded_view.updated.png',
                    '--targets', 'UN R148::3.3.3.5', '--out', str(folder / 'connected'), '--online', '--text-only'])
            status = json.loads((folder / 'connected/run-status.json').read_text())
            self.assertEqual(code, 0)
            self.assertEqual(status['index_format'], 'farhan-evidence')
            self.assertTrue(status['official_validator_passed'])
            self.assertEqual(status['api_attempts'], 2)
            self.assertIsNone(status['semantic_score'])


if __name__ == '__main__':
    unittest.main()
