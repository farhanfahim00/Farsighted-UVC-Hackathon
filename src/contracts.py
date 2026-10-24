"""Stable wire schemas. Local checks enforce stronger evidence constraints."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def object_schema(properties):
    return {'type': 'object', 'additionalProperties': False, 'required': list(properties), 'properties': properties}


STRING = {'type': 'string'}
STRINGS = {'type': 'array', 'items': STRING}
FACT = object_schema({
    'property': STRING,
    'status': {'type': 'string', 'enum': ['stated', 'explicitly_absent', 'unknown', 'representative', 'conflicting', 'observed_uncertain']},
    'value': {'type': ['string', 'number', 'boolean', 'null']},
    'unit': {'type': ['string', 'null']}, 'evidence_ids': STRINGS,
})
IMAGE_ANNOTATION = object_schema({
    'id': STRING, 'asset_id': STRING, 'quote': STRING,
    'region': object_schema({k: {'type': 'number'} for k in ('x0', 'y0', 'x1', 'y1')}),
    'qualification': {'type': 'string', 'enum': ['explicit', 'provisional', 'unclear']},
})
PROFILE = object_schema({'case_id': STRING, 'packet_id': STRING,
    'image_annotations': {'type': 'array', 'items': IMAGE_ANNOTATION},
    'facts': {'type': 'array', 'items': FACT}})
REQUIREMENT = object_schema({'text': STRING,
    'applicability': {'type': 'string', 'enum': ['applicable', 'conditional']},
    'condition': STRING, 'reason': STRING, 'primary_ref': STRING, 'refs': STRINGS})
EXTRACTION = object_schema({'requirements': {'type': 'array', 'items': REQUIREMENT}, 'exclusion_reason': STRING})
SCREENING = object_schema({'decisions': {'type': 'array', 'items': object_schema({
    'regulation': STRING, 'decision': {'type': 'string', 'enum': ['include', 'conditional', 'exclude']},
    'reason': STRING, 'fact_properties': STRINGS, 'section_ids': STRINGS})}})


def prompt(name):
    return (ROOT / 'src/prompts' / (name + '.md')).read_text(encoding='utf-8')


def runtime_fingerprint():
    """Invalidate saved responses if runtime, parser or qualification logic changes."""
    paths = [*sorted(p for p in (ROOT / 'src').rglob('*.py') if 'tests' not in p.relative_to(ROOT / 'src').parts),
             *sorted((ROOT / 'src/prompts').glob('*.md')),
             ROOT / 'tools/index_evidence.py', ROOT / 'tools/paragraph_structure.py', ROOT / 'tools/product_contract.py']
    return digest([(p.relative_to(ROOT).as_posix(), hashlib.sha256(p.read_bytes()).hexdigest()) for p in paths])
