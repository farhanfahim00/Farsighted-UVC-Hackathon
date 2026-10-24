#!/usr/bin/env python3
"""Build a portable, static evidence viewer from organizer-compatible JSON.

No inference, network calls, web server, template engine, or gold access. The
viewer is presentation software, never an extractor or submission validator.
"""
from __future__ import annotations

import argparse
from collections import Counter
from difflib import SequenceMatcher
import html
import json
import os
from pathlib import Path
import re


def read_json(path: str | None, default):
    if not path:
        return default
    return json.loads(Path(path).read_text(encoding='utf-8'))


def marked_word_diff(old: str, new: str) -> tuple[str, str]:
    """Pre-escaped HTML: deleting and inserting are visually differentiated."""
    a, b = old.split(), new.split()
    before, after = [], []
    for op, a0, a1, b0, b1 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        o = html.escape(' '.join(a[a0:a1]))
        n = html.escape(' '.join(b[b0:b1]))
        if op == 'equal':
            before.append(o); after.append(n)
        else:
            if o:
                before.append('<del>' + o + '</del>')
            if n:
                after.append('<ins>' + n + '</ins>')
    return ' '.join(before), ' '.join(after)


def pdf_mapping(index: list, base_path: str | None = None) -> dict:
    mapping = {}
    for reg in index:
        for version in reg.get('versions', []):
            files = version.get('files', [])
            if not files:
                continue
            file = files[0]
            url = file.get('pdf_url')
            # Prefer explicit locally supplied files if available; otherwise
            # use the organizer-provided PDF URL for the exact version.
            if base_path:
                candidate = Path(base_path) / file.get('name', '')
                if candidate.is_file():
                    url = candidate.resolve().as_uri()
            if url:
                mapping[reg.get('regulation', '') + '||' + version.get('version_label', '')] = url
    return mapping


def flatten_diff(packets: list[dict]) -> list[dict]:
    found = []
    for batch in packets:
        for e in batch.get('changed_provisions', []):
            old, new = e.get('old'), e.get('new')
            before, after = marked_word_diff((old or {}).get('text', ''), (new or {}).get('text', ''))
            found.append({
                'kind': e.get('kind'), 'old': old, 'new': new,
                'group': batch.get('group'), 'old_markup': before,
                'new_markup': after,
            })
    return found


