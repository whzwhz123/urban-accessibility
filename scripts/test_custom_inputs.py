"""Offline fixtures only; no HTTP server and no external API calls."""
import io
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
import zipfile

import geopandas as gpd
import pandas as pd
from pyproj import Transformer
from shapely.ops import transform

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from Back_end.custom_inputs import (
    build_template_package, import_package, package_schema, source_directory_override,
    prepare_custom_city_poi, cached_custom_city_poi, _gcj,
)
from Back_end.city_network import prepare_city_network, cached_city_network
from Back_end.assignment.core import _build_routes, ExperimentConfig

# Use a catalog city ID, not a new selectable city. All records are EXAMPLE fixtures.
CITY = {'id':'Beijing', 'name':'北京市', 'province':'北京市', 'city_code':'010',
        'center':[116.4,39.9], 'poi_available':False, 'poi_file':None}


def rewrite_package(content, updates):
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(content)) as source, zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            target.writestr(info.filename, updates.get(info.filename, source.read(info.filename)))
    return output.getvalue()


class CustomInputsTests(unittest.TestCase):
    def import_fixture(self, root, content=None, declaration='WGS84'):
        return import_package(CITY, root, content or build_template_package(), 'fixture.zip', declaration)

    def test_network_poi_and_computable_routes(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = self.import_fixture(root)
            city = result['city']
            sources = source_directory_override(city, root/'national', root/'national_poi')
            network = prepare_city_network(city, sources['bus_dir'], root/'processed')
            self.assertEqual(network['route_directions_ready'], 1)
            self.assertEqual(network['topology_edges'], 2)
            self.assertIsNotNone(cached_city_network(city, sources['bus_dir'], root/'processed'))
            poi = prepare_custom_city_poi(city, sources['poi_dir'], root/'processed')
            self.assertEqual(poi['poi_count'], 2)
            clustered = pd.read_csv(root/'processed/Beijing/poi_clustered.csv')
            self.assertAlmostEqual(clustered.lon.iloc[0], 116.401, places=8)
            self.assertIsNotNone(cached_custom_city_poi(city, sources['poi_dir'], root/'processed'))
            folder = root/'processed/Beijing/network'
            stations = pd.read_csv(folder/'bus_stations_merged.csv')
            topology = pd.read_csv(folder/'bus_edges_topology.csv')
            topology['distance_km'] = topology.geom_len_km_mean.where(topology.geom_len_km_mean > 0, topology.distance_km_mean)
            topology['edge_idx'] = range(len(topology))
            headways = root/'headways.csv'
            pd.DataFrame({'route_id':['EXAMPLE_R1'], 'peak_headway_min':[10],
                          'peak_headway_source':['test_fixture']}).to_csv(headways,index=False)
            routes, sections, _ = _build_routes(
                next((sources['bus_dir']/city['id']).glob('*_bus_stops.shp')),
                folder/'bus_stopid_to_station.csv', stations, topology, headways, None,
                ExperimentConfig(period='am_peak'))
            self.assertEqual(len(routes), 1)
            self.assertEqual(len(sections), 2)
            manifest = json.loads(Path(result['manifest']).read_text(encoding='utf-8'))
            self.assertEqual(manifest['status'], 'completed')
            self.assertEqual(len(manifest['source_sha256']), 64)
            self.assertIn('stops.geojson', manifest['source_files'])

    def test_local_linyi_real_geometry_subset(self):
        national = Path(__file__).resolve().parents[1]/'data/CPTOND-2025/dataset/bus/shapefiles/Linyi'
        if not national.is_dir():
            self.skipTest('本地没有临沂全国源数据')
        stops = gpd.read_file(next(national.glob('*_bus_stops.shp')), rows=300)
        part = next(group.sort_values('sequence').head(3) for _,group in stops.groupby('route_id') if len(group)>=3)
        route_cn = str(part.route_cn.iloc[0]).replace("'", "''")
        route_en = str(part.route_en.iloc[0]).replace("'", "''")
        routes = gpd.read_file(next(national.glob('*_bus_routes.shp')),
                               where=f"route_cn = '{route_cn}' AND route_en = '{route_en}'")
        ordered = part.stop_id.astype(str).tolist()
        pairs = set(zip(ordered, ordered[1:]))
        ids = ','.join("'"+str(value).replace("'", "''")+"'" for value in ordered)
        segments = gpd.read_file(next(national.glob('*_bus_segments.shp')),
                                 where=f's_stopid IN ({ids}) AND e_stopid IN ({ids})')
        segments = segments.loc[[pair in pairs for pair in zip(segments.s_stopid.astype(str),segments.e_stopid.astype(str))]]
        self.assertFalse(routes.empty)
        self.assertEqual(set(zip(segments.s_stopid.astype(str),segments.e_stopid.astype(str))),pairs)
        city = {'id':'Linyi','name':'临沂市','province':'山东省','city_code':'0539','center':[118.35,35.1]}
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = root/'fixture'
            fixture.mkdir()
            for kind,frame in [('stops',part),('segments',segments),('routes',routes)]:
                frame.to_file(fixture/f'subset_bus_{kind}.shp',encoding='UTF-8',index=False)
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as archive:
                for path in fixture.iterdir():
                    archive.write(path,path.name)
                # POI fixture is explicitly marked; no synthetic study result is produced.
                archive.writestr('poi.csv',f'id,name,class_1,lon,lat\nEXAMPLE_ONLY,示例非研究数据,其他,{part.geometry.x.iloc[0]},{part.geometry.y.iloc[0]}\n')
            result = import_package(city,root,buffer.getvalue(),'linyi_subset.zip','WGS84')
            network = prepare_city_network(result['city'],Path(result['source_overrides']['bus_dir']),root/'processed')
            self.assertEqual(network['route_directions_ready'],1)
            self.assertEqual(network['topology_edges'],2)
            imported = gpd.read_file(next((Path(result['source_overrides']['bus_dir'])/'Linyi').glob('*_bus_segments.shp')))
            self.assertTrue(imported.geometry.iloc[0].equals(segments.geometry.iloc[0]))
    def test_versions_and_cache_invalidation(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = self.import_fixture(root)
            second = self.import_fixture(root)
            self.assertNotEqual(first['version'],second['version'])
            self.assertTrue(Path(first['manifest']).is_file())
            prepare_custom_city_poi(first['city'], first['source_overrides']['poi_dir'], root/'processed')
            self.assertIsNone(cached_custom_city_poi(second['city'], second['source_overrides']['poi_dir'], root/'processed'))
            self.assertFalse((root/'CPTOND-2025').exists())
            self.assertEqual(CITY['poi_available'], False)

    def test_projected_geojson_and_poi(self):
        content = build_template_package()
        project = Transformer.from_crs(4326,32650,always_xy=True).transform
        updates = {}
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            for kind in ('stops','segments','routes'):
                document = json.loads(archive.read(kind+'.geojson'))
                from shapely.geometry import shape, mapping
                for feature in document['features']:
                    feature['geometry'] = mapping(transform(project,shape(feature['geometry'])))
                updates[kind+'.geojson'] = json.dumps(document)
            x,y = project(116.401,39.9)
            updates['poi.csv'] = f'id,name,class_1,lon,lat\nEXAMPLE_P1,EXAMPLE_ONLY,other,{x},{y}\n'
        with TemporaryDirectory() as temporary:
            result = self.import_fixture(temporary,rewrite_package(content,updates),'EPSG:32650')
            stops = gpd.read_file(next((Path(result['source_overrides']['bus_dir'])/'Beijing').glob('*_bus_stops.shp')))
            self.assertAlmostEqual(stops.geometry.x.iloc[0],116.4,places=8)
            poi = pd.read_csv(Path(result['source_overrides']['poi_dir'])/'poi_wgs84.csv')
            self.assertAlmostEqual(poi.lon.iloc[0],116.401,places=8)

    def test_gcj_conversion_applies_to_all_vertices_once(self):
        with TemporaryDirectory() as temporary:
            result = self.import_fixture(temporary,declaration='GCJ02')
            sources = result['source_overrides']
            expected = _gcj(116.4,39.9)
            stops = gpd.read_file(next((Path(sources['bus_dir'])/'Beijing').glob('*_bus_stops.shp')))
            self.assertAlmostEqual(stops.geometry.x.iloc[0],expected[0],places=8)
            segments = gpd.read_file(next((Path(sources['bus_dir'])/'Beijing').glob('*_bus_segments.shp')))
            self.assertAlmostEqual(segments.geometry.iloc[0].coords[1][0],_gcj(116.4025,39.9001)[0],places=8)
            prepare_custom_city_poi(result['city'], sources['poi_dir'], Path(temporary)/'processed')
            poi = pd.read_csv(Path(temporary)/'processed/Beijing/poi_clustered.csv')
            self.assertAlmostEqual(poi.lon.iloc[0],_gcj(116.401,39.9)[0],places=8)
            self.assertEqual(_gcj(0,0),(0,0))

    def test_cptond_shapefile_without_route_id(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = root/'fixture'
            fixture.mkdir()
            buffer = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(build_template_package())) as archive:
                for kind in ('stops','segments','routes'):
                    path = fixture/(kind+'.geojson')
                    path.write_bytes(archive.read(kind+'.geojson'))
                    frame = gpd.read_file(path)
                    if kind == 'routes':
                        frame = frame.drop(columns='route_id')
                    frame.to_file(fixture/f'fixture_bus_{kind}.shp',encoding='UTF-8', index=False)
                with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as output:
                    for path in fixture.glob('*'):
                        if path.suffix != '.geojson':
                            output.write(path,path.name)
                    output.writestr('poi.csv',archive.read('poi.csv'))
            result = self.import_fixture(root,buffer.getvalue())
            network = prepare_city_network(result['city'],Path(result['source_overrides']['bus_dir']),root/'processed')
            self.assertEqual(network['route_directions_ready'],1)

    def test_missing_fields_and_sequence(self):
        content = build_template_package()
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            document = json.loads(archive.read('stops.geojson'))
        for feature in document['features']:
            feature['properties'].pop('sequence')
        with TemporaryDirectory() as temporary, self.assertRaisesRegex(ValueError,'缺少必需字段.*sequence'):
            self.import_fixture(temporary,rewrite_package(content,{'stops.geojson':json.dumps(document)}))
        for feature in document['features']:
            feature['properties']['sequence'] = 1
        with TemporaryDirectory() as temporary, self.assertRaisesRegex(ValueError,'sequence重复'):
            self.import_fixture(temporary,rewrite_package(content,{'stops.geojson':json.dumps(document)}))

    def test_missing_physical_edge(self):
        content = build_template_package()
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            document = json.loads(archive.read('segments.geojson'))
        document['features'].pop()
        with TemporaryDirectory() as temporary, self.assertRaisesRegex(ValueError,'缺少相邻站'):
            self.import_fixture(temporary,rewrite_package(content,{'segments.geojson':json.dumps(document)}))

    def test_zip_security_and_limits(self):
        for name in ('../outside.txt','/outside.txt','C:/outside.txt','a/../../outside.txt','..\\outside.txt','NUL.txt'):
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer,'w') as archive:
                archive.writestr(name,'unsafe')
            with TemporaryDirectory() as temporary, self.assertRaisesRegex(ValueError,'不安全'):
                self.import_fixture(temporary,buffer.getvalue())
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer,'w') as archive:
            for index in range(201):
                archive.writestr(f'{index}.txt','')
        with TemporaryDirectory() as temporary, self.assertRaisesRegex(ValueError,'超过200'):
            self.import_fixture(temporary,buffer.getvalue())
        from unittest.mock import patch
        with TemporaryDirectory() as temporary, patch('Back_end.custom_inputs.MAX_EXPANDED_BYTES',10), self.assertRaisesRegex(ValueError,'解压大小'):
            self.import_fixture(temporary)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer,'w') as archive:
            info = zipfile.ZipInfo('link')
            info.create_system = 3
            info.external_attr = 0o120777 << 16
            archive.writestr(info,'../outside')
        with TemporaryDirectory() as temporary, self.assertRaisesRegex(ValueError,'符号链接'):
            self.import_fixture(temporary,buffer.getvalue())

    def test_invalid_poi_and_crs(self):
        content = build_template_package()
        for csv, message in [('id,name,class_1,lon,lat\na,fixture,other,NaN,39\n','有限数值'),
                             ('id,name,class_1,lon,lat\na,fixture,other,116,39\na,fixture,other,116,39\n','id必须唯一')]:
            with TemporaryDirectory() as temporary, self.assertRaisesRegex(ValueError,message):
                self.import_fixture(temporary,rewrite_package(content,{'poi.csv':csv}))
        with TemporaryDirectory() as temporary, self.assertRaisesRegex(ValueError,'必须声明'):
            self.import_fixture(temporary,declaration='')
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            document = json.loads(archive.read('stops.geojson'))
        document['crs'] = {'type':'name','properties':{'name':'EPSG:4326'}}
        with TemporaryDirectory() as temporary, self.assertRaisesRegex(ValueError,'CRS.*不一致'):
            self.import_fixture(temporary,rewrite_package(content,{'stops.geojson':json.dumps(document)}),declaration='EPSG:32650')

    def test_mixed_coordinate_declarations(self):
        with TemporaryDirectory() as temporary:
            result = self.import_fixture(temporary,declaration={'stops':'WGS84','segments':'WGS84','routes':'WGS84','poi':'GCJ02'})
            poi = pd.read_csv(Path(result['source_overrides']['poi_dir'])/'poi_wgs84.csv')
            self.assertAlmostEqual(poi.lon.iloc[0],_gcj(116.401,39.9)[0],places=8)
            self.assertEqual(package_schema()['stops']['geometry'],'Point')


if __name__ == '__main__':
    unittest.main(verbosity=2)


