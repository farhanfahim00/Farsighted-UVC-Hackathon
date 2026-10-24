"""Read-only, deterministic tests for the new post-extraction review pipeline."""
import copy
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src.refinement import (apply_review, batches, check_decisions, confirmed_rejections,
                            evidence_rows, review_context, review_summary)
from src.selection import apply_decisions
from src.packets import EvidenceIndex
from tools.audit_target_coverage import audit
from tools.refine_submission import execute


def req(rid, reg='UN R148', text='The lamp shall bear a marking.', para='4.1'):
    return {'id': rid, 'text': text,
            'source': {'regulation': reg, 'version': '1.0', 'paragraph': para, 'page': 2}}


def original_row(row, evidence=True):
    return {'id': row['id'], 'text': row['text'], 'source': row['source'],
            'applicability': 'applicable', 'condition': '', 'reason': 'product contains lamp',
            'review_flags': [], 'evidence': ([{'id': 'line1', 'text': row['text'], 'page': 2}] if evidence else [])}


def decision(rid, verdict='reject', confidence='high', issue='wrong_device_scope'):
    return {'id': rid, 'verdict': verdict, 'confidence': confidence, 'issue': issue,
            'category': 'unrelated_device', 'reason': 'The cited rule governs whole-vehicle body dimensions'}


