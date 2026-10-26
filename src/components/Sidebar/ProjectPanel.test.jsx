import React from 'react';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import ProjectPanel from './ProjectPanel';
jest.mock('antd', () => ({
  Alert: ({ message }) => <div>{message}</div>,
  Button: ({ children, ...props }) => <button {...props}>{children}</button>,
  Checkbox: ({ children, ...props }) => <label><input type="checkbox" {...props} />{children}</label>,
  Input: props => <input {...props} />,
  Select: ({ options, value, onChange, ...props }) => <select {...props} value={value || ''} onChange={e => onChange(e.target.value)}><option value="" />{options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}</select>,
}));
const response = value => ({ ok: true, json: async () => value });
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const project = { project_id: 'p1', payload: { name: '测试工程', demand_config: { grid_m: 700 } } };
const city = { id: 'Fixture' };
beforeEach(() => { global.fetch = jest.fn(async () => response({ projects: [project] })); });
test('重复打开同一工程均显式恢复原参数，并传递恢复输入选项', async () => {
  const onRestore = jest.fn(async () => ({ message: '已恢复' }));
  render(<ProjectPanel city={city} onRestore={onRestore} />);
  await screen.findByText('测试工程');
  fireEvent.change(screen.getByLabelText('已导入工程'), { target: { value: 'p1' } });
  fireEvent.click(screen.getByText('手动恢复所选工程'));
  await waitFor(() => expect(screen.getByText('手动恢复所选工程').disabled).toBe(false));
  fireEvent.click(screen.getByLabelText(/恢复工程输入/));
  fireEvent.click(screen.getByText('手动恢复所选工程'));
  await waitFor(() => expect(onRestore).toHaveBeenCalledTimes(2));
  expect(onRestore.mock.calls).toEqual([[project, { restoreInputs: false }], [project, { restoreInputs: true }]]);
});
test.each([false, true])('切城后旧工程列表不回写（旧请求失败：%s）', async fail => {
  const old = deferred();
  fetch.mockImplementation(url => url.includes('/Fixture/') ? old.promise : Promise.resolve(response({ projects: [] })));
  const view = render(<ProjectPanel city={city} onRestore={jest.fn()} />);
  view.rerender(<ProjectPanel city={{ id: 'Next' }} onRestore={jest.fn()} />);
  await act(async () => { old.resolve(fail ? { ok: false, json: async () => ({ error: '旧城市错误' }) } : response({ projects: [project] })); });
  expect(screen.queryByText('测试工程')).toBeNull();
  expect(screen.queryByText('旧城市错误')).toBeNull();
});
test('同城更换输入版本清除工程选择，重新读取列表', async () => {
  const props = { city, onRestore: jest.fn() };
  const view = render(<ProjectPanel {...props} />);
  await screen.findByText('测试工程');
  fireEvent.change(screen.getByLabelText('已导入工程'), { target: { value: 'p1' } });
  view.rerender(<ProjectPanel {...props} city={{ ...city, custom_input_version: 'v2' }} />);
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  expect(screen.getByText('手动恢复所选工程').disabled).toBe(true);
  expect(props.onRestore).not.toHaveBeenCalled();
});
