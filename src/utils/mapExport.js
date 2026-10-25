/** Compose currently visible canvas layers in a map container into one canvas.
 * Usage: <ImageExportControls canvas={() => composeMapCanvas(containerRef.current)} ... />
 * Caller owns render timing/preserveDrawingBuffer. A cleared WebGL buffer cannot
 * be recovered here. HTML labels/controls are not rasterized (metadata is separate).
 * Axis-aligned bounds only; rotated/skewed canvas elements are rejected.
 */
export function composeMapCanvas(containerOrRef, { pixelRatio = window.devicePixelRatio || 1, background = '#ffffff' } = {}) {
  const container = containerOrRef?.current || containerOrRef;
  if (!container?.getBoundingClientRect) throw new Error('地图容器尚未准备好');
  const bounds = container.getBoundingClientRect();
  if (!bounds.width || !bounds.height || !Number.isFinite(pixelRatio) || pixelRatio <= 0) throw new Error('地图容器尺寸或像素比例无效');
  const output = document.createElement('canvas');
  output.width = Math.ceil(bounds.width * pixelRatio);
  output.height = Math.ceil(bounds.height * pixelRatio);
  const context = output.getContext('2d');
  if (!context) throw new Error('无法创建截图画布');
  context.fillStyle = background;
  context.fillRect(0, 0, output.width, output.height);
  context.scale(pixelRatio, pixelRatio);
  const layers = [];
  // Compute effective visibility/opacity including wrappers; ordering uses stacking
  // z-index paths, so the basemap is drawn before the DeckGL overlay.
  container.querySelectorAll('canvas').forEach((canvas, order) => {
    let element = canvas, opacity = 1;
    const stacking = [];
    while (element) {
      const style = window.getComputedStyle(element);
      if (style.display === 'none' || style.visibility === 'hidden') return;
      opacity *= Number(style.opacity || 1);
      if (style.transform && style.transform !== 'none') {
        const matrix = new DOMMatrixReadOnly(style.transform);
        if (matrix.b !== 0 || matrix.c !== 0 || !matrix.is2D) throw new Error('截图不支持旋转、倾斜或透视画布');
      }
      if (style.zIndex !== 'auto') stacking.unshift(Number(style.zIndex) || 0);
      if (element === container) break;
      element = element.parentElement;
    }
    const rect = canvas.getBoundingClientRect();
    if (opacity <= 0 || !canvas.width || !canvas.height || !rect.width || !rect.height) return;
    if (rect.right <= bounds.left || rect.left >= bounds.right || rect.bottom <= bounds.top || rect.top >= bounds.bottom) return;
    layers.push({ canvas, rect, opacity, stacking, order });
  });
  if (!layers.length) throw new Error('容器内没有可截图的可见画布');
  layers.sort((a, b) => {
    for (let i = 0; i < Math.max(a.stacking.length, b.stacking.length); i += 1) {
      const delta = (a.stacking[i] || 0) - (b.stacking[i] || 0);
      if (delta) return delta;
    }
    return a.order - b.order;
  });
  layers.forEach(({ canvas, rect, opacity }) => {
    context.globalAlpha = opacity;
    context.drawImage(canvas, rect.left-bounds.left, rect.top-bounds.top, rect.width, rect.height);
  });
  context.globalAlpha = 1;
  // Fail explicitly for cross-origin-tainted source canvases instead of returning
  // a canvas that later fails silently. Does not start a map render or fetch.
  try { context.getImageData(0, 0, 1, 1); }
  catch { throw new Error('截图被跨域画布限制，请检查底图 CORS 设置'); }
  return output;
}

// Synchronous public integration name; preserve the existing export for callers.
export const captureMap = composeMapCanvas;

export default composeMapCanvas;