class RefinementChecks(unittest.TestCase):
    def test_source_evidence_does_not_change_original_submission(self):
        sub = {'team_id': 'team-07', 'case_id': 'dev', 'requirements': [req('a'), req('b')]}
        original = copy.deepcopy(sub)
        ledger = [{'valid': [original_row(r) for r in sub['requirements']]}]
        data, facts = review_context(sub, ledger, {'profile': {'facts': [{'property':'product_kind', 'value':'rear lamp', 'status':'stated'}]}},
                                     {'decisions': [{'regulation': 'UN R148', 'reason': 'signaling', 'decision': 'include'}]},
                                     [{'regulation': 'UN R148', 'scope_context': [{'text': 'Light signaling devices'}]}])
        self.assertEqual(sub, original)
        self.assertEqual(len(data), 2)
        self.assertTrue(data[0]['evidence_complete'])
        self.assertIn('signaling', data[0]['regulation_screening_reason'])
        self.assertEqual(facts[0]['value'], 'rear lamp')

    def test_incomplete_evidence_is_never_a_removal(self):
        item = {'id': 'a', 'evidence_complete': False}
        first = {'a': decision('a')}
        second = {'a': decision('a')}
        self.assertFalse(confirmed_rejections([item], first, second))

    def test_only_two_high_confidence_agreeing_supported_rejections_remove(self):
        rows = [{'id': 'a', 'evidence_complete': True}, {'id':'b','evidence_complete': True},
                {'id':'c','evidence_complete': True}]
        first = {x: decision(x) for x in 'abc'}
        second = {'a': decision('a'), 'b': decision('b', confidence='medium'),
                  'c': decision('c', issue='unsupported_by_source')}
        self.assertEqual(set(confirmed_rejections(rows, first, second)), {'a'})
        self.assertEqual(review_summary(rows, first, second, {'a':{}})['confirmed_rejections'], 1)

    def test_source_only_rejection_is_opt_in_aggressive_variant(self):
        row = {'id':'a','evidence_complete':True}
        first = second = {'a':decision('a', issue='unsupported_by_source')}
        self.assertFalse(confirmed_rejections([row], first, second))
        self.assertEqual(set(confirmed_rejections([row], first, second, allow_unsupported=True)), {'a'})

    def test_numeric_table_issue_never_auto_removes(self):
        row = {'id': 'a', 'evidence_complete': True}
        d = decision('a', issue='numeric_or_table_error')
        self.assertEqual(confirmed_rejections([row], {'a':d}, {'a':d}), {})

    def test_reviewer_rejects_missing_or_duplicate_ids(self):
        rows = [{'id':'a'}, {'id':'b'}]
        with self.assertRaises(ValueError):
            check_decisions({'decisions': [decision('a')]}, rows)
        with self.assertRaises(ValueError):
            check_decisions({'decisions': [decision('a'),decision('a')]}, rows)

    def test_opt_in_consolidation_not_a_gold_based_allowlist(self):
        sub = {'team_id': 'team-07', 'case_id': 'case', 'requirements': [
            req('r7', 'UN R7'), req('r148', 'UN R148'), req('r3', 'UN R3'),
            req('r150', 'UN R150'), req('r128', 'UN R128'), req('r10', 'UN R10')]}
        untouched = copy.deepcopy(sub)
        conservative, stats = apply_review(sub, {})
        self.assertEqual(conservative, sub)
        self.assertEqual(stats['kept_rows'], 6)
        modern, info = apply_review(sub, {}, policy='prefer_consolidated')
        self.assertEqual({x['id'] for x in modern['requirements']}, {'r148', 'r150', 'r128', 'r10'})
        self.assertEqual(info['regime_filtered_rows'], 2)
        self.assertEqual(sub, untouched)
        only_old, _ = apply_review({'team_id':'team-07','case_id':'case','requirements':[req('r7','UN R7')]},
                                   {}, policy='prefer_consolidated')
        self.assertEqual(len(only_old['requirements']), 1)

    def test_review_groups_requirements_by_regulation_without_loss(self):
        rows = [{'id': str(i), 'source': {'regulation': 'A' if i%2 else 'B', 'paragraph':'5.1'}} for i in range(15)]
        groups = batches(rows, batch_size=3)
        self.assertEqual(len(groups), 6)
        self.assertEqual({r['id'] for g in groups for r in g}, {str(i) for i in range(15)})
        self.assertTrue(all(len({r['source']['regulation'] for r in g}) == 1 for g in groups))

    def test_failed_or_missing_review_never_auto_removes(self):
        self.assertFalse(confirmed_rejections([{'id':'a','evidence_complete':True}], {'a':decision('a')}, {}))

    def test_tiered_selection_preserves_total_recall_and_separates_review(self):
        class Index:
            rows = [dict(id='root', text='selected section', is_table=False, parent_ids=[]),
                    dict(id='child', text='content', is_table=False, parent_ids=['root']),
                    dict(id='safety', text='shall happen', is_table=False, parent_ids=[]),
                    dict(id='other', text='not necessary', is_table=False, parent_ids=[])]
            by_reg = {'UN TEST': rows}
            def descendants(self, roots):
                result = set(roots)
                for row in self.rows:
                    if any(pid in result for pid in row['parent_ids']): result.add(row['id'])
                return list(result)
        decision_set = {'UN TEST': {'regulation':'UN TEST', 'decision':'include', 'fact_properties':[],
                                    'section_ids':['root'], 'reason':'test'}}
        sel = apply_decisions(Index(), {'profile': {'facts':[]}}, decision_set)
        self.assertEqual(set(sel['primary_target_ids']), {'root','child'})
        self.assertEqual(set(sel['review_target_ids']), {'safety'})
        self.assertEqual(set(sel['target_ids']), {'root','child','safety'})
        self.assertFalse(set(sel['primary_target_ids']) & set(sel['review_target_ids']))

    def test_tiered_chunking_does_not_reexpand_review_parent_into_primary_child(self):
        index = EvidenceIndex.__new__(EvidenceIndex)
        index.rows = [dict(id='parent', text='shall', regulation='T', version='v', file='test.pdf', parent_ids=[]),
                      dict(id='child', text='child', regulation='T', version='v', file='test.pdf', parent_ids=['parent'])]
        index.by_id = {r['id']:r for r in index.rows}
        index.packet = lambda ids: {'context_over_budget': False, 'ids': list(ids)}
        # Without the explicit flag, a parent in the review queue brings its
        # selected primary child along for a second paid extraction.
        review = index.chunks(['parent'], expand_descendants=False)
        self.assertEqual(review[0]['ids'], ['parent'])
        self.assertEqual(index.chunks(['parent'])[0]['ids'], ['parent', 'child'])

    def test_no_gold_required_even_when_present_malformed(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            run = root/'run'; run.mkdir()
            out = root/'out'
            rows = [req('a')]
            data = {'submission.json': {'team_id':'team-07','case_id':'dev','requirements':rows},
                    'ledger.json': [{'valid':[original_row(rows[0])]}],
                    'selection.json': {'decisions':[]},
                    'scope-input.json': [], 'profile.json': {'profile': {'facts':[]}}}
            for name, value in data.items(): (run/name).write_text(json.dumps(value))
            (run/'expected.json').write_text('bad gold JSON; MUST NEVER BE READ')
            args = type('Args', (), {'run':run, 'out':out, 'max_evidence_chars':6500,
                                    'dry_run':True, 'batch_size':6})()
            self.assertEqual(execute(args), 0)
            self.assertEqual(json.loads((out/'preflight.json').read_text())['review_status']['requirements_input'],1)

    def test_target_coverage_accounting_flags_high_risk_without_gold(self):
        packet = {'packet_id':'p1', 'targets': [
            {'id':'t1', 'regulation':'UN R10','version':'7','paragraph':'5.1','has_obligation':True,'text':'shall', 'page':4},
            {'id':'t2', 'regulation':'UN R10','version':'7','paragraph':'5.2','has_obligation':True,'text':'shall', 'page':4}]}
        result = {'packet_id':'p1','valid':[{'source':{'regulation':'UN R10','version':'7','paragraph':'5.1'}}]}
        stats = audit([packet],[result],[{'packet_id':'p1','state':'completed'}])
        self.assertEqual(len(stats['candidates']), 1)
        self.assertEqual(stats['candidates'][0]['paragraph'], '5.2')
        self.assertEqual(stats['candidates'][0]['triage_priority'], 'high')

    def test_end_to_end_two_pass_review_produces_validated_variants_without_reextracting(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            run, out = root/'run', root/'review'
            run.mkdir()
            rows = [req('a', 'UN R148', 'The lamp shall bear its mark.'),
                    req('b', 'UN R26', 'The entire vehicle body shall have external projections.')]
            data = {'submission.json': {'team_id':'team-07','case_id':'dev','requirements':rows},
                    'ledger.json': [{'valid':[original_row(r) for r in rows]}],
                    'selection.json': {'decisions':[]}, 'scope-input.json': [],
                    'profile.json': {'profile': {'facts':[{'property':'product_kind', 'status':'stated', 'value':'rear lamp'}]}}}
            for name, value in data.items(): (run/name).write_text(json.dumps(value))
            config = root/'runtime.json'
            config.write_text(json.dumps({'model':'fake','max_attempts':10,'max_reserved_usd':10}))
            class FakeClient:
                def __init__(self, *_args, **_kwargs):
                    self.attempts, self.spent = 0, 0.0
                def parallel(self, f, items):
                    return [f(item) for item in items]
                def request(self, system, content, schema, fingerprint):
                    self.attempts += 1
                    input_rows = json.loads(content[0]['text'])['items']
                    return {'decisions':[decision(x['id']) if x['id'] == 'b' else
                            decision(x['id'], verdict='keep', confidence='high', issue='none')
                            for x in input_rows]}
            args = type('Args', (), {'run':run, 'out':out, 'max_evidence_chars':6500,
                    'dry_run':False, 'batch_size':6, 'config':config, 'online':True,
                    'max_attempts':10, 'max_usd':10, 'model':None})()
            with patch('tools.refine_submission.Client', FakeClient), \
                 patch('tools.refine_submission.validate_submission', return_value='ok'):
                self.assertEqual(execute(args), 0)
            reviewed = json.loads((out/'submission-reviewed-conservative.json').read_text())
            self.assertEqual([r['id'] for r in reviewed['requirements']], ['a'])
            self.assertEqual(json.loads((run/'submission.json').read_text()), data['submission.json'])
            self.assertEqual(json.loads((out/'review-status.json').read_text())['state'], 'review_complete')

    def test_incomplete_review_produces_no_submission(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            run, out = root/'run', root/'review'
            run.mkdir()
            row = req('a')
            data = {'submission.json': {'team_id':'team-07','case_id':'dev','requirements':[row]},
                    'ledger.json': [{'valid':[original_row(row)]}],
                    'selection.json': {'decisions':[]}, 'scope-input.json': [],
                    'profile.json': {'profile': {'facts':[]}}}
            for name,value in data.items(): (run/name).write_text(json.dumps(value))
            config = root/'runtime.json'; config.write_text(json.dumps({'model':'fake','max_attempts':1,'max_reserved_usd':1}))
            class FailingClient:
                def __init__(self, *_args, **_kwargs):
                    self.attempts, self.spent = 1, 0
                def parallel(self, f, items): return [f(i) for i in items]
                def request(self, *_args): raise RuntimeError('no access')
            args = type('Args', (), {'run':run, 'out':out, 'max_evidence_chars':6500,
                    'dry_run':False, 'batch_size':6, 'config':config, 'online':True,
                    'max_attempts':1, 'max_usd':1, 'model':None})()
            with patch('tools.refine_submission.Client', FailingClient):
                self.assertEqual(execute(args), 2)
            self.assertFalse(list(out.glob('submission*.json')))
            self.assertEqual(json.loads((out/'review-status.json').read_text())['state'], 'partial_no_submission_written')


if __name__ == '__main__':
    unittest.main()
