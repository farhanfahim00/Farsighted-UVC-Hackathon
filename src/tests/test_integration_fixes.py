"""Regressions for the concrete defects found while integrating Farhan's branch."""
import json
from types import SimpleNamespace

import check_index
import indexer
import structure
from conftest import index_one, LONG, make_pdf, run_indexer, write_index, read_jsonl


def refs(text, table='A6-1'):
    records = [
        {'id': 'p', 'paragraph': '1', 'kind': 'paragraph', 'text': text, 'number': '1'},
        {'id': 't', 'paragraph': '2. Table ' + table, 'kind': 'table', 'text': '', 'number': '2. Table ' + table},
    ]
    paras = [SimpleNamespace(prefix='', annex=None) for _ in records]
    return structure.resolve_refs(0, structure._Lookup(records, paras))


def test_hyphenated_table_id_is_atomic():
    found = refs('Use Table A6-1 of this Regulation.')
    assert len(found) == 1
    assert found[0]['ref'] == 'A6-1' and found[0]['target_id'] == 't'


def test_external_table_does_not_resolve_to_local_same_number():
    found = refs('Use Table 1 of UN Regulation No. 48.', '1')
    assert found[0]['status'] == 'external' and found[0]['target_id'] is None


def test_ranges_are_flagged_instead_of_resolving_only_endpoints():
    for text in ('Use paragraphs 2.1 to 2.5.', 'Use Tables 1 to 3.'):
        found = refs(text, '1')
        assert len(found) == 1 and found[0]['reason'] == 'range_requires_expansion'
        assert found[0]['status'] == 'unresolved'


def test_named_annex_table_does_not_resolve_to_local_same_number():
    found = refs('Use Table 1 of Annex 6.', '1')
    table_ref = next(x for x in found if x['kind'] == 'table')
    assert table_ref['status'] == 'unresolved' and table_ref['target_id'] is None


def test_strict_checker_rejects_wrong_page_and_nonbase_role(tmp_path):
    result = index_one(tmp_path, [['1. Scope', '1.1. This Regulation applies to lamps ' + LONG + '.',
                                  '2. The lamp shall be tested ' + LONG + '.']])['_result']
    build = tmp_path / 'build'
    build.mkdir()
    for filename, key in [('paragraphs', 'records'), ('pages', 'pages'), ('tables', 'tables'), ('definitions', 'definitions')]:
        indexer.write_jsonl(build / (filename + '.jsonl'), result[key])
    expected = tmp_path / 'expected.json'
    gold = {'requirements': [{'source': {'regulation': 'UN R999', 'version': '1.0.0', 'paragraph': '2', 'page': 1}}]}
    expected.write_text(json.dumps(gold))
    args = ['--build', str(build), '--expected', str(expected)]
    assert check_index.main(args) == 0
    gold['requirements'][0]['source']['page'] = 9
    expected.write_text(json.dumps(gold))
    assert check_index.main(args) == 1
    gold['requirements'][0]['source']['page'] = 1
    expected.write_text(json.dumps(gold))
    for row in result['records']:
        row['role'] = 'latest'
    indexer.write_jsonl(build / 'paragraphs.jsonl', result['records'])
    assert check_index.main(args) == 1


def test_base_only_build_excludes_other_roles(tmp_path):
    base = make_pdf(tmp_path / 'regulations/pdf/UN_R999/base/base.pdf', [['1. Scope ' + LONG]])
    latest = make_pdf(tmp_path / 'regulations/pdf/UN_R999/latest/latest.pdf', [['1. Scope ' + LONG]])
    write_index(tmp_path, [{'regulation': 'UN R999', 'versions': [('base', '1.0.0', base), ('latest', '1.0.0', latest)]}])
    assert run_indexer(tmp_path, '--roles', 'base') == 0
    assert {r['role'] for r in read_jsonl(tmp_path / 'build/paragraphs.jsonl')} == {'base'}
