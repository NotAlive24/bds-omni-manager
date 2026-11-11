<div align="center">
  <img src="assets/icon.svg" alt="BDS Omni Manager icon" width="88" />
  <h1>BDS Omni Manager</h1>
  <p><strong>A local-first control panel for Minecraft Bedrock Dedicated Server.</strong></p>
  <p>Manage your server, worlds, add-ons, backups and Bedrock Node Tunnel from one focused interface.</p>
  <p>
    <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white" alt="Python 3.11 or newer" /></a>
    <img src="https://img.shields.io/badge/platform-Linux-111111?logo=linux&logoColor=white" alt="Linux" />
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-46e59a" alt="MIT License" /></a>
    <img src="https://img.shields.io/badge/release-2.0.3-46e59a" alt="Version 2.0.3" />
  </p>
</div>

> **Local access only.** The manager binds its web interface to `127.0.0.1`. It is not designed to be exposed to a LAN, reverse proxy, public tunnel or the internet. Bedrock Node Tunnel is for game traffic; it does not make the manager's administration interface remotely accessible.

## Overview

BDS Omni Manager is a Linux application with a browser-based interface and a strict AMOLED-black design. It uses Python's standard library and does not require a separate Python package installation.

The application focuses on day-to-day administration of a Linux **Bedrock Dedicated Server (BDS)** installation: starting and stopping the server, editing `server.properties`, using the console, managing worlds and Bedrock add-ons, creating verified backups, monitoring the host, and optionally controlling the upstream BedrockNodeTunnel systemd user service.

## Preview

<p align="center">
  <img src="docs/screenshots/content-intelligence-desktop.png" alt="BDS Omni Manager Content Intelligence workspace on desktop" width="900" />
</p>

<details>
  <summary>View the mobile layout</summary>
  <p align="center">
    <img src="docs/screenshots/content-intelligence-mobile.png" alt="BDS Omni Manager Content Intelligence workspace on a mobile-sized screen" width="520" />
  </p>
</details>

<p align="center">
  <a href="#features">Features</a> ·
  <a href="#installation">Installation</a> ·
  <a href="#first-time-setup">First-time setup</a> ·
  <a href="#backup-and-restore-safety">Backups</a> ·
  <a href="#security-and-data-locations">Security</a> ·
  <a href="#development-and-tests">Development</a>
</p>

## Features

| Workspace | What it provides |
|---|---|
| **Dashboard & server control** | Start, gracefully stop and restart the manager-owned BDS process; inspect process state, uptime, startup version information and recent console output. |
| **Properties** | Search and edit 55 documented `server.properties` fields, retain unknown/newer keys, preserve comments and repeated NetherNet UDP mapping rows, and keep an original-file backup. |
| **Console & commands** | Send commands supported by the installed BDS build, browse the searchable command index, and use 27 guided forms with server-side validation, preview and confirmation where appropriate. |
| **World controls** | Manage ticking areas across dimensions, game mode, difficulty, time, weather, game rules, world/player spawn and save operations. |
| **Players & access** | View players observed from server output, use common player actions, and manage `allowlist.json` and `permissions.json`. |
| **Worlds & add-ons** | Create, duplicate, rename, switch, export and archive worlds; inspect, import, export, activate and remove Bedrock behavior/resource packs. |
| **Backups & restore** | Create world or full-server archives, configure automatic retention and schedules, verify ZIP integrity, compare snapshots and restore with staging/rollback safeguards. |
| **Monitoring & tasks** | View Linux host and manager-launched BDS metrics, advisory health checks, scheduled backups and in-game announcements with execution history. |
| **Bedrock Node Tunnel** | Inspect and control the upstream `bedrocknode-tunnel.service`, review connection health and networking diagnostics, import reviewed BDS network settings, and configure documented `nethernet-client` parameters. |
| **Content Intelligence** | Inspect world-aware pack dependencies, missing or ambiguous references, dependency cycles, engine-version advisories, asset overlaps, pack-stack profiles and backup comparisons. |

### Bedrock add-on formats

The Mods workspace manages **Bedrock add-ons**, not Java Edition mod loaders.

