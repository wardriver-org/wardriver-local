import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_release import server

class NeighborhoodConfigTests(unittest.TestCase):
    def test_missing_empty_and_invalid_catalogs(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)/'zones.json'
            self.assertEqual(server.load_neighborhood_zones(p), [])
            p.write_text('[]')
            self.assertEqual(server.load_neighborhood_zones(p), [])
            valid = dict(key='test',name='Test',south=9,north=11,west=-11,east=-9)
            p.write_text(json.dumps([valid]))
            self.assertEqual(server.load_neighborhood_zones(p)[0]['group'], 'Local zones')
            for value in ([valid,valid], [dict(valid,north=8)], [dict(valid,west=float('nan'))], [dict(valid,south=True)], {}):
                p.write_text(json.dumps(value))
                with self.assertRaises(ValueError): server.load_neighborhood_zones(p)

    def test_catalog_change_rebuilds_without_deleting_observations(self):
        zone=dict(key='test',name='Test',icon='📍',group='Test',south=9,north=11,west=-11,east=-9)
        with tempfile.TemporaryDirectory() as directory, patch.object(server,'DB_PATH',Path(directory)/'test.db'), patch.object(server,'_DB_INITIALIZED',False), patch.object(server,'NEIGHBORHOOD_ZONES',[zone]):
            c=server.db()
            count=c.execute('select count(*) from observations').fetchone()[0]
            self.assertGreater(c.execute('select count(*) from neighborhood_devices').fetchone()[0],0)
            with patch.object(server,'NEIGHBORHOOD_ZONES',[]): server.sync_neighborhood_catalog(c)
            self.assertEqual(c.execute('select count(*) from neighborhood_devices').fetchone()[0],0)
            self.assertEqual(c.execute('select count(*) from observations').fetchone()[0],count)
            server.sync_neighborhood_catalog(c)
            self.assertGreater(c.execute('select count(*) from neighborhood_devices').fetchone()[0],0)
            c.close()
