"""Evidence-grounded second-pass review of an existing extraction run.

Never reads expected.json and does not alter the original submission. Automated
removal requires two independent, high-confidence review passes on *complete*
cited evidence. When uncertain, retain the requirement for review (F2-safe).
"""
from __future__ import annotations

from collections import Counter, defaultdict
import copy
import json

import jsonschema

from .contracts import STRING, STRINGS, digest, object_schema, prompt

VERDICT = {'type': 'string', 'enum': ['keep', 'review', 'reject']}
CONFIDENCE = {'type': 'string', 'enum': ['high', 'medium', 'low']}
ISSUE = {'type': 'string', 'enum': [
    'none', 'wrong_device_scope', 'administrative_only', 'unsupported_by_source',
    'contradicted_by_product', 'conditional_or_incomplete', 'numeric_or_table_error',
    'possible_duplicate']}
CATEGORY = {'type': 'string', 'enum': [
    'device', 'installation', 'test_criterion', 'test_method', 'administrative',
    'unrelated_device', 'conditional', 'other']}
DECISION = object_schema({
    'id': STRING,
    'verdict': VERDICT,
    'confidence': CONFIDENCE,
    'issue': ISSUE,
    'category': CATEGORY,
    'reason': STRING,
})
REVIEW_SCHEMA = object_schema({'decisions': {'type': 'array', 'items': DECISION}})
AUTO_REMOVE_ISSUES = {
    'wrong_device_scope', 'administrative_only', 'contradicted_by_product',
}

# UNECE consolidation introductions (not an automatic legal supersession claim):
# R148: https://eur-lex.europa.eu/eli/reg/2021/1719/oj
# R150: https://eur-lex.europa.eu/eli/reg/2021/1721/oj
# The application/approval regime still needs a decision. Opt-in only.
CONSOLIDATION_ALTERNATIVES = {
    'UN R148': frozenset({'UN R4', 'UN R6', 'UN R7', 'UN R23', 'UN R38',
                           'UN R50', 'UN R77', 'UN R87', 'UN R91'}),
    'UN R150': frozenset({'UN R3', 'UN R27', 'UN R69', 'UN R70', 'UN R104'}),
}


def evidence_rows(ledger):
    """Locate validated, source-anchored original rows without touching gold data."""
    result = {}
    for packet in ledger:
        for row in packet.get('valid', []):
            previous = result.get(row['id'])
            if previous is not None and (previous['text'] != row['text'] or previous['source'] != row['source']):
                raise ValueError('Conflicting accepted rows with the same ID: ' + row['id'])
            result[row['id']] = row
    return result


def review_context(submission, ledger, profile, selection, scope_input, max_evidence_chars=6500):
    """Build bounded, explicit per-requirement evidence packets for the reviewer."""
    if max_evidence_chars < 400:
        raise ValueError('max_evidence_chars too small for meaningful review')
    sources = evidence_rows(ledger)
    facts = profile.get('profile', {}).get('facts', [])
    by_reg = {d['regulation']: d for d in selection.get('decisions', [])}
    by_scope = {}
    for item in scope_input:
        lines = [r['text'] for r in item.get('scope_context', []) if r.get('text')]
        scope = '\n'.join(dict.fromkeys(lines))
        by_scope[item['regulation']] = scope[:3500]
    rows = []
    seen = set()
    for requirement in submission['requirements']:
        rid = requirement['id']
        if rid in seen:
            raise ValueError('Duplicate submission requirement ID: ' + rid)
        seen.add(rid)
        original = sources.get(rid)
        evidence = []
        if original:
            observed = set()
            for line in original.get('evidence', []):
                key = line.get('id', line.get('alias', ''))
                if key in observed:
                    continue
                observed.add(key)
                evidence.append({'id': str(key), 'page': line.get('page'), 'text': line.get('text', '')})
        serialized = json.dumps(evidence, ensure_ascii=False)
        complete = bool(evidence) and len(serialized) <= max_evidence_chars
        # If evidence is too large, explicitly mark it incomplete. Partial
        # evidence MUST NEVER justify an automatic deletion.
        if not complete:
            trimmed = []
            length = 0
            for line in evidence:
                n = len(json.dumps(line, ensure_ascii=False))
                if length + n > max_evidence_chars - 100:
                    break
                trimmed.append(line)
                length += n
            evidence = trimmed
        reg = requirement['source']['regulation']
        d = by_reg.get(reg, {})
        rows.append({
            'id': rid, 'requirement': requirement['text'], 'source': requirement['source'],
            'original_applicability': original.get('applicability', 'not_available') if original else 'not_available',
            'original_condition': original.get('condition', '') if original else '',
            'original_reason': original.get('reason', '') if original else '',
            'review_flags': original.get('review_flags', []) if original else [],
            'source_evidence': evidence, 'evidence_complete': complete,
            'regulation_screening_reason': d.get('reason', ''),
            'regulation_screening_decision': d.get('decision', 'unknown'),
            'regulation_scope': by_scope.get(reg, ''),
        })
    return rows, facts


