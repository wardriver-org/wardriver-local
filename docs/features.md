# Features and interpretation

## Ask Wardriver

Ask uses a local query planner, whitelisted SQL templates, bound parameters, and
SQLite read-only mode. No model is required. Example questions:

- How many unique networks did I find this month?
- Which manufacturers are most common?
- Show networks first discovered on my last drive.
- Which drives found the most new devices?

Queries have a four-second deadline. Table results are capped at 250 rows and
temporary map results at 5,000 devices.

## Flock review

Automatic Flock labeling requires explicit Flock Safety identity evidence,
corroborating prefix or learned fingerprint evidence, sightings across at least
two drives and two dates, a compact observation footprint, and a high heuristic
score. Generic names, vendor prefixes, and stationary behavior alone are
insufficient. Mobile or locally administered MAC signals exclude automatic matches.

Manual Confirmed / Not Flock decisions override automatic results. Scores are
heuristic points, not probabilities. Automated labels remain inferred. Observation
GPS positions locate the collector, not necessarily the camera. Weak candidates
remain available for review without counting as identified Flock devices.

Photos are stored in the local data volume. The first claimed photo for an
eligible, non-rejected camera contributes 25 XP. Photo eligibility is not proof
of camera identity.

Public-source verification is manual and cached locally. It checks nearby
OpenStreetMap/DeFlock records and regional EFF Atlas evidence. Regional deployments
or nearby records do not establish the identity of a particular observed radio.

## Cell towers

Enable the layer on Map to display cached sites. **Fetch towers in view** sends
the visible bounds to an Overpass service after confirmation. Panning reads the
cache rather than triggering downloads. The view must be no more than 0.5 degrees
on each side. Cached sites persist in `/data/cell-towers.db` independently of survey
observations and exports. OSM is incomplete and does not predict signal coverage.

## Collection methods

Imports can be marked as driving, walking, bike, bus, stationary, transit, or other.
Auto-detection uses movement where suitable GPS timestamps exist. Requested and
inferred methods remain separate. Use Transit for rail trips.

## Awards

Neighborhood awards require 500 unique devices per configured zone and award
100 XP each. Major-city packs activate from uploaded observations; see [Configuration](configuration.md).
Other progression includes drive counts, unique devices, coverage, distance,
streaks, collection methods, eligible Flock observations, and walking milestones.

Awards and XP are derived from the current dataset and may decrease after source
replacement, classification changes, or neighborhood configuration changes. The
underlying observations are not deleted by an award recalculation.
