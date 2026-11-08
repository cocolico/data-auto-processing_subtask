"""Three-view requests, partial upload cleanup and synchronized video checks."""
import json
from types import SimpleNamespace

import pytest

from convest.annotation import client, credentials, service
from convest.annotation.native import atomic_json
from convest.annotation.provider import ArkProvider, Failure
from convest.annotation.views import videos_for


def source_job(tmp_path):
    source = tmp_path/'source'
    (source/'meta').mkdir(parents=True)
    info = {'chunks_size': 1000,
            'video_path': 'videos/{video_key}/episode_{episode_index:06d}.mp4',
            'features': {f'observation.images.cam{i}':
                         {'dtype': 'video', 'shape': [16, 16, 3]} for i in range(3)}}
    atomic_json(source/'meta/info.json', info)
    for i in range(3):
        path = source/f'videos/observation.images.cam{i}/episode_000000.mp4'
        path.parent.mkdir(parents=True)
        path.write_bytes(f'camera-{i}'.encode())
    return {'source': str(source), 'source_ep': 0, 'length': 30, 'fps': 30,
            'video': str(source/'videos/observation.images.cam0/episode_000000.mp4'),
            'robot': 'wuji', 'task': ['tomato'], 'recording_id': 'recording',
            'settings': {'model': 'test-model', 'fps': 5, 'retries': 1}}


