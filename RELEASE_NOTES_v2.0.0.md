# BDS Omni Manager 2.0.0 — Content Intelligence

BDS Omni Manager 2.0.0 adds world-aware pack intelligence and safer workflows for Bedrock behavior/resource content.

## Highlights

- World-aware behavior/resource pack dependency graph with UUID edges, missing/ambiguous dependency findings and dependency-cycle detection.
- Manifest validation and minimum-engine-version advisories.
- Bounded cross-pack asset-overlap scanning with SHA-256 for eligible files.
- World pack-stack editor with exact-permutation validation, stopped-server enforcement, verified recovery archives and rollback-safe writes.
- Portable named stack profiles with cross-world preview and dependency validation before applying.
- Verified ZIP backup comparison reporting added, removed, changed and unchanged paths without extracting the compared archives.
- Preserves the dedicated BedrockNodeTunnel integration, Mods & Add-ons manager, world library, scheduler/monitoring, Command Studio and AMOLED-black interface.

## Important boundaries

- Content Intelligence is static manifest/stack/file analysis, not a runtime profiler or guarantee of pack compatibility.
- Asset path overlaps are advisory and do not prove a runtime conflict.
- It does not execute pack scripts or modify LevelDB/binary `level.dat` data.
- World and pack mutations require BDS to be stopped and create verified recovery data as described in the README.
- The HTTP UI is local-only and should not be exposed to a network.

## Install

On Linux, extract the source archive and run:

```bash
./install.sh
```

Requirements: Python 3.10 or newer, a modern browser, and a Linux BDS installation for server-management features. See `README.md` for setup, process-control boundaries, backup behavior and limitations.
