"""Offline parameter provenance and custom-data pipeline verification."""
import io
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
import zipfile

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from Back_end.run_history import recorded_run
from Back_end.custom_inputs import build_template_package, import_package, prepare_custom_city_poi
from Back_end.city_network import prepare_city_network
from Back_end.city_service import import_city_service
from Back_end.city_demand import build, defaults
from Back_end.city_workflow import compute
from Back_end.artifact_exports import package
from Back_end.assignment.parameters import DEFAULT_PARAMETERS
import Back_end.assignment.run_city as runner


class ProvenanceTests(unittest.TestCase):
    def test_partial_history_uses_only_recorded_values(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'summary.json').write_text(json.dumps({'parameters': {'bus_speed_kmh': 16}, 'converged': 'no'}))
            (root/'parameters.json').write_text(json.dumps({'bus_speed_kmh': 18, 'sample_size': 2, 'max_transfers': 0, 'headway_basis': 'user_supplied_csv'}))
            value = recorded_run(root)
            self.assertEqual(value['parameters']['bus_speed_kmh'], 16)
            self.assertEqual(value['parameters']['max_transfers'], 0)
            self.assertNotIn('vehicle_capacity', value['parameters'])
            self.assertEqual(value['parameter_sources']['bus_speed_kmh'], 'summary.json')
            self.assertEqual(value['parameter_sources']['max_transfers'], 'parameters.json')
            self.assertEqual(value['poi_count'], 2)
            self.assertEqual(value['headway_basis'],'user_supplied_csv')
            self.assertNotIn('headway_basis',value['parameters'])

    def test_no_snapshot_has_no_invented_defaults(self):
        with TemporaryDirectory() as temp:
            root = Path(temp); (root/'summary.json').write_text('{}')
            self.assertEqual(recorded_run(root)['parameters'], {})

    def test_custom_upload_demand_assignment_evaluation_export(self):
        city = {'id': 'Beijing', 'name': '北京市', 'province': '北京市', 'city_code': '010', 'center': [116.4,39.9]}
        original = build_template_package(); buffer = io.BytesIO()
        # Stretch the explicitly labelled example so its OD distance exceeds 1 km.
        with zipfile.ZipFile(io.BytesIO(original)) as source, zipfile.ZipFile(buffer, 'w') as output:
            for name in source.namelist():
                content = source.read(name)
                if name.endswith('.geojson'):
                    data = json.loads(content)
                    def stretch(coords):
                        if isinstance(coords[0], (float, int)):
                            return [116.4 + (coords[0]-116.4)*3, coords[1]]
                        return [stretch(item) for item in coords]
                    for feature in data['features']:
                        feature['geometry']['coordinates'] = stretch(feature['geometry']['coordinates'])
                    content = json.dumps(data).encode()
                elif name == 'poi.csv':
                    content = 'id,name,class_1,lon,lat\nEXAMPLE_P1,example_only,公司企业,116.401,39.9\nEXAMPLE_P2,example_only,商务住宅,116.429,39.9\n'.encode('utf-8')
                output.writestr(name, content)
        with TemporaryDirectory() as temp:
            root = Path(temp)
            imported = import_package(city, root, buffer.getvalue(), 'EXAMPLE_ONLY.zip', 'WGS84')
            city = imported['city']; sources = imported['source_overrides']
            network = prepare_city_network(city, Path(sources['bus_dir']), root/'processed')
            prepare_custom_city_poi(city, sources['poi_dir'], root/'processed')
            template = pd.read_csv(root/'processed/Beijing/network/bus_headway_template.csv')
            headways = template[['route_id']].copy();headways['peak_headway_min']=8;headways['flat_headway_min']=12
            import_city_service(city, Path(sources['bus_dir']), root/'processed', headways.to_csv(index=False))
            settings = defaults(city);settings['smooth_r']=0;settings['poi_weights']['公司企业']=4
            shared = build(city, root, settings)
            custom = dict(DEFAULT_PARAMETERS, paths_per_transfer=2, access_station_count=2,
                max_walk_distance_m=700, max_od_distance_km=12, max_transfers=1,
                transfer_penalty_min=3, bus_speed_kmh=16, vehicle_capacity=65,
                max_iterations=40, relative_gap_tol=1e-3)
            prepared = []
            actual_prepare = runner.prepare_model_sparse
            def capture(*args, **kwargs):
                model = actual_prepare(*args, **kwargs); prepared.append(model); return model
            with patch.multiple(runner, DATA=root, BUS=Path(sources['bus_dir']), POI=Path(sources['poi_dir']), BY_ID={'Beijing':city}), patch.object(runner, 'prepare_model_sparse', side_effect=capture):
                result = runner.run_city('Beijing','pm_peak',2,120,parameters=custom,demand_run_id=shared['run_id'],default_headway_min=17)
                cached = runner.run_city('Beijing','pm_peak',2,120,parameters=custom,demand_run_id=shared['run_id'],default_headway_min=17)
            self.assertTrue(cached['cache_hit'])
            self.assertEqual(len(prepared),1)
            model = prepared[0]
            self.assertEqual(model.config.fallback_headway_min,17)
            self.assertTrue((model.access.walk_distance_m <= 700).all())
            self.assertTrue((model.access.groupby('poi_idx').size() <= 2).all())
            self.assertTrue((model.paths.transfers <= 1).all())
            self.assertTrue((model.paths.path_rank_within_transfer <= 2).all())
            self.assertTrue((model.route_segments.headway_min == 8).all())
            self.assertEqual(model.config.bus_speed_kmh,16)
            self.assertEqual(model.config.transfer_penalty_min,3)
            self.assertEqual(result['parameters']['poi_category_weights'], settings['poi_weights'])
            self.assertEqual(result['parameters']['od_period_weights'], settings['od_period_weights'])
            for key,value in custom.items():self.assertEqual(result['parameters'][key],value)
            saved = json.loads((root/result['output']/'parameters.json').read_text(encoding='utf-8'))
            for key,value in custom.items():self.assertEqual(saved[key],value)
            flows = pd.read_csv(root/result['output']/'route_section_flows.csv')
            self.assertTrue((flows.capacity_per_hour == 60/8*65).all())
            self.assertAlmostEqual(result['served_demand_pax_per_hour']+result['unserved_demand_pax_per_hour'],120)
            evaluated = compute(city,root,shared['run_id'],{'coverage':{'walk_full':200,'walk_zero':700}})
            self.assertEqual(evaluated['workflow_parameters']['coverage']['walk_full'],200)
            self.assertFalse(evaluated['composite_ready'])
            self.assertNotIn('isochrone',evaluated['available_layers'])
            with zipfile.ZipFile(package(root,'Beijing',result['run_id'])) as archive:
                self.assertIsNone(archive.testzip())
                exported = json.loads(archive.read('results/parameters.json'))
                self.assertEqual(exported['bus_speed_kmh'],16)
                self.assertIn('shared_demand/parameters.json',archive.namelist())


if __name__ == '__main__': unittest.main()
