"""Standalone subgoal regression tests: no Claude API calls and no development gold."""
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

from src.subgoal import Clause, comparison, evidence_diff, parse_pdf
from tools.subgoal_pipeline import _verify_changes, build_batches, run


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
    batches, _ = build_batches(old, new, old_reqs)
    target = [b for b in batches if any(e.get('new') and e['new']['label'] == '9.2.2'
                                         for e in b['changed_provisions'])][0]
    assert any(r['id'] == 'R-010' for r in target['old_requirements'])
    assert not any(e.get('new') and e['new']['label'] == '9.2.1' for e in target['changed_provisions'])
    assert not any('R175' in b['group'] for b in batches)


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


def test_schema_failure_preserves_proposals_for_offline_review(tmp_path, monkeypatch):
    import fitz
    import tools.subgoal_pipeline as mod
    old, new = tmp_path/'old.pdf', tmp_path/'new.pdf'
    for path, voltage in [(old, '5'), (new, '6')]:
        pdf = fitz.open()
        page = pdf.new_page()
        page.insert_text((72, 80), f'1. Scope\n1.1. A unit shall use {voltage} V.', fontsize=11)
        pdf.save(path)
        pdf.close()
    req = tmp_path/'requirements_old.json'
    req.write_text(json.dumps([{'id':'R-1', 'text':'A unit shall use 5 V.',
                                'source':{'paragraph':'1.1', 'page':1}}]))
    config = tmp_path/'config.json'
    config.write_text('{}')
    too_long = 'A unit shall use 6 V. ' * 100

    class FakeClient:
        def __init__(self, *args, **kwargs): pass
        def request(self, *args, **kwargs):
            return {'changes':[{'change':'modified', 'requirement_id':'R-1',
                                'new_text':too_long, 'reason':'Voltage changed.',
                                'source':{'page':1, 'paragraph':'1.1'}}]}

    monkeypatch.setattr(mod, 'Client', FakeClient)
    out = tmp_path/'out'
    args = SimpleNamespace(old_pdf=str(old), new_pdf=str(new), requirements_old=str(req),
                           out=str(out), files_index=None, online=True, replay=False,
                           config=str(config), team_id='team-07', case_id='sample',
                           max_attempts=3, max_usd=1.0, page_images=False, allow_empty=False)
    assert mod.run(args) == 1
    review = json.loads((out/'needs-review.json').read_text())
    assert review['proposals'][0]['new_text'] == too_long
    assert 'At most 2000 characters' in review['issues'][0]
    assert not (out/'submission_subgoal.json').exists()


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
