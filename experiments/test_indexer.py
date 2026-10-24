import hashlib
from pathlib import Path
import sys
import unittest

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from index_regulations import index_pdf, signals


class OfflineIndexerChecks(unittest.TestCase):
    def test_cross_page_child_preserves_parent_conditions_and_source(self):
        doc = pymupdf.open()
        doc.new_page().insert_text((50, 60), '5. Parent duty applies if equipment is fitted.\n5.1. Child duty shall be verified.')
        doc.new_page().insert_text((50, 60), 'Continue the child duty here.\n5.2. Another duty shall be verified unless exempt.')
        data = doc.tobytes()
        doc.close()
        metadata = {'regulation': 'TEST', 'version': '1', 'role': 'base',
                    'file': 'fixture.pdf', 'sha256': hashlib.sha256(data).hexdigest()}
        pages, paragraphs, tables = index_pdf(data, metadata, 'off')
        child = next(p for p in paragraphs if p['clause'] == '5.1')
        parent = next(p for p in paragraphs if p['clause'] == '5')
        self.assertEqual((child['page_start'], child['page_end']), (1, 2))
        self.assertIn(parent['id'], child['inherited_condition_source_ids'])
        self.assertIn('Continue the child duty here.', child['text'])
        self.assertEqual([p['page'] for p in pages], [1, 2])
        self.assertEqual(tables, [])

    def test_patterns_flag_exceptions_without_deciding_applicability(self):
        result = signals('Either A or B shall apply unless the device is exempt.')
        kinds = {r['kind'] for r in result}
        self.assertIn('alternative', kinds)
        self.assertIn('unless', kinds)
        self.assertTrue(all('applies' not in r for r in result))


if __name__ == '__main__':
    unittest.main()
