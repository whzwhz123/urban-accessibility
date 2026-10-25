"""Small offline fixtures; never touch city datasets or call routing services."""
import json
import pickle
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from Back_end.task_store import (TaskStore, TaskCancelled, CheckpointStore,
    assignment_preflight, content_signature, write_json)
from Back_end.assignment.core import PreparedModel, ExperimentConfig, _bundle_costs
from Back_end.assignment.candidate import assign_msa_active, assign_msa_kpaths_optimized
from Back_end.assignment.capacity import capacity_filter
import Back_end.assignment.run_city as city_runner


def fixture():
    cfg = ExperimentConfig(sample_size=2, total_demand_pax_per_hour=500,
        access_station_count=1, max_transfers=1, paths_per_transfer=3,
        max_iterations=50, relative_gap_tol=1e-12)
    paths = pd.DataFrame({'path_idx': [0], 'od_idx': [0], 'transfers': [1],
        'boardings': [2], 'path_rank_within_transfer': [1],
        'connector_cost_min': [0.0], 'path_hash': [hash((0, 2))]})
    return PreparedModel(cfg, pd.DataFrame({'station_id': ['a', 'b', 'c', 'd']}),
        pd.DataFrame({'edge_idx': [0, 1, 2, 3]}), pd.DataFrame({'cluster_id': ['o', 'd']}),
        pd.DataFrame({'poi_idx': [0, 1], 'access_rank': [1, 1], 'station_idx': [0, 3], 'walk_time_min': [0.01, 0.01]}),
        pd.DataFrame({'od_idx': [0], 'origin_poi_idx': [0], 'destination_poi_idx': [1],
            'candidate_pair_idx': [0], 'demand_pax_per_hour': [500.0]}),
        paths, csr_matrix([[1.0, 0.0, 1.0, 0.0]]),
        np.array([1, 2, 7, 11]), np.array([0, 0, 1, 2]), np.array([1, 2, 3, 3]),
        np.ones(4), np.array([1., 2., 1., 2.]),
        np.arange(4), np.ones(4), np.arange(4), np.arange(4) + 1,
        pd.DataFrame({'capacity_per_hour': [100.] * 4, 'headway_min': [10.] * 4,
            'physical_edge_idx': [0, 1, 2, 3]}),
        {'candidate_paths': 1, 'initial_candidate_paths': 1, 'routes': 4,
            'candidate_od_pairs': 1, 'sampled_nonzero_od_pairs': 1})


