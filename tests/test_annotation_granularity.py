"""Local export/preview contracts: both levels, immutable sensor rows, no API."""
from copy import deepcopy
import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from convest.annotation import native, preview
from convest.annotation.compact import project
from convest.annotation.granularity import derive, legacy_v2_records, FILENAME
from convest.annotation.provider import normalize
from convest.annotation.prompts import TOMATO_WORKFLOW_PROMPT


def labels():
    phases = ['GRASP_CLUSTER', 'PRESENT_CLUSTER', 'APPROACH_AND_GRASP',
              'DETACH_AND_PLACE', 'APPROACH_AND_GRASP', 'DETACH_AND_PLACE',
              'RETURN_AND_RELEASE', 'REORIENT_STEM', 'GRASP_CLUSTER', 'END']
    return [{'phase': phase, 'text': phase, 'start_frame': i * 3,
             'end_frame': (i + 1) * 3, 'evidence_status': 'observed'}
            for i, phase in enumerate(phases)]


def test_coarse_mapping_preserves_repeated_picks_and_excludes_stationary_end():
    fine = labels()
    fine[7].update(evidence_status='uncertain', evidence_note='Rule-assigned boundary')
    before = deepcopy(fine)
    result = derive(fine, 30, 30)
    assert fine == before and result['fine_steps'] == project(before)
    assert [s['phase'] for s in result['coarse_steps']] == [
        'PICK_UP', 'PICK_TOMATO', 'PUT_BACK', 'ROTATE_STEM', 'PICK_UP']
    assert result['coarse_steps'][1]['fine_step_indices'] == [2, 3, 4, 5]
    assert result['coarse_steps'][1]['start_frame'] == 6
    assert result['coarse_steps'][1]['end_frame'] == 18
    assert result['excluded_intervals'] == [{'fine_step_index': 9}]
    assert 'coarse_full_coverage' not in result
    assert sum(s['end_frame'] - s['start_frame'] for s in result['coarse_steps']) == 27
    result['fine_steps'][0]['text'] = 'changed'
    assert fine == before


@pytest.mark.parametrize('change', ['gap', 'overlap', 'tail', 'fps'])
def test_invalid_timelines_are_not_silently_coarsened(change):
    fine = labels()
    if change == 'gap': fine[1]['start_frame'] += 1
    if change == 'overlap': fine[1]['start_frame'] -= 1
    if change == 'tail': fine.pop()
    with pytest.raises(ValueError):
        derive(fine, 0 if change == 'fps' else 30, 30)


def test_unknown_legacy_labels_are_explicitly_unmapped():
    fine = labels()
    fine[1]['phase'] = 'OLD_PHASE'
    result = derive(fine, 30, 30)
    assert result['excluded_intervals'][0] == {'fine_step_index': 1}
    assert result['fine_steps'][1] == project(fine)[1]


def source_dataset(root):
    (root/'meta').mkdir(parents=True)
    (root/'data').mkdir()
    (root/'videos').mkdir()
    features = {k: {'dtype': 'int64', 'shape': [1], 'names': None}
                for k in ('frame_index', 'episode_index', 'index', 'task_index', 'source_timestamp_ns')}
    features['timestamp'] = {'dtype': 'float32', 'shape': [1], 'names': None}
    features['observation.images.cam0'] = {'dtype': 'video', 'shape': [8, 8, 3], 'names': None}
    info = {'codebase_version': 'v2.1', 'chunks_size': 1000, 'fps': 30,
            'total_episodes': 2, 'total_frames': 60, 'total_tasks': 1,
            'total_videos': 2, 'total_chunks': 1, 'splits': {'train': '0:2'},
            'data_path': 'data/episode_{episode_index:06d}.parquet',
            'video_path': 'videos/episode_{episode_index:06d}.mp4', 'features': features}
    (root/'meta/info.json').write_text(json.dumps(info))
    episodes, stats = [], []
    for ep in range(2):
        table = pa.table({'frame_index': list(range(30)), 'episode_index': [ep]*30,
                          'index': list(range(ep*30, (ep+1)*30)), 'task_index': [0]*30,
                          'source_timestamp_ns': list(range(1000000000, 1000000030)),
                          'timestamp': pa.array([i/30 for i in range(30)], type=pa.float32())})
        pq.write_table(table, root/f'data/episode_{ep:06d}.parquet')
        # Native export only copies/checks video bytes; no fake decoding claims.
        (root/f'videos/episode_{ep:06d}.mp4').write_bytes(b'opaque local fixture')
        episodes.append({'episode_index': ep, 'length': 30, 'tasks': ['original'],
                         'source_recording_id': f'recording-{ep}'})
        stats.append({'episode_index': ep, 'stats': {k: native.task_stats(table[k].to_pylist())
                                                   for k in table.column_names}})
    native.jsonl(root/'meta/episodes.jsonl', episodes)
    native.jsonl(root/'meta/episodes_stats.jsonl', stats)
    native.jsonl(root/'meta/tasks.jsonl', [{'task_index': 0, 'task': 'original'}])
    return native.inspect(root)