| Format | Handling |
|---|---|
| `.mcpack` | Import a behavior or resource pack. Multiple archive files can be queued together. |
| `.mcaddon` | Validate and import its supported nested packs as a bundle. |
| `.zip` | Import a supported pack archive or ZIP containing one or more valid pack folders. |
| `.mcworld` | Import through **Worlds**, not the Mods importer. |
| Java `.jar` / Forge / Fabric | Not compatible with Bedrock Dedicated Server and not installable here. |

Manifests are validated before installation. The importer accepts `//` and `/* ... */` comments outside JSON strings and normalizes manifests before installation. Preview-only manifest format 3 can be inspected but is not activated by the stable workflow. Client-only skin packs are not server behavior/resource packs and are not installable through this workspace. Pack dependency checks and Content Intelligence are based on files and manifest metadata; they do not prove that an add-on's scripts will run on every BDS version. Test new content on a copy of your world first.

### Bedrock Node Tunnel parameters

Bedrock Node Tunnel is an optional integration. Install and configure the upstream Linux client and its `bedrocknode-tunnel.service` separately; BDS Omni Manager does not install the client or manage your Bedrock Node account.

The tunnel page exposes the documented persistent client settings:

| Setting | Client flag | Default / behavior |
|---|---|---|
| Web dashboard URL | `-web` | `https://bedrocknode.net` |
| VPS control endpoint | `-control` | `51.222.142.179:7000` |
| Local TCP target | `-local-http` | `127.0.0.1:19132` |
| Local UDP IP | `-local-udp-ip` | Automatically detected when empty |
| Local UDP port | `-local-udp-port` | `47334` |
| Optional custom token | `-token` | Optional; stored separately with owner-only permissions |

Settings are validated and passed as structured arguments through a manager-owned systemd user drop-in. The upstream vendor unit is not edited. `-reset-token` is a separate, confirmed action rather than a persistent argument; `-version` is used for client discovery. Token contents are not returned to the browser or written to manager logs.

## Requirements

- Linux (the installer creates a Linux desktop entry).
- Python 3.11 or newer.
- A modern browser.
- A Linux BDS installation for server-management features.
- `systemd --user` and the upstream BedrockNodeTunnel user service for tunnel controls.

The manager itself uses only the Python standard library. Node.js is optional and is used by UI tests for JavaScript syntax validation; it is not required to run the application.

## Installation

Clone the repository and run the installer:

```bash
git clone https://github.com/NotAlive24/bds-omni-manager.git
cd bds-omni-manager
./install.sh
```

The installer copies the runtime files to `~/.local/share/bds-omni-manager/` and creates a desktop entry under `~/.local/share/applications/`. Launch **BDS Omni Manager** from your application menu. The terminal remains open while the manager is running.

To run directly from the checkout without installing:

```bash
./run.sh
```

The manager prints its local URL when it starts. By default it tries ports `8765` through `8794` on `127.0.0.1`; open the exact URL printed in the terminal.

## First-time setup

1. Launch BDS Omni Manager and open **Settings**.
2. Set the server folder to the directory containing both `bedrock_server` and `server.properties`.
3. Choose a backup destination **outside** the BDS server directory.
4. Save the settings, return to the Dashboard and review the detected server state.
5. Start BDS from the manager when you are ready to manage that process through the UI.

If the BDS executable is not marked executable, fix its permission in the server directory:

```bash
chmod +x "/path/to/bedrock-server/bedrock_server"
```

The manager launches the official `bedrock_server` binary from its directory and adds that directory to `LD_LIBRARY_PATH` so bundled libraries can be found.

## Process-control boundaries

The manager can send console commands to, read output from and shut down the BDS process **that it started itself**. It cannot attach to a server already managed by another terminal, `systemd` unit, `tmux`, Docker container or wrapper script. Where possible, it detects an external BDS process and prevents a second server from being started against the same world.

The manager controls BDS itself and the specific upstream BedrockNodeTunnel `systemd --user` service. It does not manage WaterdogPE, Playit, Endstone or other proxy/plugin services.

Closing the browser tab does not stop the manager. Pressing `Ctrl+C` in the manager terminal stops new scheduled work, waits for active backup cleanup, attempts a final verified world backup while its managed BDS process is running, then stops the linked tunnel (if configured), gracefully stops managed BDS and closes the local web server. If the final backup fails, the error is reported and shutdown continues. An externally launched BDS process is not stopped or backed up by this shutdown path.

## Backup and restore safety

