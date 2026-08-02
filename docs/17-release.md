# Release & packaging

Canonical image is **Docker Hub** only:

```text
ianshaw027/openmail:vX.Y.Z
ianshaw027/openmail:latest
```

## What happens on a tag

Workflow: [`.github/workflows/release.yml`](../.github/workflows/release.yml)

1. Checkout the tag  
2. `scripts/sync-version.sh` rewrites `VERSION`, backend, frontend, compose default, install default (for image bake)  
3. Build with `OPENMAIL_VERSION=X.Y.Z`  
4. Push Hub tags: `vX.Y.Z`, `X.Y.Z`, `latest`, short SHA  
5. Create/update **GitHub Release** notes  

Branch pushes use [`.github/workflows/docker.yml`](../.github/workflows/docker.yml) for rolling `latest` / SHA on Hub.

## Secrets

| Secret | Required | Notes |
|--------|----------|--------|
| `DOCKERHUB_TOKEN` | **yes** for publish | Hub access token (Read/Write) |
| `DOCKERHUB_USERNAME` | optional | default `ianshaw027` |
| `GITHUB_TOKEN` | automatic | GitHub Release only |

```bash
gh secret set DOCKERHUB_TOKEN -R IanShaw027/openmail
# gh secret set DOCKERHUB_USERNAME -R IanShaw027/openmail -b ianshaw027
```

## Cut a release

```bash
make release V=0.2.0
# or: ./scripts/release.sh 0.2.0
```

Tag only (if version files already committed):

```bash
git tag -a v0.2.0 -m "OpenMail v0.2.0"
git push origin v0.2.0
```

Re-publish an existing tag:

```bash
gh workflow run release.yml -R IanShaw027/openmail -f tag=v0.1.0
```

## Version surfaces

| File | Field |
|------|--------|
| `VERSION` | plain `X.Y.Z` |
| `backend/app/__init__.py` | `__version__` (+ optional env `OPENMAIL_VERSION`) |
| `frontend/package.json` | `"version"` |
| `docker-compose.yml` | default `OPENMAIL_IMAGE=ianshaw027/openmail:vX.Y.Z` |
| `scripts/install.sh` | same default |
| Image health | `/api/health` → `"version": "X.Y.Z"` |

## Pull

```bash
docker pull ianshaw027/openmail:v0.1.0
docker compose pull && docker compose up -d
```
