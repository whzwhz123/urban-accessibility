import React from 'react';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import CityAssignmentPanel from './CityAssignmentPanel';

// Test application state independently of Ant Design's Jest-incompatible package exports.
// Real Ant Design controls are covered by the isolated browser upload scenario.
jest.mock('antd', () => {
  const React = require('react');
  return {
    Alert: ({ message }) => <div>{message}</div>,
    Button: ({ children, onClick, disabled }) => <button onClick={onClick} disabled={disabled}>{children}</button>,
    InputNumber: ({ value, onChange, 'aria-label': label }) => <input aria-label={label} value={value ?? ''} onChange={e => onChange(Number(e.target.value))} />,
    Select: ({ options, value, onChange, 'aria-label': label }) => <select aria-label={label} value={value ?? ''} onChange={e => onChange(e.target.value)}>{options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}</select>,
  };
});

const city = { id: 'Fixture', name: '示例城市' };
const demandRun = { run_id: 'shared', parameters: { hourly_demands: { am_peak: 50000, pm_peak: 60000 } } };
const parameters = { paths_per_transfer: 3, access_station_count: 2, max_walk_distance_m: 700,
  min_od_distance_km: 1, max_od_distance_km: 20, max_transfers: 1, transfer_penalty_min: 3,
  bus_speed_kmh: 16, vehicle_capacity: 65, max_iterations: 40, relative_gap_tol: 1e-3 };

beforeEach(() => {
  window.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} });
  global.fetch = jest.fn(async url => ({ ok: true, json: async () => url.includes('/inputs') ? { network: {}, poi: {} } : url.includes('/jobs') ? { jobs: [] } : { runs: [] } }));
});

test('沿用历史参数时保留历史总需求，不被时段默认值覆盖', async () => {
  const run = { city_id: 'Fixture', run_id: 'historical', period: 'am_peak', poi_count: 2,
    hourly_demand_pax: 120, default_headway_min: 17, parameters };
  render(<CityAssignmentPanel city={city} demandRun={demandRun} selectedRun={run} active onRunSelect={() => {}} />);
  await waitFor(() => expect(screen.getByLabelText('城市总公交需求（人次/小时）').value).toBe('60000'));
  fireEvent.click(screen.getByText('沿用本次参数创建新实验'));
  await waitFor(() => expect(screen.getByLabelText('城市总公交需求（人次/小时）').value).toBe('120'));
  expect(screen.getByLabelText('公交速度（公里/小时）').value).toBe('16');
  expect(screen.getByLabelText('最大步行距离（公里）').value).toBe('0.7');
  expect(screen.getByLabelText('缺失线路默认发车间隔（分钟）').value).toBe('17');
});

test('历史缺失字段不补默认值、不误报用户上传或未收敛', async () => {
  render(<CityAssignmentPanel city={city} demandRun={demandRun} selectedRun={{ city_id: 'Fixture', run_id: 'old' }} onRunSelect={() => {}} />);
  await act(async () => { await Promise.resolve(); });
  expect(screen.getByText('频率来源：未记录')).toBeTruthy();
  expect(screen.getByText(/分配收敛：未记录/)).toBeTruthy();
  expect(screen.queryByText('用户提供的逐线路发车间隔')).toBeNull();
  expect(screen.getByText('沿用本次参数创建新实验').closest('button').disabled).toBe(true);
});

const response = value => ({ ok: true, json: async () => value });
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const fixtureRun = { city_id: 'Fixture', run_id: 'historical', demand_run_id: 'other', period: 'am_peak',
  poi_count: 2, hourly_demand_pax: 120, default_headway_min: 17, parameters };
const bounds = { upper_bounds: { ordered_od_pairs: 2, candidate_paths: 9, path_flow_float64_bytes: 72 } };
const mockApi = ({ runs = [], jobs = [], job = null, handle } = {}) => {
  global.fetch = jest.fn((url, options) => {
    const custom = handle?.(url, options);
    if (custom) return custom;
    return Promise.resolve(response(url.includes('/inputs') ? { network: {}, poi: {} }
      : url.includes('/jobs?') ? { jobs } : { runs, job }));
  });
};
afterEach(() => { jest.useRealTimers(); });

