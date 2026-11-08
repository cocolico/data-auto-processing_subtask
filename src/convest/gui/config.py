"""GUI settings and immutable per-task runtime configurations, separate from legacy queues."""
from copy import deepcopy
import json
from pathlib import Path
import re
import uuid
import yaml
from convest.config import WORKSPACE, digest
from convest.ingest.batches import durable_json
from convest.capture.config import load as load_capture
from .transport import endpoint
from .oss_transfer import DEFAULT_DELIVERY

ROOT = WORKSPACE / 'outputs/gui'


def component(value):
    if not isinstance(value,str):raise ValueError('名称无效')
    value=value.strip()
    if not value or value in ('.', '..') or any(x in value for x in '/\\\n\r\x00') or len(value) > 100:
        raise ValueError('项目和场景名称必须是单层目录名称')
    return value.strip()


def path(value):
    if not isinstance(value, str) or any(x in value for x in '\n\r\x00'):
        raise ValueError('目录路径无效')
    p = Path(value).expanduser()
    if not p.is_absolute() or '..' in p.parts or str(p) == '/':
        raise ValueError('请选择绝对目录，不能选择文件系统根目录或包含 .. 的路径')
    return str(p)


def default_upload_paths(task):
    project, robot, scene = task['project'], task['robot'], task['scene']
    base = f'/data/data-demo/real/{project}/data'
    train = f'/mnt/data/train_platform/datasets/{project}_{robot}'
    return {'raw_root': f'{base}/rosbag/{robot}/{scene}',
            'lerobot_root': f'{base}/lerobot/{robot}/{scene}',
            'normal_root': f'{train}/normal/{scene}',
            'speedup_root': f'{train}/speedup/{scene}'}


def new_task(index=1):
    tid = 'task-' + uuid.uuid4().hex[:12]
    return {'id': tid, 'name': f'任务 {index}', 'robot': 'sharpa',
            'project': 'tomato', 'scene': 'full-block-more', 'enabled': True,
            'source': str(ROOT / 'demo-input' / tid),
            'raw_root': '/data/data-demo/real/tomato/data/rosbag/sharpa/full-block-more',
            'lerobot_root': '/data/data-demo/real/tomato/data/lerobot/sharpa/full-block-more',
            'normal_root': '/mnt/data/train_platform/datasets/tomato_sharpa/normal/full-block-more',
            'speedup_root': '/mnt/data/train_platform/datasets/tomato_sharpa/speedup/full-block-more',
            'include_existing': True, 'delete_raw': True, 'auto_upload_paths': True, 'delete_lerobot': False,
            'annotation_enabled': False, 'annotation_include_existing': False,
            'annotation_model': 'doubao-seed-2-0-lite-260428', 'annotation_fps': 5.0,
            'annotation_retries': 2}


def defaults():
    return {'schema_version': 1, 'data_server': {'host': '192.168.10.202', 'port': 22, 'user': 'user'},
            'training_server': {'host': '47.116.73.163', 'port': 28, 'user': 'mengxiang'},
            'training_delivery': deepcopy(DEFAULT_DELIVERY), 'tasks': [new_task()]}


