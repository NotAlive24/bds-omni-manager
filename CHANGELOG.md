## 2.0.2 — Bedrock Manifest Comment Compatibility

- Fixed `.mcaddon` and `.mcpack` imports for Bedrock manifests containing `//` line comments or `/* ... */` block comments outside JSON strings. The reported Java Saturation V1.6 archive contains a trailing `//` line in both its behavior- and resource-pack manifests, which caused Python's strict JSON parser to reject the bundle as extra data.
- Added a quote- and escape-aware manifest comment scanner. JSON strings containing `//` or `/* ... */` are preserved; comments are removed without shifting line breaks; all remaining content is still validated by the standard JSON parser. Trailing commas, extra JSON values, malformed content and unterminated block comments remain rejected.
- Normalize only manifests that contained comments into strict JSON inside the staged import before installation, so BDS receives valid JSON. Other pack files and manifest values are preserved.
- Apply the same JSONC-aware manifest reading to the installed-pack inventory, so pre-existing commented manifests are not marked invalid by the manager.
- Added regression tests for the supplied add-on's two-pack structure, both supported comment forms, comment markers inside strings, inventory inspection, strict invalid-tail rejection, and unterminated block-comment rejection.

## 2.0.1 — Player Tracking Reliability

- Expanded the Mods & Add-ons importer to select and queue multiple `.mcpack`, `.mcaddon`, and supported `.zip` archives in one operation. Each archive is uploaded sequentially and installed using the existing validation and transaction safeguards; failed files remain selected for retry, avoiding duplicate uploads of successful files.
- Clarified format boundaries: BDS-compatible behavior/resource/script packs are supported; Java `.jar` mods and client-only skin/world-template content are not server-installable, while `.mcworld` imports belong in World Management.
- Fixed online-player counts remaining stale when Bedrock disconnect log lines include both `xuid` and `pfid`; player names are now parsed separately from trailing identity metadata.
- Added XUID-based fallback removal for disconnect events whose visible name differs from the original join event, while preserving name-based cleanup for empty/offline XUIDs.
- Made player-state changes thread-safe and status responses use a detached, internally consistent player list/count snapshot.
- Scoped join/leave events to a managed BDS process generation so delayed output from an old process cannot modify a newer session.
- Cleared online-player state as soon as a managed BDS process exits, including crashes and exits first observed by the status endpoint or Stop action.
- Prevented historical log replay from rebuilding a false online-player list after manager restart.
- Protected the dashboard from out-of-order `/api/state` responses overwriting newer player counts; added defensive handling when player data is absent.
- Completed explicit AMOLED styling for success-state badges and scheduled weekday/enable checkboxes, including pointer sizing and keyboard-focus outlines.
- Expanded the Mods & Add-ons importer to select and queue multiple `.mcpack`, `.mcaddon`, and supported `.zip` archives in one operation. Each archive is uploaded sequentially and installed using the existing validation and transaction safeguards; failed files remain selected for retry, avoiding duplicate uploads of successful files.
- Clarified format boundaries: BDS-compatible behavior/resource/script packs are supported; Java `.jar` mods and client-only skin/world-template content are not server-installable, while `.mcworld` imports belong in World Management.
- Added an explicit keyboard-focus outline for the multiple-archive file picker.
- Added regression tests for XUID/PFID disconnects, blank XUID/PFID-only events, XUID fallback, historical log replay, process-exit cleanup, process-generation races, detached snapshots, and browser refresh ordering.

## 2.0.0 — Content Intelligence

- Added a world-aware behavior/resource pack dependency graph with UUID edges, missing/ambiguous dependency findings, cycle detection, malformed-manifest reporting, and minimum-engine-version advisories. Dependency resolution respects shared server packs and only the selected world's local pack folders.
- Added a bounded cross-pack asset-overlap scan that hashes eligible regular files and reports identical assets or same-relative-path files with different content. The scan is advisory, caps work at 20,000 files/350 MiB, and skips hashing individual files over 16 MiB; it never executes scripts or reads LevelDB.
- Added a world pack-stack studio for behavior/resource order with exact-permutation validation, stopped-BDS enforcement, verified recovery archives, and rollback-safe writes.
- Added persistent named stack profiles with portable preview/apply against another world's visible pack inventory, dependency closure/cycle checks, duplicate UUID ambiguity handling, and verified pre-change archives.
- Added verified backup snapshot comparison for two ZIP backups, reporting added/removed/changed/unchanged paths based on size and CRC32 without extracting either archive.
- Hardened recovery archive path checks against symlinked backup directories, symlinked stack-profile state directories, and destinations inside the BDS root.
- Added authenticated Content Intelligence HTTP routes and explicit AMOLED styles for the graph, findings, stack controls, profile tools, backup-diff disclosures, disabled/loading/error states, and responsive layouts.
- Added iterative dependency-cycle traversal to handle deep graphs without Python recursion limits; added regression coverage for a 1,500-node dependency chain.
- Preserved Mods & Add-ons, World Management, Monitoring & Tasks, Bedrock Node Tunnel service control, Command Studio, backup/restore protections, localhost-only API access, and the permanently AMOLED-black theme.

