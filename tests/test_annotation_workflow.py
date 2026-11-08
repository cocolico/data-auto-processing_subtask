"""Grouped tomato operations, repeated fruit and review-only publication gates."""
import json

import pytest

from convest.annotation.provider import normalize, PROMPT
from convest.annotation.native import validate_segments, export_dataset


def row(i, phase, *, target=None, attempt=None, outcome='not_applicable',
        evidence=None, reason=None, condition=None):
    fruit = phase in ('APPROACH_AND_GRASP', 'DETACH_AND_PLACE')
    return {'start_sec': i, 'end_sec': i+1, 'phase': phase,
            'text': f'left robot hand: {phase.lower()}.' if fruit else f'right robot hand: {phase.lower()}.',
            'evidence_status': 'observed', 'target_id': target,
            'attempt_id': attempt, 'outcome': outcome, 'outcome_evidence': evidence,
            'left_action': 'grasp' if fruit else None,
            'right_action': 'hold truss' if fruit else None, 'decision': None,
            'evidence_note': reason, 'return_condition': condition}


def parse(rows):
    return normalize(json.dumps({'steps': rows}), len(rows)*30, 30,
                     require_structured=True, require_events=True, require_workflow=True)


def picking(i, target, attempt):
    return [row(i, 'APPROACH_AND_GRASP', target=target, attempt=attempt,
                outcome='success', evidence='The fruit is secured but still attached.'),
            row(i+1, 'DETACH_AND_PLACE', target=target, attempt=attempt,
                outcome='success', evidence='The fruit separates, then rests in the tray after release.')]


def test_seven_action_sequence_repeats_each_fruit_without_splitting_detach_place():
    rows = [row(0, 'GRASP_CLUSTER'), row(1, 'PRESENT_CLUSTER'),
            *picking(2, 'T1', 1), *picking(4, 'T2', 2),
            row(6, 'RETURN_AND_RELEASE', condition={'left_side_count': 0,
                 'evidence': 'The visible side left of the right thumb/index grasp has no attached fruit.'}),
            row(7, 'REORIENT_STEM'),
            row(8, 'END', evidence='Both hands are low and stationary to the end.')]
    result = parse(rows)
    assert len(result) == 9
    assert [r['attempt_id'] for r in result[2:6]] == [1, 1, 2, 2]
    validate_segments(result, 270)


def test_combined_pick_cannot_bypass_secure_grasp_or_switch_target():
    with pytest.raises(ValueError, match='已确认抓稳'):
        parse([picking(0, 'T1', 1)[1] | {'start_sec': 0, 'end_sec': 1}])
    rows = picking(0, 'T1', 1)
    rows[1]['target_id'] = 'T2'
    with pytest.raises(ValueError, match='同一目标'):
        parse(rows)


@pytest.mark.parametrize('condition', [
    {'left_side_count': None, 'evidence': 'The grasp region is occluded.'},
    {'left_side_count': 2, 'evidence': 'Two fruit remain on this side.'},
    {'left_side_count': 0, 'evidence': None},
])
def test_unproven_return_condition_is_retained_without_manual_gate(condition):
    result = parse([row(0, 'RETURN_AND_RELEASE', condition=condition)])
    validate_segments(result, 30)
    assert result[0]['return_condition'] == condition


def test_bool_is_not_a_tomato_count():
    with pytest.raises(ValueError, match='非负整数'):
        parse([row(0, 'RETURN_AND_RELEASE', condition={'left_side_count': False, 'evidence': 'x'})])


def test_removed_review_phase_is_rejected_as_invalid_schema():
    with pytest.raises(ValueError, match='阶段'):
        parse([row(0, 'REVIEW_REQUIRED', reason='Human intervention.')])


def test_uncertain_annotation_remains_eligible_without_manual_gate():
    r = row(0, 'GRASP_CLUSTER', reason='Contact partly occluded.')
    r['evidence_status'] = 'uncertain'
    labels = parse([r])
    validate_segments(labels, 30)
    assert labels[0]['evidence_status'] == 'uncertain'
    assert labels[0]['evidence_note'] == 'Contact partly occluded.'


