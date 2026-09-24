"""Offline major-city packs, activated transactionally by real observations."""
import gzip
import hashlib
import json
import math
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parent / 'catalog' / 'neighborhoods'


def in_ring(lon, lat, ring):
    inside = False
    for a, b in zip(ring, ring[1:] + ring[:1]):
        x1, y1 = a[:2]; x2, y2 = b[:2]
        cross = (lon-x1)*(y2-y1)-(lat-y1)*(x2-x1)
        if abs(cross) <= 1e-12 and min(x1,x2)-1e-12 <= lon <= max(x1,x2)+1e-12 and min(y1,y2)-1e-12 <= lat <= max(y1,y2)+1e-12:
            return True
        if (y1 > lat) != (y2 > lat) and lon < (x2-x1)*(lat-y1)/(y2-y1)+x1:
            inside = not inside
    return inside


def in_geometry(lon, lat, geometry):
    if not geometry:
        return True  # Explicit legacy/custom survey box.
    polys = [geometry['coordinates']] if geometry['type'] == 'Polygon' else geometry['coordinates']
    return any(poly and in_ring(lon,lat,poly[0]) and not any(in_ring(lon,lat,hole) for hole in poly[1:]) for poly in polys)


def contains(zone, lat, lon):
    return zone['south'] <= lat <= zone['north'] and zone['west'] <= lon <= zone['east'] and in_geometry(lon,lat,zone.get('geometry'))


@lru_cache(maxsize=1)
def manifest():
    return json.loads((DATA / 'index.json').read_text())


@lru_cache(maxsize=8)
def pack(city_id, digest):
    # Only manifest-owned numeric Census IDs can resolve files.
    city = next(c for c in manifest()['cities'] if c['id'] == city_id)
    path = DATA / (city_id + '.json.gz')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError('Neighborhood pack integrity check failed: ' + city['name'])
    return json.loads(gzip.decompress(raw))['zones']


def setup(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS neighborhood_cities (city_id TEXT PRIMARY KEY, digest TEXT NOT NULL)')


def active_cities(conn):
    ids = {r[0] for r in conn.execute('SELECT city_id FROM neighborhood_cities')}
    return [c for c in manifest()['cities'] if c['id'] in ids]


def zones(conn, custom):
    # User-defined keys override a bundled zone with the same key.
    result = {z['key']:z for z in custom}
    for city in active_cities(conn):
        for z in pack(city['id'], city['sha256']):
            result.setdefault(z['key'], z)
    return list(result.values())


def activate(conn):
    """Check indexed observations, not an upload-wide bbox or browser viewport.

    Called inside the import transaction. A later rollback also rolls back city
    activation. Packs contain public geography only; no user data leaves the app.
    """
    setup(conn)
    old = dict(conn.execute('SELECT city_id,digest FROM neighborhood_cities'))
    changed = False
    for city in manifest()['cities']:
        if city['id'] in old:
            if old[city['id']] != city['sha256']:
                pack(city['id'],city['sha256'])
                conn.execute('UPDATE neighborhood_cities SET digest=? WHERE city_id=?',(city['sha256'],city['id']))
                changed = True
            continue
        bounds = city['bounds']
        rows = conn.execute('''SELECT o.latitude,o.longitude FROM observation_rtree r
            JOIN observations o ON o.rowid=r.rowid
            WHERE r.max_lat>=? AND r.min_lat<=? AND r.max_lon>=? AND r.min_lon<=?
            AND coalesce(o.source,'') != 'sample' ''',
            (bounds['south'],bounds['north'],bounds['west'],bounds['east']))
        found = False
        for row in rows:
            lat,lon = row[0],row[1]
            if contains(bounds,lat,lon) and (in_geometry(lon,lat,city['geometry']) or any(contains(b,lat,lon) for b in city.get('legacy_activation_boxes',[]))):
                found = True
                break
        rows.close()
        if found:
            pack(city['id'],city['sha256'])  # Validate before persisting activation.
            conn.execute('INSERT INTO neighborhood_cities VALUES (?,?)',(city['id'],city['sha256']))
            changed = True
    return changed


class ZoneIndex:
    """Small spatial buckets avoid comparing every device to every active zone."""
    def __init__(self, items):
        self.buckets = {}; self.large = []
        for z in items:
            x0,x1=math.floor(z['west']*10),math.floor(z['east']*10)
            y0,y1=math.floor(z['south']*10),math.floor(z['north']*10)
            if (x1-x0+1)*(y1-y0+1)>1000:
                self.large.append(z);continue
            for x in range(x0,x1+1):
                for y in range(y0,y1+1):self.buckets.setdefault((x,y),[]).append(z)

    def matches(self, lat, lon):
        candidates=self.buckets.get((math.floor(lon*10),math.floor(lat*10)),[])
        return (z for z in candidates+self.large if contains(z,lat,lon))
