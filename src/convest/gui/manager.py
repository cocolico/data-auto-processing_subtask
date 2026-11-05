"""Persistent UI task control and read-only queue/telemetry snapshots."""
import copy
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from convest.ingest.batches import durable_json
from . import config
from .config import load_settings, runtime, validate


def read(p, default=None):
    try:return json.loads(Path(p).read_text())
    except (OSError, ValueError):return default


def alive(info, root):
    pid=info.get('pid')
    if type(pid) is not int or pid<2:return False
    try:
        args=(Path('/proc')/str(pid)/'cmdline').read_bytes().split(b'\0')
        return b'convest.gui.worker' in args and str(root/'spec.json').encode() in args
    except OSError:return False


def rows(path, sql):
    if not path.exists():return []
    try:
        with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.5) as db:
            db.row_factory=sqlite3.Row
            return [dict(r) for r in db.execute(sql)]
    except sqlite3.OperationalError:return []


class Manager:
    def __init__(self):
        self.lock=threading.RLock();self.root=config.ROOT;self.settings=load_settings();self.processes={};self.open_resumed=False
    def task(self,tid):
        for t in self.settings['tasks']:
            if t['id']==tid:return t
        raise ValueError('任务不存在')
    def current(self,tid):
        value=read(self.root/'runs'/tid/'current.json',{})
        version=value.get('version','')
        if not version or any(x not in '0123456789abcdef' for x in version):return None
        return self.root/'runs'/tid/version
    def state(self,tid):
        root=self.current(tid)
        if root is None:return {'state':'stopped'}
        status=read(root/'runtime.json',{'state':'stopped'})
        if status.get('state') in ('starting','running','stopping') and not alive(status,root):
            status={**status,'state':'error','error':'工作进程已退出，队列和成品已保留，可重新开始'}
        return {**status,'root':str(root),'version':root.name}
    def save(self,settings):
        cfg=validate(settings)
        with self.lock:
            if any(self.state(t['id'])['state'] in ('starting','running','stopping') for t in self.settings['tasks']):
                raise ValueError('请先停止工作流并等待当前作业收尾，再保存配置')
            durable_json(self.root/'settings.json',cfg);self.settings=cfg
        return cfg
    def start(self,tid=None):
        with self.lock:
            selected=[self.task(tid)] if tid else [t for t in self.settings['tasks'] if t['enabled']]
            if tid and not selected[0]['enabled']:raise ValueError('请先启用此任务并保存设置')
            if not selected:raise ValueError('没有启用的任务')
            # Preflight ALL selected inputs before starting any worker.
            prepared=[]
            for t in selected:
                if t.get('annotation_enabled'):
                    from importlib.util import find_spec
                    if any(find_spec(name) is None for name in ('openai','pyarrow')):
                        raise ValueError('当前 Python 环境缺少标注依赖，请安装本工程的 annotation 可选依赖后启动')
                old=self.state(t['id'])
                if old['state'] in ('starting','running'):continue
                if old['state']=='stopping':raise ValueError('当前作业正在收尾，请等待停止完成')
                root,spec=runtime(self.settings,t)
                old_info=read(root/'runtime.json',{})
                for child in old_info.get('children',{}).values():
                    if alive(child,root):raise ValueError('旧的工作进程仍在收尾，请稍后重试')
                prepared.append((t,root))
            for t,root in prepared:
                (root/'stop-request.json').unlink(missing_ok=True)
                env={**os.environ,'PYTHONUNBUFFERED':'1','OPENBLAS_NUM_THREADS':'1','OMP_NUM_THREADS':'1','NUMBA_NUM_THREADS':'1'}
                with (root/'supervisor.log').open('a') as log:
                    child=subprocess.Popen([sys.executable,'-u','-m','convest.gui.worker','--spec',str(root/'spec.json')],
                                           stdout=log,stderr=subprocess.STDOUT,start_new_session=True,env=env)
                self.processes[t['id']]=child
                durable_json(root/'runtime.json',{'state':'starting','pid':child.pid,'heartbeat_ns':time.time_ns()})
                durable_json(root.parent/'current.json',{'version':root.name})
                self.remember(t['id'],True)
        return self.snapshot()
    def stop(self,tid=None):
        with self.lock:
            tasks=[self.task(tid)] if tid else self.settings['tasks']
            for t in tasks:
                self.remember(t['id'],False)
                root=self.current(t['id'])
                if root and self.state(t['id'])['state'] in ('starting','running','stopping'):
                    durable_json(root/'stop-request.json',{'requested_ns':time.time_ns()})
        return self.snapshot()
    def retry(self,tid):
        with self.lock:
            self.task(tid)
            if self.state(tid)['state'] in ('running','starting','stopping'):raise ValueError('请先停止任务并等待收尾后重试')
            root=self.current(tid)
            if root is None:return self.snapshot()
            for file,queries in [
                (root/'capture/state/queue.sqlite3',[
                    "UPDATE jobs SET state='retry',attempts=0,next_ns=0 WHERE state IN ('failed','retry')",
                    "UPDATE uploads SET next_ns=0 WHERE state='retry'"]),
                (root/'training/state/queue.sqlite3',[
                    "UPDATE tasks SET next_ns=0 WHERE state IN ('retry','upload_retry','waiting_space')"])]:
                if file.exists():
                    with sqlite3.connect(file) as db:
                        for sql in queries:db.execute(sql)
            return self.snapshot()

    def snapshot(self):
        with self.lock:
            for child in self.processes.values():child.poll()
            settings=copy.deepcopy(self.settings)
        result=[]
        for task in settings['tasks']:
            item={'id':task['id'],**self.state(task['id'])}
            root=self.current(task['id'])
            if root:
                capture=root/'capture/state';train=root/'training/state'
                item.update(jobs=rows(capture/'queue.sqlite3','SELECT route,episode,state,attempts,error FROM jobs ORDER BY route,episode'),
                            uploads=rows(capture/'queue.sqlite3','SELECT route,episode,state,raw_done,dataset_done,error FROM uploads ORDER BY updated_ns'),
                            batches=rows(capture/'queue.sqlite3','SELECT route,batch_id,name,state,deadline_ns,frames,sources,remote_path FROM capture_batches ORDER BY route,batch_id'),
                            training=rows(train/'queue.sqlite3','SELECT id,state,error,spec FROM tasks ORDER BY updated_ns'),
                            processing=read(capture/'progress.json',{}),data_upload=read(capture/'upload_progress.json',{}),
                            training_processing=read(train/'progress.json',{}),training_upload=read(train/'upload_progress.json',{}),
                            discovery=read(train/'discovery.json',{}),videos=read(train/'video_progress.json',{}),
                            raw_upload=read(capture/'upload_progress_raw.json',{}),batch_upload=read(capture/'upload_progress_batch.json',{}),
                            normal_upload=read(train/'upload_progress_normal.json',{}),speedup_upload=read(train/'upload_progress_speedup.json',{}))
                item['capture_status']=read(capture/'status.json',{})
                for row in item['training']:
                    source=json.loads(row.pop('spec'))
                    row.update(route=source.get('route'),name=source.get('name'),batch_id=source.get('batch_id'))
                versions=[{'version':p.name,'state':read(p/'runtime.json',{}).get('state','stopped')}
                          for p in root.parent.iterdir() if p.is_dir() and (p/'spec.json').exists()]
                item['versions']=versions
                spec=read(root/'spec.json',{})
                if spec.get('annotation'):
                    from convest.annotation import service as annotation
                    item['annotation']=annotation.snapshot(spec)
            result.append(item)
        disk=__import__('shutil').disk_usage(self.root)
        from convest.annotation import credentials
        return {'settings':settings,'tasks':result,'updated_ns':time.time_ns(),
                'credential':credentials.status(self.root),
                'disk':{'free':disk.free,'total':disk.total},'batch_hours':24}

    def remember(self,tid,running):
        path=self.root/'resume-intent.json'
        wanted=read(path,{})
        wanted[tid]=bool(running);durable_json(path,wanted)

    def resume_on_open(self):
        if self.open_resumed:return
        self.open_resumed=True
        wanted=read(self.root/'resume-intent.json',{})
        for task in self.settings['tasks']:
            if task['enabled'] and wanted.get(task['id']):
                try:self.start(task['id'])
                except Exception as exc:
                    durable_json(self.root/'resume-errors'/f"{task['id']}.json",{'error':str(exc),'updated_ns':time.time_ns()})

    def annotation_spec(self,tid):
        self.task(tid);root=self.current(tid)
        if root is None:raise ValueError('请先保存并开始该任务')
        spec=read(root/'spec.json',{})
        if not spec.get('annotation',{}).get('enabled'):raise ValueError('当前任务未启用自动标注')
        return spec

    def annotation_pause(self,tid,pause):
        spec=self.annotation_spec(tid)
        durable_json(Path(spec['annotation']['root'])/'pause.json',{'paused':bool(pause),'updated_ns':time.time_ns()})
        return self.snapshot()

    def annotation_retry(self,tid):
        from convest.annotation import service as annotation
        spec=self.annotation_spec(tid)
        # Retry transient/unknown jobs; excluded quality/unsupported jobs stay excluded.
        with annotation.database(spec) as db:
            with db:db.execute("UPDATE episodes SET next_ns=0 WHERE state IN ('retry_network','unknown','blocked_credentials','waiting_space')")
        return self.snapshot()
    def logs(self,tid):
        self.task(tid);root=self.current(tid)
        if not root:return '尚未开始工作流。保存设置后点击“开始工作流”。'
        content=[]
        for p in sorted(root.glob('*.log')):
            with p.open('rb') as f:
                f.seek(max(0,p.stat().st_size-14000));tail=f.read().decode('utf-8',errors='replace')
            content.append('━━ '+p.name+' ━━\n'+tail)
        return '\n\n'.join(content)[-65000:]