def reply():
    from convest.annotation.stages import CATEGORIES
    return SimpleNamespace(id='response-id',usage=None,output_text=json.dumps({'steps':[
        {'stage_index':i,'phase':phase,'text':text,'start_sec':(i*30//7)/30,'end_sec':((i+1)*30//7)/30}
        for i,(phase,_,text) in enumerate(CATEGORIES)]}))



class FakeClient:
    def __init__(self, fail_upload=None, fail_delete=False, invalid_reply=False):
        self.files = self.responses = self
        self.uploads, self.deleted, self.requests = [], [], []
        self.closed = False
        self.fail_upload, self.fail_delete, self.invalid_reply = fail_upload, fail_delete, invalid_reply

    def create(self, **kwargs):
        if 'file' in kwargs:
            number = len(self.uploads)
            if number == self.fail_upload:
                raise ConnectionError('upload interrupted')
            self.uploads.append((kwargs['file'].read(), kwargs['extra_body']))
            return SimpleNamespace(id=f'file-{number}')
        self.requests.append(kwargs)
        result = reply()
        if self.invalid_reply:
            result.output_text = '{}'
        return result

    def retrieve(self, file_id):
        return reply() if file_id == 'response-id' else SimpleNamespace(status='active')

    def delete(self, file_id):
        if self.fail_delete:
            raise ConnectionError('delete interrupted')
        self.deleted.append(file_id)

    def with_options(self, **kwargs):
        return self

    def close(self):
        self.closed = True


def test_three_videos_sent_once_with_camera_ownership_and_same_timeline(tmp_path, monkeypatch):
    job = source_job(tmp_path)
    fake = FakeClient()
    monkeypatch.setattr(client, 'create_client', lambda *a, **kw: fake)
    work = tmp_path/'request'
    labels, _ = ArkProvider().annotate(job, 'test-key', work)
    assert len(fake.requests) == 1
    assert [data for data, _ in fake.uploads] == [b'camera-0', b'camera-1', b'camera-2']
    content = fake.requests[0]['input'][0]['content']
    assert [p['file_id'] for p in content if p['type'] == 'input_video'] == ['file-0', 'file-1', 'file-2']
    assert 'cam1: RIGHT wrist' in content[3]['text']
    assert 'cam2: LEFT wrist' in content[5]['text']
    assert 'same clip-local zero' in fake.requests[0]['instructions']
    assert labels[0]['start_frame'] == 0 and labels[-1]['end_frame'] == 30
    assert fake.deleted == ['file-0', 'file-1', 'file-2'] and fake.closed
    assert all(json.loads(p.read_text())['cleanup'] == 'completed' for p in work.rglob('remote_file.json'))
    assert ArkProvider().annotate(job, 'test-key', work)[0] == labels
    assert len(fake.requests) == 1


@pytest.mark.parametrize('failure', ['partial_upload', 'invalid_reply', 'cleanup'])
def test_partial_failures_keep_all_remote_files_accounted_for(tmp_path, monkeypatch, failure):
    job = source_job(tmp_path)
    fake = FakeClient(fail_upload=1 if failure == 'partial_upload' else None,
                      invalid_reply=failure == 'invalid_reply', fail_delete=failure == 'cleanup')
    monkeypatch.setattr(client, 'create_client', lambda *a, **kw: fake)
    work = tmp_path/'request'
    if failure == 'cleanup':
        ArkProvider().annotate(job, 'test-key', work)
    else:
        with pytest.raises(Failure):
            ArkProvider().annotate(job, 'test-key', work)
    records = [json.loads(p.read_text()) for p in work.rglob('remote_file.json')]
    assert len(records) == len(fake.uploads)
    assert all(r['cleanup'] == ('pending' if failure == 'cleanup' else 'completed') for r in records)
    assert len(fake.requests) == (0 if failure == 'partial_upload' else 1)
    assert fake.closed
    if failure == 'invalid_reply':
        assert (work/'response.json').exists()


def test_missing_wrist_view_fails_before_upload(tmp_path, monkeypatch):
    job = source_job(tmp_path)
    videos_for(job)['cam2'].unlink()
    fake = FakeClient()
    monkeypatch.setattr(client, 'create_client', lambda *a, **kw: fake)
    with pytest.raises(Failure, match='cam2'):
        ArkProvider().annotate(job, 'test-key', tmp_path/'request')
    assert not fake.uploads and not fake.requests and fake.closed


@pytest.mark.parametrize('bad_view', [None, 'cam1', 'cam2'])
def test_real_service_checks_all_camera_timelines_before_request(tmp_path, monkeypatch, bad_view):
    av = pytest.importorskip('av')
    job = source_job(tmp_path)
    for camera, path in videos_for(job).items():
        with av.open(str(path), mode='w') as container:
            stream = container.add_stream('libx264', rate=30)
            stream.width = stream.height = 16
            stream.pix_fmt = 'yuv420p'
            for i in range(29 if camera == bad_view else 30):
                frame = av.VideoFrame(16, 16, 'yuv420p')
                for plane in frame.planes:
                    plane.update(bytes(plane.buffer_size))
                frame.pts = i
                for packet in stream.encode(frame):
                    container.mux(packet)
            for packet in stream.encode():
                container.mux(packet)
    root = tmp_path/'annotation'
    spec = {'annotation': {'enabled': True, 'root': str(root),
                          'credential_root': str(tmp_path/'credentials'), 'min_free_gib': 0}}
    credentials.save(spec['annotation']['credential_root'], 'test-key')
    fake = FakeClient()
    monkeypatch.setattr(client, 'create_client', lambda *a, **kw: fake)
    with service.database(spec) as db:
        db.execute('INSERT INTO episodes(id,source_ep,payload,state,updated_ns) VALUES(?,?,?,?,?)',
                   ('job', 0, json.dumps(job), 'pending', 0))
        db.commit()
        assert service.process_one(spec, db)
        row = db.execute('SELECT * FROM episodes WHERE id="job"').fetchone()
        if bad_view is None:
            # A retry must reject changed wrist data even when cam0 is unchanged.
            with videos_for(job)['cam1'].open('ab') as stream:
                stream.write(b'changed-after-first-inference')
            service.update(db, 'episodes', 'job', state='pending', next_ns=0)
            assert service.process_one(spec, db)
            retry = db.execute('SELECT * FROM episodes WHERE id="job"').fetchone()
            assert retry['state'] == 'excluded'
            assert '请求之间发生变化' in retry['error']
            assert len(fake.requests) == 1
    if bad_view:
        assert row['state'] == 'excluded' and not fake.requests and not fake.uploads
        assert 'frame count mismatch' in row['error']
    else:
        assert row['state'] == 'completed' and len(fake.requests) == 1
        evidence = json.loads((root/'sources/job.json').read_text())
        assert set(evidence['videos']) == {'cam0', 'cam1', 'cam2'}


def test_durable_response_id_is_recovered_without_upload_or_new_inference(tmp_path, monkeypatch):
    job = source_job(tmp_path)
    fake = FakeClient()
    monkeypatch.setattr(client, 'create_client', lambda *a, **kw: fake)
    work = tmp_path/'request'
    atomic_json(work/'request.json', {'phase': 'returned', 'response_id': 'response-id'})
    labels, _ = ArkProvider().annotate(job, 'test-key', work)
    assert labels[-1]['end_frame'] == 30
    assert not fake.uploads and not fake.requests and fake.closed
