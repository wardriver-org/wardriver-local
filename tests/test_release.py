import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_tmp = tempfile.TemporaryDirectory()
os.environ['WARDIVER_DB'] = str(Path(_tmp.name)/'wardriver.db')
os.environ['WARDIVER_OUI_AUTO_UPDATE'] = '0'
import server
import cell_towers

class ReleaseTests(unittest.TestCase):
    def test_bounds(self):
        for b in ([0,0,1,1], [0,0,float('nan'),1], [180,0,-180,1], None):
            with self.assertRaises(ValueError): cell_towers.bounds(b)

    def test_cache_and_provider_validation(self):
        site = {'type':'node','id':7,'lat':10.1,'lon':-10.2,'tags':{'communication:mobile_phone':'yes','operator':'<script>x</script>'}}
        response = json.dumps({'elements':[site,site,{'type':'node','id':8,'lat':10.1,'lon':-10.2,'tags':{}}]}).encode()
        path = Path(_tmp.name)/'test-sites.db'
        with patch('cell_towers.urllib.request.urlopen', return_value=io.BytesIO(response)) as network:
            cell_towers._last_fetch=0
            result=cell_towers.lookup(path,[-10.3,10,-10.1,10.2],True)
            self.assertEqual(len(result['features']),1)
            self.assertIn('communication%3Amobile_phone',network.call_args.args[0].data.decode())
        with patch('cell_towers.urllib.request.urlopen', side_effect=AssertionError('must stay offline')):
            self.assertEqual(len(cell_towers.lookup(path,[-10.3,10,-10.1,10.2])['features']),1)
        with self.assertRaises(ValueError): cell_towers.parse_sites({'remark':'timeout','elements':[]})
        with patch('cell_towers.urllib.request.urlopen', return_value=io.BytesIO(b'{"remark":"timeout","elements":[]}')):
            cell_towers._last_fetch=0
            with self.assertRaises(ValueError):cell_towers.lookup(path,[-10.3,10,-10.1,10.2],True)
        self.assertEqual(len(cell_towers.lookup(path,[-10.3,10,-10.1,10.2])['features']),1)

    @classmethod
    def setUpClass(cls):
        c=server.db();c.close()
        cls.http=server.BoundedHTTPServer(('127.0.0.1',0),server.H,2)
        cls.thread=threading.Thread(target=cls.http.serve_forever,daemon=True);cls.thread.start()
        cls.base='http://127.0.0.1:'+str(cls.http.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown();cls.http.server_close();cls.thread.join()

    def request(self,path,body,headers=None):
        req=urllib.request.Request(self.base+path,data=body,headers=headers or {})
        try:
            with urllib.request.urlopen(req,timeout=10) as r:return r.status,json.load(r)
        except urllib.error.HTTPError as e:return e.code,json.load(e)

    def test_import_rollback(self):
        headers={'X-Filename':'test.csv'}
        status,out=self.request('/api/import',b'name,latitude,longitude\nOriginal,10,-118\n',headers)
        self.assertEqual(status,200,out)
        status,out=self.request('/api/import',b'name,latitude,longitude\nInvalid,999,-118\n',headers)
        self.assertEqual(status,400,out)
        c=server.db()
        self.assertEqual(c.execute("select name from observations where source='test.csv'").fetchone()[0],'Original');c.close()

    def test_oversized_json(self):
        import http.client
        connection=http.client.HTTPConnection('127.0.0.1',self.http.server_port,timeout=10)
        connection.request('POST','/api/flock/review',headers={'Content-Length':str(1024*1024+1)})
        response=connection.getresponse()
        self.assertEqual(response.status,413)
        response.read();connection.close()

    def test_track_pagination(self):
        from urllib.parse import urlencode
        c=server.db()
        c.executemany('insert into track_fixes(fix_key,drive_key,source,seen_ts,latitude,longitude) values (?,?,?,?,?,?)',
            [(f'page-{i}', 'pagination-a' if i<30005 else 'pagination-z','pagination.csv',i,10,10) for i in range(30010)])
        c.commit();c.close()
        params=dict(west=9,east=11,south=9,north=11,limit=30000)
        ids=[];pages=0
        while True:
            with urllib.request.urlopen(self.base+'/api/map/tracks?'+urlencode(params),timeout=10) as response: data=json.load(response)
            ids.extend(r['id'] for r in data['tracks']);pages+=1
            if not data['has_more']:break
            params['cursor']=data['next_cursor']
        self.assertEqual(pages,2)
        self.assertEqual(len(ids),30010)
        self.assertEqual(len(set(ids)),30010)
        self.assertEqual(data['tracks'][-1]['drive_key'],'pagination-z')

    def test_gzip_imports(self):
        import gzip
        examples={
            'csv': b'name,latitude,longitude\nGzip,10,-118\n',
            'json': b'[{"name":"Gzip","latitude":10,"longitude":-10}]',
            'geojson': b'{"type":"FeatureCollection","features":[{"type":"Feature","geometry":{"type":"Point","coordinates":[-10,10]},"properties":{"name":"Gzip"}}]}',
            'gpx': b'<gpx><trk><trkseg><trkpt lat="34" lon="-118"><time>2026-09-23T00:00:00Z</time></trkpt></trkseg></trk></gpx>'}
        for ext,body in examples.items():
            with self.subTest(ext=ext):
                name='gzip-test.'+ext+'.gz';headers={'X-Filename':name}
                status,out=self.request('/api/import',gzip.compress(body),headers)
                self.assertEqual(status,200,out);self.assertEqual(out['imported'],1)
                status,out=self.request('/api/import',gzip.compress(body)[:-5],headers)
                self.assertEqual(status,400,out)
                c=server.db();self.assertEqual(c.execute('select count(*) from observations where source=?',(name,)).fetchone()[0],1);c.close()
        with patch.object(server,'IMPORT_MAX_BYTES',1024):
            status,out=self.request('/api/import',gzip.compress(b'x'*2048),{'X-Filename':'expanded.csv.gz'})
            self.assertEqual(status,400,out);self.assertIn('Decompressed',out['error'])
        status,_=self.request('/api/import',b'not gzip',{'X-Filename':'invalid.csv.gz'})
        self.assertEqual(status,400)

    def test_tower_endpoint_invalid(self):
        status,_=self.request('/api/cell-towers/fetch',b'{"bbox":[0,0,3,3]}')
        self.assertEqual(status,400)

if __name__=='__main__':unittest.main()
