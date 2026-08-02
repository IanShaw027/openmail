# GitHub repository settings (visibility)

Apply these in the GitHub UI after the initial push (API optional).

## About

**Description (≤160 chars):**

```text
Local-first multi-source mailbox console: browser vault, FastAPI proxy fetch, Graph/IMAP/mail.com/HttpApi, Docker.
```

**Website:** `https://mail.clomio.ai`

## Social preview

Upload [`assets/social-banner.svg`](../assets/social-banner.svg) (export to 1280×640 PNG if the UI requires raster) under **Settings → General → Social preview**.

## Topics

```
email
self-hosted
local-first
fastapi
vue
vite
imap
microsoft-graph
totp
docker
privacy
mail
```

## Social preview

Upload a 1280×640 PNG of the console (dark UI) under **Settings → General → Social preview**.

## Features to enable

- [x] Issues  
- [x] Discussions (optional, for Q&A)  
- [x] Security advisories  
- [ ] Wikis (prefer `docs/` in-repo)  
- [ ] Projects (optional)

## README badges

CI badge needs Actions enabled on the default branch after first push of `.github/workflows/ci.yml`.

## Packages / Docker

| Registry | Image |
|----------|--------|
| Docker Hub | https://hub.docker.com/r/ianshaw027/openmail |
| GHCR | `ghcr.io/ianshaw027/openmail` (CI push) |

### Docker Hub CI secret

Repo → **Settings → Secrets and variables → Actions**:

| Secret | Purpose |
|--------|---------|
| `DOCKERHUB_TOKEN` | Access token with write (or `DOCKERHUB_USERNAME` + token) |

Without `DOCKERHUB_TOKEN`, the docker workflow still builds and can push to **GHCR** via `GITHUB_TOKEN`.

### Make GHCR package public

GitHub → profile/org → **Packages** → `openmail` → **Package settings** → Change visibility → Public.
