"""No-network, answer-key-free regression tests for subsection-level screening."""
from src.packets import EvidenceIndex
from src.pipeline import packets_for_tier
from src.selection import apply_decisions


def fake_index():
    index = EvidenceIndex.__new__(EvidenceIndex)
    def row(label, parent=None, duty=False):
        return {'id': 'node-' + label, 'paragraph': label,
                'regulation': 'REG A', 'parent_id': parent,
                'parent_ids': [parent] if parent else [], 'annex': None,
                'section': None, 'text': ('Device shall comply ' if duty else 'Technical section ') + label,
                'title': 'Section ' + label, 'is_table': False,
                'has_obligation': duty}
    rows = [row('6')]
    rows += [row(f'6.{i}', 'node-6', True) for i in range(1, 21)]
    rows.append(row('6.4.1', 'node-6.4', True))
    index.rows = rows
    index.by_reg = {'REG A': rows}
    index.by_id = {x['id']: x for x in rows}
    index.scopes = {}
    return index


def test_outline_offers_depth_zero_and_one_not_grandchildren():
    ix = fake_index()
    sections = ix.outlines()[0]['sections']
    assert len(sections) == 21
    assert {x['paragraph'] for x in sections} == {'6'} | {f'6.{i}' for i in range(1,21)}
    assert next(x for x in sections if x['paragraph'] == '6')['depth'] == 0
    assert next(x for x in sections if x['paragraph'] == '6.4')['depth'] == 1
    assert '6.4.1' not in {x['paragraph'] for x in sections}


def test_selected_child_retains_its_duties_and_avoids_other_branches():
    ix = fake_index()
    decisions = {'REG A': {'regulation':'REG A','decision':'include',
                           'fact_properties': [],'section_ids':['node-6.4','node-6.7'],
                           'reason':'synthetic product has functions 4 and 7'}}
    product = {'profile': {'facts': []}}
    selected = apply_decisions(ix, product, decisions)
    primary = set(selected['primary_target_ids'])
    assert primary == {'node-6.4','node-6.4.1','node-6.7'}
    assert 'node-6.12' in selected['review_target_ids']
    assert 'node-6.12' not in primary
    parent = apply_decisions(ix, product, {'REG A': {**decisions['REG A'], 'section_ids':['node-6']}})
    assert len(parent['primary_target_ids']) == len(ix.rows)
    assert len(primary) < len(parent['primary_target_ids'])


def test_primary_mode_does_not_extract_review_tier():
    class CounterIndex:
        def __init__(self):
            self.groups=[]
        def chunks(self, ids, expand_descendants=True):
            assert not expand_descendants
            self.groups.append(list(ids))
            return [{'targets':list(ids)}]
    ix = CounterIndex()
    choice = {'target_ids':['p','r'],'primary_target_ids':['p'], 'review_target_ids':['r']}
    primary = packets_for_tier(ix, choice, 'primary')
    assert ix.groups == [['p']]
    assert primary == [{'targets':['p']}]
    assert packets_for_tier(ix, choice, 'all') == [{'targets':['p']},{'targets':['r']}]
    assert ix.groups == [['p'],['p'],['r']]
