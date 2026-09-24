import csv, hashlib, html as html_lib, io, itertools, json, math, mimetypes, os, re, sqlite3, tempfile, threading, time, urllib.parse, urllib.request, uuid, xml.etree.ElementTree as ET
import gzip
import sys
import wigle_sync
import cell_towers
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / 'public'
DB_PATH = Path(os.environ.get('WARDIVER_DB', '/data/wardriver.db'))
FLOCK_PHOTO_DIR = Path(os.environ.get('WARDIVER_FLOCK_PHOTO_DIR', str(DB_PATH.parent / 'flock-photos')))
FLOCK_PHOTO_MAX_BYTES = max(1024*1024, min(25*1024*1024, int(os.environ.get('WARDIVER_FLOCK_PHOTO_MAX_BYTES', str(10*1024*1024)))))
FLOCK_PHOTO_XP = 25
FLOCK_VERIFY_RADIUS_M = max(50, min(1000, int(os.environ.get('WARDIVER_FLOCK_VERIFY_RADIUS_M', '250'))))
FLOCK_VERIFY_CACHE_HOURS = max(1, min(720, int(os.environ.get('WARDIVER_FLOCK_VERIFY_CACHE_HOURS', '168'))))
FLOCK_OVERPASS_URLS = [x.strip() for x in os.environ.get('WARDIVER_OVERPASS_URLS','https://overpass-api.de/api/interpreter,https://overpass.kumi.systems/api/interpreter').split(',') if x.strip()]
FLOCK_EFF_BASE_URL = os.environ.get('WARDIVER_EFF_ATLAS_URL','https://atlasofsurveillance.org').rstrip('/')
FLOCK_VERIFY_TIMEOUT = max(3, min(30, int(os.environ.get('WARDIVER_FLOCK_VERIFY_TIMEOUT','12'))))
HOST = os.environ.get('WARDIVER_HOST', '0.0.0.0')
PORT = int(os.environ.get('WARDIVER_PORT', '8787'))
HTTP_WORKERS = max(2, min(32, int(os.environ.get('WARDIVER_HTTP_WORKERS', '8'))))
OUI_DIR = Path(os.environ.get('WARDIVER_OUI_DIR', str(DB_PATH.parent / 'oui')))
OUI_AUTO_UPDATE = os.environ.get('WARDIVER_OUI_AUTO_UPDATE', '1') == '1'
OUI_MAX_AGE_DAYS = int(os.environ.get('WARDIVER_OUI_MAX_AGE_DAYS', '30'))
ROUTER_URL = os.environ.get('WARDIVER_ROUTER_URL', 'https://router.project-osrm.org').rstrip('/')
ROUTER_TIMEOUT = int(os.environ.get('WARDIVER_ROUTER_TIMEOUT', '12'))
GEOCODER_URL = os.environ.get('WARDIVER_GEOCODER_URL', 'https://nominatim.openstreetmap.org').rstrip('/')
GEOCODER_TIMEOUT = int(os.environ.get('WARDIVER_GEOCODER_TIMEOUT', '10'))
TILE_DIR = Path(os.environ.get('WARDIVER_TILE_DIR', str(DB_PATH.parent / 'tiles')))
TILE_TIMEOUT = int(os.environ.get('WARDIVER_TILE_TIMEOUT', '8'))
PMTILES_PATH = Path(os.environ.get('WARDIVER_PMTILES', '/maps/region.pmtiles'))
MAP_DEFAULT_PROVIDER = os.environ.get('WARDIVER_BASEMAP_PROVIDER', 'none').strip().lower()
MAP_DEFAULT_URL = os.environ.get('WARDIVER_BASEMAP_URL', '').strip()
MAP_DEFAULT_ATTRIBUTION = os.environ.get('WARDIVER_BASEMAP_ATTRIBUTION', '').strip()
MAP_DEFAULT_CACHE = os.environ.get('WARDIVER_BASEMAP_CACHE_ALLOWED', '0') == '1'
APP_VERSION = '2.21.0'
IMPORT_MAX_BYTES = 512 * 1024 * 1024
SYNC_DEFAULT_BASE_URL = os.environ.get('WARDIVER_SYNC_BASE_URL', 'https://wardriver.org').rstrip('/')
SYNC_HEALTH_PATH = os.environ.get('WARDIVER_SYNC_HEALTH_PATH', '/api/v1/health')
SYNC_IMPORT_PATH = os.environ.get('WARDIVER_SYNC_IMPORT_PATH', '/api/v1/imports')
SYNC_TIMEOUT = int(os.environ.get('WARDIVER_SYNC_TIMEOUT', '20'))
SYNC_CHUNK_SIZE = max(100, min(5000, int(os.environ.get('WARDIVER_SYNC_CHUNK_SIZE', '1000'))))
WIGLE_API_BASE = os.environ.get('WARDIVER_WIGLE_API_BASE', 'https://api.wigle.net').rstrip('/')
WIGLE_PROFILE_PATH = '/api/v2/profile/user'
WIGLE_UPLOAD_PATH = '/api/v2/file/upload'
WIGLE_TIMEOUT = int(os.environ.get('WARDIVER_WIGLE_TIMEOUT', '45'))
OUI_SOURCES = {
    'oui.csv': 'https://standards-oui.ieee.org/oui/oui.csv',
    'mam.csv': 'https://standards-oui.ieee.org/oui28/mam.csv',
    'oui36.csv': 'https://standards-oui.ieee.org/oui36/oui36.csv',
}
_OUI = {}
_OUI_LOCK = threading.Lock()

SCHEMA = '''
CREATE TABLE IF NOT EXISTS observations (
 id TEXT PRIMARY KEY,
 kind TEXT NOT NULL DEFAULT 'wifi',
 name TEXT,
 bssid TEXT,
 security TEXT,
 channel TEXT,
 rssi REAL,
 beacon_interval TEXT,
 radio_capabilities TEXT,
 information_elements TEXT,
 radio_fingerprint TEXT,
 latitude REAL NOT NULL,
 longitude REAL NOT NULL,
 seen_at TEXT,
 source TEXT,
 collection_method TEXT NOT NULL DEFAULT 'unknown',
 inferred_method TEXT NOT NULL DEFAULT 'unknown',
 inference_confidence REAL NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_obs_kind ON observations(kind);
CREATE INDEX IF NOT EXISTS idx_obs_source ON observations(source);
CREATE TABLE IF NOT EXISTS import_runs (
 id TEXT PRIMARY KEY,
 source TEXT NOT NULL,
 parsed INTEGER NOT NULL DEFAULT 0,
 imported INTEGER NOT NULL DEFAULT 0,
 rejected INTEGER NOT NULL DEFAULT 0,
 replaced INTEGER NOT NULL DEFAULT 0,
 imported_at TEXT NOT NULL,
 requested_method TEXT NOT NULL DEFAULT 'unknown',
 collection_method TEXT NOT NULL DEFAULT 'unknown',
 inferred_method TEXT NOT NULL DEFAULT 'unknown',
 inference_confidence REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_import_runs_time ON import_runs(imported_at);
CREATE TABLE IF NOT EXISTS hardware_profiles (
 id TEXT PRIMARY KEY,
 name TEXT NOT NULL,
 gear_json TEXT NOT NULL DEFAULT '[]',
 notes TEXT,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hardware_profiles_name ON hardware_profiles(name);

CREATE TABLE IF NOT EXISTS schema_migrations (
 name TEXT PRIMARY KEY,
 applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS devices (
 device_key TEXT PRIMARY KEY,
 kind TEXT NOT NULL DEFAULT 'wifi',
 name TEXT,
 bssid TEXT,
 bssid_norm TEXT,
 security TEXT,
 channel TEXT,
 latitude REAL NOT NULL,
 longitude REAL NOT NULL,
 min_lat REAL NOT NULL,
 max_lat REAL NOT NULL,
 min_lon REAL NOT NULL,
 max_lon REAL NOT NULL,
 first_seen TEXT,
 last_seen TEXT,
 observation_count INTEGER NOT NULL DEFAULT 0,
 source TEXT,
 collection_method TEXT NOT NULL DEFAULT 'unknown',
 session_id TEXT,
 session_name TEXT,
 hardware_profile_id TEXT,
 hardware_profile_name TEXT,
 manufacturer TEXT,
 hardware_type TEXT,
 hardware_label TEXT,
 hardware_confidence TEXT,
 hardware_reason TEXT,
 mac_local INTEGER NOT NULL DEFAULT 0,
 signal_hidden INTEGER NOT NULL DEFAULT 0,
 signal_open INTEGER NOT NULL DEFAULT 0,
 signal_mobile INTEGER NOT NULL DEFAULT 0,
 updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_devices_kind ON devices(kind);
CREATE INDEX IF NOT EXISTS idx_devices_collection ON devices(collection_method);
CREATE INDEX IF NOT EXISTS idx_devices_last_seen ON devices(last_seen);
CREATE INDEX IF NOT EXISTS idx_devices_source ON devices(source);
CREATE INDEX IF NOT EXISTS idx_devices_bssid_norm ON devices(bssid_norm);
CREATE INDEX IF NOT EXISTS idx_devices_hardware ON devices(hardware_type);
CREATE TABLE IF NOT EXISTS track_fixes (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 fix_key TEXT NOT NULL UNIQUE,
 drive_key TEXT NOT NULL,
 source TEXT,
 session_id TEXT,
 session_name TEXT,
 collection_method TEXT NOT NULL DEFAULT 'unknown',
 seen_at TEXT,
 seen_ts REAL NOT NULL DEFAULT 0,
 latitude REAL NOT NULL,
 longitude REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_track_drive_time ON track_fixes(drive_key,seen_ts);
CREATE INDEX IF NOT EXISTS idx_track_source_time ON track_fixes(source,seen_ts);
CREATE INDEX IF NOT EXISTS idx_track_collection_time ON track_fixes(collection_method,seen_ts);
CREATE TABLE IF NOT EXISTS drive_devices (
 drive_key TEXT NOT NULL,
 device_key TEXT NOT NULL,
 first_seen TEXT,
 PRIMARY KEY(drive_key,device_key)
);
CREATE INDEX IF NOT EXISTS idx_drive_devices_device ON drive_devices(device_key);

CREATE TABLE IF NOT EXISTS flock_reviews (
 device_key TEXT PRIMARY KEY,
 decision TEXT NOT NULL CHECK (decision IN ('confirmed','rejected')),
 notes TEXT,
 updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_flock_reviews_decision ON flock_reviews(decision);
CREATE TABLE IF NOT EXISTS flock_assessments (
 device_key TEXT PRIMARY KEY,
 candidate INTEGER NOT NULL DEFAULT 0,
 score INTEGER NOT NULL DEFAULT 0,
 auto_score INTEGER NOT NULL DEFAULT 0,
 level TEXT NOT NULL DEFAULT 'Possible',
 identity_signal INTEGER NOT NULL DEFAULT 0,
 evidence_json TEXT NOT NULL DEFAULT '[]',
 drive_count INTEGER NOT NULL DEFAULT 0,
 day_count INTEGER NOT NULL DEFAULT 0,
 location_spread_m REAL NOT NULL DEFAULT 0,
 persistence_days REAL NOT NULL DEFAULT 0,
 fingerprint_match INTEGER NOT NULL DEFAULT 0,
 updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_flock_assessments_score ON flock_assessments(candidate,score DESC);
CREATE INDEX IF NOT EXISTS idx_flock_assessments_level ON flock_assessments(level,score DESC);
CREATE TABLE IF NOT EXISTS flock_fingerprints (
 fingerprint TEXT NOT NULL,
 device_key TEXT NOT NULL,
 confirmed_at TEXT NOT NULL,
 PRIMARY KEY(fingerprint,device_key)
);
CREATE INDEX IF NOT EXISTS idx_flock_fingerprints_fp ON flock_fingerprints(fingerprint);
CREATE TABLE IF NOT EXISTS flock_photos (
 id TEXT PRIMARY KEY,
 device_key TEXT NOT NULL,
 storage_name TEXT NOT NULL UNIQUE,
 original_name TEXT,
 mime_type TEXT NOT NULL,
 byte_size INTEGER NOT NULL DEFAULT 0,
 caption TEXT,
 claimed INTEGER NOT NULL DEFAULT 0,
 claimed_at TEXT,
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_flock_photos_device ON flock_photos(device_key,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_flock_photos_claimed ON flock_photos(claimed,device_key);
CREATE TABLE IF NOT EXISTS flock_external_checks (
 device_key TEXT PRIMARY KEY,
 checked_at TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'unverified',
 authenticity_score INTEGER NOT NULL DEFAULT 0,
 external_score INTEGER NOT NULL DEFAULT 0,
 nearest_flock_distance_m REAL,
 nearest_alpr_distance_m REAL,
 osm_match_count INTEGER NOT NULL DEFAULT 0,
 eff_deployment_match INTEGER NOT NULL DEFAULT 0,
 result_json TEXT NOT NULL DEFAULT '{}',
 error TEXT
);
CREATE INDEX IF NOT EXISTS idx_flock_external_status ON flock_external_checks(status,authenticity_score DESC);
CREATE TABLE IF NOT EXISTS flock_source_notes (
 id TEXT PRIMARY KEY,
 device_key TEXT NOT NULL,
 source_type TEXT NOT NULL DEFAULT 'public_record',
 label TEXT NOT NULL,
 url TEXT,
 note TEXT,
 supports_flock INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_flock_source_notes_device ON flock_source_notes(device_key,created_at DESC);

CREATE TABLE IF NOT EXISTS drive_stats (
 drive_key TEXT PRIMARY KEY,
 label TEXT,
 first_t REAL NOT NULL DEFAULT 0,
 last_t REAL NOT NULL DEFAULT 0,
 files INTEGER NOT NULL DEFAULT 0,
 device_count INTEGER NOT NULL DEFAULT 0,
 distance_m REAL NOT NULL DEFAULT 0,
 accepted_segments INTEGER NOT NULL DEFAULT 0,
 rejected_segments INTEGER NOT NULL DEFAULT 0,
 walk_m REAL NOT NULL DEFAULT 0,
 walk_seconds REAL NOT NULL DEFAULT 0,
 walk_sessions INTEGER NOT NULL DEFAULT 0,
 has_warwalking INTEGER NOT NULL DEFAULT 0,
 updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_drive_stats_first ON drive_stats(first_t);
CREATE INDEX IF NOT EXISTS idx_drive_stats_distance ON drive_stats(distance_m DESC);
CREATE INDEX IF NOT EXISTS idx_drive_stats_devices ON drive_stats(device_count DESC);
CREATE INDEX IF NOT EXISTS idx_drive_stats_warwalk ON drive_stats(has_warwalking,first_t);
CREATE INDEX IF NOT EXISTS idx_drive_stats_files_first ON drive_stats(files,first_t);
CREATE TABLE IF NOT EXISTS drive_days (
 drive_key TEXT NOT NULL,
 day TEXT NOT NULL,
 collection_method TEXT NOT NULL DEFAULT 'unknown',
 first_t REAL NOT NULL DEFAULT 0,
 fixes INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(drive_key,day,collection_method)
);
CREATE INDEX IF NOT EXISTS idx_drive_days_day ON drive_days(day);
CREATE INDEX IF NOT EXISTS idx_drive_days_method_day ON drive_days(collection_method,day,first_t);
CREATE TABLE IF NOT EXISTS drive_coverage_cells (
 drive_key TEXT NOT NULL,
 cell_lat INTEGER NOT NULL,
 cell_lon INTEGER NOT NULL,
 first_t REAL NOT NULL DEFAULT 0,
 last_t REAL NOT NULL DEFAULT 0,
 fixes INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(drive_key,cell_lat,cell_lon)
);
CREATE INDEX IF NOT EXISTS idx_drive_coverage_lookup ON drive_coverage_cells(cell_lat,cell_lon,first_t);
CREATE TABLE IF NOT EXISTS coverage_cells (
 cell_lat INTEGER NOT NULL,
 cell_lon INTEGER NOT NULL,
 first_t REAL NOT NULL DEFAULT 0,
 last_t REAL NOT NULL DEFAULT 0,
 fixes INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(cell_lat,cell_lon)
);
CREATE INDEX IF NOT EXISTS idx_coverage_cells_first ON coverage_cells(first_t);
CREATE TABLE IF NOT EXISTS neighborhood_devices (
 neighborhood_key TEXT NOT NULL,
 device_key TEXT NOT NULL,
 first_seen TEXT,
 last_seen TEXT,
 observation_count INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(neighborhood_key,device_key)
);
CREATE INDEX IF NOT EXISTS idx_neighborhood_devices_device ON neighborhood_devices(device_key);
CREATE INDEX IF NOT EXISTS idx_neighborhood_devices_first ON neighborhood_devices(neighborhood_key,first_seen);
CREATE INDEX IF NOT EXISTS idx_neighborhood_devices_last ON neighborhood_devices(neighborhood_key,last_seen);
CREATE TABLE IF NOT EXISTS map_cells (
 level INTEGER NOT NULL,
 gx INTEGER NOT NULL,
 gy INTEGER NOT NULL,
 kind TEXT NOT NULL,
 collection_method TEXT NOT NULL,
 device_count INTEGER NOT NULL DEFAULT 0,
 observation_count INTEGER NOT NULL DEFAULT 0,
 sum_lat REAL NOT NULL DEFAULT 0,
 sum_lon REAL NOT NULL DEFAULT 0,
 open_count INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(level,gx,gy,kind,collection_method)
);
CREATE INDEX IF NOT EXISTS idx_map_cells_lookup ON map_cells(level,gx,gy,kind,collection_method);
CREATE TABLE IF NOT EXISTS source_scope_stats (
 source TEXT NOT NULL,
 session_id TEXT NOT NULL DEFAULT '',
 observation_count INTEGER NOT NULL DEFAULT 0,
 last_seen TEXT,
 updated_at TEXT NOT NULL,
 PRIMARY KEY(source,session_id)
);
CREATE INDEX IF NOT EXISTS idx_source_scope_stats_source ON source_scope_stats(source);

CREATE TABLE IF NOT EXISTS device_summary_counts (
 dimension TEXT NOT NULL,
 value TEXT NOT NULL,
 count INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(dimension,value)
);
CREATE INDEX IF NOT EXISTS idx_device_summary_dim_count ON device_summary_counts(dimension,count DESC);
CREATE TABLE IF NOT EXISTS device_discovery_months (
 month TEXT PRIMARY KEY,
 count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS device_bssid_parents (
 prefix TEXT PRIMARY KEY,
 count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS route_history (
 id TEXT PRIMARY KEY,
 city TEXT,
 requested_miles REAL,
 distance_m REAL,
 duration_s REAL,
 geometry_json TEXT,
 steps_json TEXT,
 created_at TEXT NOT NULL,
 completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_route_history_time ON route_history(created_at);
CREATE TABLE IF NOT EXISTS sync_settings (
 id INTEGER PRIMARY KEY CHECK (id=1),
 enabled INTEGER NOT NULL DEFAULT 0,
 base_url TEXT NOT NULL DEFAULT 'https://wardriver.org',
 api_token TEXT,
 updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_runs (
 id TEXT PRIMARY KEY,
 source TEXT NOT NULL,
 remote_id TEXT,
 status TEXT NOT NULL,
 records_total INTEGER NOT NULL DEFAULT 0,
 records_sent INTEGER NOT NULL DEFAULT 0,
 records_excluded INTEGER NOT NULL DEFAULT 0,
 privacy_profile TEXT,
 privacy_rating TEXT,
 error TEXT,
 created_at TEXT NOT NULL,
 completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_sync_runs_time ON sync_runs(created_at);
CREATE TABLE IF NOT EXISTS sync_privacy_settings (
 id INTEGER PRIMARY KEY CHECK (id=1),
 profile TEXT NOT NULL DEFAULT 'balanced',
 config_json TEXT NOT NULL DEFAULT '{}',
 secret TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS wigle_settings (
 id INTEGER PRIMARY KEY CHECK (id=1),
 enabled INTEGER NOT NULL DEFAULT 0,
 api_token TEXT,
 donate INTEGER NOT NULL DEFAULT 0,
 updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS wigle_runs (
 id TEXT PRIMARY KEY,
 source TEXT NOT NULL,
 status TEXT NOT NULL,
 records_total INTEGER NOT NULL DEFAULT 0,
 remote_id TEXT,
 error TEXT,
 created_at TEXT NOT NULL,
 completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_wigle_runs_time ON wigle_runs(created_at);
CREATE TABLE IF NOT EXISTS map_settings (
 id INTEGER PRIMARY KEY CHECK (id=1),
 provider TEXT NOT NULL DEFAULT 'none',
 tile_url_template TEXT NOT NULL DEFAULT '',
 attribution TEXT NOT NULL DEFAULT '',
 cache_allowed INTEGER NOT NULL DEFAULT 0,
 updated_at TEXT NOT NULL
);
'''

# Synthetic demonstration observations; not a real field survey.
SAMPLE = [
 ('wifi','Example WiFi 1','02:00:00:00:00:01','WPA2','6',10.001,-10.001),
 ('alpr','Example Camera',None,None,None,10.002,-10.002),
 ('wifi','Example WiFi 2','02:00:00:00:00:02','WPA3','149',10.003,-10.003),
 ('wifi','Example WiFi 3','02:00:00:00:00:03','WPA2','11',10.004,-10.004),
 ('alpr','Example ALPR',None,None,None,10.005,-10.005),
 ('wifi','Example WiFi 4','02:00:00:00:00:04','WPA2','1',10.006,-10.006),
]




def _v29_apply_sqlite_pragmas(conn):
    for stmt in (
        'pragma synchronous=NORMAL',
        'pragma temp_store=MEMORY',
        'pragma cache_size=-32768',
        'pragma mmap_size=536870912',
        'pragma busy_timeout=5000',
        'pragma wal_autocheckpoint=4000',
        'pragma journal_size_limit=134217728',
        'pragma foreign_keys=ON',
    ):
        try: conn.execute(stmt)
        except Exception: pass


def _v29_ensure_spatial_index(conn, rebuild=False):
    try:
        conn.execute('CREATE VIRTUAL TABLE IF NOT EXISTS observation_rtree USING rtree(rowid,min_lat,max_lat,min_lon,max_lon)')
        conn.executescript(
          'CREATE TRIGGER IF NOT EXISTS observation_rtree_insert AFTER INSERT ON observations BEGIN '
          'INSERT OR REPLACE INTO observation_rtree(rowid,min_lat,max_lat,min_lon,max_lon) '
          'VALUES(new.rowid,new.latitude,new.latitude,new.longitude,new.longitude); END; '
          'CREATE TRIGGER IF NOT EXISTS observation_rtree_delete AFTER DELETE ON observations BEGIN '
          'DELETE FROM observation_rtree WHERE rowid=old.rowid; END; '
          'CREATE TRIGGER IF NOT EXISTS observation_rtree_update AFTER UPDATE OF latitude,longitude ON observations BEGIN '
          'INSERT OR REPLACE INTO observation_rtree(rowid,min_lat,max_lat,min_lon,max_lon) '
          'VALUES(new.rowid,new.latitude,new.latitude,new.longitude,new.longitude); END;'
        )
        if rebuild:
            conn.execute('delete from observation_rtree')
            conn.execute('insert into observation_rtree(rowid,min_lat,max_lat,min_lon,max_lon) select rowid,latitude,latitude,longitude,longitude from observations')
            conn.commit()
            n=conn.execute('select count(*) from observation_rtree').fetchone()[0]
            print(f'v2.11 observation RTree built: {n:,} rows', flush=True)
    except Exception as exc:
        print('v2.11 observation RTree warning:', exc, flush=True)


def _v29_table_columns(conn, table):
    try: return {r[1] for r in conn.execute(f'pragma table_info({table})').fetchall()}
    except Exception: return set()

def normalize_mac(v):
    raw = re.sub(r'[^0-9A-Fa-f]', '', str(v or '')).upper()
    if len(raw) < 12:
        return raw
    return raw[:12]

def is_local_mac(mac):
    raw = normalize_mac(mac)
    if len(raw) < 2:
        return False
    try:
        return bool(int(raw[:2], 16) & 0x02)
    except ValueError:
        return False

def _load_oui_file(path, target):
    try:
        with path.open('r', encoding='utf-8-sig', errors='replace', newline='') as f:
            reader = csv.DictReader(f)
            for row in reader:
                assignment = row.get('Assignment') or row.get('assignment') or row.get('Registry Assignment') or ''
                org = row.get('Organization Name') or row.get('organization') or row.get('Organization') or ''
                prefix = re.sub(r'[^0-9A-Fa-f]', '', assignment).upper()
                org = str(org).strip()
                if prefix and org:
                    target[prefix] = org
    except Exception as e:
        print(f'OUI load warning for {path}: {e}', flush=True)

def load_oui_database():
    target = {}
    _load_oui_file(ROOT / 'oui_fallback.csv', target)
    OUI_DIR.mkdir(parents=True, exist_ok=True)
    for name in ('oui.csv', 'mam.csv', 'oui36.csv'):
        path = OUI_DIR / name
        if path.exists():
            _load_oui_file(path, target)
    with _OUI_LOCK:
        _OUI.clear(); _OUI.update(target)
    print(f'Loaded {len(target):,} local MAC vendor prefixes', flush=True)

def update_oui_database():
    if not OUI_AUTO_UPDATE:
        return
    OUI_DIR.mkdir(parents=True, exist_ok=True)
    changed = False
    now = time.time()
    for name, url in OUI_SOURCES.items():
        path = OUI_DIR / name
        try:
            if path.exists() and now - path.stat().st_mtime < OUI_MAX_AGE_DAYS * 86400:
                continue
            req = urllib.request.Request(url, headers={'User-Agent':'WardriverLocal/1.2'})
            with urllib.request.urlopen(req, timeout=6) as r:
                data = r.read()
            if len(data) < 1000:
                raise ValueError('download too small')
            tmp = path.with_suffix(path.suffix + '.tmp')
            tmp.write_bytes(data); tmp.replace(path); changed = True
            print(f'Updated local IEEE vendor database: {name}', flush=True)
        except Exception as e:
            print(f'OUI update skipped for {name}: {e}', flush=True)
    if changed:
        load_oui_database()

def manufacturer_for_mac(mac):
    raw = normalize_mac(mac)
    if len(raw) < 6:
        return ''
    if is_local_mac(raw):
        return 'Private / randomized'
    with _OUI_LOCK:
        # IEEE MA-S (36-bit), MA-M (28-bit), then MA-L/OUI (24-bit).
        for n in (9, 7, 6):
            vendor = _OUI.get(raw[:n])
            if vendor:
                return vendor
    return 'Unknown'

# Observed Wi-Fi prefixes used by Flock Safety camera infrastructure. Some are
# OEM prefixes, so prefix-only matches are medium confidence rather than being
# presented as direct IEEE vendor assignments to Flock Safety.
FLOCK_WIFI_PREFIXES = {
    '70C94E','3C9180','D8F3BC','803049','B83532','145AFC','744CA1','083A88',
    '9C2F9D','C03532','940853','E4AAEA','F46ADD','F8A2D6','24B2B9','00F48D',
    'D03957','E8D0FC','E04F43','B81EA4','700894','588E81','EC1BBD','3C71BF',
    '5800E3','9035EA','5C93A2','646E69','4827EA','A4CF12','826BF2',
}

def classify_flock(name=None, manufacturer=None, bssid=None):
    # A stateless name/OUI match is a review hint, never a vendor identification.
    text=' '.join(str(x or '').strip().lower() for x in (name,manufacturer))
    hint=bool(re.search(r'\bflock\b',text) or normalize_mac(bssid)[:6] in FLOCK_WIFI_PREFIXES)
    return {'is_flock':0,'confidence':'possible' if hint else '',
            'reason':'Unverified Flock candidate; name or shared hardware prefix is insufficient' if hint else ''}


HARDWARE_TYPES = {
    'refrigerator': ('Refrigerators / freezers', 'FR'),
    'oven': ('Ovens / ranges', 'OV'),
    'appliance': ('Other appliances', 'APP'),
    'robot': ('Robot vacuums / cleaners', 'BOT'),
    'camera': ('Cameras / doorbells / security', 'CAM'),
    'printer': ('Printers / scanners', 'PRN'),
    'tv': ('TVs / streaming devices', 'TV'),
    'speaker': ('Speakers / voice assistants', 'SPK'),
    'climate': ('Thermostats / HVAC', 'HVAC'),
    'access': ('Locks / garage / access control', 'LOCK'),
    'energy': ('EV chargers / solar / energy', 'EV'),
    'network': ('Routers / mesh / network gear', 'NET'),
    'commercial': ('Commercial / industrial / POS', 'POS'),
    'vehicle': ('Vehicles / mobile hotspots', 'CAR'),
    'embedded': ('Embedded / maker / generic IoT', 'IOT'),
    'setup': ('Setup / provisioning APs', 'SET'),
}

def _contains_any(text, needles):
    return any(n in text for n in needles)

def infer_hardware(name, vendor, bssid=None):
    n = str(name or '').strip().lower()
    v = str(vendor or '').strip().lower()
    def hit(kind, confidence, reason):
        label, short = HARDWARE_TYPES[kind]
        return {'hardware_type':kind,'hardware_label':label,'hardware_short':short,'hardware_confidence':confidence,'hardware_reason':reason}

    # Explicit product/SSID language gets the highest confidence.
    if _contains_any(n, ('refrigerator','fridge','freezer','smartfridge','smart fridge')): return hit('refrigerator','high','SSID/name identifies a refrigerator or freezer')
    if _contains_any(n, ('oven','range','cooktop','stove')): return hit('oven','high','SSID/name identifies an oven, range, cooktop, or stove')
    if _contains_any(n, ('dishwasher','washer','dryer','laundry','airfryer','air fryer','microwave','keurig','coffee maker','smart appliance','homeconnect')): return hit('appliance','high','SSID/name identifies a connected appliance')
    if _contains_any(n, ('roomba','irobot','roborock','ecovacs','deebot','dreame','robot vacuum','robotvac')): return hit('robot','high','SSID/name identifies a robot cleaner')
    if _contains_any(n, ('ring-','ring_','ring setup','doorbell','camera','cam-','cam_','arlo','wyze','reolink','eufycam','blink','amcrest','hikvision','dahua','lorex','nestcam','unifi protect')): return hit('camera','high','SSID/name identifies camera, doorbell, or security hardware')
    if _contains_any(n, ('direct-','printer','epson','brother','laserjet','officejet','deskjet','envy ','pixma','xerox','lexmark')) and not _contains_any(n, ('direct-tv','directv')): return hit('printer','high','SSID/name resembles a printer or Wi-Fi Direct print network')
    if _contains_any(n, ('roku','chromecast','firetv','fire tv','appletv','apple tv','bravia','webos','smarttv','smart tv','vizio','shield tv')): return hit('tv','high','SSID/name identifies TV or streaming hardware')
    if _contains_any(n, ('sonos','homepod','alexa','amazon echo','echo-','echo_','google home','nest audio','smart speaker')): return hit('speaker','high','SSID/name identifies speaker or voice-assistant hardware')
    if _contains_any(n, ('ecobee','thermostat','nest thermostat','honeywell home','sensi','hvac','daikin','mini split','minisplit')): return hit('climate','high','SSID/name identifies thermostat or HVAC equipment')
    if _contains_any(n, ('myq','liftmaster','chamberlain','garage','august','schlage','yale lock','smart lock','door lock','access control')): return hit('access','high','SSID/name identifies lock, garage, or access-control hardware')
    if _contains_any(n, ('wall connector','wallbox','chargepoint','juicebox','evse','ev charger','enphase','solaredge','solar edge','powerwall','inverter','emporia')): return hit('energy','high','SSID/name identifies EV charging, solar, or energy hardware')
    if _contains_any(n, ('eero','orbi','deco_','deco-','unifi','ubnt','mesh','router','gateway','access point','extender','repeater')): return hit('network','high','SSID/name identifies networking or mesh hardware')
    if _contains_any(n, ('ingenico','verifone','clover','square terminal','pos-','pos_','kiosk','brightsign','digital signage','cradlepoint')): return hit('commercial','high','SSID/name identifies commercial, POS, kiosk, or signage hardware')
    if _contains_any(n, ('tesla','rivian','ford sync','carplay','vehicle hotspot','car hotspot','dashcam')): return hit('vehicle','medium','SSID/name resembles vehicle or mobile hotspot hardware')
    if _contains_any(n, ('esp_','esp-','esp32','esp8266','espressif','tasmota','shelly','sonoff','tuya','smartlife','smart life','kasa','wemo','aqara','zigbee gateway','matter hub')): return hit('embedded','high','SSID/name identifies embedded or smart-home IoT hardware')
    if _contains_any(n, ('setup','provision','configure','config-','config_','pairing','smartconfig','iot_','iot-')): return hit('setup','medium','SSID/name resembles a temporary setup or provisioning network')

    # Specialized manufacturers can provide useful medium-confidence hints. Avoid
    # broad consumer brands (Samsung/LG/Apple/etc.) that make many device classes.
    vendor_rules = [
        ('camera', ('ring llc','arlo technologies','wyze labs','reolink','amcrest','hikvision','hangzhou hikvision','dahua','eufy security','blink')), 
        ('robot', ('irobot','roborock','ecovacs','dreame')),
        ('speaker', ('sonos',)),
        ('climate', ('ecobee',)),
        ('energy', ('chargepoint','enphase','solaredge','wallbox','emporia')),
        ('access', ('chamberlain','liftmaster','august home','schlage')),
        ('commercial', ('ingenico','verifone','brightsign','cradlepoint')),
        ('embedded', ('espressif', 'shelly', 'tuya')),
        ('network', ('ubiquiti','eero','aruba networks','ruckus','cisco meraki')),
    ]
    for kind, names in vendor_rules:
        if _contains_any(v, names):
            return hit(kind,'medium',f'MAC vendor suggests {HARDWARE_TYPES[kind][0].lower()}')
    return {'hardware_type':'','hardware_label':'','hardware_short':'','hardware_confidence':'','hardware_reason':''}

load_oui_database()
if OUI_AUTO_UPDATE:
    threading.Thread(target=update_oui_database, daemon=True).start()


# v2.10 summary cache: expensive aggregate work stays server-side and is reused
# until the SQLite database/WAL files change.
_PERF_CACHE = {}
_PERF_CACHE_LOCK = threading.Lock()
_PERF_BUILD_LOCKS = {}


def _perf_db_signature():
    sig=[]
    for p in (DB_PATH, Path(str(DB_PATH)+'-wal')):
        try:
            st=p.stat(); sig.append((st.st_mtime_ns,st.st_size))
        except FileNotFoundError:
            sig.append((0,0))
    return tuple(sig)

def _cached_perf(key, builder):
    sig=_perf_db_signature()
    with _PERF_CACHE_LOCK:
        hit=_PERF_CACHE.get(key)
        if hit and hit[0]==sig:return hit[1]
        lock=_PERF_BUILD_LOCKS.setdefault(key,threading.Lock())
    with lock:
        # Another request may have completed the build while we waited.
        sig=_perf_db_signature()
        with _PERF_CACHE_LOCK:
            hit=_PERF_CACHE.get(key)
            if hit and hit[0]==sig:return hit[1]
        value=builder()
        with _PERF_CACHE_LOCK:_PERF_CACHE[key]=(sig,value)
        return value


def _plausible_obs_timestamp(ts):
    try:ts=float(ts)
    except Exception:return 0.0
    # 802.11 dates before 1997 are not credible Wardriver discovery dates.
    # Also reject clocks more than one day into the future.
    earliest=datetime(1997,1,1,tzinfo=timezone.utc).timestamp()
    latest=(datetime.now(timezone.utc)+timedelta(days=1)).timestamp()
    return ts if earliest<=ts<=latest else 0.0

