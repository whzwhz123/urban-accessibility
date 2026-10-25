import csv
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from Back_end.city_service import resolve_headways, import_city_service
from Back_end.city_workflow import sources, competitiveness, input_versions

class CollectionTests(unittest.TestCase):
    def test_partial_headways_and_frequency(self):
        with TemporaryDirectory() as temporary:
            root=Path(temporary);network=root/'Fixture/network';network.mkdir(parents=True)
            (network/'bus_headway_template.csv').write_text('route_id,route_name,topology_status\na,A,ready\nb,B,ready\n',encoding='utf-8')
            with patch('Back_end.city_service.cached_city_network',return_value={'signature':'test'}):
                result=import_city_service({'id':'Fixture','name':'测试'},root,root,'route_id,peak_frequency_per_hour,flat_frequency_per_hour\na,6,3\n')
            output=root/'run';output.mkdir()
            path,summary=resolve_headways('Fixture',root,output,15,'pm_peak')
            with path.open(encoding='utf-8-sig') as stream:rows=list(csv.DictReader(stream))
            self.assertEqual(float(rows[0]['peak_headway_min']),10)
            self.assertEqual(float(rows[0]['flat_headway_min']),20)
            self.assertEqual(float(rows[1]['peak_headway_min']),15)
            self.assertEqual(summary['headway_default_share'],.5)
            self.assertEqual(summary['headway_basis'],'user_csv_with_default_fallback')
            self.assertTrue((root/'Fixture/service/uploads'/(result['upload_id']+'.csv')).is_file())
            _,again=resolve_headways('Fixture',root,output,30,'offpeak')
            self.assertEqual(again['headway_default_share'],.5)
            with self.assertRaises(ValueError):resolve_headways('Fixture',root,output,0)

    def test_city_demand_isolation(self):
        with TemporaryDirectory() as temporary:
            root=Path(temporary);folder=root/'evaluation_inputs/Fixture';folder.mkdir(parents=True)
            for index,demand in enumerate(['a','b']):
                (folder/f'tci_{index}.json').write_text(json.dumps({'metadata':{'demand_run_id':demand},'records':[]}))
            self.assertEqual(sources(root,'Fixture','a')['tci'].name,'tci_0.json')
            self.assertIsNone(sources(root,'Fixture','c')['tci'])
            self.assertIsNone(sources(root,'Beijing')['isochrone'])

    def test_horizon_matching_and_explicit_versions(self):
        with TemporaryDirectory() as temporary:
            root=Path(temporary);folder=root/'evaluation_inputs/Fixture';folder.mkdir(parents=True)
            for name,minutes,demand in [('isochrone_a.geojson',45,'x'),('isochrone_b.geojson',30,'x'),('isochrone_c.geojson',45,'y')]:
                value={'type':'FeatureCollection','metadata':{'city_id':'Fixture','demand_run_id':demand,'minutes':[minutes]},'features':[]}
                (folder/name).write_text(json.dumps(value))
            self.assertEqual(sources(root,'Fixture','x',45)['isochrone'].name,'isochrone_a.geojson')
            self.assertEqual(sources(root,'Fixture','x',30)['isochrone'].name,'isochrone_b.geojson')
            self.assertEqual(len(input_versions(root,'Fixture','x',45)['isochrone']),1)
            self.assertIsNone(sources(root,'Fixture','x',45,{'isochrone':None})['isochrone'])
            with self.assertRaises(ValueError):sources(root,'Fixture','x',45,{'isochrone':'isochrone_b.geojson'})
            with self.assertRaises(ValueError):sources(root,'Fixture','x',45,{'isochrone':'../isochrone_a.geojson'})
            with self.assertRaises(ValueError):sources(root,'Fixture','x',45,{'isochrone':'isochrone_c.geojson'})

    def test_snapshot_has_visible_score(self):
        grid={'type':'Feature','geometry':{'type':'Polygon','coordinates':[[[115,38],[117,38],[117,40],[115,40],[115,38]]]},'properties':{'grid_id':'a'}}
        rows=[{'origin_wgs84':[116,39],'session':'snapshot','bus_time':30,'car_time':15,'transfer':1}]
        output,stats=competitiveness([grid],rows,{'alpha':2,'bus_max':70,'car_max':30})
        self.assertAlmostEqual(output[0]['properties']['tci_snapshot'],15/32)
        self.assertEqual(output[0]['properties']['tci_snapshot_norm'],50)
        self.assertIsNone(output[0]['properties']['tci_morning'])
        self.assertEqual(stats['valid_records'],1)

if __name__=='__main__':unittest.main()
