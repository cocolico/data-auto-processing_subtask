"""Finite directory rounds, restart compatibility and previous delivery isolation."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import pytest

from convest.annotation import directory_round as directory, review, service as annotation
from convest.capture.worker import context as capture_context
from convest.gui import config
from convest.ingest.batches import durable_json
from test_seven_stage_review import review_spec, labels
from test_gui import gui_spec
from test_capture_daily import daily_cfg
from test_capture import capture,ready
from test_ingest import service,bag


def test_all_snapshot_inputs_are_annotated_before_review_and_later_inputs_wait(review_spec):
    s,j,p=review_spec;s['annotation']['collection_mode']='directory'
    bag(s['capture'],0,2)
    scope=directory.ensure(s)
    assert len(scope['entries'])==2
    assert directory.progress(s)['pending_count']==1
    with pytest.raises(ValueError,match='尚未完成'):review.freeze(s)
    bag(s['capture'],0,3)
    with capture_context(s['capture']) as q:
        directory.apply_scope(s,s['capture']);ready(s['capture'],q)
        assert q.get(s['capture']['routes'][0]['id'],'episode2')['state']=='converted'
        assert q.get(s['capture']['routes'][0]['id'],'episode3') is None
    with annotation.database(s) as db:
        annotation.discover(s,db)
        class Fake:
            def annotate(self,job,*args):return labels(job['length']),{}
        assert annotation.process_one(s,db,Fake())
        assert not annotation.process_one(s,db,Fake())
        assert [r[0] for r in db.execute('SELECT runs FROM episodes')]==[1,1]
    assert directory.progress(s)['ready']
    review.freeze(s)
    assert directory.ensure(s)['id']==scope['id']
    assert review.cycle(s)['state']=='sealing'  # restart must not reopen the gate
    with capture_context(s['capture']) as q:review.seal_if_requested(s,q)
    assert review.cycle(s)['state']=='review'
    assert directory.progress(s)['annotated']==2


def test_input_errors_require_explicit_acknowledgement_and_keep_original(review_spec):
    s,j,p=review_spec;s['annotation']['collection_mode']='directory'
    invalid=bag(s['capture'],0,2)
    marker=invalid/'collection_state.json';v=json.loads(marker.read_text());v.update(state='recording',finalized=False);marker.write_text(json.dumps(v))
    directory.ensure(s)
    with capture_context(s['capture']) as q:
        directory.apply_scope(s,s['capture']);ready(s['capture'],q)
    report=directory.progress(s)
    assert report['ready'] and len(report['exceptions'])==1
    assert report['exceptions'][0]['episode']=='episode2'
    review.freeze(s)
    with capture_context(s['capture']) as q:review.seal_if_requested(s,q)
    review.edit(s,j,0,labels(p['length']),action='approve')
    with pytest.raises(ValueError,match='异常清单'):review.release(s)
    assert review.release(s,acknowledge_errors=True)['input_exceptions'][0]['episode']=='episode2'
    assert invalid.is_dir()


def test_historical_approved_sources_are_not_reannotated_or_added_to_new_scope(review_spec):
    s,j,p=review_spec
    with capture_context(s['capture']) as q:
        review.freeze(s);review.seal_if_requested(s,q)
    review.edit(s,j,0,labels(p['length']),action='approve');review.release(s)
    with annotation.database(s) as db:annotation.build_batches(s,db)
    s['annotation']['collection_mode']='directory';bag(s['capture'],0,2)
    scope=directory.ensure(s)
    assert [e['episode'] for e in scope['entries']]==['episode2']
    with annotation.database(s) as db:
        before=dict(db.execute('SELECT * FROM episodes WHERE id=?',(j,)).fetchone())
        assert not annotation.process_one(s,db)
        assert dict(db.execute('SELECT * FROM episodes WHERE id=?',(j,)).fetchone())==before
    assert review.cycle(s)['state']=='collecting'
    with pytest.raises(ValueError,match='不可改写'):review.edit(s,j,1,action='reopen')


def test_explicit_destination_binding_preserves_queues_and_approved_requests(tmp_path,monkeypatch,request):
    import uuid,shutil
    root_test=config.WORKSPACE/'outputs'/('binding-test-'+uuid.uuid4().hex)
    request.addfinalizer(lambda:shutil.rmtree(root_test,ignore_errors=True))
    monkeypatch.setattr(config,'ROOT',root_test)
    settings=config.defaults();t=settings['tasks'][0]
    t.update(annotation_enabled=True,auto_upload_paths=False,source=str(tmp_path/'bags'))
    (Path(t['source'])/'A').mkdir(parents=True)
    root,old=config.runtime(settings,t);durable_json(root/'runtime.json',{'state':'stopped'})
    with annotation.database(old) as db:
        db.execute("INSERT INTO episodes(id,payload,state,runs,revision) VALUES('already-reviewed','{}','approved',1,2)");db.commit()
    settings['tasks'][0]['normal_root']='/mnt/data/train_platform/datasets/new_project/normal/new_scene'
    settings['tasks'][0]['speedup_root']='/mnt/data/train_platform/datasets/new_project/speedup/new_scene'
    config.bind_training_destinations(settings,t,root)
    same,new=config.runtime(settings,t)
    assert same==root and new['annotation']['root']==old['annotation']['root']
    assert new['capture']['fingerprint']==old['capture']['fingerprint']
    assert new['training']['fingerprint']==old['training']['fingerprint']
    assert new['task']['normal_root'].endswith('new_project/normal/new_scene')
    with annotation.database(new) as db:
        assert not annotation.process_one(new,db)
        assert tuple(db.execute('SELECT state,runs,revision FROM episodes').fetchone())==('approved',1,2)
    # Any unrelated identity change must create an independent version.
    t['scene']='different-scene'
    assert config.runtime(settings,t)[0]!=root
