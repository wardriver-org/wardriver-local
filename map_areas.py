"""Opt-in regional PMTiles downloads. No network activity until an explicit action."""
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import uuid
from datetime import date


def bounds(value):
    try:
        b = [float(x) for x in value]
    except (TypeError, ValueError):
        raise ValueError('Enter west, south, east, north coordinates')
    if len(b) != 4 or not all(math.isfinite(x) for x in b):
        raise ValueError('Four finite coordinates are required')
    w, s, e, n = b
    if not (-180 <= w < e <= 180 and -85.05112878 <= s < n <= 85.05112878):
        raise ValueError('Invalid bounds; split areas crossing the date line into two downloads')
    return b


class Areas:
    def __init__(self, root):
        self.root = Path(root)
        self.lock = threading.RLock()
        self.search_lock = threading.Lock()
        self.last_search = 0
        self.cache = {}
        self.token = secrets.token_urlsafe(32)
        self.job = None
        self.cancelled = threading.Event()
        self.binary = os.environ.get('WARDIVER_PMTILES_BIN', 'pmtiles')
        self.limit = max(1, int(os.environ.get('WARDIVER_MAP_AREA_MAX_GB', '20'))) * 1024**3
        self.reserve = 1024**3
        self.timeout = max(60, int(os.environ.get('WARDIVER_MAP_AREA_TIMEOUT', '21600')))
        # Download files are never published until verified and renamed atomically.
        self.root.mkdir(parents=True, exist_ok=True)
        for p in self.root.glob('*.part'):
            p.unlink(missing_ok=True)

    def path(self, key):
        if not re.fullmatch(r'[a-f0-9]{32}', str(key)):
            raise ValueError('Invalid map area ID')
        return self.root / (key + '.pmtiles')

    def records(self):
        out = []
        for f in self.root.glob('*.json'):
            try:
                row = json.loads(f.read_text())
                p = self.path(row['id'])
                if p.is_file():
                    row['bytes'] = p.stat().st_size
                    row['url'] = '/maps/areas/' + row['id'] + '.pmtiles'
                    out.append(row)
            except (OSError, ValueError, KeyError):
                continue
        return sorted(out, key=lambda x: x['created_at'], reverse=True)

    def status(self):
        with self.lock:
            job = dict(self.job) if self.job else None
            if job and job['state'] in ('downloading', 'verifying'):
                p = self.root / (job['id'] + '.part')
                try: job['bytes'] = p.stat().st_size
                except FileNotFoundError: pass
            return {'ok': True, 'token': self.token, 'areas': self.records(), 'job': job,
                    'free_bytes': shutil.disk_usage(self.root).free, 'limit_bytes': self.limit,
                    'available': bool(shutil.which(self.binary))}

    def search(self, query, geocoder):
        query = str(query).strip()
        if not 2 <= len(query) <= 160:
            raise ValueError('Enter a city, region, or country (2–160 characters)')
        with self.search_lock:
            if query in self.cache:
                return self.cache[query]
            if time.monotonic() - self.last_search < 1.1:
                raise ValueError('Wait a second before searching again')
            self.last_search = time.monotonic()
            params = urllib.parse.urlencode({'q': query, 'format': 'jsonv2', 'limit': 5})
            req = urllib.request.Request(geocoder + '/search?' + params, headers={
                'User-Agent': 'WardriverLocal map-area picker (https://github.com/wardriver-org/wardriver-local)',
                'Accept': 'application/json'})
            with urllib.request.urlopen(req, timeout=12) as r:
                rows = json.loads(r.read(1024 * 1024))
            results = []
            for row in rows:
                try:
                    s, n, w, e = map(float, row['boundingbox'])
                    b = bounds([w, max(s, -85.05112878), e, min(n, 85.05112878)])
                    results.append({'name': row['display_name'], 'bbox': b})
                except (KeyError, ValueError, TypeError):
                    continue
            if len(self.cache) >= 100: self.cache.clear()
            self.cache[query] = results
            return results

    def start(self, payload):
        b = bounds(payload.get('bbox'))
        name = str(payload.get('name') or '').strip()[:160]
        if not name: raise ValueError('Name this area')
        zoom = payload.get('maxzoom')
        if isinstance(zoom, bool) or not isinstance(zoom, int) or not 0 <= zoom <= 15:
            raise ValueError('Maximum zoom must be an integer from 0 to 15')
        build = str(payload.get('build') or '')
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', build):
            raise ValueError('Select an available Protomaps build date')
        if date.fromisoformat(build) > date.today(): raise ValueError('Build date is in the future')
        if payload.get('consent') is not True:
            raise ValueError('Confirm the external map download first')
        if not shutil.which(self.binary): raise ValueError('PMTiles downloader is missing; rebuild the Docker image')
        with self.lock:
            if self.job and self.job['state'] in ('downloading', 'verifying'):
                raise ValueError('A map download is already running')
            if shutil.disk_usage(self.root).free <= self.reserve:
                raise ValueError('At least 1 GiB of free disk space must remain')
            self.cancelled.clear()
            self.job = {'id': uuid.uuid4().hex, 'name': name, 'bbox': b, 'maxzoom': zoom,
                        'build': build, 'state': 'downloading', 'bytes': 0,
                        'created_at': time.time(), 'message': 'Preparing regional extract…'}
            threading.Thread(target=self._run, args=(dict(self.job),), daemon=True).start()
            return dict(self.job)

    def _command(self, args, part):
        # No shell, user URLs, or inherited cloud credentials. Fixed public source only.
        env = {'PATH': os.environ.get('PATH', ''), 'HOME': str(self.root), 'TMPDIR': str(self.root)}
        with subprocess.Popen([self.binary] + args, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, env=env) as process:
            started = time.monotonic()
            try:
                while process.poll() is None:
                    if self.cancelled.wait(.25): raise ValueError('Download cancelled')
                    if time.monotonic() - started > self.timeout: raise ValueError('Download timed out')
                    if part.exists() and part.stat().st_size > self.limit:
                        raise ValueError('Area exceeds the configured size limit; reduce area or zoom')
                    if shutil.disk_usage(self.root).free < self.reserve:
                        raise ValueError('Stopped to preserve 1 GiB of free disk space')
                if self.cancelled.is_set(): raise ValueError('Download cancelled')
                if process.returncode:
                    raise ValueError('PMTiles command failed. Check the build date and network access; try a smaller area or lower zoom.')
            finally:
                if process.poll() is None:
                    process.terminate()
                    try: process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()

    def _run(self, row):
        part = self.root / (row['id'] + '.part')
        final = self.path(row['id'])
        try:
            source = 'https://build.protomaps.com/' + row['build'].replace('-', '') + '.pmtiles'
            self._command(['extract', source, str(part), '--bbox=' + ','.join(map(str, row['bbox'])),
                           '--maxzoom=' + str(row['maxzoom']), '--download-threads=4'], part)
            with self.lock:
                self.job.update(state='verifying', message='Verifying archive…')
            with part.open('rb') as f:
                if f.read(8) != b'PMTiles\x03': raise ValueError('Invalid PMTiles v3 archive')
            if part.stat().st_size > self.limit: raise ValueError('Area exceeds configured size limit')
            self._command(['verify', str(part)], part)
            with self.lock:
                if self.cancelled.is_set(): raise ValueError('Download cancelled')
                row.update(state='ready', bytes=part.stat().st_size, message='Ready to use')
                meta = self.root / (row['id'] + '.json')
                temp = meta.with_suffix('.tmp')
                temp.write_text(json.dumps(row))
                part.replace(final)
                temp.replace(meta)
                self.job = row
        except Exception as exc:
            with self.lock:
                self.job.update(state='cancelled' if self.cancelled.is_set() else 'failed', message=str(exc))
        finally:
            part.unlink(missing_ok=True)

    def cancel(self):
        with self.lock:
            if self.job and self.job['state'] in ('downloading', 'verifying'):
                self.cancelled.set()

    def remove(self, key, active_url):
        with self.lock:
            p = self.path(key)
            if active_url == '/maps/areas/' + key + '.pmtiles':
                raise ValueError('Select another basemap before deleting the active area')
            p.unlink(missing_ok=True)
            p.with_suffix('.json').unlink(missing_ok=True)
