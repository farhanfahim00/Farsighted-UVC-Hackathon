"""All-regulation scope screening with explicit uncertainty and modal recall safety net."""
import json
import re

import jsonschema
from .contracts import SCREENING, prompt


def select(client, index, product, fingerprint):
    outlines = index.outlines()
    batches = [outlines[i:i+4] for i in range(0, len(outlines), 4)]
    def screen(batch):
        output = client.request(prompt('screen'), [{'type': 'text', 'text': json.dumps({'product': product, 'regulations': batch})}], SCREENING, fingerprint)
        jsonschema.validate(output, SCREENING)
        expected = {r['regulation'] for r in batch}
        decisions = {r['regulation']: r for r in output['decisions']}
        if len(decisions) != len(output['decisions']) or set(decisions) != expected:
            raise ValueError('Screening must return each supplied regulation exactly once')
        return decisions
    decisions = {}
    for batch_result in client.parallel(screen, batches):
        decisions.update(batch_result)
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
    chosen = set()
    for reg, decision in decisions.items():
        rows = index.by_reg[reg]
        supported = decision['fact_properties'] and all(definite_support(p) for p in decision['fact_properties'])
        if decision['decision'] == 'exclude' and supported:
            continue
        if decision['decision'] == 'exclude':
            decision['decision'] = 'conditional'
            decision['exclusion_overridden'] = 'No definite product support independent of unverified image transcription'
        allowed = {r['id'] for r in rows}
        if not set(decision['section_ids']) <= allowed:
            raise ValueError('Invented section ID in screening')
        chosen.update(index.descendants(decision['section_ids']))
        # Safety net augments selection rather than making modal words a hard filter.
        chosen.update(r['id'] for r in rows if re.search(r'\b(?:shall|must)\b', r['text'], re.I)
            or r['is_table'] or r.get('has_obligation') or r.get('inherits_obligation') or r.get('heading_value_item'))
    return {'decisions': list(decisions.values()), 'target_ids': [r['id'] for r in index.rows if r['id'] in chosen],
            'warning': 'Model-based exclusions still need semantic evaluation. Modal safety net does not prove complete candidate recall.'}