A live LevelDB world must not be copied while BDS is writing to it. For a live world backup, the manager uses the BDS save workflow (`save hold`, `save query`, copy the paths and byte lengths from the ready manifest, then `save resume`). If BDS does not provide a ready manifest, the manager refuses to create a live snapshot rather than silently copying a potentially inconsistent database.

New ZIP/ZIP64 archives are checked for CRC integrity before publication. Restore is allowed only when no managed or detected external BDS process is running; the archive is verified and staged before files are applied, with rollback data retained during the operation. A world snapshot restores its archived world and related shared files. A full-server restore overlays paths represented in the archive and leaves unrelated files untouched. Make a current full backup before a restore when you need a recovery point for the current server state.

## Security and data locations

- **Local interface:** `127.0.0.1` only; do not expose the administration UI through a public tunnel or reverse proxy.
- **API protection:** mutation routes require a per-launch local API token.
- **Archive handling:** uploaded files and manifests are untrusted input and are validated before extraction or installation.
- **Pack operations:** changes use server-stopped checks and recovery/rollback safeguards where applicable.
- **Tunnel secrets:** custom client tokens are stored in a private EnvironmentFile with owner-only permissions; token values are not sent back to the UI.

| Data | Location |
|---|---|
| Manager configuration | `~/.config/bds-omni-manager/config.json` |
| Optional custom tunnel token | `~/.config/bds-omni-manager/nethernet-client.env` |
| Manager state and logs | `~/.local/state/bds-omni-manager/` |
| Default backup directory | `~/BDS-Backups/` |
| Installed application | `~/.local/share/bds-omni-manager/` |

The manager checks the upstream client's browser-claimed token file for presence and permissions without reading its contents. Its location follows the upstream client's XDG configuration path and service working-directory fallback.

## Development and tests

The project has no third-party Python runtime dependencies. From the repository root, run:

```bash
python -m compileall -q main.py tests
python -m unittest discover -s tests -v
bash -n install.sh run.sh
```

The 2.0.3 release was validated with **255 passing tests** covering core behavior, HTTP routes, archive and content handling, tunnel integration, monitoring/scheduling, shutdown behavior and UI regressions. The GitHub Actions workflow runs the test suite across its configured Python-version matrix.

## Project documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Changelog](CHANGELOG.md)
- [2.0.3 release notes](docs/releases/RELEASE_NOTES_v2.0.3.md)
- [Security policy](SECURITY.md)
- [Support](SUPPORT.md)
- [Contributing](CONTRIBUTING.md)
- [Accessibility](ACCESSIBILITY.md)
- [Notices and trademarks](NOTICE.md)

## Scope and limitations

BDS Omni Manager is a focused administration tool, not a remote-control service or a replacement for every BDS subsystem. It does not provide multiple simultaneous server profiles, BDS binary auto-updates, Marketplace downloads, direct LevelDB or binary `level.dat` editing, arbitrary execution of add-on scripts, or management of third-party proxy/plugin processes beyond the documented BedrockNodeTunnel user service.

The command console accepts commands supported by the installed BDS build, but only common operations have guided forms. Command availability and behavior remain subject to the installed server version, permissions, cheats and experiments. `server.properties` keys added by future BDS versions may need to be reviewed against Microsoft's documentation.

Content Intelligence is static file and manifest analysis, not a runtime profiler. It does not execute scripts, inspect LevelDB or guarantee that an imported add-on or world will load successfully. BDS remains authoritative when it loads worlds and pack stacks.

## License

MIT. See [LICENSE](LICENSE).

## Upstream projects

- [BedrockNodeTunnel](https://github.com/NotAlive24/BedrockNodeTunnel) — upstream Linux tunnel client and service.
- [Bedrock Node](https://bedrocknode.net/) — tunnel dashboard and service.
- [Microsoft Learn: Bedrock Dedicated Server](https://learn.microsoft.com/en-us/minecraft/creator/documents/bedrockserver/getting-started?view=minecraft-bedrock-stable) — server setup documentation.
- [Microsoft Learn: Server properties](https://learn.microsoft.com/en-us/minecraft/creator/documents/bedrockserver/server-properties?view=minecraft-bedrock-stable) — BDS configuration reference.
- [Microsoft Learn: Behavior packs](https://learn.microsoft.com/en-us/minecraft/creator/documents/behaviorpack?view=minecraft-bedrock-stable) — add-on documentation.