test('完整显示任务状态和参数，按是否记录run_id区分重试与恢复', async () => {
  const jobs = ['running', 'completed', 'failed', 'interrupted', 'cancelled'].map((status, i) => ({
    id: 'job' + i, kind: 'assignment', status, run_id: i > 2 ? 'checkpoint' : null,
    options: { demand_run_id: 'shared', period: 'am_peak', poi_count: 2, total_demand: 120, parameters },
  }));
  mockApi({ jobs });
  render(<CityAssignmentPanel city={city} demandRun={demandRun} onRunSelect={jest.fn()} />);
  await screen.findByText(/任务 job0：运行中/);
  for (const text of ['已完成', '失败', '中断', '已停止']) expect(screen.getByText(new RegExp('任务 job.*：' + text))).toBeTruthy();
  expect(screen.getAllByText('按原参数重试')[0].disabled).toBe(true);
  expect(screen.getAllByText('恢复断点任务')).toHaveLength(2);
  expect(screen.getAllByText('公交速度（公里/小时）：16')).toHaveLength(5);
});

test('没有run_id的失败任务也调用resume，且不带当前表单参数', async () => {
  const row = { id: 'failed-job', kind: 'assignment', status: 'failed', options: { demand_run_id: 'shared' } };
  mockApi({ jobs: [row], handle: url => url.endsWith('/resume') ? Promise.resolve(response({ ...row, status: 'queued' })) : null });
  render(<CityAssignmentPanel city={city} demandRun={demandRun} onRunSelect={jest.fn()} />);
  fireEvent.click(await screen.findByText('按原参数重试'));
  await screen.findByText(/已提交原任务选项/);
  expect(fetch).toHaveBeenCalledWith('/api/jobs/failed-job/resume', { method: 'POST' });
});

test('停止请求不误报已停止，等待安全边界', async () => {
  const row = { id: 'running-job', kind: 'assignment', status: 'running', options: { demand_run_id: 'shared' } };
  mockApi({ jobs: [row], handle: url => url.endsWith('/cancel') ? Promise.resolve(response({ ...row, cancel_requested: true })) : null });
  render(<CityAssignmentPanel city={city} demandRun={demandRun} onRunSelect={jest.fn()} />);
  fireEvent.click(await screen.findByText('停止任务并保留进度'));
  await screen.findByText(/已请求停止；需等待安全边界/);
  expect(screen.getByText('等待安全停止').disabled).toBe(true);
  expect(fetch).toHaveBeenCalledWith('/api/jobs/running-job/cancel', { method: 'POST' });
});

test('历史实验需求不同仍能查看详情，并可切换需求后显示客流图', async () => {
  const onRunSelect = jest.fn(); const onDemandChange = jest.fn();
  const summary = { ...demandRun, run_id: 'other' };
  mockApi({ runs: [fixtureRun], handle: url => url.endsWith('/summary') ? Promise.resolve(response(summary)) : null });
  const props = { city, demandRun, onRunSelect, onDemandChange };
  const view = render(<CityAssignmentPanel {...props} />);
  await screen.findByLabelText('已有实验');
  fireEvent.change(screen.getByLabelText('已有实验'), { target: { value: 'historical' } });
  expect(screen.getByText(/已打开历史实验详情/)).toBeTruthy();
  expect(screen.getByText('下载完整实验包（ZIP）').getAttribute('href')).toContain('/historical/export');
  expect(onRunSelect).toHaveBeenLastCalledWith(null);
  fireEvent.click(screen.getByText('切换历史需求并显示客流图'));
  await waitFor(() => expect(onDemandChange).toHaveBeenCalledWith(summary));
  view.rerender(<CityAssignmentPanel {...props} demandRun={summary} />);
  await waitFor(() => expect(onRunSelect).toHaveBeenLastCalledWith(fixtureRun));
});

