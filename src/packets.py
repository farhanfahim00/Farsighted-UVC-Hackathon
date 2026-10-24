"""Assemble bounded evidence using index IDs; never reads development answers."""
import base64
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re

import pymupdf

from .contracts import ROOT, digest
from .index_adapter import load_index


class EvidenceIndex:
    def __init__(self, folder):
        folder = Path(folder)
        loaded = load_index(folder)
        self.manifest, self.rows = loaded['manifest'], loaded['rows']
        self.format = loaded['format']
        self.tables = {t['id']: t for t in loaded['tables']}
        if len(self.tables) != len(loaded['tables']):
            raise ValueError('Duplicate table IDs')
        self.definitions, self.scopes = loaded['definitions'], loaded['scopes']
        if any(r['role'] != 'base' for r in self.rows):
            raise ValueError('Main runtime requires a base-only index; select a different explicit case policy before changing this')
        self.by_id = {r['id']: r for r in self.rows}
        if len(self.by_id) != len(self.rows):
            raise ValueError('Duplicate paragraph IDs')
        self.pages = {}
        self.lines = {}
        self.by_reg = defaultdict(list)
        for r in self.rows:
            self.by_reg[r['regulation']].append(r)
        for page in loaded['pages']:
            doc = (page['regulation'], page['version'], page['file'])
            key = (*doc, page['page'])
            if key in self.pages:
                raise ValueError('Duplicate page identity')
            self.pages[key] = page
            for line in page['lines']:
                if line['id'] in self.lines:
                    raise ValueError('Duplicate source line ID')
                self.lines[line['id']] = {**line, 'page': page['page'], 'regulation': doc[0], 'version': doc[1], 'file': doc[2]}
        if any(lid not in self.lines for r in self.rows for lid in r['source_line_ids']):
            raise ValueError('Broken index source references')
        for r in self.rows:
            if any(pid not in self.by_id for pid in r['parent_ids']):
                raise ValueError('Broken parent reference')
            if any(tid not in self.tables for tid in r.get('table_ids', [])):
                raise ValueError('Broken table reference')
            for lid in r['source_line_ids'] + r['footnote_context_ids']:
                line = self.lines.get(lid)
                if not line or any(line[k] != r[k] for k in ('regulation', 'version', 'file')):
                    raise ValueError('Evidence line belongs to another document or is missing')

    def target(self, spec):
        reg, sep, label = spec.partition('::')
        if not sep:
            raise ValueError('Targets use exact Regulation::Paragraph, e.g. UN R10::6.9.1')
        rows = [r for r in self.by_reg.get(reg, []) if r['paragraph'] == label]
        if len(rows) != 1:
            raise ValueError('Target missing or ambiguous: ' + spec)
        return rows[0]['id']

    def descendants(self, ids):
        selected = set(ids)
        for r in self.rows:
            if any(pid in selected for pid in r['parent_ids']):
                selected.add(r['id'])
        return [r['id'] for r in self.rows if r['id'] in selected]

    def outlines(self):
        result = []
        for reg, rows in self.by_reg.items():
            roots = [r for r in rows if not r['parent_id']]
            scope = [r for r in rows if not r['annex'] and not r['section'] and
                     (r['paragraph'] == '1' or r['paragraph'].startswith('1.'))]
            if reg in self.scopes:
                scope = [self.by_id[rid] for rid in self.scopes[reg].get('paragraph_ids', [])]
            result.append({'regulation': reg, 'scope_context': scope,
                'sections': [{'id': r['id'], 'paragraph': r['paragraph'], 'title': r.get('title'),
                              'preview': r['text'][:350]} for r in roots],
                'scope_limit_note': 'Scope-heading detection is heuristic; inspect the source before excluding a regulation.'})
        return result

    def chunks(self, target_ids, max_chars=24000):
        """Keep each chunk within one physical PDF/version; oversized paragraphs split by target lines."""
        groups, current, size, document = [], [], 0, None
        for rid in self.descendants(target_ids):
            row = self.by_id[rid]
            key = (row['regulation'], row['version'], row['file'])
            cost = len(row['text'])
            if current and (key != document or size + cost > max_chars):
                groups.append(current)
                current, size = [], 0
            current.append(rid)
            size += cost
            document = key
        if current:
            groups.append(current)
        return [self.packet(g) for g in groups]

    def packet(self, target_ids):
        targets = [self.by_id[rid] for rid in target_ids]
        documents = {(r['regulation'], r['version'], r['file']) for r in targets}
        if len(documents) != 1:
            raise ValueError('A packet must belong to one PDF and version')
        context_ids = set(target_ids)
        unresolved = []
        for r in targets:
            context_ids.update(r['parent_ids'])
            for label in r['cross_refs']:
                if isinstance(label, dict):
                    target_id = label.get('target_id')
                    if label['status'] == 'resolved' and target_id in self.by_id:
                        linked = self.by_id[target_id]
                        if all(linked[k] == r[k] for k in ('regulation', 'version', 'file')):
                            context_ids.add(target_id)
                            context_ids.update(linked['parent_ids'])
                            if label['kind'] == 'annex' and linked.get('children_ids'):
                                unresolved.append({'from': r['id'], 'reference': label,
                                    'reason': 'Annex heading included; full annex needs targeted expansion'})
                            continue
                    unresolved.append({'from': r['id'], 'reference': label})
                    continue
                candidates = [q for q in self.by_reg[r['regulation']] if q['version'] == r['version'] and
                    q['file'] == r['file'] and q['paragraph'] == label and q['annex'] == r['annex'] and q['section'] == r['section']]
                if len(candidates) == 1:
                    context_ids.add(candidates[0]['id'])
                else:
                    unresolved.append({'from': r['id'], 'reference': label})
        doc = next(iter(documents))
        target_text = ' '.join(r['text'] for r in targets)
        matched_definitions = [d for d in self.definitions if d.get('term') and
            (d['regulation'], d['version'], d['file']) == doc and
            re.search(r'(?<!\w)' + re.escape(d['term']) + r'(?!\w)', target_text, re.I)]
        for definition in matched_definitions:
            context_ids.add(definition['paragraph_id'])
            context_ids.update(self.by_id[definition['paragraph_id']]['parent_ids'])
        line_owners = defaultdict(list)
        wanted = set()
        for rid in context_ids:
            row = self.by_id[rid]
            for lid in row['source_line_ids']:
                wanted.add(lid)
                if rid in target_ids:
                    line_owners[lid].append(rid)
            wanted.update(row['footnote_context_ids'])
        # Preserve surrounding page text, including table headers, notes and orphan lines.
        pages = sorted({p for r in targets for p in range(r['page'], r['page_end'] + 1)})
        table_ids = {tid for rid in context_ids for tid in self.by_id[rid].get('table_ids', [])}
        table_ids.update(t['id'] for t in self.tables.values() if
            (t['regulation'], t['version'], t['file']) == doc and t['page'] in pages)
        attached_tables = [self.tables[tid] for tid in sorted(table_ids)]
        for table in attached_tables:
            wanted.update(table['source_line_ids'])
            if table['caption_line_id']:
                wanted.add(table['caption_line_id'])
        for page in pages:
            wanted.update(l['id'] for l in self.pages[(*doc, page)]['lines'])
        ordered = sorted(wanted, key=lambda lid: (self.lines[lid]['page'], self.lines[lid]['bbox'][1], self.lines[lid]['bbox'][0], lid))
        refs = {f'L{i:06}': {**self.lines[lid], 'target_paragraph_ids': line_owners[lid]}
                for i, lid in enumerate(ordered, 1)}
        allowed = [alias for alias, line in refs.items() if line['target_paragraph_ids']]
        packet = {'version': 2, 'index_format': self.format, 'targets': targets, 'refs': refs, 'allowed_target_refs': allowed,
                  'unresolved_references': unresolved,
                  'context_paragraphs': [self.by_id[rid] for rid in sorted(context_ids) if rid not in target_ids],
                  'tables': attached_tables, 'definitions': matched_definitions,
                  'visual_pages': sorted(set((pages if any(r['has_table'] for r in targets) else []) +
                                             [t['page'] for t in attached_tables])),
                  'context_over_budget': False}
        packet['context_over_budget'] = len(json.dumps(refs)) + len(json.dumps(attached_tables)) > 120000
        packet['packet_id'] = digest(packet)
        return packet


