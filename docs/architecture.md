# Architecture

Wardriver Local runs a Python HTTP application behind nginx with SQLite storage.
The intended deployment is a personal, local dashboard, not a multi-user server.

## Storage and queries

Raw observations remain the source of truth. Materialized devices, GPS fixes,
drive summaries, map rollups, and neighborhood memberships support interactive
queries without repeatedly scanning every observation.

- SQLite uses WAL mode, RTree indexes, and recorded startup migrations.
- Device and track queries use separate viewport requests.
- Dense views use aggregate cells instead of a silently truncated point sample.
- Track pages are accumulated before replacing the last successful snapshot;
  stale or failed requests cannot publish a partial replacement.
- Data browsing is paginated; exports stream responses.
- Neighborhood city packs activate transactionally from indexed observations.
- The small city index is loaded eagerly; city files use a bounded eight-pack cache.
- Polygon membership and spatial buckets filter active neighborhood candidates.
- Memberships rebuild when a new city activates or a catalog changes; ordinary
  repeated-city imports update only affected device memberships.

The default worker pool has eight workers, bounded to 2–32 by
`WARDIVER_HTTP_WORKERS`. Increasing concurrency can increase contention rather
than improve SQLite throughput.

## Maps and proxy

nginx exposes the dashboard on localhost and serves the PMTiles archive with
HTTP byte-range support. The application also supports archive inspection.
MapLibre and PMTiles browser libraries are bundled during the container build.
The map-init Compose profile is a one-shot regional archive downloader.

The application data and map archive are separate persistent Docker volumes.
nginx has a separate cache volume. Do not delete these as part of a normal update.

## Limits

Large viewport track queries still transfer their matching GPS fixes into browser
memory. Geometry simplification reduces rendered vertices, not transfer size.
JSON/GeoJSON and GPX import parsing require memory for expanded content; CSV uses
disk spooling. Back up the entire data volume before upgrades, and allow first-run
migrations to complete on large datasets.

Regression tests cover backend and browser logic using fixtures. They do not
replace a Docker deployment check, live browser testing, or testing external APIs
with real credentials and quotas.
