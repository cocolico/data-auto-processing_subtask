"""Single-track, ordered seven-stage protocol (frame intervals are half open)."""
from copy import deepcopy
import math

SCHEMA = 'tomato-seven-stages-v1'
FILENAME = 'annotation_stages.jsonl'
CATEGORIES = (
 ('FIRST_LIFT', '第一次拿起', 'Right hand grasps and lifts the truss to the center'),
 ('FIRST_PICK', '第一次采摘投放', 'Right hand holds truss while left hand picks tomatoes into left basket'),
 ('RETURN_TRUSS', '放回番茄串', 'Right hand returns the truss to the right plate'),
 ('REPOSITION', '调整番茄', 'Right fingers reposition the truss on the right plate'),
 ('SECOND_LIFT', '第二次拿起', 'Right hand grasps and lifts the truss to the center'),
 ('SECOND_PICK', '继续采摘投放', 'Right hand holds truss while left hand picks tomatoes into left basket'),
 ('RETURN_BARE_STEM', '放回裸杆', 'Right hand returns the bare stem to the right plate'),
)
PHASES = tuple(p for p, _, _ in CATEGORIES)
FIELDS = {'stage_index', 'phase', 'text', 'start_frame', 'end_frame'}


def is_stages(steps):
    return bool(steps) and any(s.get('phase') in PHASES for s in steps)


def canonical(steps):
    result = []
    for s in steps:
        if s.get('phase') not in PHASES:
            raise ValueError('不是方案 B 七阶段，旧标注需重新审核')
        i = PHASES.index(s['phase'])
        result.append({'stage_index': i, 'phase': PHASES[i], 'text': CATEGORIES[i][2],
                       'start_frame': s['start_frame'], 'end_frame': s['end_frame']})
    return result


def validate(steps, length):
    if type(length) is not int or length < 7 or not isinstance(steps, list) or len(steps) != 7:
        raise ValueError('每条 Episode 必须有且仅有七阶段，每阶段至少一帧')
    cursor = 0
    for i, s in enumerate(steps):
        if (set(s) != FIELDS or s['phase'] != PHASES[i] or type(s['stage_index']) is not int
                or s['stage_index'] != i or s['text'] != CATEGORIES[i][2]
                or type(s['start_frame']) is not int or type(s['end_frame']) is not int
                or not cursor == s['start_frame'] < s['end_frame'] <= length):
            raise ValueError('七阶段必须按顺序连续覆盖、文字固定且各有有效帧')
        cursor = s['end_frame']
    if cursor != length:
        raise ValueError('七阶段未覆盖视频尾帧')


def record(steps, fps, length):
    validate(steps, length)
    if type(fps) not in (int, float) or not math.isfinite(fps) or fps <= 0:
        raise ValueError('无效帧率')
    return {'schema_version': SCHEMA, 'fps': fps, 'length': length,
            'interval_convention': '[start_frame,end_frame)', 'steps': deepcopy(steps)}


def records(selected, labels, fps):
    return [{'episode_index': i, 'source_episode_index': ep['episode_index'],
             'source_recording_id': ep.get('source_recording_id'),
             **record(labels[ep['episode_index']], fps, ep['length'])}
            for i, ep in enumerate(selected)]


def normalize(value, length, fps):
    if not isinstance(value, dict) or set(value) != {'steps'} or not isinstance(value['steps'], list):
        raise ValueError('模型须返回 steps 数组')
    result = []
    for row in value['steps']:
        if not isinstance(row, dict) or set(row) != {'stage_index','phase','text','start_sec','end_sec'}:
            raise ValueError('七阶段模型字段无效')
        a, b = row['start_sec'], row['end_sec']
        if any(type(x) not in (int,float) or not math.isfinite(x) for x in (a,b)):
            raise ValueError('模型时间无效')
        result.append({k:v for k,v in row.items() if k not in ('start_sec','end_sec')} |
                      {'start_frame':round(a*fps),'end_frame':round(b*fps)})
    validate(result, length)
    return result
