# Security policy

## Supported version

Security fixes are targeted at the latest published release. Please update to the latest release before reporting an issue where practical.

## Reporting a vulnerability

**Do not disclose vulnerabilities in a public issue, discussion, pull request, screenshot, or log.**

Use GitHub's private vulnerability reporting feature on this repository (the **Security** tab → **Report a vulnerability**) when it is enabled. If private reporting is unavailable, contact the repository maintainer privately using the contact method on their GitHub profile and ask for a secure reporting channel.

Include:

- A concise description of the issue and its impact.
- Affected release/commit and Linux/Python versions.
- Minimal reproduction steps or a proof of concept that does not access another person's server or data.
- Any suggested mitigation.

Do not include real Bedrock worlds, backups, tunnel tokens, XUIDs, player/account data, public IP addresses, or other secrets. Redact logs before sharing.

## Security design notes

BDS Omni Manager is intended for local administration. It binds its HTTP interface to `127.0.0.1` and uses a per-launch API token. Do not expose it to a LAN, public network, reverse proxy, or internet tunnel. The token check is not a substitute for a hardened remote-access design.

The manager can execute server commands and modify worlds/packs. Run it only under a trusted local user account, protect that account, and keep backups outside the BDS server directory.
