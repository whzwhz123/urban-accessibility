import React, { useState } from 'react';

const escapeHtml = value => String(value ?? '未记录').replace(/[&<>"']/g, char => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[char]));
const filename = value => String(value || 'result').replace(/[^\w\u3400-\u9fff.-]/g, '_');

function saveBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  link.click();
  // Delay revocation until the browser has consumed the download URL.
  window.setTimeout(() => URL.revokeObjectURL(url), 60000);
}

/** canvas: HTMLCanvasElement, React ref, or synchronous () => HTMLCanvasElement.
 * metadata: {city, runId, title, units, legendMin, legendMax, legendLabel,
 * coordinateSystem, legendItems?: [{label, color}]}.
 * The map owner must configure preserveDrawingBuffer or pass a freshly rendered
 * canvas. HTMLCanvasElement.toDataURL cannot detect a cleared WebGL buffer.
 * All export work occurs in user click handlers; no map-package dependency.
 */
export function ImageExportControls({ canvas, metadata = {}, disabled = false }) {
  const [error, setError] = useState('');
  const getImage = () => {
    const element = typeof canvas === 'function' ? canvas() : (canvas?.current || canvas);
    if (!element || !element.width || !element.height || typeof element.toDataURL !== 'function') {
      throw new Error('地图画布尚未准备好');
    }
    // Cross-origin/tainted canvas throws here and is shown to the user.
    const image = element.toDataURL('image/png');
    if (!image.startsWith('data:image/png')) throw new Error('无法读取地图图像');
    return image;
  };
  const details = () => [
    ['城市', metadata.city], ['runID', metadata.runId], ['单位', metadata.units],
    ['图例', metadata.legendLabel],
    ['图例范围', `${metadata.legendMin ?? '未记录'} 至 ${metadata.legendMax ?? '未记录'}`],
    ['坐标系', metadata.coordinateSystem],
  ];
  const png = async () => {
    setError('');
    try {
      const image = getImage();
      const bitmap = new Image();
      bitmap.src = image;
      await bitmap.decode();
      const exportCanvas = document.createElement('canvas');
      exportCanvas.width = Math.max(bitmap.naturalWidth, 960);
      const lines = details().map(([key, value]) => `${key}：${value ?? '未记录'}`);
      const legend = (metadata.legendItems || []).map(item => item.label);
      const rows = [metadata.title || '公交线网结果地图', ...lines, ...legend];
      // Wrap long identities/metadata rather than silently clipping the PNG footer.
      const context = exportCanvas.getContext('2d');
      context.font = '20px "Microsoft YaHei", sans-serif';
      const wrapped = [];
      for (const row of rows) {
        let line = '';
        for (const char of String(row)) {
          if (context.measureText(line + char).width > exportCanvas.width - 40) {
            wrapped.push(line); line = '';
          }
          line += char;
        }
        wrapped.push(line);
      }
      const mapHeight = Math.round(bitmap.naturalHeight * exportCanvas.width / bitmap.naturalWidth);
      exportCanvas.height = mapHeight + 40 + wrapped.length * 30;
      context.fillStyle = '#fff';
      context.fillRect(0, 0, exportCanvas.width, exportCanvas.height);
      context.drawImage(bitmap, 0, 0, exportCanvas.width, mapHeight);
      context.fillStyle = '#172c35';
      context.font = '20px "Microsoft YaHei", sans-serif';
      wrapped.forEach((line, index) => context.fillText(line, 20, mapHeight + 30 + index * 30));
      exportCanvas.toBlob(blob => {
        if (blob) saveBlob(blob, `${filename(metadata.city)}_${filename(metadata.runId)}_map.png`);
        else setError('PNG 编码失败');
      }, 'image/png');
    } catch (failure) { setError(failure.message); }
  };
  const print = () => {
    setError('');
    let popup;
    try {
      const image = getImage();
      popup = window.open('', '_blank');
      if (!popup) throw new Error('打印窗口被拦截，请允许弹出窗口');
      const legends = (metadata.legendItems || []).map(item => {
        // Only accept simple CSS colors, never interpolate arbitrary style markup.
        const color = /^(#[0-9a-f]{3,8}|[a-z]+)$/i.test(item.color || '') ? item.color : '#777';
        return `<span style="display:inline-block;margin:5px 16px 5px 0"><i style="display:inline-block;width:16px;height:12px;background:${color}"></i> ${escapeHtml(item.label)}</span>`;
      }).join('');
      popup.document.open();
      popup.document.write(`<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>${escapeHtml(metadata.city)} ${escapeHtml(metadata.runId)} 地图</title><style>@page{size:A4 landscape;margin:12mm}body{font:14px "Microsoft YaHei",sans-serif;color:#172c35;margin:24px}img{display:block;max-width:100%;max-height:68vh;object-fit:contain;margin:auto}dl{display:flex;flex-wrap:wrap;gap:10px 24px}dt{font-weight:bold}dd{margin:0;overflow-wrap:anywhere}h1{font-size:22px}@media print{button{display:none}body{margin:0}img{max-height:65vh}}</style></head><body><button onclick="window.print()">打印 / 另存为 PDF</button><h1>${escapeHtml(metadata.title || '公交线网结果地图')}</h1><img src="${image}" alt="地图截图"><dl>${details().map(([key, value]) => `<div><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd></div>`).join('')}</dl><div>${legends}</div><p>图像来自当前地图画布；图层与显示范围由地图页面决定。</p></body></html>`);
      popup.document.close();
      popup.opener = null;
      // User prints after the screenshot is visibly loaded, avoiding blank PDFs.
    } catch (failure) {
      if (popup) popup.close();
      setError(failure.message);
    }
  };
  return <section aria-label="地图导出">
    <button type="button" disabled={disabled} onClick={png}>导出地图 PNG</button>{' '}
    <button type="button" disabled={disabled} onClick={print}>地图打印 / PDF</button>
    <small style={{ display: 'block' }}>导出当前地图范围和图层，并附带城市、版本与图例信息。</small>
    {error && <p role="alert">{error}</p>}
  </section>;
}

/** Standalone integration; no existing panel is modified.
 * city: ID string or {id, name}; kind: assignment|evaluation; runId: saved ID.
 * Route contract: GET {baseUrl}/export?format=zip|gpkg|shp&report=1
 * GET {baseUrl}/report?compare=runId1,runId2 (server parses IDs).
 * baseUrl may override the default API path. Server must validate city/run IDs.
 * report is only included in ZIP/SHP when the user checks the option.
 */
export default function ResultExports({ city, runId, kind = 'assignment', baseUrl,
  comparisonIds = [], canvas, metadata = {} }) {
  const [includeReport, setIncludeReport] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const cityId = typeof city === 'string' ? city : city?.id;
  const validKind = ['assignment', 'evaluation'].includes(kind);
  const ready = Boolean(cityId && runId && validKind);
  const base = baseUrl || `/api/cities/${encodeURIComponent(cityId || '')}/${kind === 'evaluation' ? 'composite-runs' : 'runs'}/${encodeURIComponent(runId || '')}`;
  const query = () => {
    const params = new URLSearchParams();
    if (comparisonIds.length) params.set('compare', comparisonIds.join(','));
    return params;
  };
  const download = async format => {
    setBusy(true); setError('');
    try {
      const params = query();
      params.set('format', format);
      if (includeReport && format !== 'gpkg') params.set('report', '1');
      const response = await fetch(`${base}/export?${params}`);
      if (!response.ok) {
        const message = await response.text();
        throw new Error(`下载失败 (${response.status})：${message.slice(0, 400)}`);
      }
      const blob = await response.blob();
      saveBlob(blob, `${filename(cityId)}_${filename(runId)}_${format}.${format === 'gpkg' ? 'gpkg' : 'zip'}`);
    } catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  };
  return <section aria-label="结果导出">
    <h3>结果导出</h3>
    <label><input type="checkbox" checked={includeReport} onChange={event => setIncludeReport(event.target.checked)} /> ZIP 附书面报告</label>
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, margin: '10px 0' }}>
      {[['zip', '完整结果 ZIP'], ['gpkg', 'GeoPackage'], ['shp', 'SHP ZIP']].map(([format, label]) =>
        <button type="button" key={format} disabled={!ready || busy} onClick={() => download(format)}>{label}</button>)}
      <a href={ready ? `${base}/report?${query()}` : undefined} aria-disabled={!ready} target="_blank" rel="noopener noreferrer">书面报告 / 打印 PDF</a>
    </div>
    <small>仅下载已保存结果，不启动计算；ZIP 不包含源数据或模型检查点。GeoPackage 单文件不附报告。</small>
    {error && <p role="alert">{error}</p>}
    {canvas && <ImageExportControls canvas={canvas} disabled={!ready} metadata={{
      ...metadata, city: metadata.city ?? (typeof city === 'string' ? city : city?.name || cityId), runId,
    }} />}
  </section>;
}
