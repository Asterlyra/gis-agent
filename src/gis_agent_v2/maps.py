import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.path import Path as MPath
from matplotlib.patches import PathPatch,Patch
from matplotlib.collections import LineCollection
from matplotlib.lines import Line2D
from shapely.geometry import shape
from shapely.geometry.polygon import orient
from shapely.ops import transform
from pyproj import Transformer
from .config import V2


def polygon(ax,g,**style):
    if g.geom_type in {'MultiPolygon','GeometryCollection'}:
        for part in g.geoms:polygon(ax,part,**style)
    elif g.geom_type=='Polygon':
        g=orient(g,sign=1);vertices=[];codes=[]
        for ring in [g.exterior,*g.interiors]:
            p=list(ring.coords);vertices+=p;codes+=[MPath.MOVETO]+[MPath.LINETO]*(len(p)-2)+[MPath.CLOSEPOLY]
        ax.add_patch(PathPatch(MPath(vertices,codes),**style))


def export_map(task,folder,result):
    for name in ['Microsoft YaHei','SimHei','PingFang SC','Arial Unicode MS','Noto Sans CJK SC']:
        if any(f.name==name for f in font_manager.fontManager.ttflist):plt.rcParams['font.family']=name;break
    folder=Path(folder);tx=Transformer.from_crs(4326,task['analysis_crs'],always_xy=True).transform
    fig,ax=plt.subplots(figsize=(12,8));fig.patch.set_facecolor('#f6f8fa');ax.set_facecolor('#f6f8fa')
    def read(path):return json.loads(path.read_text(encoding='utf-8'))['features']
    for f in read(V2/'data/regions'/task['region']/'display-water.geojson'):
        polygon(ax,transform(tx,shape(f['geometry'])),facecolor='#c5dfed',edgecolor='none',zorder=1)
    roads=[]
    for f in read(V2/'data/regions'/task['region']/'display-roads.geojson'):
        g=transform(tx,shape(f['geometry']));parts=g.geoms if g.geom_type=='MultiLineString' else [g]
        roads += [list(p.coords) for p in parts if p.geom_type=='LineString']
    ax.add_collection(LineCollection(roads,colors='#d3dbe1',linewidths=.5,zorder=2))
    zs=read(folder/'zones.geojson');geoms=[shape(f['geometry']) for f in zs]
    xmin=min(g.bounds[0] for g in geoms);ymin=min(g.bounds[1] for g in geoms)
    xmax=max(g.bounds[2] for g in geoms);ymax=max(g.bounds[3] for g in geoms)
    for g in geoms:polygon(ax,g,facecolor='none',edgecolor='#263f55',lw=1.4,zorder=5)
    overlay=folder/'overlay.geojson'
    if overlay.exists():
        for f in read(overlay):polygon(ax,shape(f['geometry']),facecolor='#d28a4b' if task['family']=='gaps' else '#4a997c',edgecolor='none',alpha=.85,zorder=3)
    if (folder/'selected.geojson').exists():
        from .config import ROOT
        all_schools=[shape(f['geometry']) for f in read(ROOT/task['inputs']['schools'])]
        ax.scatter([g.x for g in all_schools],[g.y for g in all_schools],s=13,c='#a9b5c1',zorder=4,label='输入学校')
        points=[shape(f['geometry']) for f in read(folder/'selected.geojson')]
        ax.scatter([g.x for g in points],[g.y for g in points],s=30,c='#d58b42',edgecolors='white',lw=.5,zorder=6,label='选中学校')
        ax.legend(loc='lower left')
    else:ax.legend(handles=[Patch(facecolor='#d28a4b' if task['family']=='gaps' else '#4a997c',label='未覆盖居住用地' if task['family']=='gaps' else '公园并集覆盖')],loc='lower left')
    pad=max(xmax-xmin,ymax-ymin)*.07
    ax.set_xlim(xmin-pad,xmax+pad);ax.set_ylim(ymin-pad,ymax+pad);ax.set_aspect('equal');ax.set_axis_off()
    fig.text(.06,.94,task['title'],fontsize=21,weight=600,color='#203c50')
    fig.text(.06,.897,result['summary'],fontsize=11,color='#587082')
    fig.text(.06,.035,'© OpenStreetMap contributors · Geofabrik 固定快照 · '+task['analysis_crs'],fontsize=9,color='#587082')
    fig.text(.06,.014,'真实空间成果；欧氏距离口径；数据完整性不等于现实设施完整性。',fontsize=9,color='#587082')
    scale=10**int(__import__('math').log10(max(100,(xmax-xmin)/4)))
    sx=xmax-scale;sy=ymin;ax.plot([sx,sx+scale],[sy,sy],lw=2,color='#203c50',zorder=9)
    ax.text(sx+scale/2,sy+pad*.35,f'{scale/1000:g} km',ha='center',fontsize=9,color='#203c50')
    ax.annotate('',xy=(.96,.95),xytext=(.96,.85),xycoords='axes fraction',arrowprops={'arrowstyle':'-|>','color':'#203c50'})
    ax.text(.96,.97,'GN',transform=ax.transAxes,ha='center',fontsize=9)
    fig.subplots_adjust(left=.04,right=.96,bottom=.065,top=.86)
    fig.savefig(folder/'map.png',dpi=140);plt.close(fig)
