# Architecture overview

BDS Omni Manager 2.0.0 is a local Linux application built with Python's standard library and a browser-based single-page interface.

## Main components

- `main.py`: application configuration, BDS process management, server/command/property operations, archive and world/pack services, Bedrock Node Tunnel systemd-user-service integration, monitoring/scheduling, Content Intelligence, and the threaded HTTP API.
- `static/index.html`: AMOLED-black single-page UI, navigation, forms, status views and browser-side API interactions.
- `assets/icon.svg`: application icon used by the desktop entry.
- `install.sh`: copies runtime files into `~/.local/share/bds-omni-manager/` and creates a desktop entry in `~/.local/share/applications/`.
- `run.sh`: starts the application using `python3`.
- `tests/`: standard-library `unittest` coverage for core behavior, HTTP routes, archive/content handling, tunnel integration, monitoring/scheduling, Content Intelligence and UI regressions.

## Runtime data

The application keeps mutable runtime data outside the source tree:

- `~/.config/bds-omni-manager/config.json`
- `~/.local/state/bds-omni-manager/`
- `~/BDS-Backups/` by default

The Bedrock Node token remains owned by the separate upstream client. The manager checks token-file presence/permissions without reading or returning token contents.

## Trust boundaries

- HTTP binds to `127.0.0.1`; the interface is not designed for remote exposure.
- API mutation routes use a per-launch local token.
- Uploaded archives and manifests are untrusted and must be validated before extraction or activation.
- World/pack changes enforce the server-stopped guard and use verified recovery archives/rollback where described in the README.
- Pack dependency and asset-overlap results are static file/manifest analysis, not proof of runtime behavior.
- The manager does not edit LevelDB or execute add-on scripts.

## Local validation

```bash
python -m compileall -q main.py tests
python -m unittest discover -s tests -v
bash -n install.sh run.sh
```

Node.js, when available, is used by a UI regression test to syntax-check the inline JavaScript. It is not required to run the manager.
