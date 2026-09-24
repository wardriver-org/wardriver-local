FROM node:22-alpine AS webdeps
WORKDIR /deps
RUN npm init -y >/dev/null 2>&1 \
 && npm install --no-audit --no-fund maplibre-gl@5.6.0 pmtiles@3.2.0

FROM python:3.13-alpine
WORKDIR /app
COPY server.py cell_towers.py wigle_sync.py /app/
COPY qrcode /app/qrcode
COPY public /app/public
COPY samples /app/samples
COPY oui_fallback.csv /app/oui_fallback.csv
# Vendor the browser map libraries at image-build time. Runtime map use is local.
COPY --from=webdeps /deps/node_modules/maplibre-gl/dist/maplibre-gl.js /app/public/vendor/maplibre-gl.js
COPY --from=webdeps /deps/node_modules/maplibre-gl/dist/maplibre-gl.css /app/public/vendor/maplibre-gl.css
COPY --from=webdeps /deps/node_modules/pmtiles/dist/pmtiles.js /app/public/vendor/pmtiles.js
RUN mkdir -p /data /maps /app/public/vendor \
 && addgroup -S wardriver \
 && adduser -S -G wardriver wardriver \
 && chown -R wardriver:wardriver /data /app
USER wardriver
EXPOSE 8787
CMD ["python", "/app/server.py"]
