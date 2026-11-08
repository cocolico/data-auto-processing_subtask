"""A durable finite input snapshot: finish conversion and annotation before review."""
import json
from pathlib import Path
import re
import sqlite3
import time
from convest.config import digest
from convest.ingest.batches import durable_json
from .native import read_json


def enabled(spec):
    return bool(spec.get('annotation',{}).get('enabled') and spec['annotation'].get('collection_mode')=='directory')


def location(spec):return Path(spec['annotation']['root'])/'directory-round.json'


def capture_rows(spec):
    p=Path(spec['capture']['state_root'])/'queue.sqlite3'
    if not p.exists():return {}
    with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        return {(r['route'],r['episode']):dict(r) for r in db.execute('SELECT * FROM jobs')}


def ensure(spec):
    if not enabled(spec):return None
    p=location(spec)
    identity=digest([(r['id'],r['source'],r['robot']) for r in spec['capture']['routes']])
    if p.exists():
        value=read_json(p)
        if value['input_identity']!=identity:raise ValueError('目录审核清单与当前输入不符，拒绝重用')
        gate=p.parent/'review-cycle.json'
        if not gate.exists() or read_json(gate).get('directory_round')!=value['id']:
            durable_json(gate,{'state':'collecting','started_ns':value['created_ns'],'directory_round':value['id']})
        return value
    rows=capture_rows(spec);entries=[]
    historical=set();ap=Path(spec['annotation']['root'])/'queue.sqlite3'
    if ap.exists():
        with sqlite3.connect(ap.as_uri()+'?mode=ro',uri=True) as db:
            groups={}
            for recording,state,route,batch in db.execute('SELECT recording_id,state,route,batch_id FROM episodes'):
                groups.setdefault(recording,[]).append((state,route,batch))
            for recording,items in groups.items():
                if all(state in ('approved','rejected') and db.execute("SELECT 1 FROM batches WHERE route=? AND batch_id=? AND state IN ('completed','empty')",(route,batch)).fetchone() for state,route,batch in items):historical.add(recording)
    for route in spec['capture']['routes']:
        root=Path(route['source'])
        if root.is_symlink() or not root.is_dir():raise ValueError('目录快照输入不存在或为软链接：'+str(root))
        for bag in sorted(root.iterdir(),key=lambda p:p.name):
            if not re.fullmatch(r'episode[0-9]+',bag.name) or bag.is_symlink() or not bag.is_dir():continue
            old=rows.get((route['id'],bag.name))
            if old and old['state']=='converted' and json.loads(old.get('proof') or '{}').get('recording_id') in historical:continue
            policy=route.get('history',{'mode':'all'})
            if not old and policy['mode']!='all' and (policy['mode']!='include' or bag.name not in policy.get('episodes',[])):continue
            entries.append({'route':route['id'],'episode':bag.name})
    now=time.time_ns()
    value={'schema_version':1,'input_identity':identity,'created_ns':now,'entries':entries,
           'id':digest([identity,entries,now])}
    # Existing input errors get one fresh structural check on an explicit new round.
    dbpath=Path(spec['capture']['state_root'])/'queue.sqlite3'
    if dbpath.exists():
        with sqlite3.connect(dbpath) as db:
            for e in entries:
                db.execute("UPDATE jobs SET state='waiting_stable',error=NULL,next_ns=0 WHERE route=? AND episode=? AND state='waiting_input'",(e['route'],e['episode']))
    cycle=Path(spec['annotation']['root'])/'review-cycle.json'
    if cycle.exists():
        old=read_json(cycle)
        durable_json(cycle.parent/'round-history'/f"before-{value['id']}.json",old)
    # Manifest first; recovery initializes only this matching round's gate.
    durable_json(p,value)
    durable_json(cycle,{'state':'collecting','started_ns':now,'directory_round':value['id']})
    return value


def progress(spec):
    if not enabled(spec):return None
    p=location(spec)
    if not p.exists():return {'mode':'directory','state':'not_started','total':0}
    scope=read_json(p);rows=capture_rows(spec)
    ap=Path(spec['annotation']['root'])/'queue.sqlite3';labels={}
    if ap.exists():
        with sqlite3.connect(ap.as_uri()+'?mode=ro',uri=True) as db:
            for recording,state in db.execute('SELECT recording_id,state FROM episodes'):
                labels.setdefault(recording,[]).append(state)
    converted=annotated=0;exceptions=[];pending=[]
    terminal={'needs_review','reviewing','approved','rejected','completed','excluded'}
    for e in scope['entries']:
        row=rows.get((e['route'],e['episode']));state=row['state'] if row else 'pending'
        error=row.get('error') if row else None
        definitive=state in ('excluded','failed','conflict') or (state=='waiting_input' and error and error.startswith(('ValueError:','KeyError:','FileNotFoundError:','JSONDecodeError:')))
        if definitive:
            exceptions.append({**e,'state':state,'error':error or '输入未通过转换质量检查'});continue
        if state=='converted':
            converted+=1
            proof=json.loads(row['proof']) if row.get('proof') else {}
            states=labels.get(proof.get('recording_id'),[])
            if states and all(s in terminal for s in states):annotated+=1
            else:pending.append({**e,'state':'waiting_annotation'})
        else:pending.append({**e,'state':state,'error':error})
    return {'mode':'directory','id':scope['id'],'total':len(scope['entries']),
            'converted':converted,'annotated':annotated,'exceptions':exceptions,
            'pending_count':len(pending),'pending':pending,
            'ready':not pending and converted+len(exceptions)==len(scope['entries'])}


def apply_scope(spec,cfg):
    scope=ensure(spec);report=progress(spec)
    if scope is None:return
    errors={(e['route'],e['episode']) for e in report['exceptions']}
    cfg['round_episodes']={r['id']:[e['episode'] for e in scope['entries']
                                  if e['route']==r['id'] and (e['route'],e['episode']) not in errors]
                           for r in cfg['routes']}


def gate_ready(spec):
    return not enabled(spec) or progress(spec).get('ready',False)


def reset(spec):
    p=location(spec)
    if p.exists():
        old=read_json(p)
        durable_json(p.parent/'round-history'/f"directory-{old['id']}.json",old)
        p.unlink()
    return ensure(spec)


def recording_ids(spec):
    scope=read_json(location(spec));rows=capture_rows(spec)
    return {json.loads(r.get('proof') or '{}').get('recording_id') for e in scope['entries']
            for r in [rows.get((e['route'],e['episode']))] if r and r['state']=='converted'} - {None}