def validate(settings):
    if not isinstance(settings, dict) or settings.get('schema_version') != 1:
        raise ValueError('配置格式无效')
    result = {'schema_version': 1, 'data_server': endpoint(settings.get('data_server')),
              'training_server': endpoint(settings.get('training_server')), 'tasks': []}
    delivery = deepcopy(settings.get('training_delivery', DEFAULT_DELIVERY))
    if not isinstance(delivery, dict) or delivery.get('mode') not in ('oss', 'direct'):
        raise ValueError('训练交付方式无效')
    result['training_delivery'] = {'mode': delivery['mode'],
        'transit_root': path(delivery.get('transit_root')), 'final_root': path(delivery.get('final_root'))}
    a, b = (Path(result['training_delivery'][k]) for k in ('transit_root', 'final_root'))
    if a.is_relative_to(b) or b.is_relative_to(a): raise ValueError('中转目录和最终目录不能重叠')
    ids = set(); sources = []; targets = []
    tasks = settings.get('tasks')
    if not isinstance(tasks, list) or len(tasks) > 12: raise ValueError('最多配置 12 个任务')
    for raw in tasks:
        t = deepcopy(raw)
        if not re.fullmatch(r'task-[a-f0-9]{12}', str(t.get('id', ''))) or t['id'] in ids:
            raise ValueError('任务 ID 无效或重复')
        ids.add(t['id'])
        t['name'] = component(t.get('name')); t['project'] = component(t.get('project')); t['scene'] = component(t.get('scene'))
        if t.get('robot') not in ('sharpa', 'wuji'): raise ValueError('当前仅支持 Sharpa 和 Wuji')
        generated = default_upload_paths(t)
        # Older custom destinations remain manual; never silently redirect old data.
        t.setdefault('auto_upload_paths', all(t.get(k) == v for k, v in generated.items()))
        t.setdefault('delete_lerobot', False)
        t.setdefault('annotation_enabled',False);t.setdefault('annotation_include_existing',False)
        t.setdefault('annotation_model','doubao-seed-2-0-lite-260428');t.setdefault('annotation_fps',5.0);t.setdefault('annotation_retries',2)
        if not isinstance(t['annotation_model'],str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,150}',t['annotation_model']):raise ValueError('标注模型 ID 无效')
        try:t['annotation_fps']=float(t['annotation_fps']);t['annotation_retries']=int(t['annotation_retries'])
        except (ValueError,TypeError):raise ValueError('标注采样率或重试次数无效')
        if not .2<=t['annotation_fps']<=5 or not 0<=t['annotation_retries']<=10:raise ValueError('采样率范围 0.2–5，重试次数范围 0–10')
        for key in ('enabled', 'include_existing', 'delete_raw', 'auto_upload_paths', 'delete_lerobot','annotation_enabled','annotation_include_existing'):
            if type(t.get(key)) is not bool: raise ValueError('任务开关无效')
        if t['auto_upload_paths']: t.update(generated)
        for key in ('source', 'raw_root', 'lerobot_root', 'normal_root', 'speedup_root'): t[key] = path(t.get(key))
        source = Path(t['source']).resolve()
        if source == WORKSPACE or WORKSPACE.is_relative_to(source) or source.is_relative_to(ROOT / 'runs'):
            raise ValueError('本机输入不能包含工程目录或运行输出')
        if t['enabled']:
            for other in sources:
                if source.is_relative_to(other) or other.is_relative_to(source):
                    raise ValueError('启用任务的本机输入目录不能重叠')
            sources.append(source)
            for server, keys in [('data_server', ('raw_root','lerobot_root')), ('training_server', ('normal_root','speedup_root'))]:
                for key in keys:
                    p = Path(t[key]); host = tuple(result[server].values())
                    for old_host, old in targets:
                        if host == old_host and (p.is_relative_to(old) or old.is_relative_to(p)):
                            raise ValueError('启用任务的远端存储目录不能重叠')
                    targets.append((host, p))
        result['tasks'].append({k: t[k] for k in new_task().keys()})
    return result


def load_settings():
    p = ROOT / 'settings.json'
    if p.exists(): return validate(json.loads(p.read_text()))
    cfg = defaults()
    for group in ('A', 'B'):
        (Path(cfg['tasks'][0]['source']) / group).mkdir(parents=True, exist_ok=True)
    durable_json(p, cfg)
    return cfg


