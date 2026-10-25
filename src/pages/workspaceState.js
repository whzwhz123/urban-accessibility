export const WORKSPACE_KEY = 'urban-workspace-session-v1';
export const DEFAULT_VIS = { demand: false, isochrone: false, coverage: false, tci: false,
  lines: true, stops: true, composite: false, imbalance: false };
export const TAB_KEYS = ['1', 'demand', 'assignment', '2', '3'];

export function workspaceSnapshot({ city, demandRun, selectedRun, compositeRun, activeTab, vis, tciSession }) {
  return { schema: 1, city_id: city.id, input_version: city.custom_input_version || null,
    demand_id: demandRun?.run_id || null, assignment_id: selectedRun?.run_id || null,
    evaluation_id: compositeRun?.run_id || null, active_tab: TAB_KEYS.includes(activeTab) ? activeTab : '1',
    vis: Object.fromEntries(Object.keys(DEFAULT_VIS).map(key => [key, !!vis[key]])),
    tci_session: ['morning','midday','evening','snapshot'].includes(tciSession) ? tciSession : 'morning' };
}

export function readWorkspace(storage) {
  try { const value = JSON.parse(storage.getItem(WORKSPACE_KEY)); return value?.schema === 1 && typeof value.city_id === 'string' ? value : null; }
  catch { return null; }
}

export function restoredVisibility(saved, demand, evaluation) {
  const vis = { ...DEFAULT_VIS };
  for (const key of Object.keys(vis)) if (typeof saved?.vis?.[key] === 'boolean') vis[key] = saved.vis[key];
  if (!demand) ['demand','coverage','isochrone','tci','composite','imbalance'].forEach(key => { vis[key] = false; });
  else ['coverage','isochrone','tci','composite','imbalance'].forEach(key => {
    const available = key === 'imbalance' ? evaluation?.composite_ready : evaluation?.available_layers?.includes(key);
    if (!available) vis[key] = false;
  });
  return vis;
}
