"""Offline source index. No model calls. Structural/condition signals are heuristics."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
CONDITIONS = {
    'if': r'\bif\b', 'when': r'\bwhen\b', 'where': r'\bwhere\b',
    'in_case': r'\bin\s+(?:the\s+)?case\b', 'unless': r'\bunless\b',
    'exception': r'\bexcept(?:ion)?\b', 'provided': r'\bprovided\s+(?:that|only)\b',
    'only': r'\bonly\b', 'alternative': r'\b(?:either|or)\b',
    'conditional_equipment': r'\b(?:if\s+fitted|where\s+fitted|if\s+equipped)\b',
}
CLAUSE = re.compile(r'^(\d+(?:\.\d+)*)(\.)?\s+(\S.*)$')
ANNEX = re.compile(r'^Annex\s+(\d+[A-Za-z]?)(?:\s*[-:.]?\s+.*)?$', re.I)
PART = re.compile(r'^Part\s+([IVX]+|\d+)(?:\s*[-:.]?\s+.*)?$', re.I)
ROMAN = re.compile(r'^([IVX]+)\.\s+(\S.*)$')
TABLE = re.compile(r'^Table\s*\d+[A-Za-z]?\b', re.I)


def signals(text):
    out = []
    for name, pattern in CONDITIONS.items():
        for match in re.finditer(pattern, text, re.I):
            out.append({'kind': name, 'start': match.start(), 'end': match.end(),
                        'context': text[max(0, match.start()-90):min(len(text), match.end()+180)]})
    return out


def lines_on_page(page):
    rows = []
    for block in page.get_text('dict', sort=True)['blocks']:
        for line in block.get('lines', []):
            spans = line.get('spans', [])
            text = ''.join(s['text'] for s in spans).strip()
            if not text or re.search(r'\bversion\s+[\d.]+\s*\|\s*page\s+\d+', text, re.I):
                continue
            if line['bbox'][1] > page.rect.height-30:
                continue
            rows.append({'text': text, 'bbox': list(line['bbox']),
                         'bold': any('bold' in s.get('font', '').lower() for s in spans)})
    return rows


def index_pdf(path, metadata, table_mode):
    pages, paragraphs, tables = [], [], []
    namespace = {'part': None, 'annex': None, 'annex_part': None}
    current = None
    doc_id = metadata['sha256'][:16]

    def flush():
        nonlocal current
        if current is None:
            return
        text = '\n'.join(s['text'] for s in current['segments'])
        current['text'] = text
        current['condition_signals'] = signals(text)
        current['modalities'] = sorted(set(re.findall(r'\b(?:shall\s+not|shall|must\s+not|must|may|need\s+not)\b', text, re.I)))
        current['reference_signals'] = re.findall(r'\b(?:Annex\s+\d+[A-Za-z]?|paragraphs?\s+[\d.]+|Tables?\s+\d+[A-Za-z]?)', text, re.I)
        current['page_end'] = current['segments'][-1]['page']
        paragraphs.append(current)
        current = None

    document = pymupdf.open(stream=path, filetype='pdf') if isinstance(path, bytes) else pymupdf.open(path)
    with document as doc:
        for number, page in enumerate(doc, 1):
            rows = lines_on_page(page)
            text = '\n'.join(r['text'] for r in rows)
            captions = [r['text'] for r in rows if TABLE.match(r['text'])]
            pages.append({**metadata, 'id': f'{doc_id}:p{number}', 'page': number, 'text': text,
                          'lines': [{'id': f'{doc_id}:p{number}:l{i}', **r} for i, r in enumerate(rows, 1)],
                          'table_captions': captions, 'table_review_required': bool(captions)})
            for line_number, row in enumerate(rows, 1):
                text_line = row['text']
                annex, part, roman = ANNEX.match(text_line), PART.match(text_line), ROMAN.match(text_line)
                heading = row['bold'] or (annex and text_line.lower().strip() == 'annex ' + annex[1].lower())
                if annex and heading:
                    flush()
                    namespace.update(annex=annex[1], annex_part=None)
                elif part and row['bold']:
                    flush()
                    namespace['annex_part' if namespace['annex'] else 'part'] = part[1]
                elif roman and row['bold'] and not namespace['annex']:
                    flush()
                    namespace['part'] = roman[1]
                match = CLAUSE.match(text_line)
                # A decimal value followed by a unit is not a numbered clause.
                valid_clause = bool(match and (match[2] or '.' in match[1]) and len(match[3]) > 2
                                    and not re.match(r'^(?:mm|cm|kg|km|MHz|GHz|V/m|per\s+cent)\b', match[3], re.I))
                if valid_clause:
                    flush()
                    label = match[1]
                    prefix = []
                    if namespace['part']:
                        prefix.append('Part ' + namespace['part'])
                    if namespace['annex']:
                        prefix.append('Annex ' + namespace['annex'])
                    if namespace['annex_part']:
                        prefix.append('Part ' + namespace['annex_part'])
                    current = {**metadata, 'id': f'{doc_id}:p{number}:l{line_number}',
                        'clause': label, 'paragraph': ' '.join([*prefix, label]), 'namespace': dict(namespace),
                        'page_start': number, 'segments': [], 'parent_ids': [], 'parser_review_required': False}
                elif current is None:
                    current = {**metadata, 'id': f'{doc_id}:p{number}:l{line_number}', 'clause': None,
                        'paragraph': None, 'namespace': dict(namespace), 'page_start': number,
                        'segments': [], 'parent_ids': [], 'parser_review_required': True}
                current['segments'].append({'page': number, 'line_id': f'{doc_id}:p{number}:l{line_number}', 'text': text_line})
            if table_mode == 'captions' and captions:
                for i, table in enumerate(page.find_tables().tables, 1):
                    cells = table.extract()
                    risks = []
                    if any(value is None for row in cells for value in row):
                        risks.append('merged_or_missing_cells')
                    if any(re.search(r'\d\s*\n\s*[-−]\s*$', value or '') for row in cells for value in row):
                        risks.append('detached_negative_sign')
                    tables.append({**metadata, 'id': f'{doc_id}:p{number}:table{i}', 'page': number,
                        'bbox': list(table.bbox), 'cells': cells, 'risk_flags': risks,
                        'semantic_review_required': True})
        flush()
    lookup = {}
    by_id = {record['id']: record for record in paragraphs}
    for record in paragraphs:
        key = (json.dumps(record['namespace'], sort_keys=True), record['clause'])
        if record['clause']:
            if key in lookup:
                record['parser_review_required'] = True
            lookup.setdefault(key, record)
    for record in paragraphs:
        if not record['clause']:
            continue
        parts = record['clause'].split('.')
        for length in range(1, len(parts)):
            parent = lookup.get((json.dumps(record['namespace'], sort_keys=True), '.'.join(parts[:length])))
            if parent:
                record['parent_ids'].append(parent['id'])
        record['inherited_condition_source_ids'] = [p for p in record['parent_ids'] if by_id[p]['condition_signals']]
    return pages, paragraphs, tables


def write_jsonl(path, rows):
    with path.open('w', encoding='utf-8') as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--only', nargs='+', help='Exact regulation names from the index.')
    parser.add_argument('--out', type=Path, default=ROOT / 'runs/index-base')
    parser.add_argument('--tables', choices=('off', 'captions'), default='off')
    parser.add_argument('--check-dev', action='store_true', help='Evaluate anchor coverage after indexing; gold never controls indexing.')
    args = parser.parse_args()
    index = json.loads((ROOT / 'regulations/index.json').read_text(encoding='utf-8'))
    if args.only:
        unknown = set(args.only) - {e['regulation'] for e in index}
        if unknown:
            parser.error('Unknown regulation IDs: ' + ', '.join(sorted(unknown)))
        index = [e for e in index if e['regulation'] in args.only]
    args.out.mkdir(parents=True, exist_ok=True)
    all_pages, all_paragraphs, all_tables, documents = [], [], [], []
    for entry in index:
        version = next(v for v in entry['versions'] if v['role'] == 'base')
        for file in version['files']:
            path = ROOT / 'regulations/pdf' / '_'.join(entry['regulation'].split()) / 'base' / file['name']
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != file['sha256']:
                raise ValueError(f'Checksum mismatch for {path}')
            metadata = {'regulation': entry['regulation'], 'version': version['version_label'], 'role': 'base',
                        'file': file['name'], 'sha256': digest}
            pages, paragraphs, tables = index_pdf(path, metadata, args.tables)
            all_pages.extend(pages)
            all_paragraphs.extend(paragraphs)
            all_tables.extend(tables)
            documents.append({**metadata, 'pages': len(pages), 'paragraphs': len(paragraphs)})
            print(f"{entry['regulation']}: {len(pages)} pages, {len(paragraphs)} structural blocks", flush=True)
    write_jsonl(args.out / 'pages.jsonl', all_pages)
    write_jsonl(args.out / 'paragraphs.jsonl', all_paragraphs)
    write_jsonl(args.out / 'tables.jsonl', all_tables)
    manifest = {'api_calls': 0, 'role': 'base', 'documents': documents, 'pages': len(all_pages),
        'paragraphs': len(all_paragraphs), 'tables_extracted': len(all_tables),
        'table_candidate_pages': sum(bool(p['table_captions']) for p in all_pages),
        'blocks_with_condition_signals': sum(bool(p['condition_signals']) for p in all_paragraphs),
        'warning': 'Structural/condition signals are heuristics, not final applicability or obligation decisions.'}
    if args.check_dev:
        from score_local import _paragraph_key
        gold = json.loads((ROOT / 'cases/dev-rear-lamp/expected.json').read_text(encoding='utf-8'))['requirements']
        wanted = {(r['source']['regulation'], _paragraph_key(r['source'].get('paragraph'))) for r in gold}
        found = {(r['regulation'], _paragraph_key(r['paragraph'])) for r in all_paragraphs if r['paragraph']}
        manifest['dev_anchor_coverage'] = {'found': len(wanted & found), 'expected': len(wanted),
            'missing': sorted([list(pair) for pair in wanted - found]),
            'note': 'Locating a heading does not measure semantic extraction recall.'}
    (args.out / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in manifest.items() if k != 'documents'}, indent=2))
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    raise SystemExit(main())