def bundle_json(obj) -> str:
    # JSON is inserted into JavaScript, not into HTML markup: defend against
    # </script> and other metacharacters in untrusted regulatory text.
    return json.dumps(obj, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')


def generate(main: dict, subgoal: dict, old_reqs: list, packets: list,
             index: list, out: Path, new_pdf: str | None = None,
             old_pdf: str | None = None, pdf_dir: str | None = None,
             subgoal_files_index: list | None = None) -> None:
    requirements = main.get('requirements', [])
    changes = subgoal.get('changes', [])
    reference = {r.get('id'): r for r in old_reqs}
    change_display = []
    for c in changes:
        orig = reference.get(c.get('requirement_id'), {})
        before, after = marked_word_diff(orig.get('text', ''), c.get('new_text') or '')
        change_display.append({**c, 'old_text': orig.get('text', ''),
                               'old_markup': before, 'new_markup': after})
    mapping = pdf_mapping(index, pdf_dir)
    for record in subgoal_files_index or []:
        if record.get('name') in ('old.pdf', 'new.pdf') and record.get('pdf_url'):
            mapping['subgoal-' + record['name'].removesuffix('.pdf')] = record['pdf_url']
    for tag, path in [('subgoal-old', old_pdf), ('subgoal-new', new_pdf)]:
        if path and Path(path).is_file():
            # Use a relative URL when files share a drive/root to keep the
            # exported viewer portable within the repo structure.
            mapping[tag] = Path(os.path.relpath(Path(path).resolve(), out.parent.resolve())).as_posix()
    data = {'requirements': requirements, 'changes': change_display,
            'diffs': flatten_diff(packets), 'pdfs': mapping,
            'team': main.get('team_id') or subgoal.get('team_id') or 'Farsighted',
            'case': main.get('case_id') or 'Development preview',
            'has_main': bool(requirements), 'has_subgoal': bool(changes)}

    page = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Farsighted | Regulatory Engineering</title>
<style>
:root{--navy:#0a192e;--blue:#164b74;--teal:#087d85;--pale:#eef4f7;--line:#d7e1e8;--text:#172d3e;--muted:#667887;--white:#fff}
*{box-sizing:border-box}body{margin:0;background:#f4f7f9;color:var(--text);font-family:Inter,Segoe UI,Arial,sans-serif;font-size:14px;line-height:1.5}
header{background:var(--navy);color:white;padding:24px clamp(18px,3vw,52px);display:flex;align-items:center;gap:18px;justify-content:space-between}
.brand{font-size:22px;font-weight:750;letter-spacing:-.5px}.brand span{color:#71d0d2}.tagline{font-size:12px;color:#a9c0d4;margin-top:2px}
.header-meta{color:#c9d7e4;font-size:12px;text-align:right}
.shell{display:grid;grid-template-columns:285px minmax(0,1fr);min-height:calc(100vh - 104px)}
aside{background:white;border-right:1px solid var(--line);padding:24px 18px}
aside h3{font-size:11px;letter-spacing:1.4px;color:var(--muted);text-transform:uppercase;margin:12px 8px}
.nav{display:block;border:0;border-radius:8px;width:100%;background:transparent;text-align:left;padding:12px 14px;color:var(--text);font:inherit;font-weight:650;cursor:pointer}
.nav.active{background:#e9f5f4;color:#006d76;border-left:3px solid #087d85}.nav:hover:not(.active){background:#f3f6f9}
.note{margin:25px 8px 0;padding:14px;border:1px solid var(--line);border-radius:9px;background:#fafcfd;font-size:12px;color:var(--muted)}
main{padding:26px clamp(20px,3vw,48px);min-width:0}.section{display:none}.section.active{display:block}
.topline{display:flex;justify-content:space-between;align-items:center;gap:10px;margin:0 0 17px;flex-wrap:wrap}h1{font-size:26px;letter-spacing:-.7px;margin:0}h2{font-size:18px;margin:4px 0 10px}small,.muted{color:var(--muted)}
.chips{display:flex;gap:9px;flex-wrap:wrap}.chip{border-radius:22px;background:white;border:1px solid var(--line);padding:5px 11px;font-size:12px}
.controls{display:flex;gap:10px;margin-bottom:17px;flex-wrap:wrap}input,select{font:inherit;color:var(--text);border-radius:8px;border:1px solid #c9d5df;background:white;padding:10px 12px;min-width:170px}input{flex:1;min-width:230px}
.view{display:grid;grid-template-columns:minmax(0,1fr) minmax(340px,.88fr);gap:18px;align-items:start}
.list{max-height:calc(100vh - 250px);overflow:auto;padding-right:3px}.item{padding:17px 19px;margin-bottom:9px;background:white;border:1px solid var(--line);border-radius:10px;cursor:pointer}
.item:hover{border-color:#8bb5bf}.item.selected{border:2px solid #087d85;padding:16px 18px;box-shadow:0 3px 12px rgba(8,125,133,.08)}
.item p{margin:7px 0 0;white-space:pre-line}.item-title{font-size:12px;font-weight:700;color:#31536b;display:flex;gap:8px;justify-content:space-between;flex-wrap:wrap}
.pill{border-radius:20px;background:#eef3f8;color:#31536b;padding:2px 8px;font-size:11px;font-weight:700;text-transform:capitalize}
.pill.added{background:#e4f5ee;color:#156f4a}.pill.modified{background:#fff2d5;color:#8b5a05}.pill.removed{background:#fce8e6;color:#a02e27}
.detail{background:white;border:1px solid var(--line);border-radius:12px;padding:20px;position:sticky;top:15px;max-height:calc(100vh - 170px);overflow:auto}
.label{font-size:11px;color:var(--muted);font-weight:750;text-transform:uppercase;letter-spacing:1px;margin:16px 0 6px}
.source{font-size:13px;border-radius:7px;padding:9px 12px;background:#edf4f9;font-weight:600}.btn{display:inline-block;border:0;border-radius:8px;background:var(--teal);color:white;text-decoration:none;padding:9px 13px;margin:11px 0;font-weight:700;cursor:pointer}
.btn:hover{filter:brightness(.92)}iframe{height:360px;width:100%;border:1px solid var(--line);border-radius:7px;background:#f7f9fa}
.diffgrid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.diffbox{white-space:pre-line;border:1px solid var(--line);border-radius:8px;padding:12px;background:#fbfdfe;word-break:break-word;min-height:90px}
ins{background:#c7f1d9;text-decoration:none;padding:1px 3px;border-radius:3px}del{background:#ffded9;text-decoration:line-through;padding:1px 3px;border-radius:3px}
.empty{border:1px dashed #bdcdd7;border-radius:10px;padding:30px;text-align:center;color:var(--muted);background:white}
.footer{color:var(--muted);font-size:12px;margin-top:15px}
@media(max-width:1100px){.shell{grid-template-columns:220px minmax(0,1fr)}.view{grid-template-columns:1fr}.detail{position:static;max-height:none}.list{max-height:450px}}
@media(max-width:670px){header{padding:18px}.shell{display:block}aside{display:flex;gap:6px;padding:8px;overflow:auto}aside h3,aside .note{display:none}.nav{white-space:nowrap}.diffgrid{grid-template-columns:1fr}main{padding:16px}}
</style></head><body>
<header><div><div class="brand">FARSIGHTED <span>/ CERTIVITY</span></div><div class="tagline">Traceable regulatory requirements and version-change intelligence</div></div><div class="header-meta"><div id="team"></div><div id="case"></div></div></header>
<div class="shell"><aside><div style="width:100%"><h3>Explorer</h3>
<button class="nav active" data-tab="reqs">Requirements</button>
<button class="nav" data-tab="changes">Requirement changes</button>
<button class="nav" data-tab="diffs">Regulation paragraph diff</button>
<div class="note">Source links open the exact cited PDF page where available. Paragraph-level highlighting requires separately verified geometry; no unsupported highlights are shown.</div></div></aside>
<main><section id="reqs" class="section active"><div class="topline"><h1>Requirements explorer</h1><div class="chips" id="stats-reqs"></div></div>
<div class="controls"><input id="findreq" placeholder="Search requirements, references, clauses…"><select id="regfilter"><option value="">All regulations</option></select></div>
<div class="view"><div class="list" id="req-list"></div><div class="detail" id="req-detail"><div class="empty">Select a requirement to see its source</div></div></div></section>
<section id="changes" class="section"><div class="topline"><h1>Requirement updates</h1><div class="chips" id="stats-changes"></div></div>
<div class="controls"><input id="findchange" placeholder="Search change rationale, requirement IDs…"><select id="typefilter"><option value="">All change types</option><option value="added">Added</option><option value="modified">Modified</option><option value="removed">Removed</option></select></div>
<div class="view"><div class="list" id="change-list"></div><div class="detail" id="change-detail"><div class="empty">Select a change for before/after comparison</div></div></div></section>
<section id="diffs" class="section"><div class="topline"><h1>Regulation paragraph diff</h1><div class="chips" id="stats-diffs"></div></div>
<div class="controls"><input id="finddiff" placeholder="Search paragraph numbers or changed text…"></div>
<div class="view"><div class="list" id="diff-list"></div><div class="detail" id="diff-detail"><div class="empty">Select a candidate textual change</div></div></div>
<div class="footer">Paragraph diffs are potential changes, not automatically new engineering duties. Only reviewed requirement changes belong in a scored submission.</div></section>
<div class="footer">Farsighted hackathon demo · Organizer-compatible JSON input · Static offline HTML (PDF source links may require connectivity)</div>
</main></div>
<script>const DATA = __DATA__;
const $=(id)=>document.getElementById(id);
const escapeHTML=(s)=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
$('team').textContent=DATA.team;$('case').textContent=DATA.case;
const chip=(txt)=>`<span class="chip">${escapeHTML(txt)}</span>`;
function sourceHref(source,kind='main'){
 if(!source)return '';
 const key=String(source.regulation||'')+'||'+String(source.version||'');
 const base=kind==='new'?DATA.pdfs['subgoal-new']:kind==='old'?DATA.pdfs['subgoal-old']:DATA.pdfs[key];
 return base?base+'#page='+encodeURIComponent(String(source.page||1)):'';
}
function sourceBox(source,kind='main'){
 if(!source)return '<div class="muted">No source specified.</div>';
 const detail=[source.regulation,source.version,source.paragraph?'§ '+source.paragraph:'',source.page?'PDF p. '+source.page:''].filter(Boolean).join(' · ');
 const link=sourceHref(source,kind);
 return `<div class="source">${escapeHTML(detail)}</div>${link?`<a class="btn" href="${escapeHTML(link)}" target="_blank" rel="noopener">Open cited PDF page</a><iframe loading="lazy" title="Source PDF" src="${escapeHTML(link)}"></iframe>`:'<p class="muted">No PDF URL provided for this version.</p>'}`;
}
function selectTab(tab){document.querySelectorAll('.nav').forEach(b=>b.classList.toggle('active',b.dataset.tab===tab));document.querySelectorAll('.section').forEach(s=>s.classList.toggle('active',s.id===tab));}
document.querySelectorAll('.nav').forEach(b=>b.addEventListener('click',()=>selectTab(b.dataset.tab)));
$('stats-reqs').innerHTML=chip(DATA.requirements.length+' requirements')+chip(new Set(DATA.requirements.map(r=>r.source?.regulation).filter(Boolean)).size+' regulations');
const regs=[...new Set(DATA.requirements.map(r=>r.source?.regulation).filter(Boolean))].sort();regs.forEach(r=>{$('regfilter').insertAdjacentHTML('beforeend',`<option value="${escapeHTML(r)}">${escapeHTML(r)}</option>`)});
function renderReq(){const q=$('findreq').value.toLowerCase(),reg=$('regfilter').value;
const reqs=DATA.requirements.filter(r=>(!reg||r.source?.regulation===reg)&&JSON.stringify(r).toLowerCase().includes(q));
$('req-list').innerHTML=reqs.length?reqs.slice(0,2000).map((r,i)=>`<article class="item" data-idx="${i}"><div class="item-title"><span>${escapeHTML(r.id||'Requirement')}</span><span>${escapeHTML(r.source?.regulation||'')} §${escapeHTML(r.source?.paragraph||'')}</span></div><p>${escapeHTML((r.text||'').slice(0,330))}${(r.text||'').length>330?'…':''}</p></article>`).join(''):'<div class="empty">No requirements match the filter. Load a main submission when it is ready.</div>';
$('req-list').querySelectorAll('.item').forEach(el=>el.addEventListener('click',()=>{const r=reqs[Number(el.dataset.idx)];$('req-detail').innerHTML=`<div class="label">Requirement ${escapeHTML(r.id||'')}</div><h2>${escapeHTML(r.text||'')}</h2><div class="label">Cited source</div>${sourceBox(r.source)}</div>`;document.querySelectorAll('#req-list .item').forEach(x=>x.classList.remove('selected'));el.classList.add('selected')}));}
$('findreq').addEventListener('input',renderReq);$('regfilter').addEventListener('change',renderReq);renderReq();
const countChanges=(k)=>DATA.changes.filter(c=>c.change===k).length;
$('stats-changes').innerHTML=chip(DATA.changes.length+' updates')+['added','modified','removed'].map(k=>chip(countChanges(k)+' '+k)).join('');
function renderChanges(){const q=$('findchange').value.toLowerCase(),typ=$('typefilter').value;
const list=DATA.changes.filter(r=>(!typ||r.change===typ)&&JSON.stringify(r).toLowerCase().includes(q));
$('change-list').innerHTML=list.length?list.map((r,i)=>`<article class="item" data-idx="${i}"><div class="item-title"><span><span class="pill ${escapeHTML(r.change)}">${escapeHTML(r.change)}</span> ${escapeHTML(r.requirement_id||'New obligation')}</span><span>${escapeHTML(r.source?.paragraph||'')}</span></div><p>${escapeHTML((r.new_text||r.reason||'').slice(0,300))}</p></article>`).join(''):'<div class="empty">No reviewed change submissions loaded yet. The paragraph-diff tab shows Stage 1 evidence.</div>';
$('change-list').querySelectorAll('.item').forEach(el=>el.addEventListener('click',()=>{const r=list[Number(el.dataset.idx)];const type=r.change==='removed'?'old':'new';$('change-detail').innerHTML=`<span class="pill ${escapeHTML(r.change)}">${escapeHTML(r.change)}</span><h2>${escapeHTML(r.requirement_id||'New requirement')}</h2><div class="label">Change rationale</div><p>${escapeHTML(r.reason||'')}</p><div class="diffgrid"><div><div class="label">Existing requirement</div><div class="diffbox">${r.old_markup||'—'}</div></div><div><div class="label">Proposed requirement</div><div class="diffbox">${r.new_markup||'—'}</div></div></div><div class="label">Cited regulation</div>${sourceBox(r.source,type)}`;document.querySelectorAll('#change-list .item').forEach(x=>x.classList.remove('selected'));el.classList.add('selected')}));}
$('findchange').addEventListener('input',renderChanges);$('typefilter').addEventListener('change',renderChanges);renderChanges();
$('stats-diffs').innerHTML=chip(DATA.diffs.length+' textual candidates')+chip('not scored obligations');
function renderDiff(){const q=$('finddiff').value.toLowerCase();const list=DATA.diffs.filter(d=>JSON.stringify(d).toLowerCase().includes(q));
$('diff-list').innerHTML=list.length?list.map((d,i)=>`<article class="item" data-idx="${i}"><div class="item-title"><span>${escapeHTML(d.kind)}</span><span>${escapeHTML(d.old?.label||'—')} → ${escapeHTML(d.new?.label||'—')}</span></div><p>${escapeHTML((d.new?.text||d.old?.text||'').slice(0,220))}</p></article>`).join(''):'<div class="empty">No paragraph-diff preflight loaded.</div>';
$('diff-list').querySelectorAll('.item').forEach(el=>el.addEventListener('click',()=>{const d=list[Number(el.dataset.idx)];$('diff-detail').innerHTML=`<span class="pill">${escapeHTML(d.kind)}</span><h2>${escapeHTML(d.old?.label||'—')} → ${escapeHTML(d.new?.label||'—')}</h2><div class="diffgrid"><div><div class="label">Previous PDF wording</div><div class="diffbox">${d.old_markup||'—'}</div></div><div><div class="label">New PDF wording</div><div class="diffbox">${d.new_markup||'—'}</div></div></div><div class="label">New-version source</div>${sourceBox(d.new?{page:d.new.page,paragraph:d.new.label}:null,'new')}`;document.querySelectorAll('#diff-list .item').forEach(x=>x.classList.remove('selected'));el.classList.add('selected')}));}
$('finddiff').addEventListener('input',renderDiff);renderDiff();
</script></body></html>'''
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page.replace('__DATA__', bundle_json(data)), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--main', help='Organizer main submission.json (from either team pipeline)')
    parser.add_argument('--subgoal', help='Validated submission_subgoal.json (optional)')
    parser.add_argument('--old-requirements', default='subgoal/dev/requirements_old.json')
    parser.add_argument('--evidence-packets', help='Stage 1 evidence-packets.json for candidate diff tab')
    parser.add_argument('--index', default='regulations/index.json')
    parser.add_argument('--subgoal-files-index', default='subgoal/dev/files.json')
    parser.add_argument('--old-pdf', default='subgoal/dev/old.pdf')
    parser.add_argument('--new-pdf', default='subgoal/dev/new.pdf')
    parser.add_argument('--pdf-dir', help='Directory with locally downloaded regulation PDFs')
    parser.add_argument('--out', default='runs/demo/index.html')
    a = parser.parse_args()
    main_data = read_json(a.main, {})
    subgoal_data = read_json(a.subgoal, {})
    if main_data and not isinstance(main_data.get('requirements'), list):
        parser.error('--main must contain organizer schema key requirements[]')
    if subgoal_data and not isinstance(subgoal_data.get('changes'), list):
        parser.error('--subgoal must contain organizer schema key changes[]')
    generate(main_data, subgoal_data, read_json(a.old_requirements, []),
             read_json(a.evidence_packets, []), read_json(a.index, []),
             Path(a.out), a.new_pdf, a.old_pdf, a.pdf_dir,
             read_json(a.subgoal_files_index, []))
    print(f'Generated {a.out}: {len(main_data.get("requirements", []))} requirements; '
          f'{len(subgoal_data.get("changes", []))} reviewed changes')

if __name__ == '__main__':
    main()
