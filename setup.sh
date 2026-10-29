#!/bin/zsh
set -euo pipefail
TASK_DIR="${0:A:h}"
if ! command -v conda >/dev/null; then
  print -u2 "需要 Conda / Miniforge，或自行准备包含 QGIS 的 Python 环境。"
  exit 1
fi
if [[ ! -x "$TASK_DIR/.runtime/bin/python" ]]; then
  conda env create --prefix "$TASK_DIR/.runtime" --file "$TASK_DIR/environment.yml" --solver libmamba
fi
chmod 600 "$TASK_DIR/.env.local" 2>/dev/null || true
chmod +x "$TASK_DIR/run.sh"
"$TASK_DIR/run.sh" init
"$TASK_DIR/run.sh" doctor
