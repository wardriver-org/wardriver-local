import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import map_areas


class MapAreaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.manager = map_areas.Areas(self.tmp.name)
        self.payload = dict(name='Test area', bbox=[-118.5, 34, -118.4, 34.1], maxzoom=12,
                            build='2026-07-22', consent=True)

    def wait(self):
        deadline = time.time() + 3
        while self.manager.status()['job']['state'] in ('downloading', 'verifying'):
            if time.time() > deadline: self.fail('Worker did not finish')
            time.sleep(.01)
        return self.manager.status()

    def test_reject_invalid_inputs(self):
        for bbox in (None, [], [0, 0, float('nan'), 1], [180, 0, -180, 1], [0, -90, 1, 90]):
            with self.assertRaises(ValueError): map_areas.bounds(bbox)
        for changes in ({'consent': False}, {'maxzoom': True}, {'maxzoom': 16},
                        {'build': 'https://localhost/x'}, {'build': '2026-02-30'}):
            with self.assertRaises(ValueError): self.manager.start({**self.payload, **changes})
        for key in ('../region', '', 'a'*31, 'x'*32):
            with self.assertRaises(ValueError): self.manager.path(key)

    def fake_command(self, args, part):
        if args[0] == 'extract': part.write_bytes(b'PMTiles\x03' + b'\0'*1024)

    def test_verified_archive_publication_and_delete(self):
        with patch('map_areas.shutil.which', return_value='/bin/pmtiles'), patch.object(self.manager, '_command', side_effect=self.fake_command):
            self.manager.start(self.payload)
            status = self.wait()
        self.assertEqual(status['job']['state'], 'ready')
        row = status['areas'][0]
        self.assertEqual(row['name'], 'Test area')
        with self.assertRaises(ValueError): self.manager.remove(row['id'], row['url'])
        self.manager.remove(row['id'], '/maps/region.pmtiles')
        self.assertEqual(self.manager.records(), [])

    def test_failure_never_publishes_partial_map(self):
        def fail(args, part):
            part.write_bytes(b'incomplete')
            raise ValueError('offline')
        with patch('map_areas.shutil.which', return_value='/bin/pmtiles'), patch.object(self.manager, '_command', side_effect=fail):
            self.manager.start(self.payload)
            self.assertEqual(self.wait()['job']['state'], 'failed')
        self.assertEqual(list(Path(self.tmp.name).glob('*.part')), [])
        self.assertEqual(self.manager.records(), [])

    def test_restart_cleans_interrupted_download_only(self):
        root = Path(self.tmp.name)
        (root/'old.part').write_bytes(b'partial')
        (root/'saved.pmtiles').write_bytes(b'preserve')
        map_areas.Areas(root)
        self.assertFalse((root/'old.part').exists())
        self.assertTrue((root/'saved.pmtiles').exists())

    def test_cancel_terminates_real_subprocess(self):
        executable = Path(self.tmp.name)/'fake-pmtiles'
        executable.write_text('#!/usr/bin/env python3\nimport time\ntime.sleep(30)\n')
        executable.chmod(0o755)
        self.manager.binary = str(executable)
        self.manager.start(self.payload)
        with self.assertRaises(ValueError): self.manager.start(self.payload)
        self.manager.cancel()
        self.assertEqual(self.wait()['job']['state'], 'cancelled')
        self.assertEqual(self.manager.records(), [])

    def test_search_accepts_country_and_caches(self):
        data = json.dumps([{'display_name':'Country', 'boundingbox':['0','10','20','30']}]).encode()
        with patch('map_areas.urllib.request.urlopen', return_value=io.BytesIO(data)) as net:
            result = self.manager.search('Country', 'https://example.com')
            self.assertEqual(result[0]['bbox'], [20,0,30,10])
            self.assertEqual(self.manager.search('Country', 'https://example.com'), result)
            self.assertEqual(net.call_count, 1)
            self.assertNotIn('featureType', net.call_args.args[0].full_url)


class MapAreaHTTPTests(unittest.TestCase):
    def test_map_selection_ranges_and_request_token(self):
        import threading
        import urllib.request
        import urllib.error
        import server
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = map_areas.Areas(root/'areas')
            with patch.object(server, 'DB_PATH', root/'wardriver.db'), patch.object(server, '_MAP_AREAS', manager), patch.object(server, '_DB_INITIALIZED', False), patch.object(server, 'OUI_AUTO_UPDATE', False):
                server.db().close()
                http = server.BoundedHTTPServer(('127.0.0.1', 0), server.H)
                worker = threading.Thread(target=http.serve_forever, daemon=True)
                worker.start()
                base = 'http://127.0.0.1:' + str(http.server_port)
                def post(path, body, token=None):
                    headers = {'Content-Type':'application/json'}
                    if token: headers['X-Map-Areas-Token'] = token
                    req = urllib.request.Request(base+path, data=json.dumps(body).encode(), headers=headers)
                    with urllib.request.urlopen(req, timeout=3) as response:
                        return json.load(response)
                try:
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        post('/api/map/areas/cancel', {})
                    self.assertEqual(error.exception.code, 403)
                    self.assertTrue(post('/api/map/areas/cancel', {}, manager.token)['ok'])
                    key = 'b'*32
                    manager.path(key).write_bytes(b'PMTiles\x03'+bytes(2048))
                    url = '/maps/areas/'+key+'.pmtiles'
                    request = urllib.request.Request(base+url, headers={'Range':'bytes=0-7'})
                    with urllib.request.urlopen(request, timeout=3) as response:
                        self.assertEqual(response.status, 206)
                        self.assertEqual(response.headers['Content-Range'], 'bytes 0-7/2056')
                        self.assertEqual(response.read(), b'PMTiles\x03')
                    cfg = post('/api/map/settings', {'provider':'selfhosted','tile_url_template':url})
                    self.assertEqual(cfg['tile_url_template'], url)
                    self.assertEqual(post('/api/map/test', {})['endpoint'], url)
                    with self.assertRaises(urllib.error.HTTPError) as error:
                        post('/api/map/areas/delete', {'id':key}, manager.token)
                    self.assertEqual(error.exception.code, 400)
                    post('/api/map/settings', {'provider':'none'})
                    self.assertTrue(post('/api/map/areas/delete', {'id':key}, manager.token)['ok'])
                finally:
                    http.shutdown()
                    http.server_close()
                    worker.join(timeout=3)
