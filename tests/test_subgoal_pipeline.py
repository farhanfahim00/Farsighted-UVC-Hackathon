"""Standalone subgoal regression tests: no Claude API calls and no development gold."""
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

from src.subgoal import Clause, comparison, evidence_diff, parse_pdf
from tools.subgoal_pipeline import _verify_changes, build_comparison_tasks, run


def clause(key, text, page=2):
    return Clause('body:'+key, key, 'body', text, page, page)


def test_changed_clause_and_new_obligation_in_unchanged_old_number():
    old=[clause('5.1.4.1','If system not ready, provide a warning.')]
    new=[clause('5.1.4.1','Intervention not required in these cases.'),
         clause('5.1.4.2','If system not ready, provide a warning.')]
    diff=evidence_diff(old,new)['candidates']
    # Moving an identical obligation is not a substantive modification.
    assert not any(x.get('old') and x['old']['label']=='5.1.4.1' for x in diff)
    assert any(x['kind']=='new_clause' and x['new']['label']=='5.1.4.1' for x in diff)


def test_changed_clause_extracts_new_paragraph_even_under_same_heading():
    old=[clause('6.6','An old instruction.'), clause('6.7','A restriction.')]
    new=[clause('6.6','New test requirement. It shall be tested twice.'),clause('6.7','A restriction.')]
    assert any(x['new'] and x['new']['label']=='6.6' for x in evidence_diff(old,new)['candidates'])


def test_both_alternatives_and_distinct_list_items_survive():
    old=[clause('6.1','Each vehicle shall have limits 12 V or 24 V.')]
    new=[clause('6.1','Each vehicle shall have limits 12 V or 24 V, and a separate marking shall be visible.')]
    rows=[{'change':'modified','requirement_id':'R-A','new_text':'A vehicle shall have limits 12 V or 24 V.',
           'reason':'Value updated','source':{'page':2,'paragraph':'6.1'}},
          {'change':'added','requirement_id':None,'new_text':'A separate marking shall be visible.',
           'reason':'New independent duty','source':{'page':2,'paragraph':'6.1'}}]
    a,b=_verify_changes(rows,[{'id':'R-A'}],new,old)
    assert a==rows and not b


def test_unsupported_source_and_duplicate_old_id_raise_flags():
    old=[clause('6.1','Old')];new=[clause('6.1','New')];reqs=[{'id':'R-A'}]
    good={'change':'modified','requirement_id':'R-A','new_text':'New','reason':'Changed',
          'source':{'page':2,'paragraph':'6.1'}}
    rows, issues = _verify_changes([good, {**good,'new_text':'Other new text'}],reqs,new,old)
    assert len(rows)==2 and any('adjudication' in i for i in issues)
    rows, issues = _verify_changes([{**good,'source':{'page':22,'paragraph':'6.1'}}],reqs,new,old)
    assert not rows and any('unsupported' in i for i in issues)


def test_renumbered_sections_merge_generically():
    old = [clause('8.4', 'The old vehicle system shall limit acceleration when crossing an obstacle.'),
           clause('9.1', 'No further tests are required.')]
    new = [clause('8.4', 'Updated heading for a different test.'),
           clause('9.2.1', 'The old vehicle system shall limit acceleration when crossing an obstacle.'),
           clause('9.2.2', 'A new signal shall be recorded.')]
    old_reqs = [{'id': 'R-010', 'text': 'Limit acceleration.', 'source': {'paragraph': '8.4'}},
                {'id': 'R-011', 'text': 'No further tests.', 'source': {'paragraph': '9.1'}}]
    tasks, diff = build_comparison_tasks(old, new, old_reqs)
    target = [b for b in tasks if b['group'] == 'new:body:9.2.2@p2'][0]
    assert any(r['id'] == 'R-010' for r in target['old_requirements'])
    assert not any(e.get('new') and e['new']['label'] == '9.2.1'
                   for e in target['changed_provisions'])
    assert any(move['old']['label'] == '8.4' and move['new']['label'] == '9.2.1'
               for move in diff['unchanged_moves'])
    assert not any('R175' in b['group'] for b in tasks)


def test_annex_cross_reference_does_not_switch_scope(tmp_path):
    import fitz
    p = tmp_path / 'reg.pdf'
    d = fitz.open(); page = d.new_page()
    page.insert_text((75, 85), '1. Scope\n1.1. See', fontsize=12)
    page.insert_text((75, 125), 'Annex 1 to this Regulation.', fontsize=12)
    page.insert_text((75, 165), '2. General rules\n2.1. A vehicle shall comply.', fontsize=12)
    page.insert_text((75, 205), 'Annex 1 Communication', fontsize=12)
    page.insert_text((75, 245), '3. Approval sample', fontsize=12)
    d.save(p); d.close()
    c = parse_pdf(p)
    assert next(x for x in c if x.label == '2.1').scope == 'body'
    assert next(x for x in c if x.label == '3').scope == 'annex:1'


