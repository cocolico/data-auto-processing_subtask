"""Exercise real SDK files, time boundaries and source authorization."""
from pathlib import Path
import json
import subprocess
import sys

import pytest

from convest.annotation import preview, visualization
from convest.annotation.granularity import derive
from convest.annotation.native import atomic_json


@pytest.fixture
def recording(tmp_path):
    av = pytest.importorskip('av')
    import numpy as np
    source = tmp_path/'source'
    (source/'meta').mkdir(parents=True)
    steps=[{'start_frame': 0, 'end_frame': 7, 'phase':'GRASP_CLUSTER','text':'Grasp the truss.',
            'target_id':None,'decision':None,'return_condition':None},
           {'start_frame': 7, 'end_frame': 30, 'phase':'PRESENT_CLUSTER','text':'Lift the truss.',
            'target_id':None,'decision':None,'return_condition':None}]
    cameras={}
    for n in range(3):
        key=f'observation.images.cam{n}'
        path=source/f'videos/{key}/episode_000000.mp4'
        path.parent.mkdir(parents=True)
        with av.open(str(path),'w') as container:
            stream=container.add_stream('libx264',rate=30)
            stream.width=32;stream.height=24;stream.pix_fmt='yuv420p'
            for i in range(30):
                array=np.full((24,32,3),i*5+n*10,dtype=np.uint8)
                frame=av.VideoFrame.from_ndarray(array,format='rgb24');frame.pts=i
                for packet in stream.encode(frame):container.mux(packet)
            for packet in stream.encode():container.mux(packet)
        cameras[key]=str(path)
    info={'chunks_size':1000,'video_path':'videos/{video_key}/episode_{episode_index:06d}.mp4',
          'features':{k:{'dtype':'video'} for k in cameras}}
    atomic_json(source/'meta/info.json',info)
    payload={'source':str(source),'source_ep':0,'recording_id':'test','fps':30,'length':30,'settings':{'model':'test'}}
    data={'id':'job','fps':30,'frames':30,'steps':steps,'granularity':derive(steps,30,30),
          'camera_status':{k:{'label':k,'available':True} for k in cameras}}
    return source,cameras,payload,data


def test_preview_describes_each_camera_without_claiming_model_confidence(recording,monkeypatch):
    source,cameras,payload,data=recording
    monkeypatch.setattr(preview,'job',lambda *_:(payload,data['steps']))
    Path(cameras['observation.images.cam2']).unlink()
    result=preview.describe({},'job')
    assert result['camera_status']['observation.images.cam1']=={'available':True,'label':'右手腕'}
    assert not result['camera_status']['observation.images.cam2']['available']
    assert 'confidence' not in result


def test_sampled_images_include_non_grid_boundary_and_last_frame(recording):
    _,cameras,_,data=recording
    images=list(visualization.sampled_images(next(iter(cameras.values())),data))
    assert [index for index,_ in images]==[0,7,15,29]
    assert all(jpeg.startswith(b'\xff\xd8') for _,jpeg in images)


@pytest.mark.parametrize('format',['rrd','mcap'])
def test_real_sdk_export_retains_three_views_and_annotation_times(recording,tmp_path,format):
    pytest.importorskip(visualization.FORMATS[format])
    _,cameras,_,data=recording
    output=tmp_path/f'preview.{format}'
    visualization.export({'data':data,'cameras':cameras,'format':format,'output':str(output)})
    assert output.stat().st_size>1000
    assert not output.with_name('preview.partial.'+format).exists()
    if format=='mcap':
        from mcap.reader import make_reader
        with output.open('rb') as stream:
            reader=make_reader(stream)
            messages=list(reader.iter_messages())
        images=[(channel,message) for schema,channel,message in messages if channel.topic.startswith('/cameras/')]
        assert {c.topic for c,_ in images}=={'/cameras/cam0','/cameras/cam1','/cameras/cam2'}
        assert len(images)==12
        steps=[m for _,c,m in messages if c.topic=='/annotations/steps']
        assert [m.log_time for m in steps]==[0,round(7/30*1e9)]
        assert json.loads(steps[1].data)['start_frame']==7
    else:
        check=subprocess.run([sys.executable,'-m','rerun','rrd','verify',str(output)],capture_output=True,text=True)
        assert check.returncode==0,check.stderr
        printed=subprocess.run([sys.executable,'-m','rerun','rrd','print',str(output)],capture_output=True,text=True)
        assert printed.returncode==0,printed.stderr
        for entity in ['cameras/cam0','cameras/cam1','cameras/cam2','annotations/current']:
            assert entity in printed.stdout
    with pytest.raises(FileExistsError):
        visualization.export({'data':data,'cameras':cameras,'format':format,'output':str(output)})


