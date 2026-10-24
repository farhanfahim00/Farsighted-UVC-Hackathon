"""Self-contained regressions; do not require regulation PDFs, hidden gold or API credits."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.contracts import EXTRACTION, digest
from src.extraction import safe_extract
from src.guard import validate_rows
from src.llm import Client, IncompleteResponse
from src.packets import EvidenceIndex, split_packet
from src.verification import obligation_checklist
from src import pipeline


def _duty(text, evidence=None):
    return {'id': 'req', 'text': text, 'condition': '',
            'source': {'regulation': 'UN R10', 'version': '7.2.0', 'paragraph': '6.9.1', 'page': 12},
            'evidence': evidence or [{'id': 'row1', 'text': 'Test pulse 1 data'}]}


def test_table_number_not_mistaken_for_pulse_label():
    table = {'id': 'T3b', 'source_line_ids': ['row1'], 'risk_flags': [],
             'cells': [['Test pulse', 'Immunity test level'], ['number', '12 V'],
                       ['1', '-75 V'], ['2a', '+37 V'], ['3b', '+75 V']]}
    result = obligation_checklist([{'valid': [_duty('For test pulse 1, apply -75 V in Table 3b.')]}],
                                  SimpleNamespace(tables={'T3b': table}))
    rows = result[0]['obligations'][0]['tables']
    assert [r['row_key'] for r in rows] == ['1']
    assert result[0]['obligations'][0]['constraint'] == ['-75 V']


def test_missing_classification_in_blank_key_continuation_row_is_flagged():
    table = {'id': 'T3a', 'source_line_ids': ['row1'], 'risk_flags': ['merged_or_missing_cells'],
             'cells': [['Pulse', 'Classification'], ['', 'Related / not related'],
                       ['', 'B'], ['Starting profile', 'C'], ['', 'D']]}
    result = obligation_checklist([{'valid': [_duty('Starting profile shall use class B or C.')]}],
                                  SimpleNamespace(tables={'T3a': table}))
    flags = result[0]['obligations'][0]['review_flags']
    assert 'Possible missing classification branches: D' in flags


def test_table_pseudo_paragraph_cites_governing_context_parent():
    owner = {'id': 'T', 'regulation': 'UN R10', 'version': '7.2.0',
             'paragraph': '6.9.1. Table 3b', 'parent_ids': ['P'], 'is_table': True}
    parent = {'id': 'P', 'regulation': 'UN R10', 'version': '7.2.0',
              'paragraph': '6.9.1', 'parent_ids': [], 'is_table': False}
    packet = {'targets': [owner], 'context_paragraphs': [parent],
              'refs': {'L1': {'id': 'line', 'text': 'The ESA shall ...', 'page': 12,
                              'target_paragraph_ids': ['T']}}, 'allowed_target_refs': ['L1']}
    row = {'text': 'The ESA shall withstand pulse 1.', 'applicability': 'applicable',
           'condition': '', 'reason': 'Source test', 'primary_ref': 'L1', 'refs': ['L1']}
    checked = validate_rows({'requirements': [row], 'exclusion_reason': ''}, packet)
    assert not checked['quarantine']
    assert checked['valid'][0]['source']['paragraph'] == '6.9.1'
    packet['context_paragraphs'] = []
    checked = validate_rows({'requirements': [row], 'exclusion_reason': ''}, packet)
    assert not checked['valid']
    assert 'governing numbered clause' in checked['quarantine'][0]['errors'][0]


def test_split_prefers_whole_target_paragraphs():
    targets = [{'id': 'A'}, {'id': 'B'}]
    refs = {'L1': {'target_paragraph_ids': ['A']}, 'L2': {'target_paragraph_ids': ['A']},
            'L3': {'target_paragraph_ids': ['B']}, 'L4': {'target_paragraph_ids': ['B']}}
    original = {'packet_id': 'parent', 'targets': targets, 'refs': refs,
                'allowed_target_refs': list(refs)}
    children = split_packet(original)
    assert [[t['id'] for t in c['targets']] for c in children] == [['A'], ['B']]
    assert [c['allowed_target_refs'] for c in children] == [['L1', 'L2'], ['L3', 'L4']]
    assert all(c['split_mode'] == 'target_paragraphs' for c in children)
    assert len({c['packet_id'] for c in children}) == 2


def test_descendants_include_child_even_if_index_order_reversed():
    index = object.__new__(EvidenceIndex)
    index.rows = [{'id': 'grandchild', 'parent_ids': ['child']},
                  {'id': 'child', 'parent_ids': ['parent']},
                  {'id': 'parent', 'parent_ids': []}]
    assert index.descendants(['parent']) == ['grandchild', 'child', 'parent']


def test_single_oversized_target_keeps_all_its_lines_in_compact_mode():
    index = object.__new__(EvidenceIndex)
    index.format = 'fixture'
    # One very long unrelated page line triggers the normal packet limit.
    target = {'id': 'P', 'regulation': 'UN RTEST', 'version': '1.0.0',
              'file': 'test.pdf', 'role': 'base', 'paragraph': '1', 'page': 1,
              'page_end': 1, 'title': 'Test', 'text': 'The device shall be tested.',
              'parent_ids': [], 'cross_refs': [], 'source_line_ids': ['target'],
              'footnote_context_ids': [], 'table_ids': [], 'has_table': False,
              'condition_signals': [], 'inherited_condition_source_ids': []}
    index.rows = [target]
    index.by_id = {'P': target}
    index.by_reg = {'UN RTEST': [target]}
    index.tables = {}
    index.definitions = []
    doc = ('UN RTEST', '1.0.0', 'test.pdf')
    source_lines = [
        {'id': 'target', 'text': 'The device shall be tested.', 'bbox': [0, 0, 10, 10]},
        {'id': 'unrelated', 'text': 'x' * 160000, 'bbox': [0, 20, 10, 30]},
    ]
    index.pages = {(*doc, 1): {'lines': source_lines}}
    index.lines = {line['id']: {**line, 'regulation': doc[0], 'version': doc[1],
                                 'file': doc[2], 'page': 1} for line in source_lines}
    packets = index.chunks(['P'])
    assert len(packets) == 1
    packet = packets[0]
    assert not packet['context_over_budget']
    assert packet['context_reduced']
    assert packet['omitted_page_context_lines'] == 1
    assert {v['id'] for v in packet['refs'].values()} == {'target'}
    assert packet['allowed_target_refs']


def _fake_config(**updates):
    return {'concurrency': 2, 'max_attempts': 12, 'max_reserved_usd': 10,
            'input_usd_per_million': 1, 'output_usd_per_million': 1,
            'cache_read_usd_per_million': 1, 'cache_write_usd_per_million': 1,
            'max_output_tokens': 200, 'model': 'fixture-model', 'timeout_seconds': 2,
            **updates}


def _provider_response():
    return {'stop_reason': 'end_turn', 'usage': {'input_tokens': 3, 'output_tokens': 4},
            'content': [{'type': 'text', 'text': json.dumps({'requirements': [], 'exclusion_reason': 'No duty'})}]}


def test_cache_uses_exact_model_payload_not_unrelated_code_fingerprint(tmp_path):
    calls = []
    client = Client(_fake_config(), tmp_path / 'cache', tmp_path / 'usage.jsonl', online=True,
                    send=lambda *_: calls.append(1) or _provider_response())
    client.request('system', [{'type': 'text', 'text': 'same evidence'}], EXTRACTION, 'source-before-edit')
    client.request('system', [{'type': 'text', 'text': 'same evidence'}], EXTRACTION, 'source-after-edit')
    assert len(calls) == 1
    client.request('changed system', [{'type': 'text', 'text': 'same evidence'}], EXTRACTION, 'source-after-edit')
    assert len(calls) == 2


def test_old_cache_migrates_only_for_exact_model_payload(tmp_path):
    cfg = _fake_config(legacy_cache_fingerprints=['old-run'])
    client = Client(cfg, tmp_path / 'cache', tmp_path / 'usage.jsonl', online=False)
    content = [{'type': 'text', 'text': 'identical evidence'}]
    payload = {'model': cfg['model'], 'max_tokens': cfg['max_output_tokens'],
               'system': 'extract', 'messages': [{'role': 'user', 'content': content}],
               'output_config': {'format': {'type': 'json_schema', 'schema': EXTRACTION}}}
    old = client.cache / (digest({'fingerprint': 'old-run', 'payload': payload}) + '.json')
    old.parent.mkdir(parents=True)
    old.write_text(json.dumps(_provider_response()))
    client.request('extract', content, EXTRACTION, 'new-run')
    assert client.attempts == 0
    assert len(list(client.cache.glob('*.json'))) == 2
    with pytest.raises(RuntimeError):
        client.request('extract', [{'type': 'text', 'text': 'changed evidence'}], EXTRACTION, 'new-run')


def test_split_no_duty_response_does_not_turn_into_failure():
    class Simulated:
        count = 0
        def request(self, *args):
            self.count += 1
            if self.count == 1:
                raise IncompleteResponse('max_tokens')
            return {'requirements': [], 'exclusion_reason': 'No applicable obligation here'}
    packet = {'packet_id': 'original', 'targets': [{'id': 'A'}],
              'refs': {'L1': {'target_paragraph_ids': ['A']}, 'L2': {'target_paragraph_ids': ['A']}},
              'allowed_target_refs': ['L1', 'L2']}
    with patch('src.extraction.model_packet', return_value={}):
        result = safe_extract(Simulated(), packet, {}, 'fingerprint', images=False)
    assert not result['errors']
    assert not result['valid']
    assert result.get('exclusion_reason')


def test_completed_checkpoint_replays_only_for_same_model_and_product(tmp_path):
    class Index:
        format, identity = 'fixture', 'index-identity-1'
        rows = [{'id': 'p1', 'regulation': 'UN R148', 'text': 'Shall label', 'is_table': False}]
        tables = {}
        by_id = {'p1': rows[0]}
        by_reg = {'UN R148': rows}
        def outlines(self):
            return [{'regulation': 'UN R148', 'scope_context': [], 'sections': []}]
        def descendants(self, ids):
            return list(ids)
        def chunks(self, ids, **kwargs):
            return [{'packet_id': 'packet-1', 'targets': [{'id': 'p1'}],
                     'unresolved_references': [], 'context_over_budget': False}]
    class FakeClient:
        attempts = 0
        spent = reserved = 0
        def __init__(self, *args, **kwargs):
            pass
        def parallel(self, fn, items):
            return list(map(fn, items))
    cfg = _fake_config(team_id='team-07')
    config_path = tmp_path / 'runtime.json'
    config_path.write_text(json.dumps(cfg))
    outcome = {'packet_id': 'packet-1', 'valid': [{'id': 'R-xyz', 'text': 'Module shall be marked.',
                'source': {'regulation': 'UN R148', 'version': '1.6.0', 'paragraph': '3.3.3.5', 'page': 8}}],
               'quarantine': [], 'duplicates': [], 'errors': []}
    input_packet = {'packet_id': 'product-1', 'case_id': 'dev-rear-lamp', 'evidence': []}
    selection = {'target_ids': ['p1'], 'primary_target_ids': ['p1'], 'review_target_ids': []}
    calls = []
    def extract(*args, **kwargs):
        calls.append(1)
        return outcome
    def call():
        return pipeline.main(['run', '--case', str(tmp_path), '--inputs', 'spec.md',
                              '--config', str(config_path), '--out', str(tmp_path / 'out')])
    with patch.object(pipeline, 'prepare', return_value=input_packet), \
         patch.object(pipeline, 'EvidenceIndex', return_value=Index()), \
         patch.object(pipeline, 'Client', FakeClient), \
         patch.object(pipeline, 'build_profile', return_value={'profile': {'facts': []}}), \
         patch.object(pipeline, 'select', return_value=selection), \
         patch.object(pipeline, 'safe_extract', side_effect=extract), \
         patch.object(pipeline.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='', stderr='')):
        assert call() == 0
        assert call() == 0
        assert len(calls) == 1
        cfg['model'] = 'different-model'
        config_path.write_text(json.dumps(cfg))
        assert call() == 0
        assert len(calls) == 2