def test_removed_without_source_is_valid():
    old = [clause('3.1', 'Old duty', page=1)]
    new = [clause('3.1', 'Changed duty', page=2)]
    row = {'change': 'removed', 'requirement_id': 'R-001',
           'new_text': None, 'reason': 'The old duty no longer appears.'}
    got, issues = _verify_changes([row], [{'id': 'R-001'}], new, old)
    assert not issues
    assert got == [row]


def test_actual_pdf_parser_and_manifest_if_present():
    root=Path(__file__).resolve().parents[1]
    src=root/'subgoal/dev'
    if not (src/'old.pdf').is_file() or not (src/'new.pdf').is_file():
        pytest.skip('Organizer PDFs are not included in source-only ZIP')
    prev, curr=parse_pdf(src/'old.pdf'),parse_pdf(src/'new.pdf')
    assert len(prev)>100 and len(curr)>100
    assert any(c.label=='5.1.4.1' for c in curr)
    assert any(c.label=='6.6.2.2' for c in curr)
    assert all(c.page<=c.page_end for c in curr)
    assert not any(c.label=='0.2' for c in curr)


def test_pipeline_dry_run_does_not_emit_submission(tmp_path):
    import fitz
    p1,p2=tmp_path/'old.pdf',tmp_path/'new.pdf'
    for path,text in [(p1,'1. Scope\n1.1. Old requirement shall apply.'),
                      (p2,'1. Scope\n1.1. New requirement shall apply.\n1.2. Another duty shall apply.')]:
        d=fitz.open();pg=d.new_page();pg.insert_text((72,80),text,fontsize=11);d.save(path);d.close()
    o=tmp_path/'old.json';o.write_text(json.dumps([{'id':'R-1','text':'Old requirement shall apply.',
                                                   'source':{'paragraph':'1.1','page':1,'regulation':'R TEST'}}]))
    a=SimpleNamespace(old_pdf=str(p1),new_pdf=str(p2),requirements_old=str(o),out=str(tmp_path/'out'),
                      files_index=None,online=False,replay=False,config='unused',team_id='team-07',case_id='test',
                      max_attempts=5,max_usd=1.0,page_images=False,allow_empty=False)
    assert run(a)==0
    assert (tmp_path/'out/preflight.json').is_file()
    assert not (tmp_path/'out/submission_subgoal.json').exists()


def test_mock_online_export_validates_and_never_reads_gold(tmp_path, monkeypatch):
    import fitz
    import tools.subgoal_pipeline as mod
    old=tmp_path/'old.pdf';new=tmp_path/'new.pdf'
    for path,t in [(old,'1. Scope\n1.1. A unit shall use 5 V.'),
                   (new,'1. Scope\n1.1. A unit shall use 6 V.')]:
        d=fitz.open(); p=d.new_page();p.insert_text((72,80),t,fontsize=11);d.save(path);d.close()
    req=tmp_path/'requirements_old.json';req.write_text(json.dumps([
        {'id':'R-1','text':'A unit shall use 5 V.','source':{'regulation':'TEST','paragraph':'1.1','page':1}}]))
    config=tmp_path/'config.json';config.write_text('{}')
    class FakeClient:
        def __init__(self, *a,**k): pass
        def request(self,*a,**k):
            payload = json.loads(a[1])
            if payload['task_kind'] == 'added':
                return {'changes': []}  # Changed voltage is a modification, not a new duty.
            return {'changes':[{'change':'modified','requirement_id':'R-1',
                       'new_text':'A unit shall use 6 V.','reason':'Changed numeric limit from 5 to 6 V.',
                       'source':{'page':1,'paragraph':'1.1'}}]}
    monkeypatch.setattr(mod,'Client',FakeClient)
    args=SimpleNamespace(old_pdf=str(old),new_pdf=str(new),requirements_old=str(req),out=str(tmp_path/'out'),
                         files_index=None,online=True,replay=False,config=str(config),team_id='team-07',
                         case_id='sample',max_attempts=3,max_usd=1.0,page_images=False,allow_empty=False)
    assert mod.run(args)==0
    data=json.loads((tmp_path/'out/submission_subgoal.json').read_text())
    from tools.validate import validate
    assert validate(data,requirements_old=json.loads(req.read_text()),strict=True)==[]
    assert data['changes'][0]['new_text']=='A unit shall use 6 V.'


