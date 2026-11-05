"""Check declared object events; these checks do not independently see the video."""

EVENT_FIELDS = frozenset(('attempt_id', 'outcome', 'outcome_evidence'))
OUTCOMES = frozenset(('success', 'failure', 'unknown', 'not_applicable'))
TARGET_PHASES = frozenset(('SELECT_TARGET', 'APPROACH_TARGET', 'GRASP_ATTEMPT',
                           'RETRY_TARGET', 'DETACH_TARGET', 'PLACE_IN_TRAY'))
RESULT_PHASES = frozenset(('GRASP_ATTEMPT', 'DETACH_TARGET', 'PLACE_IN_TRAY'))


def validate_events(steps):
    """Validate start-of-step memory against preceding declared outcomes.

    A failed attempt may be followed by a new attempt on the SAME tomato. Hand
    closure alone never changes the detached/collected sets. Unknown outcomes
    remain usable as factual action descriptions, but do not advance progress.
    """
    attempts = {}
    collected = set()
    collected_unknown = False
    previous_attempt = None
    maximum_attempt = 0
    for index, step in enumerate(steps):
        prefix = f'第{index + 1}段：'
        if not EVENT_FIELDS <= step.keys():
            raise ValueError(prefix + '缺少尝试编号、结果或结果证据')
        attempt, outcome = step['attempt_id'], step['outcome']
        evidence, phase, target = step['outcome_evidence'], step['phase'], step['target_id']
        if attempt is not None and (type(attempt) is not int or attempt < 1):
            raise ValueError(prefix + 'attempt_id 必须为正整数或 null')
        if not isinstance(outcome, str) or outcome not in OUTCOMES:
            raise ValueError(prefix + '动作结果无效')
        if evidence is not None and (not isinstance(evidence, str) or not evidence.strip()):
            raise ValueError(prefix + '结果证据必须为非空文本或 null')
        if outcome in ('success', 'failure') and evidence is None:
            raise ValueError(prefix + '成功或失败必须提供可见结果证据')
        if phase in TARGET_PHASES and attempt is None:
            raise ValueError(prefix + '单果操作必须有 attempt_id；目标无法辨认时 target_id 可为 null')
        if phase in RESULT_PHASES and outcome == 'not_applicable':
            raise ValueError(prefix + '抓取、摘离、放置必须明确结果或标为 unknown')
        if phase not in RESULT_PHASES and (outcome != 'not_applicable' or
                                            (phase != 'END' and evidence is not None)):
            raise ValueError(prefix + '只有单果抓取、摘离、放置阶段需要结果；请用动作描述记录其他可见变化')
        decision = step['decision']
        if decision is not None:
            remembered = decision['picked_ids']
            if collected_unknown and remembered is not None:
                raise ValueError(prefix + '有身份未知的已放置番茄，picked_ids 必须为 null')
            if not collected_unknown and (remembered is None or set(remembered) != collected):
                raise ValueError(prefix + 'picked_ids 必须等于本片此前已确认放入果盘的目标，不能包含本段或未来结果')
            if decision['choice'] == 'STOP' and phase != 'END':
                raise ValueError(prefix + 'STOP 只能放在完成所有可见操作后的 END；放回果串仍是动作')
            if decision['choice'] == 'RETRY_SAME_TARGET' and phase != 'RETRY_TARGET':
                raise ValueError(prefix + '重试决策必须放在 RETRY_TARGET')
            if phase == 'RETRY_TARGET' and decision['choice'] != 'RETRY_SAME_TARGET':
                raise ValueError(prefix + 'RETRY_TARGET 必须记录重试同一目标的决策')
        elif phase == 'RETRY_TARGET':
            raise ValueError(prefix + 'RETRY_TARGET 缺少重试决策')
        if phase == 'END':
            if index != len(steps) - 1:
                raise ValueError(prefix + 'END 后仍有动作，属于提前结束')
            if (attempt is not None or target is not None or evidence is None or
                    outcome != 'not_applicable' or step['left_action'] is not None or
                    step['right_action'] is not None):
                raise ValueError(prefix + 'END 只能表示最后的静止段；放回、移动或摘取必须另标动作')
        if attempt is None:
            continue
        if attempt not in attempts:
            if attempt <= maximum_attempt:
                raise ValueError(prefix + '新尝试编号必须递增')
            if phase == 'RETRY_TARGET':
                previous = attempts.get(previous_attempt)
                if previous is None or previous['state'] not in ('failure', 'unknown'):
                    raise ValueError(prefix + '重试必须对应此前失败或结果未知的尝试')
                if previous['target'] is not None and target != previous['target']:
                    raise ValueError(prefix + '重试同一番茄不能改成新目标 ID')
            attempts[attempt] = {'target': target, 'state': 'active', 'grabbed': False,
                                 'detached': False}
            maximum_attempt = attempt
        elif attempt != previous_attempt:
            raise ValueError(prefix + '不能复用已经离开的尝试编号')
        current = attempts[attempt]
        if current['target'] != target:
            raise ValueError(prefix + '同一尝试的目标 ID 必须一致；身份不确定时不要猜测')
        if current['state'] in ('failure', 'collected') and phase in TARGET_PHASES:
            raise ValueError(prefix + '失败或已完成的尝试不能继续推进；再次操作需要新尝试编号')
        if target is not None and target in collected and phase in TARGET_PHASES:
            raise ValueError(prefix + '目标已确认收集，不能作为新的在串目标重复摘取')
        if phase == 'PLACE_IN_TRAY' and outcome == 'success':
            if not current['detached']:
                raise ValueError(prefix + '成功放置之前缺少同一尝试的已确认摘离，不能将空手往返算作收集')
            if target is not None:
                collected.add(target)
            else:
                collected_unknown = True
            current['state'] = 'collected'
        elif phase == 'DETACH_TARGET' and outcome == 'success':
            if not current['grabbed']:
                raise ValueError(prefix + '成功摘离之前缺少同一尝试的已确认抓稳；手指闭合不等于抓取成功')
            current['detached'] = True
            current['state'] = 'active'
        elif phase == 'GRASP_ATTEMPT' and outcome == 'success':
            current['grabbed'] = True
            current['state'] = 'active'
        elif phase in RESULT_PHASES:
            current['state'] = outcome if outcome in ('failure', 'unknown') else 'active'
        previous_attempt = attempt
