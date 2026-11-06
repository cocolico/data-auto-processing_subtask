"""Read-only previews and allowlisted camera paths, addressed by queue job IDs."""
import json
from importlib.util import find_spec
from pathlib import Path

from .service import database
from .native import read_json,path_for
from .granularity import derive


def job(spec,identity):
    with database(spec) as db:
        row=db.execute("SELECT * FROM episodes WHERE id=? AND state IN ('completed','needs_review')",(identity,)).fetchone()
    if not row:raise ValueError('标注结果不存在或尚未完成')
    payload=json.loads(row['payload'])
    return payload,json.loads(row['labels'])


def describe(spec,identity):
    payload,labels=job(spec,identity)
    from .compact import project
    labels=project(labels)
    source=Path(payload['source'])
    info=read_json(source/'meta/info.json')
    cameras=[k for k,v in info['features'].items() if v['dtype']=='video']
    camera_status={key:{'available':(source/path_for(info,payload['source_ep'],video_key=key)).is_file(),
                        'label':{'cam0':'外部总览','cam1':'右手腕','cam2':'左手腕'}.get(key.rsplit('.',1)[-1],key)}
                   for key in cameras}
    protocol=None
    responses=sorted((Path(spec.get('annotation',{}).get('root','/nonexistent'))/'requests'/identity).glob('call-*/response.json'))
    if responses:
        protocol=read_json(responses[-1]).get('protocol')
    return {'id':identity,'episode_index':payload['source_ep'],'recording_id':payload['recording_id'],
            'fps':payload['fps'],'frames':payload['length'],'model':payload['settings']['model'],
            'steps':labels,'granularity':derive(labels,payload['fps'],payload['length']),
            'cameras':cameras,'camera_status':camera_status,'protocol':protocol,'readonly':True,
            'viewer_sdks':{'rrd':find_spec('rerun') is not None,'mcap':find_spec('foxglove') is not None},
            'video_available':(source/path_for(info,payload['source_ep'],video_key='observation.images.cam0')).is_file()}


def video(spec,identity,camera):
    if camera not in ('observation.images.cam0','observation.images.cam1','observation.images.cam2'):
        raise ValueError('相机无效')
    payload,_=job(spec,identity)
    root=Path(payload['source'])
    allowed=[Path(r['output']).resolve() for r in spec['capture']['routes']]
    if not any(root.resolve().is_relative_to(p) for p in allowed):raise ValueError('预览源超出当前任务范围')
    info=read_json(root/'meta/info.json')
    path=root/path_for(info,payload['source_ep'],video_key=camera)
    if not path.resolve().is_relative_to(root.resolve()) or path.is_symlink() or not path.is_file():
        raise ValueError('本地视频已释放或不可用，标签和来源仍保留')
    return path
