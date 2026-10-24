"""Load Farhan's evidence schema for the shared runtime; keep old indices readable."""
import json
from pathlib import Path


def read_jsonl(path):
    with Path(path).open(encoding='utf-8') as stream:
        return [json.loads(line) for line in stream if line.strip()]


def load_index(folder):
    folder = Path(folder)
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    canonical = 'files' in manifest
    if canonical:
        if manifest.get('skipped_files'):
            raise ValueError('Incomplete index: manifest lists skipped input PDFs')
        if not manifest['files'] or any(not f['sha256_verified'] for f in manifest['files']):
            raise ValueError('Index lacks verified PDF checksums')
        if any(f['role'] != 'base' for f in manifest['files']):
            raise ValueError('Main runtime needs a base-only build: python src/indexer.py --roles base --out runs/shared-index --jobs 1')
        documents = {(f['regulation'], f['version'], f['role'], f['file']): f for f in manifest['files']}
        if len(documents) != len(manifest['files']):
            raise ValueError('Duplicate document identity in manifest')
    rows = read_jsonl(folder / 'paragraphs.jsonl')
    pages = read_jsonl(folder / 'pages.jsonl')
    tables = read_jsonl(folder / 'tables.jsonl') if canonical else []
    definitions = read_jsonl(folder / 'definitions.jsonl') if canonical else []
    scopes = json.loads((folder / 'regulation_scopes.json').read_text(encoding='utf-8')) if canonical else {}
    if canonical:
        for r in rows + pages:
            key = (r['regulation'], r['version'], r['role'], r['file'])
            if key not in documents:
                raise ValueError('Evidence refers to a document absent from its manifest')
            r['sha256'] = documents[key]['sha256']
        for r in rows:
            r['parent_ids'] = r['parent_chain']
            r['is_table'] = r['kind'] == 'table'
            r['review_flags'] = ['parent_' + r['parent_status']] if r['parent_status'] in ('ambiguous', 'not_found') else []
            r['inherited_condition_source_ids'] = r['inherited_condition_ids']
        for page in pages:
            page['unassigned_line_ids'] = [line['id'] for line in page['lines']
                if not line['assigned_to_paragraph'] and not line['running_header'] and not line['boilerplate']]
    return {'manifest': manifest, 'rows': rows, 'pages': pages, 'tables': tables,
            'definitions': definitions, 'scopes': scopes, 'format': 'farhan-evidence' if canonical else 'legacy-evidence'}
