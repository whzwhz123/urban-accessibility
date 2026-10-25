import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import AmapCollectionPanel from './AmapCollectionPanel';

const id = 'a'.repeat(32);
const plan = { city_id: 'Fixture', demand_run_id: 'demand', kind: 'tci', session: 'morning',
  points: [{ pair_id: 'a' }, { pair_id: 'b' }], minutes: [15, 30], request_budget: 60,
  interval_ms: 1000, destinations_per_origin: 2, planned_requests: 4 };
const job = { id, city_id: 'Fixture', demand_run_id: 'demand', kind: 'tci', status: 'paused',
  records: 2, valid_records: 0, no_route_records: 1, failed_records: 1, completed_keys: ['a'], plan };
const props = { city: { id: 'Fixture', name: '示例城市' }, demandRun: { run_id: 'demand' },
  kind: 'tci', minutes: 90, method: 'cumulative', credentials: { key: 'memory-key', security: 'memory-security' },
  onCollected: jest.fn(), onSessionChange: jest.fn(), onBusyChange: jest.fn() };
const response = value => ({ ok: true, json: async () => value });
const choose = async () => {
  await waitFor(() => expect(screen.getByLabelText('恢复采集任务').options.length).toBe(2));
  fireEvent.change(screen.getByLabelText('恢复采集任务'), { target: { value: id } });
};
const send = (type, value) => {
  const frame = screen.getByTitle('隔离的高德采集服务');
  fireEvent(window, new MessageEvent('message', { origin: window.location.origin,
    source: frame.contentWindow, data: { channel: 'urban-amap', type, value } }));
};

beforeEach(() => {
  jest.useFakeTimers().setSystemTime(new Date('2026-10-21T04:00:00Z')); // Shanghai midday.
  jest.clearAllMocks();
  global.fetch = jest.fn(async url => response(url.endsWith('/collection-runs') ? { runs: [job] } : job));
});
afterEach(() => jest.useRealTimers());

test('只读展示计划和历史，页面挂载不创建任务或启动SDK', async () => {
  render(<AmapCollectionPanel {...props} />);
  await choose();
  const preview = screen.getByLabelText('恢复有效计划');
  expect(preview.textContent).toContain('早高峰');
  expect(preview.textContent).toContain('30 分钟');
  expect(preview.textContent).toContain('分时段');
  expect(preview.textContent).toContain('预算 60');
  expect(screen.getByLabelText('采集历史').textContent).toContain('成功 0 / 无法到达 1 / 失败 1');
  expect(screen.getByLabelText('采集时段').disabled).toBe(true);
  expect(screen.getByTitle('隔离的高德采集服务').getAttribute('srcdoc')).toBeNull();
  expect(global.fetch.mock.calls.every(([, options]) => !options?.method)).toBe(true);
});

test('恢复按保存时段拒绝启动，不受表单实时快照影响', async () => {
  render(<AmapCollectionPanel {...props} />);
  await choose();
  expect(screen.getByLabelText('采集时段').value).toBe('snapshot');
  fireEvent.click(screen.getByText('恢复保存计划'));
  await waitFor(() => expect(screen.getByText(/当前时间不属于保存计划时段/)).toBeTruthy());
  expect(screen.getByTitle('隔离的高德采集服务').getAttribute('srcdoc')).toBeNull();
});

test('等待中停止，随后到达保存时段也不启动采集', async () => {
  render(<AmapCollectionPanel {...props} />);
  await choose();
  fireEvent.click(screen.getByLabelText('等待实际时段采集（保持页面打开）'));
  fireEvent.click(screen.getByText('恢复保存计划'));
  await waitFor(() => expect(screen.getByText(/等待保存计划时段/)).toBeTruthy());
  fireEvent.click(screen.getByText('停止并保存'));
  await act(async () => { jest.setSystemTime(new Date('2026-10-22T00:00:00Z')); jest.advanceTimersByTime(20000); });
  expect(screen.getByTitle('隔离的高德采集服务').getAttribute('srcdoc')).toBeNull();
  expect(screen.getByText('恢复保存计划').disabled).toBe(false);
});