def runtime(settings, task):
    delivery = deepcopy(settings.get('training_delivery', DEFAULT_DELIVERY))
    if delivery['mode'] == 'oss':
        for variant in ('normal', 'speedup'):
            if not Path(task[variant + '_root']).is_relative_to(delivery['final_root']):
                raise ValueError('OSS 中转模式下训练目录须位于 ' + delivery['final_root'])
    # Annotation options do NOT recreate capture/conversion queues or source batches.
    semantic = {k: v for k, v in task.items() if k not in ('name', 'enabled', 'auto_upload_paths', 'delete_lerobot') and not k.startswith('annotation_')}
    version = digest({'task': semantic, 'data_server': settings['data_server'], 'training_server': settings['training_server'],
                      'protocol': 'gui-seven-stage-review-v1' if task.get('annotation_enabled') else 'gui-workflow-v1'})[:20]
    binding_path=ROOT/'runtime-bindings'/(task['id']+'.json')
    binding=json.loads(binding_path.read_text()) if binding_path.exists() else None
    if binding and binding.get('requested_version')==version:
        if not re.fullmatch(r'[a-f0-9]{20}',str(binding.get('version',''))):raise ValueError('无效兼容运行版本')
        version=binding['version']
    else:binding=None
    root = ROOT / 'runs' / task['id'] / version
    root.mkdir(parents=True, exist_ok=True)
    source = Path(task['source'])
    groups = [source.name] if source.name in ('A', 'B') else [g for g in ('A','B') if (source / g).is_dir()]
    if not groups: raise ValueError(f'{task["name"]}：输入父目录下需要 A/、B/，也可直接选择其中一个')
    routes = [{'robot': task['robot'], 'group': g, 'source': str(source if source.name == g else source / g)} for g in groups]
    for r in routes:
        if not Path(r['source']).is_dir() or Path(r['source']).is_symlink(): raise ValueError('输入目录不存在或为软链接')
    cfg = {'schema_version': 1, 'state_root': str(root/'capture/state'), 'work_root': str(root/'capture/work'),
           'local_batch_root': str(root/'capture/batches'), 'remote_raw_root': task['raw_root'],
           'remote_dataset_root': task['lerobot_root'], 'delivery_mode': 'daily', 'batch_hours': 24,
           'delete_raw_after_upload': task['delete_raw'], 'cleanup_intermediates': not task.get('annotation_enabled',False),
           'poll_seconds': 5, 'stable_scans': 3, 'stable_seconds': 60, 'retry_seconds': 30,
           'max_attempts': 3, 'max_jobs_per_scan': 1, 'initial_history': {'mode': 'all' if task['include_existing'] else 'none'},
           'routes': routes,
           'route_targets': {f'{task["robot"]}-{g}': {'raw': str(Path(task['raw_root'])/g),
                           'dataset': str(Path(task['lerobot_root'])/g)} for g in groups}}
    file = root/'capture.yaml'
    if not file.exists(): file.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False))
    capture = load_capture(file)
    # Scratch cleanup is operational: immutable LeRobot and originals stay retained.
    if task.get('annotation_enabled'):capture['cleanup_intermediates']=True
    # Naming is prospective metadata, not a new input/transfer queue. Existing
    # catalogued batches keep their own format; only newly opened batches opt in.
    for route in capture['routes']:
        route['batch_name_format']='completion-mmdd-v1'
        route['max_batch_sources']=100
    training = {'capture': capture, 'state_root': str(root/'training/state'), 'output_root': str(root/'training/datasets'),
                'poll_seconds': 5, 'retry_seconds': 30, 'workers': 2, 'cmax': 3.0, 'min_free_gib': 8,
                'training_target': settings['training_server']}
    training['fingerprint'] = digest({'capture': capture['fingerprint'], 'task': semantic,
                                      'training_target': settings['training_server'], 'protocol': 'gui-native-training-v1'})
    if binding:
        if capture['fingerprint']!=binding['capture_fingerprint']:raise ValueError('兼容迁移的采集身份发生变化')
        training['fingerprint']=binding['training_fingerprint']
    spec = {'root': str(root), 'capture_file': str(file), 'capture': capture, 'training': training,
            'training_delivery': delivery, 'task': task, 'data_server': settings['data_server'], 'training_server': settings['training_server']}
    annotation={'enabled':task.get('annotation_enabled',False),'root':str(ROOT/'annotation'/task['id']/version),
        'credential_root':str(ROOT),'include_existing':task.get('annotation_include_existing',False),
        'model':task.get('annotation_model','doubao-seed-2-0-lite-260428'),
        'review_required':True,'collection_mode':'directory','protocol':'tomato-seven-stages-v1',
        'fps':task.get('annotation_fps',5.0),'retries':task.get('annotation_retries',2),'min_free_gib':8}
    spec['annotation']=annotation
    if annotation['enabled']:training['annotation']={'spec':spec['annotation']}
    durable_json(root/'spec.json', spec)
    return root, spec


def bind_training_destinations(settings,task,root):
    """Explicit stopped-task migration for *unpublished* training destination changes.

    Source/robot/data-server and capture semantics must be identical. Preserve
    all queue identities and local artifacts; never use this for published pairs.
    """
    import sqlite3,time
    root=Path(root)
    old=json.loads((root/'spec.json').read_text())
    mutable={'normal_root','speedup_root','name','auto_upload_paths','delete_lerobot'}
    if ({k:v for k,v in old['task'].items() if k not in mutable} !=
            {k:v for k,v in task.items() if k not in mutable} or
            old['data_server']!=settings['data_server'] or old['training_server']!=settings['training_server']):
        raise ValueError('仅允许兼容修改训练上传路径；输入或采集身份已变化')
    state_path=root/'runtime.json'
    if state_path.exists() and json.loads(state_path.read_text()).get('state') not in ('stopped','error'):
        raise ValueError('兼容迁移前必须正常停止任务')
    dbpath=Path(old['training']['state_root'])/'queue.sqlite3'
    if dbpath.exists():
        with sqlite3.connect(dbpath.as_uri()+'?mode=ro',uri=True) as db:
            if db.execute("SELECT 1 FROM tasks WHERE state IN ('uploaded','uploading') LIMIT 1").fetchone():
                raise ValueError('训练成品已发布或上传尚未收尾，不允许重定向历史交付')
    semantic={k:v for k,v in task.items() if k not in ('name','enabled','auto_upload_paths','delete_lerobot') and not k.startswith('annotation_')}
    requested=digest({'task':semantic,'data_server':settings['data_server'],'training_server':settings['training_server'],
                      'protocol':'gui-seven-stage-review-v1' if task.get('annotation_enabled') else 'gui-workflow-v1'})[:20]
    receipt={'requested_version':requested,'version':root.name,'capture_fingerprint':old['capture']['fingerprint'],
             'training_fingerprint':old['training']['fingerprint'],'created_ns':time.time_ns(),
             'old_destinations':{k:old['task'][k] for k in ('normal_root','speedup_root')},
             'new_destinations':{k:task[k] for k in ('normal_root','speedup_root')}}
    durable_json(ROOT/'runtime-bindings'/(task['id']+'.json'),receipt)
    return receipt
