"""Source labels are projections; never rename data or change approval identity."""
import json
import sqlite3
from pathlib import Path
from convest.annotation import origins,service as annotation,preview
from test_seven_stage_review import review_spec
from test_gui import gui_spec
from test_capture_daily import daily_cfg
from test_capture import capture
from test_ingest import service


def test_source_names_resolve_uuid_and_group_without_using_batch_indices(tmp_path):
    state=tmp_path/'state';state.mkdir()
    with sqlite3.connect(state/'queue.sqlite3') as db:
        db.execute('CREATE TABLE recordings(route,episode,payload)')
        for route,name,identity in [('wuji-A','episode38','uuid-new'),('wuji-A','episode28','uuid-old'),('wuji-B','episode38','uuid-other')]:
            db.execute('INSERT INTO recordings VALUES(?,?,?)',(route,name,json.dumps({'recording_id':identity})))
    names=origins.recording_names({'capture':{'state_root':str(state)}})
    assert origins.describe('wuji-A','uuid-new',names)['source_episode_number']==38
    assert origins.describe('wuji-A','uuid-old',names)['source_label']=='wuji-A / episode28'
    assert origins.describe('wuji-B','uuid-other',names)['source_label']=='wuji-B / episode38'
    assert origins.describe('wuji-A','unmapped',names)['source_episode_number'] is None
    assert origins.describe('wuji-A','/bags/A/episode200',{})['source_episode']=='episode200'


def test_snapshot_and_preview_show_original_name_without_mutating_approved_payload(review_spec):
    spec,identity,payload=review_spec
    with annotation.database(spec) as db:before=dict(db.execute('SELECT * FROM episodes WHERE id=?',(identity,)).fetchone())
    row=annotation.snapshot(spec)['episodes'][0]
    assert row['source_episode']=='episode1' and row['source_episode_number']==1
    result=preview.describe(spec,identity)
    assert result['source_label'].endswith('/ episode1')
    assert result['episode_index']==0  # internal file lookup remains unchanged
    assert result['review_storage']['revisions'].endswith('/reviews/'+identity)
    with annotation.database(spec) as db:assert before==dict(db.execute('SELECT * FROM episodes WHERE id=?',(identity,)).fetchone())
