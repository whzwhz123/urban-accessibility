"""HTTP error contracts and live-input guards; isolated test server, no AMap."""
import json
from pathlib import Path
import sys
from threading import Thread
from unittest.mock import patch
import unittest
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from Back_end import local_api as api

class QuietHandler(api.Handler):
    def log_message(self,*args):pass

class ApiExceptionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),QuietHandler)
        cls.thread=Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
        cls.base='http://127.0.0.1:'+str(cls.server.server_port)
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join()
    def send(self,path,body=None):
        req=Request(self.base+path,data=body,method='POST' if body is not None else 'GET')
        try:
            result=urlopen(req,timeout=5);status=result.status;value=json.load(result)
        except HTTPError as error:status=error.code;value=json.load(error)
        self.assertIsInstance(value,dict)
        return status,value
    def test_bad_project_zip_is_json_400(self):
        for content in (b'',b'not zip'):
            with self.subTest(content=content):self.assertEqual(self.send('/api/projects/import',content)[0],400)
    def test_nonobject_registration_and_export_are_client_errors(self):
        for path in ('/api/cities','/api/projects/export'):
            with self.subTest(path=path):self.assertEqual(self.send(path,b'[]')[0],400)
    def test_running_city_rejects_mutating_inputs(self):
        with patch.dict(api.ACTIVE_ASSIGNMENTS,{'Fixture':'busy'}):
            for action in ('headways','timetable','population','prepare-inputs','custom-inputs','custom-inputs/activate'):
                with self.subTest(action=action):self.assertEqual(self.send('/api/cities/Fixture/'+action,b'{}')[0],409)
            self.assertEqual(self.send('/api/cities/Fixture/custom-inputs/inspect',b'not zip')[0],400)
    def test_invalid_history_pagination_returns_json(self):
        with patch.dict(api.BY_ID,{'Fixture':{'id':'Fixture'}}):
            self.assertEqual(self.send('/api/cities/Fixture/runs?offset=not_a_number')[0],400)
    def test_missing_endpoint_is_json_404(self):
        self.assertEqual(self.send('/api/not_an_endpoint')[0],404)

if __name__=='__main__':unittest.main()
