"""Deterministic task-boundary recovery checks; model numerics tested separately."""
from collections import Counter
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from Back_end.workflow_jobs import create_batch,run_batch
from Back_end.task_store import TaskCancelled

class BatchAcceptanceTests(unittest.TestCase):
    def setup_job(self):
        job=create_batch('Fixture',{'periods':[{'period':'pm_peak','total_demand':100}],'assignment':{'poi_count':2}})
        registry={job['id']:job};calls=Counter();version={'source':1}
        def step(name):
            def execute(ctx):
                calls[name]+=1
                result={'city_id':'Fixture','run_id':name}
                if name in ('assignment','evaluation'):result['demand_run_id']=ctx['demand_run_id']
                if name=='assignment':result['period']=ctx['step']['period']
                return result
            return execute
        adapters={name:step(name) for name in ('prepare','demand','assignment','evaluation')}
        adapters['fingerprint']=lambda ctx:dict(version)
        return job,registry,calls,version,adapters
    def test_cancel_and_resume_only_unfinished_steps(self):
        job,registry,calls,version,adapters=self.setup_job();normal=adapters['assignment']
        def interrupted(ctx):
            calls['assignment']+=1;ctx['checkpoint']({'run_id':'retained_checkpoint'})
            raise TaskCancelled('acceptance stop')
        adapters['assignment']=interrupted
        run_batch(job['id'],registry,None,adapters)
        self.assertEqual(job['status'],'cancelled');self.assertEqual(calls['prepare'],1)
        def resume(ctx):
            self.assertEqual(ctx['resume_run_id'],'retained_checkpoint');return normal(ctx)
        adapters['assignment']=resume;job.update(status='queued',cancel_requested=False)
        run_batch(job['id'],registry,None,adapters)
        self.assertEqual(job['status'],'completed')
        self.assertEqual(calls,{'prepare':1,'demand':1,'assignment':2,'evaluation':1})
    def test_changed_inputs_reject_resume_before_reusing_steps(self):
        job,registry,calls,version,adapters=self.setup_job()
        adapters['assignment']=lambda ctx:(_ for _ in ()).throw(TaskCancelled('stop'))
        run_batch(job['id'],registry,None,adapters);before=dict(calls)
        version['source']=2;job.update(status='queued',cancel_requested=False)
        run_batch(job['id'],registry,None,adapters)
        self.assertEqual(job['status'],'failed');self.assertEqual(dict(calls),before)
        self.assertIn('输入版本已变化',job['error'])
    def test_missing_metrics_never_report_complete_evaluation(self):
        job,registry,calls,version,adapters=self.setup_job();normal=adapters['evaluation']
        adapters['evaluation']=lambda ctx:{**normal(ctx),'status':'partial','missing_layers':['tci']}
        run_batch(job['id'],registry,None,adapters)
        self.assertEqual(job['status'],'partial');self.assertEqual(job['progress'],100)
        self.assertEqual(job['result']['evaluation']['missing_layers'],['tci'])
    def test_cancel_queued_does_not_start_any_step(self):
        job,registry,calls,version,adapters=self.setup_job();job['cancel_requested']=True
        run_batch(job['id'],registry,None,adapters)
        self.assertEqual(job['status'],'cancelled');self.assertFalse(calls)

if __name__=='__main__':unittest.main()
