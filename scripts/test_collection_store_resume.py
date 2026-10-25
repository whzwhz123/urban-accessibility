import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from Back_end.collection_store import append, create, finish, read


class CollectionResumeTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.data = self.directory.name
        self.plan = {'city_id': 'Fixture', 'demand_run_id': 'demand', 'kind': 'tci',
                     'points': [{'pair_id': 'a'}, {'pair_id': 'b'}], 'minutes': [45],
                     'sampling': 'fixture sample', 'subset_only': True, 'session': 'morning',
                     'request_budget': 60, 'interval_ms': 1000}
        self.job = create(self.data, self.plan)['id']
        self.path = Path(self.data) / 'collection_runs' / 'Fixture' / self.job

    def add(self, records):
        return append(self.data, 'Fixture', self.job, {
            'metadata': {'city_id': 'Fixture', 'demand_run_id': 'demand'}, 'records': records})

    def test_no_route_is_terminal_and_metadata_agrees(self):
        self.add([{'pair_id': 'a', 'status': 'ok'}, {'pair_id': 'b', 'status': 'no_route'}])
        result = finish(self.data, 'Fixture', self.job)
        state = read(self.data, 'Fixture', self.job)
        self.assertEqual(state['completed_keys'], ['a', 'b'])
        self.assertEqual(state['status'], 'completed')
        self.assertEqual((state['valid_records'], state['no_route_records'], state['failed_records']), (1, 1, 0))
        exported = json.loads((self.path / 'latest_records.json').read_text(encoding='utf-8'))
        self.assertTrue(exported['metadata']['complete'])
        self.assertEqual(result['state']['completed_records'], 2)

    def test_api_error_retry_and_read_does_not_rewrite_history(self):
        self.add([{'pair_id': 'a', 'status': 'no_route'}, {'pair_id': 'b', 'status': 'api_error'}])
        # Old state flags are not authoritative. Neither state nor chunks may be migrated on read.
        state_file = self.path / 'state.json'
        old = json.loads(state_file.read_text(encoding='utf-8'))
        old['status'] = 'completed'
        state_file.write_text(json.dumps(old), encoding='utf-8')
        before = {str(p): p.read_bytes() for p in self.path.rglob('*') if p.is_file()}
        state = read(self.data, 'Fixture', self.job)
        self.assertEqual(state['stored_status'], 'completed')
        self.assertEqual(state['status'], 'paused')
        self.assertEqual(state['completed_keys'], ['a'])
        self.assertEqual(state['failed_records'], 1)
        self.assertEqual(state['plan'], self.plan)
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.path.rglob('*') if p.is_file()})
        self.add([{'pair_id': 'b', 'status': 'ok'}])
        self.assertEqual(read(self.data, 'Fixture', self.job)['status'], 'completed')

    def test_only_no_route_completes_without_publishing_valid_values(self):
        self.add([{'pair_id': key, 'status': 'no_route'} for key in ['a', 'b']])
        result = finish(self.data, 'Fixture', self.job)
        self.assertIsNone(result['file'])
        self.assertEqual(result['state']['status'], 'completed')
        self.assertEqual(result['state']['valid_records'], 0)

    def test_isochrone_legacy_fail_and_multiple_minutes(self):
        plan = {**self.plan, 'kind': 'isochrone', 'points': [{'grid_id': 'g'}], 'minutes': [15, 30]}
        self.job = create(self.data, plan)['id']
        append(self.data, 'Fixture', self.job, {'metadata': {'city_id': 'Fixture', 'demand_run_id': 'demand'},
               'features': [{'properties': {'grid_id': 'g', 'minute': 15, 'status': 'no_route'}},
                            {'properties': {'grid_id': 'g', 'minute': 30, 'status': 'fail'}}]})
        state = read(self.data, 'Fixture', self.job)
        self.assertEqual(state['completed_keys'], ['g:15'])
        self.assertEqual(state['expected_records'], 2)
        self.assertEqual(state['status'], 'paused')
        self.assertEqual(state['failed_records'], 1)


if __name__ == '__main__':
    unittest.main()
