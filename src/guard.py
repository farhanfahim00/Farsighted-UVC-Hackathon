"""Per-row evidence checks. Semantic correctness remains a separate measurement."""
import re
import jsonschema

from .contracts import REQUIREMENT, digest


def normalized(text):
    return ' '.join(text.casefold().split())


def validate_rows(output, packet):
    valid, quarantine, duplicates = [], [], []
    seen = set()
    if not isinstance(output, dict) or not isinstance(output.get('requirements'), list):
        return {'valid': [], 'quarantine': [{'row': output, 'errors': ['Missing requirements array']}], 'duplicates': []}
    targets = {r['id']: r for r in packet['targets']}
    for raw in output['requirements']:
        errors = [e.message for e in jsonschema.Draft202012Validator(REQUIREMENT).iter_errors(raw)]
        if errors:
            quarantine.append({'row': raw, 'errors': errors})
            continue
        text, condition = raw['text'].strip(), raw['condition'].strip()
        if not text or not raw['reason'].strip():
            errors.append('Requirement text and applicability reason are required')
        if raw['applicability'] == 'conditional' and not condition:
            errors.append('Conditional requirement needs a condition')
        if condition and normalized(condition) not in normalized(text):
            text += '\nApplicability condition: ' + condition
        if len(text) > 2000:
            errors.append('Exported text exceeds 2000 characters')
        refs = raw['refs']
        if not refs or raw['primary_ref'] not in refs:
            errors.append('Evidence must contain primary_ref')
        if any(ref not in packet['refs'] for ref in refs):
            errors.append('Invented evidence alias')
        if raw['primary_ref'] not in packet['allowed_target_refs']:
            errors.append('Primary citation is outside target lines')
        if errors:
            quarantine.append({'row': raw, 'errors': errors})
            continue
        evidence = [{**packet['refs'][ref], 'alias': ref} for ref in dict.fromkeys(refs)]
        primary = packet['refs'][raw['primary_ref']]
        owners = [pid for pid in primary['target_paragraph_ids'] if pid in targets]
        if len(owners) != 1:
            quarantine.append({'row': raw, 'errors': ['Primary evidence has ambiguous paragraph ownership']})
            continue
        owner = targets[owners[0]]
        # A table pseudo-section is evidence within its governing numbered clause.
        # Preserve the table line as the citation, but export the clause label.
        if owner.get('is_table') or owner.get('kind') == 'table':
            # A table line's target can be a synthetic pseudo-paragraph. Its
            # numbered governing clause may be context-only, not a target.
            indexed = {r['id']: r for r in packet.get('context_paragraphs', [])}
            indexed.update(targets)
            parents = [indexed[pid] for pid in owner.get('parent_ids', []) if pid in indexed
                       and not (indexed[pid].get('is_table') or indexed[pid].get('kind') == 'table')]
            if not parents:
                quarantine.append({'row': raw, 'errors': ['Table target has no verified governing numbered clause']})
                continue
            owner = parents[0]
        source = {'regulation': owner['regulation'], 'version': owner['version'],
                  'paragraph': owner['paragraph'], 'page': primary['page']}
        key = digest({'text': normalized(text), 'source': source})
        if key in seen:
            duplicates.append(raw)
            continue
        seen.add(key)
        warnings = []
        cited_text = ' '.join(e['text'] for e in evidence)
        numbers = set(re.findall(r'(?<!\w)[−-]?\d+(?:[.,]\d+)?', text))
        if any(number not in cited_text for number in numbers):
            warnings.append('Check numerical values/units and their table context')
        valid.append({'id': 'R-' + key[:20], 'text': text, 'source': source,
                      'evidence': evidence, 'applicability': raw['applicability'],
                      'condition': condition, 'reason': raw['reason'], 'review_flags': warnings})
    if not output['requirements'] and not str(output.get('exclusion_reason', '')).strip():
        quarantine.append({'row': output, 'errors': ['Empty extraction requires an exclusion reason']})
    return {'valid': valid, 'quarantine': quarantine, 'duplicates': duplicates}


def export(team_id, case_id, results):
    rows, ids = [], set()
    for result in results:
        for row in result.get('valid', []):
            if row['id'] not in ids:
                ids.add(row['id'])
                rows.append({k: row[k] for k in ('id', 'text', 'source')})
    return {'team_id': team_id, 'case_id': case_id, 'requirements': sorted(rows, key=lambda r:r['id'])}
