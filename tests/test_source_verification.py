import unittest
from types import SimpleNamespace
from src.verification import obligation_checklist


class SourceVerificationTests(unittest.TestCase):
    def test_merged_table_branch_is_flagged_without_changing_output(self):
        table = {'id': 'T3a', 'source_line_ids': ['L1'], 'risk_flags': ['merged_or_missing_cells'],
                 'cells': [['Test pulse', 'Classification'], ['', 'Related', 'Not related'],
                           ['', 'B', 'D'], ['Starting profile', 'C', '']]}
        index = SimpleNamespace(tables={'T3a': table})
        row = {'id': 'R1', 'text': 'For starting profile the ESA shall meet class B or C.',
               'condition': 'if starting profile applies',
               'source': {'regulation': 'UN R10', 'version': '7.2.0', 'paragraph': '6.9.1', 'page': 12},
               'evidence': [{'id': 'L1', 'text': 'Starting profile B C D'}]}
        checklist = obligation_checklist([{'valid': [row]}], index)
        duty = checklist[0]['obligations'][0]
        self.assertEqual(duty['condition'], row['condition'])
        self.assertEqual(duty['tables'][0]['row_key'], 'Starting profile')
        self.assertIn('Possible missing classification branches: D', duty['review_flags'])
        self.assertEqual(duty['semantic_status'], 'unverified')

    def test_replaceability_condition_is_preserved_per_duty(self):
        index = SimpleNamespace(tables={})
        row = {'id': 'R2', 'text': 'The module shall bear a mark.',
               'condition': 'if replaceable light source modules are used',
               'source': {'regulation': 'UN R148', 'version': '1.6.0', 'paragraph': '3.3.3.5', 'page': 8},
               'evidence': [{'id': 'L2', 'text': 'In case replaceable modules are used, bear markings.'}]}
        duty = obligation_checklist([{'valid': [row]}], index)[0]['obligations'][0]
        self.assertEqual(duty['condition'], row['condition'])
        self.assertEqual(duty['evidence_anchors'], ['L2'])

    def test_component_described_as_vehicle_is_flagged(self):
        index = SimpleNamespace(tables={})
        product = {'profile': {'facts': [{'property': 'product_kind', 'status': 'stated', 'value': 'rear lamp module'}]}}
        row = {'id': 'R3', 'text': 'The rear lamp as a vehicle shall meet the limit.',
               'condition': '', 'source': {'regulation': 'UN R10', 'version': '7.2.0',
                                          'paragraph': '6.10.1', 'page': 12},
               'evidence': [{'id': 'L3', 'text': 'A vehicle or ESA shall meet the limit.'}]}
        duty = obligation_checklist([{'valid': [row]}], index, product)[0]['obligations'][0]
        self.assertIn('Product component described as a whole vehicle; verify regulatory subject', duty['review_flags'])
