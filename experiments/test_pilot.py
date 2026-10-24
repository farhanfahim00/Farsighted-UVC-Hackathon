"""Checks the safeguards that matter before a paid generation or export."""
import copy
import unittest

import pilot


class PilotChecks(unittest.TestCase):
    def setUp(self):
        self.packet = {'baseline_pages': [{'page': 8, 'text': 'In case replaceable modules are used, bear the trade name.'}]}
        self.output = {'requirements': [{'text': 'Replaceable modules shall bear the trade name.',
            'applicability': 'conditional', 'condition': 'If replaceable modules are used',
            'reason': 'The lamp may contain replaceable modules.',
            'evidence': [{'page': 8, 'quote': 'bear the trade name.'}]}]}

    def test_exact_evidence_and_explicit_condition_pass(self):
        self.assertEqual(pilot.check_output(self.output, self.packet, 'baseline'), [])

    def test_wrong_page_or_invented_quote_blocks(self):
        for page, quote in [(9, 'bear the trade name.'), (8, 'bear an invented marking.')]:
            output = copy.deepcopy(self.output)
            output['requirements'][0]['evidence'] = [{'page': page, 'quote': quote}]
            self.assertTrue(pilot.check_output(output, self.packet, 'baseline'))

    def test_missing_condition_blocks(self):
        self.output['requirements'][0]['condition'] = ''
        self.assertTrue(pilot.check_output(self.output, self.packet, 'baseline'))

    def test_truncated_generation_is_not_exported(self):
        with self.assertRaises(ValueError):
            pilot.decode_response({'stop_reason': 'max_tokens', 'content': [{'type': 'text', 'text': '{}'}]})

    def test_export_does_not_drop_conditional_scope(self):
        text = pilot.requirement_text(self.output['requirements'][0])
        self.assertIn('If replaceable modules are used', text)

    def test_reference_ids_bind_source_text_in_code(self):
        packet = {'context_pages': [{'page': 12, 'text': 'B     D\n(for other ESA)'}]}
        output = {'requirements': [{'evidence': [{'ref': 'p12-l0001'}]}]}
        hydrated = pilot.hydrate_evidence(output, packet)
        self.assertEqual(hydrated['requirements'][0]['evidence'][0]['quote'], 'B     D')
        with self.assertRaises(KeyError):
            pilot.hydrate_evidence({'requirements': [{'evidence': [{'ref': 'invented'}]}]}, packet)


if __name__ == '__main__':
    unittest.main()
