"""Local manual review copies; isolated from the annotation and delivery queues."""
import json
import os
from pathlib import Path
import re
import tempfile

FINE = {'GRASP_CLUSTER', 'PRESENT_CLUSTER', 'APPROACH_AND_GRASP', 'DETACH_AND_PLACE',
        'RETURN_AND_RELEASE', 'REORIENT_STEM', 'END'}
COARSE = {'PICK_UP', 'PICK_TOMATO', 'PUT_BACK', 'ROTATE_STEM'}


def validate(payload):
    length = payload.get('frames')
    if type(length) is not int or length <= 0:
        raise ValueError('视频帧数无效')
    fine = payload.get('steps', [])
    coarse = payload.get('granularity', {}).get('coarse_steps', [])
    for rows, phases, full in ((fine, FINE, True), (coarse, COARSE, False)):
        if not isinstance(rows, list) or (full and not rows):
            raise ValueError('缺少时间轴')
        cursor = 0
        for row in rows:
            a, b = row.get('start_frame'), row.get('end_frame')
            if (type(a) is not int or type(b) is not int or not cursor <= a < b <= length
                    or full and a != cursor or row.get('phase') not in phases):
                raise ValueError('动作边界或类别无效')
            cursor = b
        if full and cursor != length:
            raise ValueError('细动作未覆盖全部帧')
    for c in coarse:
        indices = [i for i, f in enumerate(fine)
                   if f['start_frame'] < c['end_frame'] and f['end_frame'] > c['start_frame']]
        if (not indices or fine[indices[0]]['start_frame'] != c['start_frame']
                or fine[indices[-1]]['end_frame'] != c['end_frame']
                or c.get('fine_step_indices') != indices):
            raise ValueError('粗细动作边界未严格对齐')
    for f in fine:
        parents = [c for c in coarse if c['start_frame'] <= f['start_frame'] and f['end_frame'] <= c['end_frame']]
        if len(parents) != (0 if f['phase'] == 'END' else 1):
            raise ValueError('细动作必须完整对应唯一粗动作，结束静止除外')
    if payload.get('granularity', {}).get('fine_steps') != fine:
        raise ValueError('粗细文件中的细动作副本不一致')


def save(directory, payload):
    validate(payload)
    if not payload.get('manual_review'):
        raise ValueError('仅能保存人工审核副本')
    if not isinstance(directory, str) or not Path(directory).expanduser().is_absolute():
        raise ValueError('请选择绝对路径的本地目录')
    root = Path(directory).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError('请选择本地目录')
    recording = re.sub(r'[^A-Za-z0-9_.-]', '_', str(payload.get('recording_id', 'recording')))[:120]
    episode = payload.get('episode_index')
    if type(episode) is not int or episode < 0:
        raise ValueError('Episode 编号无效')
    target = root / f'{recording}_episode_{episode}_manual_annotations.json'
    if target.is_symlink():
        raise ValueError('保存目标不能是符号链接')
    if target.exists():
        previous = json.loads(target.read_text(encoding='utf-8'))
        if (not previous.get('manual_review') or previous.get('recording_id') != payload.get('recording_id')
                or previous.get('episode_index') != episode):
            raise ValueError('目标文件不是该 Episode 的人工副本，请选择其他目录')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=root, delete=False) as stream:
            temporary = stream.name
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if temporary and Path(temporary).exists():
            Path(temporary).unlink()
    return {'path': str(target)}
