"""All-regulation scope screening with explicit uncertainty and modal recall safety net."""
import json
import re

import jsonschema
from .contracts import SCREENING, prompt


def select(client, index, product, fingerprint, checkpoint=None):
    outlines = index.outlines()
    batches = [outlines[i:i+4] for i in range(0, len(outlines), 4)]
    def screen(batch):
        output = client.request(prompt('screen'), [{'type': 'text', 'text': json.dumps({'product': product, 'regulations': batch})}], SCREENING, fingerprint)
        jsonschema.validate(output, SCREENING)
        expected = {r['regulation'] for r in batch}
        decisions = {r['regulation']: r for r in output['decisions']}
        if len(decisions) != len(output['decisions']) or set(decisions) != expected:
            raise ValueError('Screening must return each supplied regulation exactly once')
        if checkpoint:
            checkpoint(batch, decisions)
        return decisions
    decisions = {}
    for batch_result in client.parallel(screen, batches):
        decisions.update(batch_result)
    return apply_decisions(index, product, decisions)


def apply_decisions(index, product, decisions):
    """Apply evidence policy separately from paid screening; supports offline audits."""
    facts = {f['property']: f for f in product['profile']['facts']}
    hydrated = {f['property']: f for f in product.get('validation', {}).get('facts', [])}
    def definite_support(prop):
        fact = facts.get(prop, {})
        if fact.get('status') not in ('stated', 'explicitly_absent'):
            return False
        evidence = hydrated.get(prop, {}).get('evidence', [])
        # Image transcription can aid inclusion, but must not silently remove
        # a regulation by itself before its accuracy has been evaluated.
        if evidence and all(e.get('source_kind') in ('image', 'image_text') for e in evidence):
            return False
        if not evidence and any(eid.startswith('IMG_') for eid in fact.get('evidence_ids', [])):
            return False
        return True
    # Rank first by sections the screening model selected. The previous
    # global modal/table safety net remains available, but belongs to a
    # separate *review* tier rather than consuming primary extraction slots.
    # This changes priority, not the recall union of selected candidates.
    section_roots, safety_roots, overridden_roots = set(), set(), set()
    for reg, decision in decisions.items():
        rows = index.by_reg[reg]
        supported = bool(decision['fact_properties']) and all(
            definite_support(p) for p in decision['fact_properties'])
        if decision['decision'] == 'exclude' and supported:
            continue
        overridden = decision['decision'] == 'exclude'
        if overridden:
            decision['decision'] = 'conditional'
            decision['exclusion_overridden'] = (
                'No definite product support independent of unverified image transcription')
        allowed = {r['id'] for r in rows}
        if not set(decision['section_ids']) <= allowed:
            raise ValueError('Invented section ID in screening')
        (overridden_roots if overridden else section_roots).update(decision['section_ids'])
        safety = {r['id'] for r in rows if re.search(r'\b(?:shall|must)\b', r['text'], re.I)
                  or r['is_table'] or r.get('has_obligation')
                  or r.get('inherits_obligation') or r.get('heading_value_item')}
        # A regulation explicitly included without any section selection has
        # no reliable section-first queue. Retain its safety obligations in
        # primary rather than accidentally demoting the entire regulation.
        if not decision['section_ids'] and not overridden and decision['decision'] == 'include':
            section_roots.update(safety)
        safety_roots.update(safety)

    # Preserve the full descendant closure of the safety net. Build the
    # primary closure first so a paragraph cannot be sent in two tiers.
    primary_ids = set(index.descendants(section_roots))
    all_ids = set(index.descendants(section_roots | overridden_roots | safety_roots))
    review_ids = all_ids - primary_ids
    review_regs = {reg for reg, decision in decisions.items() if decision.get('exclusion_overridden')}
    source_counts = {
        'section_first': len(primary_ids),
        'safety_net_or_uncertain': len(review_ids),
        'total': len(all_ids),
    }

    return {'decisions': list(decisions.values()), 'target_ids': [r['id'] for r in index.rows if r['id'] in all_ids],
            'primary_target_ids': [r['id'] for r in index.rows if r['id'] in primary_ids],
            'review_target_ids': [r['id'] for r in index.rows if r['id'] in review_ids],
            'review_regulations': sorted(review_regs),
            'selection_tier_counts': source_counts,
            'warning': 'Both tiers preserve conditional recall. Safety-net candidates are deferred, not discarded; no semantic quality guarantee.'}
