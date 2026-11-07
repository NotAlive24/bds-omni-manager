## Summary

<!-- What changed and why? -->

## Validation

- [ ] `python -m compileall -q main.py tests`
- [ ] `python -m unittest discover -s tests -v`
- [ ] `bash -n install.sh run.sh`
- [ ] UI changes checked at desktop and narrow/mobile widths (when applicable)
- [ ] No new browser-default/light controls; added controls have explicit AMOLED styles

## Safety and compatibility

- [ ] No real world, backup, token, XUID, player data or account data is included
- [ ] Filesystem/archive changes validate paths and handle rollback/failure paths
- [ ] Mutations correctly enforce the stopped-server requirement
- [ ] HTTP routes require the local API token where appropriate
- [ ] Bedrock command/property/manifest behavior is based on current documented syntax
- [ ] README and CHANGELOG updated when user-visible behavior changes

## Screenshots (for UI changes)

<!-- Add before/after screenshots when useful. -->