# Changelog

## 1.9.0 — Mods & Add-ons

- Added a dedicated Mods & Add-ons workspace for Bedrock behavior packs, resource packs, and Script API modules. Java `.jar`/Forge/Fabric mods are explicitly rejected as incompatible with BDS.
- Added a normalized inventory for shared and world-specific packs, with search, type/health filters, summary metrics, manifest/module/dependency detail, author/license/capability metadata, minimum engine version, script-module classification, validation state, and active-world references.
- Added import for `.mcpack`, supported single-pack ZIP archives, and multi-pack `.mcaddon` bundles. Every nested pack is validated before any install commit; UUID conflicts, malformed manifests, invalid dependencies, duplicate destinations, path attacks, CRC faults, and size/entry limits abort the entire bundle.
- Added all-or-nothing `.mcaddon` install commits using staged extraction and atomic folder moves with rollback if a later commit fails. Imported packs remain installed but are not automatically activated.
- Added world-specific install selection alongside server-shared behavior/resource pack locations. Existing world folders are not overwritten.
- Added explicit rejection of `.mcaddon` archives containing `.mcworld` components so bundled worlds are not silently omitted; those worlds must be imported through Worlds & Packs separately.
- Added a combined 32 GiB expanded-content limit across a complete bundle, an 8 GiB upload cap, and a 150,000-entry archive cap. Archive safety checks reject path traversal, absolute/drive paths, case-insensitive duplicates, symlinks/special files, encryption, unsupported compression and CRC errors.
- Added a Bedrock-aware Mods page that distinguishes behavior, resource and script-enabled packs and explains that Java Edition `.jar` mods do not run on BDS.
- Hardened per-world activation and removal against duplicate UUID ambiguity. Duplicate UUID copies cannot be toggled or removed by an ambiguous location; invalid packs cannot be newly enabled.
- Reworked pack removal to archive and verify first, validate all affected world-reference JSON, atomically quarantine the pack folder, update references with rollback, and retain/report the verified archive and quarantine if final cleanup fails.
- Kept content mutations disabled until server state is loaded and confirmed stopped; world and pack changes remain blocked while a managed or detected external BDS process is running.
- Added explicit responsive AMOLED styling for the Mods page and tested filters, details, upload, activation confirmation, control backgrounds, mobile overflow and browser errors in Chromium.
- Added regression coverage for single and bundled imports, atomic rollback, cumulative bundle expansion limits, `.mcworld` rejection, `.jar` rejection, scope-aware installs, script modules, dependencies, duplicate UUIDs, safe removal and authenticated HTTP uploads.
- Preserved 1.8.0 world management, 1.7.0 Monitoring & Tasks, 1.6.0 Bedrock Node Tunnel integration, repeated UDP mapping support, 1.5.0 Command Studio, backup/restore protections, localhost-only API and AMOLED-only styling.

## 1.8.0 — World & Content Management

- Added a world library with active-world state, display names, sizes, file counts, pack-reference counts, modification time, symlink detection, and integrity notes.
- Added `.mcworld`/ZIP world import with archive path/type/count/size validation, staged extraction, CRC checks, LevelDB directory preflight, collision protection, and atomic install.
- Added `.mcworld` export with root-level entries, CRC verification, collision-resistant output names, file-count and expanded-size bounds.
- Added empty-world selection, world duplication, rename with active `level-name` rollback, active-world switching, and archive-first deletion with hidden quarantine and verified-archive recovery after partial cleanup failure.
- Added behavior/resource pack import from single `.mcpack` or ZIP archives, with shared server-level and world-specific installation scopes.
- Added manifest inspection for format version, header/module UUIDs and versions, module types, minimum engine version, and UUID/module-name dependencies. Preview-only manifest v3 packs are inspectable but cannot be activated in the stable workflow.
- Added shared/world-scope dependency resolution, duplicate UUID detection, missing-dependency errors, and invalid-manifest visibility.
- Added per-world behavior/resource pack stack controls that update `world_behavior_packs.json` and `world_resource_packs.json`, preserve existing metadata, create pre-edit backups, auto-enable valid UUID pack dependencies, and allow an already-active invalid pack to be disabled while preventing it from being newly enabled.
- Added individual `.mcpack` export and archive-first pack removal with world-reference cleanup. Removal is blocked for duplicate UUID ambiguity and active dependent packs.
- Hardened archive import/export: reject traversal/absolute paths, case-insensitive duplicates, symlinks/special files, encrypted entries and unsupported compression; cap uploads at 8 GiB, expanded data at 32 GiB, and archive entries at 150,000; stage imports and verify CRC before publishing; reject export destinations inside the BDS server directory.
- Added explicit invalid/duplicate pack status in the AMOLED UI; invalid activation toggles are disabled, while active invalid references remain switchable off. Added world/pack metrics, detail panels, file-import progress, responsive mobile layouts, and styles for every content-management control.
- Fixed `/api/worlds/detail` HTTP query parsing after removing a local `parse_qs` import that shadowed the module import.
- Added regression tests for world/pack import/export, archive security checks, manifest validation, scope-aware dependencies, duplicate UUIDs, rollback/recovery paths, authenticated HTTP routes, download tickets, and UI styling.
- Preserved 1.7.0 Monitoring & Tasks, 1.6.0 Bedrock Node Tunnel integration and repeated UDP mapping support, 1.5.0 Command Studio, 1.3.1 browser disconnect handling, backup/restore protections, path draft preservation, localhost-only access, and AMOLED-only styling.

