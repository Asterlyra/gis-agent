#!/bin/zsh
set -euo pipefail
TASK_DIR="${0:A:h}"
GIS_PYTHON="${GIS_PYTHON:-$TASK_DIR/.runtime/bin/python}"
if [[ ! -x "$GIS_PYTHON" ]]; then
  print -u2 "未找到 QGIS Python 环境。请先运行 ./setup.sh，或通过 GIS_PYTHON 指定已有环境。"
  exit 1
fi
GIS_PREFIX="${GIS_PYTHON:h:h}"
export PYTHONPATH="$TASK_DIR/src:$GIS_PREFIX/share/qgis/python:$GIS_PREFIX/share/qgis/python/plugins:${PYTHONPATH:-}"
export QGIS_PREFIX_PATH="$GIS_PREFIX"
export QT_PLUGIN_PATH="$GIS_PREFIX/plugins"
export QT_QPA_PLATFORM_PLUGIN_PATH="$GIS_PREFIX/plugins/platforms"
export PROJ_DATA="$GIS_PREFIX/share/proj"
export QT_QPA_PLATFORM=offscreen
cd "$TASK_DIR"
exec "$GIS_PYTHON" -m gis_agent_v2.cli "$@"
