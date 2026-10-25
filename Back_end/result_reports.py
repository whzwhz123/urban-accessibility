"""Printable reports from saved results only; generation is explicitly requested.

GET .../report -> generate_report(data, city, identity, kind, comparison_ids=())
Serve returned Path as text/html; charset=utf-8. Does not invoke any solver.
"""
import base64
import csv
import json
import math
from collections import defaultdict
from html import escape
from pathlib import Path


def _read(folder, name):
    path = folder / name
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.is_file() else {}


def _text(value):
    if value is None:
        return '未记录'
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value)


def _table(mapping):
    return '<table>' + ''.join('<tr><th>' + escape(str(k)) + '</th><td><pre>' + escape(_text(v)) + '</pre></td></tr>' for k, v in mapping.items()) + '</table>'


def _number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError):
        return None


def _stages(folder, summary, parameters):
    path = folder / 'assignment_iterations.csv'
    stages = defaultdict(list)
    if path.is_file():
        with path.open(encoding='utf-8-sig', newline='') as stream:
            for row in csv.DictReader(stream):
                stages[row.get('column_round', '未记录阶段')].append(row)
    cap = _number(parameters.get('max_iterations'))
    tolerance = _number(parameters.get('relative_gap_tol'))
    result = {}
    for stage, rows in stages.items():
        last = rows[-1]
        iteration, gap = _number(last.get('iteration')), _number(last.get('relative_gap'))
        at_cap = iteration >= cap if iteration is not None and cap is not None else None
        met = gap <= tolerance if gap is not None and tolerance is not None else None
        result['MSA 阶段 ' + stage] = {'日志条数': len(rows), '最后迭代': iteration, '相对间隙': gap,
            '达到迭代上限': at_cap, '日志末值满足记录阈值': met,
            '说明': '触顶与满足阈值分别判断；日志末值不代替最终求解器判据。'}
    result['最终分配记录'] = {key: summary.get(key) for key in ('converged', 'relative_gap', 'assignment_iterations')}
    rounds = _number(summary.get('reallocation_rounds'))
    limit = _number(parameters.get('max_reallocation_rounds'))
    result['容量再分配'] = {'轮数': rounds, '记录的轮数上限': limit,
        '达到轮数上限': rounds >= limit if rounds is not None and limit is not None else None,
        '停止原因': summary.get('reallocation_stop_reason'),
        '说明': '历史摘要可能未保存停止原因/上限，不从当前代码默认值倒推；容量可行不等于严格容量约束用户均衡。'}
    path = folder / 'capacity_reallocation.csv'
    if path.is_file():
        with path.open(encoding='utf-8-sig', newline='') as stream:
            last = None
            for last in csv.DictReader(stream):
                pass
        result['容量再分配日志末行'] = last
    if not stages:
        result['阶段数据缺口'] = '没有分阶段迭代日志，不能确认各阶段触顶情况。'
    return result


def _bars(pairs, title):
    finite = [(str(label), value) for label, raw in pairs if (value := _number(raw)) is not None]
    if not finite:
        return '<p>没有可绘制的有限数值。</p>'
    maximum = max(abs(value) for _, value in finite) or 1
    height = 50 + 38 * len(finite)
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 {height}" role="img"><title>{escape(title)}</title>'
    for i, (label, value) in enumerate(finite):
        y = 25 + i * 38
        svg += f'<text x="5" y="{y+16}" font-size="13">{escape(label[:38])}</text><rect x="340" y="{y}" width="{abs(value)/maximum*400:.2f}" height="22" fill="#276b78"/><text x="750" y="{y+16}" font-size="13">{value:.5g}</text>'
    return svg + '</svg>'


def _distribution(folder):
    path = folder / 'physical_segment_flows.csv'
    values, field = [], 'served_flow_pax_per_hour'
    if path.is_file():
        with path.open(encoding='utf-8-sig', newline='') as stream:
            for row in csv.DictReader(stream):
                value = _number(row.get(field))
                if value is not None:
                    values.append(value)
    else:
        for name in ('composite.geojson', 'coverage_grid.geojson', 'opportunity_grid.geojson', 'tci_grid.geojson'):
            payload = _read(folder, name)
            features = payload.get('features', [])
            fields = [key for key in ('composite','coverage_score','opportunity_score','score','tci_snapshot','tci_midday','tci_morning','tci_evening') if any(_number(f.get('properties',{}).get(key)) is not None for f in features)]
            if fields:
                field = fields[0]
                values = [v for f in features if (v := _number(f.get('properties', {}).get(field))) is not None]
                break
    if not values:
        return '<p>缺少可绘制的结果数据，未虚构图像。</p>'
    low, high = min(values), max(values)
    if low == high:
        pairs = [(f'{low:.5g}', len(values))]
    else:
        counts = [0] * 8
        for value in values:
            counts[min(7, int((value-low)/(high-low)*8))] += 1
        pairs = [(f'{low+i*(high-low)/8:.4g} 至 {low+(i+1)*(high-low)/8:.4g}', count) for i, count in enumerate(counts)]
    title = field + ' 分布（纵列为区间，条长为记录数）'
    return '<h3>' + escape(title) + '</h3>' + _bars(pairs, title)


