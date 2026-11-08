"""Durable, optimistic-concurrency review gate. Approved revisions are immutable."""
import json
from pathlib import Path
import time
from convest.config import digest
from convest.ingest.batches import durable_json
from . import stages
from .native import read_json, sha256, path_for
from .service import database, enabled


def required(spec):
    return enabled(spec) and spec['annotation'].get('review_required',False)


def cycle(spec):
    p=Path(spec['annotation']['root'])/'review-cycle.json'
    return read_json(p) if p.exists() else {'state':'collecting'}


def released(spec):
    return not required(spec) or cycle(spec)['state']=='released'


def source_evidence(payload):
    root=Path(payload['source']);info=read_json(root/'meta/info.json')
    return {'video':payload['video'],'sha256':sha256(Path(payload['video'])),
            'length':payload['length'],'fps':payload['fps'],'recording_id':payload['recording_id'],
            'videos':{c:{'video':str(root/path_for(info,payload['source_ep'],video_key='observation.images.'+c)),
                         'sha256':sha256(root/path_for(info,payload['source_ep'],video_key='observation.images.'+c))}
                      for c in ('cam0','cam1','cam2')}}


def verify_source(spec,row):
    root=Path(spec['annotation']['root']);p=root/'sources'/f"{row['id']}.json"
    current=source_evidence(json.loads(row['payload']))
    if p.exists() and read_json(p)!=current:raise ValueError('审核源视频发生变化，禁止保存或放行')
    if not p.exists():durable_json(p,current)
    return current


def review_signature(row):
    return digest({'payload':json.loads(row['payload']),'labels':json.loads(row['labels']),
                   'revision':row['revision']})


def verify_approval(spec,row):
    if row['state']!='approved' or row['review_hash']!=review_signature(row):
        raise ValueError('缺少匹配当前标签版本的人工审核凭据')
    verify_source(spec,row)


def edit(spec,identity,revision,steps=None,*,action='save',reason=''):
    if not required(spec):raise ValueError('此运行版本没有人工审核关卡，请新建七阶段任务')
    if type(revision) is not int:raise ValueError('缺少审核版本，请重新打开结果')
    if action not in ('save','approve','reject','reopen'):raise ValueError('无效审核操作')
    root=Path(spec['annotation']['root'])
    with database(spec) as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT * FROM episodes WHERE id=?',(identity,)).fetchone()
        if not row or row['revision']!=revision:raise ValueError('审核版本已变化，请重新打开结果')
        if db.execute("SELECT 1 FROM batches WHERE route=? AND batch_id=? AND state IN ('completed','empty')",(row['route'],row['batch_id'])).fetchone():
            raise ValueError('该条目所属批次已经导出或发布，历史审核版本不可改写')
        allowed=('approved',) if action=='reopen' else ('needs_review','reviewing')
        if row['state'] not in allowed or released(spec):
            raise ValueError('该条目未进入可编辑审核状态，已通过的版本不可改写')
        if action=='reject':
            try:evidence=verify_source(spec,row)
            except (OSError,ValueError) as exc:evidence={'source_validation_error':str(exc)}
        else:evidence=verify_source(spec,row)
        payload=json.loads(row['payload'])
        if action=='reopen':
            steps=json.loads(row['labels']);labels=row['labels'];state='reviewing'
        elif action=='reject':
            if not isinstance(reason,str) or not reason.strip():raise ValueError('请填写不合格原因')
            labels=None;state='rejected'
        else:
            stages.validate(steps,payload['length'])
            labels=json.dumps(steps);state='approved' if action=='approve' else 'reviewing'
        rev=revision+1;now=time.time_ns()
        signature=digest({'payload':payload,'labels':steps,'revision':rev}) if action=='approve' else None
        import getpass
        receipt={'reviewed_by':getpass.getuser(),'job':identity,'revision':rev,'action':action,'steps':steps if labels else None,
                 'source':evidence,'reviewed_ns':now,'reason':reason,'review_hash':signature}
        # Durable content-addressed receipt first; a failed transaction leaves an
        # unreferenced receipt, never an approved queue row without its evidence.
        durable_json(root/'reviews'/identity/f'{rev}-{digest(receipt)}.json',receipt)
        db.execute('UPDATE episodes SET labels=?,state=?,revision=?,review_hash=?,reviewed_ns=?,error=?,updated_ns=? WHERE id=?',
                   (labels,state,rev,signature,now if action in ('approve','reject') else None,reason or None,now,identity))
        db.commit()
    return {'revision':rev,'state':state}


