# GIS Agent

面向城市空间分析的本地工作台，使用 PyQGIS 完成空间计算，支持邻近、覆盖与多条件未覆盖分析。离线模式执行固定流程；在线模式调用模型服务。

需要 QGIS/PyQGIS 和 Python 3.11。通过 `./setup.sh` 创建 Conda 环境，或将 `GIS_PYTHON` 指向已配置的 QGIS Python，运行 `./run_v2.sh serve --port 8766`，打开 http://127.0.0.1:8766。在线模式使用 `.env.local` 中的服务凭据，参见 `.env.example`。请勿提交密钥和运行记录。

示例数据来自 OpenStreetMap/Geofabrik，© OpenStreetMap contributors，以 ODbL 1.0 提供，详见 `v2/data/数据来源与许可.md` 和 `v2/data/provenance.json`。地图组件 Leaflet 的许可见 `v2/static/vendor/LEAFLET-LICENSE`。数据不能代表权威行政边界或设施完整状况。

QGIS 原生环境及实验结果需要单独验证；现有测试可以用配置了 PyQGIS 的 Python 执行 `python -m pytest`。

## 许可

本项目原创代码采用 [MIT License](LICENSE)。第三方代码、数据和素材各自适用其原有许可。
