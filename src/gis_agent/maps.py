import json
from pathlib import Path


def export_map(task, folder, result):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon
    from matplotlib.collections import PatchCollection
    from matplotlib import font_manager
    for name in ['PingFang SC', 'Noto Sans CJK SC', 'Arial Unicode MS']:
        if any(f.name == name for f in font_manager.fontManager.ttflist):
            plt.rcParams['font.family'] = name
            break
    folder = Path(folder)
    data = json.loads((folder / 'zones.geojson').read_text())
    field = 'school_count' if task['family'] == 'proximity' else 'coverage_ratio'
    patches, values = [], []
    fig, ax = plt.subplots(figsize=(10, 5.3), layout='constrained')
    key = task['schema']['zone_key']
    for f in data['features']:
        geo = f['geometry']
        rings = [geo['coordinates']] if geo['type'] == 'Polygon' else geo['coordinates']
        for polygon in rings:
            patches.append(Polygon(polygon[0]))
            values.append(f['properties'][field])
        pts = rings[0][0]
        x = sum(p[0] for p in pts[:-1]) / (len(pts)-1)
        y = sum(p[1] for p in pts[:-1]) / (len(pts)-1)
        value = f['properties'][field]
        label = f'{value:.1%}' if field == 'coverage_ratio' else f'{value:g}'
        ax.text(x, y, f'{f["properties"][key]}\n{label}', ha='center', va='center', color='#112c38')
    collection = PatchCollection(patches, cmap='YlGnBu', edgecolor='#385663', linewidth=1.2)
    collection.set_array(__import__('numpy').array(values))
    ax.add_collection(collection)
    selected_path = folder / 'selected.geojson'
    if selected_path.exists():
        pts = [f['geometry']['coordinates'] for f in json.loads(selected_path.read_text())['features']]
        if pts:
            ax.scatter([p[0] for p in pts], [p[1] for p in pts], s=35, c='#ef9a42', edgecolors='white', label='选中学校')
            ax.legend(loc='upper right')
    ax.autoscale_view()
    ax.set_aspect('equal')
    ax.set_xlabel('投影东坐标 / m')
    ax.set_ylabel('投影北坐标 / m')
    ax.ticklabel_format(style='plain', useOffset=False)
    ax.set_title(f'{task["id"]} · {task["title"]}', loc='left', fontsize=15, pad=15)
    ax.spines[['top', 'right']].set_visible(False)
    fig.colorbar(collection, ax=ax, shrink=.75, label='学校数量' if field == 'school_count' else '覆盖比例 (0—1)')
    fig.text(.5, .005, f'可手算构造数据 · {result["crs"]} · 非真实城市分布', ha='center', fontsize=9, color='#627980')
    fig.savefig(folder / 'map.png', dpi=180)
    plt.close(fig)
