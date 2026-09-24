# wardriver.org Upload Privacy

Wardriver Local v2.17.0 adds a dedicated outbound privacy layer for **wardriver.org uploads only**.

The local database is always the source of truth. Changing a privacy setting does **not** rewrite local SSIDs, BSSIDs, coordinates, timestamps, routes, hardware profiles, Flock reviews, or photos. The transformation happens immediately before the wardriver.org HTTP request is built.

WiGLE upload behavior is separate and is not affected by these settings.

## Profiles

### Balanced — default

- SSID: private stable hash
- BSSID: private stable pseudonym scoped to this local privacy profile/database
- Location: deterministic ~50 m grid
- Time: date only
- Minimum observation age: 24 hours
- Movement/route telemetry: stripped
- Source/session/local gear metadata: stripped
- Beacon fingerprints / information elements: stripped
- Wi-Fi: enabled
- Bluetooth: disabled by default
- ALPR/Flock: enabled
- Public attribution: anonymous
- Photo upload: disabled

### Privacy first

- SSID removed
- BSSID reduced to vendor prefix only
- Location reduced to ~1 km
- Timestamp removed
- Minimum observation age: 7 days
- Bluetooth and ALPR/Flock excluded
- Hardware inference excluded
- Anonymous attribution
- Strong privacy shield

### Full fidelity

Exact identifiers, coordinates, timestamps, and local source metadata can be sent when explicitly selected. This profile is deliberately labeled **Full fidelity** rather than "private."

### Custom

Every individual control can be changed. Editing a preset control changes the UI profile to Custom.

## Identifier transforms

SSID hashes and BSSID pseudonyms are deterministic for the local privacy database. A random secret is generated once and stored in local SQLite. The secret never appears in preview output and is never included in the remote payload.

Because the pseudonym secret is local, two different Wardriver Local installations do not automatically produce the same public device identifier. This reduces cross-user correlation.

## Location privacy

Coordinate reduction uses deterministic grid snapping rather than new random noise on every upload. Repeated uploads therefore do not expose the exact coordinate by averaging multiple noisy samples.

Supported settings:

- exact
- ~25 m
- ~50 m
- ~100 m
- ~250 m
- neighborhood / ~1 km

## Private upload zones

Private zones are evaluated against the exact local coordinate **before** any location transformation. Matching observations are removed from the outbound stream completely.

v2.17 supports:

- circles centered on the current map center
- polygon zones created from the current map viewport

Zone coordinates remain local and are not included in wardriver.org request metadata. The remote payload receives only a boolean that private zones were applied and the number of configured zones.

## Time privacy and delay

Timestamp settings are:

- exact
- hour
- date only
- removed

A separate minimum-observation-age control can withhold observations for 24 hours, 7 days, or 30 days. The local client enforces the delay before sending anything, so this protection does not depend on a future wardriver.org publication queue.

If a delay is enabled and an observation has no valid timestamp, Wardriver excludes it rather than risk sending a possibly recent observation.

## Metadata stripping

The default Balanced profile strips:

- local source filenames
- session IDs and names
- local hardware-profile identifiers/names
- import timestamps
- inferred collection metadata used only by the local application
- speed, heading, bearing, accuracy, velocity, route IDs, and similar movement fields if present
- radio fingerprints, information elements, capabilities, and beacon intervals

Collection method itself may remain because it describes how the observation was collected rather than the route taken.

## Categories

wardriver.org sharing can independently allow or exclude:

- Wi-Fi
- Bluetooth
- ALPR / Flock candidates
- inferred hardware fields
- future photo uploads

ALPR/Flock exclusion uses both the observation kind and Wardriver's Flock identity classifier.

## Contributor identity

Outbound metadata supports:

- anonymous contribution
- public alias
- wardriver.org username/account attribution

When username attribution is selected, the remote service is expected to derive the account from the API token. The local client does not need to send a username string in every observation.

## Photo policy

Flock photos remain local-only in v2.17.0. The Settings UI already stores the policy that any future photo endpoint must honor:

- photo uploads allowed / disallowed
- strip EXIF/GPS/device metadata
- blur faces
- blur license plates
- maximum image dimension

This prevents a later photo-sync feature from silently defaulting to unsanitized originals.

## Preview before upload

The Settings page can inspect a selected import before upload. Preview shows:

- local observation count
- outbound observation count
- number excluded locally
- privacy shield rating
- exclusion reasons
- pseudonymized remote import label
- a real local-vs-outbound sample transformation

The Upload button builds this preview before asking for final confirmation.

## Remote/server privacy target

The local client sends only the already-transformed payload. wardriver.org itself should additionally:

- avoid retaining a pre-sanitized version because none is required
- never log API tokens, request bodies, or BSSIDs in normal HTTP logs
- minimize IP-address retention
- keep contributor data separate from public observation attribution
- support deleting a contributor's remote imports
- document retention and abuse controls

Those are server-side policies and are separate from Wardriver Local's outbound transformation engine.
