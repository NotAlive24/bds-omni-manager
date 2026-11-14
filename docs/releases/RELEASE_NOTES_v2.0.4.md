# BDS Omni Manager 2.0.4

## Safe BDS server updates

Version 2.0.4 adds a dedicated **Server Update** page for installing an official Linux Bedrock Dedicated Server ZIP without manually rebuilding the server folder or re-entering configuration.

### Update workflow

1. Select the official Linux BDS `.zip` distribution and inspect it.
2. The manager validates the archive layout, required `bedrock_server` and `server.properties` entries, safe paths/file types, extraction limits, and CRCs. The current server is not stopped during upload or preflight.
3. Review the package summary and preserved-data list, then explicitly confirm installation.
4. The manager stages the new distribution beside the current server folder and creates a full CRC-verified backup. If staging or backup fails, the current server is left in place and is not stopped.
5. After backup succeeds, the manager stops managed BDS and the linked Bedrock Node Tunnel service when the configured lifecycle option requires it.
6. Existing settings, worlds, and user-managed content are migrated into the staged release. The old complete server folder is retained for rollback.
7. The new installation is checked on disk. If a check fails, the old installation is automatically restored. On success, the updated server remains stopped so the operator can review it before starting.

### Preserved data

- `server.properties`, including custom property values and `transport=nethernet` if configured, plus a custom `server-icon.png`.
- `allowlist.json`, `permissions.json`, `packetlimitconfig.json`, and `profanity_filter.wlist`.
- The complete `worlds/` tree, including LevelDB data, world metadata, local behavior/resource packs, and their stack-reference JSON files.
- User templates from `world_templates/` (new release templates win when names collide), `keys/`, `config/`, and development behavior/resource/skin pack folders.
- Custom shared behavior/resource packs and regular sidecar files in those directories. If a pack UUID is bundled in the new BDS release, the new distribution's copy wins; otherwise the existing custom pack is retained.
- BDS Omni Manager settings are stored outside the BDS root and are not changed.

### Safety and limitations

- The importer accepts Linux BDS ZIP distributions with `bedrock_server` and `server.properties` at the ZIP root or inside one enclosing folder. It does not install Windows `.exe` packages.
- It never runs the uploaded executable during preview or installation.
- Archives with traversal paths, duplicate paths, symlinks/special files, encryption, unsupported compression, oversized content, missing required files, or CRC failures are rejected.
- A full backup is required before BDS is stopped. A failed backup aborts the update.
- The previous complete installation is retained in a sibling `.bds-omni-rollback-*` folder; the two newest rollback folders are kept. Keep enough disk space for the staged release, backup, and rollback copy.
- The manager does not automatically start the updated BDS. Review the release and start it manually from the dashboard.
- Runtime compatibility of third-party add-ons with a new BDS release remains the operator's responsibility.

### Verification

The release adds regression tests for archive validation, safe staging, protected settings/world data, custom pack merge behavior, backup-before-stop order, external process protection, upload cleanup, symlink rejection, and atomic rollback, plus UI coverage for the new page and responsive OLED styles.
