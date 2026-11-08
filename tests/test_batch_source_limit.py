"""Count-based batches: 100 is a capacity, short directory tails stay valid."""
from convest.ingest.batches import BatchPublisher
from convest.ingest.worker import worker_context
from test_ingest import service,bag,ready_scan


def test_count_cap_rolls_at_boundary_and_short_tail_seals_at_directory_end(service):
    cfg=service;route=cfg['routes'][2];route['max_batch_sources']=2
    for n in (1,2,3):bag(cfg,2,n)
    with worker_context(cfg) as queue:
        assert ready_scan(cfg,queue)['counts']=={'published':3}
    publisher=BatchPublisher(route,cfg['fingerprint']);batches=publisher.status()
    assert len(batches)==2
    assert batches[0]['sources']==['episode1','episode2'] and batches[0]['state']=='sealed'
    assert batches[0]['seal_reason']=='source_limit' and batches[0]['max_sources']==2
    assert batches[1]['sources']==['episode3'] and batches[1]['state']=='active'
    publisher.published(now_ns=batches[1]['deadline_ns']+10**15)
    assert publisher.status()[1]['state']=='active'
    publisher.seal_active()
    assert publisher.status()[1]['seal_reason']=='directory_complete'


def test_100_source_limit_is_not_a_64_source_limit(tmp_path,monkeypatch):
    route={'id':'wuji-A','output':str(tmp_path/'batch'),'batch_hours':24,'max_batch_sources':100}
    publisher=BatchPublisher(route,'fingerprint')
    monkeypatch.setattr(publisher,'_save',lambda c:None)
    monkeypatch.setattr(publisher,'_verify_seal',lambda b:{'checked_ns':10})
    b={'id':'batch-000001','state':'active','deadline_ns':1,'source_count':65,'max_sources':100}
    publisher._seal_due({'batches':[b]},100)
    assert b['state']=='active'
    b['source_count']=99;publisher._seal_due({'batches':[b]},100);assert b['state']=='active'
    b['source_count']=100;publisher._seal_due({'batches':[b]},100)
    assert b['state']=='sealed' and b['seal_reason']=='source_limit'