def render_report(data, city, identity, kind='assignment', *, comparison_ids=()):
    """Return self-contained HTML. Comparisons use explicit same-city run IDs only."""
    from .artifact_exports import run_folder
    folder = run_folder(data, city, identity, kind)
    summary, saved = _read(folder, 'summary.json'), _read(folder, 'parameters.json')
    parameters = {**saved, **(summary.get('parameters') or {})}
    scenario_keys = ('city', 'city_id', 'run_id', 'period', 'poi_count', 'hourly_demand_pax',
        'demand_run_id', 'shared_demand_signature', 'algorithm', 'source_scope', 'source_stats')
    frequency_keys = ('headway_basis', 'frequency_source', 'default_headway_min', 'assumed_headway_min',
        'headway_source_counts', 'headway_default_share', 'headway_upload_sha256', 'service_status_basis', 'routes_with_unknown_service_status')
    result_keys = ('served_demand_pax_per_hour', 'unserved_demand_pax_per_hour', 'served_demand_share',
        'relative_gap', 'converged', 'capacity_violation', 'demand_balance_error', 'grid_count', 'weights', 'available_layers')
    body = '<h1>公交线网结果报告</h1><p>' + escape(f'城市：{city}　runID：{identity}　类型：{kind}') + '</p>'
    body += '<p>这是已保存模型情景的结果汇总，不是实测精度验证；不将抽样需求或实时导航快照解释为全城历史实测。</p>'
    body += '<h2>研究情景与参数变量</h2>' + _table({k: summary.get(k) for k in scenario_keys})
    body += '<h3>历史参数快照</h3>' + _table({'parameters.json': saved, 'summary.parameters': summary.get('parameters'), 'workflow_parameters': summary.get('workflow_parameters')})
    body += '<p>摘要优先；缺失项标记为未记录，不填充当前默认参数。字段名保留原名以便追溯。</p>'
    body += '<h2>结果与单位</h2><p>pax_per_hour：人次/小时；share、relative_gap、V/C：无量纲。其余单位以原始参数定义为准。</p>' + _table({k: summary.get(k) for k in result_keys})
    body += '<h2>收敛阶段、触顶及未服务需求</h2>' + _table(_stages(folder, summary, parameters))
    body += '<h2>频率来源与数据缺口</h2>' + _table({k: summary.get(k, saved.get(k)) for k in frequency_keys})
    body += _table({'warnings': summary.get('warnings'), 'source_scope': summary.get('source_scope'), 'source_files（仅引用，不打包输入）': summary.get('source_files')})
    body += '<p>频率假设、经验补全与观测来源须区分。未记录来源不视为观测；缺失指标不填零。未服务需求是模型计算结果，不代表实测流失客流。</p>'
    body += '<h2>结果对比</h2>'
    runs = [summary]
    for other in dict.fromkeys(comparison_ids):
        if other != identity:
            runs.append(_read(run_folder(data, city, other, kind), 'summary.json'))
    if len(runs) == 1:
        body += '<p>未指定对比版本，未自动选择其他情景或历史结果。</p>'
    else:
        comparability = ('demand_run_id', 'shared_demand_signature', 'poi_count', 'hourly_demand_pax', 'algorithm', 'headway_basis', 'headway_upload_sha256', 'parameters')
        for row in runs[1:]:
            differences = [key for key in comparability if summary.get(key) is None or row.get(key) is None or summary.get(key) != row.get(key)]
            body += _table({'对比 runID': row.get('run_id'), '不一致或缺失的可比性字段': differences,
                '解释': '仅并列展示，不归因为单一因素，也不计算改善率。', '对比结果': {k: row.get(k) for k in result_keys}})
        for metric in ('served_demand_share', 'unserved_demand_pax_per_hour', 'relative_gap'):
            body += '<h3>' + escape(metric) + '</h3>' + _bars([(r.get('run_id', '未记录'), r.get(metric)) for r in runs], metric)
    body += '<h2>结果图</h2>'
    pictures = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in ('.png', '.jpg', '.jpeg'))
    for picture in pictures:
        mime = 'image/png' if picture.suffix.lower() == '.png' else 'image/jpeg'
        encoded = base64.b64encode(picture.read_bytes()).decode('ascii')
        body += '<figure><img alt="' + escape(picture.name, quote=True) + '" src="data:' + mime + ';base64,' + encoded + '"><figcaption>' + escape(picture.name) + '</figcaption></figure>'
    if not pictures:
        body += _distribution(folder)
    css = 'body{font:15px "Microsoft YaHei",sans-serif;max-width:1080px;margin:32px auto;color:#182c35;padding:0 20px}table{border-collapse:collapse;width:100%;margin:14px 0}th,td{border:1px solid #bbb;padding:8px;text-align:left;vertical-align:top}th{width:29%}pre{white-space:pre-wrap;overflow-wrap:anywhere;margin:0;font:inherit}img,svg{max-width:100%;height:auto}h2{border-bottom:2px solid #276b78;padding-bottom:6px}figure{margin:18px 0}@page{size:A4;margin:16mm}@media print{button{display:none}body{margin:0;padding:0;font-size:11px}tr,figure,svg{break-inside:avoid}h2,h3{break-after:avoid}}'
    return '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + escape(city + ' ' + identity + ' 结果报告') + '</title><style>' + css + '</style></head><body><button onclick="window.print()">打印 / 另存为 PDF</button>' + body + '</body></html>'


def generate_report(data, city, identity, kind='assignment', *, comparison_ids=()):
    """Generate only on user request; return a unique HTML Path for HTTP serving."""
    from .artifact_exports import export_directory
    html = render_report(data, city, identity, kind, comparison_ids=comparison_ids)
    output = export_directory(data, city, identity, kind) / 'report.html'
    output.write_text(html, encoding='utf-8')
    return output