def test_failed_grasp_is_reviewed_and_cannot_become_successful_pick():
    rows = picking(0, 'T1', 1)
    rows[0]['outcome'] = 'failure'
    rows[0]['outcome_evidence'] = 'Empty hand retreats while fruit stays attached.'
    result = parse(rows[:1])
    validate_segments(result, 30)
    with pytest.raises(ValueError, match='已确认抓稳'):
        parse(rows)


def test_decision_cannot_anticipate_combined_pick_result():
    from test_annotation_events import decision
    rows = picking(0, 'T1', 1)
    rows[0]['decision'] = decision('CONTINUE_PICK', 'T1', ['T1'])
    with pytest.raises(ValueError, match='此前已完成'):
        parse(rows)
    rows[0]['decision'] = None
    rows[1]['decision'] = decision('CONTINUE_PICK', 'T2', [])
    with pytest.raises(ValueError, match='决策必须'):
        parse(rows)


def test_model_workflow_cannot_silently_accept_legacy_payload():
    r = row(0, 'GRASP_CLUSTER')
    del r['return_condition'], r['evidence_note']
    with pytest.raises(ValueError, match='七动作协议'):
        parse([r])


@pytest.mark.parametrize('hand', ['Wuji', 'Sharpa'])
def test_prompt_json_example_and_robot_substitution(hand):
    text = PROMPT.format(hand=hand)
    from convest.annotation.stages import CATEGORIES
    assert hand in text and 'EXACTLY seven' in text
    assert 'NINE-SECOND' not in text and '2.8' not in text
    for phase,_,label in CATEGORIES:
        assert phase in text and label in text
    assert 'coarse/fine tracks' in text


def test_service_completes_uncertain_annotation_and_saves_both_layers(tmp_path):
    from convest.annotation import service, credentials
    root = tmp_path/'annotation'
    credential_root = tmp_path/'gui'
    credentials.save(credential_root, 'local-test-key-not-used')
    video = tmp_path/'video.mp4'; video.write_bytes(b'local fake provider fixture')
    source = tmp_path/'source'; (source/'meta').mkdir(parents=True)
    (source/'meta/info.json').write_text(json.dumps({'total_episodes': 1}))
    output = tmp_path/'capture'; (output/'.ingest').mkdir(parents=True)
    (output/'.ingest/batches.json').write_text(json.dumps({'batches': [
        {'id': 'batch', 'state': 'sealed', 'generation': str(source)}]}))
    spec = {'annotation': {'enabled': True, 'root': str(root),
                          'credential_root': str(credential_root), 'min_free_gib': 0},
            'capture': {'routes': [{'id': 'wuji-A', 'output': str(output)}]}}
    payload = {'id': 'job', 'source': str(source), 'source_ep': 0, 'video': str(video),
               'length': 30, 'fps': 30, 'recording_id': 'recording',
               'settings': {'model': 'fake', 'fps': 5, 'retries': 0}}
    r = row(0, 'GRASP_CLUSTER', reason='Contact partly occluded.')
    r['evidence_status'] = 'uncertain'
    labels = parse([r])
    class FakeProvider:
        calls = 0
        def annotate(self, *args):
            self.calls += 1
            return labels, {}
    provider = FakeProvider()
    with service.database(spec) as db:
        db.execute('INSERT INTO episodes(id,route,batch_id,source_ep,payload,state,updated_ns) '
                   'VALUES(?,?,?,?,?,?,?)', ('job','wuji-A','batch',0,json.dumps(payload),'pending',0))
        db.commit()
        assert service.process_one(spec, db, provider)
        record = db.execute('SELECT * FROM episodes WHERE id="job"').fetchone()
        assert record['state'] == 'completed' and json.loads(record['labels']) == __import__('convest.annotation.compact', fromlist=['project']).project(labels)
        assert not service.process_one(spec, db, provider)
    assert provider.calls == 1
    dual = json.loads((root/'granularity/job.json').read_text())
    assert dual['fine_steps'] == json.loads(record['labels'])
    assert dual['coarse_steps'][0]['phase'] == 'PICK_UP'
    assert not (root/'reviews').exists()
    assert not (root/'datasets').exists()
    assert video.read_bytes() == b'local fake provider fixture'
