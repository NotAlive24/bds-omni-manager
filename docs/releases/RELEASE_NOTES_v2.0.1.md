# BDS Omni Manager 2.0.1 — Player Tracking Reliability

This maintenance release fixes stale online-player counts, hardens related server-session state handling, and expands safe Bedrock add-on imports.

## Fixed

- Bedrock Dedicated Server disconnect lines can include `xuid` and `pfid`. The previous parser treated those fields as part of the player name, so the disconnect event did not remove the join entry. The parser now separates the name from trailing identity metadata.
- Disconnect events can remove a player by XUID if the visible name differs from the original connection event. Name-based removal remains available when XUID is blank or missing.
- Online-player state is protected by a dedicated lock. The status API returns one detached snapshot so the displayed player list and total count cannot be calculated from different dictionary states.
- Player events are scoped to a managed BDS process generation. Delayed log output from an older process cannot change a later session.
- A stopped or crashed BDS process clears the in-memory player list. The status endpoint and Stop action also reconcile a process that has exited before the watcher thread runs.
- Historical log lines remain visible in the log panel but are no longer replayed into the live online-player list at manager startup.
- Browser state refreshes discard older responses that arrive after a newer request, preventing stale dashboard data from replacing a newer count.
- Corrected scheduled-task checkbox styles that were being overridden by generic text-field rules; success-state status badges now have explicit OLED styling.
- Expanded ZIP import to support multiple independent unpacked pack folders with their own `manifest.json`, in addition to `.mcpack` files and `.mcaddon` bundles containing nested `.mcpack` files. The importer validates and commits these as one bundle.
- Added clear compatibility errors for skin packs and world templates, which are not server behavior/resource packs. Java `.jar` mods and encrypted Marketplace packages are not installable by BDS.

## Verification

The release adds targeted regression tests for Bedrock join/leave log formats, XUID/PFID handling, empty XUIDs, player-session generations, process exit, historical-log replay, detached state snapshots, browser request ordering, `.mcpack` and `.mcaddon` imports, multi-folder ZIP pack imports, atomic bundle validation and entry-count limits, and unsupported skin/world-template handling. Run the full suite with:

```bash
python -m compileall -q main.py tests
python -m unittest discover -s tests -v
```

The automated suite uses simulated processes and representative BDS log lines. A final live-server check is still required to confirm behavior with the exact BDS build and runtime environment.

## Verification completed

- `python -m compileall -q main.py tests` — passed.
- `python -m unittest discover -s tests -v` — **232 tests passed** in the build environment.
- `bash -n install.sh run.sh` — passed.
- Node.js inline-script syntax check — passed.
- Chromium UI load — no uncaught page errors. A delayed-response scenario confirmed that a late older `/api/state` response cannot replace the newer server state or player count.
- CSS parser — 785 rules, zero parse errors; all 338 literal class names in the HTML have a matching CSS class selector.
- Computed-style check — all eight scheduled-task checkboxes are 15 × 15 px with pointer styling; the two tunnel switches retain their distinct switch sizing.

A live BDS session was not available in this build environment. Test the release against your installed BDS executable before treating the player count as live-server verified. The test suite validates representative BDS log records and simulated process lifecycles.
