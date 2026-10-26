import React from 'react';
import { render, screen, fireEvent, act } from '@testing-library/react';
import TaskControls from './TaskControls';
const job = { id: 'one', kind: 'assignment', status: 'failed', options: { parameters: { bus_speed_kmh: 16 } } };
test('恢复仅使用任务ID，不读取或发送当前参数', async () => {
  const onChange = jest.fn();
  global.fetch = jest.fn(async () => ({ ok: true, json: async () => ({ ...job, status: 'queued' }) }));
  render(<TaskControls job={job} onChange={onChange} />);
  await act(async () => { fireEvent.click(screen.getByText('按原任务配置恢复')); });
  expect(fetch).toHaveBeenCalledWith('/api/jobs/one/resume', { method: 'POST' });
  expect(onChange).toHaveBeenCalledWith({ ...job, status: 'queued' });
});
test('切到其他任务再切回时，旧请求不能触发回调', async () => {
  let resolve;
  global.fetch = jest.fn(() => new Promise(done => { resolve = done; }));
  const onChange = jest.fn();
  const view = render(<TaskControls job={job} onChange={onChange} />);
  fireEvent.click(screen.getByText('按原任务配置恢复'));
  view.rerender(<TaskControls job={{ ...job, id: 'two' }} onChange={onChange} />);
  view.rerender(<TaskControls job={job} onChange={onChange} />);
  await act(async () => { resolve({ ok: true, json: async () => ({ ...job, status: 'queued' }) }); });
  expect(onChange).not.toHaveBeenCalled();
});
test('缺失原配置快照的历史任务不可恢复', () => {
  render(<TaskControls job={{ ...job, options: null }} />);
  expect(screen.queryByText('按原任务配置恢复')).toBeNull();
  expect(screen.getByRole('status').textContent).toContain('缺少原始配置快照');
});
