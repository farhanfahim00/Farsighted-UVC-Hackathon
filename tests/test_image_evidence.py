"""Offline evidence-policy checks. These fixtures do not measure OCR or model accuracy."""
import copy
import json
from pathlib import Path
from contextlib import contextmanager
import shutil
import unittest
import uuid

from src.contracts import ROOT, PROFILE
from src.llm import Client
from src.product import content, build_profile
from src.selection import select
from tools.product_contract import prepare, validate_profile


@contextmanager
def scratch():
    # Inherit Windows ACLs: tempfile's private ACL conflicts with the sandbox's
    # secondary identity on Python 3.14.
    folder = ROOT / 'runs/runtime-tests' / uuid.uuid4().hex
    folder.mkdir(parents=True)
    try:
        yield folder
    finally:
        if folder.parent == ROOT / 'runs/runtime-tests' and len(folder.name) == 32:
            shutil.rmtree(folder)


class ImageEvidenceChecks(unittest.TestCase):
    def setUp(self):
        self.packet = prepare(ROOT / 'cases/dev-rear-lamp', ['spec.md', 'exploded_view.png'])
        self.asset = next(a for a in self.packet['assets'] if a['kind'] == 'image')
        self.annotation = {'id': 'IMG_LED', 'asset_id': self.asset['id'],
            'quote': 'LED modules on PCB', 'qualification': 'explicit',
            'region': {'x0': .66, 'y0': .56, 'x1': .95, 'y1': .62}}
        self.fact = {'property': 'parts.light_source', 'status': 'stated',
            'value': 'LED modules on PCB', 'unit': None, 'evidence_ids': ['IMG_LED']}

    def profile(self):
        return {'case_id': self.packet['case_id'], 'packet_id': self.packet['packet_id'],
                'image_annotations': [copy.deepcopy(self.annotation)], 'facts': [copy.deepcopy(self.fact)]}

    def test_explicit_annotation_can_support_stated_fact_but_is_unverified(self):
        checked = validate_profile(self.packet, self.profile())
        self.assertTrue(checked['valid'], checked['errors'])
        evidence = checked['facts'][0]['evidence'][0]
        self.assertEqual(evidence['sha256'], self.asset['sha256'])
        self.assertEqual(evidence['text'], 'LED modules on PCB')
        self.assertFalse(evidence['transcription_verified'])
        self.assertTrue(checked['semantic_review_required'])

    def test_bad_annotation_source_region_and_duplicate_are_rejected(self):
        for mutation in ('unknown_asset', 'reversed_box', 'out_of_bounds', 'blank', 'duplicate'):
            with self.subTest(mutation=mutation):
                profile = self.profile()
                annotation = profile['image_annotations'][0]
                if mutation == 'unknown_asset':
                    annotation['asset_id'] = 'invented'
                elif mutation == 'reversed_box':
                    annotation['region']['x0'] = .98
                elif mutation == 'out_of_bounds':
                    annotation['region']['y1'] = 1.2
                elif mutation == 'blank':
                    annotation['quote'] = '  '
                else:
                    profile['image_annotations'].append(copy.deepcopy(annotation))
                self.assertFalse(validate_profile(self.packet, profile)['valid'])

    def test_provisional_or_unclear_image_text_cannot_be_confirmed(self):
        for qualification in ('provisional', 'unclear'):
            profile = self.profile()
            profile['image_annotations'][0]['qualification'] = qualification
            self.assertFalse(validate_profile(self.packet, profile)['valid'])
        profile['image_annotations'][0]['qualification'] = 'provisional'
        profile['facts'][0]['status'] = 'representative'
        self.assertTrue(validate_profile(self.packet, profile)['valid'])

    def test_confirmed_part_does_not_confirm_representative_material(self):
        source = next(e for e in self.packet['evidence'] if '| Housing' in e['text'])
        profile = self.profile()
        profile['facts'].append({'property': 'housing.material', 'status': 'stated',
            'value': 'PP', 'unit': None, 'evidence_ids': [source['id']]})
        self.assertFalse(validate_profile(self.packet, profile)['valid'])
        profile['facts'][-1]['status'] = 'representative'
        self.assertTrue(validate_profile(self.packet, profile)['valid'])

    def test_text_and_image_contradiction_remains_unresolved(self):
        source = next(e for e in self.packet['evidence'] if 'M1' in e['text'])
        profile = self.profile()
        profile['image_annotations'][0]['quote'] = 'Vehicle category N1'
        profile['facts'][0].update(property='vehicle_category', status='conflicting', value=None,
                                   evidence_ids=[source['id'], 'IMG_LED'])
        self.assertTrue(validate_profile(self.packet, profile)['valid'])
        profile['facts'][0]['value'] = 'N1'
        self.assertFalse(validate_profile(self.packet, profile)['valid'])

    def test_same_filename_changed_bytes_invalidates_profile_and_response_cache(self):
        config = json.loads((ROOT / 'config/runtime.json').read_text())
        with scratch() as folder:
            image = folder / 'diagram.png'
            calls = []
            def send(*_):
                calls.append(1)
                return {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': '{}'}], 'usage': {}}
            client = Client(config, folder / 'cache', folder / 'usage.jsonl', online=True, send=send)
            # Opaque fixture bytes exercise hashing; no image decoder or provider is used.
            image.write_bytes(b'fixture-image-version-one')
            first = prepare(folder, ['diagram.png'])
            client.request('test', content(folder, first), PROFILE, 'same-runtime')
            image.write_bytes(b'fixture-image-version-two')
            second = prepare(folder, ['diagram.png'])
            with self.assertRaisesRegex(ValueError, 'changed after preparation'):
                content(folder, first)
            old_profile = {'case_id': first['case_id'], 'packet_id': first['packet_id'], 'facts': []}
            self.assertFalse(validate_profile(second, old_profile)['valid'])
            client.request('test', content(folder, second), PROFILE, 'same-runtime')
            client.online = False
            client.request('test', content(folder, second), PROFILE, 'same-runtime')
            self.assertEqual(len(calls), 2)
            self.assertNotEqual(first['packet_id'], second['packet_id'])

    def test_wire_schema_failure_gets_one_repair(self):
        replies = [self.profile(), self.profile()]
        replies[0]['image_annotations'][0]['region']['x0'] = 'invalid'
        class FakeClient:
            def request(self, *_):
                return replies.pop(0)
        result = build_profile(FakeClient(), ROOT / 'cases/dev-rear-lamp', self.packet, 'test')
        self.assertTrue(result['validation']['valid'])
        self.assertFalse(replies)

    def test_unverified_image_text_alone_cannot_exclude_regulation(self):
        class SmallIndex:
            rows = [{'id': 'r1', 'text': 'The component shall be tested.', 'is_table': False}]
            by_reg = {'TEST': rows}
            def outlines(self):
                return [{'regulation': 'TEST', 'sections': [{'id': 'r1'}]}]
            def descendants(self, ids):
                return ids
        class FakeClient:
            def request(self, *_):
                return {'decisions': [{'regulation': 'TEST', 'decision': 'exclude', 'reason': 'Drawing label',
                    'fact_properties': ['parts.light_source'], 'section_ids': []}]}
            def parallel(self, fn, items):
                return [fn(item) for item in items]
        profile = self.profile()
        selected = select(FakeClient(), SmallIndex(), {'profile': profile,
            'validation': validate_profile(self.packet, profile)}, 'test')
        self.assertEqual(selected['target_ids'], ['r1'])
        self.assertEqual(selected['decisions'][0]['decision'], 'conditional')


if __name__ == '__main__':
    unittest.main()
