"""Task-scoped review previews of drafts, approved full data and training variants."""
import json
import sqlite3
from importlib.util import find_spec
from pathlib import Path
from .service import database, batch_identity
from .native import read_json, rows, path_for
from . import stages


def job(spec,identity):
    with database(spec) as db:
        row=db.execute("SELECT * FROM episodes WHERE id=? AND state IN ('completed','needs_review','reviewing','approved','rejected')",(identity,)).fetchone()
    if not row:raise ValueError('结果尚未进入审核或预览状态')
    return json.loads(row['payload']),json.loads(row['labels']) if row['labels'] else []


def variant_source(spec,identity,variant):
    if variant not in ('draft','reviewed','normal','speedup'):raise ValueError('未知数据版本')
    payload,labels=job(spec,identity)
    source=Path(payload['source']);ep=payload['source_ep'];mapping=None
    if variant=='reviewed':
        with database(spec) as db:
            b=db.execute("SELECT * FROM batches WHERE id=? AND state='completed'",(batch_identity(payload['route'],payload['batch_id']),)).fetchone()
        if not b:raise ValueError('审核成品尚未生成，请完成放行并等待导出')
        source=Path(b['output'])
        allowed=Path(spec['annotation']['root'])/'datasets'
        if not source.resolve().is_relative_to(allowed.resolve()):raise ValueError('预览源越界')
    elif variant in ('normal','speedup'):
        dbpath=Path(spec['training']['state_root'])/'queue.sqlite3'
        if not dbpath.exists():raise ValueError('normal/speedup 尚未生成')
        with sqlite3.connect(dbpath.as_uri()+'?mode=ro',uri=True) as db:
            db.row_factory=sqlite3.Row
            candidates=db.execute("SELECT spec,payload FROM tasks WHERE state IN ('local_ready','uploading','upload_retry','uploaded')").fetchall()
        matched=[]
        for row in candidates:
            s=json.loads(row['spec'])
            if s.get('route')==payload['route'] and s.get('batch_id')==payload['batch_id']:
                matched.append(json.loads(row['payload']))
        if len(matched)!=1:raise ValueError('尚无唯一的本地转换成品')
        source=Path(matched[0]['source'])/variant
        if not source.resolve().is_relative_to(Path(spec['training']['output_root']).resolve()):raise ValueError('预览源越界')
    else:
        allowed=[Path(r['output']).resolve() for r in spec['capture']['routes']]
        if not any(source.resolve().is_relative_to(p) for p in allowed):raise ValueError('预览源超出当前任务范围')
    if variant!='draft':
        annotations=rows(source/'meta'/stages.FILENAME)
        found=[r for r in annotations if r['source_episode_index']==payload['source_ep']]
        if len(found)!=1:raise ValueError('该条目不在审核通过的交付子集中')
        ep=found[0]['episode_index'];labels=found[0]['steps']
        if variant=='speedup':mapping=read_json(source/f'processing/episode_{ep:06d}.json')['source_index']
    if source.is_symlink():raise ValueError('预览源不得为软链接')
    return payload,labels,source,ep,mapping


def describe(spec,identity,variant='draft'):
    # Preserve the legacy two-value job fixture/API for draft previews.
    if variant=='draft':
        payload,labels=job(spec,identity);source=Path(payload['source']);ep=payload['source_ep'];mapping=None
    else:payload,labels,source,ep,mapping=variant_source(spec,identity,variant)
    info=read_json(source/'meta/info.json')
    episode={'length':payload['length']} if variant=='draft' else next(e for e in rows(source/'meta/episodes.jsonl') if e['episode_index']==ep)
    cameras=[k for k,v in info['features'].items() if v['dtype']=='video']
    camera_status={key:{'available':(source/path_for(info,ep,video_key=key)).is_file(),
                        'label':{'cam0':'外部总览','cam1':'右手腕','cam2':'左手腕'}.get(key.rsplit('.',1)[-1],key)} for key in cameras}
    row=None;frozen=False
    if spec.get('annotation',{}).get('root') and (Path(spec['annotation']['root'])/'queue.sqlite3').exists():
        with database(spec) as db:
            row=db.execute('SELECT state,revision,error,route,batch_id FROM episodes WHERE id=?',(identity,)).fetchone()
            if row:frozen=bool(db.execute("SELECT 1 FROM batches WHERE route=? AND batch_id=? AND state IN ('completed','empty')",(row['route'],row['batch_id'])).fetchone())
    state=row['state'] if row else 'completed';revision=row['revision'] if row else 0
    from .review import released
    staged=stages.is_stages(labels) or spec.get('annotation',{}).get('review_required',False)
    result={'id':identity,'episode_index':ep,'recording_id':payload['recording_id'],
            'fps':info.get('fps',payload['fps']),'frames':episode['length'],'model':payload['settings']['model'],
            'steps':labels,'cameras':cameras,'camera_status':camera_status,
            'protocol':stages.SCHEMA if staged else 'legacy','single_track':staged,
            'variant':variant,'revision':revision,'review_state':state,'error':row['error'] if row else None,
            'readonly':variant!='draft' or state not in ('needs_review','reviewing'),
            'source_frame_index':mapping,'can_reopen':variant=='draft' and state=='approved' and not frozen and not released(spec),
            'viewer_sdks':{'rrd':find_spec('rerun') is not None,'mcap':find_spec('foxglove') is not None},
            'video_available':(source/path_for(info,ep,video_key='observation.images.cam0')).is_file()}
    if not staged:
        from .compact import project
        result['steps']=project(labels)
        from .granularity import derive
        result['granularity']=derive(labels,payload['fps'],payload['length'])
    return result


def video(spec,identity,camera,variant='draft'):
    if camera not in ('observation.images.cam0','observation.images.cam1','observation.images.cam2'):raise ValueError('相机无效')
    _,_,root,ep,_=variant_source(spec,identity,variant)
    info=read_json(root/'meta/info.json');path=root/path_for(info,ep,video_key=camera)
    if not path.resolve().is_relative_to(root.resolve()) or path.is_symlink() or not path.is_file():
        raise ValueError('本地视频已释放或不可用，标签和来源仍保留')
    return path
