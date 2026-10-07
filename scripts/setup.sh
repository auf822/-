#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv
.venv/bin/python -m pip install --disable-pip-version-check -r requirements.lock
.venv/bin/python -c 'from app.db import init_db; init_db()'
echo '依赖与数据库结构已就绪。首次体验可运行 .venv/bin/python -m app.seed --demo'