test('历史需求读取失败可见，不改变当前需求', async () => {
  const onDemandChange = jest.fn();
  mockApi({ runs: [fixtureRun], handle: url => url.endsWith('/summary') ? Promise.resolve({ ok: false, json: async () => ({ error: '需求版本不存在' }) }) : null });
  render(<CityAssignmentPanel city={city} demandRun={demandRun} onDemandChange={onDemandChange} onRunSelect={jest.fn()} />);
  fireEvent.change(await screen.findByLabelText('已有实验'), { target: { value: 'historical' } });
  fireEvent.click(screen.getByText('切换历史需求并显示客流图'));
  await screen.findByText('无法切换历史需求：需求版本不存在');
  expect(onDemandChange).not.toHaveBeenCalled();
});

test('城市切换后迟到的任务历史与实验列表不回写', async () => {
  const oldHistory = deferred(); const oldRuns = deferred();
  mockApi({ handle: url => url === '/api/jobs?city_id=Fixture' ? oldHistory.promise : url === '/api/cities/Fixture/runs' ? oldRuns.promise : null });
  const props = { demandRun, onRunSelect: jest.fn() };
  const view = render(<CityAssignmentPanel {...props} city={city} />);
  view.rerender(<CityAssignmentPanel {...props} city={{ id: 'Next' }} />);
  await act(async () => {
    oldHistory.resolve(response({ jobs: [{ id: 'stale-job', kind: 'assignment', status: 'failed' }] }));
    oldRuns.resolve(response({ runs: [fixtureRun] }));
  });
  expect(screen.queryByText(/stale-job/)).toBeNull();
  expect(screen.queryByLabelText('已有实验')).toBeNull();
  expect(props.onRunSelect).not.toHaveBeenCalled();
});

test('需求切换后旧预检响应不会继续启动求解，即使切回原需求', async () => {
  const check = deferred();
  mockApi({ handle: url => url.endsWith('/assignment-preflight') ? check.promise : null });
  const props = { city, onRunSelect: jest.fn() };
  const view = render(<CityAssignmentPanel {...props} demandRun={demandRun} />);
  await waitFor(() => expect(screen.getByText('开始交通分配').disabled).toBe(false));
  fireEvent.click(screen.getByText('开始交通分配'));
  view.rerender(<CityAssignmentPanel {...props} demandRun={{ ...demandRun, run_id: 'next' }} />);
  view.rerender(<CityAssignmentPanel {...props} demandRun={demandRun} />);
  await act(async () => { check.resolve(response(bounds)); });
  expect(fetch.mock.calls.some(([url]) => url.endsWith('/assignments'))).toBe(false);
  expect(screen.queryByText(/理论OD上界/)).toBeNull();
});

test('启动返回其他需求的既有202任务时明确提示，不加载其结果', async () => {
  const existing = { id: 'existing', status: 'running', options: { demand_run_id: 'other' } };
  const onRunSelect = jest.fn();
  mockApi({ handle: url => url.endsWith('/assignment-preflight') ? Promise.resolve(response(bounds))
    : url.endsWith('/assignments') ? Promise.resolve({ ...response(existing), status: 202 }) : null });
  render(<CityAssignmentPanel city={city} demandRun={demandRun} onRunSelect={onRunSelect} />);
  await waitFor(() => expect(screen.getByText('开始交通分配').disabled).toBe(false));
  fireEvent.click(screen.getByText('开始交通分配'));
  await screen.findByText(/服务器返回了本市既有任务/);
  expect(onRunSelect).not.toHaveBeenCalled();
});

