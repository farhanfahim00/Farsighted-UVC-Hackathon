import unittest
from unittest.mock import patch
from src.extraction import extract


class BudgetPreflightTests(unittest.TestCase):
    def test_exhausted_attempts_skip_pdf_rendering(self):
        class Exhausted:
            def attempt_limit_reached(self):
                return True
        with patch('src.extraction.page_images', side_effect=AssertionError('PDF rendered')):
            result = extract(Exhausted(), {'packet_id': 'p', 'context_over_budget': False}, {}, 'fingerprint')
        self.assertEqual(result['valid'], [])
        self.assertTrue(result['errors'][0].startswith('BudgetExceeded:'))
