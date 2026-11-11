# BDS Omni Manager 2.0.3 — Nethernet Client Parameters and safe shutdown

## Highlights

### Configure the upstream nethernet-client

The Bedrock Node Tunnel page now exposes the persistent arguments documented by `nethernet-client --help`:

| UI field | Client flag |
|---|---|
| Web dashboard URL | `-web` |
| VPS control address | `-control` |
| Local Bedrock TCP target | `-local-http` |
| Local UDP IP | `-local-udp-ip` |
| Local UDP port | `-local-udp-port` |
| Optional custom token | `-token` |

Enter each value in its own field. For example, set the web URL to `https://<subdomain>.bedrocknode.net/` and the control endpoint to `<region-ip>:7000`; do not paste `nethernet-client` or the flag names into the fields. Values are validated and serialized as a fixed argument list, not executed through a shell.

`-version` remains a read-only discovery operation. `-reset-token` is a separate confirmed action, not a saved parameter. Leave the token field blank to preserve the current token. Use the explicit clear option to remove a custom token. The UI and state API report only whether a token is configured, never its value.

### Secure service configuration

The manager writes its own systemd user-service drop-in under the active XDG systemd configuration directory; it never edits the upstream vendor unit. Saving restarts the tunnel only if it was already active. A supplied custom token is stored in a separate EnvironmentFile under `~/.config/bds-omni-manager/` with file mode `0600` and directory mode `0700`. A legacy `-token` argument is migrated to that file before the vendor `ExecStart` is overridden. Settings and token changes use snapshots and rollback if writing, daemon reload, or service restart fails.

### Ctrl+C creates a final world backup

When Ctrl+C is received, the manager:

1. Stops the scheduler from starting new work and waits for active backup work to finish its `save resume` cleanup.
2. Requests a final world backup while the manager-owned BDS process is still running. The existing live-backup flow uses `save hold`/`save query` and verified archive creation.
3. Stops the linked tunnel if configured, then gracefully stops the managed BDS process.
4. Closes the local HTTP listener.

If the final backup fails, the error is printed and recorded, and shutdown still proceeds to stop the managed BDS process. The manager does not stop or back up an externally launched BDS process.

## Validation and regression coverage

- All documented `nethernet-client` argument defaults and custom values.
- HTTPS dashboard URL validation, host/port validation including bracketed IPv6, local TCP target, UDP IP and UDP port boundaries.
- Optional token privacy, file permissions, token migration, keep/clear behavior, reset confirmation, and recovery if a service restart or post-reset drop-in write fails.
- Absolute `XDG_CONFIG_HOME` behavior for the systemd user drop-in.
- Authenticated settings and reset-token HTTP routes.
- Ctrl+C final backup ordering, backup failure behavior, shutdown despite background shutdown errors, and HTTP listener closure.
- AMOLED field styling and browser form behavior at desktop and mobile widths.

## Test results

`python -m unittest discover -s tests -v`: **255 tests passed**. Python compilation, Bash syntax checks, inline JavaScript syntax checks, CSS parsing, a Chromium form interaction run, and `systemd-analyze --user verify` for a generated drop-in also passed in the build/verification environment. The live Bedrock Dedicated Server and live Bedrock Node Tunnel were not available for runtime verification.
