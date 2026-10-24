"""Build an offline evidence index using the reviewed paragraph parser.

Gold is used only by the optional post-build checker. No API or network calls.
Every visible PDF text line is retained, including footnotes omitted by segmentation.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys

import pymupdf
import paragraph_structure as structure
from index_regulations import signals, write_jsonl

ROOT = Path(__file__).resolve().parents[1]


def document_id(metadata):
    identity = {k: metadata[k] for k in ('regulation', 'version', 'role', 'file', 'sha256')}
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]


def extract_document(path, metadata):
    """Preserve raw text independently of heuristic segmentation; bind paragraphs to lines."""
    doc_id = document_id(metadata)
    pages, lines = [], []
    document = pymupdf.open(stream=path, filetype='pdf') if isinstance(path, bytes) else pymupdf.open(path)
    with document as doc:
        for number, page in enumerate(doc, 1):
            raw_lines = []
            for block in page.get_text('dict')['blocks']:
                for raw in block.get('lines', []):
                    line = structure._line_from_spans(raw['spans'], raw['bbox'], number)
                    if line is None:
                        continue
                    line.source_id = f'{doc_id}:p{number}:l{len(raw_lines)+1}'
                    header = bool(structure.HEADER_RE.match(line.text))
                    boilerplate = number == 1 and bool(structure.BOILERPLATE_RE.match(line.text))
                    raw_lines.append({'id': line.source_id, 'text': ''.join(s['text'] for s in raw['spans']),
                                      'normalized_text': line.text, 'bbox': list(raw['bbox']),
                                      'font_size': line.size, 'footnote_candidate': line.footnote,
                                      'running_header': header, 'boilerplate': boilerplate})
                    if not header and not boilerplate:
                        lines.append(line)
            pages.append({**metadata, 'id': f'{doc_id}:p{number}', 'page': number,
                          'text': page.get_text(), 'lines': raw_lines})
    rejected = []
    paras = structure.segment(lines, rejected=rejected)
    occurrences = Counter()
    object_ids = {}
    label_ids = defaultdict(list)
    for p in paras:
        occurrences[p.label] += 1
        pid = f'{doc_id}:{p.label}#{occurrences[p.label]}'
        object_ids[id(p)] = pid
        label_ids[p.label].append(pid)
    rows = []
    assigned = set()
    footnotes_by_page = {p['page']: [l['id'] for l in p['lines'] if l['footnote_candidate']] for p in pages}
    for p in paras:
        source_ids = list(dict.fromkeys(line.source_id for line in p.source_lines))
        assigned.update(source_ids)
        parent_id = None
        risks = []
        if p.is_table:
            parent_id = object_ids.get(id(p.owner))
            risks.append('table_structure_and_scope_require_review')
        else:
            up = p.number.rsplit('.', 1)[0] if '.' in p.number else None
            parent_label = f'{p.prefix} {up}'.strip() if up else p.prefix if p.number else None
            matches = label_ids.get(parent_label, [])
            if len(matches) == 1:
                parent_id = matches[0]
            elif parent_label:
                risks.append('parent_missing_or_ambiguous')
        if len(label_ids[p.label]) > 1:
            risks.append('repeated_paragraph_label')
        if p.has_table and not p.is_table:
            risks.append('possible_table_context')
        rows.append({**metadata, 'id': object_ids[id(p)], 'paragraph': p.label,
                     'page': p.page, 'page_start': p.page, 'page_end': p.page_end,
                     'section': p.section, 'annex': p.annex, 'appendix': p.appendix, 'part': p.part,
                     'text': p.text, 'source_line_ids': source_ids, 'parent_id': parent_id,
                     'condition_signals': signals(p.text), 'cross_refs': structure.cross_refs(p.text),
                     'shall_count': len(structure.SHALL_RE.findall(p.text)),
                     'has_table': p.has_table, 'is_table': p.is_table,
                     'footnote_context_ids': [lid for n in range(p.page, p.page_end+1) for lid in footnotes_by_page[n]],
                     'review_flags': risks, 'requires_page_context': True})
    by_id = {r['id']: r for r in rows}
    for r in rows:
        parents, seen = [], {r['id']}
        parent = r['parent_id']
        while parent and parent not in seen:
            seen.add(parent)
            parents.append(parent)
            parent = by_id[parent]['parent_id']
        if parent:
            r['review_flags'].append('parent_cycle')
        r['parent_ids'] = parents
        r['inherited_condition_source_ids'] = [pid for pid in parents if by_id[pid]['condition_signals']]
    unassigned = 0
    for page in pages:
        page['unassigned_line_ids'] = [l['id'] for l in page['lines']
                                       if l['id'] not in assigned and not l['running_header'] and not l['boilerplate']]
        unassigned += len(page['unassigned_line_ids'])
    stats = {'pages': len(pages), 'paragraphs': len(rows), 'unassigned_visible_lines': unassigned,
             'footnote_candidate_lines': sum(len(v) for v in footnotes_by_page.values()),
             'rejected_number_candidates': len(rejected)}
    return pages, rows, stats


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--only', nargs='+', help='Exact regulation names')
    parser.add_argument('--roles', nargs='+', choices=('base', 'latest'), default=['base'])
    parser.add_argument('--out', type=Path, default=ROOT / 'runs/evidence-base')
    parser.add_argument('--check-dev', action='store_true')
    args = parser.parse_args(argv)
    entries = json.loads((ROOT / 'regulations/index.json').read_text(encoding='utf-8'))
    if args.only:
        unknown = set(args.only) - {e['regulation'] for e in entries}
        if unknown:
            parser.error('Unknown regulations: ' + ', '.join(sorted(unknown)))
        entries = [e for e in entries if e['regulation'] in args.only]
    pages, paragraphs, documents = [], [], []
    for entry in entries:
        for version in entry['versions']:
            if version['role'] not in args.roles:
                continue
            for file in version['files']:
                path = ROOT / 'regulations/pdf' / entry['regulation'].replace(' ', '_') / version['role'] / file['name']
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if digest != file['sha256']:
                    raise ValueError('Checksum mismatch: ' + str(path))
                metadata = {'regulation': entry['regulation'], 'version': version['version_label'],
                            'role': version['role'], 'file': file['name'], 'sha256': digest}
                pp, rr, stats = extract_document(path, metadata)
                pages.extend(pp)
                paragraphs.extend(rr)
                documents.append({**metadata, **stats})
                print(f"{entry['regulation']} {version['role']}: {len(pp)} pages, {len(rr)} paragraphs", flush=True)
    ids = [r['id'] for r in paragraphs]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate paragraph IDs')
    line_ids = {line['id'] for p in pages for line in p['lines']}
    if any(lid not in line_ids for r in paragraphs for lid in r['source_line_ids']):
        raise ValueError('Broken evidence pointer')
    manifest = {'api_calls': 0, 'documents': documents, 'pages': len(pages), 'paragraphs': len(paragraphs),
                'duplicate_ids': 0, 'broken_source_line_ids': 0,
                'footnote_candidate_lines_preserved': sum(d['footnote_candidate_lines'] for d in documents),
                'unassigned_visible_lines_preserved': sum(d['unassigned_visible_lines'] for d in documents),
                'warning': 'All text lines retained; figures are in the original PDFs. Segmentation, conditions, table ownership and references remain heuristic. Footnote context is page-level, not a proven clause link.'}
    if args.check_dev:
        from check_index import evaluate
        gold = json.loads((ROOT / 'cases/dev-rear-lamp/expected.json').read_text(encoding='utf-8'))['requirements']
        manifest['dev_locator_coverage'] = evaluate(paragraphs, gold)
    args.out.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.out / 'pages.jsonl', pages)
    write_jsonl(args.out / 'paragraphs.jsonl', paragraphs)
    (args.out / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in manifest.items() if k != 'documents'}, indent=2))
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    raise SystemExit(main())
