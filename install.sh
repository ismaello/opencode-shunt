#!/usr/bin/env bash
# Kept so existing habits and scripts keep working. The installer itself is
# install.py, which needs to hash files and read a JSON manifest to tell an
# outdated file from a locally edited one.
exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/install.py" "$@"
