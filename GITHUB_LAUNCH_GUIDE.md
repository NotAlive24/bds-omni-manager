# Publish BDS Omni Manager on GitHub

This folder is prepared as the Git repository. The separate `release-assets/` folder in the launch kit contains the 2.0.0 ZIP/TAR.GZ/checksum files to upload to a GitHub Release.

## 1. Review before publishing

- Confirm you want the repository to be public. The included `LICENSE` is MIT, which permits reuse, modification and redistribution subject to its terms.
- Confirm the project name and description below.
- Review `README.md`, `SECURITY.md`, `SUPPORT.md` and the known limitations.
- Search for private information before pushing. Do not commit your BDS world, backup archives, `config.json`, logs, `.env` files, tunnel token, XUIDs, credentials or private IPs.
- The project has no third-party Python runtime dependencies. BedrockNodeTunnel is a separate upstream project and is not bundled.

## 2. Suggested GitHub repository details

- **Repository name:** `bds-omni-manager`
- **Description:** `AMOLED-black Linux GUI for Minecraft Bedrock Dedicated Server management, worlds, add-ons, backups, monitoring and BedrockNodeTunnel.`
- **Suggested visibility:** Public, if you are ready to publish the source under MIT. You can choose Private instead and change visibility later.
- **Website:** leave blank unless you have a project website.
- **Topics:** `minecraft-bedrock`, `bedrock-dedicated-server`, `minecraft-server`, `server-management`, `linux`, `arch-linux`, `python`, `bedrock-addons`, `bedrock-node-tunnel`, `amoled`

## 3. Create and push the repository

### Option A — GitHub CLI (quickest)

Install Git and GitHub CLI on Arch if needed:

```bash
sudo pacman -S git github-cli
```

Authenticate:

```bash
gh auth login
```

In a terminal, change into the extracted **repository** folder (the folder containing `main.py`, `README.md` and `.github/`), then run:

```bash
git init -b main
git add .
git status --short
git diff --cached --stat
git commit -m "Release BDS Omni Manager 2.0.0"
gh repo create bds-omni-manager --public --source=. --remote=origin --push
```

The `git status` and staged diff commands let you check exactly what will be uploaded before the commit. To create a private repository instead, change `--public` to `--private`.

### Option B — GitHub website + Git

1. On GitHub, create a **new empty repository** named `bds-omni-manager`.
2. Choose Public or Private.
3. **Do not** initialize it with a README, `.gitignore` or license; those already exist in this prepared project.
4. In the extracted repository folder, run:

```bash
git init -b main
git add .
git status --short
git diff --cached --stat
git commit -m "Release BDS Omni Manager 2.0.0"
git remote add origin https://github.com/YOUR-USERNAME/bds-omni-manager.git
git push -u origin main
```

Replace `YOUR-USERNAME` with your GitHub username or organization name.

## 4. Check the first GitHub Actions run

After pushing, open the repository's **Actions** tab. The `Tests` workflow runs the standard-library test suite on Python 3.11–3.15, compiles Python source, and checks the shell scripts. Wait for the workflow to finish and review any failed matrix job before publishing the release.

## 5. Create the v2.0.0 GitHub Release

From the repository folder:

```bash
git tag -a v2.0.0 -m "BDS Omni Manager 2.0.0"
git push origin v2.0.0
```

Then on GitHub:

1. Open **Releases** → **Draft a new release**.
2. Choose the existing tag `v2.0.0`.
3. Set the release title to `BDS Omni Manager 2.0.0 — Content Intelligence`.
4. Copy the contents of `RELEASE_NOTES_v2.0.0.md` into the release description.
5. Upload these three files from the launch kit's `release-assets/` directory:
   - `BDS-OLED-Manager-2.0.0.zip`
   - `BDS-OLED-Manager-2.0.0.tar.gz`
   - `BDS-OLED-Manager-2.0.0.sha256`
6. Publish the release.

GitHub also generates source-code ZIP and TAR.GZ snapshots automatically from the tag. The manually attached files above are the tested application source archives and their checksums.

## 6. Recommended repository settings after launch

- Keep **Issues** enabled; the included bug and feature forms will guide reports.
- Enable **Discussions** if you want a place for usage questions.
- Under **Settings → Security**, enable private vulnerability reporting if available so reporters can disclose security issues privately.
- Keep GitHub Actions enabled. The workflow has read-only repository permissions for CI.
- Consider protecting `main` and requiring the `Tests` workflow to pass before merging pull requests once you begin accepting contributions.
- Review Dependabot pull requests for GitHub Actions updates.
- Add `docs/screenshots/github-social-preview.png` under **Settings → General → Social preview** for a branded repository card. Desktop and mobile UI screenshots are in the same folder.

## 7. After publishing

- Add your GitHub repository URL to any project posts or documentation.
- Check the README renders correctly and both screenshots load.
- Verify the release assets download and compare their SHA-256 hashes.
- Create a small issue yourself only if you find a real follow-up; do not create artificial activity.

## What this package does not do

It does not create the GitHub repository, authenticate your GitHub account, or push code on your behalf. Those steps require your GitHub account and your explicit action.