def test_wrong_pdf_checksum_is_rejected_before_any_model_request(tmp_path):
    import fitz
    from tools.subgoal_pipeline import run
    old=tmp_path/'old.pdf';new=tmp_path/'new.pdf'
    for p in [old,new]:
        d=fitz.open();d.new_page();d.save(p);d.close()
    req=tmp_path/'old.json';req.write_text('[]')
    manifest=tmp_path/'files.json';manifest.write_text(json.dumps([{'name':'old.pdf','sha256':'0'*64}]))
    args=SimpleNamespace(old_pdf=str(old),new_pdf=str(new),requirements_old=str(req),out=str(tmp_path/'out'),
                         files_index=str(manifest),online=False,replay=False,config='unused',team_id='team-07',
                         case_id='sample',max_attempts=3,max_usd=1.0,page_images=False,allow_empty=False)
    with pytest.raises(ValueError,match='checksum mismatch'):
        run(args)


def test_real_pdfs_visual_table_page_attaches_to_model_payload(tmp_path,monkeypatch):
    import tools.subgoal_pipeline as mod
    root=Path(__file__).resolve().parents[1]
    pp=root/'subgoal/dev'
    if not (pp/'old.pdf').is_file() or not (pp/'new.pdf').is_file():
        pytest.skip('Organizer PDFs not present in source-only repository')
    calls=[]
    class FakeClient:
        def __init__(self,*a,**k):pass
        def request(self,system,content,schema,fingerprint):
            calls.append(content)
            return {'changes':[]}
    monkeypatch.setattr(mod,'Client',FakeClient)
    args=SimpleNamespace(old_pdf=str(pp/'old.pdf'),new_pdf=str(pp/'new.pdf'),
        requirements_old=str(pp/'requirements_old.json'),out=str(tmp_path/'out'),
        files_index=str(pp/'files.json'),online=True,replay=False,
        config=str(root/'config/runtime.json'),team_id='team-07',case_id='subgoal-dev',
        max_attempts=10,max_usd=5.0,page_images=True,allow_empty=False)
    assert mod.run(args)==1  # Zero-change LLM response must not silently qualify as complete
    assert len(calls)>1
    assert any(isinstance(content,list) and any(x.get('type')=='image' for x in content) for content in calls)
    assert not (tmp_path/'out/submission_subgoal.json').exists()


def test_manual_finalizer_validates_removed_and_changed(tmp_path):
    from tools.finalize_subgoal import finalize
    import fitz
    old = tmp_path / 'old.pdf'; new = tmp_path / 'new.pdf'
    for p, voltage in [(old, '4'), (new, '5')]:
        d=fitz.open();page=d.new_page()
        page.insert_text((75, 80), f'1. Scope\n1.1. The voltage shall be {voltage} V.', fontsize=11)
        d.save(p);d.close()
    reqs=[{'id':'R-001','text':'4 V', 'source':{'paragraph':'1.1'}}]
    rows=[{'change':'modified', 'requirement_id':'R-001',
           'new_text':'The voltage shall be 5 V.','reason':'4 V changed to 5 V.',
           'source':{'paragraph':'1.1','page':1}}]
    data, issues=finalize(rows,reqs,old,new,'team-X','subgoal-test')
    assert not issues and len(data['changes'])==1
    rows[0]['source']['page']=99
    data, issues=finalize(rows,reqs,old,new,'team-X','subgoal-test')
    assert issues and 'unsupported PDF' in ' '.join(issues)