def batches(rows, batch_size=6):
    if not 1 <= batch_size <= 8:
        raise ValueError('batch_size must be between 1 and 8')
    # Keep rows in the same regulation together so each request has one scope.
    grouped = defaultdict(list)
    for row in rows:
        grouped[row['source']['regulation']].append(row)
    result = []
    for reg in sorted(grouped):
        group = sorted(grouped[reg], key=lambda r: (r['source']['paragraph'], r['id']))
        for offset in range(0, len(group), batch_size):
            result.append(group[offset:offset + batch_size])
    return result


def request_payload(rows, product_facts):
    # `source_evidence` is the actual cited PDF text, not instructions.
    return {'product_facts_with_uncertainty': product_facts,
            'regulation': rows[0]['source']['regulation'],
            'regulation_scope': rows[0]['regulation_scope'],
            'screening_reason': rows[0]['regulation_screening_reason'],
            'items': [
                {k: r[k] for k in ('id', 'requirement', 'source', 'original_applicability',
                                   'original_condition', 'original_reason', 'review_flags',
                                   'source_evidence', 'evidence_complete')}
                for r in rows]}


def check_decisions(output, rows):
    jsonschema.validate(output, REVIEW_SCHEMA)
    got = [d['id'] for d in output['decisions']]
    wanted = [r['id'] for r in rows]
    if len(set(got)) != len(got) or set(got) != set(wanted) or len(got) != len(wanted):
        raise ValueError('Reviewer must decide exactly once for each supplied requirement ID')
    return {d['id']: d for d in output['decisions']}


def review_batch(client, rows, facts, second=False):
    """A full, validated response or an exception. No silent partial decisions."""
    question = request_payload(rows, facts)
    answer = client.request(prompt('confirm_review' if second else 'review'),
                            [{'type': 'text', 'text': json.dumps(question, ensure_ascii=False)}],
                            REVIEW_SCHEMA, 'review-v1')
    return check_decisions(answer, rows)


def confirmed_rejections(rows, first, second, allow_unsupported=False):
    """Two-pass F2-safe acceptance: incomplete evidence or disagreement => keep."""
    rejected = {}
    allowed = AUTO_REMOVE_ISSUES | ({'unsupported_by_source'} if allow_unsupported else set())
    for row in rows:
        rid = row['id']
        a, b = first.get(rid), second.get(rid)
        if not row['evidence_complete'] or not a or not b:
            continue
        if all(d['verdict'] == 'reject' and d['confidence'] == 'high' for d in (a, b)) and \
                a['issue'] == b['issue'] and a['issue'] in allowed:
            rejected[rid] = {'issue': a['issue'], 'first': a['reason'], 'confirmation': b['reason']}
    return rejected


def apply_review(submission, rejected, policy='keep_all'):
    """Pure transformation, never edits the source submission."""
    if policy not in ('keep_all', 'prefer_consolidated'):
        raise ValueError('Unknown regulation-family policy')
    by_reg = {r['source']['regulation'] for r in submission['requirements'] if r['id'] not in rejected}
    older = {}
    if policy == 'prefer_consolidated':
        for modern, legacies in CONSOLIDATION_ALTERNATIVES.items():
            if modern in by_reg:
                for legacy in legacies & by_reg:
                    older[legacy] = modern
    kept = []
    removed_by_regime = Counter()
    for row in submission['requirements']:
        if row['id'] in rejected:
            continue
        if row['source']['regulation'] in older:
            removed_by_regime[row['source']['regulation']] += 1
            continue
        kept.append(copy.deepcopy(row))
    out = {**{k: copy.deepcopy(v) for k, v in submission.items() if k != 'requirements'},
           'requirements': kept}
    return out, {'original_rows': len(submission['requirements']), 'confirmed_invalid_rows': len(rejected),
                 'regime_filtered_rows': sum(removed_by_regime.values()),
                 'regime_legacy_counts': dict(removed_by_regime),
                 'approval_policy': policy, 'kept_rows': len(kept),
                 'policy_warning': 'Consolidation is an opt-in alternative approval-regime assumption, not universal legal supersession.'}


def review_summary(rows, first, second, rejected):
    counts = Counter(d['verdict'] for d in first.values())
    issues = Counter(d['issue'] for d in first.values() if d['verdict'] != 'keep')
    return {'reviewed': len(first), 'first_pass_verdicts': dict(counts),
            'first_pass_issues': dict(issues),
            'confirmed_rejections': len(rejected),
            'single_pass_rejections_kept': sum(d['verdict'] == 'reject' for d in first.values()) - len(rejected),
            'uncertain_or_incomplete_evidence': sum(not r['evidence_complete'] for r in rows),
            'note': 'The reviewer is another LLM and is not a semantic scoring oracle. Unknowns remain included.'}