class AssignmentTaskTests(unittest.TestCase):
    def test_task_options_persist_and_cancel(self):
        with TemporaryDirectory() as temp:
            store = TaskStore(temp)
            task = store.create({'city_id': 'fixture', 'parameters': {'max_iterations': 50}})
            identifier = task['task_id']
            store.update(identifier, status='running', run_id='example')
            self.assertEqual(TaskStore(temp).get(identifier)['options'], task['options'])
            self.assertFalse(store.cancel_check(identifier)())
            store.request_cancel(identifier)
            self.assertTrue(store.cancel_check(identifier)())
            self.assertEqual(len(store.list()), 1)
            self.assertFalse(list(Path(temp).glob('*.tmp')))

    def test_checkpoint_checksum_and_signature(self):
        with TemporaryDirectory() as temp:
            store = CheckpointStore(temp, 'version1')
            store.save({'model': fixture()})
            self.assertEqual(len(store.load()['model'].paths), 1)
            with self.assertRaises(ValueError):
                CheckpointStore(temp, 'version2').load()
            manifest = json.loads((Path(temp) / 'latest.json').read_text())
            (Path(temp) / manifest['file']).write_bytes(b'corrupt')
            with self.assertRaises(ValueError):
                store.load()

    def test_resume_real_column_generation_and_msa_exact(self):
        expected_model = fixture()
        expected, expected_logs, expected_summary = assign_msa_kpaths_optimized(expected_model)
        self.assertGreater(len(expected_model.paths), 1)
        with TemporaryDirectory() as temp:
            store = CheckpointStore(temp, 'fixture')
            cancelled = False
            def checkpoint(state):
                nonlocal cancelled
                store.save(state)
                if state['column_round'] == 2 and state['msa'] and state['msa']['next_iteration'] == 11:
                    cancelled = True
            with self.assertRaises(TaskCancelled):
                assign_msa_kpaths_optimized(fixture(), checkpoint=checkpoint, cancel_check=lambda: cancelled)
            state = store.load()
            self.assertEqual(state['msa']['next_iteration'], 11)
            model = state['model']
            actual, logs, summary = assign_msa_kpaths_optimized(model, resume_state=state, checkpoint=store.save)
            np.testing.assert_array_equal(actual, expected)
            pd.testing.assert_frame_equal(logs, expected_logs, check_exact=True)
            pd.testing.assert_frame_equal(model.paths, expected_model.paths, check_exact=True)
            self.assertEqual((model.path_bundle != expected_model.path_bundle).nnz, 0)
            self.assertEqual(summary['iterations_by_round'], expected_summary['iterations_by_round'])
            self.assertEqual(len(list(Path(temp).glob('model_*.pickle'))), 2)
            small_states = list(Path(temp).glob('state_*.pickle'))
            self.assertGreater(len(small_states), 2)
            for snapshot in small_states:
                small = pickle.loads(snapshot.read_bytes())
                self.assertNotIn('model', small)
                if small.get('solver'):
                    self.assertNotIn('model', small['solver'])
            np.testing.assert_array_equal(capacity_filter(model, actual)[1], capacity_filter(expected_model, expected)[1])

    def test_column_boundary_resume_exact(self):
        baseline_model = fixture()
        flow, logs, _ = assign_msa_kpaths_optimized(baseline_model)
        with TemporaryDirectory() as temp:
            store = CheckpointStore(temp, 'fixture')
            stopped = False
            def checkpoint(state):
                nonlocal stopped
                store.save(state)
                stopped = state['column_round'] == 2 and state['msa'] is None
            with self.assertRaises(TaskCancelled):
                assign_msa_kpaths_optimized(fixture(), checkpoint=checkpoint, cancel_check=lambda: stopped)
            state = store.load()
            actual, actual_logs, _ = assign_msa_kpaths_optimized(state['model'], resume_state=state)
            np.testing.assert_array_equal(actual, flow)
            pd.testing.assert_frame_equal(actual_logs, logs, check_exact=True)

    def test_numerical_msa_matches_original_loop(self):
        model = fixture()
        assign_msa_kpaths_optimized(model)
        reference_model = __import__('copy').deepcopy(model)
        paths = reference_model.paths
        def costs_and_aon(flow):
            costs = paths.connector_cost_min.to_numpy(float) + np.asarray(reference_model.path_bundle @ _bundle_costs(reference_model, flow)).ravel()
            shortest = costs.min()
            chosen = np.isclose(costs, shortest, rtol=0.0, atol=1e-9)
            aon = np.zeros(len(paths))
            aon[chosen] = 500 / chosen.sum()
            total = float(np.dot(flow, costs))
            gap = max(0., (total - 500 * shortest) / max(total, 1e-12))
            return aon, gap
        flow, _ = costs_and_aon(np.zeros(len(paths)))
        gaps = []
        for iteration in range(1, model.config.max_iterations + 1):
            aon, gap = costs_and_aon(flow)
            gaps.append(gap)
            if gap <= model.config.relative_gap_tol:
                break
            flow += (1.0 / (iteration + 1.0)) * (aon - flow)
        actual, logs, _ = assign_msa_active(model)
        np.testing.assert_array_equal(flow, actual)
        np.testing.assert_array_equal(gaps, logs.relative_gap.to_numpy())

    def test_msa_and_capacity_cancel(self):
        with self.assertRaises(TaskCancelled):
            assign_msa_active(fixture(), cancel_check=lambda: True)
        with self.assertRaises(TaskCancelled):
            capacity_filter(fixture(), np.array([500.]), cancel_check=lambda: True)

    def test_preflight_bounds_and_measurements(self):
        result = assignment_preflight(20, {'max_transfers': 1, 'paths_per_transfer': 3}, fixture())
        self.assertEqual(result['upper_bounds']['ordered_od_pairs'], 380)
        self.assertEqual(result['upper_bounds']['candidate_paths'], 2280)
        self.assertEqual(result['measured']['paths'], 1)
        self.assertEqual(result['measured']['path_bundle_nnz'], 2)
        self.assertNotIn('seconds', result)

    def test_content_hash_parameters_and_code(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / 'code.py'
            path.write_text('v1')
            first = content_signature({'demand': 500}, [('code', path)])
            self.assertNotEqual(first, content_signature({'demand': 501}, [('code', path)]))
            path.write_text('v2')
            self.assertNotEqual(first, content_signature({'demand': 500}, [('code', path)]))

    def test_custom_source_entrypoints_and_external_static_cache(self):
        from Back_end.assignment import core, streaming
        with TemporaryDirectory() as temp:
            root = Path(temp)
            data = root/'data'
            processed = data/'processed/custom/network'
            processed.mkdir(parents=True)
            bus = root/'external_bus'
            (bus/'custom').mkdir(parents=True)
            raw = bus/'custom/custom_bus_stops.shp'
            raw.write_bytes(b'fixture')
            poi_dir = root/'external_poi'
            poi_dir.mkdir()
            custom = {'id': 'custom', 'name': 'Fixture', 'poi_format': 'custom_standard_v1',
                'poi_coordinate_system': 'WGS84', 'source_directory_override': {'bus_dir': str(bus), 'poi_dir': str(poi_dir)}}
            with patch.multiple(city_runner, DATA=data, BUS=root/'wrong_bus', POI=root/'wrong_poi', BY_ID={'custom': custom}), \
                 patch.object(city_runner, 'cached_city_network', return_value={'signature': 'n', 'route_directions_ready': 4}) as network_loader, \
                 patch.object(city_runner, 'cached_custom_city_poi', return_value={'signature': 'p', 'poi_count': 2}) as poi_loader, \
                 patch.object(city_runner, 'cached_city_poi') as legacy_loader, \
                 patch.object(city_runner, 'prepare_model_sparse', side_effect=RuntimeError('fixture reached')) as prepare:
                pd.DataFrame({'route_id': ['r']}).to_csv(processed/'bus_headway_template.csv', index=False)
                with self.assertRaisesRegex(RuntimeError, 'fixture reached'):
                    city_runner.run_city('custom', 'pm_peak', 2, 500, assumed_headway_min=15)
                self.assertEqual(network_loader.call_args.args[1], bus)
                self.assertEqual(poi_loader.call_args.args[1], poi_dir)
                legacy_loader.assert_not_called()
                self.assertEqual(prepare.call_args.kwargs['raw_stops'], raw)
            pd.DataFrame({'station_id': ['a', 'b']}).to_csv(processed/'bus_stations_merged.csv', index=False)
            pd.DataFrame({'from_station': ['a'], 'to_station': ['b'], 'geom_len_km_mean': [1.], 'distance_km_mean': [1.]}).to_csv(processed/'bus_edges_topology.csv', index=False)
            (processed/'bus_stopid_to_station.csv').write_text('fixture')
            headways = processed/'headways.csv'
            headways.write_text('fixture')
            with patch.object(streaming, '_static_network', side_effect=RuntimeError('static reached')) as static:
                with self.assertRaisesRegex(RuntimeError, 'static reached'):
                    streaming.prepare_model_sparse(data, 'custom', fixture().config, headways, raw_stops=raw)
                self.assertEqual(static.call_args.args[5], raw)
            with patch.object(core, '_build_routes', return_value=([], pd.DataFrame(), {})), \
                 patch.object(core, '_build_bundles', return_value=({}, {})):
                args = (data, processed, pd.DataFrame(), pd.DataFrame(), fixture().config, raw, headways)
                self.assertFalse(core._static_network(*args)[-1])
                self.assertTrue(core._static_network(*args)[-1])

    def test_run_city_cache_resume_and_invalidation(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            network = root / 'processed/fixture/network'
            network.mkdir(parents=True)
            template = network / 'bus_headway_template.csv'
            pd.DataFrame({'route_id': ['r'], 'route_name': ['fixture'], 'topology_status': ['ready']}).to_csv(template, index=False)
            (network.parent / 'poi_clustered.csv').write_text('poi-v1')
            def prepare(data, city_id, cfg, headways):
                model = fixture()
                model.config = cfg
                return model
            options = dict(city_id='fixture', period='pm_peak', poi_count=2, total_demand=500,
                max_iterations=50, msa_tolerance=1e-12,
                parameters={'max_transfers': 1, 'paths_per_transfer': 3, 'access_station_count': 1})
            with patch.multiple(city_runner, DATA=root, BUS=root/'bus', POI=root/'poi', BY_ID={'fixture': {'id': 'fixture', 'name': 'Fixture'}}), \
                 patch.object(city_runner, 'cached_city_network', return_value={'signature': 'n1', 'route_directions_ready': 4}), \
                 patch.object(city_runner, 'cached_city_poi', return_value={'signature': 'p1', 'poi_count': 2}), \
                 patch.object(city_runner, 'prepare_model_sparse', side_effect=prepare) as mocked:
                stopped = False
                created = []
                def progress(phase):
                    nonlocal stopped
                    if phase == '进行容量再分配':
                        stopped = True
                with self.assertRaises(TaskCancelled) as cancelled:
                    city_runner.run_city(**options, progress=progress, cancel_check=lambda: stopped, checkpoint_dir=root/'external', on_run_created=created.append)
                run = next((root/'runs/fixture').iterdir())
                self.assertEqual(created, [run.name])
                self.assertEqual(cancelled.exception.run_id, run.name)
                self.assertTrue(Path(cancelled.exception.checkpoint_dir).is_dir())
                snapshot = (run/'parameters.json').read_bytes()
                resumed = city_runner.run_city(**options, resume_run_id=run.name, on_run_created=created.append)
                self.assertEqual(resumed['run_id'], run.name)
                self.assertEqual(mocked.call_count, 1)
                self.assertEqual((run/'parameters.json').read_bytes(), snapshot)
                self.assertEqual(resumed['demand_balance_error'], 0)
                cached = city_runner.run_city(**options, on_run_created=created.append)
                self.assertEqual(created, [run.name] * 3)
                self.assertTrue(cached['cache_hit'])
                self.assertEqual(cached['run_id'], run.name)
                self.assertEqual(mocked.call_count, 1)
                changed = city_runner.run_city(**options, default_headway_min=16)
                self.assertNotEqual(changed['run_id'], run.name)
                self.assertEqual(mocked.call_count, 2)
                with self.assertRaises(ValueError):
                    city_runner.run_city(**options, resume_run_id=run.name, default_headway_min=16)
                (root/changed['output']/'route_section_flows.csv').write_text('corrupt')
                repaired = city_runner.run_city(**options, default_headway_min=16)
                self.assertFalse(repaired['cache_hit'])
                self.assertEqual(mocked.call_count, 3)
                self.assertNotEqual(repaired['run_id'], changed['run_id'])
                (network.parent/'poi_clustered.csv').write_text('poi-v2')
                changed_poi = city_runner.run_city(**options)
                self.assertFalse(changed_poi['cache_hit'])
                self.assertEqual(mocked.call_count, 4)
                baseline_model = fixture()
                baseline_model.config = ExperimentConfig(**resumed['parameters'])
                expected, expected_logs, _ = assign_msa_kpaths_optimized(baseline_model)
                pd.testing.assert_frame_equal(pd.read_csv(run/'assignment_iterations.csv'), expected_logs, check_exact=False, rtol=1e-14)
                np.testing.assert_allclose(pd.read_csv(run/'route_section_flows.csv').desired_flow_pax_per_hour,
                    baseline_model.path_bundle.T @ expected, rtol=1e-14)


if __name__ == '__main__':
    unittest.main(verbosity=2)
