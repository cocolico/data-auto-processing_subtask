"""Deterministic coarse stages derived from fine annotations, never a second inference.

Frame intervals are half-open. Adjacent operations in the same coarse class
merge across fruit attempts; the original fine boundaries and quality remain.
Unmapped legacy/END phases are explicit exclusions, not invented actions.
"""
from copy import deepcopy
import math

SCHEMA = 'tomato-dual-granularity-v2'
FILENAME = 'annotation_granularity.jsonl'
CATEGORIES = (
    ('PICK_UP', '拿起番茄', 'Pick up the tomato truss.'),
    ('PICK_TOMATO', '摘取番茄', 'Pick tomatoes and place them in the left tray.'),
    ('PUT_BACK', '放回番茄', 'Put back the tomato truss.'),
    ('ROTATE_STEM', '旋转番茄杆', 'Rotate the tomato stem.'),
)
PHASE_TO_CATEGORY = {
    'GRASP_CLUSTER': 0, 'PRESENT_CLUSTER': 0,
    'APPROACH_AND_GRASP': 1, 'DETACH_AND_PLACE': 1,
    'RETURN_AND_RELEASE': 2, 'REORIENT_STEM': 3,
}


def derive(steps, fps, length):
    """Return both layers without changing model steps or their evidence fields."""
    if not isinstance(fps, (int, float)) or isinstance(fps, bool) or not math.isfinite(fps) or fps <= 0:
        raise ValueError('Invalid annotation FPS')
    if type(length) is not int or length <= 0 or not steps:
        raise ValueError('Nonempty annotation timeline required')
    coarse, excluded, cursor = [], [], 0
    from .compact import project
    fine = project(steps)
    for index, step in enumerate(fine):
        start, end = step.get('start_frame'), step.get('end_frame')
        if type(start) is not int or type(end) is not int or not cursor == start < end <= length:
            raise ValueError('Fine annotations must cover every frame exactly once')
        cursor = end
        category_id = PHASE_TO_CATEGORY.get(step.get('phase'))
        if category_id is None:
            excluded.append({'fine_step_index': index})
            continue
        phase, text_zh, text = CATEGORIES[category_id]
        if coarse and coarse[-1]['phase'] == phase and coarse[-1]['end_frame'] == start:
            segment = coarse[-1]
            segment.update(end_frame=end)
        else:
            segment = {'phase': phase, 'text': text, 'text_zh': text_zh,
                       'start_frame': start, 'end_frame': end, 'fine_step_indices': []}
            coarse.append(segment)
        segment['fine_step_indices'].append(index)
    if cursor != length:
        raise ValueError('Unannotated episode tail')
    return {'schema_version': SCHEMA, 'fps': fps, 'length': length,
            'interval_convention': '[start_frame,end_frame)', 'fine_step_index_base': 0,
            'method': 'deterministic_fine_phase_mapping',
            'categories': [{'phase': p, 'text': en, 'text_zh': zh}
                           for i, (p, zh, en) in enumerate(CATEGORIES)],
            'fine_steps': fine, 'coarse_steps': coarse, 'excluded_intervals': excluded}


def records(selected, labels, fps):
    """Use delivery episode IDs and retain the immutable source identity."""
    return [{'episode_index': i, 'source_episode_index': ep['episode_index'],
             'source_recording_id': ep.get('source_recording_id'),
             **derive(labels[ep['episode_index']], fps, ep['length'])}
            for i, ep in enumerate(selected)]
