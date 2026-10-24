"""Whole-case orchestration checks with synthetic model results; no paid requests."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from src import pipeline
from src.contracts import ROOT


class Index:
    format = 'fixture'
    tables = {}
    rows = [{'id': 'p1', 'regulation': 'UN R148', 'text': 'The module shall carry a mark.', 'is_table': False}]
    by_reg = {'UN R148': rows}

    def outlines(self):
        return [{'regulation': 'UN R148'}]

    def descendants(self, ids):
        return set(ids)

    def chunks(self, ids, **kwargs):
        return [{'packet_id': p, 'targets': [{'id': p}], 'unresolved_references': []} for p in ids]


class Client:
    attempts = 0
    spent = reserved = 0

    def __init__(self, *args, **kwargs):
        pass

    def parallel(self, fn, items):
        return list(map(fn, items))

    def request(self, *args):
        return {'decisions': [{'regulation': 'UN R148', 'decision': 'include',
            'section_ids': ['p1'], 'fact_properties': [], 'reason': 'Fixture lamp'}]}


class FullWorkflowTests(unittest.TestCase):
    def test_full_case_reaches_screening_extraction_export_and_checkpoints(self):
        result = {'packet_id': 'p1', 'errors': [], 'quarantine': [], 'duplicates': [],
                  'valid': [{'id': 'R-fixture', 'text': 'The module shall bear the applicant mark.',
                    'source': {'regulation': 'UN R148', 'version': '1.6.0', 'paragraph': '3.3.3.5', 'page': 8}}]}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            with patch.object(pipeline, 'EvidenceIndex', return_value=Index()), \
                 patch.object(pipeline, 'Client', Client), \
                 patch.object(pipeline, 'build_profile', return_value={'profile': {'facts': []}}), \
                 patch.object(pipeline, 'safe_extract', return_value=result):
                code = pipeline.main(['run', '--case', str(ROOT / 'cases/dev-rear-lamp'),
                    '--inputs', 'spec.md', '--out', str(out)])
            self.assertEqual(code, 0)
            status = json.loads((out / 'run-status.json').read_text())
            self.assertEqual(status['state'], 'complete')
            self.assertFalse(status['partial_targets'])
            self.assertEqual(len(list((out / 'screening-checkpoints').glob('*.json'))), 1)
            self.assertEqual(len(list((out / 'extraction-checkpoints').glob('*.json'))), 1)
            coverage = json.loads((out / 'coverage.json').read_text())
            self.assertEqual(coverage['exported_requirements'], 1)
            self.assertEqual(coverage['packet_states']['completed'], 1)
            self.assertEqual(coverage['targets_accounted'], 1)
            self.assertEqual(json.loads((out / 'target-accounting.json').read_text())[0]['target_ids'], ['p1'])
            self.assertIsNone(coverage['semantic_recall'])
