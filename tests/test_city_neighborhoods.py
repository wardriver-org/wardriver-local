import ast
import gzip
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_release import server
import neighborhoods

class CityNeighborhoodTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.patches=[patch.object(server,'DB_PATH',Path(self.tmp.name)/'test.db'),patch.object(server,'_DB_INITIALIZED',False),patch.object(server,'NEIGHBORHOOD_ZONES',[])]
        for p in self.patches:p.start()
        self.conn=server.db()
    def tearDown(self):
        self.conn.close()
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()
    def row(self,lat,lon,mac='00:11:22:33:44:aa'):
        return dict(kind='wifi',name='Test',bssid=mac,latitude=lat,longitude=lon,seen_at='2026-01-01T12:00:00Z')
    def active(self):return {c['name'] for c in neighborhoods.active_cities(self.conn)}

    def test_empty_and_small_city_do_not_load_packs(self):
        neighborhoods.pack.cache_clear()
        self.assertEqual(self.active(),set())
        # A small town well outside the major-city activation polygons.
        server.insert_rows([self.row(44.4759,-73.2121)],'small-town.csv')
        self.assertEqual(self.active(),set())
        self.assertEqual(neighborhoods.pack.cache_info().misses,0)

    def test_multicity_import_only_loads_visited_cities_and_survives_restart(self):
        neighborhoods.pack.cache_clear()
        rows=[self.row(34.0522,-118.2437),self.row(40.7505,-73.9934,'00:11:22:33:44:bb')]
        server.insert_rows(rows,'two-cities.csv')
        self.assertEqual(self.active(),{'Los Angeles','New York City'})
        self.assertEqual(neighborhoods.pack.cache_info().misses,2)
        keys={z['key'] for z in server.neighborhood_zones(self.conn)}
        self.assertIn('downtown-la',keys)
        self.assertIn('beverly-hills',keys)
        self.assertGreater(self.conn.execute('select count(*) from neighborhood_devices').fetchone()[0],0)
        server.insert_rows(rows,'repeat.csv')
        self.assertEqual(neighborhoods.pack.cache_info().misses,2)
        with patch.object(server,'_DB_INITIALIZED',False):
            reopened=server.db();reopened.close()
        self.assertEqual(self.active(),{'Los Angeles','New York City'})
        result=server._neighborhood_metrics(self.conn)
        self.assertEqual(len(result['cities']),2)

    def test_failed_import_rolls_back_activation(self):
        with patch.object(server,'refresh_drive_materialization',side_effect=RuntimeError('fixture failure')):
            with self.assertRaises(RuntimeError):server.insert_rows([self.row(34.0522,-118.2437)],'bad.csv')
        self.assertEqual(self.active(),set())
        self.assertEqual(self.conn.execute("select count(*) from observations where source='bad.csv'").fetchone()[0],0)

    def test_source_polygon_holes_and_bbox_false_positives(self):
        z=dict(south=0,north=4,west=0,east=4,geometry={'type':'Polygon','coordinates':[[[0,0],[4,0],[0,4],[0,0]],[[.5,.5],[1,.5],[1,1],[.5,1],[.5,.5]]]})
        self.assertTrue(neighborhoods.contains(z,.2,.2))
        self.assertFalse(neighborhoods.contains(z,3,3))
        self.assertFalse(neighborhoods.contains(z,.75,.75))
        idx=neighborhoods.ZoneIndex([z])
        self.assertEqual(list(idx.matches(3,3)),[])
        self.assertEqual(list(idx.matches(.2,.2)),[z])

    def test_every_major_city_pack_is_present_valid_and_unique(self):
        manifest=neighborhoods.manifest();self.assertEqual(len(manifest['cities']),88)
        all_keys=set()
        for city in manifest['cities']:
            self.assertGreaterEqual(city['population_2020'],250000)
            pack=neighborhoods.pack(city['id'],city['sha256'])
            self.assertTrue(pack,city['name'])
            self.assertEqual(len(pack),city['zone_count'])
            for z in pack:
                self.assertNotIn(z['key'],all_keys);all_keys.add(z['key'])
                self.assertTrue(-90<=z['south']<z['north']<=90)
                self.assertTrue(-180<=z['west']<z['east']<=180)
                self.assertEqual(z['city_id'],city['id'])
                if z.get('geometry'):self.assertIn(z['geometry']['type'],('Polygon','MultiPolygon'))

    def test_custom_zone_overrides_bundled_key(self):
        server.insert_rows([self.row(34.0522,-118.2437)],'city.csv')
        local=dict(key='downtown-la',name='My custom zone',icon='📍',group='Custom',south=34,north=34.1,west=-118.3,east=-118.2)
        with patch.object(server,'NEIGHBORHOOD_ZONES',[local]):
            result=[z for z in server.neighborhood_zones(self.conn) if z['key']=='downtown-la']
            self.assertEqual(result,[local])

    def test_http_import_and_full_reset(self):
        import threading,urllib.request
        http=server.BoundedHTTPServer(('127.0.0.1',0),server.H,2)
        thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(http.server_port)
        try:
            payload=b'name,bssid,latitude,longitude\nTest,00:11:22:33:44:cc,34.0522,-118.2437\n'
            request=urllib.request.Request(base+'/api/import',data=payload,headers={'X-Filename':'city-http.csv'})
            with urllib.request.urlopen(request) as response:self.assertTrue(json.load(response)['ok'])
            self.assertEqual(self.active(),{'Los Angeles'})
            request=urllib.request.Request(base+'/api/reset',data=b'{}',headers={'X-Wardriver-Local':'1'})
            with urllib.request.urlopen(request) as response:self.assertTrue(json.load(response)['ok'])
            self.assertEqual(self.active(),set())
            self.assertEqual(self.conn.execute('select count(*) from neighborhood_devices').fetchone()[0],0)
        finally:http.shutdown();http.server_close();thread.join()

    def test_wigle_import_activates_city_without_network_lookup(self):
        import io,wigle_sync
        wigle_sync.setup(self.conn)
        csv=b'MAC,SSID,AuthMode,FirstSeen,Channel,RSSI,CurrentLatitude,CurrentLongitude,Type\n00:11:22:33:44:dd,Own,[WPA2],2026-01-01 12:00:00,1,-50,40.7505,-73.9934,WIFI\n'
        with patch('urllib.request.urlopen',side_effect=AssertionError('neighborhood lookup must stay offline')):
            count,duplicate=wigle_sync.import_file(server,self.conn,'fixture-owner','transaction1',io.BytesIO(csv))
        self.assertEqual(count,1)
        self.assertEqual(self.active(),{'New York City'})

    def test_upgrade_detects_existing_observations(self):
        # Simulate an old database: observations exist, no city has been activated.
        self.conn.execute('begin immediate')
        server._insert_rows_conn(self.conn,[self.row(34.0522,-118.2437)],'existing.csv')
        self.conn.commit()
        self.assertEqual(self.active(),set())
        with patch.object(server,'_DB_INITIALIZED',False):
            c=server.db();c.close()
        self.assertEqual(self.active(),{'Los Angeles'})
