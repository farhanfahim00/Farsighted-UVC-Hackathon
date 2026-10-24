"""Extract, split truncated packets, salvage valid rows, and repair bad rows once."""
import json

from .contracts import EXTRACTION, digest, prompt
from .guard import validate_rows
from .llm import IncompleteResponse
from .packets import model_packet, page_images, split_packet


def extract(client, packet, product, fingerprint, images=True, depth=0):
    if packet.get('context_over_budget'):
        return {'packet_id': packet['packet_id'], 'valid': [], 'quarantine': [],
                'errors': ['Evidence context exceeds local limit; rebuild a smaller target group'], 'duplicates': []}
    # Skip costly PDF rendering once this invocation cannot start another request.
    if hasattr(client, 'attempt_limit_reached') and client.attempt_limit_reached():
        return {'packet_id': packet['packet_id'], 'valid': [], 'quarantine': [],
                'errors': ['BudgetExceeded: Local attempt limit reached; resume with an explicit budget'], 'duplicates': []}
    content = [{'type': 'text', 'text': json.dumps({'product': product, 'packet': model_packet(packet)}, ensure_ascii=False)}]
    if images:
        content.extend(page_images(packet))
    try:
        output = client.request(prompt('extract'), content, EXTRACTION, fingerprint)
    except IncompleteResponse as exc:
        children = split_packet(packet) if exc.reason == 'max_tokens' and depth < 3 else []
        if not children:
            return {'packet_id': packet['packet_id'], 'valid': [], 'quarantine': [], 'duplicates': [], 'errors': [str(exc)]}
        results = [safe_extract(client, child, product, fingerprint, images, depth+1) for child in children]
        merged = {'packet_id': packet['packet_id'],
                  **{key: [row for result in results for row in result.get(key, [])]
                     for key in ('valid', 'quarantine', 'duplicates', 'errors')},
                  'split_children': results}
        # All child windows returning no-duty is a legitimate completed result.
        # Previously this reason was discarded, so the parent appeared failed.
        if all(r.get('exclusion_reason') for r in results) and not merged['valid']:
            merged['exclusion_reason'] = 'All split targets explicitly returned no applicable duty'
        unique = {}
        for row in merged['valid']:
            if row['id'] in unique:
                merged['duplicates'].append(row)
            else:
                unique[row['id']] = row
        merged['valid'] = list(unique.values())
        return merged
    result = validate_rows(output, packet)
    result.update(packet_id=packet['packet_id'], errors=[], exclusion_reason=output.get('exclusion_reason', ''))
    if result['quarantine']:
        repair_content = content + [{'type': 'text', 'text': 'Repair only these rejected rows; return replacements, not the already accepted set:\n' + json.dumps(result['quarantine'])}]
        try:
            repaired = client.request(prompt('extract'), repair_content, EXTRACTION, fingerprint)
            repaired_result = validate_rows(repaired, packet)
            result['repair_original_quarantine'] = result['quarantine']
            result['quarantine'] = repaired_result['quarantine'] if repaired_result['valid'] else result['quarantine']
            result['valid'].extend(repaired_result['valid'])
            result['duplicates'].extend(repaired_result['duplicates'])
        except (RuntimeError, ValueError, OSError) as exc:
            result['errors'].append('Repair not completed: ' + str(exc))
    unique = {}
    for row in result['valid']:
        if row['id'] in unique:
            result['duplicates'].append(row)
        unique[row['id']] = row
    result['valid'] = list(unique.values())
    return result


def safe_extract(client, packet, product, fingerprint, images=True, depth=0):
    try:
        return extract(client, packet, product, fingerprint, images, depth)
    except (RuntimeError, ValueError, OSError, KeyError, TypeError) as exc:
        return {'packet_id': packet['packet_id'], 'valid': [], 'quarantine': [], 'duplicates': [],
                'errors': [type(exc).__name__ + ': ' + str(exc)]}
