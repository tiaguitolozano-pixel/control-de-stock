#!/usr/bin/env bash
# Arranque en Linux / Mac.
set -e
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
exec .venv/bin/python iniciar.py
