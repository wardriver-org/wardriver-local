#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
[ -f .env ] || cp .env.example .env
docker compose up -d --build --wait
if [ "${1:-}" = '--force' ]; then
  docker compose --profile map-setup config --format json | docker compose exec -T wardriver python -c '
import json, math, sys
command=json.load(sys.stdin)["services"]["map-init"]["command"]
raw=next(x.split("=",1)[1] for x in command if x.startswith("--bbox="))
w,s,e,n=map(float,raw.split(","))
if not all(map(math.isfinite,(w,s,e,n))) or not (-180<=w<e<=180 and -90<=s<n<=90):
    raise SystemExit("Set valid WARDIVER_MAP_BBOX bounds before replacing the map")
'
  docker compose run --rm --user 0 -v wardriver-local_wardriver_maps:/maps:rw --entrypoint sh wardriver -c 'rm -f /maps/region.pmtiles'
fi
if ! docker compose exec -T wardriver sh -c 'test -s /maps/region.pmtiles'; then
  echo 'Downloading your configured region. WARDIVER_MAP_BBOX must be set in .env.'
  docker compose --profile map-setup run --rm map-init
fi
docker compose exec -T wardriver python - <<'PY'
import json, urllib.request
body = json.dumps({'provider':'selfhosted','tile_url_template':'/maps/region.pmtiles','attribution':'© OpenStreetMap contributors · Protomaps','cache_allowed':False}).encode()
request = urllib.request.Request('http://127.0.0.1:8787/api/map/settings', data=body, headers={'Content-Type':'application/json'})
with urllib.request.urlopen(request) as response:
    print(response.read().decode())
PY