def test_export_rejects_untrusted_source_and_invalid_identity(recording,tmp_path,monkeypatch):
    source,_,payload,data=recording
    monkeypatch.setattr(preview,'job',lambda *_:(payload,data['steps']))
    spec={'annotation':{'root':str(tmp_path/'annotation')},'capture':{'routes':[{'output':str(tmp_path/'other')}]}}
    with pytest.raises(ValueError,match='范围'):
        visualization.start(spec,'job','mcap')
    with pytest.raises(ValueError,match='ID'):
        visualization.start(spec,'../job','rrd')
    assert not (tmp_path/'annotation').exists()


def test_failed_export_never_publishes_partial_file(recording,tmp_path,monkeypatch):
    _,cameras,_,data=recording
    output=tmp_path/'preview.mcap'
    def broken(request,target):
        target.write_bytes(b'partial')
        raise RuntimeError('simulated export error')
    monkeypatch.setattr(visualization,'_foxglove',broken)
    with pytest.raises(RuntimeError,match='simulated'):
        visualization.export({'data':data,'cameras':cameras,'format':'mcap','output':str(output)})
    assert not output.exists() and not (tmp_path/'preview.partial.mcap').exists()


def test_background_export_is_downloadable_and_keeps_source_unchanged(recording,tmp_path,monkeypatch):
    import time
    pytest.importorskip('foxglove')
    source,cameras,payload,data=recording
    before={p:p.read_bytes() for p in source.rglob('*') if p.is_file()}
    monkeypatch.setattr(preview,'job',lambda *_:(payload,data['steps']))
    spec={'annotation':{'root':str(tmp_path/'annotation')},'capture':{'routes':[{'output':str(source)}]}}
    first=visualization.start(spec,'job','mcap')
    assert first['state']=='running'
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        result=visualization.status(spec,'job','mcap')
        if result['state']!='running':break
        time.sleep(.02)
    assert result['state']=='completed',result
    assert visualization.artifact(spec,'job','mcap').read_bytes().startswith(b'\x89MCAP')
    assert visualization.start(spec,'job','mcap')['state']=='completed'
    assert before=={p:p.read_bytes() for p in before}


def test_viewer_http_download_requires_token_and_retains_byte_ranges(recording,tmp_path,monkeypatch):
    import secrets
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer
    from convest.gui.server import Handler
    source,_,payload,data=recording
    monkeypatch.setattr(preview,'job',lambda *_:(payload,data['steps']))
    spec={'annotation':{'root':str(tmp_path/'annotation')},'capture':{'routes':[{'output':str(source)}]}}
    _,_,folder,output=visualization._request(spec,'job','mcap')
    folder.mkdir(parents=True);output.write_bytes(b'0123456789')
    class Manager:
        def annotation_spec(self,task):
            assert task=='task'
            return spec
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    server.manager=Manager();server.token=secrets.token_urlsafe(32)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    query='/api/annotation/viewer-file?task=task&job=job&format=mcap'
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(base+query)
        assert exc.value.code==403
        request=urllib.request.Request(base+query+'&token='+server.token,headers={'Range':'bytes=2-5'})
        with urllib.request.urlopen(request) as response:
            assert response.status==206 and response.read()==b'2345'
            assert response.headers['Content-Type']=='application/octet-stream'
            assert response.headers['Content-Disposition']=='attachment; filename="annotation.mcap"'
        request=urllib.request.Request(base+'/api/annotation/viewer-export',data=b'{}',headers={'Content-Type':'application/json'})
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(request)
        assert exc.value.code==403
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)
