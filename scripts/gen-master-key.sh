#!/usr/bin/env bash
# Print a new OPENMAIL_MASTER_KEY (32 random bytes, base64).
set -euo pipefail
python3 -c "import os, base64; print(base64.b64encode(os.urandom(32)).decode())"
