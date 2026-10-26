import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import MainPage from './MainPage';
import { WORKSPACE_KEY, DEFAULT_VIS } from './workspaceState';

let mockSidebar;
jest.mock('../components/Map/AssignmentCompareMap', () => () => <div data-testid="comparison-map" />);
jest.mock('../components/Map/MapContainer', () => () => <div data-testid="evaluation-map" />);
jest.mock('../components/Map/CityPreviewMap', () => ({ run }) => <div data-testid="flow-map">{run?.run_id}</div>);
jest.mock('../components/Sidebar/SidebarPanel', () => props => { mockSidebar = props; return <div data-testid="sidebar">{props.city.name}</div>; });
const cities = [{ id: 'Beijing', name: '北京市', center: [116.4,39.9], has_evaluation: true }, { id: 'Linyi', name: '临沂市', center: [118.35,35.1] }];
const demand = { city_id: 'Linyi', run_id: 'd1', current_inputs: true };
const run = { city_id: 'Linyi', run_id: 'r1', demand_run_id: 'd1' };
beforeEach(() => { sessionStorage.clear(); mockSidebar = null; });
function api(current = true) {
  global.fetch = jest.fn(async url => ({ ok: true, json: async () => url === '/api/cities' ? { cities } : url.endsWith('/demand-runs') ? { runs: [{ ...demand, current_inputs: current }] } : { runs: [run] } }));
}
function stored(extra = {}) { sessionStorage.setItem(WORKSPACE_KEY, JSON.stringify({ schema: 1, city_id: 'Linyi', input_version: null,
  demand_id: 'd1', assignment_id: 'r1', active_tab: 'assignment', vis: DEFAULT_VIS, tci_session: 'midday', ...extra })); }
test('新窗口默认北京线网，不自动生成需求或启动任务', async () => {
  api(); render(<MainPage />); await screen.findByTestId('sidebar');
  expect(mockSidebar.city.id).toBe('Beijing'); expect(mockSidebar.vis.demand).toBe(false); expect(mockSidebar.vis.stops).toBe(true);
  expect(fetch.mock.calls.every(([,options]) => !options?.method)).toBe(true);
});
test('刷新恢复有效城市与实验版本，只发GET请求', async () => {
  api(); stored(); render(<MainPage />); await screen.findByTestId('sidebar');
  expect(mockSidebar.city.id).toBe('Linyi'); expect(mockSidebar.demandRun.run_id).toBe('d1'); expect(mockSidebar.selectedRun.run_id).toBe('r1');
  expect(mockSidebar.activeTab).toBe('assignment'); expect(fetch.mock.calls.every(([,options]) => !options?.method)).toBe(true);
});
test('原POI已变更时不恢复旧需求与旧客流', async () => {
  api(false); stored(); render(<MainPage />); await screen.findByTestId('sidebar');
  expect(mockSidebar.demandRun).toBeNull(); expect(mockSidebar.selectedRun).toBeNull();
  expect(mockSidebar.notice).toMatch(/已变化|不可用/);
});
test('城市源版本改变时丢弃旧结果引用', async () => {
  api(); stored({ input_version: 'obsolete' }); render(<MainPage />); await screen.findByTestId('sidebar');
  expect(mockSidebar.selectedRun).toBeNull(); expect(mockSidebar.demandRun).toBeNull();
  await waitFor(() => expect(JSON.parse(sessionStorage.getItem(WORKSPACE_KEY)).input_version).toBeNull());
});