def _parse_obs_time(value):
    if not value:return 0.0
    try:
        v=str(value).strip().replace('Z','+00:00')
        dt=datetime.fromisoformat(v)
        if dt.tzinfo is None:dt=dt.replace(tzinfo=timezone.utc)
        return _plausible_obs_timestamp(dt.timestamp())
    except Exception:
        try:
            dt=datetime.strptime(str(value)[:19],'%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
            return _plausible_obs_timestamp(dt.timestamp())
        except Exception:return 0.0

def _haversine_km(a_lat,a_lon,b_lat,b_lon):
    try:
        a_lat=float(a_lat);a_lon=float(a_lon);b_lat=float(b_lat);b_lon=float(b_lon)
    except Exception:return 0.0
    r=6371.0088
    p1=math.radians(a_lat);p2=math.radians(b_lat)
    dp=math.radians(b_lat-a_lat);dl=math.radians(b_lon-a_lon)
    x=math.sin(dp/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return r*2*math.atan2(math.sqrt(x),math.sqrt(max(0.0,1-x)))


FLOCK_SCORE_VERSION='v2-conservative'

def _flock_prefix_info(bssid):
    raw=normalize_mac(bssid);prefix=raw[:6] if len(raw)>=6 else ''
    return prefix, prefix in FLOCK_WIFI_PREFIXES

def _flock_photo_detect(data,mime_hint=''):
    head=bytes(data[:16])
    if head.startswith(b'\xff\xd8\xff'):return 'image/jpeg','.jpg'
    if head.startswith(b'\x89PNG\r\n\x1a\n'):return 'image/png','.png'
    if len(head)>=12 and head[:4]==b'RIFF' and head[8:12]==b'WEBP':return 'image/webp','.webp'
    raise ValueError('Photo must be a JPEG, PNG, or WebP image.')

def _flock_photo_public_row(r):
    x=dict(r)
    x['claimed']=1 if int(x.get('claimed') or 0) else 0
    x['url']='/api/flock/photo/'+urllib.parse.quote(str(x.get('id') or ''))
    x.pop('storage_name',None)
    return x

def _flock_photo_rows(conn,device_key):
    return [_flock_photo_public_row(r) for r in conn.execute("""select id,device_key,storage_name,original_name,mime_type,byte_size,caption,claimed,claimed_at,created_at
                                                                from flock_photos where device_key=? order by created_at desc,id desc""",(device_key,))]

def _flock_photo_device_eligible(conn,device_key):
    r=conn.execute("""select a.candidate,a.score,a.identity_signal,rv.decision from flock_assessments a
                      left join flock_reviews rv on rv.device_key=a.device_key where a.device_key=?""",(device_key,)).fetchone()
    return bool(r and (r['decision']=='confirmed' or (r['decision']!='rejected' and r['identity_signal'] and r['score']>=80)))

def _flock_photo_documented(conn,device_key):
    return bool(conn.execute('select 1 from flock_photos where device_key=? and claimed=1 limit 1',(device_key,)).fetchone())

def flock_photo_add(device_key,data,filename='',mime_hint='',caption='',claimed=False):
    device_key=str(device_key or '').strip();filename=Path(str(filename or 'photo')).name[:180]
    caption=str(caption or '').strip()[:500];claimed=bool(claimed)
    if not device_key:raise ValueError('device_key is required')
    if not data:raise ValueError('Choose a photo to upload.')
    if len(data)>FLOCK_PHOTO_MAX_BYTES:raise ValueError(f'Photo is too large. Maximum size is {FLOCK_PHOTO_MAX_BYTES//(1024*1024)} MB.')
    mime,ext=_flock_photo_detect(data,mime_hint)
    photo_id=str(uuid.uuid4());storage_name=photo_id+ext;now=datetime.now(timezone.utc).isoformat()
    FLOCK_PHOTO_DIR.mkdir(parents=True,exist_ok=True)
    final_path=(FLOCK_PHOTO_DIR/storage_name).resolve()
    if final_path.parent!=FLOCK_PHOTO_DIR.resolve():raise ValueError('invalid photo path')
    conn=db();xp_earned=0
    try:
        if not conn.execute('select 1 from devices where device_key=?',(device_key,)).fetchone():raise ValueError('device not found')
        if not conn.execute('select 1 from flock_assessments where device_key=? and candidate=1',(device_key,)).fetchone():raise ValueError('photo uploads are limited to Flock Review candidates')
        was_documented=_flock_photo_documented(conn,device_key)
        tmp=FLOCK_PHOTO_DIR/(storage_name+'.tmp')
        tmp.write_bytes(data);os.chmod(tmp,0o600);tmp.replace(final_path)
        try:
            conn.execute("""insert into flock_photos(id,device_key,storage_name,original_name,mime_type,byte_size,caption,claimed,claimed_at,created_at)
                            values (?,?,?,?,?,?,?,?,?,?)""",
                         (photo_id,device_key,storage_name,filename,mime,len(data),caption,1 if claimed else 0,now if claimed else None,now))
            conn.commit()
        except Exception:
            try:final_path.unlink(missing_ok=True)
            except Exception:pass
            raise
        if claimed and not was_documented and _flock_photo_device_eligible(conn,device_key):xp_earned=FLOCK_PHOTO_XP
        row=conn.execute('select * from flock_photos where id=?',(photo_id,)).fetchone()
        documented=_flock_photo_documented(conn,device_key)
        return {'ok':True,'photo':_flock_photo_public_row(row),'xp_earned':xp_earned,'device_photo_documented':documented,'xp_each':FLOCK_PHOTO_XP}
    finally:conn.close()

def flock_photo_claim(photo_id,claimed=True):
    photo_id=str(photo_id or '').strip();claimed=(str(claimed).strip().lower() in ('1','true','yes','on')) if isinstance(claimed,str) else bool(claimed)
    if not photo_id:raise ValueError('photo_id is required')
    conn=db()
    try:
        row=conn.execute('select * from flock_photos where id=?',(photo_id,)).fetchone()
        if not row:raise ValueError('photo not found')
        key=row['device_key'];was_documented=_flock_photo_documented(conn,key);now=datetime.now(timezone.utc).isoformat()
        conn.execute('update flock_photos set claimed=?,claimed_at=? where id=?',(1 if claimed else 0,now if claimed else None,photo_id));conn.commit()
        documented=_flock_photo_documented(conn,key)
        xp_earned=FLOCK_PHOTO_XP if claimed and not was_documented and documented and _flock_photo_device_eligible(conn,key) else 0
        xp_lost=FLOCK_PHOTO_XP if (not claimed) and was_documented and not documented and _flock_photo_device_eligible(conn,key) else 0
        updated=conn.execute('select * from flock_photos where id=?',(photo_id,)).fetchone()
        return {'ok':True,'photo':_flock_photo_public_row(updated),'xp_earned':xp_earned,'xp_lost':xp_lost,
                'device_photo_documented':documented,'xp_each':FLOCK_PHOTO_XP}
    finally:conn.close()

def flock_photo_delete(photo_id):
    photo_id=str(photo_id or '').strip()
    if not photo_id:raise ValueError('photo_id is required')
    conn=db()
    try:
        row=conn.execute('select * from flock_photos where id=?',(photo_id,)).fetchone()
        if not row:raise ValueError('photo not found')
        key=row['device_key'];was_documented=_flock_photo_documented(conn,key);eligible=_flock_photo_device_eligible(conn,key)
        conn.execute('delete from flock_photos where id=?',(photo_id,));conn.commit()
        documented=_flock_photo_documented(conn,key)
        path=(FLOCK_PHOTO_DIR/row['storage_name']).resolve()
        if path.parent==FLOCK_PHOTO_DIR.resolve():
            try:path.unlink(missing_ok=True)
            except Exception:pass
        return {'ok':True,'deleted':photo_id,'device_key':key,'device_photo_documented':documented,
                'xp_lost':FLOCK_PHOTO_XP if was_documented and not documented and eligible else 0,'xp_each':FLOCK_PHOTO_XP}
    finally:conn.close()

def flock_photo_file(photo_id):
    conn=db()
    try:row=conn.execute('select id,storage_name,mime_type from flock_photos where id=?',(str(photo_id or ''),)).fetchone()
    finally:conn.close()
    if not row:raise ValueError('photo not found')
    path=(FLOCK_PHOTO_DIR/row['storage_name']).resolve()
    if path.parent!=FLOCK_PHOTO_DIR.resolve() or not path.is_file():raise ValueError('photo file is missing')
    return path,row['mime_type']

def _http_text(url, data=None, headers=None, timeout=None):
    hdr={'User-Agent':f'Wardriver-Local/{APP_VERSION} (+https://wardriver.org)'}
    if headers:hdr.update(headers)
    req=urllib.request.Request(url,data=data,headers=hdr,method='POST' if data is not None else 'GET')
    with urllib.request.urlopen(req,timeout=timeout or FLOCK_VERIFY_TIMEOUT) as r:
        return r.read().decode('utf-8','replace'),getattr(r,'status',200)

def _norm_text(v):return re.sub(r'\s+',' ',str(v or '').strip())

def _osm_element_position(el):
    if el.get('type')=='node' and el.get('lat') is not None and el.get('lon') is not None:return float(el['lat']),float(el['lon'])
    c=el.get('center') or {}
    if c.get('lat') is not None and c.get('lon') is not None:return float(c['lat']),float(c['lon'])
    return None,None

def _osm_flock_strength(tags):
    tags=tags or {};strong=[];weak=[]
    for key in ('manufacturer','brand','model','description','name'):
        val=_norm_text(tags.get(key))
        if re.search(r'\bflock(?:\s+safety)?\b',val,re.I):strong.append(f'{key}={val}')
    # operator=Flock Safety exists in older/community tagging but is not a
    # reliable manufacturer assertion. Keep it visible without treating it as
    # independent strong vendor evidence.
    op=_norm_text(tags.get('operator'))
    if re.search(r'\bflock(?:\s+safety)?\b',op,re.I):weak.append(f'operator={op}')
    return strong,weak

def _osm_external_matches(lat,lon,radius_m):
    q=f"""[out:json][timeout:{min(20,FLOCK_VERIFY_TIMEOUT)}];(
      nwr(around:{int(radius_m)},{lat:.7f},{lon:.7f})["man_made"="surveillance"]["surveillance:type"~"^(ALPR|ANPR)$",i];
      nwr(around:{int(radius_m)},{lat:.7f},{lon:.7f})["surveillance"="license_plate_scanner"];
    );out center tags;"""
    body=urllib.parse.urlencode({'data':q}).encode()
    errors=[];payload=None;used=None
    for base in FLOCK_OVERPASS_URLS:
        try:
            text,_=_http_text(base,body,{'Content-Type':'application/x-www-form-urlencoded'},FLOCK_VERIFY_TIMEOUT)
            payload=json.loads(text);used=base;break
        except Exception as e:errors.append(f'{base}: {e}')
    if payload is None:raise RuntimeError('OpenStreetMap/Overpass lookup failed: '+' | '.join(errors[:2]))
    matches=[]
    for el in payload.get('elements') or []:
        elat,elon=_osm_element_position(el)
        if elat is None:continue
        dist=_haversine_km(float(lat),float(lon),elat,elon)*1000.0;tags=el.get('tags') or {};strong,weak=_osm_flock_strength(tags)
        eid=f"{el.get('type','node')}/{el.get('id')}";manufacturer=_norm_text(tags.get('manufacturer') or tags.get('brand'))
        matches.append({'source_family':'osm-deflock','source':'OpenStreetMap / DeFlock','element_id':eid,'distance_m':round(dist,1),
                        'latitude':elat,'longitude':elon,'flock_tagged':bool(strong),'flock_strong_tags':strong,'flock_weak_tags':weak,
                        'manufacturer':manufacturer,'model':_norm_text(tags.get('model')),'operator':_norm_text(tags.get('operator')),
                        'direction':_norm_text(tags.get('direction') or tags.get('camera:direction')),'mount':_norm_text(tags.get('camera:mount')),
                        'surveillance_zone':_norm_text(tags.get('surveillance:zone')),
                        'osm_url':f'https://www.openstreetmap.org/{eid}',
                        'deflock_url':f'https://maps.deflock.org/?lat={elat:.6f}&lng={elon:.6f}&zoom=18',
                        'note':'DeFlock consumes OpenStreetMap ALPR data; these are one evidence family, not two independent confirmations.'})
    matches.sort(key=lambda x:x['distance_m'])
    return matches,used

def _reverse_place(lat,lon):
    url=GEOCODER_URL+'/reverse?'+urllib.parse.urlencode({'format':'jsonv2','lat':f'{lat:.7f}','lon':f'{lon:.7f}','zoom':'10','addressdetails':'1'})
    try:
        text,_=_http_text(url,None,None,GEOCODER_TIMEOUT);j=json.loads(text);a=j.get('address') or {}
        city=a.get('city') or a.get('town') or a.get('village') or a.get('municipality') or a.get('county') or ''
        state=a.get('state') or '';code=(a.get('ISO3166-2-lvl4') or a.get('ISO3166-2-lvl6') or '').split('-')[-1]
        state_short=code if len(code)<=3 else state
        label=', '.join(x for x in (city,state_short) if x)
        return {'city':city,'state':state,'state_short':state_short,'county':a.get('county') or '','country':a.get('country') or '','label':label,'display_name':j.get('display_name') or ''}
    except Exception as e:return {'error':str(e),'label':''}

def _eff_atlas_lookup(place):
    label=_norm_text((place or {}).get('label'))
    if not label:return {'checked':False,'match':False,'reason':'No city/state resolved for regional deployment lookup.'}
    url=FLOCK_EFF_BASE_URL+'/search?'+urllib.parse.urlencode({'location':label})
    try:
        text,_=_http_text(url,None,None,FLOCK_VERIFY_TIMEOUT)
        stripped=html_lib.unescape(re.sub(r'<[^>]+>',' ',text));stripped=re.sub(r'\s+',' ',stripped)
        records=[]
        pat=re.compile(r'Agency:\s*(.*?)\s+State:\s*(.*?)\s+City:\s*(.*?)\s+County:\s*(.*?)\s+Technology:\s*(.*?)\s+Vendor:\s*(.*?)(?=Agency:|$)',re.I)
        for m in pat.finditer(stripped):
            agency,state,city,county,technology,vendor=[_norm_text(x) for x in m.groups()]
            if 'license plate' not in technology.lower():continue
            if 'flock' not in vendor.lower():continue
            exact_city=bool(place.get('city') and city and city.casefold()==str(place.get('city')).casefold())
            records.append({'agency':agency,'city':city,'state':state,'county':county,'technology':technology,'vendor':vendor,'exact_city':exact_city})
        # Some page templates repeat data differently. A conservative fallback
        # only establishes regional corroboration, never exact-camera evidence.
        page_match=('Flock Safety' in stripped and 'Automated License Plate Readers' in stripped)
        return {'checked':True,'match':bool(records or page_match),'exact_city':any(r['exact_city'] for r in records),'records':records[:12],
                'location':label,'url':url,'source':'EFF Atlas of Surveillance',
                'note':'Atlas records document agency/city deployments, not the identity of one exact roadside camera.'}
    except Exception as e:return {'checked':False,'match':False,'location':label,'url':url,'error':str(e),'source':'EFF Atlas of Surveillance'}

def _flock_source_notes(conn,device_key):
    return [dict(r) for r in conn.execute('select id,source_type,label,url,note,supports_flock,created_at from flock_source_notes where device_key=? order by created_at desc',(device_key,))]

def _cached_external_verification(conn,device_key):
    r=conn.execute('select * from flock_external_checks where device_key=?',(device_key,)).fetchone()
    if not r:return None
    x=dict(r)
    try:result=json.loads(x.pop('result_json') or '{}')
    except:result={}
    x.update(result);x['cached']=True;return x

def _verification_authenticity(device,review,photos,osm_matches,eff,user_sources):
    local=int(device.get('flock_score') or 0);decision=(review or {}).get('decision') if review else None
    claimed=any(int(p.get('claimed') or 0) for p in photos or [])
    supportive=[x for x in user_sources or [] if int(x.get('supports_flock') or 0)]
    flock_matches=[m for m in osm_matches if m.get('flock_tagged')];nearest_flock=flock_matches[0]['distance_m'] if flock_matches else None
    nearest_alpr=osm_matches[0]['distance_m'] if osm_matches else None
    if nearest_flock is not None:
        external=100 if nearest_flock<=30 else 90 if nearest_flock<=75 else 75 if nearest_flock<=150 else 55
    elif nearest_alpr is not None:
        external=40 if nearest_alpr<=50 else 25 if nearest_alpr<=150 else 15
    else:external=0
    if eff.get('match'):external=min(100,external+(15 if eff.get('exact_city') else 8))
    # Evidence-confidence score, deliberately not an assertion of cryptographic
    # authenticity. External exact-location data gets substantial weight.
    authenticity=round(local*.55+external*.35+(8 if claimed else 0)+(10 if supportive else 0))
    if decision=='confirmed':authenticity=max(authenticity,85)
    authenticity=max(0,min(100,authenticity))
    if decision=='rejected':
        status='conflict' if (nearest_flock is not None or supportive) else 'rejected'
        label='Conflict: external evidence disagrees with local rejection' if status=='conflict' else 'Rejected locally'
    elif nearest_flock is not None and nearest_flock<=75 and (decision=='confirmed' or local>=55):status='verified_multi_source';label='Verified by multiple evidence families'
    elif nearest_flock is not None and nearest_flock<=150 and local>=25:status='externally_corroborated';label='Externally corroborated'
    elif decision=='confirmed' and claimed:status='locally_verified';label='Locally verified with claimed photo'
    elif decision=='confirmed':status='locally_confirmed';label='Manually confirmed locally'
    elif supportive and local>=55:status='public_record_corroborated';label='Corroborated by a user-reviewed public source'
    elif nearest_flock is not None:status='external_match';label='External Flock record nearby'
    elif eff.get('match') and local>=55:status='deployment_corroborated';label='Flock deployment corroborated in this area'
    elif local>=80:status='high_local';label='High local confidence; no exact external record found'
    else:status='unverified';label='Not externally verified yet'
    families=[]
    if local>0:families.append({'key':'local-radio','name':'Wardriver local evidence','independent':True,'detail':f'Local Flock score {local}/100.'})
    manu=str(device.get('manufacturer') or '')
    prefix,_=_flock_prefix_info(device.get('bssid'))
    if 'flock' in manu.lower() or prefix in FLOCK_WIFI_PREFIXES:families.append({'key':'vendor-radio','name':'Vendor/OUI identity','independent':True,'detail':manu or 'Known Flock-associated radio prefix.'})
    if osm_matches:families.append({'key':'osm-deflock','name':'OpenStreetMap / DeFlock location record','independent':True,'detail':f'{len(osm_matches)} ALPR record(s) within verification radius.'})
    if eff.get('match'):families.append({'key':'eff-atlas','name':'EFF Atlas deployment record','independent':True,'detail':f"Flock ALPR deployment appears in {eff.get('location') or 'the resolved area'}."})
    if decision=='confirmed' or claimed:families.append({'key':'human-visual','name':'Local human/visual review','independent':True,'detail':('Manual confirmation' if decision=='confirmed' else '')+(' + claimed field photo' if claimed else '')})
    if supportive:families.append({'key':'public-record','name':'User-reviewed public source','independent':True,'detail':f'{len(supportive)} supporting source(s) attached.'})
    return {'status':status,'label':label,'authenticity_score':authenticity,'external_score':external,'nearest_flock_distance_m':nearest_flock,
            'nearest_alpr_distance_m':nearest_alpr,'evidence_families':families,
            'note':'Verification confidence combines independent evidence families. OSM-derived maps are counted once. No external record is not proof that a camera is absent or non-Flock.'}

def flock_verify_external(device_key,force=False):
    device_key=str(device_key or '').strip()
    if not device_key:raise ValueError('device_key is required')
    conn=db()
    try:
        device=_flock_map_row(conn,device_key)
        if not device:raise ValueError('device not found')
        cached=_cached_external_verification(conn,device_key)
        if cached and not force:
            ts=_parse_obs_time(cached.get('checked_at'))
            if ts and time.time()-ts<FLOCK_VERIFY_CACHE_HOURS*3600:
                cached['sources']=_flock_source_notes(conn,device_key);return {'ok':True,'verification':cached}
        review=_flock_review_row(conn,device_key);review=dict(review) if review else None;photos=_flock_photo_rows(conn,device_key);user_sources=_flock_source_notes(conn,device_key)
        lat=float(device['latitude']);lon=float(device['longitude'])
    finally:conn.close()
    errors=[]
    try:osm_matches,overpass_url=_osm_external_matches(lat,lon,FLOCK_VERIFY_RADIUS_M)
    except Exception as e:osm_matches=[];overpass_url=None;errors.append(str(e))
    place=_reverse_place(lat,lon);eff=_eff_atlas_lookup(place)
    if eff.get('error'):errors.append('EFF Atlas: '+str(eff['error']))
    conn=db()
    try:
        # Re-read local state in case it changed while network requests ran.
        device=_flock_map_row(conn,device_key);review=_flock_review_row(conn,device_key);review=dict(review) if review else None;photos=_flock_photo_rows(conn,device_key);user_sources=_flock_source_notes(conn,device_key)
        auth=_verification_authenticity(device,review,photos,osm_matches,eff,user_sources)
        result={**auth,'checked_at':datetime.now(timezone.utc).isoformat(),'radius_m':FLOCK_VERIFY_RADIUS_M,'osm_matches':osm_matches[:25],'osm_source_url':overpass_url,
                'place':place,'eff_atlas':eff,'sources':user_sources,
                'where_else_found':[],'errors':errors,
                'privacy_note':'External verification is manual-only. This check sends this camera coordinate/radius to public OpenStreetMap services and a resolved city/state string to the EFF Atlas search page.'}
        if osm_matches:
            result['where_else_found']=[
              {'name':'OpenStreetMap','url':osm_matches[0]['osm_url'],'independent':True,'source_family':'osm-deflock','detail':f"Nearest mapped ALPR {osm_matches[0]['distance_m']:.0f} m away."},
              {'name':'DeFlock Maps','url':osm_matches[0]['deflock_url'],'independent':False,'source_family':'osm-deflock','detail':'Displays the same underlying OSM ALPR record; shown as another place the record is published, not another independent verification.'},
              {'name':'Flock Camera Map','url':'https://flockcamera.app/map/','independent':False,'source_family':'osm-deflock','detail':'Also publishes OSM-derived ALPR data; not counted as independent evidence.'}
            ]
        if eff.get('match'):result['where_else_found'].append({'name':'EFF Atlas of Surveillance','url':eff.get('url'),'independent':True,'source_family':'eff-atlas','detail':'Independent regional Flock ALPR deployment record; not an exact-camera location proof.'})
        now=result['checked_at'];conn.execute("""insert or replace into flock_external_checks(device_key,checked_at,status,authenticity_score,external_score,
                  nearest_flock_distance_m,nearest_alpr_distance_m,osm_match_count,eff_deployment_match,result_json,error)
                  values (?,?,?,?,?,?,?,?,?,?,?)""",
                  (device_key,now,auth['status'],auth['authenticity_score'],auth['external_score'],auth['nearest_flock_distance_m'],auth['nearest_alpr_distance_m'],len(osm_matches),1 if eff.get('match') else 0,json.dumps(result,separators=(',',':')),(' | '.join(errors)[:2000] if errors else None)))
        conn.commit();result['cached']=False;return {'ok':True,'verification':result}
    finally:conn.close()

def flock_source_add(device_key,label,url='',note='',supports_flock=False,source_type='public_record'):
    device_key=str(device_key or '').strip();label=_norm_text(label)[:160];url=_norm_text(url)[:1000];note=_norm_text(note)[:1500];source_type=_norm_text(source_type or 'public_record')[:80]
    if not device_key:raise ValueError('device_key is required')
    if not label:raise ValueError('source label is required')
    if url and not re.match(r'^https?://',url,re.I):raise ValueError('source URL must start with http:// or https://')
    conn=db()
    try:
        if not conn.execute('select 1 from devices where device_key=?',(device_key,)).fetchone():raise ValueError('device not found')
        sid=str(uuid.uuid4());conn.execute('insert into flock_source_notes(id,device_key,source_type,label,url,note,supports_flock,created_at) values (?,?,?,?,?,?,?,?)',
                  (sid,device_key,source_type,label,url,note,1 if supports_flock else 0,datetime.now(timezone.utc).isoformat()))
        conn.execute('delete from flock_external_checks where device_key=?',(device_key,));conn.commit();return {'ok':True,'source_id':sid,'sources':_flock_source_notes(conn,device_key)}
    finally:conn.close()

def flock_source_delete(source_id):
    source_id=str(source_id or '').strip();conn=db()
    try:
        r=conn.execute('select device_key from flock_source_notes where id=?',(source_id,)).fetchone()
        if not r:raise ValueError('source not found')
        key=r['device_key'];conn.execute('delete from flock_source_notes where id=?',(source_id,));conn.execute('delete from flock_external_checks where device_key=?',(key,));conn.commit();return {'ok':True,'device_key':key,'sources':_flock_source_notes(conn,key)}
    finally:conn.close()

def _flock_review_row(conn,device_key):
    return conn.execute('select decision,notes,updated_at from flock_reviews where device_key=?',(device_key,)).fetchone()

def _flock_sync_confirmed_fingerprints(conn,device_key,confirmed):
    conn.execute('delete from flock_fingerprints where device_key=?',(device_key,))
    if not confirmed:return 0
    rows=conn.execute("""select distinct radio_fingerprint from observations
                         where device_key=? and trim(coalesce(radio_fingerprint,''))<>''""",(device_key,)).fetchall()
    now=datetime.now(timezone.utc).isoformat()
    conn.executemany('insert or ignore into flock_fingerprints(fingerprint,device_key,confirmed_at) values (?,?,?)',
                     [(r['radio_fingerprint'],device_key,now) for r in rows])
    return len(rows)

def _flock_history(conn,device_key,d):
    hist=conn.execute("""select count(*) observations,
                               count(distinct substr(coalesce(seen_at,created_at),1,10)) day_count,
                               min(coalesce(seen_at,created_at)) first_seen,max(coalesce(seen_at,created_at)) last_seen,
                               min(latitude) min_lat,max(latitude) max_lat,min(longitude) min_lon,max(longitude) max_lon,
                               avg(rssi) avg_rssi
                        from observations where device_key=?""",(device_key,)).fetchone()
    drive_count=conn.execute('select count(*) from drive_devices where device_key=?',(device_key,)).fetchone()[0]
    obs=int(hist['observations'] or d['observation_count'] or 0) if hist else int(d['observation_count'] or 0)
    days=int(hist['day_count'] or 0) if hist else 0
    min_lat=float(hist['min_lat'] if hist and hist['min_lat'] is not None else d['min_lat'])
    max_lat=float(hist['max_lat'] if hist and hist['max_lat'] is not None else d['max_lat'])
    min_lon=float(hist['min_lon'] if hist and hist['min_lon'] is not None else d['min_lon'])
    max_lon=float(hist['max_lon'] if hist and hist['max_lon'] is not None else d['max_lon'])
    spread=_haversine_km(min_lat,min_lon,max_lat,max_lon)*1000.0
    first=_parse_obs_time((hist['first_seen'] if hist else None) or d['first_seen'])
    last=_parse_obs_time((hist['last_seen'] if hist else None) or d['last_seen'])
    persistence=max(0.0,(last-first)/86400.0) if first and last else 0.0
    fps=[r['radio_fingerprint'] for r in conn.execute("""select distinct radio_fingerprint from observations
                                                         where device_key=? and trim(coalesce(radio_fingerprint,''))<>''""",(device_key,))]
    fp_match=False;fp_sources=0
    if fps:
        ph=','.join('?' for _ in fps)
        fp_sources=conn.execute(f"select count(distinct device_key) from flock_fingerprints where fingerprint in ({ph}) and device_key<>?",(*fps,device_key)).fetchone()[0]
        fp_match=fp_sources>0
    return {'observations':obs,'day_count':days,'drive_count':int(drive_count or 0),'spread_m':spread,
            'persistence_days':persistence,'fingerprints':fps,'fingerprint_match':fp_match,'fingerprint_sources':int(fp_sources or 0),
            'avg_rssi':hist['avg_rssi'] if hist else None}

def _flock_level(score,decision=None):
    if decision=='confirmed':return 'Confirmed'
    if decision=='rejected':return 'Rejected'
    if score>=80:return 'High'
    if score>=55:return 'Probable'
    return 'Possible'

def _flock_score_device(conn,device_key):
    d=conn.execute('select * from devices where device_key=?',(device_key,)).fetchone()
    if not d:return None
    d=dict(d);review=_flock_review_row(conn,device_key);decision=review['decision'] if review else None
    evidence=[];score=0;identity_signal=0
    name=str(d.get('name') or '').strip();vendor=str(d.get('manufacturer') or '').strip();text=(name+' '+vendor).lower()
    explicit=bool(re.search(r'\bflock[\s_-]+safety\b',text))
    prefix,prefix_hit=_flock_prefix_info(d.get('bssid'))
    if explicit:
        detail='SSID/name or manufacturer explicitly contains Flock Safety'
        evidence.append({'key':'explicit_identity','label':'Explicit Flock identity','points':60,'detail':detail});score+=60;identity_signal=1
    if prefix_hit:
        pretty=':'.join(prefix[i:i+2] for i in range(0,6,2))
        evidence.append({'key':'known_prefix','label':'Shared hardware prefix (weak hint)','points':10,
                         'detail':f'{pretty} is in the local Flock infrastructure prefix list; prefix alone is not definitive.'});score+=10
    if str(d.get('kind') or '').lower()=='alpr':
        evidence.append({'key':'alpr_class','label':'ALPR/camera classification (not vendor evidence)','points':0,'detail':'Materialized device is classified as ALPR/camera; this does not identify its vendor.'})

    hist=_flock_history(conn,device_key,d)
    if hist['fingerprint_match']:
        evidence.append({'key':'learned_fingerprint','label':'Matches locally confirmed radio fingerprint','points':10,
                         'detail':f"Beacon/radio fingerprint matches {hist['fingerprint_sources']} other manually confirmed Flock device(s)."})
        score+=10
    drives=hist['drive_count']
    if drives>=5:pts=14
    elif drives>=3:pts=10
    elif drives>=2:pts=6
    else:pts=0
    if pts:evidence.append({'key':'repeat_drives','label':'Repeated on separate drives','points':pts,'detail':f'Seen on {drives} distinct drive/session(s).'});score+=pts
    days=hist['day_count']
    if days>=5:pts=10
    elif days>=3:pts=7
    elif days>=2:pts=4
    else:pts=0
    if pts:evidence.append({'key':'repeat_days','label':'Repeated on separate days','points':pts,'detail':f'Seen on {days} distinct date(s).'});score+=pts
    spread=hist['spread_m']
    if hist['observations']>=3 and spread<=25:pts=12
    elif hist['observations']>=3 and spread<=75:pts=8
    elif hist['observations']>=3 and spread<=150:pts=4
    else:pts=0
    if pts:evidence.append({'key':'location_stability','label':'Stable physical location','points':pts,'detail':f'Observation footprint spans about {spread:.0f} m.'});score+=pts
    persistence=hist['persistence_days']
    if persistence>=30:pts=8
    elif persistence>=14:pts=6
    elif persistence>=7:pts=4
    else:pts=0
    if pts:evidence.append({'key':'persistence','label':'Persistent over time','points':pts,'detail':f'First-to-last sighting spans {persistence:.0f} days.'});score+=pts
    if hist['observations']>=3 and not int(d.get('signal_mobile') or 0):
        evidence.append({'key':'fixed_behavior','label':'Fixed-location behavior','points':4,'detail':'Device is not flagged as moving across the mapped dataset.'});score+=4

    # Behavior can strengthen a Flock-specific signal, but fixed camera behavior
    # by itself is not enough to identify a vendor. Unidentified ALPR candidates
    # are capped as Possible until a Flock-specific signal or manual review exists.
    auto_score=max(0,min(99,int(round(score))))
    # Repetition and stationary behavior are not brand-specific. Require
    # independent categories, and do not treat capture GPS as a tower location.
    corroborated=prefix_hit or hist['fingerprint_sources']>=2
    identity_signal=int(explicit and corroborated and drives>=2 and days>=2
                        and spread<=150 and not int(d.get('signal_mobile') or 0)
                        and not is_local_mac(d.get('bssid') or ''))
    if not identity_signal:auto_score=min(auto_score,54)
    evidence.append({'key':'conservative_gate','label':'Automatic identification requirements',
                     'points':0,'detail':'Requires Flock Safety name, radio corroboration, 2 drives, 2 days, observation spread ≤150 m and a globally assigned non-mobile MAC. Score is a heuristic, not a probability.'})
    candidate=1 if (auto_score>0 or decision in ('confirmed','rejected')) else 0
    final_score=100 if decision=='confirmed' else 0 if decision=='rejected' else auto_score
    level=_flock_level(final_score,decision)
    if decision=='confirmed':
        evidence.insert(0,{'key':'manual_confirmed','label':'Manually confirmed','points':'override','detail':'User reviewed this device and confirmed it as Flock.'})
    elif decision=='rejected':
        evidence.insert(0,{'key':'manual_rejected','label':'Manually rejected','points':'override','detail':'User reviewed this device and marked it Not Flock.'})
    return {'device_key':device_key,'candidate':candidate,'score':final_score,'auto_score':auto_score,'level':level,
            'identity_signal':identity_signal,'evidence':evidence,'drive_count':drives,'day_count':days,
            'location_spread_m':round(spread,1),'persistence_days':round(persistence,1),'fingerprint_match':1 if hist['fingerprint_match'] else 0,
            'fingerprint_count':len(hist['fingerprints']),'review_decision':decision,'review_notes':review['notes'] if review else None}

def refresh_flock_assessments(conn,keys=None):
    prefixes=sorted(FLOCK_WIFI_PREFIXES);ph=','.join('?' for _ in prefixes)
    if keys is None:
        rows=conn.execute(f"""select device_key from devices where kind='alpr' or lower(coalesce(name,'')) like '%flock%'
                             or lower(coalesce(manufacturer,'')) like '%flock%' or substr(coalesce(bssid_norm,''),1,6) in ({ph})
                             union select device_key from flock_reviews
                             union select distinct o.device_key from observations o join flock_fingerprints f on f.fingerprint=o.radio_fingerprint
                             where o.device_key is not null""",prefixes).fetchall()
        keys={r['device_key'] for r in rows};conn.execute('delete from flock_assessments')
    else:
        keys={str(k) for k in keys if k}
        if not keys:return 0
        conn.execute('create temp table if not exists flock_touched_keys(k text primary key)');conn.execute('delete from flock_touched_keys')
        conn.executemany('insert or ignore into flock_touched_keys(k) values (?)',((k,) for k in keys))
        rows=conn.execute(f"""select d.device_key from devices d join flock_touched_keys t on t.k=d.device_key
              where d.kind='alpr' or lower(coalesce(d.name,'')) like '%flock%' or lower(coalesce(d.manufacturer,'')) like '%flock%'
                 or substr(coalesce(d.bssid_norm,''),1,6) in ({ph})
              union select r.device_key from flock_reviews r join flock_touched_keys t on t.k=r.device_key
              union select distinct o.device_key from observations o join flock_touched_keys t on t.k=o.device_key
                         join flock_fingerprints f on f.fingerprint=o.radio_fingerprint""",prefixes).fetchall()
        candidates={r['device_key'] for r in rows}
        non_candidates=keys-candidates
        if non_candidates:
            conn.execute('delete from flock_assessments where device_key in (select k from flock_touched_keys)')
        keys=candidates
    now=datetime.now(timezone.utc).isoformat();updated=0
    for key in keys:
        a=_flock_score_device(conn,key)
        if not a or not a['candidate']:
            conn.execute('delete from flock_assessments where device_key=?',(key,));continue
        conn.execute("""insert or replace into flock_assessments(device_key,candidate,score,auto_score,level,identity_signal,evidence_json,
                     drive_count,day_count,location_spread_m,persistence_days,fingerprint_match,updated_at)
                     values (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (key,1,a['score'],a['auto_score'],a['level'],a['identity_signal'],json.dumps(a['evidence'],separators=(',',':')),
                      a['drive_count'],a['day_count'],a['location_spread_m'],a['persistence_days'],a['fingerprint_match'],now));updated+=1
    return updated

def _flock_map_row(conn,device_key):
    r=conn.execute("""select d.device_key id,d.device_key,d.kind,d.name,d.bssid,d.security,d.channel,d.latitude,d.longitude,d.first_seen,d.last_seen,d.last_seen seen_at,
                             d.source,d.collection_method,d.session_id,d.session_name,d.hardware_profile_id,d.hardware_profile_name,d.manufacturer,
                             d.hardware_type,d.hardware_label,d.hardware_confidence,d.hardware_reason,d.mac_local,d.mac_local signal_randomized,
                             d.signal_hidden,d.signal_open,d.signal_mobile,d.observation_count _count,
                             a.score flock_score,a.auto_score flock_auto_score,a.level flock_level,a.identity_signal flock_identity_signal,
                             a.evidence_json flock_evidence_json,a.drive_count flock_drive_count,a.day_count flock_day_count,
                             a.location_spread_m flock_location_spread_m,a.persistence_days flock_persistence_days,a.fingerprint_match flock_fingerprint_match,
                             rv.decision flock_review_decision,rv.notes flock_review_notes
                      from devices d left join flock_assessments a on a.device_key=d.device_key
                      left join flock_reviews rv on rv.device_key=d.device_key where d.device_key=?""",(device_key,)).fetchone()
    if not r:return None
    row=dict(r);decision=row.get('flock_review_decision');score=int(row.get('flock_score') or 0);identity=int(row.get('flock_identity_signal') or 0)
    row['is_flock']=1 if decision=='confirmed' or (decision!='rejected' and identity and score>=80) else 0
    row['flock_confidence']=str(row.get('flock_level') or '').lower();row['flock_reason']=''
    try:
        evidence=json.loads(row.get('flock_evidence_json') or '[]')
    except:evidence=[]
    row['flock_evidence']=evidence;row['flock_reason']='; '.join(str(x.get('label') or '') for x in evidence[:3] if x.get('label'))
    if row['is_flock']:row['kind']='alpr'
    return row

def flock_review_list(search='',level='all',decision='all',limit=500):
    conn=db()
    try:
        try:limit=max(1,min(2000,int(limit)))
        except:limit=500
        where=['a.candidate=1'];args=[]
        if level and level!='all':where.append('a.level=?');args.append(level)
        if decision=='unreviewed':where.append('rv.decision is null')
        elif decision in ('confirmed','rejected'):where.append('rv.decision=?');args.append(decision)
        search=str(search or '').strip().lower()[:120]
        if search:
            raw=normalize_mac(search);clauses=["lower(coalesce(d.name,'')) like ?","lower(coalesce(d.bssid,'')) like ?","lower(coalesce(d.manufacturer,'')) like ?"]
            pat='%'+search+'%';args.extend([pat,pat,pat])
            if len(raw)>=4:clauses.append("coalesce(d.bssid_norm,'') like ?");args.append('%'+raw+'%')
            where.append('('+' or '.join(clauses)+')')
        sql="""select d.device_key,d.kind,d.name,d.bssid,d.manufacturer,d.latitude,d.longitude,d.first_seen,d.last_seen,d.observation_count,
                      d.source,d.collection_method,a.score,a.auto_score,a.level,a.identity_signal,a.evidence_json,a.drive_count,a.day_count,
                      a.location_spread_m,a.persistence_days,a.fingerprint_match,rv.decision,rv.notes,rv.updated_at review_updated_at,
                      (select count(*) from flock_photos p where p.device_key=d.device_key) photo_count,
                      (select count(*) from flock_photos p where p.device_key=d.device_key and p.claimed=1) claimed_photo_count
               from flock_assessments a join devices d on d.device_key=a.device_key left join flock_reviews rv on rv.device_key=d.device_key
               where """+' and '.join(where)+" order by case rv.decision when 'confirmed' then 0 when 'rejected' then 2 else 1 end,a.score desc,d.last_seen desc limit ?"
        args.append(limit);rows=[]
        for r in conn.execute(sql,args):
            x=dict(r)
            try:x['evidence']=json.loads(x.pop('evidence_json') or '[]')
            except:x['evidence']=[]
            rows.append(x)
        stats={}
        for r in conn.execute("""select a.level,coalesce(rv.decision,'unreviewed') decision,count(*) n
                                 from flock_assessments a left join flock_reviews rv on rv.device_key=a.device_key
                                 where a.candidate=1 group by a.level,coalesce(rv.decision,'unreviewed')"""):
            stats[f"{r['level']}|{r['decision']}"]=r['n']
        totals={
          'candidates':conn.execute('select count(*) from flock_assessments where candidate=1').fetchone()[0],
          'confirmed':conn.execute("select count(*) from flock_reviews where decision='confirmed'").fetchone()[0],
          'rejected':conn.execute("select count(*) from flock_reviews where decision='rejected'").fetchone()[0],
          'high':conn.execute("select count(*) from flock_assessments a left join flock_reviews r on r.device_key=a.device_key where a.level='High' and r.decision is null").fetchone()[0],
          'probable':conn.execute("select count(*) from flock_assessments a left join flock_reviews r on r.device_key=a.device_key where a.level='Probable' and r.decision is null").fetchone()[0],
          'possible':conn.execute("select count(*) from flock_assessments a left join flock_reviews r on r.device_key=a.device_key where a.level='Possible' and r.decision is null").fetchone()[0],
          'photos':conn.execute('select count(*) from flock_photos').fetchone()[0],
          'photo_documented':conn.execute("""select count(distinct p.device_key) from flock_photos p join flock_assessments a on a.device_key=p.device_key
                                            left join flock_reviews r on r.device_key=p.device_key where p.claimed=1 and (r.decision='confirmed' or (coalesce(r.decision,'')<>'rejected' and a.identity_signal=1 and a.score>=80))""").fetchone()[0],
        }
        return {'ok':True,'candidates':rows,'totals':totals,'breakdown':stats,'score_version':FLOCK_SCORE_VERSION,
                'fingerprint_support':{'preserved':True,'learned_confirmed':conn.execute('select count(distinct fingerprint) from flock_fingerprints').fetchone()[0]}}
    finally:conn.close()

def flock_review_detail(device_key):
    conn=db()
    try:
        row=_flock_map_row(conn,device_key)
        if not row:raise ValueError('device not found')
        review=_flock_review_row(conn,device_key)
        sightings=[dict(r) for r in conn.execute("""select coalesce(seen_at,created_at) seen_at,source,session_id,session_name,collection_method,
                                                         latitude,longitude,rssi,radio_fingerprint
                                                  from observations where device_key=? order by coalesce(seen_at,created_at) desc,rowid desc limit 50""",(device_key,))]
        drives=[dict(r) for r in conn.execute("""select source,session_id,max(session_name) session_name,max(collection_method) collection_method,
                                                     count(*) observations,min(coalesce(seen_at,created_at)) first_seen,max(coalesce(seen_at,created_at)) last_seen
                                              from observations where device_key=? group by source,session_id order by last_seen desc limit 50""",(device_key,))]
        fingerprints=[dict(r) for r in conn.execute("""select radio_fingerprint,count(*) observations from observations where device_key=?
                                                         and trim(coalesce(radio_fingerprint,''))<>'' group by radio_fingerprint order by observations desc""",(device_key,))]
        learned=conn.execute('select count(*) from flock_fingerprints where device_key=?',(device_key,)).fetchone()[0]
        photos=_flock_photo_rows(conn,device_key);documented=any(int(p.get('claimed') or 0) for p in photos)
        photo_xp_eligible=_flock_photo_device_eligible(conn,device_key);external=_cached_external_verification(conn,device_key);source_notes=_flock_source_notes(conn,device_key)
        if external:external['sources']=source_notes
        return {'ok':True,'device':row,'review':dict(review) if review else None,'sightings':sightings,'drives':drives,
                'fingerprints':fingerprints,'learned_fingerprints':learned,'photos':photos,'external_verification':external,'verification_sources':source_notes,
                'photo_xp':{'documented':documented,'eligible':photo_xp_eligible,'xp_each':FLOCK_PHOTO_XP,
                            'earned':FLOCK_PHOTO_XP if documented and photo_xp_eligible else 0},
                'fingerprint_note':'Radio fingerprints are learned only when imports contain beacon/capability/IE fingerprint fields. Existing logs without those fields cannot be retroactively fingerprinted.'}
    finally:conn.close()

def flock_review_update(device_key,decision=None,notes=''):
    device_key=str(device_key or '').strip();decision=str(decision or '').strip().lower();notes=str(notes or '').strip()[:2000]
    if not device_key:raise ValueError('device_key is required')
    if decision not in ('confirmed','rejected','clear'):raise ValueError('decision must be confirmed, rejected, or clear')
    conn=db()
    try:
        if not conn.execute('select 1 from devices where device_key=?',(device_key,)).fetchone():raise ValueError('device not found')
        if decision=='clear':
            conn.execute('delete from flock_reviews where device_key=?',(device_key,));_flock_sync_confirmed_fingerprints(conn,device_key,False)
        else:
            conn.execute('insert or replace into flock_reviews(device_key,decision,notes,updated_at) values (?,?,?,?)',
                         (device_key,decision,notes,datetime.now(timezone.utc).isoformat()))
            _flock_sync_confirmed_fingerprints(conn,device_key,decision=='confirmed')
            # A manual confirmation is authoritative for the materialized map
            # model so confirmed devices remain exact ALPR/Flock markers even
            # when their original source row was generic WIFI.
            if decision=='confirmed':
                fields='device_key,kind,collection_method,latitude,longitude,observation_count,signal_open,signal_hidden,signal_mobile,mac_local,security,manufacturer,hardware_type,first_seen,bssid_norm'
                old=[dict(r) for r in conn.execute('select '+fields+' from devices where device_key=?',(device_key,))]
                conn.execute("update devices set kind='alpr' where device_key=?",(device_key,))
                new=[dict(r) for r in conn.execute('select '+fields+' from devices where device_key=?',(device_key,))]
                _apply_map_rollup_changes(conn,old,new);_apply_device_summary_changes(conn,old,new)
        # Confirmed fingerprints can affect other candidates, so refresh the small
        # candidate set instead of only this device.
        refresh_flock_assessments(conn,None);conn.execute('delete from flock_external_checks where device_key=?',(device_key,));conn.commit()
    finally:conn.close()
    return flock_review_detail(device_key)

def _device_key(row):
    raw=normalize_mac(row.get('bssid'))
    if len(raw)>=12:return 'mac:'+raw
    try:return f"{row.get('kind') or 'wifi'}:{row.get('name') or ''}:{float(row.get('latitude')):.4f}:{float(row.get('longitude')):.4f}"
    except Exception:return f"{row.get('kind') or 'wifi'}:{row.get('name') or ''}:{row.get('latitude')}:{row.get('longitude')}"


NEIGHBORHOOD_UNLOCK_TARGET = 500
NEIGHBORHOOD_UNLOCK_XP = 100

def load_neighborhood_zones(path):
    """Load optional local survey zones; never bundle a user's home region."""
    path = Path(path)
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(raw, list) or len(raw) > 1000:
        raise ValueError('Neighborhood catalog must be a list of at most 1000 zones')
    zones, keys = [], set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError('Each neighborhood must be an object')
        key, name = item.get('key'), item.get('name')
        if not isinstance(key, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,79}', key) or key in keys:
            raise ValueError('Neighborhood keys must be unique lowercase identifiers')
        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            raise ValueError('Neighborhood names must contain 1-120 characters')
        bounds = {}
        for field in ('south', 'north', 'west', 'east'):
            value = item.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError('Neighborhood bounds must be finite numbers')
            bounds[field] = float(value)
        if not (-90 <= bounds['south'] < bounds['north'] <= 90 and -180 <= bounds['west'] < bounds['east'] <= 180):
            raise ValueError('Neighborhood bounds must be ordered geographic coordinates')
        zone = dict(key=key, name=name.strip(), **bounds)
        for field, default in (('icon', '📍'), ('group', 'Local zones')):
            value = item.get(field, default)
            if not isinstance(value, str) or len(value) > 120:
                raise ValueError('Neighborhood labels must be strings up to 120 characters')
            zone[field] = value
        zones.append(zone)
        keys.add(key)
    return zones


NEIGHBORHOOD_ZONES = load_neighborhood_zones(DB_PATH.parent / 'neighborhoods.json')


def sync_neighborhood_catalog(conn):
    signature = hashlib.sha256(json.dumps(NEIGHBORHOOD_ZONES, sort_keys=True).encode()).hexdigest()
    marker = 'neighborhood_catalog:' + signature
    if not _migration_done(conn, marker):
        rebuild_neighborhood_memberships(conn)
        conn.execute("delete from schema_migrations where name like 'neighborhood_catalog:%'")
        _mark_migration(conn, marker)


# v2.16 scale materializations. These keep hot-page work proportional to the
# number of drives/cells instead of raw observation or GPS-fix cardinality.
MAP_ROLLUP_LEVELS = {8:25, 9:50, 10:100, 11:200}  # 0.04, .02, .01, .005 degrees


def _summary_month(value):
    v=str(value or '')
    if len(v)<7:return ''
    m=v[:7]
    if not re.fullmatch(r'20\d{2}-(0[1-9]|1[0-2])',m):return ''
    try:
        y=int(m[:4]);return m if 1997<=y<=datetime.now(timezone.utc).year+1 else ''
    except:return ''


def _apply_device_summary_changes(conn, old_rows, new_rows):
    from collections import Counter
    dims=Counter();months=Counter();parents=Counter()
    def add_row(r,sign):
        kind=str(r.get('kind') or 'wifi');method=str(r.get('collection_method') or 'unknown') or 'unknown'
        dims[('kind',kind)]+=sign;dims[('method',method)]+=sign
        if kind=='wifi':dims[('security',str(r.get('security') or 'Unknown') or 'Unknown')]+=sign
        hw=str(r.get('hardware_type') or '').strip()
        if hw:dims[('hardware',hw)]+=sign
        vendor=str(r.get('manufacturer') or '').strip()
        if vendor and vendor not in ('Unknown','Private / randomized'):dims[('vendor',vendor)]+=sign
        for key,col in [('hidden','signal_hidden'),('randomized','mac_local'),('open','signal_open'),('mobile','signal_mobile')]:
            if int(r.get(col) or 0):dims[('signal',key)]+=sign
        month=_summary_month(r.get('first_seen'))
        if month:months[month]+=sign
        raw=str(r.get('bssid_norm') or '')
        if len(raw)>=12:parents[raw[:10]]+=sign
    for r in old_rows:add_row(r,-1)
    for r in new_rows:add_row(r,1)
    if dims:
        conn.executemany("""insert into device_summary_counts(dimension,value,count) values (?,?,?)
                            on conflict(dimension,value) do update set count=count+excluded.count""",
                         [(d,v,n) for (d,v),n in dims.items() if n])
        conn.execute('delete from device_summary_counts where count<=0')
    if months:
        conn.executemany("""insert into device_discovery_months(month,count) values (?,?)
                            on conflict(month) do update set count=count+excluded.count""",[(m,n) for m,n in months.items() if n])
        conn.execute('delete from device_discovery_months where count<=0')
    if parents:
        conn.executemany("""insert into device_bssid_parents(prefix,count) values (?,?)
                            on conflict(prefix) do update set count=count+excluded.count""",[(m,n) for m,n in parents.items() if n])
        conn.execute('delete from device_bssid_parents where count<=0')


def rebuild_device_summaries(conn):
    conn.execute('delete from device_summary_counts');conn.execute('delete from device_discovery_months');conn.execute('delete from device_bssid_parents')
    conn.execute("insert into device_summary_counts select 'kind',coalesce(nullif(kind,''),'wifi'),count(*) from devices group by 2")
    conn.execute("insert into device_summary_counts select 'method',coalesce(nullif(collection_method,''),'unknown'),count(*) from devices group by 2")
    conn.execute("insert into device_summary_counts select 'security',coalesce(nullif(security,''),'Unknown'),count(*) from devices where kind='wifi' group by 2")
    conn.execute("insert into device_summary_counts select 'hardware',hardware_type,count(*) from devices where trim(coalesce(hardware_type,''))<>'' group by 2")
    conn.execute("insert into device_summary_counts select 'vendor',manufacturer,count(*) from devices where trim(coalesce(manufacturer,''))<>'' and manufacturer not in ('Unknown','Private / randomized') group by 2")
    for value,col in [('hidden','signal_hidden'),('randomized','mac_local'),('open','signal_open'),('mobile','signal_mobile')]:
        n=int(conn.execute(f'select coalesce(sum({col}),0) from devices').fetchone()[0] or 0)
        if n:conn.execute("insert into device_summary_counts(dimension,value,count) values ('signal',?,?)",(value,n))
    conn.execute("""insert into device_discovery_months(month,count)
                    select strftime('%Y-%m',datetime(first_seen)),count(*) from devices
                    where datetime(first_seen) is not null and datetime(first_seen)>=datetime('1997-01-01') and datetime(first_seen)<=datetime('now','+1 day')
                    group by 1""")
    conn.execute("""insert into device_bssid_parents(prefix,count) select substr(bssid_norm,1,10),count(*) from devices
                    where length(coalesce(bssid_norm,''))>=12 group by substr(bssid_norm,1,10)""")
    return conn.execute('select count(*) from device_summary_counts').fetchone()[0]


def _map_rollup_key(row, level, factor):
    lat=float(row['latitude']);lon=float(row['longitude'])
    return (level,int((lon+180.0)*factor),int((lat+90.0)*factor),str(row['kind'] or 'wifi'),str(row['collection_method'] or 'unknown'))


def _apply_map_rollup_changes(conn, old_rows, new_rows):
    from collections import defaultdict
    delta=defaultdict(lambda:[0,0,0.0,0.0,0])
    for sign,rows in ((-1,old_rows),(1,new_rows)):
        for r in rows:
            try:obs=int(r['observation_count'] or 0);lat=float(r['latitude']);lon=float(r['longitude']);op=int(r['signal_open'] or 0)
            except Exception:continue
            for level,factor in MAP_ROLLUP_LEVELS.items():
                key=_map_rollup_key(r,level,factor);d=delta[key]
                d[0]+=sign;d[1]+=sign*obs;d[2]+=sign*lat;d[3]+=sign*lon;d[4]+=sign*op
    if not delta:return
    vals=[(*k,*v) for k,v in delta.items() if v[0] or v[1] or v[2] or v[3] or v[4]]
    conn.executemany("""insert into map_cells(level,gx,gy,kind,collection_method,device_count,observation_count,sum_lat,sum_lon,open_count)
                        values (?,?,?,?,?,?,?,?,?,?)
                        on conflict(level,gx,gy,kind,collection_method) do update set
                          device_count=device_count+excluded.device_count,
                          observation_count=observation_count+excluded.observation_count,
                          sum_lat=sum_lat+excluded.sum_lat,sum_lon=sum_lon+excluded.sum_lon,
                          open_count=open_count+excluded.open_count""",vals)
    conn.execute('delete from map_cells where device_count<=0')


def rebuild_map_rollups(conn):
    conn.execute('delete from map_cells')
    for level,factor in MAP_ROLLUP_LEVELS.items():
        conn.execute("""insert into map_cells(level,gx,gy,kind,collection_method,device_count,observation_count,sum_lat,sum_lon,open_count)
                        select ?,cast((longitude+180.0)*? as integer),cast((latitude+90.0)*? as integer),
                               coalesce(nullif(kind,''),'wifi'),coalesce(nullif(collection_method,''),'unknown'),
                               count(*),coalesce(sum(observation_count),0),coalesce(sum(latitude),0),coalesce(sum(longitude),0),coalesce(sum(signal_open),0)
                        from devices group by 2,3,4,5""",(level,factor,factor))
    return conn.execute('select count(*) from map_cells').fetchone()[0]


def _refresh_neighborhood_memberships(conn, keys):
    keys={str(k) for k in keys if k}
    if not keys:return
    conn.execute('create temp table if not exists affected_neighborhood_keys(k text primary key)')
    conn.execute('delete from affected_neighborhood_keys')
    conn.executemany('insert or ignore into affected_neighborhood_keys(k) values (?)',((k,) for k in keys))
    conn.execute('delete from neighborhood_devices where device_key in (select k from affected_neighborhood_keys)')
    rows=conn.execute("""select d.device_key,d.latitude,d.longitude,d.first_seen,d.last_seen,d.observation_count
                         from devices d join affected_neighborhood_keys a on a.k=d.device_key""").fetchall()
    batch=[]
    for r in rows:
        lat=float(r['latitude']);lon=float(r['longitude'])
        for z in NEIGHBORHOOD_ZONES:
            if z['south']<=lat<=z['north'] and z['west']<=lon<=z['east']:
                batch.append((z['key'],r['device_key'],r['first_seen'],r['last_seen'],int(r['observation_count'] or 0)))
        if len(batch)>=5000:
            conn.executemany('insert or replace into neighborhood_devices(neighborhood_key,device_key,first_seen,last_seen,observation_count) values (?,?,?,?,?)',batch);batch=[]
    if batch:conn.executemany('insert or replace into neighborhood_devices(neighborhood_key,device_key,first_seen,last_seen,observation_count) values (?,?,?,?,?)',batch)


def rebuild_neighborhood_memberships(conn):
    conn.execute('delete from neighborhood_devices')
    for z in NEIGHBORHOOD_ZONES:
        conn.execute("""insert or replace into neighborhood_devices(neighborhood_key,device_key,first_seen,last_seen,observation_count)
                        select ?,d.device_key,d.first_seen,d.last_seen,d.observation_count
                        from device_rtree r join devices d on d.rowid=r.rowid
                        where r.max_lat>=? and r.min_lat<=? and r.max_lon>=? and r.min_lon<=?""",
                     (z['key'],z['south'],z['north'],z['west'],z['east']))
    return conn.execute('select count(*) from neighborhood_devices').fetchone()[0]


def _refresh_source_scope_stats(conn, source, session_id=None):
    sid=str(session_id or '')
    where,args=_drive_scope_clause(source,session_id)
    row=conn.execute(f"select count(*) n,max(coalesce(seen_at,created_at)) last_seen from observations where {where}",args).fetchone()
    n=int(row['n'] or 0)
    if not n:
        conn.execute('delete from source_scope_stats where source=? and session_id=?',(str(source or ''),sid));return
    conn.execute("""insert or replace into source_scope_stats(source,session_id,observation_count,last_seen,updated_at)
                    values (?,?,?,?,?)""",(str(source or ''),sid,n,row['last_seen'],datetime.now(timezone.utc).isoformat()))


def rebuild_source_scope_stats(conn):
    conn.execute('delete from source_scope_stats')
    now=datetime.now(timezone.utc).isoformat()
    conn.execute("""insert into source_scope_stats(source,session_id,observation_count,last_seen,updated_at)
                    select source,coalesce(session_id,''),count(*),max(coalesce(seen_at,created_at)),?
                    from observations where trim(coalesce(source,''))<>'' group by source,coalesce(session_id,'')""",(now,))
    return conn.execute('select count(*) from source_scope_stats').fetchone()[0]


def _refresh_global_coverage_cells(conn, affected):
    affected={(int(a),int(b)) for a,b in affected}
    if not affected:return
    conn.execute('create temp table if not exists affected_cov_cells(cell_lat integer,cell_lon integer,primary key(cell_lat,cell_lon))')
    conn.execute('delete from affected_cov_cells')
    conn.executemany('insert or ignore into affected_cov_cells(cell_lat,cell_lon) values (?,?)',affected)
    conn.execute("""delete from coverage_cells where exists (
                      select 1 from affected_cov_cells a where a.cell_lat=coverage_cells.cell_lat and a.cell_lon=coverage_cells.cell_lon)""")
    conn.execute("""insert into coverage_cells(cell_lat,cell_lon,first_t,last_t,fixes)
                    select d.cell_lat,d.cell_lon,min(d.first_t),max(d.last_t),sum(d.fixes)
                    from drive_coverage_cells d join affected_cov_cells a
                      on a.cell_lat=d.cell_lat and a.cell_lon=d.cell_lon
                    group by d.cell_lat,d.cell_lon""")


def refresh_drive_stats(conn, drive_key, old_cells=None, refresh_global=True):
    old_cells=set(old_cells or [])
    conn.execute('delete from drive_stats where drive_key=?',(drive_key,))
    conn.execute('delete from drive_days where drive_key=?',(drive_key,))
    conn.execute('delete from drive_coverage_cells where drive_key=?',(drive_key,))
    rows=conn.execute("""select id,source,session_name,collection_method,seen_at,seen_ts,latitude,longitude
                         from track_fixes where drive_key=? and seen_ts>0 order by seen_ts,id""",(drive_key,))
    speed_limits={'warwalking':20,'bike':70,'cycling':70,'bus':160,'wardriving':210,'transit':260,'stationary':12,'other':210,'unknown':210}
    first_t=last_t=0.0;prev=None;distance_m=0.0;accepted=rejected=0;files=set();labels=[]
    walk_m=walk_seconds=0.0;walk_sessions=0;walk_prev=None;has_walk=0
    days={};cells={}
    for r in rows:
        t=float(r['seen_ts'] or 0);lat=float(r['latitude']);lon=float(r['longitude']);method=str(r['collection_method'] or 'unknown').lower()
        if not first_t:first_t=t
        last_t=t
        if r['source']:files.add(str(r['source']))
        if r['session_name']:labels.append(str(r['session_name']))
        elif r['source']:labels.append(str(r['source']))
        day=str(r['seen_at'] or '')[:10]
        if len(day)==10:
            k=(day,method);v=days.get(k)
            if v is None:days[k]=[t,1]
            else:v[0]=min(v[0],t);v[1]+=1
        ck=(int(round(lat*1000)),int(round(lon*1000)));cv=cells.get(ck)
        if cv is None:cells[ck]=[t,t,1]
        else:cv[0]=min(cv[0],t);cv[1]=max(cv[1],t);cv[2]+=1
        if prev is not None:
            pt,plat,plon,pmethod=prev;dt=t-pt
            if 0<dt<=1800:
                km=_haversine_km(plat,plon,lat,lon);limit=max(speed_limits.get(method,210),speed_limits.get(pmethod,210))
                if km>=.003 and km<=8 and km/(dt/3600)<=limit:distance_m+=km*1000;accepted+=1
                elif km>=.003:rejected+=1
        prev=(t,lat,lon,method)
        if method=='warwalking':
            has_walk=1
            if walk_prev is None or t-walk_prev[0]>1800:
                walk_sessions+=1;walk_prev=(t,lat,lon);continue
            dt=t-walk_prev[0]
            if 0<dt<=1800:
                km=_haversine_km(walk_prev[1],walk_prev[2],lat,lon)
                if .001<=km<=3 and km/(dt/3600)<=20:walk_m+=km*1000;walk_seconds+=dt
            walk_prev=(t,lat,lon)
        else:walk_prev=None
    device_count=int(conn.execute('select count(*) from drive_devices where drive_key=?',(drive_key,)).fetchone()[0] or 0)
    if first_t or device_count:
        label=(max(labels) if labels else drive_key);now=datetime.now(timezone.utc).isoformat()
        conn.execute("""insert or replace into drive_stats(drive_key,label,first_t,last_t,files,device_count,distance_m,accepted_segments,rejected_segments,
                        walk_m,walk_seconds,walk_sessions,has_warwalking,updated_at) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (drive_key,label,first_t,last_t,max(1,len(files)),device_count,distance_m,accepted,rejected,walk_m,walk_seconds,walk_sessions,has_walk,now))
        if days:conn.executemany('insert or replace into drive_days(drive_key,day,collection_method,first_t,fixes) values (?,?,?,?,?)',
                                 [(drive_key,d,m,v[0],v[1]) for (d,m),v in days.items()])
        if cells:conn.executemany('insert or replace into drive_coverage_cells(drive_key,cell_lat,cell_lon,first_t,last_t,fixes) values (?,?,?,?,?,?)',
                                  [(drive_key,a,b,v[0],v[1],v[2]) for (a,b),v in cells.items()])
    if refresh_global:_refresh_global_coverage_cells(conn, old_cells|set(cells.keys()))


def rebuild_drive_scale_materializations(conn):
    conn.execute('delete from drive_stats');conn.execute('delete from drive_days');conn.execute('delete from drive_coverage_cells');conn.execute('delete from coverage_cells')
    drives=[r['drive_key'] for r in conn.execute('select distinct drive_key from track_fixes order by drive_key')]
    for i,dk in enumerate(drives,1):
        refresh_drive_stats(conn,dk,set(),False)
        if i%50==0:conn.commit()
    conn.execute('delete from coverage_cells')
    conn.execute("""insert into coverage_cells(cell_lat,cell_lon,first_t,last_t,fixes)
                    select cell_lat,cell_lon,min(first_t),max(last_t),sum(fixes) from drive_coverage_cells group by cell_lat,cell_lon""")
    return len(drives)

def _neighborhood_metrics(conn, now_ts=None):
    now_ts=float(now_ts or time.time())
    grouped={r['neighborhood_key']:dict(r) for r in conn.execute("""select neighborhood_key,count(*) devices,
              coalesce(sum(observation_count),0) observations,min(first_seen) first_seen,max(last_seen) last_seen
              from neighborhood_devices group by neighborhood_key""")}
    out=[]
    for z in NEIGHBORHOOD_ZONES:
        row=grouped.get(z['key'],{});devices=int(row.get('devices') or 0);observations=int(row.get('observations') or 0)
        earned=devices>=NEIGHBORHOOD_UNLOCK_TARGET;earned_at=None
        if earned:
            er=conn.execute("""select first_seen from neighborhood_devices where neighborhood_key=? and trim(coalesce(first_seen,''))<>''
                               order by first_seen,device_key limit 1 offset ?""",(z['key'],NEIGHBORHOOD_UNLOCK_TARGET-1)).fetchone()
            earned_at=(er['first_seen'][:10] if er and er['first_seen'] else None)
        last_ts=_parse_obs_time(row.get('last_seen')) if row.get('last_seen') else 0.0
        stale_days=max(0,int((now_ts-last_ts)/86400)) if last_ts else None;gap=max(0,NEIGHBORHOOD_UNLOCK_TARGET-devices)
        progress=100.0 if earned else round(min(100,devices/NEIGHBORHOOD_UNLOCK_TARGET*100),1)
        center_lat=(z['south']+z['north'])/2;center_lon=(z['west']+z['east'])/2
        if earned and stale_days is not None and stale_days>=180:reason=f'Award unlocked, but coverage is {stale_days:,} days old.';priority=60+min(40,stale_days/15)
        elif earned:reason='Neighborhood award unlocked.';priority=0
        elif progress>=75:reason=f'Only {gap:,} more unique devices to unlock.';priority=105-(100-progress)
        elif progress>=25:reason=f'{devices:,}/{NEIGHBORHOOD_UNLOCK_TARGET:,} mapped — good progress toward the award.';priority=60+progress/2
        elif devices>0:reason=f'Lightly surveyed: {devices:,}/{NEIGHBORHOOD_UNLOCK_TARGET:,} unique devices.';priority=30+progress
        else:reason='No unique devices mapped in this survey zone yet.';priority=22
        out.append({'key':z['key'],'name':z['name'],'icon':z['icon'],'group':z['group'],'south':z['south'],'north':z['north'],'west':z['west'],'east':z['east'],
                    'center_lat':center_lat,'center_lon':center_lon,'devices':devices,'observations':observations,'target':NEIGHBORHOOD_UNLOCK_TARGET,
                    'gap':gap,'progress':progress,'earned':earned,'earned_at':earned_at,'first_seen':row.get('first_seen'),'last_seen':row.get('last_seen'),
                    'stale_days':stale_days,'reason':reason,'base_priority':round(priority,1)})
    unlocked=sum(1 for x in out if x['earned']);locked=[x for x in out if not x['earned']]
    next_unlock=min(locked,key=lambda x:(x['gap'],-x['devices'],x['name'])) if locked else None
    return {'target':NEIGHBORHOOD_UNLOCK_TARGET,'xp_each':NEIGHBORHOOD_UNLOCK_XP,'unlocked':unlocked,'total':len(out),
            'neighborhood_xp':unlocked*NEIGHBORHOOD_UNLOCK_XP,'next_unlock':next_unlock,'zones':out,
            'boundary_note':'Curated offline survey zones; boundaries are approximate and intended for progress tracking, not legal/municipal use.'}

def _build_perf_summary():
    conn=db();now=time.time();now_dt=datetime.now(timezone.utc);stale_cutoff_iso=datetime.fromtimestamp(now-180*86400,timezone.utc).isoformat()
    total_devices=int(conn.execute('select count(*) from devices').fetchone()[0] or 0)
    source_count=int(conn.execute('select count(distinct source) from source_scope_stats').fetchone()[0] or 0)
    bucket=lambda dim:{str(r['value']):int(r['count'] or 0) for r in conn.execute('select value,count from device_summary_counts where dimension=?',(dim,))}
    kinds=bucket('kind');alpr=int(kinds.get('alpr',0))
    stale=int(conn.execute("select count(*) from devices where coalesce(last_seen,'')<>'' and last_seen<?",(stale_cutoff_iso,)).fetchone()[0] or 0)
    refreshed=int(conn.execute('select count(*) from devices where refreshed_180=1').fetchone()[0] or 0)
    method_counts=Counter(bucket('method'));security_counts=Counter(bucket('security'));hardware_counts=Counter(bucket('hardware'));vendor_counts=Counter(bucket('vendor'))
    sig=bucket('signal');multibssid=int(conn.execute('select coalesce(sum(count),0) from device_bssid_parents where count>=2').fetchone()[0] or 0)
    signals=Counter({'hidden':int(sig.get('hidden',0)),'randomized':int(sig.get('randomized',0)),'open':int(sig.get('open',0)),'mobile':int(sig.get('mobile',0)),'multibssid':multibssid})
    timeline=[[r['month'],r['count']] for r in conn.execute('select month,count from device_discovery_months order by month desc limit 18').fetchall()[::-1]]

    activity_days=[]
    for r in conn.execute("select distinct day from drive_days where length(day)=10 order by day"):
        try:activity_days.append(datetime.fromisoformat(r['day']).date())
        except:pass
    def streak_stats(days):
        if not days:return (0,0)
        longest=run=1
        for a,b in zip(days,days[1:]):run=run+1 if (b-a).days==1 else 1;longest=max(longest,run)
        today=now_dt.date();current=0
        if days[-1] in (today,today-timedelta(days=1)):
            current=1
            for i in range(len(days)-1,0,-1):
                if (days[i]-days[i-1]).days==1:current+=1
                else:break
        return current,longest
    current_streak,longest_streak=streak_stats(activity_days)

    coverage_cells=int(conn.execute('select count(*) from coverage_cells').fetchone()[0] or 0)
    method_first={str(r['collection_method'] or 'unknown'):float(r['t'] or 0) for r in conn.execute("select collection_method,min(first_t) t from drive_days where first_t>0 group by collection_method")}
    ds=conn.execute("""select count(*) drives,coalesce(sum(distance_m),0) distance_m,coalesce(sum(accepted_segments),0) accepted,
                              coalesce(sum(rejected_segments),0) rejected,coalesce(sum(walk_m),0) walk_m,coalesce(sum(walk_seconds),0) walk_seconds,
                              coalesce(sum(walk_sessions),0) walks,coalesce(sum(case when files>1 then 1 else 0 end),0) multi_log from drive_stats""").fetchone()
    drive_count=int(ds['drives'] or 0);total_distance=float(ds['distance_m'] or 0)/1000.0;total_miles=float(ds['distance_m'] or 0)/1609.344
    accepted=int(ds['accepted'] or 0);rejected=int(ds['rejected'] or 0);walk_miles=float(ds['walk_m'] or 0)/1609.344;walk_seconds=float(ds['walk_seconds'] or 0);walks=int(ds['walks'] or 0);multi_log_drives=int(ds['multi_log'] or 0)
    largest_row=conn.execute("select label,distance_m,files from drive_stats order by distance_m desc limit 1").fetchone();device_row=conn.execute("select label,device_count,files from drive_stats order by device_count desc limit 1").fetchone()
    largest_drive={'label':largest_row['label'] if largest_row else '—','miles':float(largest_row['distance_m'] or 0)/1609.344 if largest_row else 0,'files':int(largest_row['files'] or 0) if largest_row else 0}
    device_record={'label':device_row['label'] if device_row else '—','devices':int(device_row['device_count'] or 0) if device_row else 0,'files':int(device_row['files'] or 0) if device_row else 0}
    walk_devices=int(conn.execute("""select count(distinct dd.device_key) from drive_devices dd join drive_stats ds on ds.drive_key=dd.drive_key where ds.has_warwalking=1""").fetchone()[0] or 0)
    walk_hardware=int(conn.execute("""select count(distinct dd.device_key) from drive_devices dd join drive_stats ds on ds.drive_key=dd.drive_key join devices d on d.device_key=dd.device_key where ds.has_warwalking=1 and trim(coalesce(d.hardware_type,''))<>''""").fetchone()[0] or 0)
    current_ym=now_dt.strftime('%Y-%m');this_month=int(conn.execute("select count(distinct drive_key) from drive_days where collection_method='warwalking' and substr(day,1,7)=?",(current_ym,)).fetchone()[0] or 0)

    flock_rows=conn.execute("""select d.first_seen from flock_assessments a join devices d on d.device_key=a.device_key left join flock_reviews r on r.device_key=a.device_key where (r.decision='confirmed' or (coalesce(r.decision,'')<>'rejected' and a.identity_signal=1 and a.score>=80)) order by d.first_seen""").fetchall()
    flock_times=[_parse_obs_time(r['first_seen']) for r in flock_rows if r['first_seen']];flock_count=len(flock_rows);flock_first=flock_times[0] if flock_times else 0
    alpr_first=_parse_obs_time(conn.execute("select min(first_seen) from devices where kind='alpr'").fetchone()[0])

    def nth_sql(sql,args,n):
        if n<=0:return 0.0
        r=conn.execute(sql+' limit 1 offset ?',(*args,n-1)).fetchone();return _parse_obs_time(r[0]) if r and r[0] else 0.0
    def nth_drive(n,where='1=1'):
        r=conn.execute(f"select first_t from drive_stats where {where} and first_t>0 order by first_t limit 1 offset ?",(n-1,)).fetchone();return float(r[0] or 0) if r else 0.0
    def nth_coverage(n):
        r=conn.execute('select first_t from coverage_cells where first_t>0 order by first_t limit 1 offset ?',(n-1,)).fetchone();return float(r[0] or 0) if r else 0.0
    def nth_device(n,where='1=1'):
        return nth_sql(f"select first_seen from devices where {where} and first_seen is not null order by first_seen",(),n)
    def nth_refresh(n):
        return nth_sql("select refreshed_at from devices where refreshed_180=1 and refreshed_at is not null order by refreshed_at",(),n)
    def distance_crossings():
        out={};cum=0.0
        for r in conn.execute('select first_t,last_t,distance_m from drive_stats where distance_m>0 order by first_t'):
            before=cum;cum+=float(r['distance_m'] or 0)/1609.344
            for target in (50,500,5000):
                if target not in out and before<target<=cum:out[target]=float(r['last_t'] or r['first_t'] or 0)
            if len(out)==3:break
        return out
    distance_cross=distance_crossings()
    session_multi_times=[float(r['first_t'] or 0) for r in conn.execute('select first_t from drive_stats where files>1 and first_t>0 order by first_t limit 25')]
    def streak_cross(days,target):
        if not days:return 0.0
        run=1
        if target<=1:return datetime.combine(days[0],datetime.min.time(),tzinfo=timezone.utc).timestamp()
        for a,b in zip(days,days[1:]):
            run=run+1 if (b-a).days==1 else 1
            if run>=target:return datetime.combine(b,datetime.min.time(),tzinfo=timezone.utc).timestamp()
        return 0.0

    neighborhoods=_neighborhood_metrics(conn,now);neighborhood_xp=int(neighborhoods['neighborhood_xp'])
    FLOCK_BONUS_XP=50;flock_bonus_xp=flock_count*FLOCK_BONUS_XP
    flock_photo_documented=int(conn.execute("""select count(distinct p.device_key) from flock_photos p join flock_assessments a on a.device_key=p.device_key left join flock_reviews r on r.device_key=p.device_key where p.claimed=1 and (r.decision='confirmed' or (coalesce(r.decision,'')<>'rejected' and a.identity_signal=1 and a.score>=80))""").fetchone()[0] or 0);flock_photo_xp=flock_photo_documented*FLOCK_PHOTO_XP
    hw_total=sum(hardware_counts.values())
    xp=int(round(total_devices*.02+total_miles*5+coverage_cells+drive_count*5+len(activity_days)*10+len([k for k in method_counts if k not in ('','unknown')])*25+min(alpr,100)*2+flock_bonus_xp+flock_photo_xp+neighborhood_xp+min(hw_total,2000)*.1+refreshed*2+multi_log_drives*10+longest_streak*10))
    ranks=[(0,'Scout','🧭'),(1000,'Mapper','📍'),(3000,'Surveyor','📡'),(7000,'Pathfinder','🗺️'),(14000,'Road Warrior','🚙'),(24000,'Grid Runner','🔷'),(40000,'Cartographer','🌐'),(70000,'Atlas','🏆'),(120000,'Legend','⭐')]
    rank_index=max(i for i,(threshold,_,_) in enumerate(ranks) if xp>=threshold);threshold,rank_name,rank_icon=ranks[rank_index]
    if rank_index+1<len(ranks):next_threshold,next_rank,_=ranks[rank_index+1];rank_progress=max(0,min(100,(xp-threshold)/max(1,next_threshold-threshold)*100))
    else:next_threshold=xp;next_rank='Max rank';rank_progress=100
    metric={'drives':drive_count,'devices':total_devices,'miles':total_miles,'coverage':coverage_cells,'alpr':alpr,'flock':flock_count,'methods':len([k for k in method_counts if k not in ('','unknown')]),'multi_log':multi_log_drives,'refreshed':refreshed,'streak':longest_streak,'hardware':hw_total,'warwalking':walks}
    awards_def=[
      ('first-drive','Survey Starter','🚗','Complete 5 mapped drives.','drives',5,nth_drive(5)),
      ('device-1k','Signal Scout','📡','Map 5,000 unique devices.','devices',5000,nth_device(5000)),
      ('device-10k','Signal Hunter','📶','Map 50,000 unique devices.','devices',50000,nth_device(50000)),
      ('device-100k','RF Cartographer','🌐','Map 500,000 unique devices.','devices',500000,nth_device(500000)),
      ('miles-10','Getting Around','🛣️','Record 50 mapped miles.','miles',50,distance_cross.get(50,0)),
      ('miles-100','Road Regular','🚙','Record 500 mapped miles.','miles',500,distance_cross.get(500,0)),
      ('miles-1000','Long Haul','🏁','Record 5,000 mapped miles.','miles',5000,distance_cross.get(5000,0)),
      ('coverage-50','Coverage Pioneer','🧩','Reach 250 distinct coverage cells.','coverage',250,nth_coverage(250)),
      ('coverage-250','Street Sweeper','🧹','Reach 1,250 distinct coverage cells.','coverage',1250,nth_coverage(1250)),
      ('coverage-1000','Grid Master','🔷','Reach 5,000 distinct coverage cells.','coverage',5000,nth_coverage(5000)),
      ('camera-first','Camera Spotter','📷','Map 10 unique ALPR / camera devices.','alpr',10,nth_device(10,"kind='alpr'")),
      ('flock-first','Flock Finder','🎯','Map 5 unique Flock cameras.','flock',5,(flock_times[4] if len(flock_times)>=5 else 0)),
      ('flock-10','Flock Hunter','🛡️','Map 50 unique Flock cameras.','flock',50,(flock_times[49] if len(flock_times)>=50 else 0)),
      ('warwalk-first','Warwalker','🥾','Complete 5 warwalking surveys.','warwalking',5,nth_drive(5,'has_warwalking=1')),
      ('methods-3','Method Explorer','🧰','Use four collection methods.','methods',4,(sorted(t for k,t in method_first.items() if k not in ('','unknown'))[3] if len([k for k in method_first if k not in ('','unknown')])>=4 else 0)),
      ('multi-log','Session Builder','🗂️','Complete 5 drives combining multiple log files.','multi_log',5,(session_multi_times[4] if len(session_multi_times)>=5 else 0)),
      ('refresh-25','Coverage Refresher','♻️','Revisit 250 devices after 180+ days.','refreshed',250,nth_refresh(250)),
      ('streak-3','Hot Streak','🔥','Collect on 7 consecutive days.','streak',7,streak_cross(activity_days,7)),
      ('streak-7','Fortnight Mapper','⚡','Collect on 14 consecutive days.','streak',14,streak_cross(activity_days,14)),
      ('streak-14','Monthly Sweep','🏆','Collect on 30 consecutive days.','streak',30,streak_cross(activity_days,30)),
      ('streak-30','Seasoned Mapper','👑','Collect on 90 consecutive days.','streak',90,streak_cross(activity_days,90)),
      ('hardware-100','Hardware Hunter','🔎','Identify 500 inferred hardware devices.','hardware',500,nth_device(500,"trim(coalesce(hardware_type,''))<>''")),
      ('drives-25','Pathfinder','🗺️','Complete 100 mapped drives.','drives',100,nth_drive(100))]

    awards=[]
    for key,name,icon,desc,mkey,target,earned_t in awards_def:
        current=float(metric.get(mkey,0) or 0);earned=current>=target
        awards.append({'key':key,'name':name,'icon':icon,'description':desc,'current':current,'target':target,'progress':100 if earned else round(max(0,min(100,current/max(1,target)*100)),1),'earned':earned,'earned_at':datetime.fromtimestamp(earned_t,timezone.utc).date().isoformat() if earned and earned_t else None})
    recent=sorted([a for a in awards if a['earned']],key=lambda a:(a.get('earned_at') or '',a['name']),reverse=True)[:6]
    special_badges=[]
    if flock_count>=5:special_badges.append({'type':'flock','icon':'🎯','name':'Flock Finder','detail':f'{flock_count:,} unique Flock camera'+('' if flock_count==1 else 's'),'xp':flock_bonus_xp})
    if flock_photo_documented>=5:special_badges.append({'type':'flock-photo','icon':'📷','name':'Field Evidence','detail':f'{flock_photo_documented:,} Flock camera'+('' if flock_photo_documented==1 else 's')+' documented with claimed photos','xp':flock_photo_xp})
    for days,name,icon in [(90,'Seasoned Mapper','👑'),(30,'Monthly Sweep','🏆'),(14,'Fortnight Mapper','⚡'),(7,'Hot Streak','🔥')]:
        if current_streak>=days:special_badges.append({'type':'streak','icon':icon,'name':name,'detail':f'{current_streak}-day active streak','xp':current_streak*10});break
    gamification={'xp':xp,'rank':rank_name,'rank_icon':rank_icon,'rank_threshold':threshold,'next_rank':next_rank,'next_threshold':next_threshold,'rank_progress':round(rank_progress,1),'drives':drive_count,'coverage_cells':coverage_cells,'activity_days':len(activity_days),'current_streak':current_streak,'longest_streak':longest_streak,'flock_count':flock_count,'flock_bonus_xp':flock_bonus_xp,'flock_xp_each':FLOCK_BONUS_XP,'special_badges':special_badges,'refreshed_devices':refreshed,'multi_log_drives':multi_log_drives,'largest_drive':{'name':largest_drive['label'],'miles':round(largest_drive['miles'],2),'files':largest_drive['files']},'device_record':{'name':device_record['label'],'devices':device_record['devices'],'files':device_record['files']},'awards':awards,'recent':recent,'neighborhood_unlocked':neighborhoods['unlocked'],'neighborhood_total':neighborhoods['total'],'neighborhood_xp':neighborhood_xp,'neighborhood_xp_each':NEIGHBORHOOD_UNLOCK_XP,'flock_photo_documented':flock_photo_documented,'flock_photo_xp':flock_photo_xp,'flock_photo_xp_each':FLOCK_PHOTO_XP,'xp_note':f'XP favors useful coverage. Each unique Flock camera adds {FLOCK_BONUS_XP} bonus XP; the first claimed photo for each qualifying Flock device adds {FLOCK_PHOTO_XP} XP; each neighborhood unlocked at {NEIGHBORHOOD_UNLOCK_TARGET} unique devices adds {NEIGHBORHOOD_UNLOCK_XP} XP; longest streak adds 10 XP per consecutive day; all awards and ranks use the harder v2.21.0 rules. Raw observation rows and repeat photos of the same camera do not earn XP.'}
    conn.close()
    return {'ok':True,'total_devices':total_devices,'sources':source_count,'distance':{'km':total_distance,'miles':total_miles,'accepted':accepted,'rejected':rejected},'alpr':alpr,'hardware_total':hw_total,'private_mac':signals['randomized'],'open_networks':signals['open'],'methods':[[k,v] for k,v in method_counts.most_common()],'hardware':[[HARDWARE_TYPES.get(k,(k,k))[0],v,k] for k,v in hardware_counts.most_common()],'vendors':[[k,v] for k,v in vendor_counts.most_common(20)],'security':[[k,v] for k,v in security_counts.most_common(20)],'signals':dict(signals),'stale_180d':stale,'method_count':len(method_counts),'timeline':timeline,'walking':{'miles':walk_miles,'seconds':walk_seconds,'walks':walks,'avg':walk_miles/walks if walks else 0,'devices':walk_devices,'hardware':walk_hardware,'thisMonth':this_month},'neighborhoods':neighborhoods,'gamification':gamification}

def stats_summary():
    def build():
        conn=db()
        try:
            total=conn.execute('select count(*) from observations').fetchone()[0]
            wifi=conn.execute("select count(*) from devices where kind='wifi'").fetchone()[0]
            alpr=conn.execute("select count(*) from devices where kind='alpr'").fetchone()[0]
            sources=[dict(r) for r in conn.execute(
                "select source,sum(observation_count) count,max(last_seen) last_import from source_scope_stats group by source order by last_import desc"
            )]
            sec=[dict(r) for r in conn.execute(
                "select coalesce(nullif(security,''),'Unknown') security,count(*) count from devices "
                "where kind='wifi' group by security order by count desc limit 8")]
            kinds=[dict(r) for r in conn.execute(
                "select coalesce(nullif(kind,''),'unknown') kind,count(*) count from devices group by kind order by count desc")]
            methods=[dict(r) for r in conn.execute(
                "select coalesce(nullif(collection_method,''),'unknown') collection_method,count(*) count from devices group by collection_method order by count desc")]
            routes=conn.execute('select count(*) from route_history').fetchone()[0]
            return {'total':total,'wifi':wifi,'alpr':alpr,'sources':sources,'security':sec,'kinds':kinds,'methods':methods,'routes':routes,'tile_cache':tile_cache_stats()}
        finally:conn.close()
    return _cached_perf('stats',build)

def performance_summary():
    return _cached_perf('summary', _build_perf_summary)

def hardware_counts_summary():
    def build():
        conn=db()
        try:
            hardware={str(r['value']):int(r['count'] or 0) for r in conn.execute("select value,count from device_summary_counts where dimension='hardware'")}
            sig={str(r['value']):int(r['count'] or 0) for r in conn.execute("select value,count from device_summary_counts where dimension='signal'")}
            multibssid=conn.execute('select coalesce(sum(count),0) from device_bssid_parents where count>=2').fetchone()[0]
            return {'ok':True,'hardware':hardware,'signals':{'hidden':int(sig.get('hidden',0)),'randomized':int(sig.get('randomized',0)),
                    'open':int(sig.get('open',0)),'mobile':int(sig.get('mobile',0)),'multibssid':int(multibssid or 0)}}
        finally:conn.close()
    return _cached_perf('hardware-counts',build)

def _migration_done(conn, name):
    return bool(conn.execute('select 1 from schema_migrations where name=?',(name,)).fetchone())

def _mark_migration(conn, name):
    conn.execute('insert or replace into schema_migrations(name,applied_at) values (?,?)',(name,datetime.now(timezone.utc).isoformat()))
    conn.commit()

def _drive_key(source=None, session_id=None):
    sid=str(session_id or '').strip()
    return ('session:'+sid) if sid else ('source:'+str(source or 'Unknown'))

def _scope_clause(source, session_id=None, alias=''):
    # Identity of one imported file. The same filename may legitimately appear
    # in different drive sessions.
    p=(alias+'.') if alias else ''
    sid=str(session_id or '').strip()
    if sid:return f"{p}source=? and {p}session_id=?", (str(source or ''),sid)
    return f"{p}source=? and ({p}session_id is null or trim({p}session_id)='')", (str(source or ''),)

def _drive_scope_clause(source, session_id=None, alias=''):
    p=(alias+'.') if alias else ''
    sid=str(session_id or '').strip()
    if sid:return f"{p}session_id=?", (sid,)
    return f"{p}source=? and ({p}session_id is null or trim({p}session_id)='')", (str(source or ''),)

def _observation_identity(kind, name, bssid, lat, lon):
    raw=normalize_mac(bssid)
    if len(raw)>=12:return raw,'mac:'+raw
    try:return raw,f"{kind or 'wifi'}:{name or ''}:{float(lat):.4f}:{float(lon):.4f}"
    except Exception:return raw,f"{kind or 'wifi'}:{name or ''}:{lat}:{lon}"

def _ensure_materialized_rtrees(conn, rebuild=False):
    conn.execute('CREATE VIRTUAL TABLE IF NOT EXISTS device_rtree USING rtree(rowid,min_lat,max_lat,min_lon,max_lon)')
    conn.execute('CREATE VIRTUAL TABLE IF NOT EXISTS track_rtree USING rtree(rowid,min_lat,max_lat,min_lon,max_lon)')
    conn.executescript(
      'CREATE TRIGGER IF NOT EXISTS device_rtree_insert AFTER INSERT ON devices BEGIN '
      'INSERT OR REPLACE INTO device_rtree(rowid,min_lat,max_lat,min_lon,max_lon) VALUES(new.rowid,new.min_lat,new.max_lat,new.min_lon,new.max_lon); END;'
      'CREATE TRIGGER IF NOT EXISTS device_rtree_delete AFTER DELETE ON devices BEGIN DELETE FROM device_rtree WHERE rowid=old.rowid; END;'
      'CREATE TRIGGER IF NOT EXISTS device_rtree_update AFTER UPDATE OF latitude,longitude,min_lat,max_lat,min_lon,max_lon ON devices BEGIN '
      'INSERT OR REPLACE INTO device_rtree(rowid,min_lat,max_lat,min_lon,max_lon) VALUES(new.rowid,new.min_lat,new.max_lat,new.min_lon,new.max_lon); END;'
      'CREATE TRIGGER IF NOT EXISTS track_rtree_insert AFTER INSERT ON track_fixes BEGIN '
      'INSERT OR REPLACE INTO track_rtree(rowid,min_lat,max_lat,min_lon,max_lon) VALUES(new.id,new.latitude,new.latitude,new.longitude,new.longitude); END;'
      'CREATE TRIGGER IF NOT EXISTS track_rtree_delete AFTER DELETE ON track_fixes BEGIN DELETE FROM track_rtree WHERE rowid=old.id; END;'
      'CREATE TRIGGER IF NOT EXISTS track_rtree_update AFTER UPDATE OF latitude,longitude ON track_fixes BEGIN '
      'INSERT OR REPLACE INTO track_rtree(rowid,min_lat,max_lat,min_lon,max_lon) VALUES(new.id,new.latitude,new.latitude,new.longitude,new.longitude); END;'
    )
    if rebuild:
        conn.execute('delete from device_rtree')
        conn.execute('insert into device_rtree(rowid,min_lat,max_lat,min_lon,max_lon) select rowid,min_lat,max_lat,min_lon,max_lon from devices')
        conn.execute('delete from track_rtree')
        conn.execute('insert into track_rtree(rowid,min_lat,max_lat,min_lon,max_lon) select id,latitude,latitude,longitude,longitude from track_fixes')
        conn.commit()

def backfill_observation_keys(conn):
    cur=conn.execute("select rowid,kind,name,bssid,latitude,longitude from observations where device_key is null or trim(device_key)='' or bssid_norm is null")
    updated=0
    while True:
        batch=cur.fetchmany(5000)
        if not batch:break
        vals=[]
        for r in batch:
            raw,key=_observation_identity(r['kind'],r['name'],r['bssid'],r['latitude'],r['longitude'])
            vals.append((raw,key,r['rowid']))
        conn.executemany('update observations set bssid_norm=?,device_key=? where rowid=?',vals)
        updated+=len(vals)
        if updated%50000==0:
            conn.commit();print(f'v2.11 observation identities: {updated:,}',flush=True)
    conn.commit()
    return updated

_DEVICE_INSERT_SQL="""insert into devices(
 device_key,kind,name,bssid,bssid_norm,security,channel,latitude,longitude,min_lat,max_lat,min_lon,max_lon,
 first_seen,last_seen,observation_count,source,collection_method,session_id,session_name,hardware_profile_id,hardware_profile_name,
 manufacturer,hardware_type,hardware_label,hardware_confidence,hardware_reason,mac_local,signal_hidden,signal_open,signal_mobile,refreshed_180,refreshed_at,updated_at
) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""

def _device_flush(conn, g):
    if not g:return
    n=max(1,g['count'])
    lat=g['sum_lat']/n;lon=g['sum_lon']/n
    latest=g['latest']
    bssid=latest.get('bssid') or g.get('bssid') or ''
    raw=normalize_mac(bssid)
    vendor=manufacturer_for_mac(bssid) if bssid else ''
    hw=infer_hardware(latest.get('name'),vendor,bssid)
    flock=classify_flock(latest.get('name'),vendor,bssid)
    material_kind='alpr' if flock['is_flock'] else (latest.get('kind') or 'wifi')
    if flock['is_flock'] and not hw.get('hardware_type'):
        hw={'hardware_type':'camera','hardware_label':HARDWARE_TYPES['camera'][0],'hardware_short':HARDWARE_TYPES['camera'][1],
            'hardware_confidence':flock['confidence'],'hardware_reason':flock['reason']}
    sec=str(latest.get('security') or '').strip()
    hidden=1 if not str(latest.get('name') or '').strip() or 'hidden' in str(latest.get('name') or '').lower() else 0
    open_flag=1 if re.search(r'(^|[\s\[\(])(open|none|unencrypted)([\s\]\)]|$)',sec,re.I) else 0
    mobile=1 if _haversine_km(g['min_lat'],g['min_lon'],g['max_lat'],g['max_lon'])>.5 else 0
    conn.execute(_DEVICE_INSERT_SQL,(
      g['key'],material_kind,latest.get('name'),bssid,raw,latest.get('security'),latest.get('channel'),
      lat,lon,g['min_lat'],g['max_lat'],g['min_lon'],g['max_lon'],g['first_seen'],g['last_seen'],g['count'],
      latest.get('source'),latest.get('collection_method') or 'unknown',latest.get('session_id'),latest.get('session_name'),
      latest.get('hardware_profile_id'),latest.get('hardware_profile_name'),vendor,hw.get('hardware_type') or None,
      hw.get('hardware_label') or None,hw.get('hardware_confidence') or None,hw.get('hardware_reason') or None,
      1 if bssid and is_local_mac(bssid) else 0,hidden,open_flag,mobile,
      1 if g['first_ts'] and g['last_ts'] and (g['last_ts']-g['first_ts'])>=180*86400 else 0,
      g['last_seen'] if g['first_ts'] and g['last_ts'] and (g['last_ts']-g['first_ts'])>=180*86400 else None,
      datetime.now(timezone.utc).isoformat()
    ))

def _materialize_device_cursor(conn, cursor):
    current=None
    for r0 in cursor:
        r=dict(r0);key=r.get('device_key') or _device_key(r)
        lat=float(r['latitude']);lon=float(r['longitude'])
        seen=str(r.get('seen_at') or r.get('created_at') or '')
        ts=_parse_obs_time(seen)
        if current is None or current['key']!=key:
            if current:_device_flush(conn,current)
            current={'key':key,'count':0,'sum_lat':0.0,'sum_lon':0.0,'min_lat':lat,'max_lat':lat,'min_lon':lon,'max_lon':lon,
                     'first_seen':seen,'last_seen':seen,'first_ts':ts,'last_ts':ts,'latest':r,'bssid':r.get('bssid')}
        current['count']+=1;current['sum_lat']+=lat;current['sum_lon']+=lon
        current['min_lat']=min(current['min_lat'],lat);current['max_lat']=max(current['max_lat'],lat)
        current['min_lon']=min(current['min_lon'],lon);current['max_lon']=max(current['max_lon'],lon)
        if ts and (not current['first_ts'] or ts<current['first_ts']):current['first_ts']=ts;current['first_seen']=seen
        if ts>=current['last_ts']:
            current['last_ts']=ts;current['last_seen']=seen;current['latest']=r
    if current:_device_flush(conn,current)

def rebuild_all_devices(conn):
    print('v2.11 building materialized devices…',flush=True)
    conn.execute('delete from devices')
    cur=conn.execute("""select rowid,kind,name,bssid,bssid_norm,security,channel,latitude,longitude,seen_at,source,collection_method,
                              created_at,session_id,session_name,hardware_profile_id,hardware_profile_name,device_key
                       from observations where device_key is not null order by device_key,coalesce(seen_at,created_at),rowid""")
    _materialize_device_cursor(conn,cur)
    conn.commit()
    n=conn.execute('select count(*) from devices').fetchone()[0]
    print(f'v2.11 materialized devices: {n:,}',flush=True)
    return n

def refresh_devices_for_keys(conn, keys):
    keys={str(k) for k in keys if k}
    if not keys:return
    conn.execute('create temp table if not exists affected_device_keys(k text primary key)')
    conn.execute('delete from affected_device_keys')
    conn.executemany('insert or ignore into affected_device_keys(k) values (?)',((k,) for k in keys))
    old_rows=[dict(r) for r in conn.execute("""select d.device_key,d.kind,d.collection_method,d.latitude,d.longitude,d.observation_count,d.signal_open,d.signal_hidden,d.signal_mobile,d.mac_local,d.security,d.manufacturer,d.hardware_type,d.first_seen,d.bssid_norm
                                                from devices d join affected_device_keys a on a.k=d.device_key""")]
    conn.execute('delete from devices where device_key in (select k from affected_device_keys)')
    cur=conn.execute("""select rowid,kind,name,bssid,bssid_norm,security,channel,latitude,longitude,seen_at,source,collection_method,
                              created_at,session_id,session_name,hardware_profile_id,hardware_profile_name,device_key
                       from observations where device_key in (select k from affected_device_keys)
                       order by device_key,coalesce(seen_at,created_at),rowid""")
    _materialize_device_cursor(conn,cur)
    new_rows=[dict(r) for r in conn.execute("""select d.device_key,d.kind,d.collection_method,d.latitude,d.longitude,d.observation_count,d.signal_open,d.signal_hidden,d.signal_mobile,d.mac_local,d.security,d.manufacturer,d.hardware_type,d.first_seen,d.bssid_norm
                                                from devices d join affected_device_keys a on a.k=d.device_key""")]
    _apply_map_rollup_changes(conn,old_rows,new_rows)
    _apply_device_summary_changes(conn,old_rows,new_rows)
    _refresh_neighborhood_memberships(conn,keys)
    refresh_flock_assessments(conn,keys)

def _track_fix_key(drive_key, ts, lat, lon):
    return f"{drive_key}|{int(round(ts or 0))}|{float(lat):.5f}|{float(lon):.5f}"

def refresh_drive_materialization(conn, source, session_id=None):
    drive_key=_drive_key(source,session_id)
    old_cells={(int(r['cell_lat']),int(r['cell_lon'])) for r in conn.execute('select cell_lat,cell_lon from drive_coverage_cells where drive_key=?',(drive_key,))}
    conn.execute('delete from track_fixes where drive_key=?',(drive_key,))
    conn.execute('delete from drive_devices where drive_key=?',(drive_key,))
    where,args=_drive_scope_clause(source,session_id)
    conn.execute(f"""insert or ignore into drive_devices(drive_key,device_key,first_seen)
                     select ?,device_key,min(coalesce(seen_at,created_at)) from observations
                     where {where} and device_key is not null group by device_key""",(drive_key,*args))
    cur=conn.execute(f"""select rowid,source,session_id,session_name,collection_method,coalesce(seen_at,created_at) seen_at,
                               latitude,longitude from observations where {where}
                         order by coalesce(seen_at,created_at),rowid""",args)
    batch=[]
    for r in cur:
        ts=_parse_obs_time(r['seen_at'])
        key=_track_fix_key(drive_key,ts,r['latitude'],r['longitude'])
        batch.append((key,drive_key,r['source'],r['session_id'],r['session_name'],r['collection_method'] or 'unknown',
                      r['seen_at'],ts,float(r['latitude']),float(r['longitude'])))
        if len(batch)>=1000:
            conn.executemany('insert or ignore into track_fixes(fix_key,drive_key,source,session_id,session_name,collection_method,seen_at,seen_ts,latitude,longitude) values (?,?,?,?,?,?,?,?,?,?)',batch);batch=[]
    if batch:conn.executemany('insert or ignore into track_fixes(fix_key,drive_key,source,session_id,session_name,collection_method,seen_at,seen_ts,latitude,longitude) values (?,?,?,?,?,?,?,?,?,?)',batch)
    refresh_drive_stats(conn,drive_key,old_cells)
    _refresh_source_scope_stats(conn,source,session_id)

def rebuild_all_tracks(conn):
    print('v2.11 building deduplicated GPS tracks…',flush=True)
    conn.execute('delete from track_fixes');conn.execute('delete from drive_devices')
    drives=conn.execute("""select distinct source,session_id from observations
                           where trim(coalesce(source,''))<>'' order by coalesce(session_id,''),source""").fetchall()
    done=set()
    for i,r in enumerate(drives,1):
        dk=_drive_key(r['source'],r['session_id'])
        if dk in done:continue
        done.add(dk);refresh_drive_materialization(conn,r['source'],r['session_id'])
        if i%25==0:conn.commit()
    conn.commit()
    n=conn.execute('select count(*) from track_fixes').fetchone()[0]
    print(f'v2.11 GPS track fixes: {n:,}',flush=True)
    return n

_DB_INIT_LOCK = threading.Lock()
_DB_INITIALIZED = False

def _open_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c=sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory=sqlite3.Row
    _v29_apply_sqlite_pragmas(c)
    return c

def _initialize_database(c):
    FLOCK_PHOTO_DIR.mkdir(parents=True,exist_ok=True)
    # WAL is a database-level setting. Set it once during initialization rather
    # than negotiating it on every API connection.
    try:c.execute('pragma journal_mode=WAL')
    except Exception:pass
    c.executescript(SCHEMA)

    obs_cols={r[1] for r in c.execute('pragma table_info(observations)').fetchall()}
    for col,decl in [
      ('collection_method',"TEXT NOT NULL DEFAULT 'unknown'"),('inferred_method',"TEXT NOT NULL DEFAULT 'unknown'"),
      ('inference_confidence',"REAL NOT NULL DEFAULT 0"),('session_id',"TEXT"),('session_name',"TEXT"),
      ('hardware_profile_id',"TEXT"),('hardware_profile_name',"TEXT"),('hardware_profile_gear',"TEXT"),
      ('bssid_norm',"TEXT"),('device_key',"TEXT"),('rssi',"REAL"),('beacon_interval',"TEXT"),
      ('radio_capabilities',"TEXT"),('information_elements',"TEXT"),('radio_fingerprint',"TEXT")
    ]:
        if col not in obs_cols:c.execute(f'alter table observations add column {col} {decl}')

    dev_cols={r[1] for r in c.execute('pragma table_info(devices)').fetchall()}
    if 'refreshed_180' not in dev_cols:c.execute("alter table devices add column refreshed_180 INTEGER NOT NULL DEFAULT 0")
    if 'refreshed_at' not in dev_cols:c.execute("alter table devices add column refreshed_at TEXT")

    run_cols={r[1] for r in c.execute('pragma table_info(import_runs)').fetchall()}
    for col,decl in [
      ('requested_method',"TEXT NOT NULL DEFAULT 'unknown'"),('collection_method',"TEXT NOT NULL DEFAULT 'unknown'"),
      ('inferred_method',"TEXT NOT NULL DEFAULT 'unknown'"),('inference_confidence',"REAL NOT NULL DEFAULT 0"),
      ('session_id',"TEXT"),('session_name',"TEXT"),('hardware_profile_id',"TEXT"),('hardware_profile_name',"TEXT"),
      ('hardware_profile_gear',"TEXT")
    ]:
        if col not in run_cols:c.execute(f'alter table import_runs add column {col} {decl}')

    # Raw observations remain the audit/source-of-truth table. Index only the
    # fields used by source/session management and explicit raw-data queries.
    c.execute('drop index if exists idx_obs_lat_lon')
    c.execute('create index if not exists idx_obs_source on observations(source)')
    c.execute('create index if not exists idx_obs_kind on observations(kind)')
    c.execute('create index if not exists idx_obs_collection_method on observations(collection_method)')
    c.execute('create index if not exists idx_obs_seen_at on observations(seen_at)')
    c.execute('create index if not exists idx_obs_device_key on observations(device_key)')
    c.execute('create index if not exists idx_obs_source_session on observations(source,session_id)')
    c.execute('create index if not exists idx_obs_session_seen on observations(session_id,seen_at)')
    c.execute('create index if not exists idx_obs_collection_device on observations(collection_method,device_key)')
    c.execute('drop index if exists idx_obs_source_collection_seen')
    c.execute('drop index if exists idx_obs_security')
    c.execute('create index if not exists idx_devices_first_seen on devices(first_seen)')
    c.execute('create index if not exists idx_devices_kind_security on devices(kind,security)')
    c.execute('create index if not exists idx_devices_manufacturer on devices(manufacturer)')
    c.execute('create index if not exists idx_devices_hardware_first on devices(hardware_type,first_seen)')
    c.execute('create index if not exists idx_devices_refreshed on devices(refreshed_180,refreshed_at)')
    c.execute("create index if not exists idx_devices_bssid_parent on devices(substr(bssid_norm,1,10)) where length(coalesce(bssid_norm,''))>=12")

    now_sync=datetime.now(timezone.utc).isoformat()
    c.execute('insert or ignore into sync_settings(id,enabled,base_url,api_token,updated_at) values (1,0,?,?,?)',(SYNC_DEFAULT_BASE_URL,None,now_sync))
    privacy_row=c.execute('select 1 from sync_privacy_settings where id=1').fetchone()
    if not privacy_row:
        privacy_cfg=_privacy_default('balanced');c.execute('insert into sync_privacy_settings(id,profile,config_json,secret,updated_at) values (1,?,?,?,?)',('balanced',json.dumps(privacy_cfg,separators=(',',':')),os.urandom(32).hex(),now_sync))
    sync_run_cols={r[1] for r in c.execute('pragma table_info(sync_runs)').fetchall()}
    for col,decl in [('records_excluded','INTEGER NOT NULL DEFAULT 0'),('privacy_profile','TEXT'),('privacy_rating','TEXT')]:
        if col not in sync_run_cols:c.execute(f'alter table sync_runs add column {col} {decl}')
    c.execute('insert or ignore into wigle_settings(id,enabled,api_token,donate,updated_at) values (1,0,?,0,?)',(None,now_sync))
    c.execute("insert or ignore into map_settings(id,provider,tile_url_template,attribution,cache_allowed,updated_at) values (1,?,?,?,?,?)",
              (MAP_DEFAULT_PROVIDER if MAP_DEFAULT_PROVIDER in ('none','selfhosted','custom') else 'none',
               MAP_DEFAULT_URL,MAP_DEFAULT_ATTRIBUTION,1 if MAP_DEFAULT_CACHE else 0,now_sync))

    old_map=c.execute('select provider,tile_url_template from map_settings where id=1').fetchone()
    if old_map and old_map['provider']=='selfhosted' and ('tileserver' in (old_map['tile_url_template'] or '') or '/styles/wardriver/' in (old_map['tile_url_template'] or '')):
        c.execute('update map_settings set tile_url_template=?,cache_allowed=0,updated_at=? where id=1',('/maps/region.pmtiles',now_sync))

    n=c.execute('select count(*) from observations').fetchone()[0]
    if n==0 and os.environ.get('WARDIVER_SAMPLE_DATA','1')=='1':
        now=datetime.now(timezone.utc).isoformat()
        c.executemany('insert into observations(id,kind,name,bssid,security,channel,latitude,longitude,seen_at,source,collection_method,inferred_method,inference_confidence,created_at) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',[
            (str(uuid.uuid4()),k,nm,b,sec,ch,lat,lon,now,'sample','unknown','unknown',0,now) for k,nm,b,sec,ch,lat,lon in SAMPLE
        ])
        c.commit()

    # These are migrations, not startup maintenance. Each O(n) scan is run once
    # and recorded so normal startup stays O(1).
    if not _migration_done(c,'v211_compat_cleanup'):
        c.execute('SELECT 1')  # Legacy name-only ALPR promotion is intentionally disabled.
        for r in c.execute("select source,count(*) n,min(created_at) first_at from observations where trim(coalesce(source,''))<>'' and source<>'sample' group by source").fetchall():
            exists=c.execute('select 1 from import_runs where source=? limit 1',(r['source'],)).fetchone()
            if not exists:
                c.execute('insert into import_runs(id,source,parsed,imported,rejected,replaced,imported_at,requested_method,collection_method,inferred_method,inference_confidence) values (?,?,?,?,?,?,?,?,?,?,?)',
                          (str(uuid.uuid4()),r['source'],r['n'],r['n'],0,0,r['first_at'] or datetime.now(timezone.utc).isoformat(),'unknown','unknown','unknown',0))
        _mark_migration(c,'v211_compat_cleanup')

    if not _migration_done(c,'v211_observation_identity'):
        backfill_observation_keys(c)
        _mark_migration(c,'v211_observation_identity')

    _v29_ensure_spatial_index(c, rebuild=not _migration_done(c,'v211_observation_rtree'))
    if not _migration_done(c,'v211_observation_rtree'):_mark_migration(c,'v211_observation_rtree')

    _ensure_materialized_rtrees(c)
    if not _migration_done(c,'v211_devices_v1'):
        rebuild_all_devices(c)
        _mark_migration(c,'v211_devices_v1')
    if not _migration_done(c,'v2116_flock_device_classification'):
        changed=0
        rows=c.execute('select device_key,kind,name,bssid,manufacturer from devices').fetchall()
        updates=[]
        for r in rows:
            flock=classify_flock(r['name'],r['manufacturer'],r['bssid'])
            if flock['is_flock'] and r['kind']!='alpr':
                updates.append(('alpr',r['device_key']));changed+=1
        if updates:c.executemany('update devices set kind=? where device_key=?',updates)
        _mark_migration(c,'v2116_flock_device_classification')
        if changed:print(f'v2.11.6 restored Flock camera classification for {changed:,} materialized devices',flush=True)
    if not _migration_done(c,'v211_tracks_v1'):
        rebuild_all_tracks(c)
        _mark_migration(c,'v211_tracks_v1')
    if not _migration_done(c,'v213_flock_confidence_v1'):
        n=refresh_flock_assessments(c,None)
        _mark_migration(c,'v213_flock_confidence_v1')
        print(f'v2.13 Flock confidence assessments: {n:,}',flush=True)
    if not _migration_done(c,'v2208_flock_conservative'):
        refresh_flock_assessments(c,None)
        _mark_migration(c,'v2208_flock_conservative')
    _ensure_materialized_rtrees(c, rebuild=not _migration_done(c,'v211_materialized_rtrees'))
    if not _migration_done(c,'v211_materialized_rtrees'):_mark_migration(c,'v211_materialized_rtrees')
    if not _migration_done(c,'v216_refreshed_flags'):
        c.execute("""update devices set refreshed_180=case when first_seen is not null and last_seen is not null and (julianday(last_seen)-julianday(first_seen))>=180 then 1 else 0 end,
                                  refreshed_at=case when first_seen is not null and last_seen is not null and (julianday(last_seen)-julianday(first_seen))>=180 then last_seen else null end""")
        _mark_migration(c,'v216_refreshed_flags')
    if not _migration_done(c,'v216_device_summaries'):
        n=rebuild_device_summaries(c);_mark_migration(c,'v216_device_summaries');print(f'v2.16 device summary buckets: {n:,}',flush=True)
    if not _migration_done(c,'v216_map_rollups'):
        n=rebuild_map_rollups(c);_mark_migration(c,'v216_map_rollups');print(f'v2.16 map rollup cells: {n:,}',flush=True)
    if not _migration_done(c,'v216_neighborhood_memberships'):
        n=rebuild_neighborhood_memberships(c);_mark_migration(c,'v216_neighborhood_memberships');print(f'v2.16 neighborhood memberships: {n:,}',flush=True)
    if not _migration_done(c,'v216_drive_stats'):
        n=rebuild_drive_scale_materializations(c);_mark_migration(c,'v216_drive_stats');print(f'v2.16 drive stats: {n:,}',flush=True)
    if not _migration_done(c,'v216_source_stats'):
        n=rebuild_source_scope_stats(c);_mark_migration(c,'v216_source_stats');print(f'v2.16 source scopes: {n:,}',flush=True)
    sync_neighborhood_catalog(c)
    try:c.execute('pragma optimize')
    except Exception:pass
    c.commit()


def db():
    global _DB_INITIALIZED
    c=_open_db()
    if not _DB_INITIALIZED:
        with _DB_INIT_LOCK:
            if not _DB_INITIALIZED:
                _initialize_database(c)
                _DB_INITIALIZED=True
    return c

def num(v):
    if v is None or str(v).strip() == '': return None
    try: return float(str(v).strip())
    except: return None

def clean_key(k):
    return ''.join(ch for ch in str(k or '').strip().lower() if ch.isalnum())

ALIASES = {
 'latitude': {'latitude','lat','trilat','gpslatitude','currentlatitude','bestlat','y'},
 'longitude': {'longitude','lon','lng','long','trilong','gpslongitude','currentlongitude','bestlon','x'},
 'name': {'name','ssid','essid','networkname','title','provider','devicename'},
 'bssid': {'bssid','mac','macaddress','netid','networkid'},
 'security': {'security','authmode','encryption','crypto','wep','privacy'},
 'channel': {'channel','chan','frequencychannel'},
 'rssi': {'rssi','signal','signallevel','signalstrength','dbm'},
 'beacon_interval': {'beaconinterval','beacon_interval','beacon'},
 'radio_capabilities': {'radiocapabilities','capabilities','capability','80211capabilities','wificapabilities'},
 'information_elements': {'informationelements','information_elements','ies','vendories','vendor_ies','vendorinformationelements'},
 'radio_fingerprint': {'radiofingerprint','radio_fingerprint','wififingerprint','beaconfingerprint'},
 'kind': {'kind','type','category','devicetype'},
 'seen_at': {'seenat','firstseen','lastseen','time','timestamp','date','discoveredat'},
}

def normalize_row(row):
    cleaned = {clean_key(k): v for k,v in row.items()}
    out = {}
    for dest, keys in ALIASES.items():
        for key in keys:
            if key in cleaned and cleaned[key] not in (None,''):
                out[dest] = cleaned[key]
                break
    return out

def normalize_kind(v, name=None):
    # Source files often label Flock cameras as generic WIFI. Prefer strong
    # device/SSID signals over the source's generic type field.
    kind = str(v or '').strip().lower()
    label = str(name or '').strip().lower()
    if 'alpr camera' in label:
        return 'alpr'
    if any(x in kind for x in ('alpr', 'flock', 'camera')):
        return 'alpr'
    return 'wifi'

def parse_seen_time(v):
    if not v: return None
    text=str(v).strip()
    for candidate in (text, text.replace('Z','+00:00')):
        try:
            dt=datetime.fromisoformat(candidate)
            if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
            ts=_plausible_obs_timestamp(dt.timestamp())
            return ts or None
        except: pass
    for fmt in ('%Y-%m-%d %H:%M:%S','%Y/%m/%d %H:%M:%S','%m/%d/%Y %H:%M:%S'):
        try:
            ts=_plausible_obs_timestamp(datetime.strptime(text,fmt).replace(tzinfo=timezone.utc).timestamp())
            return ts or None
        except: pass
    return None

def infer_collection_method(rows):
    import math, statistics
    pts=[]
    for raw in rows:
        r=normalize_row(raw); lat=num(r.get('latitude')); lon=num(r.get('longitude')); t=parse_seen_time(r.get('seen_at'))
        if lat is None or lon is None or t is None: continue
        if -90<=lat<=90 and -180<=lon<=180: pts.append((t,lat,lon))
    pts.sort()
    if len(pts)<4:return {'method':'unknown','confidence':0.15,'reason':'Not enough timestamped GPS points to infer movement reliably.'}
    speeds=[]; moved_m=0; direct_span=0
    def dist(a,b):
        lat1,lon1,lat2,lon2=map(math.radians,(a[1],a[2],b[1],b[2])); dlat=lat2-lat1; dlon=lon2-lon1
        q=math.sin(dlat/2)**2+math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
        return 6371000*2*math.asin(min(1,math.sqrt(q)))
    direct_span=dist(pts[0],pts[-1])
    for a,b in zip(pts,pts[1:]):
        dt=b[0]-a[0]
        if dt<=0 or dt>300: continue
        d=dist(a,b); moved_m+=d
        if d<2: continue
        kph=d/dt*3.6
        if kph<180:speeds.append(kph)
    if not speeds:
        return {'method':'stationary','confidence':0.82 if direct_span<100 else 0.45,'reason':'Timestamped points show little measurable movement.'}
    med=statistics.median(speeds); p75=statistics.quantiles(speeds,n=4,method='inclusive')[2] if len(speeds)>=4 else max(speeds)
    moving=sum(1 for x in speeds if x>1)/len(speeds)
    if med<0.8 and moved_m<180 and direct_span<120:
        return {'method':'stationary','confidence':0.88,'reason':f'GPS footprint stayed compact ({direct_span:.0f} m end-to-end) with near-zero movement speed.'}
    if med<=7 and p75<=11:
        conf=min(.96,.62+len(speeds)/120+.12*(1 if moving>.7 else 0)); return {'method':'warwalking','confidence':conf,'reason':f'Median movement speed {med:.1f} km/h; 75th percentile {p75:.1f} km/h.'}
    if med<=25 and p75<=38:
        conf=min(.93,.58+len(speeds)/140); return {'method':'bike','confidence':conf,'reason':f'Median movement speed {med:.1f} km/h; 75th percentile {p75:.1f} km/h.'}
    if med>=18:
        conf=min(.95,.62+len(speeds)/150); return {'method':'wardriving','confidence':conf,'reason':f'Median movement speed {med:.1f} km/h; 75th percentile {p75:.1f} km/h.'}
    return {'method':'unknown','confidence':.35,'reason':f'Mixed/ambiguous movement speeds (median {med:.1f} km/h).'}

def normalize_gear_list(value):
    if isinstance(value,list): items=value
    else: items=re.split(r'[\n,;]+',str(value or ''))
    out=[]
    for item in items:
        item=str(item or '').strip()
        if item and item not in out: out.append(item[:180])
        if len(out)>=40: break
    return out

def hardware_profiles_list():
    conn=db()
    try: rows=[dict(r) for r in conn.execute('select * from hardware_profiles order by lower(name),updated_at desc').fetchall()]
    finally: conn.close()
    for r in rows:
        try:r['gear']=json.loads(r.pop('gear_json') or '[]')
        except:r['gear']=[]
    return rows

def hardware_profile_snapshot(profile_id):
    pid=str(profile_id or '').strip()
    if not pid:return (None,None,None)
    conn=db()
    try:r=conn.execute('select id,name,gear_json from hardware_profiles where id=?',(pid,)).fetchone()
    finally:conn.close()
    if not r:raise ValueError('hardware profile not found')
    try:gear=normalize_gear_list(json.loads(r['gear_json'] or '[]'))
    except:gear=[]
    return (str(r['id']),str(r['name'] or '').strip(),json.dumps(gear,separators=(',',':')))

def _radio_fingerprint_from_row(r):
    supplied=str(r.get('radio_fingerprint') or '').strip()
    if supplied:return supplied[:160]
    parts=[]
    for key in ('beacon_interval','radio_capabilities','information_elements'):
        value=re.sub(r'\s+',' ',str(r.get(key) or '').strip().lower())
        if value:parts.append(f'{key}={value}')
    # Channel/security alone are far too generic to be a useful fingerprint.
    if not parts:return None
    canonical='|'.join(parts)
    return 'sha256:'+hashlib.sha256(canonical.encode('utf-8','ignore')).hexdigest()[:32]

def _insert_rows_conn(conn, rows, source, collection_method='unknown', inferred_method='unknown', inference_confidence=0,
                      session_id=None, session_name=None, hardware_profile_id=None, hardware_profile_name=None, hardware_profile_gear=None):
    now=datetime.now(timezone.utc).isoformat();count=0;rejected=0;parsed=0;keys=set();batch=[]
    sql="""insert into observations(id,kind,name,bssid,security,channel,rssi,beacon_interval,radio_capabilities,information_elements,radio_fingerprint,latitude,longitude,seen_at,source,collection_method,
             inferred_method,inference_confidence,created_at,session_id,session_name,hardware_profile_id,hardware_profile_name,
             hardware_profile_gear,bssid_norm,device_key) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
    for rawrow in rows:
        parsed+=1;r=normalize_row(rawrow);lat=num(r.get('latitude'));lon=num(r.get('longitude'))
        if lat is None or lon is None or not(-90<=lat<=90 and -180<=lon<=180):
            rejected+=1;continue
        kind=normalize_kind(r.get('kind'),r.get('name'));bssid_norm,device_key=_observation_identity(kind,r.get('name'),r.get('bssid'),lat,lon)
        keys.add(device_key)
        fingerprint=_radio_fingerprint_from_row(r)
        batch.append((str(uuid.uuid4()),kind,r.get('name'),r.get('bssid'),r.get('security'),str(r.get('channel') or ''),num(r.get('rssi')),
                      r.get('beacon_interval'),r.get('radio_capabilities'),r.get('information_elements'),fingerprint,
                      lat,lon,r.get('seen_at') or now,source,collection_method,inferred_method,float(inference_confidence or 0),now,
                      session_id or None,session_name or None,hardware_profile_id or None,hardware_profile_name or None,
                      hardware_profile_gear or None,bssid_norm,device_key))
        if len(batch)>=1000:
            conn.executemany(sql,batch);count+=len(batch);batch=[]
    if batch:conn.executemany(sql,batch);count+=len(batch)
    return count,rejected,parsed,keys

def insert_rows(rows, source, collection_method='unknown', inferred_method='unknown', inference_confidence=0, session_id=None, session_name=None,
                hardware_profile_id=None, hardware_profile_name=None, hardware_profile_gear=None):
    conn=db()
    try:
        conn.execute('begin immediate')
        count,rejected,parsed,keys=_insert_rows_conn(conn,rows,source,collection_method,inferred_method,inference_confidence,session_id,session_name,
                                                     hardware_profile_id,hardware_profile_name,hardware_profile_gear)
        refresh_devices_for_keys(conn,keys);refresh_drive_materialization(conn,source,session_id);conn.commit()
        return count,rejected
    except:
        conn.rollback();raise
    finally:conn.close()

def _looks_like_mac(v):
    return bool(re.fullmatch(r'(?i)(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}', str(v or '').strip()))

def _looks_like_headerless_wardrive(fields):
    # Common local wardrive log format:
    # BSSID,SSID,SECURITY,TIME,CHANNEL,RSSI,LAT,LON,ALT,ACCURACY,TYPE
    if len(fields) < 11 or not _looks_like_mac(fields[0]):
        return False
    lat, lon = num(fields[6]), num(fields[7])
    return lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180

def parse_csv(data):
    text = data.decode('utf-8-sig', errors='replace')
    lines = [x for x in text.splitlines() if x.strip()]
    if not lines: return []

    # First try the headerless wardrive format used by the local capture logs.
    # This is intentionally checked before DictReader so the first observation
    # is never mistaken for a header row.
    try:
        sample_dialect = csv.Sniffer().sniff('\n'.join(lines[:5]), delimiters=',;\t|')
    except Exception:
        sample_dialect = csv.excel
    parsed = list(csv.reader(io.StringIO('\n'.join(lines)), dialect=sample_dialect))
    if parsed and _looks_like_headerless_wardrive(parsed[0]):
        rows = []
        for f in parsed:
            if not _looks_like_headerless_wardrive(f):
                continue
            rows.append({
                'bssid': f[0].strip(),
                'name': f[1].strip(),
                'security': f[2].strip(),
                'seen_at': f[3].strip(),
                'channel': f[4].strip(),
                'signal': f[5].strip(),
                'latitude': f[6].strip(),
                'longitude': f[7].strip(),
                'altitude': f[8].strip(),
                'accuracy': f[9].strip(),
                'kind': f[10].strip(),
            })
        return rows

    # Otherwise find a normal CSV/WiGLE header.
    idx = 0
    found_header = False
    for i,line in enumerate(lines[:25]):
        low = clean_key(line)
        if any(a in low for a in ('latitude','trilat','gpslatitude')) and any(a in low for a in ('longitude','trilong','gpslongitude')):
            idx = i; found_header = True; break
    sample = '\n'.join(lines[idx:idx+5])
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=',;\t|')
    except Exception:
        dialect = csv.excel
    return list(csv.DictReader(io.StringIO('\n'.join(lines[idx:])), dialect=dialect))


def iter_csv_stream(fileobj):
    """Yield CSV rows without materializing the entire upload in memory."""
    fileobj.seek(0)
    wrapper=io.TextIOWrapper(fileobj,encoding='utf-8-sig',errors='replace',newline='')
    try:
        prefix=[]
        while len(prefix)<25:
            line=wrapper.readline()
            if not line:break
            if line.strip():prefix.append(line)
        if not prefix:return
        try:dialect=csv.Sniffer().sniff(''.join(prefix[:5]),delimiters=',;\t|')
        except Exception:dialect=csv.excel
        first=list(csv.reader([prefix[0]],dialect=dialect))[0] if prefix else []
        if first and _looks_like_headerless_wardrive(first):
            reader=csv.reader(itertools.chain(prefix,wrapper),dialect=dialect)
            for f in reader:
                if not _looks_like_headerless_wardrive(f):continue
                yield {'bssid':f[0].strip(),'name':f[1].strip(),'security':f[2].strip(),'seen_at':f[3].strip(),
                       'channel':f[4].strip(),'signal':f[5].strip(),'latitude':f[6].strip(),'longitude':f[7].strip(),
                       'altitude':f[8].strip(),'accuracy':f[9].strip(),'kind':f[10].strip()}
            return
        idx=0
        for i,line in enumerate(prefix):
            low=clean_key(line)
            if any(a in low for a in ('latitude','trilat','gpslatitude')) and any(a in low for a in ('longitude','trilong','gpslongitude')):
                idx=i;break
        header_and_rest=itertools.chain(prefix[idx:],wrapper)
        reader=csv.DictReader(header_and_rest,dialect=dialect)
        for row in reader:yield row
    finally:
        # Do not close the caller-owned SpooledTemporaryFile when the generator
        # is sampled and discarded before the second full pass.
        try:wrapper.detach()
        except Exception:pass


def parse_geojson(data):
    obj=json.loads(data.decode('utf-8')); rows=[]
    if isinstance(obj, dict) and obj.get('type')=='FeatureCollection':
        for f in obj.get('features',[]):
            g=f.get('geometry') or {}; p=f.get('properties') or {}; coords=g.get('coordinates') or []
            if g.get('type')=='Point' and len(coords)>=2:
                rows.append({**p,'longitude':coords[0],'latitude':coords[1]})
    elif isinstance(obj,list): rows=obj
    elif isinstance(obj,dict): rows=[obj]
    return rows

def parse_gpx(data):
    root=ET.fromstring(data); rows=[]
    for el in root.iter():
        tag=el.tag.split('}')[-1]
        if tag not in ('wpt','trkpt'): continue
        row={'latitude':el.attrib.get('lat'),'longitude':el.attrib.get('lon')}
        for c in list(el):
            t=c.tag.split('}')[-1]
            if t in ('name','time','desc'):
                row['name' if t=='name' else 'seen_at' if t=='time' else 'description']=c.text
        rows.append(row)
    return rows


def geocode_city(query):
    q = str(query or '').strip()
    if not q or len(q) > 160:
        raise ValueError('enter a city, optionally with state/country')
    params = urllib.parse.urlencode({
        'q': q,
        'format': 'jsonv2',
        'limit': 1,
        'addressdetails': 1,
        'featureType': 'settlement',
    })
    req = urllib.request.Request(
        f"{GEOCODER_URL}/search?{params}",
        headers={
            'User-Agent': 'Wardriver.org-local/2.0 (local route planner)',
            'Accept': 'application/json',
        },
    )
    with urllib.request.urlopen(req, timeout=GEOCODER_TIMEOUT) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    if not data:
        raise ValueError('city not found')
    r = data[0]
    lat, lon = float(r['lat']), float(r['lon'])
    bbox = [float(x) for x in (r.get('boundingbox') or [])]
    return {
        'ok': True,
        'query': q,
        'lat': lat,
        'lon': lon,
        'display_name': r.get('display_name') or q,
        'boundingbox': bbox if len(bbox) == 4 else None,
        'geocoder': GEOCODER_URL,
    }


def route_instruction(step):
    man = step.get('maneuver') or {}
    typ = str(man.get('type') or '').replace('_', ' ').strip().lower()
    mod = str(man.get('modifier') or '').replace('_', ' ').strip().lower()
    road = str(step.get('name') or '').strip()
    ref = str(step.get('ref') or '').strip()
    road_label = road or ref or 'the road'
    if typ == 'depart':
        text = f"Depart on {road_label}"
    elif typ == 'arrive':
        text = 'Arrive at coverage stop'
    elif typ in ('turn','continue','new name','fork','end of road','on ramp','off ramp','merge'):
        action = typ.title()
        if typ == 'new name': action = 'Continue'
        if mod: action += f" {mod}"
        text = f"{action} onto {road_label}" if road_label != 'the road' else action
    elif typ in ('roundabout','rotary','roundabout turn','exit roundabout','exit rotary'):
        exit_no = man.get('exit')
        text = 'Enter the roundabout'
        if exit_no: text += f" and take exit {exit_no}"
        if road: text += f" onto {road}"
    else:
        text = (typ.title() if typ else 'Continue')
        if mod: text += f" {mod}"
        if road: text += f" on {road}"
    return text

def fetch_driving_route(points):
    if len(points) < 2:
        raise ValueError('at least two route points are required')
    if len(points) > 24:
        raise ValueError('too many route points; maximum is 24')
    coords=[]
    for p in points:
        lat=num(p.get('lat')); lon=num(p.get('lon'))
        if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError('invalid route coordinate')
        coords.append((lat,lon))
    coord_text=';'.join(f'{lon:.7f},{lat:.7f}' for lat,lon in coords)
    url=f"{ROUTER_URL}/route/v1/driving/{coord_text}?steps=true&geometries=geojson&overview=full&continue_straight=false"
    req=urllib.request.Request(url, headers={'User-Agent':'WardriverLocal/1.0'})
    with urllib.request.urlopen(req, timeout=ROUTER_TIMEOUT) as r:
        obj=json.loads(r.read().decode('utf-8'))
    if obj.get('code') != 'Ok' or not obj.get('routes'):
        raise ValueError(obj.get('message') or obj.get('code') or 'routing service returned no route')
    route=obj['routes'][0]
    steps=[]
    n=1
    for leg_i,leg in enumerate(route.get('legs') or [], start=1):
        for step in leg.get('steps') or []:
            man=step.get('maneuver') or {}
            loc=man.get('location') or [None,None]
            steps.append({
                'number': n,
                'leg': leg_i,
                'instruction': route_instruction(step),
                'distance_m': float(step.get('distance') or 0),
                'duration_s': float(step.get('duration') or 0),
                'road': step.get('name') or '',
                'type': man.get('type') or '',
                'modifier': man.get('modifier') or '',
                'location': {'lon':loc[0], 'lat':loc[1]} if len(loc)>=2 else None,
            })
            n += 1
    geometry=(route.get('geometry') or {}).get('coordinates') or []
    return {
        'ok': True,
        'router': ROUTER_URL,
        'distance_m': float(route.get('distance') or 0),
        'duration_s': float(route.get('duration') or 0),
        'geometry': geometry,
        'steps': steps,
        'waypoints': [{'lat':lat,'lon':lon} for lat,lon in coords],
    }


def map_settings(conn=None):
    own = conn is None
    conn = conn or db()
    row = conn.execute('select * from map_settings where id=1').fetchone()
    out = dict(row) if row else {'provider':'none','tile_url_template':'','attribution':'','cache_allowed':0}
    out['cache_allowed'] = bool(out.get('cache_allowed'))
    if own: conn.close()
    return out

def validate_tile_template(template):
    template = str(template or '').strip()
    if not template:
        raise ValueError('tile URL template is required')
    if not all(x in template for x in ('{z}','{x}','{y}')):
        raise ValueError('tile URL must contain {z}, {x}, and {y}')
    parsed = urllib.parse.urlsplit(template)
    if parsed.scheme not in ('http','https') or not parsed.netloc:
        raise ValueError('tile URL must use http:// or https://')
    return template

def tile_path(z,x,y):
    return TILE_DIR / str(z) / str(x) / f'{y}.png'

def tile_remote_url(cfg,z,x,y):
    return cfg['tile_url_template'].replace('{z}',str(z)).replace('{x}',str(x)).replace('{y}',str(y))

def get_tile(z,x,y):
    if z < 0 or z > 19: raise ValueError('invalid zoom')
    n=2**z; x=x % n
    if y < 0 or y >= n: raise ValueError('invalid tile')
    cfg=map_settings()
    if cfg.get('provider') == 'none':
        raise ValueError('basemap disabled')
    if cfg.get('provider') == 'selfhosted':
        raise ValueError('self-hosted PMTiles is read directly by MapLibre; raster tile proxy is not used')
    template=validate_tile_template(cfg.get('tile_url_template'))
    path=tile_path(z,x,y)
    if cfg.get('cache_allowed') and path.exists(): return path.read_bytes(), True
    req=urllib.request.Request(tile_remote_url(cfg,z,x,y),headers={'User-Agent':'WardriverLocal/2.9.5 basemap proxy','Accept':'image/*'})
    with urllib.request.urlopen(req,timeout=TILE_TIMEOUT) as r:
        data=r.read(); ctype=(r.headers.get('Content-Type') or '').lower()
    if len(data) < 100: raise ValueError('tile download failed')
    if cfg.get('cache_allowed'):
        path.parent.mkdir(parents=True,exist_ok=True)
        tmp=path.with_suffix('.tmp'); tmp.write_bytes(data); tmp.replace(path)
    return data, False

def tile_cache_stats():
    TILE_DIR.mkdir(parents=True,exist_ok=True)
    files=list(TILE_DIR.rglob('*.png'))
    return {'tiles':len(files),'bytes':sum(f.stat().st_size for f in files)}

def pmtiles_info():
    exists=PMTILES_PATH.exists() and PMTILES_PATH.is_file()
    size=PMTILES_PATH.stat().st_size if exists else 0
    return {
        'exists': bool(exists),
        'path': str(PMTILES_PATH),
        'url': '/maps/region.pmtiles',
        'bytes': int(size),
        'mb': round(size/1048576, 1) if size else 0,
        'modified_at': datetime.fromtimestamp(PMTILES_PATH.stat().st_mtime, timezone.utc).isoformat() if exists else None,
    }

def test_map_provider(cfg=None):
    cfg=cfg or map_settings()
    provider=cfg.get('provider')
    if provider == 'none':
        return {'ok':True,'provider':'none','message':'Basemap is disabled; observation overlays remain available.'}
    if provider == 'selfhosted':
        info=pmtiles_info()
        if not info['exists']:
            raise ValueError('maps/region.pmtiles is missing; run docker compose --profile map-setup run --rm map-init')
        if info['bytes'] < 1024:
            raise ValueError('maps/region.pmtiles is too small to be a valid regional archive')
        with PMTILES_PATH.open('rb') as f:
            magic=f.read(7)
        if magic != b'PMTiles':
            raise ValueError('maps/region.pmtiles does not have a PMTiles header')
        return {'ok':True,'provider':'selfhosted','endpoint':'/maps/region.pmtiles','content_type':'application/vnd.pmtiles',**info}
    template=validate_tile_template(cfg.get('tile_url_template'))
    url=template.replace('{z}','0').replace('{x}','0').replace('{y}','0')
    req=urllib.request.Request(url,headers={'User-Agent':'WardriverLocal/2.9.5 basemap test','Accept':'image/*'})
    with urllib.request.urlopen(req,timeout=TILE_TIMEOUT) as r:
        data=r.read(512); status=getattr(r,'status',200); ctype=r.headers.get('Content-Type','')
    if status >= 400 or len(data) < 16:
        raise ValueError(f'tile server returned HTTP {status}')
    return {'ok':True,'provider':provider,'endpoint':url,'content_type':ctype}

def slippy_tile(lat,lon,z):
    import math
    lat=max(-85.05112878,min(85.05112878,float(lat)))
    n=2**z
    x=int((float(lon)+180.0)/360.0*n)
    y=int((1.0-math.asinh(math.tan(math.radians(lat)))/math.pi)/2.0*n)
    return x,y

def data_quality(conn):
    total=conn.execute('select count(*) from observations').fetchone()[0]
    missing_bssid=conn.execute("select count(*) from observations where kind='wifi' and trim(coalesce(bssid,''))='' ").fetchone()[0]
    malformed=0
    for r in conn.execute("select bssid from observations where trim(coalesce(bssid,''))<>''"):
        if len(normalize_mac(r['bssid'])) < 12: malformed += 1
    duplicates=conn.execute("select coalesce(sum(n-1),0) from (select count(*) n from observations where trim(coalesce(bssid,''))<>'' group by upper(replace(replace(bssid,':',''),'-','')), round(latitude,6), round(longitude,6) having count(*)>1)").fetchone()[0]
    stale_cut=(datetime.now(timezone.utc)-timedelta(days=180)).isoformat()
    stale=conn.execute("select count(*) from observations where coalesce(seen_at,created_at) < ?",(stale_cut,)).fetchone()[0]
    future=conn.execute("select count(*) from observations where coalesce(seen_at,created_at) > ?",((datetime.now(timezone.utc)+timedelta(days=1)).isoformat(),)).fetchone()[0]
    return {'total':total,'missing_bssid':missing_bssid,'malformed_bssid':malformed,'duplicate_samples':int(duplicates or 0),'stale_180d':stale,'future_timestamps':future}

def compare_sources(conn,a,b):
    def rows(src):
        out={}
        for r in conn.execute("select * from observations where source=? and trim(coalesce(bssid,''))<>'' order by created_at desc",(src,)):
            k=normalize_mac(r['bssid'])
            if k and k not in out: out[k]=dict(r)
        return out
    A,B=rows(a),rows(b); ka,kb=set(A),set(B)
    added=sorted(kb-ka); removed=sorted(ka-kb); common=ka&kb
    changed=[]; moved=[]
    for k in common:
        x,y=A[k],B[k]; diffs=[]
        if (x.get('name') or '') != (y.get('name') or ''): diffs.append('SSID/name')
        if (x.get('security') or '') != (y.get('security') or ''): diffs.append('security')
        if str(x.get('channel') or '') != str(y.get('channel') or ''): diffs.append('channel')
        if diffs: changed.append({'bssid':x.get('bssid') or k,'before':x,'after':y,'fields':diffs})
        # ~100m threshold
        dlat=(float(x['latitude'])-float(y['latitude']))*111000
        dlon=(float(x['longitude'])-float(y['longitude']))*111000*max(.2,__import__('math').cos(__import__('math').radians((float(x['latitude'])+float(y['latitude']))/2)))
        dist=(dlat*dlat+dlon*dlon)**.5
        if dist>100: moved.append({'bssid':x.get('bssid') or k,'meters':round(dist),'before':x,'after':y})
    return {'source_a':a,'source_b':b,'added_count':len(added),'removed_count':len(removed),'changed_count':len(changed),'moved_count':len(moved),
            'added':[B[k] for k in added[:100]],'removed':[A[k] for k in removed[:100]],'changed':changed[:100],'moved':sorted(moved,key=lambda x:-x['meters'])[:100]}


SYNC_PRIVACY_TRANSFORM_VERSION = 1

SYNC_PRIVACY_PROFILE_DEFAULTS = {
    'full': {
        'profile':'full','ssid_mode':'exact','bssid_mode':'exact','location_precision_m':0,'time_precision':'exact','delay_hours':0,
        'strip_movement':False,'strip_local_metadata':False,'strip_radio_fingerprint':False,
        'upload_wifi':True,'upload_bluetooth':True,'upload_alpr':True,'upload_hardware_inference':True,'upload_photos':True,
        'photo_strip_exif':True,'photo_blur_faces':False,'photo_blur_plates':False,'photo_max_dimension':2400,
        'attribution_mode':'username','public_alias':'','zones':[]
    },
    'balanced': {
        'profile':'balanced','ssid_mode':'hash','bssid_mode':'pseudonym','location_precision_m':50,'time_precision':'date','delay_hours':24,
        'strip_movement':True,'strip_local_metadata':True,'strip_radio_fingerprint':True,
        'upload_wifi':True,'upload_bluetooth':False,'upload_alpr':True,'upload_hardware_inference':True,'upload_photos':False,
        'photo_strip_exif':True,'photo_blur_faces':True,'photo_blur_plates':True,'photo_max_dimension':1600,
        'attribution_mode':'anonymous','public_alias':'','zones':[]
    },
    'privacy': {
        'profile':'privacy','ssid_mode':'remove','bssid_mode':'vendor','location_precision_m':1000,'time_precision':'none','delay_hours':168,
        'strip_movement':True,'strip_local_metadata':True,'strip_radio_fingerprint':True,
        'upload_wifi':True,'upload_bluetooth':False,'upload_alpr':False,'upload_hardware_inference':False,'upload_photos':False,
        'photo_strip_exif':True,'photo_blur_faces':True,'photo_blur_plates':True,'photo_max_dimension':1200,
        'attribution_mode':'anonymous','public_alias':'','zones':[]
    }
}

def _privacy_default(profile='balanced'):
    return json.loads(json.dumps(SYNC_PRIVACY_PROFILE_DEFAULTS.get(profile,SYNC_PRIVACY_PROFILE_DEFAULTS['balanced'])))

def _privacy_normalize(raw):
    raw=dict(raw or {});profile=str(raw.get('profile') or 'balanced').strip().lower()
    if profile not in ('full','balanced','privacy','custom'):profile='balanced'
    base=_privacy_default('balanced' if profile=='custom' else profile)
    if profile=='custom':base.update(raw)
    # Explicit values from clients are accepted even for named profiles so a
    # UI may preview edits before switching to Custom.
    for k in list(base):
        if k in raw:base[k]=raw[k]
    base['profile']=profile
    if base.get('ssid_mode') not in ('exact','hash','remove'):base['ssid_mode']='hash'
    if base.get('bssid_mode') not in ('exact','pseudonym','vendor'):base['bssid_mode']='pseudonym'
    try:loc=int(base.get('location_precision_m') or 0)
    except:loc=50
    if loc not in (0,25,50,100,250,1000):loc=50
    base['location_precision_m']=loc
    if base.get('time_precision') not in ('exact','hour','date','none'):base['time_precision']='date'
    try:delay=int(base.get('delay_hours') or 0)
    except:delay=24
    if delay not in (0,24,168,720):delay=24
    base['delay_hours']=delay
    for k in ('strip_movement','strip_local_metadata','strip_radio_fingerprint','upload_wifi','upload_bluetooth','upload_alpr','upload_hardware_inference','upload_photos','photo_strip_exif','photo_blur_faces','photo_blur_plates'):
        base[k]=bool(base.get(k))
    try:dim=int(base.get('photo_max_dimension') or 1600)
    except:dim=1600
    base['photo_max_dimension']=min(4000,max(800,dim))
    if base.get('attribution_mode') not in ('anonymous','username','alias'):base['attribution_mode']='anonymous'
    base['public_alias']=str(base.get('public_alias') or '').strip()[:80]
    zones=[]
    for z in list(base.get('zones') or [])[:100]:
        if not isinstance(z,dict):continue
        shape=str(z.get('shape') or 'circle').lower();name=str(z.get('name') or 'Private zone').strip()[:80]
        zid=str(z.get('id') or uuid.uuid4())[:80]
        if shape=='circle':
            try:lat=float(z.get('lat'));lon=float(z.get('lon'));radius=float(z.get('radius_m'))
            except:continue
            if -90<=lat<=90 and -180<=lon<=180 and 25<=radius<=100000:zones.append({'id':zid,'name':name,'shape':'circle','lat':lat,'lon':lon,'radius_m':radius})
        elif shape=='polygon':
            pts=[]
            for pt in list(z.get('points') or [])[:100]:
                try:lon=float(pt[0]);lat=float(pt[1])
                except:continue
                if -90<=lat<=90 and -180<=lon<=180:pts.append([lon,lat])
            if len(pts)>=3:zones.append({'id':zid,'name':name,'shape':'polygon','points':pts})
    base['zones']=zones
    return base

def _privacy_rating(cfg):
    c=_privacy_normalize(cfg);score=0.0
    score += {'exact':0,'hash':1.5,'remove':3}.get(c['ssid_mode'],0)
    score += {'exact':0,'pseudonym':2,'vendor':3}.get(c['bssid_mode'],0)
    score += {0:0,25:.5,50:1,100:2,250:3,1000:4}.get(c['location_precision_m'],0)
    score += {'exact':0,'hour':.5,'date':1.5,'none':3}.get(c['time_precision'],0)
    score += {0:0,24:1,168:2,720:3}.get(c['delay_hours'],0)
    score += 1 if c['strip_movement'] else 0
    score += 1 if c['strip_local_metadata'] else 0
    score += 1 if c['strip_radio_fingerprint'] else 0
    score += 1 if c['attribution_mode']=='anonymous' else .5 if c['attribution_mode']=='alias' else 0
    score += 1 if c['zones'] else 0
    score += .5 if not c['upload_bluetooth'] else 0
    score += .5 if not c['upload_alpr'] else 0
    if score<=1.0:return {'label':'Full fidelity','level':'full','score':round(score,1)}
    if score>=12:return {'label':'Strong','level':'strong','score':round(score,1)}
    if score>=7:return {'label':'Moderate','level':'moderate','score':round(score,1)}
    return {'label':'Light','level':'light','score':round(score,1)}

def sync_privacy_settings(conn=None, include_secret=False):
    own=conn is None;conn=conn or db();row=conn.execute('select * from sync_privacy_settings where id=1').fetchone()
    if not row:
        now=datetime.now(timezone.utc).isoformat();secret=os.urandom(32).hex();cfg=_privacy_default('balanced')
        conn.execute('insert into sync_privacy_settings(id,profile,config_json,secret,updated_at) values (1,?,?,?,?)',('balanced',json.dumps(cfg,separators=(',',':')),secret,now));conn.commit();row=conn.execute('select * from sync_privacy_settings where id=1').fetchone()
    d=dict(row);cfg=_privacy_normalize(json.loads(d.get('config_json') or '{}'));cfg['profile']=str(d.get('profile') or cfg.get('profile') or 'balanced')
    out={'ok':True,'config':cfg,'rating':_privacy_rating(cfg),'transform_version':SYNC_PRIVACY_TRANSFORM_VERSION,
         'profiles':{'full':'Full fidelity','balanced':'Balanced','privacy':'Privacy first','custom':'Custom'},
         'profile_defaults':{k:_privacy_default(k) for k in ('full','balanced','privacy')},
         'local_only_note':'These settings transform only wardriver.org upload payloads. Local SQLite rows, local map data, exports, and local photos are not modified.'}
    if include_secret:out['secret']=str(d.get('secret') or '')
    if own:conn.close()
    return out

def save_sync_privacy_settings(raw):
    cfg=_privacy_normalize(raw);profile=cfg['profile'];conn=db();row=conn.execute('select secret from sync_privacy_settings where id=1').fetchone();secret=str(row['secret'] if row else '') or os.urandom(32).hex();now=datetime.now(timezone.utc).isoformat()
    conn.execute('insert or replace into sync_privacy_settings(id,profile,config_json,secret,updated_at) values (1,?,?,?,?)',(profile,json.dumps(cfg,separators=(',',':')),secret,now));conn.commit();conn.close();return sync_privacy_settings()

def _privacy_hash(secret,label,value,n=16):
    raw=(str(label)+'\\0'+str(value or '')).encode('utf-8','ignore');key=bytes.fromhex(secret) if re.fullmatch(r'[0-9a-fA-F]{64}',str(secret or '')) else str(secret or '').encode()[:32]
    return hashlib.blake2s(raw,key=key,digest_size=max(4,min(16,n//2))).hexdigest()[:n]

def _privacy_snap(lat,lon,meters):
    if not meters:return float(lat),float(lon)
    lat=float(lat);lon=float(lon);lat_step=float(meters)/111320.0;cos=max(.15,abs(math.cos(math.radians(lat))));lon_step=float(meters)/(111320.0*cos)
    return round(round(lat/lat_step)*lat_step,7),round(round(lon/lon_step)*lon_step,7)

def _privacy_point_in_polygon(lat,lon,points):
    inside=False;j=len(points)-1
    for i in range(len(points)):
        xi,yi=points[i];xj,yj=points[j]
        if ((yi>lat)!=(yj>lat)) and (lon < (xj-xi)*(lat-yi)/((yj-yi) or 1e-12)+xi):inside=not inside
        j=i
    return inside

def _privacy_zone_match(lat,lon,zones):
    for z in zones or []:
        if z.get('shape')=='circle':
            if _haversine_km(float(lat),float(lon),float(z['lat']),float(z['lon']))*1000<=float(z['radius_m']):return z
        elif z.get('shape')=='polygon' and _privacy_point_in_polygon(float(lat),float(lon),z.get('points') or []):return z
    return None

def _privacy_kind(row):
    k=str(row.get('kind') or 'wifi').strip().lower()
    if k in ('ble','bluetooth','bt'):return 'bluetooth'
    flock=classify_flock(row.get('name'),row.get('manufacturer'),row.get('bssid'))
    if k=='alpr' or flock.get('is_flock'):return 'alpr'
    return 'wifi'

def _privacy_time(value,mode):
    if mode=='none' or not value:return None
    if mode=='exact':return str(value)
    ts=_parse_obs_time(value)
    if not ts:return None
    dt=datetime.fromtimestamp(ts,timezone.utc)
    if mode=='hour':return dt.replace(minute=0,second=0,microsecond=0).isoformat()
    if mode=='date':return dt.date().isoformat()
    return str(value)

def _privacy_before_sample(row):
    keys=('kind','name','bssid','security','channel','rssi','latitude','longitude','seen_at','source','collection_method','session_id','session_name','hardware_profile_name')
    return {k:row.get(k) for k in keys if k in row and row.get(k) not in (None,'')}

def privacy_transform_observation(row,cfg,secret,now_ts=None):
    c=_privacy_normalize(cfg);r=dict(row);now_ts=float(now_ts or time.time());group=_privacy_kind(r)
    if group=='wifi' and not c['upload_wifi']:return None,'category_wifi'
    if group=='bluetooth' and not c['upload_bluetooth']:return None,'category_bluetooth'
    if group=='alpr' and not c['upload_alpr']:return None,'category_alpr'
    try:lat=float(r.get('latitude'));lon=float(r.get('longitude'))
    except:return None,'invalid_location'
    zone=_privacy_zone_match(lat,lon,c['zones'])
    if zone:return None,'private_zone'
    if c['delay_hours']:
        ts=_parse_obs_time(r.get('seen_at'))
        if not ts:return None,'delay_unknown_time'
        if ts>now_ts-c['delay_hours']*3600:return None,'delay_recent'
    out={k:v for k,v in r.items() if v is not None}
    # Never expose local database keys under privacy transforms.
    if c['profile']!='full' or c['strip_local_metadata']:
        if out.get('id'):out['id']='obs_'+_privacy_hash(secret,'obs',out.get('id'),20)
    ssid=str(out.get('name') or '')
    if c['ssid_mode']=='remove':out.pop('name',None)
    elif c['ssid_mode']=='hash' and ssid:out['name']='ssid_'+_privacy_hash(secret,'ssid',ssid,16)
    bssid=str(out.get('bssid') or '')
    if c['bssid_mode']=='pseudonym' and bssid:out['bssid']='dev_'+_privacy_hash(secret,'bssid',normalize_mac(bssid) or bssid,20)
    elif c['bssid_mode']=='vendor' and bssid:
        raw=normalize_mac(bssid);out['bssid']=(':'.join(raw[i:i+2] for i in range(0,6,2))+':xx:xx:xx') if len(raw)>=6 else None
        if out.get('bssid') is None:out.pop('bssid',None)
    lat2,lon2=_privacy_snap(lat,lon,c['location_precision_m']);out['latitude']=lat2;out['longitude']=lon2
    seen=_privacy_time(out.get('seen_at'),c['time_precision'])
    if seen is None:out.pop('seen_at',None)
    else:out['seen_at']=seen
    if c['strip_movement']:
        for k in ('speed','heading','course','bearing','altitude','accuracy','gps_accuracy','velocity','track','route','route_id','seen_ts'):out.pop(k,None)
    if c['strip_local_metadata']:
        for k in ('source','session_id','session_name','hardware_profile_id','hardware_profile_name','hardware_profile_gear','created_at','inferred_method','inference_confidence','device_key','bssid_norm'):out.pop(k,None)
    if c['strip_radio_fingerprint']:
        for k in ('radio_fingerprint','information_elements','radio_capabilities','beacon_interval'):out.pop(k,None)
    if not c['upload_hardware_inference']:
        for k in ('manufacturer','hardware_type','hardware_label','hardware_confidence','hardware_reason'):out.pop(k,None)
    return out,None

def _privacy_remote_source(source,cfg,secret):
    if cfg.get('profile')=='full' and not cfg.get('strip_local_metadata'):return str(source)
    return 'import_'+_privacy_hash(secret,'source',source,12)

def _privacy_public_config(cfg):
    # Private-zone coordinates and local pseudonym secret never leave the machine.
    c=dict(_privacy_normalize(cfg));zones=c.pop('zones',[]);c['private_zones_applied']=bool(zones);c['private_zone_count']=len(zones);c.pop('public_alias',None)
    return c

def sync_privacy_preview(source,sample_limit=2,override_cfg=None):
    source=str(source or '').strip()
    if not source:raise ValueError('choose an import to preview')
    p=sync_privacy_settings(include_secret=True);cfg=_privacy_normalize(override_cfg) if isinstance(override_cfg,dict) else p['config'];secret=p['secret'];conn=db();total=int(conn.execute('select count(*) from observations where source=?',(source,)).fetchone()[0] or 0)
    if not total:conn.close();raise ValueError('no observations found for that import')
    counts=Counter();samples=[];eligible=0;now_ts=time.time()
    cur=conn.execute('select * from observations where source=? order by rowid',(source,))
    for row in cur:
        d=dict(row);out,reason=privacy_transform_observation(d,cfg,secret,now_ts)
        if reason:counts[reason]+=1;continue
        eligible+=1
        if len(samples)<max(1,min(5,int(sample_limit))):samples.append({'local':_privacy_before_sample(d),'upload':out})
    conn.close();excluded=total-eligible
    return {'ok':True,'source':source,'records_local':total,'records_upload':eligible,'records_excluded':excluded,'excluded':dict(counts),'samples':samples,
            'config':cfg,'rating':_privacy_rating(cfg),'remote_source':_privacy_remote_source(source,cfg,secret),
            'photos_note':'Flock photos remain local-only in this build. Photo privacy controls are stored now and will gate any future wardriver.org photo endpoint.',
            'local_only_note':p['local_only_note']}

def sync_settings(conn=None, include_token=False):
    own = conn is None
    conn = conn or db()
    row = conn.execute('select * from sync_settings where id=1').fetchone()
    if own: conn.close()
    d = dict(row) if row else {'enabled':0,'base_url':SYNC_DEFAULT_BASE_URL,'api_token':None}
    token = str(d.get('api_token') or '')
    out = {
        'enabled': bool(d.get('enabled')),
        'base_url': str(d.get('base_url') or SYNC_DEFAULT_BASE_URL).rstrip('/'),
        'has_token': bool(token),
        'token_hint': (('••••' + token[-4:]) if token else ''),
        'health_path': SYNC_HEALTH_PATH,
        'import_path': SYNC_IMPORT_PATH,
        'chunk_size': SYNC_CHUNK_SIZE,
    }
    if include_token: out['api_token'] = token
    return out

def remote_url(base, path):
    base = str(base or '').strip().rstrip('/')
    if not base: raise ValueError('API base URL is required')
    if not re.match(r'^https?://', base, re.I): raise ValueError('API base URL must start with http:// or https://')
    return base + '/' + str(path or '').lstrip('/')

def wardriver_remote_request(method, path, payload=None, settings=None):
    settings = settings or sync_settings(include_token=True)
    url = remote_url(settings['base_url'], path)
    headers = {'Accept':'application/json','User-Agent':f'WardriverLocal/{APP_VERSION}'}
    token = settings.get('api_token') or ''
    if token: headers['Authorization'] = 'Bearer ' + token
    data = None
    if payload is not None:
        data = json.dumps(payload, separators=(',',':')).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=SYNC_TIMEOUT) as r:
            body = r.read()
            ctype = str(r.headers.get('Content-Type') or '')
            parsed = None
            if body and ('json' in ctype or body[:1] in (b'{',b'[')):
                try: parsed = json.loads(body.decode('utf-8'))
                except: parsed = None
            return {'ok':200 <= r.status < 300,'status':r.status,'url':url,'data':parsed}
    except urllib.error.HTTPError as e:
        body = e.read(2048)
        msg = body.decode('utf-8','replace').strip() or str(e)
        raise RuntimeError(f'remote API returned HTTP {e.code}: {msg[:500]}')
    except Exception as e:
        raise RuntimeError(f'remote API connection failed: {e}')

def sync_source_to_remote(source):
    cfg=sync_settings(include_token=True)
    if not cfg['enabled']:raise ValueError('wardriver.org sync is disabled')
    if not cfg.get('api_token'):raise ValueError('API token is required before upload')
    privacy=sync_privacy_settings(include_secret=True);pcfg=privacy['config'];secret=privacy['secret'];rating=privacy['rating'];conn=db()
    total=int(conn.execute('select count(*) from observations where source=?',(source,)).fetchone()[0] or 0)
    if not total:conn.close();raise ValueError('no observations found for that import')
    run_id=str(uuid.uuid4());transfer_id=str(uuid.uuid4());now=datetime.now(timezone.utc).isoformat();remote_source=_privacy_remote_source(source,pcfg,secret)
    conn.execute('insert into sync_runs(id,source,status,records_total,records_sent,records_excluded,privacy_profile,privacy_rating,created_at) values (?,?,?,?,?,?,?,?,?)',
                 (run_id,source,'uploading',total,0,0,pcfg['profile'],rating['label'],now));conn.commit()
    cur=conn.execute('select * from observations where source=? order by rowid',(source,));sent=0;excluded=0;remote_id='';index=0;pending=None;now_ts=time.time();excluded_reasons=Counter()
    contributor={'mode':pcfg['attribution_mode']}
    if pcfg['attribution_mode']=='alias' and pcfg.get('public_alias'):contributor['alias']=pcfg['public_alias']
    privacy_meta={'transform_version':SYNC_PRIVACY_TRANSFORM_VERSION,'profile':pcfg['profile'],'rating':rating['label'],'settings':_privacy_public_config(pcfg),'contributor':contributor}
    def send_chunk(rows,final):
        nonlocal sent,remote_id,index
        index+=1
        payload={'schema':'wardriver.observations.v1','source':remote_source,'client':{'name':'wardriver-local','version':APP_VERSION},
                 'privacy':privacy_meta,'transfer':{'id':transfer_id,'chunk':index,'chunks':0,'final':bool(final)},'observations':rows}
        resp=wardriver_remote_request('POST',SYNC_IMPORT_PATH,payload,cfg)
        if isinstance(resp.get('data'),dict):remote_id=str(resp['data'].get('id') or resp['data'].get('import_id') or remote_id)
        sent+=len(rows);conn.execute('update sync_runs set records_sent=?,records_excluded=?,remote_id=? where id=?',(sent,excluded,remote_id,run_id));conn.commit()
    try:
        batch=[]
        for row in cur:
            out,reason=privacy_transform_observation(dict(row),pcfg,secret,now_ts)
            if reason:excluded+=1;excluded_reasons[reason]+=1;continue
            batch.append(out)
            if len(batch)>=SYNC_CHUNK_SIZE:
                if pending is not None:send_chunk(pending,False)
                pending=batch;batch=[]
        if batch:
            if pending is not None:send_chunk(pending,False)
            pending=batch
        if pending is not None:send_chunk(pending,True)
        else:
            # Nothing survives privacy filters. Do not contact the remote with
            # local metadata just to send an empty transfer.
            pass
        done=datetime.now(timezone.utc).isoformat();conn.execute("update sync_runs set status='complete',records_sent=?,records_excluded=?,remote_id=?,completed_at=? where id=?",(sent,excluded,remote_id,done,run_id));conn.commit()
        return {'ok':True,'run_id':run_id,'source':source,'remote_source':remote_source,'records_sent':sent,'records_total':total,'records_excluded':excluded,
                'excluded':dict(excluded_reasons),'remote_id':remote_id,'privacy_profile':pcfg['profile'],'privacy_rating':rating['label']}
    except Exception as e:
        done=datetime.now(timezone.utc).isoformat();conn.execute("update sync_runs set status='failed',records_sent=?,records_excluded=?,error=?,completed_at=? where id=?",(sent,excluded,str(e)[:1000],done,run_id));conn.commit();raise
    finally:conn.close()

def wigle_settings(conn=None, include_token=False):
    own=conn is None
    conn=conn or db()
    row=conn.execute('select * from wigle_settings where id=1').fetchone()
    if own: conn.close()
    d=dict(row) if row else {'enabled':0,'api_token':None,'donate':0}
    token=str(d.get('api_token') or '')
    out={'enabled':bool(d.get('enabled')),'has_token':bool(token),'token_hint':(('••••'+token[-4:]) if token else ''),'donate':bool(d.get('donate')),'api_base':WIGLE_API_BASE,'profile_path':WIGLE_PROFILE_PATH,'upload_path':WIGLE_UPLOAD_PATH}
    if include_token: out['api_token']=token
    return out

def _wigle_auth_headers(token):
    token=str(token or '').strip()
    if not token: raise ValueError('WiGLE API token is required')
    return {'Authorization':'Basic '+token,'Accept':'application/json','User-Agent':f'WardriverLocal/{APP_VERSION}'}

def wigle_test_connection():
    cfg=wigle_settings(include_token=True)
    if not cfg['enabled']: raise ValueError('enable WiGLE uploads first')
    url=WIGLE_API_BASE+WIGLE_PROFILE_PATH
    req=urllib.request.Request(url,headers=_wigle_auth_headers(cfg.get('api_token')),method='GET')
    try:
        with urllib.request.urlopen(req,timeout=WIGLE_TIMEOUT) as r:
            raw=r.read(8192); data=json.loads(raw.decode('utf-8','replace') or '{}')
            if r.status>=400 or str(data.get('success','true')).lower()!='true': raise RuntimeError(data.get('message') or f'HTTP {r.status}')
            return {'ok':True,'status':r.status,'endpoint':url,'userid':data.get('userid') or ''}
    except urllib.error.HTTPError as e:
        msg=e.read(2048).decode('utf-8','replace').strip() or str(e); raise RuntimeError(f'WiGLE returned HTTP {e.code}: {msg[:500]}')
    except Exception as e:
        if isinstance(e,(ValueError,RuntimeError)): raise
        raise RuntimeError(f'WiGLE connection failed: {e}')

def _wigle_time(v):
    text=str(v or '').strip()
    if not text: return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    try:
        dt=datetime.fromisoformat(text.replace('Z','+00:00'))
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return text[:19].replace('T',' ')

def wigle_csv_for_source(source,include_ids=None):
    conn=db(); rows=[dict(r) for r in conn.execute('select * from observations where source=? order by coalesce(seen_at,created_at),id',(source,)).fetchall()]; conn.close()
    usable=[r for r in rows if (include_ids is None or r['id'] in include_ids) and normalize_mac(r.get('bssid')) and r.get('latitude') is not None and r.get('longitude') is not None]
    if not usable: raise ValueError('no WiGLE-compatible observations found for that import')
    out=io.StringIO(newline='')
    out.write(f'WigleWifi-1.4,appRelease={APP_VERSION},model=Wardriver Local,release={APP_VERSION},device=local,display=local,board=local,brand=wardriver.org\n')
    out.write('MAC,SSID,AuthMode,FirstSeen,Channel,RSSI,CurrentLatitude,CurrentLongitude,AltitudeMeters,AccuracyMeters,Type\n')
    w=csv.writer(out,lineterminator='\n')
    for r in usable:
        mac=str(r.get('bssid') or '').strip(); ssid=str(r.get('name') or '').replace('\n',' ').replace('\r',' ')
        auth=str(r.get('security') or '[UNKNOWN]'); ch=str(r.get('channel') or '0')
        # Wardriver's current local schema does not retain RSSI/altitude/accuracy, so use neutral placeholders.
        w.writerow([mac,ssid,auth,_wigle_time(r.get('seen_at') or r.get('created_at')),ch,'-100',f"{float(r['latitude']):.7f}",f"{float(r['longitude']):.7f}",'0','0','WIFI'])
    return out.getvalue().encode('utf-8'),len(usable)

def _multipart_file(field_name, filename, content_type, data, extra=None):
    boundary='----Wardriver'+uuid.uuid4().hex
    b=boundary.encode(); parts=[]
    for k,v in (extra or {}).items():
        parts += [b'--'+b+b'\r\n',f'Content-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()]
    parts += [b'--'+b+b'\r\n',f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'.encode(),f'Content-Type: {content_type}\r\n\r\n'.encode(),data,b'\r\n',b'--'+b+b'--\r\n']
    return b''.join(parts),boundary

def wigle_upload_source(source,cfg_override=None,include_ids=None):
    if str(source).startswith('wigle-own:'):raise ValueError('Downloaded WiGLE logs cannot be re-uploaded')
    cfg=cfg_override or wigle_settings(include_token=True)
    if not cfg['enabled']: raise ValueError('WiGLE uploads are disabled')
    csv_bytes,count=wigle_csv_for_source(source,include_ids)
    run_id=str(uuid.uuid4()); now=datetime.now(timezone.utc).isoformat(); conn=db(); conn.execute('insert into wigle_runs(id,source,status,records_total,created_at) values (?,?,?,?,?)',(run_id,source,'uploading',count,now)); conn.commit(); conn.close()
    try:
        body,boundary=_multipart_file('file',f'wardriver-{re.sub(r"[^A-Za-z0-9._-]+","-",source)[:80]}.csv','text/csv',csv_bytes,{'donate':'on' if cfg.get('donate') else 'false'})
        url=WIGLE_API_BASE+WIGLE_UPLOAD_PATH; headers=_wigle_auth_headers(cfg.get('api_token')); headers['Content-Type']='multipart/form-data; boundary='+boundary; headers['Content-Length']=str(len(body))
        req=urllib.request.Request(url,data=body,headers=headers,method='POST')
        with urllib.request.urlopen(req,timeout=WIGLE_TIMEOUT) as r:
            raw=r.read(32768); data=json.loads(raw.decode('utf-8','replace') or '{}')
            if r.status>=400 or str(data.get('success','false')).lower()!='true': raise RuntimeError(data.get('message') or f'HTTP {r.status}')
        remote_id=str(data.get('transid') or data.get('id') or data.get('message') or '')[:250]
        done=datetime.now(timezone.utc).isoformat(); conn=db(); conn.execute("update wigle_runs set status='complete',remote_id=?,completed_at=? where id=?",(remote_id,done,run_id)); conn.commit(); conn.close()
        return {'ok':True,'source':source,'records_total':count,'remote_id':remote_id,'response':data}
    except urllib.error.HTTPError as e:
        msg=e.read(4096).decode('utf-8','replace').strip() or str(e); err=f'WiGLE returned HTTP {e.code}: {msg[:1000]}'
        done=datetime.now(timezone.utc).isoformat(); conn=db(); conn.execute("update wigle_runs set status='failed',error=?,completed_at=? where id=?",(err,done,run_id)); conn.commit(); conn.close(); raise RuntimeError(err)
    except Exception as e:
        done=datetime.now(timezone.utc).isoformat(); conn=db(); conn.execute("update wigle_runs set status='failed',error=?,completed_at=? where id=?",(str(e)[:1000],done,run_id)); conn.commit(); conn.close(); raise


# ---------------------------------------------------------------------------
# Ask Wardriver
# Local natural-language query compiler. User text is NEVER executed as SQL.
# It is parsed into a structured plan, compiled from whitelisted SQL fragments,
# parameterized, and executed with SQLite query_only enabled.
# ---------------------------------------------------------------------------
ASK_TABLE_LIMIT = 250
ASK_MAP_LIMIT = 5000
ASK_GROUP_LIMIT = 50
ASK_QUERY_TIMEOUT_SECONDS = 4.0

ASK_HARDWARE_TERMS = {
    'refrigerator': ('refrigerator','refrigerators','fridge','fridges','freezer','freezers'),
    'oven': ('oven','ovens','range','ranges','cooktop','cooktops','stove','stoves'),
    'appliance': ('appliance','appliances','dishwasher','dishwashers','washer','washers','dryer','dryers','microwave','microwaves'),
    'robot': ('robot vacuum','robot vacuums','roomba','roborock','deebot','robot cleaner','robot cleaners'),
    'camera': ('security camera','security cameras','doorbell','doorbells'),
    'printer': ('printer','printers','scanner','scanners'),
    'tv': ('smart tv','smart tvs','television','televisions','streaming device','streaming devices'),
    'speaker': ('speaker','speakers','voice assistant','voice assistants'),
    'climate': ('thermostat','thermostats','hvac','air conditioner','air conditioners'),
    'access': ('smart lock','smart locks','garage opener','garage openers','access control'),
    'energy': ('ev charger','ev chargers','solar','powerwall','inverter','inverters'),
    'network': ('router','routers','mesh','access point','access points','network gear'),
    'commercial': ('pos','kiosk','kiosks','digital signage','commercial device','commercial devices'),
    'vehicle': ('vehicle','vehicles','car hotspot','car hotspots','mobile hotspot','mobile hotspots'),
    'embedded': ('embedded','maker','esp32','esp8266','tasmota','shelly','sonoff','tuya'),
    'setup': ('setup network','setup networks','provisioning','pairing network','pairing networks'),
}
ASK_COLLECTION_TERMS = {
    'wardriving': ('wardriving','wardrive','driving'),
    'warwalking': ('warwalking','warwalk','walking'),
    'bike': ('cycling','cycle','bike','biking'),
    'bus': ('bus','bus ride'),
    'stationary': ('stationary','fixed capture'),
    'transit': ('transit','train','metro','subway'),
}
ASK_GROUP_TERMS = (
    ('hardware_profile', ('hardware profile','hardware profiles','gear profile','gear profiles')),
    ('manufacturer', ('manufacturer','manufacturers','vendor','vendors','brand','brands')),
    ('collection_method', ('collection method','collection methods','survey method','survey methods')),
    ('hardware', ('hardware type','hardware types','device type','device types','iot type','iot types')),
    ('channel', ('channels','channel usage')),
    ('security', ('security type','security types','encryption','security')),
    ('source', ('sources','imports','source files')),
)

def _ask_clean_text(value):
    return re.sub(r'\s+', ' ', str(value or '').replace('’',"'").replace('“','"').replace('”','"')).strip()

def _ask_now(context=None):
    context=context or {}
    try:offset=int(context.get('timezone_offset_minutes',0))
    except Exception:offset=0
    offset=max(-840,min(840,offset));local_tz=timezone(-timedelta(minutes=offset))
    raw=str(context.get('now_iso') or '').strip()
    if raw:
        try:
            d=datetime.fromisoformat(raw.replace('Z','+00:00'))
            if d.tzinfo is None:d=d.replace(tzinfo=timezone.utc)
            return d.astimezone(local_tz)
        except Exception:pass
    return datetime.now(timezone.utc).astimezone(local_tz)

def _ask_utc_iso(dt):
    if dt.tzinfo is None:dt=dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()

def _ask_month_start(dt):return dt.replace(day=1,hour=0,minute=0,second=0,microsecond=0)

def _ask_time_filter(q, now):
    stale=re.search(r"(?:not|haven't|hasn't|have not|has not)\s+(?:been\s+)?seen(?:\s+in|\s+for)?\s+(?:the\s+)?(?:last\s+)?(\d+)\s*(day|days|week|weeks|month|months|year|years)",q)
    if 'stale' in q or stale:
        if stale:
            n=max(1,min(3650,int(stale.group(1))));unit=stale.group(2)
            days=n*(7 if 'week' in unit else 30 if 'month' in unit else 365 if 'year' in unit else 1)
        else:days=180
        return {'field':'last_seen','op':'before','value':_ask_utc_iso(now-timedelta(days=days)),'label':f'not seen in {days} days'}
    discovery=bool(re.search(r'\b(found|discovered|discoveries|new|first seen|first discovered|did i find)\b',q))
    field='first_seen' if discovery else 'last_seen'
    m=re.search(r'\blast\s+(\d+)\s*(day|days|week|weeks|month|months|year|years)\b',q)
    if m:
        n=max(1,min(3650,int(m.group(1))));unit=m.group(2)
        days=n*(7 if 'week' in unit else 30 if 'month' in unit else 365 if 'year' in unit else 1)
        return {'field':field,'op':'range','start':_ask_utc_iso(now-timedelta(days=days)),'end':_ask_utc_iso(now+timedelta(seconds=1)),'label':f'{field.replace("_"," ")} in last {n} {unit}'}
    if 'today' in q:
        start=now.replace(hour=0,minute=0,second=0,microsecond=0)
        return {'field':field,'op':'range','start':_ask_utc_iso(start),'end':_ask_utc_iso(now+timedelta(seconds=1)),'label':f'{field.replace("_"," ")} today'}
    if 'yesterday' in q:
        end=now.replace(hour=0,minute=0,second=0,microsecond=0);start=end-timedelta(days=1)
        return {'field':field,'op':'range','start':_ask_utc_iso(start),'end':_ask_utc_iso(end),'label':f'{field.replace("_"," ")} yesterday'}
    if 'this week' in q:
        start=(now-timedelta(days=now.weekday())).replace(hour=0,minute=0,second=0,microsecond=0)
        return {'field':field,'op':'range','start':_ask_utc_iso(start),'end':_ask_utc_iso(now+timedelta(seconds=1)),'label':f'{field.replace("_"," ")} this week'}
    if 'this month' in q:
        start=_ask_month_start(now)
        return {'field':field,'op':'range','start':_ask_utc_iso(start),'end':_ask_utc_iso(now+timedelta(seconds=1)),'label':f'{field.replace("_"," ")} this month'}
    if 'last month' in q:
        end=_ask_month_start(now);start=_ask_month_start(end-timedelta(days=1))
        return {'field':field,'op':'range','start':_ask_utc_iso(start),'end':_ask_utc_iso(end),'label':f'{field.replace("_"," ")} last month'}
    if 'this year' in q:
        start=now.replace(month=1,day=1,hour=0,minute=0,second=0,microsecond=0)
        return {'field':field,'op':'range','start':_ask_utc_iso(start),'end':_ask_utc_iso(now+timedelta(seconds=1)),'label':f'{field.replace("_"," ")} this year'}
    if 'last year' in q:
        end=now.replace(month=1,day=1,hour=0,minute=0,second=0,microsecond=0);start=end.replace(year=end.year-1)
        return {'field':field,'op':'range','start':_ask_utc_iso(start),'end':_ask_utc_iso(end),'label':f'{field.replace("_"," ")} last year'}
    m=re.search(r'\bin\s+(20\d{2})\b',q)
    if m:
        y=int(m.group(1));start=now.replace(year=y,month=1,day=1,hour=0,minute=0,second=0,microsecond=0);end=start.replace(year=y+1)
        return {'field':field,'op':'range','start':_ask_utc_iso(start),'end':_ask_utc_iso(end),'label':f'{field.replace("_"," ")} in {y}'}
    return None

def _ask_extract_limit(q, default):
    m=re.search(r'\b(?:top|first|show|list)\s+(\d{1,4})\b',q)
    return max(1,min(250,int(m.group(1)))) if m else default

def _ask_manufacturer_filter(raw,q):
    patterns=[
      r'\b(?:manufacturer|vendor|made by|brand)\s+(?:is\s+)?["\']?([^"\']+?)["\']?(?=\s+(?:found|seen|discovered|using|with|on\s+channel|this|last|within|only|that|which|where)\b|$)',
      r'\bfrom\s+["\']?([^"\']+?)["\']?(?=\s+(?:found|seen|discovered|using|with|on\s+channel|this|last|within|only|that|which|where)\b|$)',
    ]
    for pat in patterns:
        m=re.search(pat,q,re.I)
        if m:
            v=m.group(1).strip(' .,-')
            if v and v not in ('my last drive','last drive','here','the last drive'):return v[:80]
    m=re.search(r'\b(?:show|find|list|map)(?:\s+me)?(?:\s+all)?\s+(.+?)\s+iot\s+devices?\b',q,re.I)
    if m:
        v=m.group(1).strip(' .,-')
        if v and v not in ('all','smart','interesting','nearby'):return v[:80]
    return None

def ask_parse_query(query, context=None):
    raw=_ask_clean_text(query)
    if not raw:raise ValueError('Type a question for Wardriver.')
    if len(raw)>500:raise ValueError('Question is too long (500 characters max).')
    q=raw.lower()
    if re.search(r'\b(drop|delete|update|insert|alter|pragma|attach|detach|vacuum|replace|create)\b',q):
        raise ValueError('Ask Wardriver is read-only. It does not accept SQL modification commands.')
    if ('coverage' in q and ('area' in q or 'places' in q)) or 'route me' in q:
        raise ValueError('Ask Wardriver v1 queries devices and drives. Coverage-cell and route-generation questions are not enabled yet.')
    plan={'entity':'devices','intent':'list','output':'map','filters':[],'limit':ASK_TABLE_LIMIT,'query':raw}
    if re.search(r'\b(which|what)\s+drives?\b',q) and ('most' in q or 'rank' in q or 'top' in q):
        plan.update({'entity':'drives','intent':'drive_rank','output':'table','limit':_ask_extract_limit(q,25)})
        plan['metric']='new_devices' if 'new' in q or 'discover' in q or 'found' in q else 'devices';return plan
    group=None
    for key,terms in ASK_GROUP_TERMS:
        if any(t in q for t in terms) and (re.search(r'\b(which|what|top|most|common|breakdown|rank)\b',q) or 'compare' in q):group=key;break
    if 'compare' in q and ('wardriving' in q or 'warwalking' in q):group='collection_method'
    if group:plan.update({'intent':'group','output':'table','group_by':group,'limit':_ask_extract_limit(q,20)})
    if re.search(r'^\s*(how many|count\b)',q) or re.search(r'\bhow many\b',q):plan.update({'intent':'count','output':'metric','limit':1})
    if 'flock' in q:plan['filters'].append({'type':'flock','label':'Flock cameras'})
    elif 'alpr' in q or 'license plate reader' in q or 'license plate readers' in q:plan['filters'].append({'type':'kind','value':'alpr','label':'ALPR / cameras'})
    elif ('camera' in q or 'cameras' in q) and group!='hardware':plan['filters'].append({'type':'camera_any','label':'cameras / ALPR'})
    if ('wi-fi' in q or 'wifi' in q or re.search(r'\bnetworks?\b',q)) and 'flock' not in q and 'alpr' not in q:plan['filters'].append({'type':'kind','value':'wifi','label':'Wi-Fi'})
    if re.search(r'\bopen\b',q) and ('network' in q or 'wifi' in q or 'wi-fi' in q or 'security' in q):plan['filters'].append({'type':'open','label':'open / unencrypted'})
    sec=None
    if 'wpa3' in q:sec='wpa3'
    elif 'wpa2' in q:sec='wpa2'
    elif re.search(r'\bwpa\b',q):sec='wpa'
    elif re.search(r'\bwep\b',q):sec='wep'
    if sec:plan['filters'].append({'type':'security','value':sec,'label':sec.upper()})
    m=re.search(r'\bchannel\s*[:#]?\s*(\d{1,3})\b',q)
    if m:plan['filters'].append({'type':'channel','value':m.group(1),'label':f'channel {m.group(1)}'})
    if re.search(r'\b(?:only\s+)?seen\s+(?:exactly\s+)?once\b',q) or 'only seen once' in q:plan['filters'].append({'type':'capture_count','op':'eq','value':1,'label':'seen once'})
    else:
        m=re.search(r'\b(?:seen|captured|observed)\s+(?:more than|over)\s+(\d+)\s*(?:times|captures?)?',q)
        if m:plan['filters'].append({'type':'capture_count','op':'gt','value':int(m.group(1)),'label':f'more than {m.group(1)} captures'})
        m2=re.search(r'\b(?:at least)\s+(\d+)\s*(?:times|captures?)',q)
        if m2:plan['filters'].append({'type':'capture_count','op':'gte','value':int(m2.group(1)),'label':f'at least {m2.group(1)} captures'})
    for method,terms in ASK_COLLECTION_TERMS.items():
        if any(re.search(r'\b'+re.escape(t)+r'\b',q) for t in terms):
            if plan['intent']=='group' and plan.get('group_by')=='collection_method' and 'compare' in q:continue
            plan['filters'].append({'type':'collection_method','value':method,'label':method});break
    if plan['intent']=='group' and plan.get('group_by')=='collection_method' and 'compare' in q:
        methods=[m for m,terms in ASK_COLLECTION_TERMS.items() if any(re.search(r'\b'+re.escape(t)+r'\b',q) for t in terms)]
        if methods:plan['filters'].append({'type':'collection_in','values':methods,'label':' vs '.join(methods)})
    matched_hardware=None
    for hw,terms in ASK_HARDWARE_TERMS.items():
        if any(t in q for t in terms):matched_hardware=hw;break
    if matched_hardware:plan['filters'].append({'type':'hardware_type','value':matched_hardware,'label':HARDWARE_TYPES.get(matched_hardware,(matched_hardware,''))[0]})
    elif re.search(r'\biot\b',q):plan['filters'].append({'type':'hardware_any','label':'inferred IoT / smart hardware'})
    vendor=_ask_manufacturer_filter(raw,q)
    if vendor:plan['filters'].append({'type':'manufacturer','value':vendor,'label':f'manufacturer/name contains {vendor}'})
    m=re.search(r'\b(?:ssid|name)\s+(?:contains\s+|is\s+)?["\']([^"\']+)["\']',raw,re.I)
    if m:plan['filters'].append({'type':'name','value':m.group(1)[:120],'label':f'name contains {m.group(1)[:120]}'})
    m=re.search(r'\bbssid\s+(?:contains\s+|is\s+)?["\']?([0-9a-fA-F:.\-]{4,32})',raw,re.I)
    if m:plan['filters'].append({'type':'bssid','value':m.group(1)[:40],'label':f'BSSID {m.group(1)[:40]}'})
    m=re.search(r'\bsource\s+(?:contains\s+|is\s+)?["\']([^"\']+)["\']',raw,re.I)
    if m:plan['filters'].append({'type':'source','value':m.group(1)[:160],'label':f'source contains {m.group(1)[:160]}'})
    if 'last drive' in q or 'most recent drive' in q:plan['filters'].append({'type':'last_drive','label':'most recent drive'})
    m=re.search(r'\bwithin\s+(\d+(?:\.\d+)?)\s*(mile|miles|mi|kilometer|kilometers|km)\s+(?:of\s+)?(?:here|the map|current location|current map)',q)
    if m:
        if not bool((context or {}).get('map_ready')):raise ValueError('That question uses “here.” Open Map once so Wardriver has a current map center, then try again.')
        radius=float(m.group(1));unit=m.group(2)
        if unit.startswith('k'):radius*=0.621371
        radius=max(.05,min(250.0,radius))
        try:lat=float((context or {}).get('lat'));lon=float((context or {}).get('lon'))
        except Exception:lat=lon=float('nan')
        if not (math.isfinite(lat) and math.isfinite(lon)):raise ValueError('That question needs a current map center. Open Map once and try again.')
        plan['filters'].append({'type':'radius','lat':lat,'lon':lon,'miles':radius,'label':f'within {radius:.1f} mi of current map center'})
    tf=_ask_time_filter(q,_ask_now(context))
    if tf:plan['filters'].append({'type':'time',**tf})
    if plan['intent']=='list':plan['limit']=_ask_extract_limit(q,ASK_TABLE_LIMIT);plan['output']='map'
    return plan

def _ask_last_drive_key(conn):
    r=conn.execute('select drive_key,max(seen_ts) t from track_fixes group by drive_key order by t desc limit 1').fetchone()
    if r:return r['drive_key']
    r=conn.execute('select drive_key,max(first_seen) t from drive_devices group by drive_key order by t desc limit 1').fetchone()
    return r['drive_key'] if r else None

def _ask_sql_literal(value):
    if value is None:return 'NULL'
    if isinstance(value,bool):return '1' if value else '0'
    if isinstance(value,(int,float)):return str(value)
    return "'"+str(value).replace("'","''")+"'"

def _ask_sql_display(sql,params):
    parts=sql.split('?');out=parts[0]
    for i,p in enumerate(params):out+=_ask_sql_literal(p)+(parts[i+1] if i+1<len(parts) else '')
    return out

def _ask_plan_where(plan,conn):
    clauses=[];args=[];resolved=[]
    for f in plan.get('filters',[]):
        t=f.get('type')
        if t=='kind':clauses.append('d.kind=?');args.append(f['value'])
        elif t=='flock':
            clauses.append("EXISTS (SELECT 1 FROM flock_assessments fa LEFT JOIN flock_reviews fr ON fr.device_key=fa.device_key WHERE fa.device_key=d.device_key AND (fr.decision='confirmed' OR (coalesce(fr.decision,'')<>'rejected' AND fa.identity_signal=1 AND fa.score>=80)))")
        elif t=='camera_any':clauses.append("(d.kind='alpr' or d.hardware_type='camera')")
        elif t=='open':clauses.append('d.signal_open=1')
        elif t=='security':clauses.append("lower(coalesce(d.security,'')) like ?");args.append('%'+str(f['value']).lower()+'%')
        elif t=='channel':clauses.append("trim(coalesce(d.channel,''))=?");args.append(str(f['value']))
        elif t=='capture_count':
            op={'eq':'=','gt':'>','gte':'>='}.get(f.get('op'),'=');clauses.append(f'd.observation_count {op} ?');args.append(int(f.get('value') or 0))
        elif t=='collection_method':clauses.append('d.collection_method=?');args.append(f['value'])
        elif t=='collection_in':
            vals=[str(x) for x in f.get('values',[]) if x]
            if vals:clauses.append('d.collection_method in ('+','.join('?' for _ in vals)+')');args.extend(vals)
        elif t=='hardware_type':clauses.append('d.hardware_type=?');args.append(f['value'])
        elif t=='hardware_any':clauses.append("(trim(coalesce(d.hardware_type,''))<>'' or lower(coalesce(d.name,'')) like '%smart%' or lower(coalesce(d.name,'')) like '%iot%' or lower(coalesce(d.name,'')) like '%things%')")
        elif t=='manufacturer':
            pat='%'+str(f['value']).lower()+'%';clauses.append("(lower(coalesce(d.manufacturer,'')) like ? or lower(coalesce(d.name,'')) like ?)");args.extend([pat,pat])
        elif t=='name':clauses.append("lower(coalesce(d.name,'')) like ?");args.append('%'+str(f['value']).lower()+'%')
        elif t=='source':clauses.append("lower(coalesce(d.source,'')) like ?");args.append('%'+str(f['value']).lower()+'%')
        elif t=='bssid':
            raw=normalize_mac(f['value'])
            if len(raw)>=4:clauses.append("coalesce(d.bssid_norm,'') like ?");args.append('%'+raw+'%')
            else:clauses.append("lower(coalesce(d.bssid,'')) like ?");args.append('%'+str(f['value']).lower()+'%')
        elif t=='last_drive':
            dk=_ask_last_drive_key(conn)
            if not dk:clauses.append('0=1');resolved.append('No drive history is available.')
            else:clauses.append('d.device_key in (select device_key from drive_devices where drive_key=?)');args.append(dk);f['resolved_drive_key']=dk
        elif t=='time':
            field='first_seen' if f.get('field')=='first_seen' else 'last_seen'
            if f.get('op')=='before':clauses.append(f'datetime(d.{field}) < datetime(?)');args.append(f['value'])
            else:clauses.append(f'datetime(d.{field}) >= datetime(?) and datetime(d.{field}) < datetime(?)');args.extend([f['start'],f['end']])
        elif t=='radius':
            lat=float(f['lat']);lon=float(f['lon']);miles=float(f['miles']);lat_delta=miles/69.0;lon_delta=miles/max(1.0,69.0*abs(math.cos(math.radians(lat))))
            south,north=lat-lat_delta,lat+lat_delta;west,east=lon-lon_delta,lon+lon_delta
            clauses.append('d.rowid in (select rowid from device_rtree where max_lat>=? and min_lat<=? and max_lon>=? and min_lon<=?) and haversine_miles(d.latitude,d.longitude,?,?)<=?')
            args.extend([south,north,west,east,lat,lon,miles])
    return clauses,args,resolved

def _ask_register_functions(conn):
    def hmi(lat1,lon1,lat2,lon2):
        try:return _haversine_km(float(lat1),float(lon1),float(lat2),float(lon2))*0.621371
        except Exception:return 1e12
    conn.create_function('haversine_miles',4,hmi,deterministic=True)

def _ask_group_definition(group):
    defs={
      'manufacturer':("coalesce(nullif(trim(d.manufacturer),''),'Unknown')",'Manufacturer'),
      'channel':("coalesce(nullif(trim(d.channel),''),'Unknown')",'Channel'),
      'security':("coalesce(nullif(trim(d.security),''),'Unknown')",'Security'),
      'hardware':("coalesce(nullif(trim(d.hardware_label),''),nullif(trim(d.hardware_type),''),'Unknown')",'Hardware'),
      'hardware_profile':("coalesce(nullif(trim(d.hardware_profile_name),''),'Unassigned')",'Hardware profile'),
      'collection_method':("coalesce(nullif(trim(d.collection_method),''),'Unknown')",'Collection method'),
      'source':("coalesce(nullif(trim(d.source),''),'Unknown')",'Source'),
    }
    return defs.get(group,defs['manufacturer'])

def _ask_map_rows(conn,where,args,limit=ASK_MAP_LIMIT):
    sql="""select d.device_key id,d.device_key,d.kind,d.name,d.bssid,d.security,d.channel,d.latitude,d.longitude,
                  d.first_seen,d.last_seen,d.last_seen seen_at,d.source,d.collection_method,d.session_id,d.session_name,
                  d.hardware_profile_id,d.hardware_profile_name,d.manufacturer,d.hardware_type,d.hardware_label,
                  d.hardware_confidence,d.hardware_reason,d.mac_local,d.signal_hidden,d.signal_open,d.signal_mobile,
                  d.observation_count as _count from devices d"""
    if where:sql+=' where '+' and '.join(where)
    sql+=' order by d.last_seen desc,d.device_key limit ?'
    rows=[dict(r) for r in conn.execute(sql,(*args,limit+1)).fetchall()];truncated=len(rows)>limit;rows=rows[:limit]
    for row in rows:
        assessment=_flock_map_row(conn,row['device_key'])
        if assessment:
            for key,value in assessment.items():
                if key.startswith('flock_') or key=='is_flock':row[key]=value
            if row.get('is_flock'):row['kind']='alpr'

    return rows,truncated

def _ask_bounds(rows):
    pts=[]
    for r in rows:
        try:pts.append((float(r['latitude']),float(r['longitude'])))
        except Exception:pass
    if not pts:return None
    return {'south':min(x[0] for x in pts),'north':max(x[0] for x in pts),'west':min(x[1] for x in pts),'east':max(x[1] for x in pts)}

def _ask_interpretation(plan):
    pieces=[f.get('label') for f in plan.get('filters',[]) if f.get('label')]
    if plan.get('intent')=='group':pieces.append('grouped by '+str(plan.get('group_by','')).replace('_',' '))
    elif plan.get('intent')=='count':pieces.append('count unique devices')
    elif plan.get('intent')=='drive_rank':pieces.append('rank drives by '+str(plan.get('metric','devices')).replace('_',' '))
    else:pieces.append('matching unique devices')
    return ' · '.join(pieces) if pieces else 'all unique devices'

def ask_execute(query,context=None):
    started=time.perf_counter();plan=ask_parse_query(query,context);conn=db()
    try:
        _ask_register_functions(conn);conn.execute('pragma query_only=ON');deadline=time.monotonic()+ASK_QUERY_TIMEOUT_SECONDS
        conn.set_progress_handler(lambda:1 if time.monotonic()>deadline else 0,25000)
        warnings=[];columns=[];rows=[];map_rows=[];map_truncated=False;match_count=None;sql='';params=[]
        if plan['intent']=='drive_rank':
            metric='new_devices' if plan.get('metric')=='new_devices' else 'devices'
            sql="""select dd.drive_key,coalesce(max(nullif(t.session_name,'')),max(nullif(t.source,'')),dd.drive_key) as drive,
                          count(distinct dd.device_key) as devices,
                          count(distinct case when datetime(dd.first_seen)=datetime(d.first_seen) then dd.device_key end) as new_devices,
                          min(t.seen_at) as started,max(t.seen_at) as ended,
                          coalesce(max(nullif(t.collection_method,'')),'unknown') as collection_method
                   from drive_devices dd join devices d on d.device_key=dd.device_key left join track_fixes t on t.drive_key=dd.drive_key
                   group by dd.drive_key order by """+metric+' desc,devices desc limit ?'
            params=[int(plan.get('limit') or 25)];rows=[dict(r) for r in conn.execute(sql,params).fetchall()]
            columns=[{'key':'drive','label':'Drive'},{'key':'new_devices','label':'New devices'},{'key':'devices','label':'Unique devices'},{'key':'collection_method','label':'Collection'},{'key':'started','label':'Started'}]
            answer=f"Ranked {len(rows):,} drives by {'new unique devices' if metric=='new_devices' else 'unique devices observed'}."
        else:
            where,args,resolved=_ask_plan_where(plan,conn);warnings.extend(resolved);count_sql='select count(*) as n from devices d'+((' where '+' and '.join(where)) if where else '')
            match_count=int(conn.execute(count_sql,args).fetchone()['n'])
            if plan['intent']=='count':
                sql=count_sql;params=list(args);answer=f"{match_count:,} matching unique device{'s' if match_count!=1 else ''}.";rows=[{'count':match_count}];columns=[{'key':'count','label':'Unique devices'}]
            elif plan['intent']=='group':
                expr,label=_ask_group_definition(plan.get('group_by'));sql=f'select {expr} as value,count(*) as devices from devices d'
                if where:sql+=' where '+' and '.join(where)
                sql+=f' group by {expr} order by devices desc,value limit ?';params=[*args,int(plan.get('limit') or 20)];rows=[dict(r) for r in conn.execute(sql,params).fetchall()]
                columns=[{'key':'value','label':label},{'key':'devices','label':'Unique devices'}];answer=f'{match_count:,} matching unique devices grouped by {label.lower()}.'
            else:
                sql="""select d.kind,d.name,d.bssid,d.manufacturer,d.security,d.channel,d.observation_count as captures,
                              d.first_seen,d.last_seen,d.collection_method,d.hardware_label,d.hardware_profile_name,d.source from devices d"""
                if where:sql+=' where '+' and '.join(where)
                sql+=' order by d.last_seen desc,d.device_key limit ?';params=[*args,int(plan.get('limit') or ASK_TABLE_LIMIT)];rows=[dict(r) for r in conn.execute(sql,params).fetchall()]
                columns=[{'key':'kind','label':'Type'},{'key':'name','label':'Name / SSID'},{'key':'bssid','label':'BSSID'},{'key':'manufacturer','label':'Manufacturer'},{'key':'security','label':'Security'},{'key':'channel','label':'Channel'},{'key':'captures','label':'Captures'},{'key':'last_seen','label':'Last seen'},{'key':'collection_method','label':'Collection'}]
                map_rows,map_truncated=_ask_map_rows(conn,where,args,ASK_MAP_LIMIT);answer=f"Found {match_count:,} matching unique device{'s' if match_count!=1 else ''}."
                if match_count>len(rows):warnings.append(f'Table shows the first {len(rows):,} matches.')
                if map_truncated:warnings.append(f'Map result is capped at {ASK_MAP_LIMIT:,} devices; narrow the question for a complete map.')
        conn.set_progress_handler(None,0);elapsed=round((time.perf_counter()-started)*1000,2)
        return {'ok':True,'query':_ask_clean_text(query),'answer':answer,'interpretation':_ask_interpretation(plan),'plan':plan,'sql':_ask_sql_display(sql,params),'sql_template':sql,'parameters':params,'columns':columns,'rows':rows,'match_count':match_count,'map_rows':map_rows,'map_truncated':map_truncated,'bounds':_ask_bounds(map_rows),'warnings':warnings,'elapsed_ms':elapsed,'safety':{'read_only':True,'sqlite_query_only':True,'compiler':'wardriver-whitelist-v1','model_used':False}}
    except sqlite3.OperationalError as e:
        if 'interrupted' in str(e).lower():raise ValueError('That query took too long. Add a date, manufacturer, type, or other filter and try again.')
        raise
    finally:
        try:conn.set_progress_handler(None,0)
        except Exception:pass
        conn.close()


def parse_byte_range(value, size):
    if not value or not value.startswith('bytes='):
        return None
    spec=value[6:].split(',',1)[0].strip()
    if '-' not in spec:
        raise ValueError('invalid byte range')
    a,b=spec.split('-',1)
    if a=='':
        length=int(b); start=max(0,size-length); end=size-1
    else:
        start=int(a); end=int(b) if b else size-1
    if start<0 or end<start or start>=size:
        raise ValueError('range outside file')
    return start,min(end,size-1)

def send_pmtiles(handler, head_only=False):
    if not PMTILES_PATH.exists() or not PMTILES_PATH.is_file():
        handler.send_response(404); handler.send_header('Cache-Control','no-store'); handler.end_headers(); return
    size=PMTILES_PATH.stat().st_size
    try:
        rng=parse_byte_range(handler.headers.get('Range'), size)
    except Exception:
        handler.send_response(416); handler.send_header('Content-Range',f'bytes */{size}'); handler.end_headers(); return
    if rng:
        start,end=rng; length=end-start+1; handler.send_response(206)
        handler.send_header('Content-Range',f'bytes {start}-{end}/{size}')
    else:
        start,end=0,size-1; length=size; handler.send_response(200)
    handler.send_header('Content-Type','application/vnd.pmtiles')
    handler.send_header('Accept-Ranges','bytes')
    handler.send_header('Content-Length',str(length))
    handler.send_header('Cache-Control','public, max-age=3600')
    handler.send_header('Access-Control-Allow-Origin','*')
    handler.end_headers()
    if head_only:return
    with PMTILES_PATH.open('rb') as f:
        f.seek(start); remaining=length
        while remaining>0:
            chunk=f.read(min(1024*1024,remaining))
            if not chunk:break
            handler.wfile.write(chunk); remaining-=len(chunk)

def _compact_rows(rows, fields):
    packed=[]
    for row in rows:
        a=[row.get(f) for f in fields]
        while a and (a[-1] is None or a[-1]=='' or a[-1]==0):a.pop()
        packed.append(a)
    return {'compact':True,'fields':list(fields),'rows':packed}


def xml_escape(value):
    return (str(value or '')
            .replace('&','&amp;')
            .replace('<','&lt;')
            .replace('>','&gt;')
            .replace('\"','&quot;')
            .replace("'",'&apos;'))


def observations_to_kml(rows, document_name='Wardriver Local observations'):
    wifi=[]; alpr=[]; other=[]
    for r in rows:
        kind=str(r.get('kind') or 'wifi').lower()
        (alpr if kind=='alpr' else wifi if kind=='wifi' else other).append(r)
    parts=['<?xml version="1.0" encoding="UTF-8"?>','<kml xmlns="http://www.opengis.net/kml/2.2"><Document>']
    parts.append(f'<name>{xml_escape(document_name)}</name>')
    parts.append('<Style id="wifi"><IconStyle><color>ffff7928</color><scale>0.62</scale><Icon><href>http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png</href></Icon></IconStyle><LabelStyle><scale>0</scale></LabelStyle></Style>')
    parts.append('<Style id="alpr"><IconStyle><color>ff7350d9</color><scale>0.78</scale><Icon><href>http://maps.google.com/mapfiles/kml/shapes/camera.png</href></Icon></IconStyle><LabelStyle><scale>0</scale></LabelStyle></Style>')
    parts.append('<Style id="other"><IconStyle><color>ff5b8a13</color><scale>0.62</scale></IconStyle><LabelStyle><scale>0</scale></LabelStyle></Style>')
    def folder(title, items, style):
        parts.append(f'<Folder><name>{xml_escape(title)}</name>')
        for r in items:
            try:
                lat=float(r.get('latitude')); lon=float(r.get('longitude'))
            except (TypeError,ValueError):
                continue
            name=r.get('name') or ('Flock / ALPR' if style=='alpr' else r.get('bssid') or '(unnamed)')
            details=[('Type',r.get('kind')),('Name / SSID',r.get('name')),('BSSID',r.get('bssid')),('Manufacturer',r.get('manufacturer')),('Security',r.get('security')),('Channel',r.get('channel')),('Seen',r.get('seen_at')),('Collection method',r.get('collection_method')),('Inferred method',r.get('inferred_method')),('Inference confidence',f"{float(r.get('inference_confidence') or 0)*100:.0f}%"),('Hardware profile',r.get('hardware_profile_name')),('Source',r.get('source')),('Latitude',f'{lat:.7f}'),('Longitude',f'{lon:.7f}')]
            html='<table>'+''.join(f'<tr><th align="left">{xml_escape(k)}</th><td>{xml_escape(v)}</td></tr>' for k,v in details if v not in (None,''))+'</table>'
            parts.append('<Placemark>')
            parts.append(f'<name>{xml_escape(name)}</name><styleUrl>#{style}</styleUrl>')
            parts.append(f'<description><![CDATA[{html}]]></description>')
            parts.append(f'<Point><coordinates>{lon:.7f},{lat:.7f},0</coordinates></Point></Placemark>')
        parts.append('</Folder>')
    folder('Wi-Fi',wifi,'wifi')
    folder('ALPR / cameras',alpr,'alpr')
    if other:
        folder('Other',other,'other')
    parts.append('</Document></kml>')
    return ''.join(parts)


class H(BaseHTTPRequestHandler):
    server_version=f'WardriverLocal/{APP_VERSION}'
    protocol_version='HTTP/1.1'
    def log_message(self, fmt,*args): print('%s - %s' % (self.address_string(), fmt%args), flush=True)
    def send_bytes(self, code, body, ctype='application/octet-stream', extra=None):
        if isinstance(body,str): body=body.encode()
        self.send_response(code); self.send_header('Content-Type',ctype); self.send_header('Content-Length',str(len(body))); self.send_header('Cache-Control','no-store')
        for k,v in (extra or {}).items(): self.send_header(k,v)
        self.end_headers(); self.wfile.write(body)
    def json(self, code, obj): self.send_bytes(code,json.dumps(obj).encode(),'application/json; charset=utf-8')
    def observations(self, limit=10000):
        q=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query); kind=q.get('kind',['all'])[0]; source=q.get('source',[''])[0]; collection=q.get('collection_method',[''])[0]; enrich=(q.get('enrich',['1'])[0]!='0')
        try:
            limit = max(1, min(int(q.get('limit',[limit])[0]), 100000))
        except (TypeError, ValueError):
            limit = 10000
        conn=db(); where=[]; args=[]
        if kind and kind!='all': where.append('kind=?'); args.append(kind)
        if source: where.append('source=?'); args.append(source)
        if collection and collection!='all': where.append('collection_method=?'); args.append(collection)
        sql='select * from observations' + ((' where '+' and '.join(where)) if where else '') + ' order by created_at desc limit ?'; args.append(limit)
        rows=[dict(r) for r in conn.execute(sql,args).fetchall()]; conn.close()
        if enrich:
            for r in rows:
                r['manufacturer'] = manufacturer_for_mac(r.get('bssid')) if r.get('bssid') else ''
                r['mac_local'] = bool(r.get('bssid') and is_local_mac(r.get('bssid')))
                r.update(infer_hardware(r.get('name'), r.get('manufacturer'), r.get('bssid')))
        return rows

    def map_observations(self):
        started=time.perf_counter()
        q=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        def f(name,default):
            try:return float((q.get(name) or [default])[0])
            except:return default
        west=max(-180.0,min(180.0,f('west',-180.0))); east=max(-180.0,min(180.0,f('east',180.0)))
        south=max(-90.0,min(90.0,f('south',-90.0))); north=max(-90.0,min(90.0,f('north',90.0)))
        if south>north:south,north=north,south
        try: limit=max(1,min(20000,int((q.get('limit') or ['8000'])[0])))
        except: limit=8000
        try: before_rowid=max(0,int((q.get('before_rowid') or ['0'])[0]))
        except: before_rowid=0
        kind=(q.get('kind') or ['all'])[0].strip().lower(); collection=(q.get('collection_method') or [''])[0].strip(); search=(q.get('q') or [''])[0].strip().lower()[:160]
        conn=db(); cols=_v29_table_columns(conn,'observations'); where=['r.max_lat>=?','r.min_lat<=?']; args=[south,north]
        if before_rowid:
            where.append('o.rowid<?'); args.append(before_rowid)
        # Antimeridian is uncommon for Wardriver's normal regional use; handle it correctly anyway.
        if west<=east: where += ['r.max_lon>=?','r.min_lon<=?']; args += [west,east]
        else: where += ['(r.max_lon>=? OR r.min_lon<=?)']; args += [west,east]
        if kind and kind!='all': where.append('o.kind=?'); args.append(kind)
        if collection and collection!='all' and 'collection_method' in cols: where.append('o.collection_method=?'); args.append(collection)
        if search:
            where.append("(lower(coalesce(o.name,'')) like ? OR lower(coalesce(o.bssid,'')) like ? OR lower(coalesce(o.source,'')) like ?)")
            pat='%'+search+'%'; args += [pat,pat,pat]
        optional=[]
        for col in ('collection_method','hardware_type','hardware_label','inference_confidence','session_id','session_name','hardware_profile_id','hardware_profile_name'):
            if col in cols: optional.append('o.'+col)
        select=['o.rowid as _rowid','o.id','o.kind','o.name','o.bssid','o.latitude','o.longitude','o.seen_at','o.source']+optional
        sql='select '+','.join(select)+' from observation_rtree r join observations o on o.rowid=r.rowid where '+' and '.join(where)+' order by o.rowid desc limit ?'; args.append(limit+1)
        fetched=[dict(r) for r in conn.execute(sql,args).fetchall()]
        has_more=len(fetched)>limit
        rows=fetched[:limit]
        next_before_rowid=(rows[-1].get('_rowid') if has_more and rows else None)
        conn.close()
        # Manufacturer lookup is retained only for ALPR and small viewport result sets;
        # it is intentionally not mandatory for every point in the map hot path.
        if len(rows)<=2500:
            for r in rows:r['manufacturer']=manufacturer_for_mac(r.get('bssid')) if r.get('bssid') else ''
        return {'ok':True,'observations':rows,'count':len(rows),'limit':limit,'has_more':has_more,'next_before_rowid':next_before_rowid,'bounds':{'west':west,'east':east,'south':south,'north':north},'query_ms':round((time.perf_counter()-started)*1000,2)}

    def _bbox_query(self, q):
        def f(name,default):
            try:return float((q.get(name) or [default])[0])
            except:return default
        west=max(-180.0,min(180.0,f('west',-180.0)));east=max(-180.0,min(180.0,f('east',180.0)))
        south=max(-90.0,min(90.0,f('south',-90.0)));north=max(-90.0,min(90.0,f('north',90.0)))
        if south>north:south,north=north,south
        return west,east,south,north


    def map_aggregate(self):
        started=time.perf_counter();q=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        west,east,south,north=self._bbox_query(q)
        try:cols=max(16,min(160,int((q.get('cols') or ['100'])[0])))
        except:cols=100
        try:rows_n=max(10,min(100,int((q.get('rows') or ['50'])[0])))
        except:rows_n=50
        kind=(q.get('kind') or q.get('view') or ['all'])[0].strip().lower()
        collection=(q.get('collection_method') or [''])[0].strip()
        search=(q.get('q') or [''])[0].strip().lower()[:160]
        try:zoom=float((q.get('zoom') or ['10'])[0])
        except:zoom=10.0
        compact=(q.get('compact') or ['0'])[0]=='1'

        # Persisted fixed-grid rollups make normal low-zoom browsing independent
        # of device cardinality. Search falls back to the live RTree aggregate.
        if not search and west<=east:
            level=8 if zoom<9 else 9 if zoom<10 else 10 if zoom<11 else 11
            factor=MAP_ROLLUP_LEVELS[level]
            gx0=int((west+180.0)*factor);gx1=int((east+180.0)*factor)
            gy0=int((south+90.0)*factor);gy1=int((north+90.0)*factor)
            conn=db();where=['level=?','gx>=?','gx<=?','gy>=?','gy<=?'];args=[level,gx0,gx1,gy0,gy1]
            if kind=='other':where.append("kind not in ('wifi','alpr')")
            elif kind=='all' or not kind:where.append("kind<>'alpr'")
            elif kind=='alpr':
                conn.close();return {'ok':True,'clusters':[],'count':0,'represented_devices':0,'represented_observations':0,'rollup':True,'level':level,'query_ms':round((time.perf_counter()-started)*1000,2)}
            else:where.append('kind=?');args.append(kind)
            if collection and collection!='all':where.append('collection_method=?');args.append(collection)
            sql="""select gx,gy,sum(device_count) device_count,sum(observation_count) observation_count,
                          sum(sum_lat) sum_lat,sum(sum_lon) sum_lon,sum(open_count) open_count
                   from map_cells where """+' and '.join(where)+' group by gx,gy'
            raw=[dict(r) for r in conn.execute(sql,args).fetchall()];conn.close();clusters=[];represented_devices=represented_observations=0
            for r in raw:
                n=int(r['device_count'] or 0);obs=int(r['observation_count'] or 0)
                if n<=0:continue
                represented_devices+=n;represented_observations+=obs
                clusters.append({'id':f"cluster:{level}:{r['gx']}:{r['gy']}",'kind':'wifi' if kind in ('all','wifi','') else kind,
                    'name':f"{n:,} device"+('' if n==1 else 's'),'latitude':round(float(r['sum_lat'] or 0)/n,6),'longitude':round(float(r['sum_lon'] or 0)/n,6),
                    'collection_method':collection if collection and collection!='all' else 'mixed','signal_open':1 if int(r['open_count'] or 0)>0 else 0,
                    '_count':obs,'_device_count':n,'_cluster':1})
            meta={'ok':True,'count':len(clusters),'represented_devices':represented_devices,'represented_observations':represented_observations,'rollup':True,'level':level,'bounds':{'west':west,'east':east,'south':south,'north':north},'query_ms':round((time.perf_counter()-started)*1000,2)}
            if compact:
                fields=('id','kind','name','latitude','longitude','collection_method','signal_open','_count','_device_count','_cluster')
                meta.update(_compact_rows(clusters,fields));return meta
            meta['clusters']=clusters;return meta

        # Search/edge cases use the exact live aggregate.

        # Those are fetched separately as exact devices so Flock remains visible.
        conn=db();where=['r.max_lat>=?','r.min_lat<=?'];args=[south,north]
        if west<=east:
            where+=['r.max_lon>=?','r.min_lon<=?'];args += [west,east]
        else:
            # Dateline crossing is uncommon for this local app; fall back to the
            # exact device endpoint rather than producing a misleading grid.
            conn.close()
            return {'ok':True,'clusters':[],'count':0,'represented_devices':0,'represented_observations':0,
                    'cols':cols,'rows':rows_n,'fallback_exact':True,'query_ms':round((time.perf_counter()-started)*1000,2)}
        if kind=='other':where.append("d.kind not in ('wifi','alpr')")
        elif kind=='all' or not kind:where.append("d.kind<>'alpr'")
        elif kind=='alpr':
            conn.close()
            return {'ok':True,'clusters':[],'count':0,'represented_devices':0,'represented_observations':0,
                    'cols':cols,'rows':rows_n,'query_ms':round((time.perf_counter()-started)*1000,2)}
        else:where.append('d.kind=?');args.append(kind)
        if collection and collection!='all':where.append('d.collection_method=?');args.append(collection)
        if search:
            raw_search=normalize_mac(search)
            clauses=["lower(coalesce(d.name,'')) like ?","lower(coalesce(d.bssid,'')) like ?",
                     "lower(coalesce(d.manufacturer,'')) like ?","lower(coalesce(d.source,'')) like ?"]
            pat='%'+search+'%';args += [pat,pat,pat,pat]
            if len(raw_search)>=4:
                clauses.append("coalesce(d.bssid_norm,'') like ?");args.append('%'+raw_search+'%')
            where.append('('+' or '.join(clauses)+')')

        lon_span=max(1e-9,east-west);lat_span=max(1e-9,north-south)
        lon_cell=lon_span/cols;lat_cell=lat_span/rows_n
        # Since rows are already bounded by west/east/south/north, subtraction
        # yields non-negative values and CAST(... AS INTEGER) is a stable bin.
        sql="""select
                 cast((d.longitude-?)/? as integer) gx,
                 cast((d.latitude-?)/? as integer) gy,
                 avg(d.latitude) latitude,avg(d.longitude) longitude,
                 count(*) device_count,
                 coalesce(sum(d.observation_count),0) observation_count,
                 min(d.first_seen) first_seen,max(d.last_seen) last_seen,
                 coalesce(sum(d.signal_open),0) open_count
               from device_rtree r join devices d on d.rowid=r.rowid
               where """+' and '.join(where)+"""
               group by gx,gy"""
        params=[west,lon_cell,south,lat_cell,*args]
        raw=[dict(r) for r in conn.execute(sql,params).fetchall()];conn.close()
        clusters=[]
        represented_devices=0;represented_observations=0
        for r in raw:
            n=int(r['device_count'] or 0);obs=int(r['observation_count'] or 0)
            if n<=0:continue
            represented_devices+=n;represented_observations+=obs
            gx=int(r['gx']);gy=int(r['gy'])
            clusters.append({
                'id':f'cluster:{gx}:{gy}','device_key':f'cluster:{gx}:{gy}',
                'kind':'wifi' if kind in ('all','wifi','') else kind,
                'name':f'{n:,} device'+('' if n==1 else 's'),
                'bssid':None,'manufacturer':None,'security':None,'channel':None,
                'latitude':r['latitude'],'longitude':r['longitude'],
                'first_seen':r['first_seen'],'last_seen':r['last_seen'],'seen_at':r['last_seen'],
                'source':None,'collection_method':collection if collection and collection!='all' else 'mixed',
                'hardware_type':None,'hardware_label':None,'hardware_confidence':None,'hardware_reason':None,
                'signal_open':1 if int(r['open_count'] or 0)>0 else 0,
                '_count':obs,'_device_count':n,'_cluster':1,'cluster_open_count':int(r['open_count'] or 0),
                'is_flock':0,'flock_confidence':'','flock_reason':''
            })
        meta={'ok':True,'count':len(clusters),'represented_devices':represented_devices,'represented_observations':represented_observations,'cols':cols,'rows':rows_n,'bounds':{'west':west,'east':east,'south':south,'north':north},'query_ms':round((time.perf_counter()-started)*1000,2)}
        if compact:
            fields=('id','kind','name','latitude','longitude','collection_method','signal_open','_count','_device_count','_cluster')
            meta.update(_compact_rows(clusters,fields));return meta
        meta['clusters']=clusters;return meta

    def map_devices(self):
        started=time.perf_counter();q=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        west,east,south,north=self._bbox_query(q)
        try:limit=max(1,min(30000,int((q.get('limit') or ['15000'])[0])))
        except:limit=15000
        kind=(q.get('kind') or q.get('view') or ['all'])[0].strip().lower()
        collection=(q.get('collection_method') or [''])[0].strip()
        search=(q.get('q') or [''])[0].strip().lower()[:160]
        compact=(q.get('compact') or ['0'])[0]=='1'
        adaptive=(q.get('adaptive') or ['0'])[0]=='1'
        # For dense exact viewports, avoid selecting/sorting/serializing 30k rows
        # only to discover the cap was hit. A persisted level-11 map rollup gives
        # a cheap conservative estimate and lets the browser switch directly to
        # the complete aggregate representation.
        if adaptive and not search and west<=east:
            factor=MAP_ROLLUP_LEVELS[11];gx0=int((west+180.0)*factor);gx1=int((east+180.0)*factor);gy0=int((south+90.0)*factor);gy1=int((north+90.0)*factor)
            ec=db();ew=['level=11','gx>=?','gx<=?','gy>=?','gy<=?'];ea=[gx0,gx1,gy0,gy1]
            if kind=='other':ew.append("kind not in ('wifi','alpr')")
            elif kind and kind!='all':ew.append('kind=?');ea.append(kind)
            if collection and collection!='all':ew.append('collection_method=?');ea.append(collection)
            estimated=int(ec.execute('select coalesce(sum(device_count),0) from map_cells where '+' and '.join(ew),ea).fetchone()[0] or 0);ec.close()
            if estimated>limit:
                meta={'ok':True,'count':0,'limit':limit,'truncated':True,'dense':True,'estimated_devices':estimated,
                      'bounds':{'west':west,'east':east,'south':south,'north':north},'query_ms':round((time.perf_counter()-started)*1000,2)}
                if compact:meta.update({'compact':True,'fields':[],'rows':[]})
                else:meta['devices']=[]
                return meta
        conn=db();where=['r.max_lat>=?','r.min_lat<=?'];args=[south,north]
        if west<=east:where+=['r.max_lon>=?','r.min_lon<=?'];args += [west,east]
        else:where+=['(r.max_lon>=? OR r.min_lon<=?)'];args += [west,east]
        if kind=='other':where.append("d.kind not in ('wifi','alpr')")
        elif kind and kind!='all':where.append('d.kind=?');args.append(kind)
        if collection and collection!='all':where.append('d.collection_method=?');args.append(collection)
        if search:
            raw_search=normalize_mac(search)
            clauses=["lower(coalesce(d.name,'')) like ?","lower(coalesce(d.bssid,'')) like ?",
                     "lower(coalesce(d.manufacturer,'')) like ?","lower(coalesce(d.source,'')) like ?"]
            pat='%'+search+'%';args += [pat,pat,pat,pat]
            if len(raw_search)>=4:
                clauses.append("coalesce(d.bssid_norm,'') like ?");args.append('%'+raw_search+'%')
            where.append('('+' or '.join(clauses)+')')
        sql="""select d.device_key id,d.device_key,d.kind,d.name,d.bssid,d.security,d.channel,
                      d.latitude,d.longitude,d.first_seen,d.last_seen,d.last_seen seen_at,d.source,d.collection_method,
                      d.session_name,d.hardware_profile_name,d.manufacturer,d.hardware_type,d.hardware_label,
                      d.hardware_confidence,d.hardware_reason,d.mac_local,d.mac_local as signal_randomized,
                      d.signal_hidden,d.signal_open,d.signal_mobile,d.observation_count as _count,
                      fa.score flock_score,fa.level flock_level,fa.identity_signal flock_identity_signal,
                      fa.evidence_json flock_evidence_json,fr.decision flock_review_decision
               from device_rtree r join devices d on d.rowid=r.rowid
               left join flock_assessments fa on fa.device_key=d.device_key left join flock_reviews fr on fr.device_key=d.device_key where """+' and '.join(where)+' order by d.rowid desc limit ?'
        args.append(limit+1);rows=[dict(r) for r in conn.execute(sql,args).fetchall()];conn.close()
        truncated=len(rows)>limit;rows=rows[:limit]
        for row in rows:
            decision=row.get('flock_review_decision');score=int(row.get('flock_score') or 0);identity=int(row.get('flock_identity_signal') or 0)
            row['is_flock']=1 if decision=='confirmed' or (decision!='rejected' and identity and score>=80) else 0
            row['flock_confidence']=str(row.get('flock_level') or '').lower();row['flock_reason']=''
            try:evidence=json.loads(row.get('flock_evidence_json') or '[]')
            except:evidence=[]
            row['flock_evidence']=evidence;row['flock_reason']='; '.join(str(x.get('label') or '') for x in evidence[:3] if x.get('label'))
            if row['is_flock']:row['kind']='alpr'
        map_keys=('id','device_key','kind','name','bssid','security','channel','latitude','longitude','first_seen','last_seen','seen_at',
                  'source','collection_method','session_name','hardware_profile_name','manufacturer','hardware_type','hardware_label',
                  'hardware_confidence','hardware_reason','mac_local','signal_randomized','signal_hidden','signal_open','signal_mobile','_count',
                  'is_flock','flock_score','flock_level','flock_identity_signal','flock_review_decision','flock_reason')
        rows=[{k:row[k] for k in map_keys if k in row and row[k] is not None and row[k]!=''} for row in rows]
        meta={'ok':True,'count':len(rows),'limit':limit,'truncated':truncated,'bounds':{'west':west,'east':east,'south':south,'north':north},'query_ms':round((time.perf_counter()-started)*1000,2)}
        if compact:
            fields=('device_key','kind','name','bssid','security','channel','latitude','longitude','first_seen','last_seen','source','collection_method','session_name','hardware_profile_name','manufacturer','hardware_type','hardware_label','hardware_confidence','hardware_reason','mac_local','signal_hidden','signal_open','signal_mobile','_count','is_flock','flock_score','flock_level','flock_identity_signal','flock_review_decision','flock_reason')
            meta.update(_compact_rows(rows,fields));return meta
        meta['devices']=rows;return meta

    def map_tracks(self):
        started=time.perf_counter();q=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        west,east,south,north=self._bbox_query(q)
        latpad=max(.001,(north-south)*.03);lonpad=max(.001,abs(east-west)*.03)
        south=max(-90,south-latpad);north=min(90,north+latpad)
        if west<=east:west=max(-180,west-lonpad);east=min(180,east+lonpad)
        try:limit=max(100,min(50000,int((q.get('limit') or ['30000'])[0])))
        except:limit=30000
        collection=(q.get('collection_method') or [''])[0].strip()
        compact=(q.get('compact') or ['0'])[0]=='1'
        conn=db();where=['r.max_lat>=?','r.min_lat<=?'];args=[south,north]
        if west<=east:where+=['r.max_lon>=?','r.min_lon<=?'];args += [west,east]
        else:where+=['(r.max_lon>=? OR r.min_lon<=?)'];args += [west,east]
        if collection and collection!='all':where.append('t.collection_method=?');args.append(collection)
        cursor=(q.get('cursor') or [''])[0]
        if cursor:
            try:
                key,ts,ident=json.loads(cursor)
                if not isinstance(key,str) or not math.isfinite(float(ts)):raise ValueError()
                where.append('(t.drive_key,t.seen_ts,t.id)>(?,?,?)');args.extend((key,float(ts),int(ident)))
            except (ValueError,TypeError):
                conn.close();return {'ok':False,'error':'Invalid track cursor'}
        sql="""select t.id,t.drive_key,t.source,t.session_id,t.session_name,t.collection_method,t.seen_at,t.seen_ts,
                      t.latitude,t.longitude
               from track_rtree r join track_fixes t on t.id=r.rowid where """+' and '.join(where)+' order by t.drive_key,t.seen_ts,t.id limit ?'
        args.append(limit+1);rows=[dict(r) for r in conn.execute(sql,args).fetchall()];conn.close()
        truncated=len(rows)>limit;rows=rows[:limit]
        meta={'ok':True,'count':len(rows),'limit':limit,'truncated':truncated,'has_more':truncated,
              'next_cursor':json.dumps([rows[-1]['drive_key'],rows[-1]['seen_ts'],rows[-1]['id']]) if truncated and rows else None,
              'query_ms':round((time.perf_counter()-started)*1000,2)}
        if compact:
            fields=('id','drive_key','source','session_id','session_name','collection_method','seen_at','seen_ts','latitude','longitude')
            meta.update(_compact_rows(rows,fields));return meta
        meta['tracks']=rows;return meta

    def map_bounds(self):
        q=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        kind=(q.get('kind') or q.get('view') or ['all'])[0].strip().lower()
        collection=(q.get('collection_method') or [''])[0].strip()
        search=(q.get('q') or [''])[0].strip().lower()[:160]
        where=[];args=[]
        if kind=='other':where.append("kind not in ('wifi','alpr')")
        elif kind and kind!='all':where.append('kind=?');args.append(kind)
        if collection and collection!='all':where.append('collection_method=?');args.append(collection)
        if search:
            raw_search=normalize_mac(search)
            clauses=["lower(coalesce(name,'')) like ?","lower(coalesce(bssid,'')) like ?",
                     "lower(coalesce(manufacturer,'')) like ?","lower(coalesce(source,'')) like ?"]
            pat='%'+search+'%';args += [pat,pat,pat,pat]
            if len(raw_search)>=4:
                clauses.append("coalesce(bssid_norm,'') like ?");args.append('%'+raw_search+'%')
            where.append('('+' or '.join(clauses)+')')
        conn=db();sql='select min(min_lat) south,max(max_lat) north,min(min_lon) west,max(max_lon) east,count(*) count from devices'
        if where:sql+=' where '+' and '.join(where)
        r=conn.execute(sql,args).fetchone();conn.close()
        return {'ok':True,'count':int(r['count'] or 0),'bounds':{'south':r['south'],'north':r['north'],'west':r['west'],'east':r['east']}}

    def observations_page(self):
        q=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        kind=(q.get('kind') or ['all'])[0].strip().lower();source=(q.get('source') or [''])[0].strip()
        collection=(q.get('collection_method') or [''])[0].strip();search=(q.get('q') or [''])[0].strip().lower()[:160]
        try:limit=max(1,min(5000,int((q.get('limit') or ['1000'])[0])))
        except:limit=1000
        try:before=max(0,int((q.get('before_rowid') or ['0'])[0]))
        except:before=0
        enrich=(q.get('enrich') or ['1'])[0]!='0';where=[];args=[]
        if before:where.append('rowid<?');args.append(before)
        if kind and kind!='all':where.append('kind=?');args.append(kind)
        if source:where.append('source=?');args.append(source)
        if collection and collection!='all':where.append('collection_method=?');args.append(collection)
        if search:
            raw_search=normalize_mac(search)
            clauses=["lower(coalesce(name,'')) like ?","lower(coalesce(bssid,'')) like ?","lower(coalesce(source,'')) like ?"]
            pat='%'+search+'%';args += [pat,pat,pat]
            if len(raw_search)>=4:
                clauses.append("coalesce(bssid_norm,'') like ?");args.append('%'+raw_search+'%')
            where.append('('+' or '.join(clauses)+')')
        conn=db();sql='select rowid as _rowid,* from observations'+((' where '+' and '.join(where)) if where else '')+' order by rowid desc limit ?'
        rows=[dict(r) for r in conn.execute(sql,(*args,limit+1)).fetchall()]
        has_more=len(rows)>limit;rows=rows[:limit];next_before=rows[-1]['_rowid'] if has_more and rows else None
        # Count uses same filters except cursor and is only returned on the first page.
        total=None
        if not before:
            count_where=[];count_args=[]
            if kind and kind!='all':count_where.append('kind=?');count_args.append(kind)
            if source:count_where.append('source=?');count_args.append(source)
            if collection and collection!='all':count_where.append('collection_method=?');count_args.append(collection)
            if search:
                raw_search=normalize_mac(search)
                clauses=["lower(coalesce(name,'')) like ?","lower(coalesce(bssid,'')) like ?","lower(coalesce(source,'')) like ?"]
                pat='%'+search+'%';count_args += [pat,pat,pat]
                if len(raw_search)>=4:
                    clauses.append("coalesce(bssid_norm,'') like ?");count_args.append('%'+raw_search+'%')
                count_where.append('('+' or '.join(clauses)+')')
            total=conn.execute('select count(*) from observations'+((' where '+' and '.join(count_where)) if count_where else ''),count_args).fetchone()[0]
        conn.close()
        if enrich:
            for r in rows:
                r['manufacturer']=manufacturer_for_mac(r.get('bssid')) if r.get('bssid') else ''
                r['mac_local']=bool(r.get('bssid') and is_local_mac(r.get('bssid')))
                r.update(infer_hardware(r.get('name'),r.get('manufacturer'),r.get('bssid')))
        return {'ok':True,'observations':rows,'count':len(rows),'total':total,'limit':limit,'has_more':has_more,'next_before_rowid':next_before}

    def _stream_headers(self, ctype, filename):
        self.send_response(200);self.send_header('Content-Type',ctype);self.send_header('Cache-Control','no-store')
        self.send_header('Content-Disposition',f'attachment; filename="{filename}"');self.send_header('Connection','close')
        self.end_headers();self.close_connection=True

    def _export_row_iter(self):
        conn=db();cur=conn.execute('select * from observations order by rowid')
        try:
            for rr in cur:
                r=dict(rr);r['manufacturer']=manufacturer_for_mac(r.get('bssid')) if r.get('bssid') else ''
                yield r
        finally:conn.close()

    def stream_export_csv(self):
        self._stream_headers('text/csv; charset=utf-8','wardriver-observations.csv')
        fields=['id','kind','name','bssid','manufacturer','security','channel','latitude','longitude','seen_at','source','collection_method',
                'inferred_method','inference_confidence','session_id','session_name','hardware_profile_id','hardware_profile_name','hardware_profile_gear','created_at']
        out=io.StringIO();w=csv.DictWriter(out,fieldnames=fields,extrasaction='ignore');w.writeheader();self.wfile.write(out.getvalue().encode());out.seek(0);out.truncate(0)
        for r in self._export_row_iter():
            w.writerow(r)
            if out.tell()>=64*1024:
                self.wfile.write(out.getvalue().encode('utf-8'));out.seek(0);out.truncate(0)
        if out.tell():self.wfile.write(out.getvalue().encode('utf-8'))

    def stream_export_geojson(self):
        self._stream_headers('application/geo+json; charset=utf-8','wardriver-observations.geojson')
        self.wfile.write(b'{"type":"FeatureCollection","features":[');first=True
        for r in self._export_row_iter():
            props={k:v for k,v in r.items() if k not in ('latitude','longitude')}
            feat={'type':'Feature','geometry':{'type':'Point','coordinates':[r['longitude'],r['latitude']]},'properties':props}
            if not first:self.wfile.write(b',')
            first=False;self.wfile.write(json.dumps(feat,separators=(',',':')).encode('utf-8'))
        self.wfile.write(b']}')

    def stream_export_kml(self):
        self._stream_headers('application/vnd.google-earth.kml+xml; charset=utf-8','wardriver-google-earth.kml')
        self.wfile.write(b'<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>')
        for r in self._export_row_iter():
            name=xml_escape(str(r.get('name') or r.get('bssid') or r.get('kind') or 'Observation'))
            desc=xml_escape(' · '.join(str(x) for x in [r.get('kind'),r.get('bssid'),r.get('manufacturer'),r.get('security'),r.get('hardware_profile_name'),r.get('source')] if x))
            item=f'<Placemark><name>{name}</name><description>{desc}</description><Point><coordinates>{float(r["longitude"]):.7f},{float(r["latitude"]):.7f},0</coordinates></Point></Placemark>'
            self.wfile.write(item.encode('utf-8'))
        self.wfile.write(b'</Document></kml>')

    def do_HEAD(self):
        path=urllib.parse.urlsplit(self.path).path
        if path=='/maps/region.pmtiles': return send_pmtiles(self, True)
        if path in ('/','/dashboard','/dashboard/','/api/health'):
            self.send_response(200); self.send_header('Cache-Control','no-store'); self.end_headers(); return
        self.send_response(404); self.end_headers()

    def do_GET(self):
        path=urllib.parse.urlsplit(self.path).path
        if path=='/maps/region.pmtiles': return send_pmtiles(self, False)
        if path=='/api/cell-towers':
            try:
                q=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                bbox=(q.get('bbox') or [''])[0].split(',')
                return self.json(200,cell_towers.lookup(DB_PATH.parent/'cell-towers.db',bbox))
            except ValueError as e:return self.json(400,{'ok':False,'error':str(e)})
            except Exception:return self.json(500,{'ok':False,'error':'Could not read tower cache'})
        if path=='/api/qr':
            try:
                import qrcode, qrcode.image.svg
                qs=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query); text=(qs.get('text') or [''])[0]
                if not text or len(text)>4096: raise ValueError('invalid QR payload')
                img=qrcode.make(text,image_factory=qrcode.image.svg.SvgPathImage,box_size=8,border=2)
                out=io.BytesIO(); img.save(out); return self.send_bytes(200,out.getvalue(),'image/svg+xml',{'Cache-Control':'no-store'})
            except Exception as e: return self.send_bytes(400,str(e),'text/plain')
        if path=='/api/map/archive': return self.json(200,{'ok':True,**pmtiles_info()})
        if path=='/api/health': return self.json(200,{'ok':True,'database':'sqlite','path':str(DB_PATH),'auth':False,'version':APP_VERSION})
        if path.startswith('/api/tiles/'):
            try:
                m=re.fullmatch(r'/api/tiles/(\d+)/(\d+)/(\d+)\.png',path)
                if not m: raise ValueError('invalid tile path')
                data,cached=get_tile(*(int(x) for x in m.groups()))
                return self.send_bytes(200,data,'image/png',{'Cache-Control':'public, max-age=31536000','X-Wardriver-Tile-Cache':'HIT' if cached else 'MISS'})
            except Exception as e: return self.send_bytes(404,str(e),'text/plain')
        if path=='/api/tile-cache/stats': return self.json(200,{'ok':True,**tile_cache_stats(),**map_settings()})
        if path=='/api/map/settings': return self.json(200,{'ok':True,**map_settings()})
        if path=='/api/hardware-profiles': return self.json(200,{'ok':True,'profiles':hardware_profiles_list()})
        if path=='/api/sync/settings': return self.json(200,{'ok':True,**sync_settings()})
        if path=='/api/sync/privacy': return self.json(200,sync_privacy_settings())
        if path=='/api/sync/history':
            conn=db(); rows=[dict(r) for r in conn.execute('select * from sync_runs order by created_at desc limit 100').fetchall()]; conn.close(); return self.json(200,{'ok':True,'runs':rows})
        if path=='/api/wigle/own-sync': return self.json(200,wigle_sync.status())
        if path=='/api/wigle/settings': return self.json(200,{'ok':True,**wigle_settings()})
        if path=='/api/wigle/history':
            conn=db(); rows=[dict(r) for r in conn.execute('select * from wigle_runs order by created_at desc limit 100').fetchall()]; conn.close(); return self.json(200,{'ok':True,'runs':rows})
        if path=='/api/wigle/export':
            try:
                qs=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query); source=(qs.get('source') or [''])[0]
                data,count=wigle_csv_for_source(source); name=re.sub(r'[^A-Za-z0-9._-]+','-',source)[:80] or 'import'
                return self.send_bytes(200,data,'text/csv; charset=utf-8',{'Content-Disposition':f'attachment; filename="wardriver-wigle-{name}.csv"','Cache-Control':'no-store'})
            except Exception as e:return self.json(400,{'ok':False,'error':str(e)})
        if path=='/api/imports':
            conn=db()
            try:
                rows=[dict(r) for r in conn.execute('select * from import_runs order by imported_at desc limit 200').fetchall()]
                counts={}
                for r in conn.execute("select source,coalesce(session_id,'') sid,count(*) n from observations group by source,coalesce(session_id,'')"):
                    counts[(r['source'],r['sid'])]=int(r['n'])
                for r in rows:r['current_count']=counts.get((r.get('source'),str(r.get('session_id') or '')),0)
            finally:conn.close()
            return self.json(200,{'imports':rows})
        if path=='/api/routes/history':
            conn=db(); rows=[dict(r) for r in conn.execute('select * from route_history order by created_at desc limit 100').fetchall()]; conn.close()
            for r in rows:
                try:r['geometry']=json.loads(r.pop('geometry_json') or '[]')
                except:r['geometry']=[]
                try:r['steps']=json.loads(r.pop('steps_json') or '[]')
                except:r['steps']=[]
            return self.json(200,{'routes':rows})
        if path=='/api/data-quality':
            conn=db(); q=data_quality(conn); conn.close(); return self.json(200,{'ok':True,**q})
        if path=='/api/compare':
            qs=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query); a=(qs.get('a') or [''])[0]; b=(qs.get('b') or [''])[0]
            if not a or not b: return self.json(400,{'ok':False,'error':'choose two imports'})
            conn=db(); out=compare_sources(conn,a,b); conn.close(); return self.json(200,{'ok':True,**out})
        if path=='/api/geocode':
            try:
                qs=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query); q=(qs.get('city') or [''])[0]
                return self.json(200,geocode_city(q))
            except Exception as e:
                return self.json(502,{'ok':False,'error':str(e),'geocoder':GEOCODER_URL})
        if path=='/api/insights/summary': return self.json(200,performance_summary())
        if path=='/api/hardware/counts': return self.json(200,hardware_counts_summary())
        m_photo=re.fullmatch(r'/api/flock/photo/([0-9a-fA-F-]{36})',path)
        if m_photo:
            try:
                fp,mime=flock_photo_file(m_photo.group(1));return self.send_bytes(200,fp.read_bytes(),mime,{'Cache-Control':'private, max-age=31536000, immutable','Content-Disposition':'inline'})
            except ValueError as e:return self.send_bytes(404,str(e),'text/plain')
        if path=='/api/flock/review':
            q=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            return self.json(200,flock_review_list((q.get('q') or [''])[0],(q.get('level') or ['all'])[0],(q.get('decision') or ['all'])[0],(q.get('limit') or ['500'])[0]))
        if path=='/api/flock/detail':
            try:
                q=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query);key=(q.get('device_key') or [''])[0]
                return self.json(200,flock_review_detail(key))
            except ValueError as e:return self.json(404,{'ok':False,'error':str(e)})
        if path=='/api/flock/verification':
            try:
                q=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query);key=(q.get('device_key') or [''])[0];conn=db()
                try:v=_cached_external_verification(conn,key);src=_flock_source_notes(conn,key)
                finally:conn.close()
                return self.json(200,{'ok':True,'verification':v,'sources':src})
            except ValueError as e:return self.json(404,{'ok':False,'error':str(e)})
        if path=='/api/map/diagnostics':
            try:
                t0=time.perf_counter(); conn=db()
                obs=conn.execute('select count(*) from observations').fetchone()[0]
                try: rtree=conn.execute('select count(*) from observation_rtree').fetchone()[0]
                except Exception: rtree=-1
                devices=conn.execute('select count(*) from devices').fetchone()[0]
                tracks=conn.execute('select count(*) from track_fixes').fetchone()[0]
                bounds=conn.execute('select min(min_lat),max(max_lat),min(min_lon),max(max_lon) from devices').fetchone()
                conn.close()
                pm=pmtiles_info()
                return self.json(200,{'ok':True,'observations':obs,'rtree_rows':rtree,'rtree_matches_count':obs==rtree,'devices':devices,'track_fixes':tracks,'bounds':{'south':bounds[0],'north':bounds[1],'west':bounds[2],'east':bounds[3]},'pmtiles':pm,'elapsed_ms':round((time.perf_counter()-t0)*1000,2),'version':APP_VERSION})
            except Exception as e:return self.json(500,{'ok':False,'error':str(e),'version':APP_VERSION})
        if path=='/api/map/aggregate': return self.json(200,self.map_aggregate())
        if path=='/api/map/devices': return self.json(200,self.map_devices())
        if path=='/api/map/tracks': return self.json(200,self.map_tracks())
        if path=='/api/map/bounds': return self.json(200,self.map_bounds())
        if path=='/api/map/observations': return self.json(200,self.map_observations())
        if path=='/api/observations': return self.json(200,self.observations_page())
        if path=='/api/stats':
            return self.json(200,stats_summary())
        if path=='/api/export/geojson': return self.stream_export_geojson()
        if path=='/api/export/kml': return self.stream_export_kml()
        if path=='/api/export/csv': return self.stream_export_csv()
        rel = 'dashboard.html' if path in ('/','/dashboard','/dashboard/') else path.lstrip('/')
        fp=(PUBLIC/rel).resolve()
        if PUBLIC.resolve() not in fp.parents and fp!=PUBLIC.resolve(): return self.send_bytes(403,'forbidden','text/plain')
        if not fp.exists() or not fp.is_file(): fp=PUBLIC/'dashboard.html'
        ctype=mimetypes.guess_type(str(fp))[0] or 'application/octet-stream'; return self.send_bytes(200,fp.read_bytes(),ctype)
    def do_POST(self):
        path=urllib.parse.urlsplit(self.path).path
        # Validate framing before any handler reads from the socket.
        self.close_connection=True
        if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length', []))>1:
            return self.json(400,{'ok':False,'error':'Unsupported request framing'})
        try:length=int(self.headers.get('Content-Length','0'))
        except ValueError:return self.json(400,{'ok':False,'error':'Invalid Content-Length'})
        limit=IMPORT_MAX_BYTES if path=='/api/import' else FLOCK_PHOTO_MAX_BYTES if path=='/api/flock/photo' else 1024*1024
        if length<0 or length>limit:return self.json(413,{'ok':False,'error':'Request body exceeds endpoint limit'})
        if path=='/api/cell-towers/fetch':
            try:
                payload=json.loads(self.rfile.read(length))
                if not isinstance(payload,dict):raise ValueError('Expected a JSON object')
                return self.json(200,cell_towers.lookup(DB_PATH.parent/'cell-towers.db',payload.get('bbox'),True))
            except ValueError as e:return self.json(400,{'ok':False,'error':str(e)})
            except Exception:return self.json(502,{'ok':False,'error':'Tower provider unavailable; cached towers remain available'})
        if path=='/api/wigle/own-sync':
            try:
                payload=json.loads(self.rfile.read(length) or b'{}')
                if not isinstance(payload,dict):raise ValueError('Expected JSON object')
                return self.json(202,wigle_sync.start(sys.modules[__name__],payload))
            except Exception as e:return self.json(400,{'ok':False,'error':str(e)})
        if path=='/api/flock/photo':
            try:
                q=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                key=(q.get('device_key') or [''])[0];filename=(q.get('filename') or ['photo'])[0];caption=(q.get('caption') or [''])[0]
                claimed=str((q.get('claimed') or ['0'])[0]).strip().lower() in ('1','true','yes','on')
                length=int(self.headers.get('Content-Length','0'))
                if length<=0:return self.json(400,{'ok':False,'error':'empty photo upload'})
                if length>FLOCK_PHOTO_MAX_BYTES:return self.json(413,{'ok':False,'error':f'photo exceeds {FLOCK_PHOTO_MAX_BYTES//(1024*1024)} MB limit'})
                data=self.rfile.read(length)
                if len(data)!=length:raise ValueError('upload ended before Content-Length bytes were received')
                return self.json(200,flock_photo_add(key,data,filename,self.headers.get('Content-Type',''),caption,claimed))
            except ValueError as e:return self.json(400,{'ok':False,'error':str(e)})
            except Exception as e:return self.json(500,{'ok':False,'error':'Flock photo upload failed: '+str(e)})
        if path=='/api/flock/photo/claim':
            try:
                length=int(self.headers.get('Content-Length','0'));payload=json.loads(self.rfile.read(length).decode('utf-8') or '{}')
                return self.json(200,flock_photo_claim(payload.get('photo_id'),payload.get('claimed',True)))
            except ValueError as e:return self.json(400,{'ok':False,'error':str(e)})
            except Exception as e:return self.json(500,{'ok':False,'error':'Flock photo claim failed: '+str(e)})
        if path=='/api/flock/photo/delete':
            try:
                length=int(self.headers.get('Content-Length','0'));payload=json.loads(self.rfile.read(length).decode('utf-8') or '{}')
                return self.json(200,flock_photo_delete(payload.get('photo_id')))
            except ValueError as e:return self.json(400,{'ok':False,'error':str(e)})
            except Exception as e:return self.json(500,{'ok':False,'error':'Flock photo delete failed: '+str(e)})
        if path=='/api/flock/verify':
            try:
                length=int(self.headers.get('Content-Length','0'));payload=json.loads(self.rfile.read(length).decode('utf-8') or '{}')
                return self.json(200,flock_verify_external(payload.get('device_key'),bool(payload.get('force',False))))
            except ValueError as e:return self.json(400,{'ok':False,'error':str(e)})
            except Exception as e:return self.json(502,{'ok':False,'error':'External verification failed: '+str(e)})
        if path=='/api/flock/source':
            try:
                length=int(self.headers.get('Content-Length','0'));payload=json.loads(self.rfile.read(length).decode('utf-8') or '{}')
                return self.json(200,flock_source_add(payload.get('device_key'),payload.get('label'),payload.get('url'),payload.get('note'),payload.get('supports_flock',False),payload.get('source_type','public_record')))
            except ValueError as e:return self.json(400,{'ok':False,'error':str(e)})
            except Exception as e:return self.json(500,{'ok':False,'error':'Flock source save failed: '+str(e)})
        if path=='/api/flock/source/delete':
            try:
                length=int(self.headers.get('Content-Length','0'));payload=json.loads(self.rfile.read(length).decode('utf-8') or '{}')
                return self.json(200,flock_source_delete(payload.get('source_id')))
            except ValueError as e:return self.json(400,{'ok':False,'error':str(e)})
            except Exception as e:return self.json(500,{'ok':False,'error':'Flock source delete failed: '+str(e)})
        if path=='/api/flock/review':
            try:
                length=int(self.headers.get('Content-Length','0'));payload=json.loads(self.rfile.read(length).decode('utf-8') or '{}')
                return self.json(200,flock_review_update(payload.get('device_key'),payload.get('decision'),payload.get('notes')))
            except ValueError as e:return self.json(400,{'ok':False,'error':str(e)})
            except Exception as e:return self.json(500,{'ok':False,'error':'Flock review failed: '+str(e)})
        if path=='/api/ask':
            try:
                length=int(self.headers.get('Content-Length','0'))
                if length<=0 or length>16*1024:return self.json(400,{'ok':False,'error':'invalid Ask Wardriver request'})
                payload=json.loads(self.rfile.read(length).decode('utf-8') or '{}');query=str(payload.get('query') or '').strip();context=payload.get('context') if isinstance(payload.get('context'),dict) else {}
                return self.json(200,ask_execute(query,context))
            except ValueError as e:return self.json(400,{'ok':False,'error':str(e)})
            except Exception as e:return self.json(500,{'ok':False,'error':'Ask Wardriver failed: '+str(e)})
        if path=='/api/hardware-profiles/save':
            try:
                length=int(self.headers.get('Content-Length','0')); payload=json.loads(self.rfile.read(length).decode('utf-8') or '{}')
                pid=str(payload.get('id') or '').strip()[:100] or str(uuid.uuid4())
                name=str(payload.get('name') or '').strip()[:120]
                if not name:return self.json(400,{'ok':False,'error':'profile name is required'})
                gear=normalize_gear_list(payload.get('gear') or [])
                notes=str(payload.get('notes') or '').strip()[:1200]
                now=datetime.now(timezone.utc).isoformat();conn=db()
                try:
                    old=conn.execute('select created_at from hardware_profiles where id=?',(pid,)).fetchone();created=old['created_at'] if old else now
                    conn.execute('insert or replace into hardware_profiles(id,name,gear_json,notes,created_at,updated_at) values (?,?,?,?,?,?)',(pid,name,json.dumps(gear,separators=(',',':')),notes,created,now));conn.commit()
                finally:conn.close()
                return self.json(200,{'ok':True,'profile':{'id':pid,'name':name,'gear':gear,'notes':notes,'created_at':created,'updated_at':now}})
            except Exception as e:return self.json(400,{'ok':False,'error':str(e)})
        if path=='/api/hardware-profiles/delete':
            try:
                length=int(self.headers.get('Content-Length','0'));payload=json.loads(self.rfile.read(length).decode('utf-8') or '{}');pid=str(payload.get('id') or '').strip()
                if not pid:return self.json(400,{'ok':False,'error':'profile id is required'})
                conn=db()
                try:cur=conn.execute('delete from hardware_profiles where id=?',(pid,));conn.commit()
                finally:conn.close()
                return self.json(200,{'ok':True,'deleted':cur.rowcount})
            except Exception as e:return self.json(400,{'ok':False,'error':str(e)})
        if path=='/api/imports/hardware-profile':
            try:
                length=int(self.headers.get('Content-Length','0'));payload=json.loads(self.rfile.read(length).decode('utf-8') or '{}')
                source=str(payload.get('source') or '').strip();pid=str(payload.get('hardware_profile_id') or '').strip()
                session_id=str(payload.get('session_id') or '').strip() or None
                if not source:return self.json(400,{'ok':False,'error':'source is required'})
                snap=hardware_profile_snapshot(pid) if pid else (None,None,None)
                conn=db()
                try:
                    if not session_id:
                        run=conn.execute('select session_id from import_runs where source=? order by imported_at desc limit 1',(source,)).fetchone()
                        session_id=(run['session_id'] if run and run['session_id'] else None)
                    where,args=_drive_scope_clause(source,session_id)
                    n=conn.execute(f'select count(*) from observations where {where}',args).fetchone()[0]
                    if not n:return self.json(404,{'ok':False,'error':'drive/source not found'})
                    keys={r['device_key'] for r in conn.execute(f'select distinct device_key from observations where {where} and device_key is not null',args)}
                    conn.execute(f'update observations set hardware_profile_id=?,hardware_profile_name=?,hardware_profile_gear=? where {where}',snap+args)
                    conn.execute(f'update import_runs set hardware_profile_id=?,hardware_profile_name=?,hardware_profile_gear=? where {where}',snap+args)
                    refresh_devices_for_keys(conn,keys);conn.commit()
                finally:conn.close()
                return self.json(200,{'ok':True,'source':source,'session_id':session_id,'updated':n,'hardware_profile_id':snap[0],'hardware_profile_name':snap[1]})
            except Exception as e:return self.json(400,{'ok':False,'error':str(e)})
        if path=='/api/map/settings':
            try:
                length=int(self.headers.get('Content-Length','0')); payload=json.loads(self.rfile.read(length).decode('utf-8') or '{}')
                provider=str(payload.get('provider') or 'none').strip().lower()
                if provider not in ('none','selfhosted','custom'): raise ValueError('invalid basemap provider')
                template=str(payload.get('tile_url_template') or '').strip()
                if provider=='custom': validate_tile_template(template)
                elif provider=='selfhosted': template='/maps/region.pmtiles'
                attribution=str(payload.get('attribution') or '').strip()[:500]
                cache_allowed=1 if payload.get('cache_allowed') and provider=='custom' else 0
                conn=db(); conn.execute('update map_settings set provider=?,tile_url_template=?,attribution=?,cache_allowed=?,updated_at=? where id=1',(provider,template,attribution,cache_allowed,datetime.now(timezone.utc).isoformat())); conn.commit(); conn.close()
                return self.json(200,{'ok':True,**map_settings()})
            except Exception as e:return self.json(400,{'ok':False,'error':str(e)})
        if path=='/api/map/test':
            try:return self.json(200,test_map_provider())
            except Exception as e:return self.json(502,{'ok':False,'error':str(e)})
        if path=='/api/sync/settings':
            try:
                length=int(self.headers.get('Content-Length','0')); payload=json.loads(self.rfile.read(length).decode('utf-8') or '{}')
                conn=db(); old=sync_settings(conn,include_token=True)
                enabled=1 if payload.get('enabled') else 0
                base=str(payload.get('base_url') or old['base_url'] or SYNC_DEFAULT_BASE_URL).strip().rstrip('/')
                remote_url(base,'/')
                token=old.get('api_token') or ''
                if payload.get('clear_token'): token=''
                elif 'api_token' in payload and str(payload.get('api_token') or '').strip(): token=str(payload.get('api_token')).strip()
                conn.execute('update sync_settings set enabled=?,base_url=?,api_token=?,updated_at=? where id=1',(enabled,base,token,datetime.now(timezone.utc).isoformat())); conn.commit(); conn.close()
                return self.json(200,{'ok':True,**sync_settings()})
            except Exception as e:return self.json(400,{'ok':False,'error':str(e)})
        if path=='/api/sync/privacy':
            try:
                length=int(self.headers.get('Content-Length','0'));payload=json.loads(self.rfile.read(length).decode('utf-8') or '{}')
                return self.json(200,save_sync_privacy_settings(payload.get('config') if isinstance(payload.get('config'),dict) else payload))
            except Exception as e:return self.json(400,{'ok':False,'error':str(e)})
        if path=='/api/sync/privacy/preview':
            try:
                length=int(self.headers.get('Content-Length','0'));payload=json.loads(self.rfile.read(length).decode('utf-8') or '{}');source=str(payload.get('source') or '').strip()
                return self.json(200,sync_privacy_preview(source,payload.get('sample_limit',2),payload.get('config') if isinstance(payload.get('config'),dict) else None))
            except Exception as e:return self.json(400,{'ok':False,'error':str(e)})
        if path=='/api/sync/test':
            try:
                cfg=sync_settings(include_token=True)
                if not cfg['enabled']: return self.json(400,{'ok':False,'error':'enable wardriver.org sync first'})
                resp=wardriver_remote_request('GET',SYNC_HEALTH_PATH,None,cfg)
                return self.json(200,{'ok':True,'status':resp['status'],'endpoint':resp['url']})
            except Exception as e:return self.json(502,{'ok':False,'error':str(e)})
        if path=='/api/sync/upload':
            try:
                length=int(self.headers.get('Content-Length','0')); payload=json.loads(self.rfile.read(length).decode('utf-8') or '{}'); source=str(payload.get('source') or '').strip()
                if not source:return self.json(400,{'ok':False,'error':'choose an import to upload'})
                return self.json(200,sync_source_to_remote(source))
            except Exception as e:return self.json(502,{'ok':False,'error':str(e)})
        if path=='/api/wigle/settings':
            try:
                length=int(self.headers.get('Content-Length','0')); payload=json.loads(self.rfile.read(length).decode('utf-8') or '{}')
                conn=db(); old=wigle_settings(conn,include_token=True); enabled=1 if payload.get('enabled') else 0; donate=1 if payload.get('donate') else 0; token=old.get('api_token') or ''
                if payload.get('clear_token'): token=''
                elif 'api_token' in payload and str(payload.get('api_token') or '').strip(): token=str(payload.get('api_token')).strip()
                conn.execute('update wigle_settings set enabled=?,api_token=?,donate=?,updated_at=? where id=1',(enabled,token,donate,datetime.now(timezone.utc).isoformat())); conn.commit(); conn.close()
                return self.json(200,{'ok':True,**wigle_settings()})
            except Exception as e:return self.json(400,{'ok':False,'error':str(e)})
        if path=='/api/wigle/test':
            try:return self.json(200,wigle_test_connection())
            except Exception as e:return self.json(502,{'ok':False,'error':str(e)})
        if path=='/api/wigle/upload':
            try:
                length=int(self.headers.get('Content-Length','0')); payload=json.loads(self.rfile.read(length).decode('utf-8') or '{}'); source=str(payload.get('source') or '').strip()
                if not source:return self.json(400,{'ok':False,'error':'choose an import to upload'})
                return self.json(200,wigle_upload_source(source))
            except Exception as e:return self.json(502,{'ok':False,'error':str(e)})
        if path=='/api/tile-cache/prefetch':
            try:
                cfg=map_settings()
                if cfg.get('provider')=='none': return self.json(400,{'ok':False,'error':'configure a basemap provider first'})
                if cfg.get('provider')=='selfhosted': return self.json(400,{'ok':False,'error':'self-hosted PMTiles is already fully local; no tile prefetch is needed'})
                if not cfg.get('cache_allowed'): return self.json(400,{'ok':False,'error':'offline caching is disabled for this basemap provider'})
                length=int(self.headers.get('Content-Length','0')); payload=json.loads(self.rfile.read(length).decode())
                b=payload.get('bounds') or {}; zooms=payload.get('zooms') or []
                coords=[]
                for z in sorted(set(max(0,min(19,int(x))) for x in zooms)):
                    x0,y0=slippy_tile(float(b['maxLat']),float(b['minLon']),z); x1,y1=slippy_tile(float(b['minLat']),float(b['maxLon']),z)
                    for x in range(min(x0,x1),max(x0,x1)+1):
                        for y in range(min(y0,y1),max(y0,y1)+1): coords.append((z,x,y))
                if len(coords)>450: return self.json(400,{'ok':False,'error':f'area requires {len(coords)} tiles; zoom in or reduce zoom range (max 450)'})
                got=0; errors=0
                for z,x,y in coords:
                    try:get_tile(z,x,y); got+=1
                    except:errors+=1
                return self.json(200,{'ok':True,'requested':len(coords),'cached':got,'errors':errors,**tile_cache_stats()})
            except Exception as e:return self.json(400,{'ok':False,'error':str(e)})
        if path=='/api/routes/history':
            try:
                length=int(self.headers.get('Content-Length','0')); payload=json.loads(self.rfile.read(length).decode())
                rid=str(uuid.uuid4()); now=datetime.now(timezone.utc).isoformat(); conn=db()
                conn.execute('insert into route_history values (?,?,?,?,?,?,?,?,?)',(rid,payload.get('city') or '',float(payload.get('requested_miles') or 0),float(payload.get('distance_m') or 0),float(payload.get('duration_s') or 0),json.dumps(payload.get('geometry') or []),json.dumps(payload.get('steps') or []),now,None)); conn.commit(); conn.close()
                return self.json(200,{'ok':True,'id':rid,'created_at':now})
            except Exception as e:return self.json(400,{'ok':False,'error':str(e)})
        if path=='/api/routes/complete':
            try:
                length=int(self.headers.get('Content-Length','0')); payload=json.loads(self.rfile.read(length).decode()); rid=str(payload.get('id') or '')
                conn=db(); conn.execute('update route_history set completed_at=? where id=?',(datetime.now(timezone.utc).isoformat(),rid)); conn.commit(); conn.close(); return self.json(200,{'ok':True})
            except Exception as e:return self.json(400,{'ok':False,'error':str(e)})
        if path=='/api/routes/delete':
            try:
                length=int(self.headers.get('Content-Length','0')); payload=json.loads(self.rfile.read(length).decode() or '{}'); rid=str(payload.get('id') or '').strip()
                if not rid: return self.json(400,{'ok':False,'error':'route id is required'})
                conn=db()
                try:
                    cur=conn.execute('delete from route_history where id=?',(rid,)); conn.commit(); deleted=cur.rowcount
                finally: conn.close()
                if not deleted: return self.json(404,{'ok':False,'error':'route not found'})
                return self.json(200,{'ok':True,'id':rid,'deleted':deleted})
            except Exception as e:return self.json(400,{'ok':False,'error':str(e)})
        if path=='/api/routes/clear':
            try:
                if self.headers.get('X-Wardriver-Local')!='1':return self.json(403,{'ok':False,'error':'local write guard required'})
                conn=db()
                try:
                    n=conn.execute('select count(*) from route_history').fetchone()[0]
                    conn.execute('delete from route_history'); conn.commit()
                finally: conn.close()
                return self.json(200,{'ok':True,'deleted':n})
            except Exception as e:return self.json(400,{'ok':False,'error':str(e)})
        if path=='/api/imports/reclassify':
            try:
                length=int(self.headers.get('Content-Length','0'));payload=json.loads(self.rfile.read(length).decode() or '{}')
                source=str(payload.get('source') or '').strip();session_id=str(payload.get('session_id') or '').strip() or None
                method=str(payload.get('collection_method') or '').strip().lower()
                if not source:return self.json(400,{'ok':False,'error':'source is required'})
                if method=='cycling':method='bike'
                allowed={'wardriving','warwalking','bike','bus','stationary','transit','other'}
                if method not in allowed:return self.json(400,{'ok':False,'error':'invalid collection method'})
                conn=db()
                try:
                    where,args=_drive_scope_clause(source,session_id)
                    n=conn.execute(f'select count(*) from observations where {where}',args).fetchone()[0]
                    if not n:return self.json(404,{'ok':False,'error':'drive/source not found'})
                    keys={r['device_key'] for r in conn.execute(f'select distinct device_key from observations where {where} and device_key is not null',args)}
                    conn.execute(f'update observations set collection_method=? where {where}',(method,*args))
                    conn.execute(f'update import_runs set requested_method=?,collection_method=? where {where}',(method,method,*args))
                    refresh_devices_for_keys(conn,keys);refresh_drive_materialization(conn,source,session_id);conn.commit()
                finally:conn.close()
                return self.json(200,{'ok':True,'source':source,'session_id':session_id,'collection_method':method,'updated':n,'verified':True})
            except Exception as e:return self.json(400,{'ok':False,'error':str(e)})
        if path=='/api/imports/delete':
            try:
                length=int(self.headers.get('Content-Length','0'));payload=json.loads(self.rfile.read(length).decode() or '{}')
                source=str(payload.get('source') or '').strip();session_id=str(payload.get('session_id') or '').strip() or None
                if not source:return self.json(400,{'ok':False,'error':'source is required'})
                conn=db()
                try:
                    where,args=_scope_clause(source,session_id)
                    n=conn.execute(f'select count(*) from observations where {where}',args).fetchone()[0]
                    runs=conn.execute(f'select count(*) from import_runs where {where}',args).fetchone()[0]
                    if n==0 and runs==0:return self.json(404,{'ok':False,'error':'source not found'})
                    keys={r['device_key'] for r in conn.execute(f'select distinct device_key from observations where {where} and device_key is not null',args)}
                    conn.execute(f'delete from observations where {where}',args);conn.execute(f'delete from import_runs where {where}',args)
                    refresh_devices_for_keys(conn,keys);refresh_drive_materialization(conn,source,session_id);conn.commit()
                finally:conn.close()
                return self.json(200,{'ok':True,'source':source,'session_id':session_id,'deleted':n,'import_runs_deleted':runs})
            except Exception as e:return self.json(400,{'ok':False,'error':str(e)})
        if path=='/api/reset':
            if self.headers.get('X-Wardriver-Local')!='1':return self.json(403,{'ok':False,'error':'local write guard required'})
            conn=db()
            try:
                conn.execute('begin immediate');conn.execute('delete from observations');conn.execute('delete from import_runs')
                for table in ('devices','track_fixes','drive_devices','drive_stats','drive_days','drive_coverage_cells','coverage_cells','neighborhood_devices','map_cells','source_scope_stats','device_summary_counts','device_discovery_months','device_bssid_parents','flock_assessments','flock_reviews','flock_fingerprints','flock_external_checks','flock_source_notes'):
                    conn.execute('delete from '+table)
                conn.commit()
            except:conn.rollback();raise
            finally:conn.close()
            return self.json(200,{'ok':True})
        if path=='/api/route':
            try:
                length=int(self.headers.get('Content-Length','0'))
                if length<=0 or length>64*1024: return self.json(400,{'ok':False,'error':'invalid route request'})
                payload=json.loads(self.rfile.read(length).decode('utf-8'))
                points=payload.get('points') or []
                mode=str(payload.get('mode') or 'drive').strip().lower()
                out=fetch_driving_route(points)
                out['activity']='walk' if mode=='walk' else 'drive'
                return self.json(200,out)
            except Exception as e:
                return self.json(502,{'ok':False,'error':str(e),'router':ROUTER_URL})
        if path!='/api/import': return self.json(404,{'error':'not found'})
        length=int(self.headers.get('Content-Length','0'))
        if length<=0 or length>IMPORT_MAX_BYTES:return self.json(400,{'error':'invalid upload size (max 512 MB)'})
        name=urllib.parse.unquote(self.headers.get('X-Filename','upload.csv'));ext=Path(name).suffix.lower()
        session_id=str(self.headers.get('X-Drive-ID','') or '').strip()[:100] or None
        session_name=urllib.parse.unquote(str(self.headers.get('X-Drive-Name','') or '')).strip()[:200] or None
        hardware_profile_id=str(self.headers.get('X-Hardware-Profile-ID','') or '').strip()[:100] or None
        spool=tempfile.SpooledTemporaryFile(max_size=8*1024*1024,mode='w+b')
        try:
            remaining=length
            while remaining:
                chunk=self.rfile.read(min(1024*1024,remaining))
                if not chunk:raise ValueError('upload ended before Content-Length bytes were received')
                spool.write(chunk);remaining-=len(chunk)
            spool.seek(0)
            if ext=='.gz':
                ext=Path(name[:-3]).suffix.lower()
                if ext not in ('.csv','.json','.geojson','.gpx'):
                    raise ValueError('Gzip uploads must end in .csv.gz, .json.gz, .geojson.gz or .gpx.gz')
                expanded=tempfile.SpooledTemporaryFile(max_size=8*1024*1024,mode='w+b')
                try:
                    total=0
                    with gzip.GzipFile(fileobj=spool,mode='rb') as archive:
                        while True:
                            chunk=archive.read(min(1024*1024,IMPORT_MAX_BYTES-total+1))
                            if not chunk:break
                            total+=len(chunk)
                            if total>IMPORT_MAX_BYTES:raise ValueError('Decompressed file exceeds the 512 MB limit')
                            expanded.write(chunk)
                    expanded.seek(0)
                except Exception:
                    expanded.close();raise
                spool.close();spool=expanded
            profile_snapshot=hardware_profile_snapshot(hardware_profile_id) if hardware_profile_id else (None,None,None)
            requested=str(self.headers.get('X-Collection-Method','auto') or 'auto').strip().lower()
            allowed={'auto','wardriving','warwalking','bike','bus','cycling','stationary','transit','other','unknown'}
            if requested not in allowed:requested='auto'
            if requested=='cycling':requested='bike'

            if ext in ('.json','.geojson'):
                data=spool.read();rows=parse_geojson(data);sample=rows[:5000]
                rows_factory=lambda:iter(rows)
            elif ext=='.gpx':
                data=spool.read();rows=parse_gpx(data);sample=rows[:5000]
                rows_factory=lambda:iter(rows)
            else:
                sample=list(itertools.islice(iter_csv_stream(spool),5000))
                rows_factory=lambda:(spool.seek(0) or iter_csv_stream(spool))

            inferred=infer_collection_method(sample)
            selected=inferred['method'] if requested=='auto' else requested
            conn=db()
            try:
                conn.execute('begin immediate')
                where,args=_scope_clause(name,session_id)
                existing=conn.execute(f'select count(*) from observations where {where}',args).fetchone()[0]
                old_keys={r['device_key'] for r in conn.execute(f'select distinct device_key from observations where {where} and device_key is not null',args)}
                if existing:conn.execute(f'delete from observations where {where}',args)
                count,rejected,parsed,new_keys=_insert_rows_conn(conn,rows_factory(),name,selected,inferred['method'],inferred['confidence'],
                                                                 session_id,session_name,*profile_snapshot)
                if count==0:raise ValueError('No valid observations; existing import preserved')
                refresh_devices_for_keys(conn,old_keys|new_keys)
                refresh_drive_materialization(conn,name,session_id)
                conn.execute("""insert into import_runs(id,source,parsed,imported,rejected,replaced,imported_at,requested_method,collection_method,
                              inferred_method,inference_confidence,session_id,session_name,hardware_profile_id,hardware_profile_name,hardware_profile_gear)
                              values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                             (str(uuid.uuid4()),name,parsed,count,rejected,existing,datetime.now(timezone.utc).isoformat(),requested,selected,
                              inferred['method'],float(inferred['confidence']),session_id,session_name,*profile_snapshot))
                conn.commit()
            except:
                conn.rollback();raise
            finally:conn.close()
            return self.json(200,{'ok':True,'imported':count,'rejected':rejected,'parsed':parsed,'filename':name,'replaced':existing,
                    'collection_method':selected,'requested_method':requested,'inferred_method':inferred['method'],
                    'inference_confidence':inferred['confidence'],'inference_reason':inferred['reason'],'session_id':session_id,
                    'session_name':session_name,'hardware_profile_id':profile_snapshot[0],'hardware_profile_name':profile_snapshot[1]})
        except Exception as e:return self.json(400,{'ok':False,'error':str(e)})
        finally:spool.close()


class BoundedHTTPServer(HTTPServer):
    """Small bounded pool so a burst of map/API requests cannot spawn unlimited threads."""
    request_queue_size=128
    def __init__(self,address,handler,max_workers=HTTP_WORKERS):
        super().__init__(address,handler)
        self._executor=ThreadPoolExecutor(max_workers=max_workers,thread_name_prefix='wardriver-http')
        self._pending=threading.BoundedSemaphore(max_workers*3)
    def process_request(self,request,client_address):
        self._pending.acquire()
        try:self._executor.submit(self._run_request,request,client_address)
        except:
            self._pending.release();self.shutdown_request(request);raise
    def _run_request(self,request,client_address):
        try:
            request.settimeout(30)
            self.finish_request(request,client_address)
        except Exception:self.handle_error(request,client_address)
        finally:
            self.shutdown_request(request);self._pending.release()
    def server_close(self):
        try:self._executor.shutdown(wait=False,cancel_futures=True)
        finally:super().server_close()

if __name__=='__main__':
    c=db(); c.close(); print(f'Wardriver local SQLite v{APP_VERSION} starting on http://{HOST}:{PORT} · {HTTP_WORKERS} workers', flush=True)
    BoundedHTTPServer((HOST,PORT),H).serve_forever()
