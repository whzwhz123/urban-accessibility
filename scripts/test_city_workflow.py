"""Small reproducible checks; synthetic fixtures stay outside project results."""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

import numpy as np
import pandas as pd
from pyproj import Transformer
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from Back_end.city_demand import build, defaults, importance
from Back_end.city_workflow import coverage, competitiveness, normalize, opportunity
from Back_end.assignment.core import ExperimentConfig, poi_marginals
from Back_end.assignment.streaming import _sample_od_pairs, _stream_sample_od_pairs
from scipy.sparse import coo_matrix

class WorkflowTests(unittest.TestCase):
    def test_demand_weights_and_cache(self):
        city={'id':'Fixture','name':'测试城市','center':[116,39]}
        with TemporaryDirectory() as temporary:
            root=Path(temporary);folder=root/'processed/Fixture';folder.mkdir(parents=True)
            project=Transformer.from_crs(4326,32650,always_xy=True)
            x,y=project.transform([116,116.02,116.04],[39,39,39])
            pd.DataFrame({'x_m':x,'y_m':y,'class_1':['医疗保健服务','商务住宅','公司企业']}).to_csv(folder/'poi_clustered.csv',index=False)
            settings=defaults(city);settings['smooth_r']=0
            first=build(city,root,settings);again=build(city,root,settings)
            self.assertTrue(again['cache_hit']);self.assertEqual(first['run_id'],again['run_id'])
            a=json.loads((root/first['output']/'demand_grid.geojson').read_text(encoding='utf-8'))
            settings['poi_weights']['医疗保健']=6
            second=build(city,root,settings)
            b=json.loads((root/second['output']/'demand_grid.geojson').read_text(encoding='utf-8'))
            self.assertNotEqual(first['run_id'],second['run_id'])
            self.assertAlmostEqual(sum(f['properties']['poi_weighted'] for f in b['features'])-sum(f['properties']['poi_weighted'] for f in a['features']),3)
            self.assertAlmostEqual(importance(pd.Series(['医疗保健服务']),settings['poi_weights'])[0],6)

    def test_od_weights_and_conservation(self):
        poi=pd.DataFrame({'class_1':['公司企业','商务住宅','医疗保健服务'],'poi_count':[1,1,1],'cluster_id':['a','b','c']})
        cfg=ExperimentConfig(period='pm_peak',total_demand_pax_per_hour=100,poi_category_weights={'公司企业':2,'商务住宅':1,'医疗保健':0})
        production,attraction=poi_marginals(poi,cfg)
        self.assertEqual(production[0],70);self.assertEqual(production[2],0);self.assertEqual(attraction[2],0)
        o=np.array([0,0,1,1,2,2]);d=np.array([1,2,0,2,0,1])
        result=_sample_od_pairs(poi,o,d,np.ones(6),np.ones(6)*10,cfg)
        # Returned sampled supports must conserve demand and omit zero-attraction destinations.
        self.assertAlmostEqual(result[3].sum(),100)
        self.assertFalse((result[1]==2).any())
        poi['x_m']=[0,2000,4000];poi['y_m']=0
        access=pd.DataFrame({'poi_idx':[0,1,2],'access_rank':[1,1,1],'station_idx':[0,1,2],'walk_time_min':[0,0,0]})
        graph=coo_matrix((np.ones(6)*10,(np.array([6,6,7,7,8,8]),np.array([4,5,3,5,3,4]))),shape=(9,9)).tocsr()
        stream_cfg=ExperimentConfig(period='pm_peak',access_station_count=1,max_transfers=0,total_demand_pax_per_hour=100,poi_category_weights=cfg.poi_category_weights)
        streamed=_stream_sample_od_pairs(graph,poi,access,3,stream_cfg)
        self.assertAlmostEqual(streamed[3].sum(),100)
        self.assertFalse((streamed[1]==2).any())

    def test_tci_parameters_and_missing(self):
        grids=[{'type':'Feature','geometry':{'type':'Polygon','coordinates':[[[0,0],[1,0],[1,1],[0,1],[0,0]]]},'properties':{'grid_id':'a','cx':.5,'cy':.5}}]
        row={'origin_wgs84':[.5,.5],'session':'evening','bus_time':20,'car_time':10,'transfer':1}
        a,_=competitiveness(grids,[row],{'alpha':0,'bus_max':70,'car_max':30})
        b,_=competitiveness(grids,[row],{'alpha':5,'bus_max':70,'car_max':30})
        self.assertEqual(a[0]['properties']['tci_evening'],.5);self.assertEqual(b[0]['properties']['tci_evening'],.4)
        self.assertIsNone(b[0]['properties']['tci_morning'])
        empty,stats=competitiveness(grids,[row],{'alpha':5,'bus_max':10,'car_max':30})
        self.assertEqual(empty,[]);self.assertEqual(stats['excluded_records'],1)

    def test_population_crs_and_blending(self):
        import rasterio
        from rasterio.transform import from_origin
        city={'id':'Fixture','name':'测试城市','center':[116,39]}
        with TemporaryDirectory() as temporary:
            root=Path(temporary);folder=root/'processed/Fixture';folder.mkdir(parents=True)
            x,y=Transformer.from_crs(4326,32650,always_xy=True).transform([116,116.02],[39,39])
            pd.DataFrame({'x_m':x,'y_m':y,'class_1':['商务住宅','公司企业']}).to_csv(folder/'poi_clustered.csv',index=False)
            pop=root/'population/Fixture';pop.mkdir(parents=True)
            with rasterio.open(pop/'valid.tif','w',driver='GTiff',height=100,width=100,count=1,dtype='float32',crs='EPSG:32650',transform=from_origin(x[0]-10000,y[0]+10000,200,200)) as raster:
                raster.write(np.arange(10000,dtype=np.float32).reshape(100,100),1)
            settings=defaults(city);settings.update(smooth_r=0,kde_weight=.5)
            result=build(city,root,settings)
            self.assertGreater(result['population_known_grids'],0)
            self.assertEqual(result['parameters']['kde_weight'],.5)

    def test_opportunity_sigma_and_horizon(self):
        geometry={'type':'Polygon','coordinates':[[[115.99,38.99],[116.01,38.99],[116.01,39.01],[115.99,39.01],[115.99,38.99]]]}
        grids=[{'type':'Feature','geometry':geometry,'properties':{'grid_id':'a','cx':116,'cy':39}}]
        polygons=[{'type':'Feature','geometry':geometry,'properties':{'minute':45,'origin_wgs84':[116,39]}}]
        x,y=Transformer.from_crs(4326,32650,always_xy=True).transform([116.005,116.006],[39,39])
        poi=pd.DataFrame({'x_m':x,'y_m':y,'class_1':['商务住宅','公司企业']})
        low,_,_=opportunity(grids,polygons,poi,32650,{'商务住宅':1,'公司企业':1},{'sigma':5,'max_time':45,'method':'legacy_distance'},lambda value:None)
        high,_,_=opportunity(grids,polygons,poi,32650,{'商务住宅':1,'公司企业':1},{'sigma':30,'max_time':45,'method':'legacy_distance'},lambda value:None)
        self.assertLess(low[0]['properties']['iso_score_raw'],high[0]['properties']['iso_score_raw'])
        missing,_,_=opportunity(grids,polygons,poi,32650,{}, {'sigma':15,'max_time':30},lambda value:None)
        self.assertEqual(missing,[])

    def test_coverage_and_invalid_parameters(self):
        grids=[{'type':'Feature','geometry':None,'properties':{'cx':116,'cy':39}}]
        x,y=Transformer.from_crs(4326,32650,always_xy=True).transform(116,39)
        stations=pd.DataFrame({'x_m':[x+500],'y_m':[y],'route_ids':['a|b']})
        a=coverage(grids,stations,32650,{'walk_full':300,'walk_zero':800,'walk_weight':1,'diversity_r':600})
        b=coverage(grids,stations,32650,{'walk_full':600,'walk_zero':800,'walk_weight':1,'diversity_r':600})
        self.assertAlmostEqual(a[0]['properties']['coverage_score'],60);self.assertEqual(b[0]['properties']['coverage_score'],100)
        with self.assertRaises(ValueError):normalize({'coverage':{'walk_full':900,'walk_zero':800}})

if __name__=='__main__':unittest.main()
