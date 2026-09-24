import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from test_release import server

class AwardBalanceTests(unittest.TestCase):
    def test_unlocks_recalculate_at_new_boundaries(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(server,'DB_PATH',Path(directory)/'awards.db'), patch.object(server,'_DB_INITIALIZED',False), patch.object(server,'NEIGHBORHOOD_ZONES',[{'key':'test-zone','name':'Test zone','group':'Test','icon':'📍','south':9,'north':11,'west':-11,'east':-9}]):
            c=server.db();c.execute('delete from drive_stats');c.execute('delete from drive_days');c.execute('delete from neighborhood_devices')
            zone=server.NEIGHBORHOOD_ZONES[0]['key']
            c.executemany('insert into neighborhood_devices values (?,?,?,?,?)',[(zone,f'device-{i}','2026-01-01','2026-01-01',100) for i in range(499)])
            c.executemany('insert into drive_stats(drive_key,first_t,last_t,distance_m,updated_at) values (?,?,?,?,?)',[(f'drive-{i}',1767225600+i*86400,1767225600+i*86400,10*1609.344,'2026-01-01') for i in range(4)])
            c.commit();count=c.execute('select count(*) from observations').fetchone()[0]
            before=server._build_perf_summary();awards={a['key']:a for a in before['gamification']['awards']}
            self.assertFalse(awards['first-drive']['earned']);self.assertFalse(awards['miles-10']['earned'])
            self.assertEqual(before['neighborhoods']['unlocked'],0)
            self.assertEqual(before['neighborhoods']['next_unlock']['gap'],1)
            c.execute('insert into neighborhood_devices values (?,?,?,?,?)',(zone,'device-499','2026-02-01','2026-02-01',1))
            c.execute('insert into drive_stats(drive_key,first_t,last_t,distance_m,updated_at) values (?,?,?,?,?)',('drive-4',1767571200,1767571200,10*1609.344,'2026-01-05'));c.commit()
            after=server._build_perf_summary();awards={a['key']:a for a in after['gamification']['awards']}
            self.assertTrue(awards['first-drive']['earned']);self.assertEqual(awards['first-drive']['earned_at'],'2026-01-05')
            self.assertTrue(awards['miles-10']['earned']);self.assertEqual(after['neighborhoods']['neighborhood_xp'],100)
            self.assertEqual(next(z for z in after['neighborhoods']['zones'] if z['key']==zone)['earned_at'],'2026-02-01')
            self.assertEqual(c.execute('select count(*) from observations').fetchone()[0],count)
            self.assertEqual(after['gamification']['flock_photo_xp_each'],25)
            self.assertEqual(after['gamification']['flock_xp_each'],50)
            c.close()