## 1.7.0 — Monitoring & Tasks

- Added a dedicated Monitoring & Tasks workspace with host CPU, memory, disk, network, and manager-launched BDS process metrics.
- Added bounded five-second metric sampling and historical SVG charts; the UI polls only while the monitoring page is active and visible.
- Added advisory health checks for the BDS executable, `server.properties`, managed or externally detected BDS process state, server disk capacity, backup-folder writability, and recent error-like log entries. A stopped server is informational; an externally launched BDS process is identified by PID and marked unmanaged.
- Added local-time recurring tasks for world backups, full-server backups, and in-game announcements, with weekday selection, enable/pause, edit, delete, and a 50-entry execution history.
- Persisted schedules in a dedicated state file, separate from the main manager configuration. Added server-side validation, an allow-list of task types, safe Bedrock `say` command building, and authenticated HTTP endpoints. No arbitrary shell-task execution was introduced.
- Defined and implemented a missed-run policy: tasks run only within 150 seconds of their scheduled local time; missed jobs are skipped and not replayed after sleep or reboot. Invalid persisted job IDs are ignored, and worker-thread launch failures are recorded as failed runs while releasing the scheduler lock.
- Hardened shutdown ordering: scheduler and automatic-backup loops stop accepting new work; manager shutdown waits for running scheduled backups to finish their save-manifest copy and `save resume` cleanup before the managed BDS process is stopped.
- Added responsive AMOLED styling for every new metric, chart, health, schedule, form, toggle, status, empty-state, and history element.
- Added tests for Linux metric sources and calculations, process-metric unavailability, health state, schedule validation/persistence/updates/deletes, backup and announcement execution, failure history, authenticated APIs, due-time and weekday matching, missed-run skipping, and shutdown waiting.
- Preserved all 1.6.0 Bedrock Node Tunnel integration, repeated UDP mapping handling, 1.5.0 Command Studio, 1.3.1 HTTP disconnect handling, backup/restore safety, path-draft preservation, localhost-only binding, and AMOLED-only styling.

## 1.6.0 — Bedrock Node Tunnel integration

- Added a dedicated Bedrock Node Tunnel dashboard for the upstream `nethernet-client` Linux user service `bedrocknode-tunnel.service`.
- Added safe systemd user-service controls: start, stop, restart, enable at login, and disable at login. Commands are fixed allow-listed argument arrays; no shell is used.
- Detect the client binary and version, preferring the exact `nethernet-client` path found in the systemd `ExecStart` property and falling back to PATH.
- Added current systemd invocation ID to service status and scope connection-health journal queries to that invocation. Old logs can remain visible in the log viewer but can no longer make a newly restarted tunnel appear connected.
- Added explicit claim-required classification for first-run claim and invalid-token re-claim messages.
- Check the token file's presence and owner-only permission mode in the XDG config path and the systemd service working-directory fallback, without opening or returning token bytes. Empty/relative `XDG_CONFIG_HOME` now correctly falls back to `~/.config`.
- Added BDS NetherNet transport, local TCP target, UDP target, `server-port`, `server-udp-ports`, and TCP reachability diagnostics.
- Added the `server-ip` bind-address property with IPv4/IPv6 literal validation; an empty value remains empty to retain BDS's all-interface default.
- Added a dedicated multiline `server-udp-ports` editor and lossless parsing/merging for repeated mapping rows. Dashboard imports preserve repeated UDP rows while continuing to reject duplicate scalar properties. The editor and importer cap a single update at 32 entries and preserve the pre-edit properties backup.
- Fixed the tunnel state renderer's external-client count initialization so the panel cannot access the count before it is declared.
- Hardened the dashboard import confirmation against text changes while the confirmation dialog is open.
- Added a preview-and-confirm import for network-only lines copied from the Bedrock Node dashboard. The manager preserves comments and does not guess account-specific public ports or UDP mappings.
- Added optional tunnel start/stop linking for BDS lifecycle actions. Tunnel service failures are returned as warnings while the requested BDS stop/restart action still proceeds.
- Added explicit first-time setup guidance, upstream links, public-vs-local port explanation, and optional systemd linger note.
- Added 10 tests for invocation scoping, token fallback, invalid-token state, executable discovery, and lifecycle failure handling.
- Preserved the 1.5.0 Command Studio styling and preview safety, 1.3.1 HTTP disconnect fix, backup/restore protections, path draft protection, localhost-only binding, and AMOLED-only theme.

