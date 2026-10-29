"""相同工具定义用于 A/B/C。所有工具仅操作本会话图层句柄。"""


def tool(name, description, props, required):
    types = {}
    for k, desc in props.items():
        types[k] = {'type': 'number' if k == 'distance' else 'string', 'description': desc}
    return {'type': 'function', 'function': {'name': name, 'description': description,
            'parameters': {'type': 'object', 'properties': types,
                           'required': required, 'additionalProperties': False}}}


TOOLS = [
    tool('describe', '返回图层 CRS、单位、类型、字段及少量样本。省略 layer 查看全部图层。',
         {'layer': '会话图层句柄'}, []),
    tool('reproject', '调用 QGIS 转换坐标系，返回新图层句柄。',
         {'layer': '输入句柄', 'crs': '目标 CRS，如 EPSG:32650'}, ['layer', 'crs']),
    tool('filter', '属性字段等值筛选。',
         {'layer': '输入句柄', 'field': '实际字段名', 'value': '筛选值'}, ['layer', 'field', 'value']),
    tool('buffer', 'QGIS 平面缓冲，distance 按输入 CRS 的坐标单位解释；unit 仅接受 m。输入需为米制投影。',
         {'layer': '输入句柄', 'distance': '距离数值，0—100000', 'unit': 'm'}, ['layer', 'distance']),
    tool('select', '选择与 mask 相交的输入对象。多个 mask 命中不会复制同一记录；原始重复业务 ID 会保留。',
         {'layer': '待选图层', 'mask': '选择范围句柄'}, ['layer', 'mask']),
    tool('within_distance', '按真实平面几何距离选择任一 reference 的 distance 米以内对象，距离边界纳入。比近似多边形缓冲更适合严格距离阈值。输入均应为相同米制 CRS。',
         {'layer': '待选对象句柄', 'reference': '参考设施句柄', 'distance': '米制距离'},
         ['layer', 'reference', 'distance']),
    tool('deduplicate', '按业务 ID 去重，保留第一条记录。',
         {'layer': '输入句柄', 'key': '实际业务 ID 字段'}, ['layer', 'key']),
    tool('dissolve', '对全部输入几何求并集，移除重复覆盖；原属性可能丢失。', {'layer': '输入句柄'}, ['layer']),
    tool('intersection', '求交并保留两侧属性。输出会包含 overlay 的分区字段。',
         {'layer': '输入句柄', 'overlay': '叠加图层句柄'}, ['layer', 'overlay']),
    tool('assign_zones', '空间连接到分区，附加分区 ID。相交多个分区时可能一对多。',
         {'layer': '对象图层', 'zones': '分区图层', 'zone_key': '分区业务字段'}, ['layer', 'zones', 'zone_key']),
    tool('aggregate', '按 group_field 分组，count 计记录数或 sum 求 value_field 之和；未出现的分组不输出。',
         {'layer': '输入句柄', 'group_field': '分组字段', 'output_field': '新统计字段',
          'operation': 'count 或 sum', 'value_field': 'sum 必须提供数值字段'},
         ['layer', 'group_field', 'output_field', 'operation']),
    tool('left_join', '统计表左连接回全部分区，缺失值填 0。输入 table 必须每个分区仅一行。',
         {'zones': '全部分区句柄', 'table': '统计表句柄', 'zone_key': '共同业务字段',
          'value_field': '统计值字段', 'output_field': '可选重命名'},
         ['zones', 'table', 'zone_key', 'value_field']),
    tool('calculate', '新增面积或比例字段。area_m2 计算平面几何面积，输入需为米制；ratio 为 numerator/denominator。',
         {'layer': '输入句柄', 'field': '新字段', 'operation': 'area_m2 或 ratio',
          'numerator': 'ratio 分子字段', 'denominator': 'ratio 分母字段'}, ['layer', 'field', 'operation']),
    tool('finalize', '完成任务并导出分区 GeoPackage、CSV、GeoJSON 和地图。proximity 需 selected，coverage 建议 overlay。',
         {'zones': '最终统计分区句柄', 'selected': '最终去重学校句柄', 'overlay': '交叠图层句柄'}, ['zones']),
]