test('不同需求的运行任务完成后只加入历史，不覆盖当前地图', async () => {
  jest.useFakeTimers();
  const job = { id: 'other-task', status: 'running', options: { demand_run_id: 'other' } };
  const onRunSelect = jest.fn();
  mockApi({ job, handle: url => url === '/api/jobs/other-task' ? Promise.resolve(response({ ...job, status: 'completed', result: fixtureRun })) : null });
  render(<CityAssignmentPanel city={city} demandRun={demandRun} onRunSelect={onRunSelect} />);
  await act(async () => { await Promise.resolve(); });
  expect(screen.getByText(/本市既有分配任务使用不同/)).toBeTruthy();
  await act(async () => { jest.advanceTimersByTime(1500); });
  expect(onRunSelect).not.toHaveBeenCalled();
  expect(screen.getByText(/任务已完成，但需求版本不同/)).toBeTruthy();
  expect(screen.getByLabelText('已有实验')).toBeTruthy();
});

test('未收敛与未服务需求有语义说明，三时段缺失条件可见', async () => {
  render(<CityAssignmentPanel city={city} demandRun={demandRun} selectedRun={{ ...fixtureRun, converged: 'no', unserved_demand_pax_per_hour: 30 }} onRunSelect={jest.fn()} />);
  await act(async () => { await Promise.resolve(); });
  expect(screen.getByText(/不应当作已收敛结果/)).toBeTruthy();
  expect(screen.getByText(/不代表观测到的拒载人数/)).toBeTruthy();
  expect(screen.getByText(/早高峰：缺少实验；平峰：缺少实验；晚高峰：缺少实验/)).toBeTruthy();
  expect(screen.getByText(/条件不齐或历史字段缺失/)).toBeTruthy();
});

test('恢复请求在切换城市后返回时不污染新城市任务', async () => {
  const resume = deferred();
  const row = { id: 'old-failed', kind: 'assignment', status: 'failed', options: {} };
  mockApi({ handle: url => url === '/api/jobs?city_id=Fixture' ? Promise.resolve(response({ jobs: [row] }))
    : url.endsWith('/resume') ? resume.promise : null });
  const props = { demandRun, onRunSelect: jest.fn() };
  const view = render(<CityAssignmentPanel {...props} city={city} />);
  fireEvent.click(await screen.findByText('按原参数重试'));
  view.rerender(<CityAssignmentPanel {...props} city={{ id: 'Next' }} />);
  await act(async () => { resume.resolve(response({ ...row, status: 'running' })); });
  expect(screen.queryByText(/已提交原任务选项/)).toBeNull();
  expect(screen.queryByText('正在计算')).toBeNull();
});

test('旧需求的在途轮询响应在需求切换后不回写地图', async () => {
  jest.useFakeTimers();
  const poll = deferred();
  const job = { id: 'polling', status: 'running', options: { demand_run_id: 'shared' } };
  mockApi({ job, handle: url => url === '/api/jobs/polling' ? poll.promise : null });
  const onRunSelect = jest.fn();
  const props = { city, demandRun, onRunSelect };
  const view = render(<CityAssignmentPanel {...props} />);
  await act(async () => { await Promise.resolve(); });
  await act(async () => { jest.advanceTimersByTime(1500); });
  view.rerender(<CityAssignmentPanel {...props} demandRun={{ ...demandRun, run_id: 'next' }} />);
  await act(async () => { poll.resolve(response({ ...job, status: 'completed', result: { ...fixtureRun, demand_run_id: 'shared' } })); });
  expect(onRunSelect).not.toHaveBeenCalled();
  expect(screen.queryByLabelText('已有实验')).toBeNull();
});

test('独立规模检查在需求切换后不显示旧预检结果', async () => {
  const check = deferred();
  mockApi({ handle: url => url.endsWith('/assignment-preflight') ? check.promise : null });
  const props = { city, demandRun, onRunSelect: jest.fn() };
  const view = render(<CityAssignmentPanel {...props} />);
  await waitFor(() => expect(screen.getByText('检查计算规模').disabled).toBe(false));
  fireEvent.click(screen.getByText('检查计算规模'));
  view.rerender(<CityAssignmentPanel {...props} demandRun={{ ...demandRun, run_id: 'next' }} />);
  await act(async () => { check.resolve(response(bounds)); });
  expect(screen.queryByText(/理论OD上界/)).toBeNull();
});