def split_packet(packet):
    allowed = packet['allowed_target_refs']
    if len(allowed) < 2:
        return []
    middle = len(allowed) // 2
    children = []
    for refs in (allowed[:middle], allowed[middle:]):
        child = {**packet, 'allowed_target_refs': refs, 'split_parent': packet['packet_id']}
        child.pop('packet_id', None)
        child['packet_id'] = digest(child)
        children.append(child)
    return children


def model_packet(packet):
    aliases = {r['id']: alias for alias, r in packet['refs'].items()}
    return {'packet_id': packet['packet_id'],
            'targets': [{k:r[k] for k in ('id', 'paragraph', 'page', 'page_end', 'title', 'breadcrumb',
                        'condition_signals', 'inherited_condition_source_ids') if k in r} for r in packet['targets']],
            'context_paragraphs': [{'id': r['id'], 'paragraph': r['paragraph'], 'title': r.get('title'),
                'breadcrumb': r.get('breadcrumb', []), 'refs': [aliases[lid] for lid in r['source_line_ids'] if lid in aliases]}
                for r in packet.get('context_paragraphs', [])],
            'tables': [{k:t[k] for k in ('id', 'page', 'cells', 'caption', 'risk_flags', 'owner_status')} |
                {'refs': [aliases[lid] for lid in t['source_line_ids'] if lid in aliases]}
                for t in packet.get('tables', [])],
            'definitions': [{'term': d['term'], 'paragraph_id': d['paragraph_id']} for d in packet.get('definitions', [])],
            'source_lines': {alias: {'text': r['text'], 'page': r['page']} for alias,r in packet['refs'].items()},
            'allowed_target_refs': packet['allowed_target_refs'], 'unresolved_references': packet['unresolved_references']}


def page_images(packet):
    """Render original PDF pages with headers/notes. No tight crop can remove governing context."""
    if not packet['visual_pages']:
        return []
    r = packet['targets'][0]
    path = ROOT / 'regulations/pdf' / '_'.join(r['regulation'].replace('/', ' ').split()) / r['role'] / r['file']
    if hashlib.sha256(path.read_bytes()).hexdigest() != r['sha256']:
        raise ValueError('PDF checksum changed before rendering')
    result = []
    with pymupdf.open(path) as doc:
        for p in packet['visual_pages']:
            data = doc[p-1].get_pixmap(dpi=150).tobytes('png')
            result.extend([{'type': 'text', 'text': f'Regulatory PDF physical page {p}; headers and notes retained'},
                           {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': base64.b64encode(data).decode()}}])
    return result