## 1.5.0 — Command Studio visual redesign

- Rebuilt the Command Studio layout: a dedicated operation picker and context panel now sit above a balanced argument grid instead of competing with each other in a cramped three-column row.
- Added explicit AMOLED styling for the operation selector and every generated input, number field, select, boolean selector, and textarea. Native grey browser controls are no longer left unstyled.
- Added consistent field cards, required markers, helper text, focus/hover/disabled states, responsive layouts, and a clearer empty state.
- Added operation category, description, cheat/permission hints, extra-confirmation indicators, and parameter counts.
- Redesigned command preview as a distinct review panel with readable monospace output and clear safety status.
- Added client-side required/range validity checks before requesting a server-side command preview.
- Invalidates previews when a form changes and binds each preview to the exact form and argument values, preventing stale commands from being sent.
- Handles edits and form changes while a preview request is in flight; outdated results are ignored and the Preview button is re-enabled.
- Added 4 UI regression tests for generated-control styling, accessible structure, preview invalidation, and application version.
- Preserved the 1.3.1 HTTP disconnect fix, 1.2.0 backup/restore protections, path draft protection, local-only API, and AMOLED-only theme.

## 1.4.0 — Complete BDS control foundation

- Added Command Studio with 27 guided operations: gamemode, difficulty, time, weather, gamerule, say, tell, title, teleport, give, effect, XP, summon, setblock, fill, clone, tags, kick, OP/de-OP, world/player spawn, mob events, clear inventory, kill, and playsound.
- Added authenticated API routes to return form definitions and create validated command previews. Preview never executes a command.
- Added server-side validation for targets/selectors, coordinate triplets, identifiers, messages, enum values, and numeric bounds.
- Added an explicit confirmation step for commands that can modify large world regions, remove entities/items, disconnect players, or change operator permissions.
- Added an in-app button to query the installed BDS `/help` output.
- Added property search across setting labels, keys, and descriptions.
- Corrected weather-duration UI wording to game ticks and XP command generation to Bedrock points syntax.
- Corrected `/clear` data/max-count argument ordering and added regression coverage.
- Added regression tests for command previews, input validation, destructive-operation confirmation, and UI integration.
- Preserved 1.3.1 HTTP disconnect handling, AMOLED-only styling, path drafts, backup/restore safeguards, and existing configuration locations.

## 1.3.1

- Handle expected browser disconnects (`BrokenPipeError`, connection reset/abort) without emitting repeated server tracebacks.
- Do not attempt a second HTTP response after a client has already closed its connection.
- Preserve normal error handling for genuine application and server-side failures.
- Add regression tests for disconnects during response headers and response-body writes.

## 1.3.0 — UI refinement and folder selection

- Reworked the visual system for a cleaner, quieter AMOLED interface: consistent spacing, calmer surfaces, clearer typography, cleaner cards, and responsive layouts.
- Tightened sidebar navigation so the Settings destination remains visible and pinned above the local-only/application footer, including shorter laptop-height layouts.
- Added a native Linux folder chooser for server and backup paths when `zenity`, `kdialog`, or `yad` is installed. Text fields still support normal typing and paste; the chooser only fills the field and never saves automatically.
- Added explicit Reset controls for unsaved path drafts.
- Hardened configuration draft state: periodic status polling cannot overwrite a field after the user starts editing it, even after focus moves elsewhere. Failed saves keep the draft intact.
- Made status polling visibility-aware and non-overlapping to avoid unnecessary background requests and update races.
- Added visible focus states and reduced-motion support.
- Added tests for folder chooser selection/cancel/fallback and path draft preservation.

## 1.2.0

- Improved version detection and cache invalidation.
- Added archive CRC verification and staged restore with rollback.
- Added process cleanup and additional integration tests.
