"""Read-only GUI access to isolated local annotation trials."""
import json
from pathlib import Path
import re

from convest.config import WORKSPACE
from .granularity import derive


ROOT = WORKSPACE / 'outputs/annotation-smoke'
RUN_NAME = re.compile(r'20\d{6}-\d{6}-[a-z0-9]{8}')
CAMERAS = ('cam0', 'cam1', 'cam2')


def run_path(name):
    if not RUN_NAME.fullmatch(name or ''):
        raise ValueError('单独测试编号无效')
    root = ROOT.resolve()
    path = root / name
    if path.is_symlink() or not path.is_dir() or path.resolve() != path:
        raise ValueError('单独测试不存在')
    return path


def _read(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('单独测试结果缺失')
    return json.loads(path.read_text())


def list_runs():
    if not ROOT.is_dir():
        return []
    result = []
    for path in sorted(ROOT.iterdir(), reverse=True):
        if not RUN_NAME.fullmatch(path.name) or path.is_symlink() or not path.is_dir():
            continue
        if not all((path / name).is_file() for name in ('annotation_test_report.json', 'labels-retry-1.json')):
            continue
        try:
            report = _read(path / 'annotation_test_report.json')
        except (OSError, ValueError):
            continue
        result.append({'id': path.name, 'episode_index': report.get('source_episode_index', 0),
                       'status': report.get('status', 'draft')})
    return result


def describe(name):
    path = run_path(name)
    report = _read(path / 'annotation_test_report.json')
    labels = _read(path / 'labels-retry-1.json')
    granularity = _read(path / 'annotation_granularity.jsonl')
    if report.get('source_episode_index') != labels.get('episode') or labels.get('episode') != granularity.get('source_episode_index'):
        raise ValueError('单独测试的 Episode 信息不一致')
    fps = labels['dataset_fps']
    steps = granularity['fine_steps']
    if granularity != {'episode_index': granularity['episode_index'],
                       'source_episode_index': granularity['source_episode_index'],
                       'source_recording_id': granularity.get('source_recording_id'),
                       **derive(steps, fps, report['frames'])}:
        raise ValueError('单独测试的粗细标注不一致')
    cameras = ['observation.images.' + camera for camera in CAMERAS]
    status = {camera: {'available': (path / f'episode_000000_{camera}.mp4').is_file(),
                       'label': {'cam0': '外部总览', 'cam1': '右手腕', 'cam2': '左手腕'}[camera]}
              for camera in CAMERAS}
    return {'id': 'trial:' + name, 'trial': True, 'episode_index': labels['episode'],
            'recording_id': Path(report['source']).name, 'fps': fps, 'frames': report['frames'],
            'model': report['model'], 'protocol': 'tomato-seven-actions-multiview-v7',
            'steps': steps, 'granularity': granularity, 'cameras': cameras,
            'camera_status': {'observation.images.' + camera: value for camera, value in status.items()},
            'video_available': status['cam0']['available'], 'readonly': True,
            'viewer_sdks': {'rrd': False, 'mcap': False},
            'notice': '单独测试草稿：末段视觉复核未通过，不能用于训练或上传。'}


def video(name, camera):
    if camera not in CAMERAS:
        raise ValueError('相机无效')
    path = run_path(name) / f'episode_000000_{camera}.mp4'
    if path.is_symlink() or not path.is_file():
        raise ValueError('本地测试视频不存在')
    return path
