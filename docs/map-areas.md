# Download maps from Wardriver Local

Open **Settings → Download map areas**. Search for a city, region, or country; use the current map view; or enter west,south,east,north bounds. Review the bounds, choose a maximum zoom and an available [Protomaps build date](https://maps.protomaps.com/builds), allow the external download, and click **Download area**.

Select **Use map** once verification completes. The app uses its existing local MapLibre/PMTiles renderer; a separate TileServer GL service is not required. These Protomaps archives are not OpenMapTiles-schema MBTiles for the separate tile-server repository.

## Install / upgrade

Rebuild the image from this branch:

```sh
docker compose up -d --build
```

The image copies the static PMTiles CLI from `protomaps/go-pmtiles:v1.30.1`. Native installations must install that CLI as `pmtiles` on PATH (or set `WARDIVER_PMTILES_BIN`). The service account needs write permission to the database parent directory. No Docker socket, privileged container, or extra listening port is used.

Archives and metadata live in `/data/map-areas`, within the existing `wardriver_data` volume. The original `/maps/region.pmtiles` stays untouched and available through **Use bundled local map**. Back up the data volume normally.

## Coverage, updates, and storage

- Search returns up to five geocoder matches with **rectangular bounding boxes**, not administrative boundary polygons. Confirm the coordinates. Country boxes may contain neighboring countries or large stretches of sea. Split date-line-crossing areas into separate downloads.
- The maximum detail is zoom 15. Smaller areas and lower zoom levels use less disk space. Exact size is not known before extraction; the UI shows free storage, bytes written, and the per-download limit rather than a fabricated percentage.
- One extraction runs at a time. The default limit is 20 GiB per download, with 1 GiB free-space reserve and a six-hour timeout. Configure `WARDIVER_MAP_AREA_MAX_GB` and `WARDIVER_MAP_AREA_TIMEOUT` in `.env`. Monitoring is periodic, so these limits are safeguards rather than a filesystem quota. Very large countries may need smaller areas or lower detail.
- **Prepare update** selects the existing area's bounds and zoom. Choose a new build date and download a separate archive. It never overwrites an active map. Use the new map, then delete the old one. The active map cannot be deleted from this panel.
- Daily builds can expire. A download failure can mean the selected date is unavailable; select a retained build from the linked list. Nothing is downloaded at startup, and updates are manual.
- Cancellation, errors, timeout, and low disk remove partial output. Restarting the app discards interrupted downloads on the first map-area request; they must be started again. Completed maps persist. Downloads are not resumable.
- Completed extracts pass the CLI's `verify` check before publication. IDs give each completed archive a distinct URL, avoiding stale chunks when switching maps. There is no automatic map merging or coverage-based switching.

## Privacy and access

Clicking Search sends only the entered place name to the configured `WARDIVER_GEOCODER_URL` (Nominatim by default). Searches are button-triggered, rate-limited, and cached in process. No autocomplete requests occur while typing. Downloading makes range requests to `build.protomaps.com`, exposing the requesting server's address and the selected region through tile requests. Observations and survey history are not part of either request. Opening the build-list link contacts Protomaps from the browser.

The application is a trusted-local-user tool; keep its existing loopback binding or put authentication in front of remote access. Download operations use a per-process request token against cross-site form requests. This is not a substitute for user authentication. Arbitrary download URLs and shell commands are not accepted by the API.

Required OpenStreetMap/Protomaps attribution is retained when selecting a downloaded map. See [Protomaps downloads](https://docs.protomaps.com/basemaps/downloads) and [PMTiles CLI](https://docs.protomaps.com/pmtiles/cli) for dataset terms and extraction details.
