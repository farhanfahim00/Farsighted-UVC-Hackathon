"""Original spec + actual drawing -> source-bound proposed product facts."""
import base64
import hashlib
import json
import mimetypes
from pathlib import Path
import jsonschema

from tools.product_contract import prepare, validate_profile
from .contracts import PROFILE, prompt


def content(case_dir, packet):
    case_dir = Path(case_dir).resolve()
    result = [{'type': 'text', 'text': 'Source passages and qualifications:\n' + json.dumps(packet, ensure_ascii=False)}]
    for asset in packet['assets']:
        path = (case_dir / asset['file']).resolve()
        if not path.is_relative_to(case_dir):
            raise ValueError('Input asset path leaves case folder')
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != asset['sha256']:
            raise ValueError('Product input changed after preparation')
        if asset['kind'] == 'image':
            media = mimetypes.guess_type(path.name)[0]
            result.extend([{'type': 'text', 'text': 'Product image evidence ID: ' + asset['id']},
                           {'type': 'image', 'source': {'type': 'base64', 'media_type': media, 'data': base64.b64encode(data).decode()}}])
        elif path.suffix.lower() in ('.md', '.txt'):
            result.append({'type': 'text', 'text': 'Unchanged original product document: ' + asset['file'] + '\n' + data.decode('utf-8-sig')})
        elif path.suffix.lower() == '.pdf':
            result.extend([{'type': 'text', 'text': 'Original product PDF. Cite extracted passage IDs; unresolved visual information stays unknown.'},
                           {'type': 'document', 'source': {'type': 'base64', 'media_type': 'application/pdf', 'data': base64.b64encode(data).decode()}}])
    return result


def build_profile(client, case_dir, packet, fingerprint):
    def check(output):
        result = validate_profile(packet, output)
        result['errors'].extend(e.message for e in jsonschema.Draft202012Validator(PROFILE).iter_errors(output))
        result['valid'] = not result['errors']
        return result

    data = content(case_dir, packet)
    output = client.request(prompt('profile'), data, PROFILE, fingerprint)
    checked = check(output)
    if not checked['valid']:
        data.append({'type': 'text', 'text': 'Repair this proposed profile using the validation errors:\n' + json.dumps({'profile': output, 'errors': checked['errors']})})
        output = client.request(prompt('profile'), data, PROFILE, fingerprint)
        checked = check(output)
    if not checked['valid']:
        raise ValueError('Profile integrity failed after one repair: ' + '; '.join(checked['errors']))
    return {'profile': output, 'validation': checked}
