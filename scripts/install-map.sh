#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
if [ -s maps/region.pmtiles ]; then
  docker compose build wardriver
  docker compose run --rm --user 0 -v wardriver-local_wardriver_maps:/maps:rw -v "$(pwd)/maps:/import:ro" --entrypoint sh wardriver -c 'test -s /maps/region.pmtiles || cp /import/region.pmtiles /maps/region.pmtiles'
fi
exec sh scripts/setup-local-map.sh
