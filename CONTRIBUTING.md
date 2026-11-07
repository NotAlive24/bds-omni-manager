# Contributing

Thanks for considering a contribution to BDS Omni Manager.

## Before opening an issue or pull request

- Search existing issues and discussions first.
- For bugs, use the bug report form and include exact reproduction steps.
- For feature ideas, explain the Bedrock workflow, relevant BDS version, and expected safety behavior.
- Never publish server worlds, backup archives, Bedrock Node tokens, XUIDs, account details, player data, or unredacted logs.

## Development requirements

- Linux is the target platform; Arch Linux is the primary development environment.
- Python 3.11 or newer is required.
- The application uses the Python standard library only. Avoid introducing third-party runtime dependencies without a clear need and a documented reason.
- Node.js is used by the UI regression test when available to syntax-check inline JavaScript; it is not an application runtime dependency.

## Run locally

```bash
./run.sh
```

For an installed desktop entry:

```bash
./install.sh
```

## Run checks

```bash
python -m compileall -q main.py tests
python -m unittest discover -s tests -v
bash -n install.sh run.sh
```

The test suite uses temporary directories and mocked processes/services. Do not run experimental tests against a valuable live world or production tunnel account.

## Engineering expectations

1. Keep the web server bound to loopback by default. Do not add unauthenticated remote management.
2. Treat browser input, uploaded archives, manifests, filenames and persisted state as untrusted.
3. Validate paths before reading, writing, extracting, deleting or moving files. Reject traversal, symlinks and special archive entries where relevant.
4. For destructive filesystem changes, create and verify a recovery copy and implement rollback for partial failures.
5. Require a confirmed stopped BDS state before world/pack mutations that cannot safely happen live.
6. Do not expose or log tunnel tokens, credentials, XUIDs or sensitive player data.
7. Keep the UI AMOLED-black. Every new visible control needs explicit styles for normal, hover, focus, disabled, error and responsive states as appropriate.
8. Preserve accessible labels, keyboard focus, clear empty/loading/error states and safe confirmation flows.
9. Keep `README.md` and `CHANGELOG.md` accurate. Do not claim compatibility or tests that were not performed.
10. Keep tests deterministic and independent of a real BDS binary, Bedrock Node account or public tunnel.

## Pull request checklist

Use the repository pull request template. Include tests for both success and failure paths, update documentation, and provide screenshots for meaningful UI changes.