def freeze(spec):
    """Request a graceful capture boundary; the capture role seals under its lock."""
    if not required(spec):raise ValueError('未启用七阶段人工审核')
    if cycle(spec)['state']!='collecting':return cycle(spec)
    from . import directory_round
    if not directory_round.gate_ready(spec):raise ValueError('本轮目录清单尚未完成转换和自动标注，请等待；需要退出时请正常停止任务')
    value={**cycle(spec),'state':'sealing','requested_ns':time.time_ns()}
    durable_json(Path(spec['annotation']['root'])/'review-cycle.json',value)
    return value


def seal_if_requested(spec,queue):
    if not required(spec) or cycle(spec)['state']!='sealing':return
    from convest.capture import daily
    # No new capture runs in this state. BatchPublisher obtains the same lock
    # used by publication and performs full hash/video verification before sealing.
    for route in spec['capture']['routes']:
        daily.publisher(spec['capture'],route).seal_active()
    errors=daily.maintain(spec['capture'],queue)
    if errors:raise ValueError(str(errors))
    value={**cycle(spec),'state':'review','sealed_ns':time.time_ns()}
    durable_json(Path(spec['annotation']['root'])/'review-cycle.json',value)


def release(spec,acknowledge_errors=False):
    if cycle(spec)['state']!='review':raise ValueError('请先结束本批并等待封存完成')
    from . import directory_round
    report=directory_round.progress(spec)
    if report and not report.get('ready'):raise ValueError('目录清单尚未全部处理完成')
    exceptions=report.get('exceptions',[]) if report else []
    if exceptions and acknowledge_errors is not True:raise ValueError('本轮有输入异常，请查看异常清单并明确确认排除后再放行')
    with database(spec) as db:
        from .service import discover
        discover(spec,db)
        if report:
            ids=directory_round.recording_ids(spec)
            rows=[r for r in db.execute("SELECT * FROM episodes WHERE state!='history_skipped'") if r['recording_id'] in ids]
        else:rows=db.execute("SELECT * FROM episodes WHERE state!='history_skipped' AND updated_ns>=?",(cycle(spec).get('started_ns',0),)).fetchall()
        if (not rows and not report) or any(r['state'] not in ('approved','rejected') for r in rows):
            raise ValueError('请先逐条审核通过或明确标记不合格；保存草稿不代表通过')
        for row in rows:
            if row['state']=='approved':verify_approval(spec,row)
        approved={r['id']:r['review_hash'] for r in rows if r['state']=='approved'}
    value={**cycle(spec),'state':'released','approved':approved,'released_ns':time.time_ns(),'input_exceptions':exceptions}
    durable_json(Path(spec['annotation']['root'])/'review-cycle.json',value)
    return value


def next_cycle(spec):
    if not released(spec):
        with database(spec) as db:
            empty=not db.execute('SELECT 1 FROM episodes LIMIT 1').fetchone()
        if cycle(spec)['state']=='review' and empty:
            value={'state':'collecting','started_ns':time.time_ns()}
            durable_json(Path(spec['annotation']['root'])/'review-cycle.json',value)
            return value
        raise ValueError('请先审核放行当前批次')
    with database(spec) as db:
        if db.execute("SELECT 1 FROM batches WHERE state NOT IN ('completed','empty') LIMIT 1").fetchone():
            raise ValueError('请等待本批审核成品生成完成')
    import sqlite3
    for path,table in [(Path(spec['capture']['state_root'])/'queue.sqlite3','uploads'),
                       (Path(spec['training']['state_root'])/'queue.sqlite3','tasks')]:
        if not path.exists():raise ValueError('请等待本批转换及上传完成后接收下一批')
        with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
            terminal=('completed','annotation_excluded') if table=='uploads' else ('uploaded',)
            if db.execute('SELECT 1 FROM '+table+' WHERE state NOT IN ('+','.join('?' for _ in terminal)+') LIMIT 1',terminal).fetchone():
                raise ValueError('请等待本批转换及上传完成后接收下一批')
    from . import directory_round
    if directory_round.enabled(spec):
        directory_round.reset(spec)
        return cycle(spec)
    # Keep historic review receipts and delivery revisions immutable.
    value={'state':'collecting','started_ns':time.time_ns()}
    durable_json(Path(spec['annotation']['root'])/'review-cycle.json',value)
    return value
