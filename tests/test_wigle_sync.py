import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_release import server
import wigle_sync

CSV=b'MAC,SSID,AuthMode,FirstSeen,Channel,RSSI,CurrentLatitude,CurrentLongitude,Type\n00:11:22:33:44:55,Own,[WPA2],2026-01-01 12:00:00,1,-50,10,-10,WIFI\n'

class OwnSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=patch.object(server,'DB_PATH',Path(self.temp.name)/'own.db');self.path.start()
        self.init=patch.object(server,'_DB_INITIALIZED',False);self.init.start()
        self.c=server.db();wigle_sync.setup(self.c)
    def tearDown(self):
        self.c.close();self.init.stop();self.path.stop();self.temp.cleanup()
    def test_dedup_preserves_local_data_and_provenance(self):
        server.insert_rows(list(server.iter_csv_stream(io.BytesIO(CSV))),'original.csv')
        before=self.c.execute('select count(*) from observations').fetchone()[0]
        wigle_sync.index_existing(server,self.c)
        self.assertEqual(wigle_sync.import_file(server,self.c,'account','tx1',io.BytesIO(CSV)),(0,1))
        self.assertEqual(self.c.execute('select count(*) from observations').fetchone()[0],before)
        self.assertEqual(self.c.execute('select count(*) from wigle_remote_hashes').fetchone()[0],1)
        with self.assertRaises(ValueError):server.wigle_upload_source('wigle-own:account:tx1.csv')
    def test_invalid_download_rolls_back(self):
        payload=CSV+b'not-a-mac,Bad,[WPA2],2026-01-01 12:00:00,1,-50,10,-10,WIFI\n'
        before=self.c.execute('select count(*) from observations').fetchone()[0]
        with self.assertRaises(ValueError):wigle_sync.import_file(server,self.c,'account','bad',io.BytesIO(payload))
        self.assertEqual(self.c.execute('select count(*) from observations').fetchone()[0],before)
        self.assertEqual(self.c.execute('select count(*) from wigle_owned_files').fetchone()[0],0)
    def test_only_authenticated_history_is_downloaded_and_resumes(self):
        cfg={'enabled':True,'api_token':'fixture','donate':False};paths=[]
        def metadata(s,c,path):
            paths.append(path)
            if path=='/api/v2/profile/user':return {'success':True,'userid':'my-account'}
            if 'pagestart=0&' in path:return {'success':True,'results':[{'transid':'mine-1'}]}
            return {'success':True,'results':[]}
        def download(s,c,path):
            paths.append(path);self.assertEqual(path,'/api/v2/file/csv/mine-1');return io.BytesIO(CSV)
        with patch.object(wigle_sync,'json_request',side_effect=metadata),patch.object(wigle_sync,'request',side_effect=download),patch.object(wigle_sync.time,'sleep'):
            for _ in range(2):
                wigle_sync._lock.acquire();wigle_sync.update(running=True,error='',files=0,imported=0,duplicates=0)
                wigle_sync.run(server,cfg,'');self.assertEqual(wigle_sync.status()['phase'],'Complete',wigle_sync.status())
        self.assertEqual(paths.count('/api/v2/file/csv/mine-1'),1)
        self.assertFalse(any('/network/' in p for p in paths))
    def test_outbound_requires_ownership_and_blocks_downloads(self):
        with patch.object(server,'wigle_settings',return_value={'enabled':True,'api_token':'fixture'}):
            with self.assertRaises(ValueError):wigle_sync.start(server,{'source':'local.csv'})
            with self.assertRaises(ValueError):wigle_sync.start(server,{'source':'wigle-own:x:y.csv','own_data':True})
    def test_only_unsynced_observations_are_uploaded(self):
        cfg={'enabled':True,'api_token':'fixture','donate':False}
        rows=list(server.iter_csv_stream(io.BytesIO(CSV)))
        newer=dict(rows[0]);newer['FirstSeen']='2026-01-02 12:00:00'
        server.insert_rows(rows+[newer],'mine.csv')
        def metadata(s,c,path):
            if path=='/api/v2/profile/user':return {'success':True,'userid':'my-account'}
            return {'success':True,'results':[{'transid':'mine-1'}] if 'pagestart=0&' in path else []}
        with patch.object(wigle_sync,'json_request',side_effect=metadata),patch.object(wigle_sync,'request',return_value=io.BytesIO(CSV)),patch.object(wigle_sync.time,'sleep'),patch.object(server,'wigle_settings',return_value=cfg),patch.object(server,'wigle_upload_source',return_value={'ok':True}) as upload:
            wigle_sync._lock.acquire();wigle_sync.update(running=True,error='',files=0,imported=0,duplicates=0);wigle_sync.run(server,cfg,'mine.csv')
            self.assertEqual(wigle_sync.status()['phase'],'Complete',wigle_sync.status())
            self.assertEqual(len(upload.call_args.kwargs['include_ids']),1)
