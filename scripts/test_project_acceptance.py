"""Offline project acceptance; every write stays in TemporaryDirectory."""
import io
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from Back_end.project_bundle import export_project, import_project, restore_project, restore_references, list_projects
from Back_end.custom_inputs import build_template_package, import_package, prepare_custom_city_poi
from Back_end.city_network import prepare_city_network

CITY = dict(id='AcceptOffline', name='Offline fixture', province='Fixture', city_code='TEST', center=[116.4, 39.9])
RUN = '20261025_120000_1234abcd'


def rewrite(content, transform=None, extra=None):
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(content)) as source, zipfile.ZipFile(output, 'w') as target:
        for name in source.namelist():
            raw = source.read(name)
            if name == 'project.json' and transform:
                value = json.loads(raw)
                replacement = transform(value)
                raw = json.dumps(value if replacement is None else replacement).encode()
            target.writestr(name, raw)
        for name, raw in (extra or {}).items():
            target.writestr(name, raw)
    return output.getvalue()


class ProjectAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.target = self.root / 'target'
        run = self.source / 'demand_runs' / CITY['id'] / RUN
        run.mkdir(parents=True)
        (run / 'summary.json').write_text(json.dumps({'run_id': RUN, 'city_id': CITY['id']}))
        (run / 'parameters.json').write_text('{"sigma":15}')
        (run / 'checkpoint.pkl').write_bytes(b'NEVER LOAD')
        self.package = export_project(self.source, CITY, {'workspace': {'demand_id': RUN}}, include_results=True)

    def test_roundtrip_staged_then_explicit_idempotent_restore(self):
        project = import_project(self.target, self.package['content'])
        self.assertFalse((self.target / 'demand_runs').exists())
        self.assertFalse(project['can_resume_checkpoint'])
        result = restore_project(self.target, project['project_id'])
        self.assertEqual(len(result['restored_references']), 1)
        folder = self.target / 'demand_runs' / CITY['id'] / RUN
        self.assertEqual((folder / 'parameters.json').read_text(), '{"sigma":15}')
        self.assertFalse((folder / 'checkpoint.pkl').exists())
        again = restore_references(self.target, project['project_id'])
        self.assertFalse(again['restored_references'][0]['copied_files'])
        self.assertEqual(len(list_projects(self.target, CITY)['projects']), 1)
        self.assertTrue(result['requires_signature_validation'])

    def test_conflict_skips_entire_run_without_overwrite(self):
        project = import_project(self.target, self.package['content'])
        folder = self.target / 'demand_runs' / CITY['id'] / RUN
        folder.mkdir(parents=True)
        (folder / 'summary.json').write_text('existing')
        result = restore_references(self.target, project['project_id'])
        self.assertEqual(result['restored_references'], [])
        self.assertEqual(result['restore_warnings'][0]['reason'], 'group_skipped')
        self.assertEqual((folder / 'summary.json').read_text(), 'existing')
        self.assertFalse((folder / 'parameters.json').exists())

    def test_damaged_staged_attachment_skips_entire_run(self):
        project = import_project(self.target, self.package['content'])
        staged = self.target / project['data_root'] / 'demand_runs' / CITY['id'] / RUN
        (staged / 'parameters.json').write_text('damaged')
        result = restore_references(self.target, project['project_id'])
        self.assertEqual(result['restored_references'], [])
        self.assertFalse((self.target / 'demand_runs').exists())

    def test_wrong_city_is_rejected_before_copy(self):
        project = import_project(self.target, self.package['content'])
        with self.assertRaises(ValueError):
            restore_project(self.target, project['project_id'], original_city={'id': 'Other'})
        self.assertFalse((self.target / 'demand_runs').exists())

    def test_invalid_zip_is_validation_error(self):
        for content in (b'', b'not zip', b'PK\x03\x04broken'):
            with self.subTest(content=content), self.assertRaises(ValueError):
                import_project(self.target, content)
        self.assertFalse(self.target.exists())

    def test_missing_manifest_is_validation_error(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('data/a.csv', 'a')
        with self.assertRaises(ValueError):
            import_project(self.target, buffer.getvalue())
        self.assertFalse(self.target.exists())

    def test_nonobject_manifest_is_validation_error(self):
        with self.assertRaises(ValueError):
            import_project(self.target, rewrite(self.package['content'], lambda value: []))
        self.assertFalse(self.target.exists())

    def test_unsafe_zip_paths_are_rejected_before_writes(self):
        for name in ('../outside.csv', '/absolute.csv', 'data/../../outside.csv', 'data/C:/outside.csv', 'data/NUL.csv', 'data/a./b.csv', '..\\outside.csv'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                import_project(self.target, rewrite(self.package['content'], extra={name: 'unsafe'}))
        self.assertFalse(self.target.exists())

    def test_checksum_and_unlisted_member_rejected(self):
        bad = rewrite(self.package['content'], lambda value: value['files'][0].update(sha256='0' * 64))
        with self.assertRaisesRegex(ValueError, 'checksum'):
            import_project(self.target, bad)
        with self.assertRaises(ValueError):
            import_project(self.target, rewrite(self.package['content'], extra={'data/unlisted.csv': 'x'}))
        self.assertFalse(self.target.exists())

    def test_absolute_payload_and_unsafe_id_rejected(self):
        bad = rewrite(self.package['content'], lambda value: value['payload'].update(path='C:/private/file.csv'))
        with self.assertRaises(ValueError):
            import_project(self.target, bad)
        for identity in ('../other', 'Accept/Other'):
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                export_project(self.source, {'id': identity}, {})

    def test_portable_inputs_restore_new_versions_without_overwrite(self):
        imported = import_package(CITY, self.source, build_template_package(), 'fixture.zip', 'WGS84')
        city = imported['city']
        sources = imported['source_overrides']
        prepare_city_network(city, Path(sources['bus_dir']), self.source / 'processed')
        prepare_custom_city_poi(city, Path(sources['poi_dir']), self.source / 'processed')
        package = export_project(self.source, city, {}, include_inputs=True)
        self.assertIsNotNone(package['manifest']['project_inputs'])
        project = import_project(self.target, package['content'])
        first = restore_project(self.target, project['project_id'], restore_inputs=True)
        self.assertEqual(first['input_status'], 'restored')
        self.assertEqual(first['city_status'], 'registered')
        second = restore_project(self.target, project['project_id'], restore_inputs=True, original_city=first['city'])
        self.assertEqual(second['input_status'], 'restored')
        self.assertNotEqual(first['input_version'], second['input_version'])
        self.assertTrue((self.target / 'custom_inputs' / CITY['id'] / first['input_version']).is_dir())
        self.assertFalse((self.target / 'processed').exists())

    def test_missing_input_package_reports_failure_not_activation(self):
        project = import_project(self.target, self.package['content'])
        result = restore_project(self.target, project['project_id'], restore_inputs=True)
        self.assertEqual(result['input_status'], 'failed')
        self.assertFalse((self.target / 'custom_inputs').exists())


if __name__ == '__main__':
    unittest.main()
