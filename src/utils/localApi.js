export async function readJson(response) {
  let value;
  try { value = await response.json(); }
  catch { throw new Error(`本地服务返回了无效响应（HTTP ${response.status || '未知'}），请检查服务并重试。`); }
  if (!response.ok) throw new Error(value.error || `请求失败（HTTP ${response.status}）`);
  return value;
}
