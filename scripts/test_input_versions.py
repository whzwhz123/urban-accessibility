"""Input version switching uses example fixtures only, never production data."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from Back_end.custom_inputs import import_package,build_template_package
from Back_end.input_versions import activate,list_versions

CITY={'id':'Beijing','name':'北京市','province':'北京市','city_code':'010','center':[116.4,39.9],'has_evaluation':True}
class InputVersionTests(unittest.TestCase):
    def test_switch_and_return_to_original_preserves_all_data(self):
        with TemporaryDirectory() as temporary:
            root=Path(temporary)
            a=import_package(CITY,root,build_template_package(),'a.zip','WGS84')
            b=import_package(CITY,root,build_template_package(),'b.zip','WGS84')
            self.assertEqual(len(list_versions(root,'Beijing')),2)
            first=activate(root,CITY,a['version']);second=activate(root,CITY,b['version'])
            self.assertEqual(first['custom_input_version'],a['version']);self.assertEqual(second['custom_input_version'],b['version'])
            original=activate(root,CITY,None)
            self.assertTrue(original['has_evaluation']);self.assertNotIn('custom_input_version',original)
            folder=root/'custom_inputs/Beijing'
            self.assertFalse((folder/'active.json').exists())
            self.assertEqual(len(list((folder/'activation_history').glob('*.json'))),2)
            for value in [a,b]:
                self.assertTrue((folder/value['version']/'original/package.zip').is_file())
                self.assertTrue(Path(value['manifest']).is_file())
    def test_old_absolute_paths_do_not_bind_reactivated_data(self):
        with TemporaryDirectory() as temporary:
            root=Path(temporary);value=import_package(CITY,root,build_template_package(),'a.zip','WGS84')
            path=Path(value['manifest']);record=json.loads(path.read_text(encoding='utf-8'))
            record['city']['source_directory_override']={'bus_dir':'D:/nonexistent/old','poi_dir':'D:/nonexistent/old'}
            path.write_text(json.dumps(record),encoding='utf-8')
            city=activate(root,CITY,value['version'])
            self.assertFalse(Path(city['source_directory_override']['bus_dir']).is_absolute())
            self.assertTrue((root/city['source_directory_override']['poi_dir']/'poi_wgs84.csv').is_file())
    def test_invalid_version_does_not_change_pointer(self):
        with TemporaryDirectory() as temporary:
            root=Path(temporary);value=import_package(CITY,root,build_template_package(),'a.zip','WGS84')
            activate(root,CITY,value['version']);pointer=root/'custom_inputs/Beijing/active.json';before=pointer.read_bytes()
            with self.assertRaises(ValueError):activate(root,CITY,'../outside')
            self.assertEqual(before,pointer.read_bytes())
if __name__=='__main__':unittest.main()
