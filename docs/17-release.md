# Release & packaging

Every **git tag** `vX.Y.Z` builds a container package and refreshes the GitHub Release.
Versions in the tree are kept in sync by `scripts/sync-version.sh`.

## What happens on a tag

Workflow: [`.github/workflows/release.yml`](../.github/workflows/release.yml)

1. Checkout the tag
2. Run `scripts/sync-version.sh` so the **image** bakes:
   - `VERSION`
   - `backend/app/__init__.py` → `__version__`
   - `frontend/package.json`
   - `docker-compose.yml` default `OPENMAIL_IMAGE`
   - `scripts/install.sh` default image
3. `docker buildx` with `OPENMAIL_VERSION=X.Y.Z`
4. Push tags:
   - `ghcr.io/ianshaw027/openmail:vX.Y.Z`
   - `ghcr.io/ianshaw027/openmail:X.Y.Z`
   - `ghcr.io/ianshaw027/openmail:latest`
   - short SHA
   - **Docker Hub** `ianshaw027/openmail:…` only if secrets are set
5. Create/update **GitHub Release** notes (image pull + changelog slice)

Branch pushes (main) use [`.github/workflows/docker.yml`](../.github/workflows/docker.yml) for rolling `latest` / SHA tags only (no GitHub Release).

## Credentials (git / GitHub)

| Target | Auth |
|--------|------|
| GHCR + GitHub Release | Built-in `GITHUB_TOKEN` (`packages: write`, `contents: write`) — no extra secret |
| Docker Hub (optional) | Repo secrets `DOCKERHUB_TOKEN` and optional `DOCKERHUB_USERNAME` (default `ianshaw027`) |

Set Hub secrets (once):

```bash
# create a Docker Hub access token with Read/Write, then:
gh secret set DOCKERHUB_TOKEN -R IanShaw027/openmail
# optional:
# gh secret set DOCKERHUB_USERNAME -R IanShaw027/openmail -b ianshaw027
```

Local `gh` / `git` must already be able to push tags to `origin` (your existing GitHub credential).

## Cut a release (recommended)

From a clean `main`:

```bash
# sync version sources, commit, annotated tag, push branch + tag
make release V=0.2.0
# or:
./scripts/release.sh 0.2.0
```

CI then packages automatically. Watch:

- Actions: https://github.com/IanShaw027/openmail/actions
- Packages: https://github.com/IanShaw027/openmail/pkgs/container/openmail
- Releases: https://github.com/IanShaw027/openmail/releases

### Tag only (version already committed)

```bash
git tag -a v0.2.0 -m "OpenMail v0.2.0"
git push origin v0.2.0
```

Or re-run packaging for an existing tag:

```bash
gh workflow run release.yml -R IanShaw027/openmail -f tag=v0.1.0
```

### Sync files without tagging

```bash
make sync-version V=0.2.0
# review, commit yourself, then tag
```

## Version surfaces

| File | Field |
|------|--------|
| `VERSION` | plain `X.Y.Z` |
| `backend/app/__init__.py` | `__version__` (+ optional env `OPENMAIL_VERSION`) |
| `frontend/package.json` | `"version"` |
| `docker-compose.yml` | default `OPENMAIL_IMAGE=ghcr.io/ianshaw027/openmail:vX.Y.Z` |
| `scripts/install.sh` | same default |
| Image health | `/api/health` → `"version": "X.Y.Z"` |

## Pull published image

```bash
# GHCR (always published by release workflow)
docker pull ghcr.io/ianshaw027/openmail:v0.1.0

# Docker Hub (if secret configured, or manual desktop push)
docker pull ianshaw027/openmail:v0.1.0

export OPENMAIL_IMAGE=ghcr.io/ianshaw027/openmail:v0.1.0
docker compose pull && docker compose up -d
```
