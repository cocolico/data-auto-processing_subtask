"""Seven task-level tomato actions and factual evidence metadata.

Checks declared event consistency, not visual truth. Legacy
fine-grained annotations continue to use events.py.
"""
WORKFLOW_FIELDS = frozenset(('evidence_note', 'return_condition'))
PHASES = frozenset(('GRASP_CLUSTER', 'PRESENT_CLUSTER', 'APPROACH_AND_GRASP',
                   'DETACH_AND_PLACE', 'RETURN_AND_RELEASE', 'REORIENT_STEM',
                   'END'))
DECISION_PHASES = frozenset(('APPROACH_AND_GRASP', 'RETURN_AND_RELEASE',
                            'REORIENT_STEM', 'END'))
SINGLE_FRUIT = frozenset(('APPROACH_AND_GRASP', 'DETACH_AND_PLACE'))


def optional_text(value):
    return value is None or isinstance(value, str) and bool(value.strip())


def validate_workflow(steps):
    attempts = {}
    maximum = 0
    previous_attempt = None
    collected = set()
    unknown_collected = False
    for index, step in enumerate(steps):
        prefix = f'第{index + 1}段：'
        phase, attempt, target = step['phase'], step['attempt_id'], step['target_id']
        outcome, evidence = step['outcome'], step['outcome_evidence']
        if phase not in PHASES or not WORKFLOW_FIELDS <= step.keys():
            raise ValueError(prefix + '缺少七动作协议字段或阶段无效')
        if not optional_text(step['evidence_note']) or not optional_text(evidence):
            raise ValueError(prefix + '证据说明和结果证据必须为文本或 null')
        if outcome not in ('success', 'failure', 'unknown', 'not_applicable'):
            raise ValueError(prefix + '动作结果无效')
        if attempt is not None and (type(attempt) is not int or attempt < 1):
            raise ValueError(prefix + '尝试编号必须为正整数或 null')
        condition = step['return_condition']
        if phase == 'RETURN_AND_RELEASE':
            if not isinstance(condition, dict) or set(condition) != {'left_side_count', 'evidence'}:
                raise ValueError(prefix + '放回动作需要计数条件，无法确认时计数为 null')
            count = condition['left_side_count']
            if count is not None and (type(count) is not int or count < 0):
                raise ValueError(prefix + '局部番茄计数必须为非负整数或 null')
            if not optional_text(condition['evidence']):
                raise ValueError(prefix + '放回条件证据无效')
        elif condition is not None:
            raise ValueError(prefix + '只有放回阶段填写 return_condition')
        decision = step['decision']
        if decision is not None:
            if phase not in DECISION_PHASES:
                raise ValueError(prefix + '决策必须属于本阶段起点，不能提前记录结果')
            if decision['choice'] == 'STOP' and phase != 'END':
                raise ValueError(prefix + 'STOP 只能位于 END')
            if decision['choice'] == 'RETRY_SAME_TARGET' and phase != 'APPROACH_AND_GRASP':
                raise ValueError(prefix + '重试决策应记录在新接近抓取阶段')
            remembered = decision['picked_ids']
            if unknown_collected:
                if remembered is not None:
                    raise ValueError(prefix + '已放置目标身份未知，picked_ids 必须为 null')
            elif remembered is None or set(remembered) != collected:
                raise ValueError(prefix + 'picked_ids 只能包含此前已完成放置的目标')
        if phase == 'END':
            if (index != len(steps)-1 or step['left_action'] is not None or
                    step['right_action'] is not None or not evidence):
                raise ValueError(prefix + 'END 只能是双手低位静止的最后一段，需姿态证据')
        if phase not in SINGLE_FRUIT:
            if attempt is not None or target is not None or outcome != 'not_applicable':
                raise ValueError(prefix + '整串/结束动作不声明单果尝试或结果')
            if phase != 'END' and evidence is not None:
                raise ValueError(prefix + '整串动作证据放入描述或放回条件，不填写单果结果')
            continue
        if attempt is None or outcome == 'not_applicable':
            raise ValueError(prefix + '单果阶段必须有尝试编号和结果')
        if not step['left_action'] or not step['right_action']:
            raise ValueError(prefix + '单果阶段须描述左手操作和右手托持')
        if outcome in ('success', 'failure') and not evidence:
            raise ValueError(prefix + '成功或失败须有可见对象变化证据')
        if target is not None and target in collected:
            raise ValueError(prefix + '不能再次摘取已确认放置的目标')
        if phase == 'APPROACH_AND_GRASP':
            if attempt <= maximum:
                raise ValueError(prefix + '每次新的单果操作须使用递增尝试编号')
            attempts[attempt] = {'target': target, 'grasped': outcome == 'success', 'placed': False}
            maximum = attempt
        else:
            prior = attempts.get(attempt)
            if (prior is None or previous_attempt != attempt or not prior['grasped'] or
                    prior['placed'] or prior['target'] != target):
                raise ValueError(prefix + '摘取送盘前缺少同一目标、同一尝试的已确认抓稳')
            if outcome == 'success':
                # The combined action attests detachment AND deposit together;
                # the prompt requires both visible events in outcome_evidence.
                prior['placed'] = True
                if target is None:
                    unknown_collected = True
                else:
                    collected.add(target)
        previous_attempt = attempt