def test_new_stage_separates_atomic_additions_and_one_old_requirement():
    from tools.subgoal_pipeline import build_comparison_tasks, _task_response_issues
    old = [clause('4.1', 'The unit shall use 5 V.'),
           clause('4.2', 'A visible marker shall be present.')]
    new = [clause('4.1', 'The unit shall use 6 V.'),
           clause('4.2', 'A visible marker shall be present.'),
           clause('4.3', 'The unit shall signal overload. The unit shall record a fault.')]
    reqs = [{'id': 'R-A', 'source': {'paragraph': '4.1', 'page': 2},
             'text': 'The unit shall use 5 V.'},
            {'id': 'R-B', 'source': {'paragraph': '4.2', 'page': 2},
             'text': 'A visible marker shall be present.'}]
    tasks, _ = build_comparison_tasks(old, new, reqs)
    existing = [t for t in tasks if t['task_kind'] == 'existing']
    added = [t for t in tasks if t['task_kind'] == 'added']
    assert [t['old_requirement']['id'] for t in existing] == ['R-A']
    assert {t['focus_new_clause']['label'] for t in added} == {'4.1', '4.3'}
    focus = next(t for t in added if t['focus_new_clause']['label'] == '4.3')
    assert any(c['label'] == '4.2' for c in focus['old_clauses'])
    two_new_duties = [
        {'change': 'added', 'requirement_id': None, 'new_text': text,
         'reason': 'Introduced requirement.', 'source': {'paragraph': '4.3', 'page': 2}}
        for text in ['The unit shall signal overload.', 'The unit shall record a fault.']]
    assert _task_response_issues(focus, two_new_duties) == []
    assert _task_response_issues(focus, [{**two_new_duties[0], 'change': 'modified'}])


def test_new_stage_shows_prior_duty_under_different_number_without_gold():
    from tools.subgoal_pipeline import build_comparison_tasks
    old = [clause('7.4', 'The verification procedure shall be performed from stationary position.'),
           clause('7.5', 'The test shall include stopping distance.')]
    new = [clause('7.5', 'The test shall include stopping distance.'),
           clause('7.6.1', 'The verification procedure for moving vehicles shall begin at 3 km/h.')]
    reqs = [{'id': 'REQ-Z', 'source': {'paragraph': '7.4', 'page': 2},
             'text': 'The verification procedure shall be performed from stationary position.'}]
    tasks, _ = build_comparison_tasks(old, new, reqs)
    target = next(t for t in tasks if t.get('focus_new_clause', {}).get('label') == '7.6.1')
    assert target['task_kind'] == 'added'
    assert any(c['label'] == '7.4' for c in target['old_clauses'])
    assert any(r['id'] == 'REQ-Z' for r in target['old_requirements'])


def test_real_development_stage2_preflight_is_generic_and_gold_free():
    from tools.subgoal_pipeline import build_comparison_tasks
    root = Path(__file__).resolve().parents[1]
    dev = root / 'subgoal/dev'
    if not (dev / 'old.pdf').exists() or not (dev / 'new.pdf').exists():
        pytest.skip('Organizer PDFs not bundled in source-only repository')
    old = parse_pdf(dev / 'old.pdf')
    new = parse_pdf(dev / 'new.pdf')
    reqs = json.loads((dev / 'requirements_old.json').read_text(encoding='utf-8'))
    tasks, diff = build_comparison_tasks(old, new, reqs)
    assert len([t for t in tasks if t['task_kind'] == 'added']) == len(
        [e for e in diff['candidates'] if e.get('new')])
    assert all(t['old_clauses'] for t in tasks if t['task_kind'] == 'added')
    assert all(len(t['candidate_new_clauses']) > 0 for t in tasks if t['task_kind'] == 'existing')
    # Production is entirely derived from PDFs and old requirements: no gold.
    assert not any('expected' in t for t in tasks)


def test_normalize_lettered_substeps_to_real_pdf_clause_without_losing_atomic_rows():
    from tools.subgoal_pipeline import _normalize_task_sources, _task_response_issues, _verify_changes
    focus = {
        'scope': 'body', 'key': 'body:6.6.2.2', 'label': '6.6.2.2',
        'page': 11, 'page_end': 11,
        'text': 'The procedure shall include (a) A; (b) B; (c) C.'
    }
    task = {'task_kind': 'added', 'group': 'new:body:6.6.2.2@p11',
            'focus_new_clause': focus}
    rows = [
        {'change': 'added', 'requirement_id': None, 'new_text': f'Action {letter} shall occur.',
         'reason': 'New independent step',
         'source': {'paragraph': f'6.6.2.2({letter})', 'page': 11}}
        for letter in 'abcde'
    ]
    normalized = _normalize_task_sources(task, rows)
    assert {r['source']['paragraph'] for r in normalized} == {'6.6.2.2'}
    assert len(normalized) == 5
    assert _task_response_issues(task, normalized) == []
    clean, issues = _verify_changes(normalized, [], [clause('6.6.2.2', focus['text'], page=11)], [])
    assert len(clean) == 5 and issues == []


