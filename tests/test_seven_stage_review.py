"""Production seven-stage review: actual conversion, gating, exports and previews."""
from copy import deepcopy
import json
from pathlib import Path
import numpy as np
import pyarrow.parquet as pq
import pytest

from convest.annotation import stages, review, service as annotation, credentials, native, preview
from convest.annotation.provider import ArkProvider
from convest.capture.worker import context as capture_context
from convest.gui import worker as gui_worker
from convest.training import annotations, worker as training
from convest.training.normal import export as normal_export
from convest.training.adatempo.lerobot_main import process as speedup_export
from test_gui import gui_spec
from test_capture_daily import daily_cfg
from test_capture import capture, ready
from test_ingest import service, bag
from test_annotation_granularity import source_dataset


def labels(n):
    return [{'stage_index':i,'phase':phase,'text':text,'start_frame':i*n//7,'end_frame':(i+1)*n//7}
            for i,(phase,_,text) in enumerate(stages.CATEGORIES)]


def test_prompt_and_response_are_exactly_scheme_b():
    from convest.annotation.prompts import TOMATO_WORKFLOW_PROMPT
    prompt=TOMATO_WORKFLOW_PROMPT.format(hand='Wuji')
    assert 'Wuji' in prompt and 'EXACTLY seven' in prompt
    assert 'NINE' not in prompt and 'END or idle' in prompt
    rows=[{k:v for k,v in s.items() if k not in ('start_frame','end_frame')} |
          {'start_sec':s['start_frame']/30,'end_sec':s['end_frame']/30} for s in labels(210)]
    assert ArkProvider.normalize_production(json.dumps({'steps':rows}),{'length':210,'fps':30})==labels(210)
    with pytest.raises(ValueError):ArkProvider.normalize_production('{"steps":[]}',{'length':210,'fps':30})
    rows[4]['phase']='FIRST_LIFT'
    with pytest.raises(ValueError):ArkProvider.normalize_production(json.dumps({'steps':rows}),{'length':210,'fps':30})


@pytest.mark.parametrize('corruption',['missing','order','gap','overlap','zero','text','index','bool'])
def test_stage_contract_rejects_invalid_timelines(corruption):
    s=labels(70)
    if corruption=='missing':s.pop()
    elif corruption=='order':s[1],s[2]=s[2],s[1]
    elif corruption=='gap':s[1]['start_frame']+=1
    elif corruption=='overlap':s[1]['start_frame']-=1
    elif corruption=='zero':s[1]['end_frame']=s[1]['start_frame']
    elif corruption=='text':s[1]['text']='made-up label'
    elif corruption=='index':s[1]['stage_index']=5
    else:s[1]['start_frame']=True
    with pytest.raises(ValueError):stages.validate(s,70)


def test_export_keeps_sensor_bytes_and_distinguishes_repeated_text(tmp_path):
    source=source_dataset(tmp_path/'source');output=tmp_path/'annotated';s=labels(30)
    native.export_dataset(source,output,{1:s})
    assert native.validate_output(source,output,{1:s})['status']=='passed'
    table=pq.read_table(output/'data/episode_000000.parquet')
    assert set(table['stage_index'].to_pylist())==set(range(7))
    task_ids=table['task_index'].to_pylist()
    assert task_ids[s[0]['start_frame']]==task_ids[s[4]['start_frame']]
    assert task_ids[s[1]['start_frame']]==task_ids[s[5]['start_frame']]
    assert len(native.rows(output/'meta/tasks.jsonl'))==5
    assert (output/'meta'/stages.FILENAME).is_file()
    assert not (output/'meta/annotation_granularity.jsonl').exists()
    assert annotations.validate_dataset(output)[0]['steps']==s
    changed=table.set_column(table.schema.get_field_index('stage_index'),'stage_index',table['task_index'])
    pq.write_table(changed,output/'data/episode_000000.parquet')
    with pytest.raises(ValueError,match='stage_index'):annotations.validate_dataset(output)


def test_speedup_keeps_one_frame_final_stage_and_every_stage():
    n=30;s=labels(n);s[-2]['end_frame']=n-1;s[-1]['start_frame']=n-1
    record={'episode_index':0,'source_episode_index':0,'source_recording_id':'r',**stages.record(s,30,n)}
    obs=annotations.protect_stages(record,np.arange(0,n-1,3.),n)
    mapped=annotations.remap(record,obs)
    assert len(mapped['steps'])==7
    assert mapped['steps'][-1]['end_frame']-mapped['steps'][-1]['start_frame']==1
    assert np.all(np.diff(obs)>0) and obs[-1]<n-1
    assert annotations.alignment(record,obs,'hash')['skipped_stage_source_indices']==[]


@pytest.fixture
def review_spec(gui_spec,tmp_path):
    s=gui_spec;s['capture']['routes']=s['capture']['routes'][:1]
    s['capture']['delete_raw_after_upload']=False
    s['capture']['cleanup_intermediates']=False
    s['annotation']={'enabled':True,'review_required':True,'root':str(tmp_path/'annotation'),
                     'credential_root':str(tmp_path/'gui'),'include_existing':True,'model':'fake','fps':5,'retries':0,'min_free_gib':0}
    credentials.save(s['annotation']['credential_root'],'offline-fixture')
    s['training']['annotation']={'spec':s['annotation']}
    bag(s['capture'],0,1)
    with capture_context(s['capture']) as q:ready(s['capture'],q)
    with annotation.database(s) as db:
        annotation.discover(s,db)
        row=db.execute('SELECT * FROM episodes').fetchone();p=json.loads(row['payload'])
        class Fake:
            def annotate(self,*args):return labels(p['length']),{}
        assert annotation.process_one(s,db,Fake())
        assert db.execute('SELECT state FROM episodes').fetchone()[0]=='needs_review'
    return s,row['id'],p


def test_review_save_is_not_approval_and_source_changes_cannot_pass(review_spec):
    s,j,p=review_spec;steps=labels(p['length'])
    with capture_context(s['capture']) as q:
        assert not gui_worker.capture_upload(s,q)
        review.freeze(s);review.seal_if_requested(s,q)
    with pytest.raises(ValueError,match='逐条审核'):review.release(s)
    result=review.edit(s,j,0,steps)
    assert result=={'revision':1,'state':'reviewing'}
    with pytest.raises(ValueError,match='逐条审核'):review.release(s)
    with pytest.raises(ValueError,match='版本'):review.edit(s,j,0,steps,action='approve')
    video=Path(p['video']);video.chmod(0o644)
    with video.open('ab') as f:f.write(b'changed')
    with pytest.raises(ValueError,match='视频发生变化'):review.edit(s,j,1,steps,action='approve')


def test_full_review_handoff_normal_speedup_and_task_scoped_previews(review_spec):
    s,j,p=review_spec;steps=labels(p['length'])
    with capture_context(s['capture']) as q:
        review.freeze(s);review.seal_if_requested(s,q)
        assert review.cycle(s)['state']=='review'
        assert not gui_worker.capture_upload(s,q)
    draft=preview.describe(s,j)
    assert not draft['readonly'] and draft['single_track'] and draft['steps']==steps
    # Edit an actual shared boundary; the downstream dataset must use this revision.
    steps[0]['end_frame']+=1;steps[1]['start_frame']+=1
    review.edit(s,j,0,steps)
    assert preview.describe(s,j)['steps']==steps
    review.edit(s,j,1,steps,action='approve')
    with pytest.raises(ValueError,match='不可改写'):review.edit(s,j,2,steps)
    with annotation.database(s) as db:
        annotation.build_batches(s,db)
        assert db.execute('SELECT count(*) FROM batches').fetchone()[0]==0
    review.release(s)
    with annotation.database(s) as db:
        annotation.build_batches(s,db)
        b=db.execute('SELECT * FROM batches').fetchone()
        assert b['state']=='completed',b['error']
    assert preview.describe(s,j,'reviewed')['steps']==steps
    with training.context(s['training']) as db:
        training.discover(s['training'],db)
        assert training.process_once(s['training'],db)
        row=db.execute('SELECT * FROM tasks').fetchone()
        assert row['state']=='local_ready',row['error']
        pair=Path(json.loads(row['payload'])['source'])
        assert annotations.validate_pair(b['output'],pair/'normal',speedup=False)['status']=='passed'
        assert annotations.validate_pair(pair/'normal',pair/'speedup',speedup=True)['status']=='passed'
        for variant in ('normal','speedup'):
            result=preview.describe(s,j,variant)
            assert result['readonly'] and len(result['steps'])==7
            for camera in ('cam0','cam1','cam2'):
                assert preview.video(s,j,'observation.images.'+camera,variant).is_file()
        assert preview.describe(s,j,'speedup')['source_frame_index']
        with pytest.raises(ValueError,match='等待'):review.next_cycle(s)
        assert gui_worker.training_upload(s,db)
    with capture_context(s['capture']) as q:
        while gui_worker.capture_upload(s,q):pass
    assert review.next_cycle(s)['state']=='collecting'


def test_bad_model_result_stays_available_for_manual_review(review_spec):
    s,j,p=review_spec
    with annotation.database(s) as db:
        annotation.update(db,'episodes',j,state='pending',labels=None,next_ns=0)
        class Bad:
            def annotate(self,*a):raise ValueError('missing seven stages')
        assert annotation.process_one(s,db,Bad())
        row=db.execute('SELECT * FROM episodes').fetchone()
        assert row['state']=='needs_review' and row['labels'] is None
    assert preview.describe(s,j)['steps']==[]
    with pytest.raises(ValueError,match='不合格原因'):review.edit(s,j,0,action='reject')
    assert review.edit(s,j,0,action='reject',reason='视频缺少第二次拿起')['state']=='rejected'


def test_all_rejected_batch_never_creates_empty_training_dataset(review_spec):
    s,j,p=review_spec
    with capture_context(s['capture']) as q:
        review.freeze(s);review.seal_if_requested(s,q)
    review.edit(s,j,0,action='reject',reason='没有第二次采摘')
    review.release(s)
    with annotation.database(s) as db:
        annotation.build_batches(s,db)
        row=db.execute('SELECT * FROM batches').fetchone()
        assert row['state']=='empty' and row['output'] is None
    with training.context(s['training']) as db:
        training.discover(s['training'],db)
        assert not training.process_once(s['training'],db)
        assert db.execute('SELECT count(*) FROM tasks').fetchone()[0]==0


def test_review_http_checks_token_and_revision_and_serves_actual_variant(review_spec):
    import secrets,threading,urllib.request,urllib.error
    from http.server import ThreadingHTTPServer
    from convest.gui.server import Handler
    s,j,p=review_spec
    class Manager:
        def annotation_spec(self,tid):
            if tid!='test-task':raise ValueError('任务不存在')
            return s
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    server.manager=Manager();server.token=secrets.token_urlsafe(32)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    def post(body,token=None):
        req=urllib.request.Request(base+'/api/annotation/review',data=json.dumps(body).encode(),headers={'Content-Type':'application/json','X-Workflow-Token':token or ''})
        with urllib.request.urlopen(req) as r:return json.load(r)
    body={'task':'test-task','job':j,'revision':0,'steps':labels(p['length']),'action':'save'}
    try:
        with pytest.raises(urllib.error.HTTPError) as e:post(body)
        assert e.value.code==403
        assert post(body,server.token)['state']=='reviewing'
        with pytest.raises(urllib.error.HTTPError) as e:post(body,server.token)
        assert e.value.code==400
        from urllib.parse import urlencode
        params=urlencode({'task':'test-task','job':j,'variant':'draft'})
        with urllib.request.urlopen(base+'/api/annotation/preview?'+params) as r:v=json.load(r)
        assert v['revision']==1 and v['steps']==labels(p['length'])
        with pytest.raises(urllib.error.HTTPError):urllib.request.urlopen(base+'/api/annotation/video?'+params+'&camera=observation.images.cam0')
        req=urllib.request.Request(base+'/api/annotation/video?'+params+'&camera=observation.images.cam0&token='+server.token,headers={'Range':'bytes=0-31'})
        with urllib.request.urlopen(req) as r:assert r.status==206 and len(r.read())==32
    finally:server.shutdown();server.server_close();thread.join()


def test_approval_can_only_be_withdrawn_before_release(review_spec):
    s,j,p=review_spec;steps=labels(p['length'])
    with capture_context(s['capture']) as q:
        review.freeze(s);review.seal_if_requested(s,q)
    review.edit(s,j,0,steps,action='approve')
    assert preview.describe(s,j)['can_reopen']
    assert review.edit(s,j,1,action='reopen')=={'revision':2,'state':'reviewing'}
    with pytest.raises(ValueError,match='逐条审核'):review.release(s)
    review.edit(s,j,2,steps,action='approve');review.release(s)
    with pytest.raises(ValueError):review.edit(s,j,3,action='reopen')
    assert not preview.describe(s,j)['can_reopen']


def test_missing_source_can_be_rejected_but_never_approved(review_spec):
    s,j,p=review_spec
    missing=Path(p['source'])/native.path_for(native.read_json(Path(p['source'])/'meta/info.json'),p['source_ep'],video_key='observation.images.cam2')
    missing.unlink()
    with pytest.raises(OSError):review.edit(s,j,0,labels(p['length']),action='approve')
    assert review.edit(s,j,0,action='reject',reason='源视频损坏，人工排除')['state']=='rejected'


def test_next_review_cycle_does_not_revalidate_released_historical_sources(review_spec):
    s,j,p=review_spec;steps=labels(p['length'])
    with capture_context(s['capture']) as q:
        review.freeze(s);review.seal_if_requested(s,q)
    review.edit(s,j,0,steps,action='approve');review.release(s)
    # Simulate a new collecting cycle after all prior deliveries and retention.
    from convest.ingest.batches import durable_json
    import time
    started=time.time_ns()
    durable_json(Path(s['annotation']['root'])/'review-cycle.json',{'state':'collecting','started_ns':started})
    with annotation.database(s) as db:
        old=db.execute('SELECT * FROM episodes WHERE id=?',(j,)).fetchone()
        payload=json.loads(old['payload']);payload['id']='new-job'
        db.execute('INSERT INTO episodes(id,route,batch_id,source_ep,payload,state,labels,updated_ns) VALUES(?,?,?,?,?,?,?,?)',
                   ('new-job',old['route'],'new-batch',old['source_ep'],json.dumps(payload),'needs_review',json.dumps(steps),time.time_ns()))
        db.commit()
    review.edit(s,'new-job',0,steps,action='approve')
    durable_json(Path(s['annotation']['root'])/'review-cycle.json',{'state':'review','started_ns':started})
    # Historical approval no longer needs readable local videos on the next cycle.
    with annotation.database(s) as db:
        old_payload={**json.loads(old['payload']),'source':'/nonexistent/released-history'}
        db.execute('UPDATE episodes SET payload=? WHERE id=?',(json.dumps(old_payload),j));db.commit()
    assert list(review.release(s)['approved'])==['new-job']
