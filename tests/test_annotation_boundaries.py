"""Completion boundaries must survive normalization, including repeated labels."""
import json

import pytest

from convest.annotation.provider import normalize
from convest.annotation.native import annotation_events


def step(start, end, text):
    return {'start_sec': start, 'end_sec': end, 'text': text,
            'evidence_status': 'observed'}


def test_repeated_identical_operations_keep_their_boundary():
    text = 'left robot hand: align with tomato; right robot hand: hold truss.'
    labels = normalize(json.dumps({'steps': [step(0, 1.2, text), step(1.2, 2, text)]}), 60, 30)
    assert [(s['start_frame'], s['end_frame']) for s in labels] == [(0, 36), (36, 60)]
    assert labels[0]['text'] == labels[1]['text']


def test_supporting_hand_does_not_merge_active_hand_phases_or_cycles():
    actions = ['grasp', 'detach', 'transport', 'place', 'release'] * 2
    rows = [step(i, i + 1, f'left robot hand: {action} tomato; right robot hand: hold truss.')
            for i, action in enumerate(actions)]
    labels = normalize(json.dumps({'steps': rows}), 300, 30)
    assert len(labels) == 10
    assert [(s['start_frame'], s['end_frame']) for s in labels] == [
        (i * 30, (i + 1) * 30) for i in range(10)]


@pytest.mark.parametrize('second_start', [0.8, 1.2])
def test_detailed_segments_still_reject_overlap_or_gap(second_start):
    rows = [step(0, 1, 'left robot hand: grasp tomato.'),
            step(second_start, 2, 'left robot hand: detach tomato.')]
    with pytest.raises(ValueError, match='cover every frame'):
        normalize(json.dumps({'steps': rows}), 60, 30)


def test_structured_regrasp_decision_survives_normalization():
    decision = {'choice': 'REGRASP', 'target_id': None,
                'picked_ids': ['T1'], 'remaining_ids': ['T2', 'T3'],
                'reachable_ids': [], 'blocked_ids': ['T2', 'T3'],
                'blocked_reasons': {'T2': 'front tomato', 'T3': 'right gripper'},
                'right_grasp': 'stem side A', 'reason': 'left approach blocked',
                'intended_effect': 'expose the remaining tomatoes'}
    row = {**step(0, 2, 'left robot hand: assess remaining tomatoes; right robot hand: hold truss.'),
           'phase': 'ASSESS_REMAINING', 'target_id': None,
           'left_action': 'assess remaining tomatoes', 'right_action': 'hold truss',
           'decision': decision}
    result = normalize(json.dumps({'steps': [row]}), 60, 30, require_structured=True)
    assert result[0]['decision'] == decision
    assert result[0]['phase'] == 'ASSESS_REMAINING'


def test_structured_memory_rejects_picked_remaining_overlap():
    decision = {'choice': 'CONTINUE_PICK', 'target_id': 'T1',
                'picked_ids': ['T1'], 'remaining_ids': ['T1'],
                'reachable_ids': ['T1'], 'blocked_ids': [],
                'blocked_reasons': {}, 'right_grasp': None, 'reason': None,
                'intended_effect': None}
    row = {**step(0, 1, 'left robot hand: select tomato.'),
           'phase': 'SELECT_TARGET', 'target_id': 'T1',
           'left_action': 'select tomato', 'right_action': None, 'decision': decision}
    with pytest.raises(ValueError, match='不能重叠'):
        normalize(json.dumps({'steps': [row]}), 30, 30, require_structured=True)


def test_model_path_requires_structured_labels():
    with pytest.raises(ValueError, match='缺少番茄采摘阶段'):
        normalize(json.dumps({'steps': [step(0, 1, 'right robot hand: grasp truss.')]}),
                  30, 30, require_structured=True)


def test_placement_cannot_carry_a_decision_about_the_next_pick():
    decision = {'choice': 'CONTINUE_PICK', 'target_id': 'T2',
                'picked_ids': ['T1'], 'remaining_ids': ['T2'],
                'reachable_ids': None, 'blocked_ids': None,
                'blocked_reasons': None, 'right_grasp': None, 'reason': None,
                'intended_effect': None}
    row = {**step(0, 1, 'left robot hand: place T1 in tray.'),
           'phase': 'PLACE_IN_TRAY', 'target_id': 'T1',
           'left_action': 'place T1', 'right_action': 'hold truss', 'decision': decision}
    with pytest.raises(ValueError, match='阶段的起点'):
        normalize(json.dumps({'steps': [row]}), 30, 30, require_structured=True)


def test_event_sidecar_uses_delivered_episode_indices_without_losing_source_ids():
    selected = [{'episode_index': 2, 'source_recording_id': 'recording-a'},
                {'episode_index': 7, 'source_recording_id': 'recording-b'}]
    labels = {2: [{'phase': 'GRASP_CLUSTER', 'start_frame': 0, 'end_frame': 10}],
              7: [{'phase': 'REGRASP_AND_REORIENT', 'start_frame': 0, 'end_frame': 12}]}
    events = annotation_events(selected, labels)
    assert [(e['episode_index'], e['source_episode_index'], e['source_recording_id'])
            for e in events] == [(0, 2, 'recording-a'), (1, 7, 'recording-b')]
    assert events[1]['steps'] == labels[7]
