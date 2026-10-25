import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.recover_subgoal_additions import _near_duplicate, _verify_recovered


def test_near_duplicate_is_conservative_about_numbers():
    assert _near_duplicate('The test shall use 4 km/h.', 'The test shall use 4 km/h.')
    assert not _near_duplicate('The test shall use 4 km/h.', 'The test shall use 5 km/h.')


def test_verify_recovery_accepts_only_new_atomic_additions():
    clause = {'label': '6.2.1', 'page': 3, 'page_end': 3, 'text': 'A. B.', 'scope': 'body'}
    rows = [
        {'change': 'added', 'requirement_id': None, 'new_text': 'The test shall do B.',
         'reason': 'New step B.', 'source': {'page': 3, 'paragraph': '6.2.1'}},
        {'change': 'modified', 'requirement_id': 'R-1', 'new_text': 'Bad',
         'reason': 'Bad', 'source': {'page': 3, 'paragraph': '6.2.1'}},
    ]
    got, issues = _verify_recovered(rows, clause, [])
    assert len(got) == 1 and got[0]['change'] == 'added'
    assert issues


def test_mock_recovery_adds_missing_second_duty(tmp_path, monkeypatch):
    import fitz
    import tools.recover_subgoal_additions as mod

    old = tmp_path / 'old.pdf'
    new = tmp_path / 'new.pdf'
    d = fitz.open(); p = d.new_page(); p.insert_text((72, 80), '1. Scope\n1.1. Existing duty shall apply.', fontsize=11); d.save(old); d.close()
    d = fitz.open(); p = d.new_page(); p.insert_text((72, 80),
        '1. Scope\n1.1. Existing duty shall apply.\n1.2. The test shall do A. The test shall do B.', fontsize=11); d.save(new); d.close()

    reqs = tmp_path / 'requirements_old.json'; reqs.write_text('[]', encoding='utf-8')
    base = tmp_path / 'base.json'; base.write_text(json.dumps({
        'team_id': 'team-07', 'case_id': 'subgoal-dev',
        'changes': [{'change': 'added', 'requirement_id': None, 'new_text': 'The test shall do A.',
                     'reason': 'New A.', 'source': {'page': 1, 'paragraph': '1.2'}}]
    }), encoding='utf-8')
    config = tmp_path / 'config.json'; config.write_text(json.dumps({
        'model': 'fake', 'concurrency': 1, 'timeout_seconds': 10, 'max_attempts': 5,
        'max_output_tokens': 1000, 'max_reserved_usd': 1.0,
        'input_usd_per_million': 1.0, 'output_usd_per_million': 1.0,
        'cache_read_usd_per_million': 0.0, 'cache_write_usd_per_million': 0.0,
    }), encoding='utf-8')

    calls = []
    class FakeClient:
        def __init__(self, *a, **k): pass
        def request(self, system, content, schema, fingerprint):
            calls.append(json.loads(content if isinstance(content, str) else content[0]['text']))
            if calls[-1]['NEW_CLAUSE']['label'] == '1.2':
                return {'changes': [{'change': 'added', 'requirement_id': None,
                    'new_text': 'The test shall do B.', 'reason': 'New B.',
                    'source': {'page': 1, 'paragraph': '1.2'}}]}
            return {'changes': []}
    monkeypatch.setattr(mod, 'Client', FakeClient)

    args = SimpleNamespace(old_pdf=str(old), new_pdf=str(new), requirements_old=str(reqs),
        base_submission=str(base), config=str(config), out=str(tmp_path / 'out'), online=True,
        replay=False, page_images=False, allow_partial=False, max_attempts=10, max_usd=2.0,
        max_output_tokens=1000)
    assert mod.run(args) == 0
    out = json.loads((tmp_path / 'out/submission_subgoal.json').read_text(encoding='utf-8'))
    assert [x['new_text'] for x in out['changes']] == ['The test shall do A.', 'The test shall do B.']
    assert any(c['NEW_CLAUSE']['label'] == '1.2' for c in calls)


def test_actual_dev_new_clause_recovery_targets_cover_dense_paragraphs_if_pdfs_present():
    from src.subgoal import parse_pdf, evidence_diff
    root = Path(__file__).resolve().parents[1]
    old, new = root / 'subgoal/dev/old.pdf', root / 'subgoal/dev/new.pdf'
    if not old.is_file() or not new.is_file():
        pytest.skip('Organizer PDFs are not in source-only repository')
    diff = evidence_diff(parse_pdf(old), parse_pdf(new))
    labels = {e['new']['label'] for e in diff['candidates'] if e.get('kind') == 'new_clause' and e.get('new')}
    # Generic regression against the organizer PDFs only: these clauses are actually present as new clauses.
    assert {'6.6.1.1', '6.6.2.1', '6.6.2.2'} <= labels