test('恢复发送原计划参数，停止后仍接收服务端暂停结果', async () => {
  jest.setSystemTime(new Date('2026-10-21T00:00:00Z'));
  render(<AmapCollectionPanel {...props} />);
  await choose();
  const frame = screen.getByTitle('隔离的高德采集服务');
  const post = jest.spyOn(frame.contentWindow, 'postMessage');
  fireEvent.click(screen.getByText('恢复保存计划'));
  await waitFor(() => expect(frame.getAttribute('srcdoc')).toBeTruthy());
  send('ready');
  const start = post.mock.calls.find(([message]) => message.type === 'start')[0].value;
  expect(start.config).toEqual({ ...plan, server_job_id: id, completed_keys: ['a'], force_refresh: false });
  fireEvent.click(screen.getByText('停止并保存'));
  expect(post.mock.calls.some(([message]) => message.type === 'stop')).toBe(true);
  await act(async () => { send('complete', { metadata: {}, stored_result: { state: job, valid_records: 0, file: null } }); });
  expect(screen.getByText(/已暂停／可恢复；成功 0.*失败记录不作为零值/)).toBeTruthy();
  expect(props.onSessionChange).toHaveBeenCalledWith('morning');
  expect(props.onCollected).not.toHaveBeenCalled();
});

test('切换城市或需求清理恢复选择，旧计划不启动', async () => {
  const { rerender } = render(<AmapCollectionPanel {...props} />);
  await choose();
  rerender(<AmapCollectionPanel {...props} demandRun={{ run_id: 'other' }} />);
  expect(screen.getByLabelText('恢复采集任务').value).toBe('');
  expect(screen.queryByLabelText('恢复有效计划')).toBeNull();
  rerender(<AmapCollectionPanel {...props} city={{ id: 'Other', name: '另一城市' }} />);
  expect(screen.getByLabelText('恢复采集任务').value).toBe('');
  expect(screen.getByTitle('隔离的高德采集服务').getAttribute('srcdoc')).toBeNull();
  await act(async () => {});
});

test('停止正在读取计划的任务，迟到的响应不能启动SDK', async () => {
  render(<AmapCollectionPanel {...props} />);
  await choose();
  let resolve;
  global.fetch.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  fireEvent.click(screen.getByText('恢复保存计划'));
  fireEvent.click(screen.getByText('停止并保存'));
  await act(async () => { resolve(response(job)); });
  expect(screen.getByTitle('隔离的高德采集服务').getAttribute('srcdoc')).toBeNull();
});

test('等待到保存时段后才发送计划，iframe就绪前停止不会发送凭证', async () => {
  render(<AmapCollectionPanel {...props} />);
  await choose();
  fireEvent.click(screen.getByLabelText('等待实际时段采集（保持页面打开）'));
  fireEvent.click(screen.getByText('恢复保存计划'));
  await waitFor(() => expect(screen.getByText(/等待保存计划时段/)).toBeTruthy());
  const frame = screen.getByTitle('隔离的高德采集服务');
  expect(frame.getAttribute('srcdoc')).toBeNull();
  await act(async () => { jest.setSystemTime(new Date('2026-10-22T00:00:00Z')); jest.advanceTimersByTime(10000); });
  expect(frame.getAttribute('srcdoc')).toBeTruthy();
  const post = jest.spyOn(frame.contentWindow, 'postMessage');
  fireEvent.click(screen.getByText('停止并保存'));
  send('ready');
  expect(post.mock.calls.some(([message]) => message.type === 'start')).toBe(false);
  await act(async () => {});
});

test('已完成历史仍展示，不默认重复采集，刷新挂载不自动恢复', async () => {
  global.fetch.mockImplementation(async url => response(url.endsWith('/collection-runs')
    ? { runs: [{ ...job, status: 'completed' }] } : { ...job, status: 'completed', failed_records: 0, no_route_records: 2 }));
  const { unmount } = render(<AmapCollectionPanel {...props} />);
  await choose();
  expect(screen.getByLabelText('采集历史').textContent).toContain('已完成');
  expect(screen.getByText('恢复保存计划').disabled).toBe(true);
  unmount();
  render(<AmapCollectionPanel {...props} />);
  await waitFor(() => expect(screen.getByLabelText('恢复采集任务').options.length).toBe(2));
  expect(screen.getByLabelText('恢复采集任务').value).toBe('');
  expect(screen.getByTitle('隔离的高德采集服务').getAttribute('srcdoc')).toBeNull();
});

test('历史详情读取失败时不相信旧completed标签，不允许恢复', async () => {
  global.fetch.mockImplementation(async url => {
    if (url.endsWith('/collection-runs')) return response({ runs: [{ ...job, status: 'completed' }] });
    throw new Error('offline');
  });
  render(<AmapCollectionPanel {...props} />);
  await choose();
  expect(screen.getByLabelText('采集历史').textContent).not.toContain('已完成');
  expect(screen.getByLabelText('采集历史').textContent).toContain('状态未核验');
  expect(screen.getByText('恢复保存计划').disabled).toBe(true);
});
