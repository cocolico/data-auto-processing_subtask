"""Regression cases from the tomato truss review: empty grasp and early END."""
import json

import pytest

from convest.annotation.provider import normalize
from convest.annotation.native import validate_ending_motion, validate_action_motion


def decision(choice, target, picked):
    return {'choice': choice, 'target_id': target, 'picked_ids': picked,
            'remaining_ids': None, 'reachable_ids': None, 'blocked_ids': None,
            'blocked_reasons': None, 'right_grasp': None, 'reason': None,
            'intended_effect': None}


def row(start, phase, target=None, attempt=None, outcome='not_applicable',
        evidence=None, choice=None, picked=None):
    return {'start_sec': start, 'end_sec': start + 1,
            'text': f'left robot hand: {phase.lower()}.', 'evidence_status': 'observed',
            'phase': phase, 'target_id': target,
            'left_action': None if phase == 'END' else phase.lower(),
            'right_action': None,
            'decision': decision(choice, target, picked or []) if choice else None,
            'attempt_id': attempt, 'outcome': outcome, 'outcome_evidence': evidence}


def parse(rows):
    return normalize(json.dumps({'steps': rows}), len(rows) * 30, 30,
                     require_structured=True, require_events=True)


def test_failed_grasp_retries_same_tomato_without_advancing_memory():
    rows = [
        row(0, 'APPROACH_TARGET', 'T6', 6, choice='CONTINUE_PICK'),
        row(1, 'GRASP_ATTEMPT', 'T6', 6, 'failure', 'hand opens empty; T6 remains on truss'),
        row(2, 'RETRY_TARGET', 'T6', 7, choice='RETRY_SAME_TARGET'),
        row(3, 'GRASP_ATTEMPT', 'T6', 7, 'success', 'T6 moves with closed left hand'),
        row(4, 'DETACH_TARGET', 'T6', 7, 'success', 'T6 visibly separates from stem'),
        row(5, 'PLACE_IN_TRAY', 'T6', 7, 'success', 'T6 remains in left tray after release'),
        row(6, 'END', outcome='not_applicable', evidence='both arms remain still'),
    ]
    assert len(parse(rows)) == 7


def test_finger_closure_alone_cannot_confirm_detachment():
    rows = [row(0, 'APPROACH_TARGET', 'T6', 6),
            row(1, 'GRASP_ATTEMPT', 'T6', 6, 'failure', 'hand closes but leaves empty'),
            row(2, 'DETACH_TARGET', 'T6', 6, 'success', 'model claims separation')]
    with pytest.raises(ValueError, match='失败或已完成的尝试'):
        parse(rows)


def test_empty_trip_to_tray_cannot_count_as_collected():
    rows = [row(0, 'APPROACH_TARGET', 'T6', 6),
            row(1, 'GRASP_ATTEMPT', 'T6', 6, 'success', 'hand moves with fruit'),
            row(2, 'PLACE_IN_TRAY', 'T6', 6, 'success', 'model claims deposit')]
    with pytest.raises(ValueError, match='缺少同一尝试的已确认摘离'):
        parse(rows)


def test_retry_cannot_silently_switch_to_next_tomato():
    rows = [row(0, 'GRASP_ATTEMPT', 'T6', 6, 'failure', 'empty hand'),
            row(1, 'RETRY_TARGET', 'T7', 7, choice='RETRY_SAME_TARGET')]
    with pytest.raises(ValueError, match='不能改成新目标'):
        parse(rows)


def test_decision_cannot_erase_known_picked_memory():
    rows = [row(0, 'APPROACH_TARGET', 'T1', 1, choice='CONTINUE_PICK'),
            row(1, 'GRASP_ATTEMPT', 'T1', 1, 'success', 'fruit moves with hand'),
            row(2, 'DETACH_TARGET', 'T1', 1, 'success', 'fruit separates'),
            row(3, 'PLACE_IN_TRAY', 'T1', 1, 'success', 'fruit rests in tray'),
            row(4, 'APPROACH_TARGET', 'T2', 2, choice='CONTINUE_PICK')]
    rows[-1]['decision']['picked_ids'] = None
    with pytest.raises(ValueError, match='picked_ids'):
        parse(rows)


