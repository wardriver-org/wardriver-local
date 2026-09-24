"""Additive own-account WiGLE transfer. Never queries public network searches."""
import hashlib
import json
import re
import tempfile
import threading
import time
import urllib.parse
import urllib.request

_lock = threading.Lock()
_state_lock = threading.Lock()
_state = {'running': False, 'phase': 'idle', 'files': 0, 'imported': 0, 'duplicates': 0, 'error': ''}

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('WiGLE redirect refused; credentials stay on the configured API host')

def status():
    with _state_lock:
        return {'ok': True, **_state}

def update(**values):
    with _state_lock:
        _state.update(values)

def setup(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS wigle_owned_files(account TEXT,transid TEXT,source TEXT,imported INTEGER,duplicates INTEGER,PRIMARY KEY(account,transid));
    CREATE TABLE IF NOT EXISTS wigle_remote_hashes(account TEXT,digest TEXT,PRIMARY KEY(account,digest));
    CREATE TABLE IF NOT EXISTS wigle_sync_outbox(account TEXT,signature TEXT,status TEXT,PRIMARY KEY(account,signature));
    CREATE TABLE IF NOT EXISTS wigle_observation_hashes(digest TEXT PRIMARY KEY,observation_id TEXT);
    CREATE INDEX IF NOT EXISTS wigle_hash_observation ON wigle_observation_hashes(observation_id);
    ''')
    c.commit()

def request(s, cfg, path):
    if s.WIGLE_API_BASE != 'https://api.wigle.net':
        raise ValueError('Own-data sync requires the official https://api.wigle.net endpoint')
    req = urllib.request.Request(s.WIGLE_API_BASE+path,headers=s._wigle_auth_headers(cfg['api_token']))
    return urllib.request.build_opener(NoRedirect()).open(req,timeout=s.WIGLE_TIMEOUT)

def json_request(s,cfg,path):
    with request(s,cfg,path) as response:
        raw=response.read(4*1024*1024+1)
    if len(raw)>4*1024*1024:raise ValueError('WiGLE response exceeded metadata limit')
    data=json.loads(raw)
    if not isinstance(data,dict) or data.get('success') is not True:
        raise ValueError('WiGLE request unsuccessful; check credentials, quota and API availability')
    return data

def account(s,cfg):
    data=json_request(s,cfg,'/api/v2/profile/user')
    user=data.get('userid')
    if not isinstance(user,str) or not user.strip():raise ValueError('WiGLE account identity unavailable; sync stopped')
    return user,hashlib.sha256(user.encode()).hexdigest()

def page(s,cfg,offset):
    data=json_request(s,cfg,f'/api/v2/file/transactions?pagestart={offset}&pageend={offset+99}')
    rows=data.get('results')
    if not isinstance(rows,list):raise ValueError('Unexpected own-upload history format; sync stopped')
    for row in rows:
        if not isinstance(row,dict) or not re.fullmatch(r'[A-Za-z0-9_-]{1,150}',str(row.get('transid',''))):
            raise ValueError('Invalid transaction in own-upload history')
    return rows

def digest(s,row):
    r=s.normalize_row(row)
    mac=s.normalize_mac(r.get('bssid'));stamp=s._parse_obs_time(r.get('seen_at'))
    lat=s.num(r.get('latitude'));lon=s.num(r.get('longitude'))
    if len(mac)!=12 or not stamp or lat is None or lon is None or not(-90<=lat<=90 and -180<=lon<=180):return None
    # ALPR is an application label for Wi-Fi, not a separate radio observation.
    value=[mac,int(stamp),round(lat,7),round(lon,7)]
    return hashlib.sha256(json.dumps(value,separators=(',',':')).encode()).hexdigest()

def index_existing(s,c):
    c.execute('DELETE FROM wigle_observation_hashes WHERE observation_id NOT IN (SELECT id FROM observations)')
    for row in c.execute('SELECT o.* FROM observations o LEFT JOIN wigle_observation_hashes h ON h.observation_id=o.id WHERE h.observation_id IS NULL'):
        key=digest(s,dict(row))
        if key:c.execute('INSERT OR IGNORE INTO wigle_observation_hashes VALUES (?,?)',(key,row['id']))
    c.commit()

def import_file(s,c,owner,transid,spool):
    source=f'wigle-own:{owner[:16]}:{transid}.csv'
    imported=duplicates=parsed=0;keys=set()
    c.execute('BEGIN IMMEDIATE')
    try:
        for raw in s.iter_csv_stream(spool):
            parsed+=1;key=digest(s,raw)
            if not key:raise ValueError('Downloaded log contains unsupported radio IDs, timestamps or coordinates; file left unimported')
            c.execute('INSERT OR IGNORE INTO wigle_remote_hashes VALUES (?,?)',(owner,key))
            if c.execute('SELECT 1 FROM wigle_observation_hashes WHERE digest=?',(key,)).fetchone():
                duplicates+=1;continue
            count,rejected,_,touched=s._insert_rows_conn(c,[raw],source)
            if rejected or count!=1:raise ValueError('Downloaded observation could not be imported')
            obs_id=c.execute('SELECT id FROM observations WHERE rowid=last_insert_rowid()').fetchone()[0]
            c.execute('INSERT INTO wigle_observation_hashes VALUES (?,?)',(key,obs_id))
            imported+=count;keys.update(touched)
        if not parsed:raise ValueError('WiGLE file is empty or still processing; retry later')
        if imported:
            s.refresh_devices_for_keys(c,keys);s.refresh_drive_materialization(c,source,None)
            c.execute('INSERT INTO import_runs(id,source,parsed,imported,rejected,replaced,imported_at) VALUES (?,?,?,?,0,0,?)',
                      (str(s.uuid.uuid4()),source,parsed,imported,s.datetime.now(s.timezone.utc).isoformat()))
        c.execute('INSERT INTO wigle_owned_files VALUES (?,?,?,?,?)',(owner,transid,source,imported,duplicates))
        c.commit();return imported,duplicates
    except Exception:
        c.rollback();raise

def run(s,cfg,source):
    c=None
    try:
        user,owner=account(s,cfg);update(account=user,phase='Checking own upload history')
        c=s.db();setup(c);index_existing(s,c)
        offset=0;seen=set()
        # Pull first so logs already in WiGLE aren't sent there again by this job.
        while True:
            rows=page(s,cfg,offset)
            if not rows:break
            fresh=0
            for row in rows:
                tid=str(row['transid'])
                if tid in seen:continue
                seen.add(tid);fresh+=1
                if c.execute('SELECT 1 FROM wigle_owned_files WHERE account=? AND transid=?',(owner,tid)).fetchone():continue
                update(phase='Downloading own upload '+tid)
                with tempfile.SpooledTemporaryFile(max_size=8*1024*1024,mode='w+b') as spool:
                    with request(s,cfg,'/api/v2/file/csv/'+urllib.parse.quote(tid,safe='')) as response:
                        size=0
                        while True:
                            chunk=response.read(min(1024*1024,s.IMPORT_MAX_BYTES-size+1))
                            if not chunk:break
                            size+=len(chunk)
                            if size>s.IMPORT_MAX_BYTES:raise ValueError('WiGLE CSV exceeds the 512 MB local limit')
                            spool.write(chunk)
                    spool.seek(0);update(phase='Importing own upload '+tid)
                    count,dupes=import_file(s,c,owner,tid,spool)
                current=status();update(files=current['files']+1,imported=current['imported']+count,duplicates=current['duplicates']+dupes)
                time.sleep(1)
            if not fresh:raise ValueError('WiGLE history pagination did not advance')
            offset+=len(rows)
            time.sleep(1)
        if source:
            if source.startswith('wigle-own:'):raise ValueError('Downloaded WiGLE logs cannot be uploaded again')
            # Token/account changes during a job must not redirect the upload.
            current=s.wigle_settings(include_token=True)
            if not current['enabled'] or current['api_token']!=cfg['api_token']:raise ValueError('WiGLE settings changed; upload stopped')
            pending=[];hashes=[];pending_hashes=set()
            for row in c.execute('SELECT * FROM observations WHERE source=?',(source,)):
                key=digest(s,dict(row))
                if not key:raise ValueError('Selected log contains unsupported observations; upload stopped')
                if key not in pending_hashes and not c.execute('SELECT 1 FROM wigle_remote_hashes WHERE account=? AND digest=?',(owner,key)).fetchone():
                    pending.append(row['id']);hashes.append(key);pending_hashes.add(key)
            if pending:
                signature=hashlib.sha256(''.join(sorted(set(hashes))).encode()).hexdigest()
                if c.execute('SELECT 1 FROM wigle_sync_outbox WHERE account=? AND signature=?',(owner,signature)).fetchone():
                    raise ValueError('This upload was already attempted; check WiGLE history before retrying')
                c.execute('INSERT INTO wigle_sync_outbox VALUES (?,?,?)',(owner,signature,'sending'));c.commit()
                update(phase='Uploading selected owned log')
                s.wigle_upload_source(source,cfg_override=cfg,include_ids=set(pending))
                c.executemany('INSERT OR IGNORE INTO wigle_remote_hashes VALUES (?,?)',[(owner,key) for key in hashes])
                c.execute("UPDATE wigle_sync_outbox SET status='complete' WHERE account=? AND signature=?",(owner,signature));c.commit()
        update(phase='Complete')
    except Exception as error:
        # Never automatically retry uploads after an ambiguous remote outcome.
        update(phase='Stopped',error=str(error)[:500])
    finally:
        if c:c.close()
        update(running=False);_lock.release()

def start(s,payload):
    cfg=s.wigle_settings(include_token=True)
    if not cfg['enabled'] or not cfg['api_token']:raise ValueError('Save and enable your WiGLE credentials first')
    source=str(payload.get('source') or '')
    if source and payload.get('own_data') is not True:raise ValueError('Confirm that the selected local log contains only your own observations')
    if source.startswith('wigle-own:'):raise ValueError('WiGLE downloads are excluded from outbound sync')
    if not _lock.acquire(blocking=False):raise ValueError('An own-data sync is already running')
    update(running=True,phase='Connecting',files=0,imported=0,duplicates=0,error='',account='')
    try:threading.Thread(target=run,args=(s,cfg,source),daemon=True,name='wigle-own-sync').start()
    except Exception:_lock.release();update(running=False);raise
    return status()
