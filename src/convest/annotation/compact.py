"""Compact persisted annotation contract; project legacy records without mutating them."""
from copy import deepcopy

FIELDS = ('start_frame', 'end_frame', 'phase', 'text', 'target_id', 'decision', 'return_condition')
MODEL_FIELDS = frozenset(('start_sec', 'end_sec', 'phase', 'text', 'target_id', 'decision', 'return_condition'))


def project(steps):
    from . import stages
    if stages.is_stages(steps):return stages.canonical(steps)
    result = []
    for step in steps:
        row = {key: deepcopy(step.get(key)) for key in FIELDS}
        if row['decision'] is not None:
            row['decision'] = {key: row['decision'].get(key) for key in ('choice', 'target_id')}
        if row['return_condition'] is not None:
            row['return_condition'] = {'left_side_count': row['return_condition'].get('left_side_count')}
        result.append(row)
    return result


def validate(steps):
    from . import stages
    if stages.is_stages(steps):
        stages.validate(steps,steps[-1]['end_frame']);return
    import re
    from .workflow import PHASES, DECISION_PHASES
    choices = {'CONTINUE_PICK', 'RETRY_SAME_TARGET', 'REGRASP', 'STOP', 'UNKNOWN'}
    def target(value):
        return value is None or isinstance(value, str) and re.fullmatch(r'T[1-9][0-9]*', value)
    for i, step in enumerate(steps):
        if set(step) != set(FIELDS) or step['phase'] not in PHASES or not target(step['target_id']):
            raise ValueError('Invalid compact annotation phase/fields/target')
        decision = step['decision']
        if decision is not None:
            if (not isinstance(decision, dict) or set(decision) != {'choice', 'target_id'}
                    or decision['choice'] not in choices or not target(decision['target_id'])
                    or step['phase'] not in DECISION_PHASES):
                raise ValueError('Invalid compact decision')
            if decision['choice'] == 'STOP' and step['phase'] != 'END':
                raise ValueError('STOP requires END')
            if step['target_id'] is not None and decision['target_id'] not in (None, step['target_id']):
                raise ValueError('Decision and action target differ')
        condition = step['return_condition']
        if step['phase'] == 'RETURN_AND_RELEASE':
            if not isinstance(condition, dict) or set(condition) != {'left_side_count'}:
                raise ValueError('Return requires left_side_count')
            count = condition['left_side_count']
            if count is not None and (type(count) is not int or count < 0):
                raise ValueError('Invalid left_side_count')
        elif condition is not None:
            raise ValueError('Only return has return_condition')
        if step['phase'] == 'END' and i != len(steps) - 1:
            raise ValueError('END must be last')
