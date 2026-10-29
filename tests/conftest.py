import os
import sys
from pathlib import Path

prefix = Path(sys.prefix)
if os.name == 'nt':
    from gis_agent_v2.runtime import configure
    configure()
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_PLUGIN_PATH', str(prefix / 'plugins'))
os.environ.setdefault('QT_QPA_PLATFORM_PLUGIN_PATH', str(prefix / 'plugins/platforms'))
os.environ.setdefault('PROJ_DATA', str(prefix / 'share/proj'))
sys.path.insert(0, str(prefix / 'share/qgis/python'))
sys.path.insert(0, str(prefix / 'share/qgis/python/plugins'))
