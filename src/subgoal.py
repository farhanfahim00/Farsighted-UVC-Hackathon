"""Evidence-first comparison of two versions of a regulation (no answer-key access).

This module only discovers changes and produces evidence packets.  It does not
claim that a textual change necessarily modifies an engineering requirement.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
import hashlib
import json
import re
from typing import Iterable

# A leading number followed by a dot is a clause.  The no-final-dot form is
# accepted for revised clauses such as "5.1.4.1 An intervention ...".
NUMBERED = re.compile(r'^(?P<label>\d+(?:\.\d+)*\.)\s+(?P<body>.*)$|^(?P<label2>\d+(?:\.\d+)+)\s+(?P<body2>.+)$')
ANNEX = re.compile(r'^Annex\s+(?P<n>\d+[A-Za-z]?)\b(?!\s+(?:to|of|under|as|in|for)\b)', re.I)
FOOTER = re.compile(r'^\s*UN\s+R\d+\s*,\s*version\s+[^|]+\|\s*page\s+\d+\s+of\s+\d+\s*$', re.I)
DOC_HEADER = re.compile(r'^UN\s+R\d+\s*\(version\s+', re.I)
BULLET = re.compile(r'^\((?:[a-z]|[ivx]{1,4}|\d{1,2})\)\s+', re.I)
SPACES = re.compile(r'\s+')


def canonical(text: str) -> str:
    return SPACES.sub(' ', text).strip()


def text_sha(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


@dataclass
class Clause:
    key: str
    label: str
    scope: str
    text: str
    page: int
    page_end: int

    def data(self):
        return asdict(self)


def parse_pdf(path: str | Path) -> list[Clause]:
    import fitz

    doc = fitz.open(str(path))
    clauses: list[Clause] = []
    scope = 'body'
    active: Clause | None = None
    started = False
    for page_idx, pg in enumerate(doc):
        page = page_idx + 1
        for raw in pg.get_text('text', sort=True).splitlines():
            line = raw.strip()
            if not line or FOOTER.match(line) or DOC_HEADER.match(line):
                continue
            if line.startswith(('Consolidated text of this version', 'Role in the hackathon:',
                                'the bottom of every page.')):
                continue
            ann = ANNEX.match(line)
            if ann:
                scope = 'annex:' + ann['n'].upper()
                active = None
                started = True
                continue
            m = NUMBERED.match(line)
            if m and (m.group('label') or m.group('label2')).split('.')[0] == '0':
                m = None  # table values such as 0.2 are not clause headings
            if m:
                label = (m.group('label') or m.group('label2')).rstrip('.')
                body = (m.group('body') or m.group('body2') or '').strip()
                if not started and label != '1':
                    # Preambles contain footnote markers and numeric sentences.
                    continue
                started = True
                key = scope + ':' + label
                # Duplicate labels can appear in disconnected annex subparts.
                # Disambiguate without replacing the original clause.
                if any(x.key == key for x in clauses):
                    key += '@p' + str(page)
                active = Clause(key, label, scope, body, page, page)
                clauses.append(active)
            elif active is not None:
                # Keep list-marker boundaries; they're often separate duties.
                if BULLET.match(line):
                    active.text += '\n' + line
                elif active.text:
                    active.text += ' ' + line
                else:
                    active.text = line
                active.page_end = page
    doc.close()
    return [c for c in clauses if c.text.strip()]


def comparison(a: str, b: str) -> float:
    aa, bb = canonical(a).lower(), canonical(b).lower()
    if not aa or not bb:
        return 0.0
    if aa == bb:
        return 1.0
    # Character similarity rewards exact unchanged clauses (including numbers),
    # while token overlap catches paragraphs split in the newer version.
    chars = SequenceMatcher(None, aa[:3000], bb[:3000], autojunk=False).ratio()
    sa, sb = set(re.findall(r'\b\w+\b', aa)), set(re.findall(r'\b\w+\b', bb))
    token_cover = len(sa & sb) / max(1, min(len(sa), len(sb)))
    weight = min(1.0, min(len(sa), len(sb)) / 15.0)
    return max(chars, 0.72 * token_cover * weight)


def word_deltas(before: str, after: str, limit: int = 18) -> list[dict]:
    """Small, exact before/after spans for human and model review; no legal conclusions."""
    old_words, new_words = before.split(), after.split()
    delta = []
    matcher = SequenceMatcher(a=old_words, b=new_words, autojunk=False)
    for action, a0, a1, b0, b1 in matcher.get_opcodes():
        if action == 'equal':
            continue
        delta.append({'operation': action, 'before': ' '.join(old_words[a0:a1])[:700],
                      'after': ' '.join(new_words[b0:b1])[:700]})
        if len(delta) >= limit:
            break
    return delta


def evidence_diff(old: list[Clause], new: list[Clause]) -> dict:
    """Construct an intentionally high-recall shortlist for model adjudication.

    Never use expected.json or output-specific regulation names, page numbers or
    requirement IDs to decide which changed provisions are candidates.
    """
    old_map, new_map = {c.key: c for c in old}, {c.key: c for c in new}
    # Exact text matches can establish a move across a completely different
    # top-level section without guessing at numbering or legal semantics.
    exact_new: dict[tuple[str, str], list[Clause]] = {}
    for c in new:
        exact_new.setdefault((c.scope, canonical(c.text).casefold()), []).append(c)
    matched_new: set[str] = set()
    entries: list[dict] = []
    unchanged_moves: list[dict] = []
    for prev in old:
        if prev.key in new_map:
            same_label = new_map[prev.key]
            sim_label = comparison(prev.text, same_label.text)
        else:
            same_label, sim_label = None, 0.0
        # Search only within the same section/scope, and prefer matching clause
        # text over matching its number after renumbering.
        family = prev.label.split('.')[0]
        possible = [c for c in new if c.scope == prev.scope and c.label.split('.')[0] == family]
        cross_family_exact = [c for c in exact_new.get((prev.scope, canonical(prev.text).casefold()), [])
                              if c.key != prev.key]
        if cross_family_exact:
            possible.extend(c for c in cross_family_exact if c not in possible)
        best = max(possible, key=lambda c: comparison(prev.text, c.text), default=None)
        best_score = comparison(prev.text, best.text) if best else 0.0
        # A materially better textual match can indicate renumbering. Do not
        # force ambiguous matches; send both alternatives to the model.
        if best and best_score >= .68 and best_score > sim_label + .12:
            current = best
            classification = 'renumbered_or_rewritten' if prev.key != best.key else 'changed'
        else:
            current = same_label
            classification = 'changed' if current else 'missing_old_clause'
        if current:
            matched_new.add(current.key)
        if current and canonical(prev.text) == canonical(current.text):
            if prev.key != current.key:
                unchanged_moves.append({'old': prev.data(), 'new': current.data()})
            continue
        entries.append({'kind': classification, 'old': prev.data(),
                        'new': current.data() if current else None,
                        'alternate_new': best.data() if (best and current is not best and best_score >= .42) else None,
                        'text_similarity': round(comparison(prev.text, current.text), 3) if current else 0,
                        'word_deltas': word_deltas(prev.text, current.text) if current else []})
    for curr in new:
        if curr.key in matched_new:
            continue
        # If this clause was not matched to any older clause, it is a possible
        # addition. Only the LLM decides whether it creates a duty.
        entries.append({'kind': 'new_clause', 'old': None, 'new': curr.data(),
                        'alternate_new': None, 'text_similarity': 0, 'word_deltas': []})
    return {'old_clauses': len(old), 'new_clauses': len(new), 'candidates': entries,
            'unchanged_moves': unchanged_moves}


def related_requirements(entry: dict, old_requirements: list[dict], max_results: int = 15) -> list[dict]:
    old, new = entry.get('old'), entry.get('new')
    source_label = old['label'] if old else None
    cand_label = new['label'] if new else None
    def score(r):
        s = r.get('source', {}).get('paragraph', '').strip().rstrip('.')
        if s in (source_label, cand_label):
            return 10.0
        if source_label and (s.startswith(source_label + '.') or source_label.startswith(s + '.')):
            return 6.0
        if cand_label and (s.startswith(cand_label + '.') or cand_label.startswith(s + '.')):
            return 4.0
        return max(comparison(r.get('text',''), (new or old or {}).get('text','')), 0.0)
    ranked = [(score(r),r) for r in old_requirements]
    return [r for s,r in sorted(ranked, key=lambda t:-t[0])[:max_results] if s >= .34]


def candidate_packets(changes: dict, old_requirements: list[dict], max_candidates: int = 8) -> list[dict]:
    """Add old duty context, keeping every changed clause discoverable."""
    entries = changes['candidates']
    packets = []
    for start in range(0, len(entries), max_candidates):
        batch = entries[start:start + max_candidates]
        reqs = {}
        for e in batch:
            for r in related_requirements(e, old_requirements):
                reqs[r['id']] = r
        packets.append({'batch_id': f'{start // max_candidates + 1:03}',
                        'changes': batch, 'existing_requirements': list(reqs.values())})
    return packets


def prepare(old_pdf: Path, new_pdf: Path, requirements_old: Path) -> dict:
    old, new = parse_pdf(old_pdf), parse_pdf(new_pdf)
    old_reqs = json.loads(requirements_old.read_text(encoding='utf-8'))
    diff = evidence_diff(old, new)
    packets = candidate_packets(diff, old_reqs)
    return {'old_pdf_sha256': hashlib.sha256(old_pdf.read_bytes()).hexdigest(),
            'new_pdf_sha256': hashlib.sha256(new_pdf.read_bytes()).hexdigest(),
            'old_requirements_sha256': hashlib.sha256(requirements_old.read_bytes()).hexdigest(),
            'old_clauses': len(old), 'new_clauses': len(new),
            'change_candidate_count': len(diff['candidates']),
            'packets': packets}
