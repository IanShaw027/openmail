"""OpenMail backend application package."""

from __future__ import annotations

import os

__version__ = "0.3.0"

# Docker/CI may inject OPENMAIL_VERSION so health matches the image tag.
_env_ver = (os.environ.get("OPENMAIL_VERSION") or "").strip()
if _env_ver:
    __version__ = _env_ver.lstrip("v")
