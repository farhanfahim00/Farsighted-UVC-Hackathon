import unittest
from types import SimpleNamespace
from src.packets import EvidenceIndex
from tools.audit_candidates import audit
from src.selection import apply_decisions


class CandidateTests(unittest.TestCase):
    def test_uncertain_exclusion_is_retained_in_review_queue(self):
        rows = [{'id': reg, 'regulation': reg, 'text': 'shall test', 'is_table': False} for reg in ('A', 'B')]
        index = SimpleNamespace(rows=rows, by_reg={r['id']: [r] for r in rows}, descendants=lambda ids: set(ids))
        decisions = {reg: {'decision': 'exclude' if reg == 'B' else 'include',
            'fact_properties': ['unknown'], 'section_ids': [], 'regulation': reg} for reg in ('A', 'B')}
        result = apply_decisions(index, {'profile': {'facts': []}}, decisions)
        self.assertEqual(result['primary_target_ids'], ['A'])
        self.assertEqual(result['review_target_ids'], ['B'])
        self.assertEqual(set(result['target_ids']), {'A', 'B'})

    def test_chunks_keep_source_order_and_split_large_context(self):
        index = object.__new__(EvidenceIndex)
        index.rows = [{'id': str(i), 'regulation': 'R', 'version': '1', 'file': 'f',
                       'text': 'x', 'parent_ids': []} for i in range(6)]
        index.by_id = {r['id']: r for r in index.rows}
        index.packet = lambda ids: {'ids': ids, 'context_over_budget': len(ids) > 2}
        packets = index.chunks(['5', '2', '0', '4', '1', '3'])
        self.assertEqual([rid for p in packets for rid in p['ids']], list(map(str, range(6))))
        self.assertTrue(all(not p['context_over_budget'] for p in packets))

    def test_audit_separates_missing_index_from_selection(self):
        rows = [{'id': str(i), 'regulation': 'R', 'version': '1', 'paragraph': str(i)} for i in range(2)]
        index = SimpleNamespace(rows=rows, descendants=lambda ids: set(ids))
        expected = {'requirements': [{'id': str(i), 'text': 'duty',
                    'source': {'regulation': 'R', 'version': '1', 'paragraph': str(i)}} for i in range(3)]}
        result = audit(index, {'target_ids': ['0']}, expected)
        self.assertEqual(result['requirements_with_selected_source'], 1)
        self.assertEqual(result['gaps'], {'selection_miss': 1, 'index_missing': 1})
