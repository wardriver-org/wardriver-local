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

No regional catalog is bundled. To enable neighborhood awards, create your own
`neighborhoods.json` as a list of zones. This synthetic example illustrates the
format; replace its bounds and name with a zone you want to survey:

```json
[
  {"key":"my-zone","name":"My survey zone","group":"Local zones","icon":"📍",
   "south":10.0,"north":10.1,"west":-10.1,"east":-10.0}
]
```

Keys must be unique lowercase identifiers. Coordinates must be finite, ordered
geographic bounds; antimeridian-crossing boxes must be split into separate zones.
Up to 1,000 zones are supported. Copy the file into the persistent data volume:

```bash
docker compose cp neighborhoods.json wardriver:/data/neighborhoods.json
docker compose restart wardriver
```

On restart, changed catalogs rebuild neighborhood memberships from existing
devices. Observations, reviews, and photos are preserved. Neighborhood XP may
change. An empty list disables neighborhood awards; invalid files stop startup
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
