# Own-data WiGLE sync — v2.21.0

## Use

1. Rebuild using `docker compose up -d --build --force-recreate wardriver nginx` and reload the dashboard.
2. Open Settings → Uploads & privacy → WiGLE.net sync.
3. Save and enable your own WiGLE account's “Encoded for use” API token. Test the connection.
4. Under **Sync only my data**, click **Sync my data now**. By default this downloads missing logs from your account's upload history.
5. To send data too, select a local import and check **Also upload the selected local import; I confirm it contains only my own observations**. Then sync. This checks remote history first and uploads only unmatched observations from that selected source.

There is no schedule or automatic bulk upload. No public network search or third-party observation overlay is used. Credentials remain in the existing local WiGLE settings. Local file ownership cannot be determined from CSV contents: the outbound checkbox is your explicit confirmation. Existing standalone upload remains a separate manual action; own-data deduplication applies to the new sync action.

## Data behavior

- Reads only authenticated `/api/v2/profile/user`, `/api/v2/file/transactions`, and `/api/v2/file/csv/{transid}`; the transaction IDs come exclusively from that account's returned history. Upload uses the existing `/api/v2/file/upload` adapter.
- Own-sync requires `https://api.wigle.net`; inbound redirects are refused to avoid forwarding credentials.
- Account + transaction ID records prevent re-downloading already imported logs. The source format is `wigle-own:<account-hash>:<transaction>.csv`.
- Observation matching uses MAC, timestamp to the second and coordinates rounded to seven decimal places. This skips exact normalized capture matches; it does not merge nearby or differently timestamped sightings. Pre-existing duplicate local rows are not deleted.
- Imports are additive and transactional. Existing records, local metadata and Flock reviews are preserved. New imported rows use an unknown collection method until you assign one.
- Sync-origin logs cannot be uploaded through either outbound path. For selected local sources, hashes of downloaded/accepted remote observations prevent upload loops. Pending upload signatures are journaled before sending; ambiguous failures are not automatically resent.
- No deletes propagate. Deleting a previously synced local log does not cause its transaction to be automatically downloaded again. Restore from your backup if needed.
- Background status survives browser reloads while the server remains running. Completed per-file progress and outbound attempt records persist in SQLite. After container restart, run sync again to resume; the in-memory status starts at idle.
- API quota/network failures stop the job with a visible error. After the quota resets or the issue is fixed, rerun. Completed files are skipped. An unresolved upload remains blocked unless a later history download proves those observations exist remotely; do not blindly repeat it with the old standalone upload button.
- Download size is capped at 512 MB per CSV. Unsupported radio identifiers, invalid coordinates/timestamps, empty/pending files and unexpected API response shapes stop that file without marking it complete. This release targets compatible MAC-based survey logs; it is not a full cellular-data restore client.

## Verification limits

Sixteen backend tests pass, including own-history-only downloads, resumable imports, duplicates against existing local captures, rollback of invalid downloaded rows, ownership checks, blocking outbound downloaded logs, and uploading only missing observations. JavaScript syntax passes. WiGLE calls in these tests use fixtures; live credentials, API quotas, full-history completeness and Docker runtime have not been verified here. The public Swagger page was inaccessible during development; unexpected response formats deliberately fail closed.

Deduplication can initially take time while scanning your existing observations. Current outbound CSV conversion retains the existing adapter's format and placeholder altitude/accuracy/RSSI behavior; this is not a byte-for-byte archive mirror.


---

# wardriver.org remote API scaffold

This file documents the optional remote-sync adapter included in Wardriver Local v2.4. Local mapping does not depend on this API.

## Feature behavior

- Disabled by default.
- No automatic/background upload.
- The user explicitly selects one imported source and presses **Upload selected import**.
- The API token is stored only in the local SQLite database unless deployment tooling overrides the adapter later.
- Upload history is recorded in `sync_runs`.

## Connection test

`GET {base_url}/api/v1/health`

Authorization, when configured:

```text
Authorization: Bearer <token>
```

Any HTTP 2xx response is treated as healthy.

## Import upload

`POST {base_url}/api/v1/imports`

Content type: `application/json`

Example payload:

```json
{
  "schema": "wardriver.observations.v1",
  "source": "wardrive_2026-08-09.log",
  "client": {"name": "wardriver-local", "version": "2.4"},
  "transfer": {
    "id": "uuid-for-entire-transfer",
    "chunk": 1,
    "chunks": 4,
    "final": false
  },
  "observations": [
    {
      "id": "local-observation-uuid",
      "kind": "wifi",
      "name": "Example SSID",
      "bssid": "00:11:22:33:44:55",
      "security": "WPA2",
      "channel": "6",
      "latitude": 10.0522,
      "longitude": -10.2437,
      "seen_at": "2026-08-09T18:22:42Z",
      "source": "wardrive_2026-08-09.log",
      "created_at": "2026-08-09T18:23:01Z"
    }
  ]
}
```

The adapter accepts any 2xx response. If the response body contains `id` or `import_id`, Wardriver Local stores it as `remote_id`.

## Environment overrides

```text
WARDIVER_SYNC_BASE_URL=https://wardriver.org
WARDIVER_SYNC_HEALTH_PATH=/api/v1/health
WARDIVER_SYNC_IMPORT_PATH=/api/v1/imports
WARDIVER_SYNC_TIMEOUT=20
WARDIVER_SYNC_CHUNK_SIZE=1000
```

These settings make it possible to adapt the local build to the completed API without redesigning the dashboard.