def test_end_cannot_hide_return_or_following_actions():
    early = [row(0, 'END', evidence='claimed end'), row(1, 'APPROACH_TARGET', 'T7', 7)]
    with pytest.raises(ValueError, match='END 后仍有动作'):
        parse(early)
    return_as_end = [row(0, 'END', evidence='right hand returns stem to plate')]
    return_as_end[0]['right_action'] = 'return truss to plate'
    with pytest.raises(ValueError, match='最后的静止段'):
        parse(return_as_end)


def test_model_path_requires_attempt_outcomes():
    rows = [row(0, 'APPROACH_TARGET', 'T1', 1)]
    for key in ('attempt_id', 'outcome', 'outcome_evidence'):
        del rows[0][key]
    with pytest.raises(ValueError, match='缺少尝试编号'):
        parse(rows)


def test_whole_truss_phase_does_not_claim_individual_result():
    whole = row(0, 'GRASP_CLUSTER', outcome='success', evidence='right hand lifts the truss')
    with pytest.raises(ValueError, match='只有单果抓取'):
        parse([whole])
    whole['outcome'] = 'not_applicable'
    whole['outcome_evidence'] = None
    assert parse([whole])[0]['attempt_id'] is None


def test_end_motion_check_rejects_hidden_arm_motion(tmp_path):
    pa = pytest.importorskip('pyarrow')
    pq = pytest.importorskip('pyarrow.parquet')
    root = tmp_path / 'source'
    (root / 'meta').mkdir(parents=True)
    data = root / 'data/chunk-000/episode_000000.parquet'
    data.parent.mkdir(parents=True)
    (root / 'meta/info.json').write_text(json.dumps({
        'chunks_size': 1000,
        'data_path': 'data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet',
        'features': {'observation.state': {
            'names': ['left_fr3_joint1.position', 'left_fr3_joint1.velocity']}}}))
    pq.write_table(pa.table({'observation.state': pa.array(
        [[0.0, 0.0], [0.05, 0.0], [0.7, 0.0], [0.8, 0.0]],
        type=pa.list_(pa.float32(), 2))}), data)
    with pytest.raises(ValueError, match='仍有明显关节运动'):
        validate_ending_motion(root, 0, [{'phase': 'END', 'start_frame': 0, 'end_frame': 4}])
    validate_ending_motion(root, 0, [{'phase': 'END', 'start_frame': 2, 'end_frame': 4}])


def test_action_motion_rejects_unsupported_place_and_unlabeled_left_arm(tmp_path):
    pa = pytest.importorskip('pyarrow')
    pq = pytest.importorskip('pyarrow.parquet')
    root = tmp_path / 'source'
    (root / 'meta').mkdir(parents=True)
    data = root / 'data/chunk-000/episode_000000.parquet'
    data.parent.mkdir(parents=True)
    (root / 'meta/info.json').write_text(json.dumps({
        'chunks_size': 1000,
        'data_path': 'data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet',
        'features': {'observation.state': {'names': ['left_fr3_joint1.position']}}}))
    pq.write_table(pa.table({'observation.state': pa.array(
        [[0.0], [0.1], [0.2], [0.8], [1.3]], type=pa.list_(pa.float32(), 1))}), data)
    with pytest.raises(ValueError, match='声称送盘成功'):
        validate_action_motion(root, 0, [{'phase': 'PLACE_IN_TRAY', 'outcome': 'success',
                                          'start_frame': 0, 'end_frame': 3}])
    with pytest.raises(ValueError, match='未标左手动作'):
        validate_action_motion(root, 0, [{'phase': 'RETURN_CLUSTER', 'left_action': None,
                                          'start_frame': 2, 'end_frame': 5}])
    validate_action_motion(root, 0, [{'phase': 'PLACE_IN_TRAY', 'outcome': 'success',
                                      'start_frame': 2, 'end_frame': 5}])
