"""Annotation-aware sampling: short/repeated segments, endpoints and persistence."""
from copy import deepcopy
import json

import numpy as np
import pytest

from convest.annotation.granularity import derive, FILENAME
from convest.training import annotations
from convest.training.adatempo.sampling import nearest_frames


def record(bounds, phases=None):
    phases = phases or ['GRASP_CLUSTER']*(len(bounds)-1)
    fine = [{'start_frame': a, 'end_frame': b, 'phase': p, 'text': p,
             'target_id': None, 'decision': None, 'return_condition': None}
            for a,b,p in zip(bounds, bounds[1:], phases)]
    return {'episode_index': 0, 'source_episode_index': 3, 'source_recording_id': 'r3',
            **derive(fine, 30, bounds[-1])}


def test_skipped_segments_keep_identity_without_changing_sampling():
    source = record([0, 4, 5, 6, 14, 20])
    obs = np.array([0., 3., 6., 9., 12., 15., 18.])
    before = obs.copy()
    out = annotations.remap(source, obs)
    audit = annotations.alignment(source, obs, 'hash')
    assert np.array_equal(obs, before)
    assert audit['fine_step_source_indices'] == [0, 3, 4]
    assert audit['skipped_fine_step_source_indices'] == [1, 2]
    assert [(s['start_frame'], s['end_frame']) for s in out['fine_steps']] == [(0, 2), (2, 5), (5, 7)]
    assert len(out['coarse_steps']) == 1


def test_final_action_only_segment_is_recorded_as_skipped():
    source = record([0, 9, 10])
    obs = np.arange(9.)
    assert len(annotations.remap(source, obs)['fine_steps']) == 1
    assert annotations.alignment(source, obs, 'hash')['skipped_fine_step_source_indices'] == [1]


@pytest.mark.parametrize('cmax', [1., 1.1, 1.5, 2., 3.])
def test_fractional_sampling_maps_every_output_frame_without_mutation(cmax):
    rng = np.random.default_rng(12)
    for _ in range(30):
        n = int(rng.integers(20, 300))
        bounds = [0, *sorted(rng.choice(np.arange(1,n-1), size=8, replace=False).tolist()), n]
        source = record(bounds)
        virtual = [0.]
        while virtual[-1] < n-1:
            virtual.append(min(n-1, virtual[-1]+rng.uniform(1,cmax)))
        obs = np.asarray(virtual[:-1])
        before = obs.copy()
        out = annotations.remap(source, obs)
        audit = annotations.alignment(source, obs, 'hash')
        assert np.array_equal(obs, before)
        assert out['length'] == len(obs)
        assert out['fine_steps'][0]['start_frame'] == 0
        assert out['fine_steps'][-1]['end_frame'] == len(obs)
        for step, source_id in zip(out['fine_steps'], audit['fine_step_source_indices']):
            indices = nearest_frames(obs)[step['start_frame']:step['end_frame']]
            original = source['fine_steps'][source_id]
            assert np.all(indices >= original['start_frame'])
            assert np.all(indices < original['end_frame'])


def test_dataset_validation_checks_missing_sidecar_and_incorrect_coarse_labels(tmp_path):
    from test_annotation_granularity import source_dataset, labels
    from convest.annotation import native
    source = source_dataset(tmp_path/'raw')
    target = tmp_path/'full'
    native.export_dataset(source, target, {1: labels()})
    annotations.validate_dataset(target)
    copy = tmp_path/'normal'
    import shutil
    shutil.copytree(target, copy)
    assert annotations.validate_pair(target, copy, speedup=False)['status'] == 'passed'
    rows = annotations.load(copy)
    rows[0]['coarse_steps'][0]['end_frame'] += 1
    annotations.save(copy, rows)
    with pytest.raises(ValueError, match='Coarse/fine'):
        annotations.validate_dataset(copy)
    (copy/'meta'/FILENAME).unlink()
    with pytest.raises(ValueError, match='missing'):
        annotations.validate_pair(target, copy, speedup=False)
