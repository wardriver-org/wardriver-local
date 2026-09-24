# Configuration

## Maps

Fresh databases use the grid basemap. Existing databases keep saved map settings.
Set `WARDIVER_MAP_BBOX` in `.env` before downloading a regional PMTiles archive.
The order is `west,south,east,north`, in decimal degrees; use the bounds of your
survey area. Leave no spaces around the commas. No default region is supplied.
The configured source URL must serve a compatible, accessible PMTiles archive.

```bash
sh scripts/setup-local-map.sh
```

The script starts the app, downloads a missing archive, and selects it in map
settings. Pass `--force` to replace the stored map after changing regions. Larger
regions and higher `WARDIVER_MAP_MAXZOOM` values need more storage and download
time. Archive replacement is explicit and affects the shared map volume.

If you already have a PMTiles archive at `maps/region.pmtiles`, import it with:

```bash
sh scripts/install-map.sh
```

Browser libraries are downloaded during the Docker build. Runtime maps read
`/maps/region.pmtiles` using HTTP Range requests; no TileServer GL is required.

## Neighborhood awards

City neighborhood packs load automatically when a successful import contains an
observation inside a supported city's activation area. The major-city list is
fixed to the 2020 Census: incorporated places of at least 250,000 residents in
the 50 states/DC, plus Urban Honolulu CDP (88 cities). Smaller cities are excluded,
except the nearby communities retained within the original Los Angeles pack.
The map still starts globally; restoring the Los Angeles catalog does not make
Los Angeles the user's default location.

The application reads only a small city index initially. An RTree query checks
actual observations, then a city-polygon check filters bounding-box false positives.
Only matched city files are decompressed; at most eight packs are cached in memory.
City activation and neighborhood progress use the same transaction as the import.
Rejected/rolled-back imports do not activate cities. Existing observations also
activate matching cities once at upgrade/startup. Demonstration rows with the
reserved `sample` source do not trigger activation.

Activation is persistent in SQLite. Deleting an individual import retains its
city catalog but recalculates its device progress. The full data reset clears all
city activation. Restarting needs no download, and neighborhood loading sends no
survey data to an external service. It does not download basemap tiles.

Source coverage is not exhaustive or uniform: some cities have only a few mapped
neighborhoods. Zillow polygons, OSM place-based targets, city open data, and the
original Los Angeles boxes are attributed separately in the catalog. Approximate
boxes are marked in the UI. Device membership uses the materialized device's
representative coordinate, consistent with the dashboard's existing device model.
City activation instead uses raw observation coordinates, so a moving device can
activate more than one city. Old neighborhood keys are retained for the 50 restored
Los Angeles zones.

### Optional custom zones

To add private survey zones, create a local `neighborhoods.json` list. The example
below is synthetic; replace its bounds and name with a zone you want to survey:

```json
[
  {"key":"my-zone","name":"My survey zone","group":"Local zones","icon":"📍",
   "south":10.0,"north":10.1,"west":-10.1,"east":-10.0}
]
```

Custom keys override matching bundled keys; other custom zones are added alongside
activated city packs. No custom configuration is required for the bundled catalog.

Keys must be unique lowercase identifiers. Coordinates must be finite, ordered
geographic bounds; antimeridian-crossing boxes must be split into separate zones.
Up to 1,000 zones are supported. Copy the file into the persistent data volume:

```bash
docker compose cp neighborhoods.json wardriver:/data/neighborhoods.json
docker compose restart wardriver
```

On restart, changed catalogs rebuild neighborhood memberships from existing
devices. Observations, reviews, and photos are preserved. Neighborhood XP may
change. An empty list removes custom zones only; bundled cities still activate. Invalid files stop startup
with an error instead of silently choosing a different region. This private
configuration is excluded from Git.

## Optional services

Set `WARDIVER_ROUTER_URL` and `WARDIVER_GEOCODER_URL` in `.env` to your preferred
OSRM-compatible router and Nominatim-compatible geocoder. The defaults are public
services and require internet access. Local map storage alone does not make
routing or place search offline.

## Imports

Observation files support up to 512 MiB (536,870,912 bytes). For gzip, this limit
applies separately to compressed and expanded contents. CSV uses disk spooling;
JSON, GeoJSON, and GPX need memory for parsing. ZIP/TAR observation imports are
not supported. Re-importing a source filename replaces that source.

## Troubleshooting

```bash
curl http://127.0.0.1:8787/api/health
curl http://127.0.0.1:8787/api/map/archive
curl -X POST http://127.0.0.1:8787/api/map/test
docker compose logs --tail=100 wardriver nginx
```

Use your configured `WARDIVER_PORT` if it differs from 8787. A byte-range request
to an installed archive should return HTTP 206:

```bash
curl -i -H 'Range: bytes=0-31' http://127.0.0.1:8787/maps/region.pmtiles
```

For missing browser map assets, rebuild with `docker compose build --no-cache`
and then run `docker compose up -d`.
