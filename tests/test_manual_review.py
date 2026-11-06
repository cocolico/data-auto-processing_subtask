from copy import deepcopy
import json
import pytest
from convest.gui.manual_review import save, validate


def sample():
    fine = [{'start_frame': 0, 'end_frame': 10, 'phase': 'GRASP_CLUSTER'},
            {'start_frame': 10, 'end_frame': 20, 'phase': 'PRESENT_CLUSTER'},
            {'start_frame': 20, 'end_frame': 30, 'phase': 'DETACH_AND_PLACE'}]
    return {'frames': 30, 'recording_id': 'example', 'episode_index': 0, 'steps': fine,
            'manual_review': {'boundaries_linked': True}, 'granularity': {
                'fine_steps': deepcopy(fine), 'coarse_steps': [
                    {'start_frame': 0, 'end_frame': 20, 'phase': 'PICK_UP', 'fine_step_indices': [0, 1]},
                    {'start_frame': 20, 'end_frame': 30, 'phase': 'PICK_TOMATO', 'fine_step_indices': [2]}]}}


def test_repeated_save_same_directory(tmp_path):
    payload = sample()
    first = save(str(tmp_path), payload)
    payload['steps'][0]['phase'] = 'PRESENT_CLUSTER'
    payload['granularity']['fine_steps'] = deepcopy(payload['steps'])
    second = save(str(tmp_path), payload)
    assert first == second
    assert len(list(tmp_path.iterdir())) == 1
    assert json.loads(next(tmp_path.iterdir()).read_text())['steps'][0]['phase'] == 'PRESENT_CLUSTER'


@pytest.mark.parametrize('change', ['boundary', 'indices', 'missing_parent', 'duplicate_fine'])
def test_reject_inconsistent_layers(change, tmp_path):
    payload = sample()
    coarse = payload['granularity']['coarse_steps']
    if change == 'boundary':
        coarse[0]['end_frame'] = 19
    elif change == 'indices':
        coarse[0]['fine_step_indices'] = [0]
    elif change == 'missing_parent':
        coarse.pop()
    else:
        payload['granularity']['fine_steps'][0]['end_frame'] = 9
    with pytest.raises(ValueError):
        save(str(tmp_path), payload)
    assert not list(tmp_path.iterdir())


def test_existing_unrelated_file_is_preserved(tmp_path):
    target = tmp_path / 'example_episode_0_manual_annotations.json'
    target.write_text('{"original":true}')
    with pytest.raises(ValueError):
        save(str(tmp_path), sample())
    assert target.read_text() == '{"original":true}'


def test_end_is_explicitly_excluded():
    payload = sample()
    payload['steps'][-1]['phase'] = 'END'
    payload['granularity']['fine_steps'] = deepcopy(payload['steps'])
    payload['granularity']['coarse_steps'].pop()
    validate(payload)
