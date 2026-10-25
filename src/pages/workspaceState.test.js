import { WORKSPACE_KEY, DEFAULT_VIS, workspaceSnapshot, readWorkspace, restoredVisibility } from './workspaceState';

test('会话只保存白名单版本引用，不保存凭证、POI或完整结果', () => {
  const snapshot = workspaceSnapshot({ city: { id: 'Linyi', credentials: 'SECRET' },
    demandRun: { run_id: 'demand', poi: [1,2], key: 'SECRET' }, selectedRun: { run_id: 'run' },
    compositeRun: null, activeTab: 'assignment', vis: DEFAULT_VIS, tciSession: 'midday' });
  expect(snapshot.city_id).toBe('Linyi'); expect(snapshot.assignment_id).toBe('run');
  expect(JSON.stringify(snapshot)).not.toMatch(/SECRET|credentials|poi/);
});
test('损坏或旧格式存储不阻止打开软件', () => {
  expect(readWorkspace({ getItem: () => '{bad' })).toBeNull();
  expect(readWorkspace({ getItem: () => JSON.stringify({ schema: 0 }) })).toBeNull();
  expect(readWorkspace({ getItem: key => key === WORKSPACE_KEY ? JSON.stringify({ schema: 1, city_id: 'Linyi' }) : null })).toEqual({ schema: 1, city_id: 'Linyi' });
});
test('失效需求或缺失指标不能恢复出虚假的专题图', () => {
  const saved = { vis: { ...DEFAULT_VIS, demand: true, composite: true, tci: true } };
  expect(restoredVisibility(saved, null, null).demand).toBe(false);
  const restored = restoredVisibility(saved, {}, { available_layers: ['demand','coverage'], composite_ready: false });
  expect(restored.demand).toBe(true); expect(restored.composite).toBe(false); expect(restored.tci).toBe(false);
});
