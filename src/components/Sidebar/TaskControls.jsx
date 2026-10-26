import React, {useEffect, useRef, useState} from 'react';
import {readJson} from '../../utils/localApi';

const SUPPORTED = ['assignment', 'batch', 'demand', 'workflow', 'inputs'];

export default function TaskControls({job, onChange}) {
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const scope = useRef(null);
  if (scope.current?.id !== job?.id) scope.current = { id: job?.id };
  useEffect(() => { setBusy(false); setError(''); }, [job?.id]);
  if (!job) return null;
  const supported = SUPPORTED.includes(job.kind);
  const stopping = job.cancel_requested || job.status === 'cancel_requested';
  const running = ['queued', 'running', 'cancel_requested'].includes(job.status);
  const recovering = ['failed', 'cancelled', 'interrupted'].includes(job.status);
  const hasSnapshot = job.kind === 'inputs' || !!job.options;
  const action = supported && job.id ? running ? 'cancel' : recovering && hasSnapshot ? 'resume' : null : null;
  const control = async () => {
    const identity = job.id;
    const own = scope.current;
    setBusy(true); setError('');
    try {
      const next = await fetch(`/api/jobs/${encodeURIComponent(identity)}/${action}`, {method: 'POST'}).then(readJson);
      if (scope.current === own) onChange?.(next);
    } catch (failure) {
      if (scope.current === own) setError(failure.message);
    } finally {
      if (scope.current === own) setBusy(false);
    }
  };
  return <div style={{marginTop: 6, fontSize: 11}}>
    {action && <button disabled={busy || (action === 'cancel' && stopping)} onClick={control}>
      {busy ? '处理中…' : action === 'cancel' ? stopping ? '已请求停止' : '停止任务' : '按原任务配置恢复'}
    </button>}
    {running && stopping && <span role="status">已请求停止，尚未停止；等待安全边界，已有结果与断点保留，此时不能恢复。</span>}
    {!supported && <span role="status">旧任务未记录可识别的任务类型，不支持停止或恢复；请查看已有结果，或按明确参数新建任务。</span>}
    {supported && recovering && !hasSnapshot && <span role="status">旧任务缺少原始配置快照，不能使用当前表单参数冒充恢复；请新建任务。</span>}
    {action === 'resume' && <span>{job.kind === 'batch'
      ? '沿用原参数和输入版本，仅继续未完成步骤；已完成步骤不会重跑。'
      : '沿用原任务配置，不读取当前表单；分配优先使用断点，其他任务重做未完成计算。'}</span>}
    {error && <div role="alert" style={{color: '#ffca83'}}>{error}</div>}
  </div>;
}