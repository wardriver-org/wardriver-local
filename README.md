<div align="center">

# 📡 Wardriver Local

### Your surveys. Your maps. Your data.

A self-hosted wireless survey dashboard for walking, cycling, driving, and transit.

![Docker Compose](https://img.shields.io/badge/deploy-Docker_Compose-2496ED?style=for-the-badge&logo=docker&logoColor=white)
![SQLite](https://img.shields.io/badge/storage-SQLite-a6e3a1?style=for-the-badge)
![PMTiles](https://img.shields.io/badge/maps-PMTiles-cba6f7?style=for-the-badge)

</div>

Import your observations, explore coverage, and plan your next survey. Wardriver runs on your own machine with a local SQLite database and optional external integrations.

## ✨ Features

| Workspace | Explore |
| :--- | :--- |
| **Map** | Device points, GPS traces, heatmaps, coverage gaps, and optional cached cell sites. |
| **Survey** | Imports, collection-method tagging, drive/walk planning, and navigation exports. |
| **Insights** | Statistics, XP, awards, and optional user-configured neighborhood zones. |
| **Flock** | Conservative candidate classification, manual review, photos, and public-source checks. |
| **Data** | Record browsing, import comparisons, exports, and local plain-language queries. |

- **Import:** WiGLE CSV, CSV, JSON, GeoJSON, GPX, and gzip-compressed equivalents.
- **Export:** CSV, GeoJSON, KML, and planned-route GPX.
- **Collection methods:** walking, cycling, driving, transit, stationary, and other. Bus has its own category; rail journeys can use Transit.
- **WiGLE:** manually sync your own upload history and optionally send a selected local import.
- **Maps:** optional local PMTiles, custom raster tiles, or a grid without a basemap.

## 🚀 Quick start

Install Docker Engine and the Docker Compose plugin. From this repository:

```bash
cp .env.example .env
docker compose up -d --build
```

Open **[http://127.0.0.1:8787](http://127.0.0.1:8787)** and import a survey under **Survey**. `samples/sample.csv` contains synthetic demonstration data.

Fresh installations start with a global view and no geographic basemap. No home city or survey region is bundled. Use map search or **Fit** after importing observations to move to your survey area.

For a local basemap, set `WARDIVER_MAP_BBOX` in `.env` to your desired `west,south,east,north` bounds, then run:

```bash
sh scripts/setup-local-map.sh
```

This downloads the selected region into the persistent map volume and enables it. See [Configuration](docs/configuration.md) for details.

> The dashboard binds to localhost and has no built-in login. Remote access requires an appropriately configured authenticated access layer.

## 🛡️ Data & sharing

Local imports stay in your database. Uploads are optional. wardriver.org privacy controls can transform identifiers, locations, times, and metadata before sharing; preview the outbound payload first. These controls do **not** apply to WiGLE uploads, which have separate settings.

A local basemap works offline after setup. Routing, geocoding, verification, vendor updates, and sync may contact their configured services. Flock and hardware labels are inferred unless manually confirmed; observation coordinates describe the collector's location.

## 🐳 Maintenance

Back up your data and configuration before upgrading. Keep the Compose project name `wardriver-local` so your existing volumes are reused.

```bash
docker compose up -d --build --force-recreate wardriver nginx
docker compose ps
docker compose logs --tail=100 wardriver nginx
```

| Volume | Contents |
| :--- | :--- |
| `wardriver_data` | Survey database, settings, photos, and cell-tower cache. |
| `wardriver_maps` | Your regional PMTiles archive. |
| `wardriver_nginx_cache` | Proxy cache. |

**Do not run `docker compose down -v` unless you intend to delete these volumes.** Use `docker compose stop` to pause the stack.

This distribution removes the previously bundled regional neighborhood catalog. Existing observations and photos are preserved; neighborhood progress and XP reflect your locally configured zones. Add your own zones as described in the configuration guide.

## 📚 Documentation

- [Configuration & troubleshooting](docs/configuration.md)
- [WiGLE sync & remote API](docs/sync.md)
- [Upload privacy](docs/privacy.md)
- [Queries, Flock review & awards](docs/features.md)
- [Architecture & performance](docs/architecture.md)

## 🤝 Development

Run the regression checks with Python and Node.js:

```bash
python3 -m unittest discover -s tests -v
for test in tests/test_*.js; do node "$test" || exit 1; done
```

Report reproducible bugs with small anonymized samples. Remove credentials, private locations, and identifying survey data before posting an issue.

---

**Map what you discover. Understand where you've been. Plan what's next.**
