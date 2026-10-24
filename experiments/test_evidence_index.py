import hashlib
import json
from pathlib import Path
import sys
import unittest

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from check_index import evaluate
from index_evidence import document_id, extract_document


class EvidenceIndexChecks(unittest.TestCase):
    def test_raw_footnote_and_cross_page_parent_context_survive(self):
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((50, 60), '5. The following conditions apply if the equipment is fitted.', fontsize=11)
        page.insert_text((50, 80), '5.1. The device shall comply with this complete testing condition.', fontsize=11)
        page.insert_text((50, 110), '1', fontsize=6)
        page.insert_text((55, 110), 'This exception applies unless the equipment is exempt.', fontsize=8)
        doc.new_page().insert_text((50, 60), 'The test also covers the continuing operating condition.\n5.2. The other device shall be tested under its own condition.', fontsize=11)
        data = doc.tobytes()
        doc.close()
        metadata = {'regulation': 'TEST', 'version': '1', 'role': 'base', 'file': 'test.pdf',
                    'sha256': hashlib.sha256(data).hexdigest()}
        pages, rows, _ = extract_document(data, metadata)
        raw = {l['id']: l for p in pages for l in p['lines']}
        self.assertTrue(any('This exception applies unless' in l['text'] for l in raw.values()))
        child = next(r for r in rows if r['paragraph'] == '5.1')
        parent = next(r for r in rows if r['paragraph'] == '5')
        self.assertEqual((child['page'], child['page_end']), (1, 2))
        self.assertIn(parent['id'], child['inherited_condition_source_ids'])
        self.assertTrue(all(lid in raw for r in rows for lid in r['source_line_ids']))
        self.assertTrue(any('5.1.' in raw[lid]['text'] for lid in child['source_line_ids']))
        # The same PDF must produce byte-identical evidence records.
        second = extract_document(data, metadata)
        self.assertEqual(json.dumps((pages, rows), sort_keys=True), json.dumps(second[:2], sort_keys=True))

    def test_identity_distinguishes_role_and_file(self):
        meta = {'regulation': 'R', 'version': '1', 'role': 'base', 'file': 'a.pdf', 'sha256': 'abc'}
        self.assertNotEqual(document_id(meta), document_id({**meta, 'role': 'subgoal_old'}))
        self.assertNotEqual(document_id(meta), document_id({**meta, 'file': 'b.pdf'}))

    def expected(self, page=5):
        return {'text': 'The component shall satisfy its condition.',
                'source': {'regulation': 'R', 'version': '1', 'paragraph': '3.1', 'page': page}}

    def row(self, **changes):
        return {'id': 'r', 'role': 'base', 'regulation': 'R', 'version': '1',
                'paragraph': '3.1', 'page': 5, 'page_end': 5, **changes}

    def test_near_page_does_not_count_as_exact(self):
        result = evaluate([self.row(page=4, page_end=4)], [self.expected()])
        self.assertEqual(result['all_expected_pages_in_exact_span'], 0)
        self.assertEqual(result['within_one_page_diagnostic_only'], 1)

    def test_wrong_version_does_not_count(self):
        result = evaluate([self.row(version='2')], [self.expected()])
        self.assertEqual(result['labels_found_in_correct_version'], 0)

    def test_checks_all_expected_pages_and_duplicate_ids(self):
        result = evaluate([self.row(), self.row()], [self.expected(), self.expected(6)])
        self.assertEqual(result['distinct_locators'], 1)
        self.assertEqual(result['all_expected_pages_in_exact_span'], 0)
        self.assertEqual(result['duplicate_record_ids'], 1)

    def test_empty_expected_has_no_division_by_zero(self):
        self.assertEqual(evaluate([], [])['distinct_locators'], 0)


if __name__ == '__main__':
    unittest.main()
