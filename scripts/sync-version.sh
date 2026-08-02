#!/usr/bin/env bash
# Sync product version across repo sources from a semver (with or without leading v).
# Usage: ./scripts/sync-version.sh 0.2.0
#        ./scripts/sync-version.sh v0.2.0
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RAW="${1:-}"

if [[ -z "$RAW" ]]; then
  echo "usage: $0 <version>   e.g. 0.2.0 or v0.2.0" >&2
  exit 1
fi

# Strip leading v; reject junk
VERSION="${RAW#v}"
if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.+-]+)?$ ]]; then
  echo "error: invalid semver: $RAW" >&2
  exit 1
fi

TAG="v${VERSION}"
# Default published image is GHCR (CI always pushes). Hub is optional dual-publish.
IMAGE_DEFAULT="ghcr.io/ianshaw027/openmail:${TAG}"

echo "→ sync version ${VERSION} (tag ${TAG})"

# backend package version
python3 - "$ROOT" "$VERSION" <<'PY'
import pathlib, re, sys
root, ver = pathlib.Path(sys.argv[1]), sys.argv[2]
path = root / "backend" / "app" / "__init__.py"
text = path.read_text(encoding="utf-8")
new = re.sub(
    r'^__version__\s*=\s*["\'][^"\']*["\']',
    f'__version__ = "{ver}"',
    text,
    count=1,
    flags=re.M,
)
if new == text and f'__version__ = "{ver}"' not in text:
    raise SystemExit(f"could not update {path}")
path.write_text(new, encoding="utf-8")
print(f"  {path.relative_to(root)}")
PY

# frontend package.json
python3 - "$ROOT" "$VERSION" <<'PY'
import json, pathlib, sys
root, ver = pathlib.Path(sys.argv[1]), sys.argv[2]
path = root / "frontend" / "package.json"
data = json.loads(path.read_text(encoding="utf-8"))
data["version"] = ver
path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"  {path.relative_to(root)}")
PY

# compose default image pin
python3 - "$ROOT" "$IMAGE_DEFAULT" <<'PY'
import pathlib, re, sys
root, image = pathlib.Path(sys.argv[1]), sys.argv[2]
path = root / "docker-compose.yml"
text = path.read_text(encoding="utf-8")
new = re.sub(
    r"(\$\{OPENMAIL_IMAGE:-)(?:ghcr\.io/)?ianshaw027/openmail:[^}]+(\})",
    rf"\1{image}\2",
    text,
    count=1,
)
# Only rewrite the compose default pin line already handled above.
# Do not mass-replace Hub comment examples (optional dual-publish).
if f"OPENMAIL_IMAGE:-{image}" not in new and "${OPENMAIL_IMAGE:-" + image + "}" not in new:
    if new == text:
        raise SystemExit(f"could not update compose image in {path}")
path.write_text(new, encoding="utf-8")
print(f"  {path.relative_to(root)}")
PY

# install.sh default
python3 - "$ROOT" "$IMAGE_DEFAULT" <<'PY'
import pathlib, re, sys
root, image = pathlib.Path(sys.argv[1]), sys.argv[2]
path = root / "scripts" / "install.sh"
text = path.read_text(encoding="utf-8")
pat = r'IMAGE="\$\{OPENMAIL_IMAGE:-[^}]+\}"'
if not re.search(pat, text):
    raise SystemExit(f"could not find OPENMAIL_IMAGE default in {path}")
new = re.sub(pat, f'IMAGE="${{OPENMAIL_IMAGE:-{image}}}"', text, count=1)
path.write_text(new, encoding="utf-8")
print(f"  {path.relative_to(root)}")
PY

# VERSION file (single source of truth for tooling)
printf '%s\n' "$VERSION" >"$ROOT/VERSION"
echo "  VERSION"

echo "✓ version files → ${VERSION}"
