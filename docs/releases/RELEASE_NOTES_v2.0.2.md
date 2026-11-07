# BDS Omni Manager 2.0.2 — Bedrock Manifest Comment Compatibility

## Fixed

Some Bedrock add-on archives contain JSON-style comments in `manifest.json`. The supplied `Java Saturation V1.6(NEW).mcaddon` includes two pack manifests, each ending with a standalone `//` line. The previous importer passed these files to Python's strict JSON parser and rejected the bundle with `Extra data` despite the JSON object itself being complete.

The manifest reader now strips `//` and `/* ... */` comments only when they appear outside JSON strings. The remaining text is still parsed by Python's standard JSON decoder, so malformed JSON, trailing commas, extra values and unterminated block comments are not silently accepted. Imported manifests that contained comments are rewritten as strict JSON in staging before they are published to the BDS pack folders. Existing installed-pack inventory checks use the same reader.

## Regression coverage

- Two-pack `.mcaddon` import with comments in both behavior and resource manifests.
- Line comments and block comments.
- `//` and `/* ... */` text inside JSON string values.
- Installed inventory reading of pre-existing commented manifests.
- Rejection of non-comment trailing data and unterminated block comments.
- Full existing player-tracking, pack import, UI, and application test suite.

## Compatibility note

This release fixes the manager's manifest parser and does not guarantee that every third-party add-on runs on every BDS build. Script API module dependencies, minimum engine versions, pack content, and client UI behavior remain specific to each add-on and Minecraft version. The importer installs packs but does not automatically activate them in a world.
