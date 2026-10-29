"""Configure the Windows conda QGIS layout before importing native libraries."""
import os
import sys
from pathlib import Path

_dll_handles = []


def configure():
    if os.name != 'nt':
        return
    prefix = Path(sys.prefix)
    library = prefix / 'Library'
    os.environ.update(QGIS_PREFIX_PATH=str(library), QT_QPA_PLATFORM='offscreen',
                      QT_API='pyqt5', MPLBACKEND='Agg', GDAL_DATA=str(library/'share/gdal'),
                      PROJ_DATA=str(library/'share/proj'),
                      QT_PLUGIN_PATH=os.pathsep.join(str(library/p) for p in ('plugins', 'qtplugins')))
    candidates = [library/'plugins/platforms', library/'qtplugins/platforms']
    qpa = next((path for path in candidates if (path/'qwindows.dll').is_file()), candidates[0])
    os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = str(qpa)
    for path in (prefix, library/'bin'):
        if path.is_dir():
            _dll_handles.append(os.add_dll_directory(str(path)))
    for path in (library/'python', library/'python/plugins'):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