def test_normalize_parent_page_range_and_whitespace_roman_substep():
    from tools.subgoal_pipeline import _normalize_substep_source
    parent = {'label': '5.1.5', 'page': 5, 'page_end': 6}
    row = {'change': 'added', 'source': {'paragraph': '5.1.5(d)(viii)', 'page': 6}}
    assert _normalize_substep_source(row, [parent])['source']['paragraph'] == '5.1.5'
    other = {'change': 'added', 'source': {'paragraph': '5.1.5 (a)', 'page': 5}}
    assert _normalize_substep_source(other, [parent])['source']['paragraph'] == '5.1.5'


def test_rejects_unrelated_or_mislocated_citations_after_normalization():
    from tools.subgoal_pipeline import _normalize_task_sources, _task_response_issues
    focus = {'label': '6.6.2.1', 'page': 11, 'page_end': 11}
    task = {'task_kind': 'added', 'group': 'new:body:6.6.2.1@p11',
            'focus_new_clause': focus}
    for source in ({'paragraph': '6.6.2.2(a)', 'page': 11},
                   {'paragraph': '6.6.2.1(a)', 'page': 12},
                   {'paragraph': '6.6.2.10(a)', 'page': 11},
                   {'paragraph': '6.6.2.1-neighbor', 'page': 11}):
        rows = [{'change': 'added', 'requirement_id': None,
                 'reason': 'New duty', 'new_text': 'Device shall work.', 'source': source}]
        transformed = _normalize_task_sources(task, rows)
        assert transformed == rows
        assert _task_response_issues(task, transformed)


def test_finalizer_accepts_source_substeps_without_any_llm_requests():
    from tools.subgoal_pipeline import _verify_changes
    row = {'change': 'added', 'requirement_id': None, 'new_text': 'The device shall activate.',
           'reason': 'New step', 'source': {'paragraph': '6.6.2.2(d)', 'page': 11}}
    clean, issues = _verify_changes([row], [], [clause('6.6.2.2', 'New', page=11)], [])
    assert not issues
    assert clean[0]['source'] == {'paragraph': '6.6.2.2', 'page': 11}


def test_modified_substeps_normalize_only_within_supplied_evidence():
    from tools.subgoal_pipeline import _normalize_task_sources, _task_response_issues
    task = {'task_kind': 'existing', 'group': 'existing:R-42',
            'old_requirement': {'id': 'R-42'},
            'candidate_new_clauses': [
                {'label': '7.2', 'page': 3, 'page_end': 4},
                {'label': '7.2.1', 'page': 4, 'page_end': 4}]}
    row = {'change': 'modified', 'requirement_id': 'R-42',
           'new_text': 'The new requirement', 'reason': 'Changed',
           'source': {'paragraph': '7.2.1(c)', 'page': 4}}
    norm = _normalize_task_sources(task, [row])
    assert norm[0]['source']['paragraph'] == '7.2.1'
    assert _task_response_issues(task, norm) == []


def test_full_pipeline_normalizes_model_substep_and_exports_without_retry(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import tools.subgoal_pipeline as mod
    import fitz
    old = tmp_path / 'old.pdf'
    new = tmp_path / 'new.pdf'
    for path, text in ((old, '1. Scope\n1.1. Baseline shall remain.'),
                       (new, '1. Scope\n1.1. Baseline shall remain.\n'
                             '1.2. The testing procedure shall be:\n(a) Activate the indicator.')):
        d = fitz.open()
        pg = d.new_page()
        pg.insert_text((72, 80), text, fontsize=11)
        d.save(path)
        d.close()
    req = tmp_path / 'old_reqs.json'
    req.write_text('[]', encoding='utf-8')
    conf = tmp_path / 'config.json'
    conf.write_text('{}', encoding='utf-8')
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass
        def request(self, system, content, schema, fingerprint):
            task = json.loads(content)
            if task.get('focus_new_clause', {}).get('label') == '1.2':
                return {'changes': [{
                    'change': 'added', 'requirement_id': None,
                    'new_text': 'Activate the indicator.',
                    'reason': 'New independently testable step.',
                    'source': {'paragraph': '1.2(a)', 'page': 1}}]}
            return {'changes': []}
    monkeypatch.setattr(mod, 'Client', FakeClient)
    args = SimpleNamespace(old_pdf=str(old), new_pdf=str(new),
                           requirements_old=str(req), out=str(tmp_path / 'out'),
                           files_index=None, online=True, replay=False,
                           config=str(conf), team_id='team-test', case_id='dev',
                           max_attempts=10, max_usd=1.0,
                           page_images=False, allow_empty=False)
    assert mod.run(args) == 0
    data = json.loads((tmp_path / 'out/submission_subgoal.json').read_text())
    assert len(data['changes']) == 1
    assert data['changes'][0]['source']['paragraph'] == '1.2'
