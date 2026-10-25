import sys,json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import pandas as pd
from pyproj import Transformer
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from Back_end.city_workflow import opportunity
from Back_end.collection_store import create,append,finish,read
from Back_end.sample_analysis import navigation_stability

class NewWorkflowTests(unittest.TestCase):
    def test_no_time_proxy_and_band_bounds(self):
        g={'type':'Polygon','coordinates':[[[115.99,38.99],[116.01,38.99],[116.01,39.01],[115.99,39.01],[115.99,38.99]]]}
        grids=[{'type':'Feature','geometry':g,'properties':{'grid_id':'a','cx':116,'cy':39}}]
        x,y=Transformer.from_crs(4326,32650,always_xy=True).transform([116.001],[39.001])
        poi=pd.DataFrame({'x_m':x,'y_m':y,'class_1':['商务住宅']})
        polygons=[{'type':'Feature','geometry':g,'properties':{'minute':t,'origin_wgs84':[116,39]}} for t in [15,30,45]]
        a,_,_=opportunity(grids,polygons,poi,32650,{'商务住宅':2},{'max_time':45,'sigma':1,'method':'cumulative'},lambda s:None)
        b,_,_=opportunity(grids,polygons,poi,32650,{'商务住宅':2},{'max_time':45,'sigma':30,'method':'cumulative'},lambda s:None)
        self.assertEqual(a[0]['properties']['iso_score_raw'],2);self.assertEqual(a,b)
        band,_,_=opportunity(grids,polygons,poi,32650,{'商务住宅':2},{'max_time':45,'sigma':15,'method':'time_bands'},lambda s:None)
        self.assertAlmostEqual(band[0]['properties']['iso_score_raw'],2*__import__('math').exp(-.5));self.assertEqual(band[0]['properties']['iso_score_upper_bound'],2)
        absent,_,stats=opportunity(grids,polygons[1:],poi,32650,{}, {'max_time':45,'sigma':15,'method':'time_bands'},lambda s:None)
        self.assertEqual(absent,[]);self.assertEqual(stats['incomplete_time_band_origins'],1)

    def test_durable_idempotent_batches_and_failures(self):
        with TemporaryDirectory() as directory:
            plan={'city_id':'Fixture','demand_run_id':'demand','kind':'tci','points':[{},{}],'minutes':[45],'sampling':'test','subset_only':True}
            job=create(directory,plan);identity=job['id'];row={'pair_id':'a','grid_id':'origin','status':'ok','bus_time':20,'car_time':10,'transfer':0}
            payload={'metadata':{'city_id':'Fixture','demand_run_id':'demand'},'records':[row]}
            append(directory,'Fixture',identity,payload);append(directory,'Fixture',identity,payload)
            self.assertEqual(len(list((Path(directory)/'collection_runs/Fixture'/identity/'chunks').glob('*'))),1)
            self.assertEqual(read(directory,'Fixture',identity)['completed_keys'],['a'])
            payload['records']=[{'pair_id':'b','status':'no_route'}];append(directory,'Fixture',identity,payload)
            result=finish(directory,'Fixture',identity);self.assertEqual(result['state']['status'],'completed');self.assertEqual(result['valid_records'],1)
            with self.assertRaises(ValueError):append(directory,'Fixture',identity,{'metadata':{'city_id':'Other'},'records':[]})
            failure=create(directory,plan);payload['records']=[{'pair_id':'a','status':'api_error'}];append(directory,'Fixture',failure['id'],payload)
            self.assertIsNone(finish(directory,'Fixture',failure['id'])['file'])

    def test_stability_is_diagnostic(self):
        rows=[{'grid_id':str(i),'bus_time':20+j,'car_time':10+i,'transfer':0} for i in range(3) for j in range(3)]
        result=navigation_stability(rows,{'bus_max':70,'car_max':30,'alpha':2})
        self.assertEqual(len(result['levels']),3);self.assertEqual(result['valid_records'],9);self.assertTrue(result['limitations'])

if __name__=='__main__':unittest.main()
