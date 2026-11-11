# Architecture overview

BDS Omni Manager 2.0.3 is a local Linux application built with Python's standard library and a browser-based single-page interface.

## Main components

- `main.py`: application configuration, BDS process management, server/command/property operations, archive and world/pack services, validated `nethernet-client` parameter management through a systemd user drop-in, monitoring/scheduling, Content Intelligence, and the threaded HTTP API.
- `static/index.html`: AMOLED-black single-page UI, navigation, forms, status views and browser-side API interactions.
- `assets/icon.svg`: application icon used by the desktop entry.
- `install.sh`: copies runtime files into `~/.local/share/bds-omni-manager/` and creates a desktop entry in `~/.local/share/applications/`.
- `run.sh`: starts the application using `python3`.
- `tests/`: standard-library `unittest` coverage for core behavior, HTTP routes, archive/content handling, tunnel integration, monitoring/scheduling, Content Intelligence and UI regressions.

## Runtime data

The application keeps mutable runtime data outside the source tree:

- `~/.config/bds-omni-manager/config.json`
- `~/.config/bds-omni-manager/nethernet-client.env` (optional custom tunnel token; owner-only file and directory permissions)
- `~/.local/state/bds-omni-manager/`
- `~/BDS-Backups/` by default

The upstream client normally owns its browser-claimed token. When an operator explicitly configures a custom `-token` in BDS Omni Manager, the manager stores it in a separate owner-only EnvironmentFile (`0600`, in a `0700` config directory) and references it through a systemd user-service drop-in. Token contents are never returned to the UI or written to manager logs. The drop-in is placed under the active XDG systemd user configuration directory and does not alter the vendor unit.

## Trust boundaries

- HTTP binds to `127.0.0.1`; the interface is not designed for remote exposure.
- API mutation routes use a per-launch local token.
- Uploaded archives and manifests are untrusted and must be validated before extraction or activation.
- World/pack changes enforce the server-stopped guard and use verified recovery archives/rollback where described in the README.
- Pack dependency and asset-overlap results are static file/manifest analysis, not proof of runtime behavior.
- The manager does not edit LevelDB or execute add-on scripts.

## Shutdown lifecycle

On Ctrl+C, the application stops accepting new scheduled work and waits for active backup work to release BDS `save hold`. It then requests a final world backup while its managed BDS process is still running. After the backup attempt, it stops the linked tunnel (if configured) and the managed BDS process, then closes the local HTTP listener. The backup is verified by the existing archive/CRC checks. If the backup fails, the failure is reported and shutdown continues safely. The manager does not stop an externally launched BDS process.

The tunnel parameter API accepts only structured documented flags and validates URLs, endpoints, IP addresses and port ranges. It does not accept arbitrary shell fragments. `-reset-token` is a separate authenticated action, never a persistent ExecStart argument.

## Local validation

```bash
python -m compileall -q main.py tests
python -m unittest discover -s tests -v
bash -n install.sh run.sh
```

Node.js, when available, is used by a UI regression test to syntax-check the inline JavaScript. It is not required to run the manager.
