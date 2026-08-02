#!/usr/bin/env bash
# Local helper: bump version sources, commit, tag, push (triggers CI package + GH Release).
# Uses git credentials already configured (gh / remote).
#
# Usage:
#   ./scripts/release.sh 0.2.0              # release from clean main
#   ./scripts/release.sh 0.2.0 --dry-run    # show plan only
#   ./scripts/release.sh 0.2.0 --push-only  # assume already synced; only tag+push
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VERSION_RAW="${1:-}"
DRY=0
PUSH_ONLY=0
for a in "${@:2}"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    --push-only) PUSH_ONLY=1 ;;
    *) echo "unknown flag: $a" >&2; exit 1 ;;
  esac
done

if [[ -z "$VERSION_RAW" ]]; then
  cat <<EOF >&2
usage: $0 <version> [--dry-run] [--push-only]

  1) sync-version.sh writes VERSION / backend / frontend / compose / install
  2) commit "chore(release): vX.Y.Z"
  3) annotated tag vX.Y.Z
  4) git push origin main --tags  → GitHub Actions builds package + creates Release

Requires: clean working tree, on main/master, remote origin.
EOF
  exit 1
fi

VERSION="${VERSION_RAW#v}"
TAG="v${VERSION}"

branch="$(git rev-parse --abbrev-ref HEAD)"
if [[ "$branch" != "main" && "$branch" != "master" ]]; then
  echo "error: release from main/master (currently on $branch)" >&2
  exit 1
fi

if [[ -n "$(git status --porcelain)" ]]; then
  echo "error: working tree not clean" >&2
  git status --short
  exit 1
fi

if git rev-parse "$TAG" >/dev/null 2>&1; then
  echo "error: tag $TAG already exists locally" >&2
  exit 1
fi

if git ls-remote --tags origin "refs/tags/${TAG}" | grep -q .; then
  echo "error: tag $TAG already exists on origin" >&2
  exit 1
fi

echo "plan: release ${TAG} on ${branch}"
if [[ $DRY -eq 1 ]]; then
  echo "(dry-run) would run sync-version, commit, tag, push"
  exit 0
fi

if [[ $PUSH_ONLY -eq 0 ]]; then
  bash "$ROOT/scripts/sync-version.sh" "$VERSION"
  git add VERSION backend/app/__init__.py frontend/package.json docker-compose.yml scripts/install.sh
  # package-lock may pin version field if present
  if grep -q '"version"' frontend/package-lock.json 2>/dev/null; then
    # keep lock name/version in sync when top-level version exists
    python3 - "$ROOT" "$VERSION" <<'PY'
import json, pathlib, sys
root, ver = pathlib.Path(sys.argv[1]), sys.argv[2]
p = root / "frontend" / "package-lock.json"
if not p.exists():
    raise SystemExit(0)
data = json.loads(p.read_text(encoding="utf-8"))
data["version"] = ver
if "packages" in data and "" in data["packages"]:
    data["packages"][""]["version"] = ver
p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print("  frontend/package-lock.json")
PY
    git add frontend/package-lock.json || true
  fi
  git commit -m "chore(release): ${TAG}"
fi

git tag -a "$TAG" -m "OpenMail ${TAG}"
echo "→ push ${branch} + ${TAG}"
git push origin "$branch"
git push origin "$TAG"

echo "✓ pushed ${TAG}"
echo "  Actions: https://github.com/IanShaw027/openmail/actions"
echo "  Package: ghcr.io/ianshaw027/openmail:${TAG}"
echo "  Hub:     ianshaw027/openmail:${TAG}  (if DOCKERHUB_TOKEN secret set)"
echo "  Release: https://github.com/IanShaw027/openmail/releases/tag/${TAG}"
