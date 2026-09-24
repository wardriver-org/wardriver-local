"""Opt-in OSM cell-site lookup with a bounded, persistent local cache."""
import json
import math
import sqlite3
import threading
import time
import urllib.parse
import urllib.request

MAX_BYTES = 4 * 1024 * 1024
MAX_FEATURES = 5000
_lock = threading.Lock()
_last_fetch = 0.0


def bounds(value):
    try:
        west, south, east, north = map(float, value)
    except (TypeError, ValueError):
        raise ValueError('bbox must contain west, south, east, north')
    if not all(math.isfinite(v) for v in (west, south, east, north)):
        raise ValueError('bbox must be finite')
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError('Invalid bounds; dateline-crossing views must be split')
    if east-west > 0.5 or north-south > 0.5:
        raise ValueError('Zoom in: tower searches are limited to 0.5 degrees per side')
    return west, south, east, north


def parse_sites(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get('elements'), list):
        raise ValueError('Invalid tower provider response')
    if payload.get('remark'):
        raise ValueError('Tower provider returned an incomplete response; retry later')
    features = {}
    for item in payload['elements']:
        tags = item.get('tags') or {}
        if tags.get('communication:mobile_phone') != 'yes':
            continue
        kind, ident = item.get('type'), item.get('id')
        if kind not in ('node', 'way', 'relation') or not isinstance(ident, int):
            continue
        center = item if kind == 'node' else item.get('center', {})
        try:
            lat, lon = float(center['lat']), float(center['lon'])
        except (KeyError, TypeError, ValueError):
            continue
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        key = f'{kind}/{ident}'
        props = {k: str(tags.get(k) or '')[:300] for k in ('name', 'operator', 'network', 'height', 'ref', 'man_made')}
        props.update(osm_id=key, source='OpenStreetMap', url=f'https://www.openstreetmap.org/{key}')
        features[key] = {'type': 'Feature', 'id': key, 'geometry': {'type': 'Point', 'coordinates': [lon, lat]}, 'properties': props}
        if len(features) > MAX_FEATURES:
            raise ValueError('Too many tower records; zoom in')
    return list(features.values())


def lookup(db_path, bbox, fetch=False, endpoint='https://overpass-api.de/api/interpreter'):
    global _last_fetch
    west, south, east, north = bounds(bbox)
    conn = sqlite3.connect(str(db_path), timeout=10)
    try:
        conn.execute('CREATE TABLE IF NOT EXISTS sites (id TEXT PRIMARY KEY, lon REAL, lat REAL, feature TEXT, fetched REAL)')
        conn.execute('CREATE INDEX IF NOT EXISTS sites_location ON sites(lon,lat)')
        if fetch:
            if not _lock.acquire(blocking=False):
                raise ValueError('A tower fetch is already running')
            try:
                if time.monotonic() - _last_fetch < 10:
                    raise ValueError('Wait 10 seconds between tower fetches')
                _last_fetch = time.monotonic()
                query = f'[out:json][timeout:20];nwr["communication:mobile_phone"="yes"]({south},{west},{north},{east});out center tags;'
                req = urllib.request.Request(endpoint, data=urllib.parse.urlencode({'data': query}).encode(), headers={'User-Agent': 'WardriverLocal/2.21.0', 'Content-Type': 'application/x-www-form-urlencoded'})
                with urllib.request.urlopen(req, timeout=25) as response:
                    raw = response.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise ValueError('Tower response too large; zoom in')
                features = parse_sites(json.loads(raw))
                now = time.time()
                with conn:
                    conn.execute('DELETE FROM sites WHERE lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?', (west, east, south, north))
                    conn.executemany('INSERT OR REPLACE INTO sites VALUES (?,?,?,?,?)', [(f['id'], *f['geometry']['coordinates'], json.dumps(f), now) for f in features])
                    conn.execute('DELETE FROM sites WHERE id IN (SELECT id FROM sites ORDER BY fetched DESC, id LIMIT -1 OFFSET 100000)')
            finally:
                _lock.release()
        rows = conn.execute('SELECT feature,fetched FROM sites WHERE lon BETWEEN ? AND ? AND lat BETWEEN ? AND ? LIMIT ?', (west,east,south,north,MAX_FEATURES+1)).fetchall()
        return {'ok': True, 'type': 'FeatureCollection', 'features': [json.loads(r[0]) for r in rows[:MAX_FEATURES]], 'truncated': len(rows)>MAX_FEATURES, 'oldest_fetched': min((r[1] for r in rows), default=None), 'source': '© OpenStreetMap contributors (ODbL)', 'cached': not fetch}
    finally:
        conn.close()
