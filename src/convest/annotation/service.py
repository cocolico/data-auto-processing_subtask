"""Persistent per-episode queue and sealed annotated batch handoff to the GUI."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import random
import shutil
import sqlite3
import time

from convest.config import digest
from convest.ingest.batches import durable_json
from . import credentials
from .compact import project as compact_steps
from .native import read_json, rows, path_for, sha256, inspect, export_dataset, validate_output
from .provider import ArkProvider, Failure, classify, redact
from .views import videos_for

# Legacy terminal state is readable; new work never enters it or rewrites published batches.
TERMINAL={'completed','excluded','history_skipped','needs_review'}


def enabled(spec):return bool(spec.get('annotation',{}).get('enabled'))


@contextmanager
def database(spec):
    root=Path(spec['annotation']['root']);root.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(root/'queue.sqlite3',timeout=30);db.row_factory=sqlite3.Row
    db.executescript('''
      CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS episodes(id TEXT PRIMARY KEY,route TEXT,batch_id TEXT,
        recording_id TEXT,source_ep INTEGER,payload TEXT NOT NULL,state TEXT NOT NULL,
        runs INTEGER DEFAULT 0,result_failures INTEGER DEFAULT 0,unknown_failures INTEGER DEFAULT 0,
        next_ns INTEGER DEFAULT 0,error TEXT,usage TEXT,labels TEXT,updated_ns INTEGER);
      CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY,route TEXT,batch_id TEXT,
        source TEXT,state TEXT,output TEXT,proof TEXT,error TEXT,updated_ns INTEGER);
    ''');db.commit()
    try:yield db
    finally:db.close()


def update(db,table,identity,**values):
    if table not in ('episodes','batches'):raise ValueError('Invalid annotation table')
    values['updated_ns']=time.time_ns()
    with db:db.execute('UPDATE '+table+' SET '+','.join(k+'=?' for k in values)+' WHERE id=?',[*values.values(),identity])


def meta(db,key,default=None):
    row=db.execute('SELECT value FROM metadata WHERE key=?',(key,)).fetchone()
    return json.loads(row[0]) if row else default


def setmeta(db,key,value):
    with db:db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)',(key,json.dumps(value)))


def usage_record(root,identity):
    calls=[]
    for path in sorted((Path(root)/'requests'/identity).glob('call-*/response.json')):
        response=read_json(path)
        calls.append({'call':path.parent.name,'response_id':response.get('response_id'),'usage':response.get('usage',{})})
    return {'total_tokens':sum(c['usage'].get('total_tokens',0) or 0 for c in calls),'returned_calls':calls}


def blocked(spec):
    if not enabled(spec):return None
    path=Path(spec['annotation']['root'])/'blocked.json'
    try:return read_json(path)
    except FileNotFoundError:return None


def paused(spec):
    p=Path(spec['annotation']['root'])/'pause.json'
    return p.exists() and read_json(p).get('paused',False)


def disk_ok(spec):
    if not enabled(spec):return True
    root=Path(spec['annotation']['root']);root.mkdir(parents=True,exist_ok=True)
    return shutil.disk_usage(root).free >= spec['annotation'].get('min_free_gib',8)*1024**3


def discover(spec,db):
    if not enabled(spec):return
    first=not meta(db,'initialized',False)
    include=spec['annotation'].get('include_existing',False)
    for route in spec['capture']['routes']:
        catalogue=Path(route['output'])/'.ingest/batches.json'
        if not catalogue.exists():continue
        for batch in read_json(catalogue)['batches']:
            source=Path(batch.get('generation',''))
            if not source.is_absolute() or not (source/'meta/info.json').is_file():continue
            # An already released immutable dataset is not rediscovered as new.
            if not (source/'data').is_dir():continue
            info=read_json(source/'meta/info.json')
            for ep in rows(source/'meta/episodes.jsonl'):
                recording=ep.get('source_recording_id') or digest([route['id'],batch['id'],ep['episode_index']])
                identity=digest([route['id'],batch['id'],recording,ep['episode_index'],ep['length']])
                old=db.execute('SELECT state FROM episodes WHERE id=?',(identity,)).fetchone()
                if old:
                    if old['state']=='history_skipped' and include:
                        delivered=db.execute('SELECT state FROM batches WHERE id=?',(batch_identity(route['id'],batch['id']),)).fetchone()
                        if delivered and delivered['state']=='completed':continue
                        update(db,'episodes',identity,state='pending',next_ns=0,error=None)
                        with db:db.execute("UPDATE batches SET state='pending',proof=NULL WHERE route=? AND batch_id=? AND state='empty'",(route['id'],batch['id']))
                    continue
                payload={'id':identity,'route':route['id'],'batch_id':batch['id'],'recording_id':recording,
                    'source_ep':ep['episode_index'],'source':str(source),
                    'video':str(source/path_for(info,ep['episode_index'],video_key='observation.images.cam0')),
                    'length':ep['length'],'fps':info['fps'],'task':ep['tasks'],'robot':route['robot'],
                    'settings':{k:spec['annotation'][k] for k in ('model','fps','retries')}}
                state='history_skipped' if first and not include else 'pending'
                with db:db.execute('''INSERT OR IGNORE INTO episodes(id,route,batch_id,recording_id,source_ep,payload,state,updated_ns)
                    VALUES(?,?,?,?,?,?,?,?)''',(identity,route['id'],batch['id'],recording,ep['episode_index'],json.dumps(payload),state,time.time_ns()))
    setmeta(db,'initialized',True)


def process_one(spec,db,provider=None):
    if not enabled(spec) or paused(spec):return False
    root=Path(spec['annotation']['root']);now=time.time_ns();gate=blocked(spec)
    credential_status=credentials.status(spec['annotation']['credential_root'])
    if gate and gate.get('credential_revision')!=credential_status['revision']:
        (root/'blocked.json').unlink(missing_ok=True)
        with db:db.execute("UPDATE episodes SET next_ns=0 WHERE state='blocked_credentials'")
        gate=None
    states=['blocked_credentials'] if gate else ['pending','retry_network','unknown','processing','blocked_credentials','waiting_space']
    query='SELECT * FROM episodes WHERE state IN ('+','.join('?' for _ in states)+') AND next_ns<=? ORDER BY updated_ns,id LIMIT 1'
    row=db.execute(query,[*states,now]).fetchone()
    if not row:return False
    job=json.loads(row['payload']);identity=row['id'];runs=row['runs']
    if row['result_failures'] and row['error']:job['retry_feedback']=row['error']
    if not disk_ok(spec):
        update(db,'episodes',identity,state='blocked_credentials' if gate else 'waiting_space',next_ns=now+30*10**9,error='等待本机可用空间')
        return False
    model_lock=Path(spec['annotation']['credential_root'])/'annotation-model.lock'
    model_lock.parent.mkdir(parents=True,exist_ok=True)
    with model_lock.open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return False
        engine=provider or ArkProvider()
        def progress(**details):
            durable_json(root/'progress.json',{'id':identity,'route':row['route'],'recording_id':row['recording_id'],
                'episode_index':row['source_ep'],'started_ns':now,'updated_ns':time.time_ns(),**details})
        try:
            try:key=credentials.load(spec['annotation']['credential_root'])
            except (OSError,ValueError) as exc:raise Failure('credentials',str(exc)) from exc
            if not key:raise Failure('credentials','尚未配置豆包 API Key')
            video=Path(job['video'])
            if not video.is_file():raise Failure('unsupported','标注源视频不可用，保留原作业')
            # Production requires three synchronized views, including for old
            # pending queue payloads. Injected offline providers may use fixtures.
            videos={'cam0':video}
            if provider is None:
                from convest.training.adatempo.lerobot_video import inspect_video
                try:
                    videos=videos_for(job)
                    info=read_json(Path(job['source'])/'meta/info.json')
                    for camera,path in videos.items():
                        inspect_video(path,job['length'],job['fps'],info['features']['observation.images.'+camera]['shape'])
                except (OSError,ValueError,KeyError) as exc:
                    raise Failure('unsupported',f'三视角标注输入校验失败：{exc}') from exc
            old_work=root/'requests'/identity/f'call-{runs:04d}'
            reuse=(row['state']=='processing' and runs>0 and (old_work/'response.json').exists())
            if row['state']=='processing' and not reuse and (old_work/'request.json').exists():
                request=read_json(old_work/'request.json')
                if request.get('phase')=='sent' and not request.get('response_id'):
                    raise Failure('unknown','上次请求已发出但没有保存回复，结果未知')
                reuse=bool(request.get('response_id'))
            if not reuse:runs+=1
            request_work=root/'requests'/identity/f'call-{runs:04d}'
            request_work.mkdir(parents=True,exist_ok=True)
            update(db,'episodes',identity,state='processing',runs=runs,error=None)
            progress(phase='preparing_video')
            # Persist/check every view, not only cam0, across retries and sealing.
            evidence_path=root/'sources'/f'{identity}.json'
            hashes={camera:{'video':str(path),'sha256':sha256(path)} for camera,path in videos.items()}
            if evidence_path.exists():
                evidence=read_json(evidence_path)
                previous=evidence.get('videos',{'cam0':{'sha256':evidence['sha256']}})
                if any(camera not in hashes or hashes[camera]['sha256']!=entry['sha256']
                       for camera,entry in previous.items()):
                    raise Failure('unsupported','标注源视频在请求之间发生变化，保留并隔离该条数据')
            durable_json(evidence_path,{'video':str(video),'sha256':hashes['cam0']['sha256'],
                'videos':hashes,'length':job['length'],'fps':job['fps'],
                'recording_id':job['recording_id']})
            labels,usage=engine.annotate(job,key,request_work,progress)
            from .compact import project, validate as validate_compact
            labels = project(labels)
            validate_compact(labels)
            from .granularity import derive
            dual = derive(labels, job["fps"], job["length"])
            durable_json(root/"granularity"/f"{identity}.json", {
                "source": job["source"], "source_episode_index": job["source_ep"],
                "source_recording_id": job["recording_id"], **dual})
            from .native import validate_segments, validate_ending_motion, validate_action_motion
            validate_segments(labels,job['length'])
            validate_ending_motion(job['source'],job['source_ep'],labels)
            validate_action_motion(job['source'],job['source_ep'],labels)
            update(db,'episodes',identity,state='completed',labels=json.dumps(labels),usage=json.dumps(usage_record(root,identity)),next_ns=0,error=None)
            (root/'blocked.json').unlink(missing_ok=True)
            progress(phase='completed',steps=len(labels))
            return True
        except Exception as exc:
            failure=classify(exc)
            message=redact(failure)
            details={'error':message,'usage':json.dumps(usage_record(root,identity))};delay=min(300,10*2**min(runs,5))+random.uniform(0,3)
            if failure.kind in ('credentials','quota'):
                state='blocked_credentials';delay=300
                durable_json(root/'blocked.json',{'kind':failure.kind,'error':message,'credential_revision':credential_status['revision'],
                    'next_probe_ns':time.time_ns()+300*10**9,'policy':'pause_current_task'})
            elif failure.kind=='unsupported':state='excluded';delay=0
            elif failure.kind=='unknown':
                count=row['unknown_failures']+1;details['unknown_failures']=count
                state='excluded' if count+row['result_failures']>job['settings']['retries'] else 'unknown'
            elif failure.kind=='network':
                state='retry_network';delay=failure.retry_after or delay
            else:
                count=row['result_failures']+1;details['result_failures']=count
                state='excluded' if count+row['unknown_failures']>job['settings']['retries'] else 'pending'
            update(db,'episodes',identity,state=state,next_ns=time.time_ns()+int(delay*10**9),**details)
            progress(phase=state,error=message,next_retry_ns=time.time_ns()+int(delay*10**9))
            return True


def batch_identity(route,batch_id):return digest([route,batch_id])


def build_batches(spec,db):
    if not enabled(spec) or paused(spec) or blocked(spec):return
    root=Path(spec['annotation']['root'])
    for route in spec['capture']['routes']:
        path=Path(route['output'])/'.ingest/batches.json'
        if not path.exists():continue
        for batch in read_json(path)['batches']:
            if batch.get('state')!='sealed':continue
            identity=batch_identity(route['id'],batch['id'])
            old=db.execute('SELECT * FROM batches WHERE id=?',(identity,)).fetchone()
            if old and old['state'] in ('completed','empty'):continue
            jobs=db.execute('SELECT * FROM episodes WHERE route=? AND batch_id=? ORDER BY source_ep',
                            (route['id'],batch['id'])).fetchall()
            if not jobs or any(j['state'] not in TERMINAL for j in jobs):continue
            output=root/'datasets'/identity;source=Path(batch['generation'])
            with db:db.execute('''INSERT OR IGNORE INTO batches(id,route,batch_id,source,state,updated_ns)
                VALUES(?,?,?,?,?,?)''',(identity,route['id'],batch['id'],str(source),'pending',time.time_ns()))
            try:
                source_info=read_json(source/'meta/info.json')
                if len(jobs)!=source_info['total_episodes']:continue
                labels={j['source_ep']:json.loads(j['labels']) for j in jobs if j['state']=='completed'}
                exclusions=[{'source_episode_index':j['source_ep'],'recording_id':j['recording_id'],
                    'state':j['state'],'reason':j['error']} for j in jobs if j['state']!='completed']
                if not labels:
                    update(db,'batches',identity,state='empty',proof=json.dumps({'excluded':exclusions}),error='无可交付的成功标注')
                    continue
                source_data=inspect(source)
                for job_row in jobs:
                    if job_row['state']!='completed':continue
                    payload=json.loads(job_row['payload'])
                    episode=source_data['episodes'][job_row['source_ep']]
                    evidence=read_json(root/'sources'/f"{job_row['id']}.json")
                    if episode['length']!=payload['length']:
                        raise ValueError('封存视频与标注源不一致，保留并停止该批交付')
                    for camera,entry in evidence.get('videos',{'cam0':{'sha256':evidence['sha256']}}).items():
                        current_video=source/path_for(source_data['info'],job_row['source_ep'],video_key='observation.images.'+camera)
                        if sha256(current_video)!=entry['sha256']:
                            raise ValueError(f'封存 {camera} 视频与标注源不一致，保留并停止该批交付')
                if output.exists():
                    report=validate_output(source_data,output,labels)
                else:
                    from convest.capture.transfer import ensure_real_path
                    stage=root/'staging'/identity;ensure_real_path(stage)
                    if not disk_ok(spec):raise OSError(28,'等待标注批次导出空间')
                    required=source_data['bytes']+int(spec['annotation'].get('min_free_gib',8)*1024**3)
                    if shutil.disk_usage(root).free<required:raise OSError(28,'等待标注批次导出空间')
                    # Staging belongs only to this deterministic batch queue row.
                    if stage.exists():shutil.rmtree(stage)
                    export_dataset(source_data,stage,labels)
                    report=validate_output(source_data,stage,labels)
                    from convest.ingest.batches import flush_directory
                    for artifact in stage.rglob('*'):
                        if artifact.is_file():
                            with artifact.open('rb') as stream:os.fsync(stream.fileno())
                    for directory in sorted((p for p in stage.rglob('*') if p.is_dir()),key=lambda p:len(p.parts),reverse=True):
                        flush_directory(directory)
                    flush_directory(stage)
                    output.parent.mkdir(parents=True,exist_ok=True)
                    if output.exists():raise FileExistsError(output)
                    stage.rename(output)
                    flush_directory(output.parent)
                from convest.capture.transfer import manifest
                from convest.ingest.inputs import snapshot
                proof={'status':'completed','dataset':str(output),'source':str(source),'source_batch_id':batch['id'],
                    'route':route['id'],'files':manifest(output),'snapshot':snapshot(output),
                    'validation':report,'excluded':exclusions,'created_ns':time.time_ns(),
                    'original_tasks':source_data['tasks'],
                    'annotations':[{'source_episode_index':j['source_ep'],'recording_id':j['recording_id'],
                        'settings':json.loads(j['payload'])['settings'],'steps':compact_steps(json.loads(j['labels'])) if j['labels'] else [],
                        'usage':json.loads(j['usage']) if j['usage'] else {},'request_attempts':j['runs'],
                        'unknown_failures':j['unknown_failures'],'state':j['state']} for j in jobs]}
                audit=root/'receipts'/identity
                durable_json(audit/'annotation.json',proof)
                proof['audit']=str(audit)
                update(db,'batches',identity,state='completed',output=str(output),proof=json.dumps(proof),error=None)
            except Exception as exc:
                update(db,'batches',identity,state='waiting_space' if getattr(exc,'errno',None)==28 else 'failed',error=redact(exc))


def delivery(spec,route,batch_id):
    if not enabled(spec):return None
    with database(spec) as db:
        row=db.execute('SELECT * FROM batches WHERE id=?',(batch_identity(route,batch_id),)).fetchone()
        if not row:return {'state':'pending'}
        return {'state':row['state'],**(json.loads(row['proof']) if row['proof'] else {}),'error':row['error']}


def snapshot(spec):
    if not enabled(spec):return {'enabled':False,'episodes':[],'batches':[],'paused':False,'blocked':None}
    with database(spec) as db:
        episodes=[dict(r) for r in db.execute('SELECT id,route,batch_id,recording_id,source_ep,state,runs,result_failures,unknown_failures,next_ns,error,usage,updated_ns FROM episodes ORDER BY updated_ns DESC')]
        batches=[dict(r) for r in db.execute('SELECT id,route,batch_id,state,output,error FROM batches ORDER BY updated_ns DESC')]
    root=Path(spec['annotation']['root'])
    return {'enabled':True,'episodes':episodes,'batches':batches,'paused':paused(spec),'blocked':blocked(spec),
            'progress':read_json(root/'progress.json') if (root/'progress.json').exists() else {}}


def cleanup(spec,db):
    if blocked(spec) or meta(db,'cleanup_after',0)>time.time_ns():return
    setmeta(db,'cleanup_after',time.time_ns()+300*10**9)
    key=credentials.load(spec['annotation']['credential_root'])
    if key:ArkProvider().cleanup(key,Path(spec['annotation']['root'])/'requests')