def test_native_export_contains_validated_dual_labels_and_source_mapping(tmp_path):
    source = source_dataset(tmp_path/'source')
    before = {p: native.sha256(p) for p in source['root'].rglob('*') if p.is_file()}
    output = tmp_path/'delivered'
    fine = labels()
    fine[0].update(evidence_status='uncertain', evidence_note='Partly occluded')
    native.export_dataset(source, output, {1: fine})
    report = native.validate_output(source, output, {1: fine})
    assert report['status'] == 'passed' and f'meta/{FILENAME}' in report['files']
    assert not (output/'meta/annotation_events.jsonl').exists()
    records = native.rows(output/'meta'/FILENAME)
    assert len(records) == 1
    record = records[0]
    assert (record['episode_index'], record['source_episode_index'], record['source_recording_id']) == (0, 1, 'recording-1')
    assert record['fine_steps'] == project(fine)
    table = pq.read_table(output/'data/episode_000000.parquet')
    vocabulary = {r['task_index']: r['task'] for r in native.rows(output/'meta/tasks.jsonl')}
    assert [vocabulary[t] for t in table['task_index'].to_pylist()] == [s['text'] for s in fine for _ in range(3)]
    assert before == {p: native.sha256(p) for p in before}
    legacy = legacy_v2_records([source['episodes'][1]], {1: fine}, 30)
    native.jsonl(output/'meta'/FILENAME, legacy)
    assert native.validate_output(source, output, {1: fine})['status'] == 'passed'
    record['coarse_steps'][0]['end_frame'] += 1
    native.jsonl(output/'meta'/FILENAME, records)
    with pytest.raises(ValueError, match='Dual-granularity'):
        native.validate_output(source, output, {1: fine})
    (output/'meta'/FILENAME).unlink()
    with pytest.raises(ValueError, match='Dual-granularity'):
        native.validate_output(source, output, {1: fine})


def test_formal_preview_exposes_both_layers(tmp_path, monkeypatch):
    source = source_dataset(tmp_path/'source')
    payload = {'source': str(source['root']), 'source_ep': 1, 'recording_id': 'recording-1',
               'fps': 30, 'length': 30, 'settings': {'model': 'fake'}}
    monkeypatch.setattr(preview, 'job', lambda *_: (payload, labels()))
    result = preview.describe({}, 'job')
    assert result['steps'] == result['granularity']['fine_steps']
    assert len(result['granularity']['coarse_steps']) == 5


def test_old_evidence_field_is_readable_but_not_requested_in_new_prompt():
    from test_annotation_workflow import row
    old = row(0, 'GRASP_CLUSTER')
    old['review_reason'] = old.pop('evidence_note')
    result = normalize(json.dumps({'steps': [old]}), 30, 30, require_workflow=True)
    assert 'evidence_note' in result[0] and 'review_reason' not in result[0]
    assert 'review_reason' not in TOMATO_WORKFLOW_PROMPT
    assert 'human-review' not in TOMATO_WORKFLOW_PROMPT


def test_compact_prompt_normalizes_and_derives_exact_requested_keys():
    text = TOMATO_WORKFLOW_PROMPT.format(hand='Wuji')
    raw = text.split('Example shape (not a video observation):\n')[1].strip()
    fine = normalize(raw, 30, 30, require_workflow=True)
    assert set(fine[0]) == {'start_frame', 'end_frame', 'phase', 'text', 'target_id', 'decision', 'return_condition'}
    result = derive(fine, 30, 30)
    assert set(result['coarse_steps'][0]) == {'phase', 'text', 'start_frame', 'end_frame', 'fine_step_indices'}
    assert set(result['categories'][0]) == {'phase', 'text'}
    assert result['schema_version'] == 'tomato-dual-granularity-v3'
    assert 'text_zh' not in json.dumps(result)
    assert 'coarse_full_coverage' not in result
    for removed in ['evidence_status', 'attempt_id', 'left_action', 'right_action', 'outcome', 'outcome_evidence', 'evidence_note', 'picked_ids', 'remaining_ids', 'reachable_ids', 'blocked_ids', 'blocked_reasons', 'right_grasp', 'intended_effect']:
        assert f'"{removed}"' not in raw
        assert removed not in json.dumps(result)


def test_compact_nested_fields_and_roundtrip():
    raw = {'steps': [
        {'start_sec': 0, 'end_sec': 1, 'phase': 'APPROACH_AND_GRASP', 'text': 'Grasp one tomato.',
         'target_id': 'T1', 'decision': {'choice': 'CONTINUE_PICK', 'target_id': 'T1'}, 'return_condition': None},
        {'start_sec': 1, 'end_sec': 2, 'phase': 'RETURN_AND_RELEASE', 'text': 'Return the truss.',
         'target_id': None, 'decision': None, 'return_condition': {'left_side_count': None}}]}
    fine = normalize(json.dumps(raw), 60, 30, require_workflow=True)
    assert fine[0]['decision'] == raw['steps'][0]['decision']
    assert fine[1]['return_condition'] == {'left_side_count': None}
    assert project(fine) == fine
    raw['steps'][1]['return_condition']['left_side_count'] = True
    with pytest.raises(ValueError, match='left_side_count'):
        normalize(json.dumps(raw), 60, 30, require_workflow=True)
