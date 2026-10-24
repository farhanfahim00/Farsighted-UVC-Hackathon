import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from product_contract import prepare, validate_profile, compare_condition

ROOT = Path(__file__).resolve().parents[1]


class ProductContractChecks(unittest.TestCase):
    def setUp(self):
        self.packet = prepare(ROOT / 'cases/dev-rear-lamp', ['spec.md', 'exploded_view.png'])

    def profile(self, fact):
        return {'case_id': self.packet['case_id'], 'packet_id': self.packet['packet_id'], 'facts': [fact]}

    def test_representative_led_cannot_become_confirmed(self):
        source = next(e for e in self.packet['evidence'] if 'LED modules' in e['text'])
        fact = {'property': 'light_source', 'status': 'stated', 'value': 'LED', 'evidence_ids': [source['id']]}
        self.assertFalse(validate_profile(self.packet, self.profile(fact))['valid'])
        fact['status'] = 'representative'
        self.assertTrue(validate_profile(self.packet, self.profile(fact))['valid'])
        self.assertIsNone(compare_condition([fact], {'property': 'light_source', 'op': 'eq', 'value': 'LED'}))

    def test_missing_fact_and_unit_mismatch_are_unknown(self):
        condition = {'property': 'width', 'op': 'eq', 'value': 100, 'unit': 'mm'}
        self.assertIsNone(compare_condition([], condition))
        self.assertIsNone(compare_condition([{'property': 'width', 'status': 'stated', 'value': 100, 'unit': 'cm'}], condition))

    def test_case_and_evidence_mismatch_rejected(self):
        profile = self.profile({'property': 'vehicle_category', 'status': 'stated', 'value': 'M1', 'evidence_ids': ['invented']})
        self.assertFalse(validate_profile(self.packet, profile)['valid'])
        profile['packet_id'] = 'another-case'
        self.assertIn('Profile belongs to a different case or evidence packet', validate_profile(self.packet, profile)['errors'])

    def test_unknown_requires_null(self):
        profile = self.profile({'property': 'replaceable', 'status': 'unknown', 'value': True, 'evidence_ids': []})
        self.assertFalse(validate_profile(self.packet, profile)['valid'])

    def test_alternatives_keep_three_valued_logic(self):
        facts = [{'property': 'active', 'status': 'explicitly_absent', 'value': False}]
        no = {'property': 'active', 'op': 'eq', 'value': True}
        unknown = {'property': 'replaceable', 'op': 'eq', 'value': True}
        self.assertIsNone(compare_condition(facts, {'op': 'or', 'args': [no, unknown]}))
        self.assertFalse(compare_condition(facts, {'op': 'and', 'args': [no, unknown]}))

    def test_gold_and_paths_outside_case_rejected(self):
        for filename in ('expected.json', '../../README.md'):
            with self.assertRaises(ValueError):
                prepare(ROOT / 'cases/dev-rear-lamp', [filename])

    def test_image_is_not_claimed_as_interpreted(self):
        image = next(a for a in self.packet['assets'] if a['kind'] == 'image')
        self.assertEqual(image['interpretation'], 'not_performed')


if __name__ == '__main__':
    unittest.main()
