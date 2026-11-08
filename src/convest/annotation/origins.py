"""Presentation-only source names. Never change queue IDs or approved payloads."""
import json
from pathlib import Path
import re
import sqlite3


def recording_names(spec):
    state=spec.get('capture',{}).get('state_root')
    if not state:return {}
    path=Path(state)/'queue.sqlite3'
    if not path.exists():return {}
    result={}
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='recordings'").fetchone():return {}
        for route,name,raw in db.execute('SELECT route,episode,payload FROM recordings'):
            payload=json.loads(raw)
            recording=payload.get('recording_id')
            if recording and re.fullmatch(r'episode[0-9]+',name):
                key=(route,recording)
                result[key]=name if key not in result or result[key]==name else None
    return result


def describe(route,recording_id,names):
    name=names.get((route,recording_id))
    if not name:
        candidate=Path(recording_id or '').name
        if re.fullmatch(r'episode[0-9]+',candidate):name=candidate
    return {'source_episode':name,'source_episode_number':int(name[7:]) if name else None,
            'source_label':f'{route} / {name}' if name else f'{route} / 原始编号待确认'}
