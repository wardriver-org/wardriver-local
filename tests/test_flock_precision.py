import unittest
from unittest.mock import patch
from test_release import server

class FlockPrecisionTests(unittest.TestCase):
    def score(self,name='Home WiFi',mac='70:C9:4E:00:00:01',decision=None,**changes):
        device={'name':name,'manufacturer':'Generic OEM','bssid':mac,'kind':'alpr','signal_mobile':0}
        history=dict(fingerprint_match=False,fingerprint_sources=0,drive_count=5,day_count=5,spread_m=10,observations=100,persistence_days=60,fingerprints=[])
        history.update(changes)
        class Connection:
            def execute(self,*args):return self
            def fetchone(self):return device
        review={'decision':decision,'notes':''} if decision else None
        with patch.object(server,'_flock_review_row',return_value=review),patch.object(server,'_flock_history',return_value=history):
            return server._flock_score_device(Connection(),'test')

    def test_shared_prefix_and_behavior_not_identity(self):
        for name in ['Home WiFi','Flock of Seagulls','flock','flock-safety']:
            result=self.score(name=name,**({'drive_count':1} if name=='flock-safety' else {}))
            self.assertFalse(result['identity_signal']);self.assertLess(result['score'],80)
        self.assertFalse(server.classify_flock('Flock Safety',None,'70:C9:4E:00:00:01')['is_flock'])

    def test_name_and_fingerprint_alone_do_not_identify(self):
        result=self.score(name='Flock Safety',mac='00:11:22:33:44:55',fingerprint_match=True,fingerprint_sources=1)
        self.assertFalse(result['identity_signal'])
        result=self.score(fingerprint_match=True,fingerprint_sources=5)
        self.assertFalse(result['identity_signal'])

    def test_corroborated_and_manual_overrides(self):
        result=self.score(name='Flock Safety')
        self.assertTrue(result['identity_signal']);self.assertGreaterEqual(result['score'],80)
        result=self.score(name='Flock Safety',day_count=1)
        self.assertFalse(result['identity_signal'])
        result=self.score(name='Flock Safety',spread_m=700)
        self.assertFalse(result['identity_signal'])
        result=self.score(name='Flock Safety',mac='82:6B:F2:00:00:01')
        self.assertFalse(result['identity_signal'])
        self.assertEqual(self.score(decision='confirmed')['level'],'Confirmed')
        self.assertEqual(self.score(name='Flock Safety',decision='rejected')['score'],0)
