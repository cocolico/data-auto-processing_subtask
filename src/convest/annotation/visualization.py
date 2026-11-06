"""Optional local SDK exports. One isolated exporter runs at a time."""
from concurrent.futures import ThreadPoolExecutor
from importlib.util import find_spec
import argparse
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading

from .native import atomic_json, read_json

FORMATS = {'rrd': 'rerun', 'mcap': 'foxglove'}
SAMPLE_FPS = 2
MAX_BYTES = 512 * 1024**2
MIN_FREE = 512 * 1024**2
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='annotation-viewer')
_lock = threading.Lock()
_jobs = {}


def _request(spec, identity, format):
    from .preview import describe, video
    if format not in FORMATS or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', identity):
        raise ValueError('无效的查看器格式或结果 ID')
    data = describe(spec, identity)
    cameras = {key: str(video(spec, identity, key)) for key in data['cameras']
               if key in ('observation.images.cam0', 'observation.images.cam1', 'observation.images.cam2')
               and data['camera_status'][key]['available']}
    if not cameras:
        raise ValueError('本机视频已释放，仍可下载标注 JSON，无法导出图像预览')
    data['sample_fps'] = SAMPLE_FPS
    fingerprints = {key: [Path(p).stat().st_size, Path(p).stat().st_mtime_ns] for key, p in cameras.items()}
    signature = hashlib.sha256(json.dumps([data, cameras, fingerprints], sort_keys=True).encode()).hexdigest()[:20]
    root = Path(spec['annotation']['root']).resolve()/'viewer-exports'/identity/signature
    # Avoid following an externally substituted export directory.
    if any(p.is_symlink() for p in [root, *root.parents]):
        raise ValueError('导出目录不能包含软链接')
    return data, cameras, root, root/f'preview.{format}'


def status(spec, identity, format):
    _, _, root, output = _request(spec, identity, format)
    with _lock:
        job = _jobs.get(str(output))
        if job and job['state'] != 'completed':
            return dict(job)
    if output.is_file():
        return {'state': 'completed', 'message': f'已生成 {format.upper()}：{output.stat().st_size / 1024**2:.1f} MiB，点击下载后用对应查看器打开。'}
    return {'state': 'idle', 'message': '尚未生成查看器文件。'}


def start(spec, identity, format):
    data, cameras, root, output = _request(spec, identity, format)
    if find_spec(FORMATS[format]) is None:
        raise ValueError('未安装可视化 SDK；请在当前运行环境安装项目的 visualization 可选依赖')
    root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(root).free < MIN_FREE:
        raise ValueError('本机可用空间低于 512 MiB，暂不生成预览文件')
    key = str(output)
    with _lock:
        if output.is_file():
            return {'state': 'completed', 'message': '已生成，点击下载查看器文件。'}
        if key in _jobs and _jobs[key]['state'] == 'running':
            return dict(_jobs[key])
        if any(j['state'] == 'running' for j in _jobs.values()):
            raise ValueError('另一个查看器文件正在生成，请稍后重试')
        request = root/f'{format}-request.json'
        atomic_json(request, {'data': data, 'cameras': cameras, 'output': str(output), 'format': format})
        _jobs[key] = {'state': 'running', 'message': f'正在本机生成 {format.upper()}，采样率 2 fps，动作边界完整保留…'}
        _pool.submit(_run, request, key)
        return dict(_jobs[key])


def _run(request, key):
    try:
        result = subprocess.run([sys.executable, '-m', __name__, str(request)],
                                capture_output=True, text=True, timeout=900)
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout)[-1200:])
        if not Path(key).is_file():
            raise RuntimeError('导出进程未生成完整文件')
        value = {'state': 'completed', 'message': '已生成，点击下载后用对应查看器打开。'}
    except Exception as exc:
        value = {'state': 'failed', 'message': f'查看器导出失败：{exc}。原始数据和标注未修改。'}
    with _lock:
        _jobs[key] = value


def artifact(spec, identity, format):
    _, _, _, output = _request(spec, identity, format)
    if not output.is_file() or output.is_symlink():
        raise ValueError('查看器文件尚未生成')
    return output


def _budget(output):
    if shutil.disk_usage(output.parent).free < MIN_FREE:
        raise ValueError('导出时本机可用空间不足 512 MiB')
    if output.exists() and output.stat().st_size > MAX_BYTES:
        raise ValueError('单条可视化文件超过 512 MiB 上限，请选用更短的 Episode')


def sampled_images(path, data):
    """Decode incrementally; retain exact segment starts in addition to 2 Hz."""
    import av
    stride = max(1, round(data['fps']/SAMPLE_FPS))
    boundaries = {s['start_frame'] for s in data['steps']} | {data['frames']-1}
    count = 0
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        if stream.average_rate != data['fps']:
            raise ValueError('源视频帧率与标注不一致')
        for index, frame in enumerate(container.decode(video=0)):
            count += 1
            if frame.pts is None or abs(float(frame.pts*frame.time_base)-index/data['fps']) > 1e-4:
                raise ValueError('源视频时间轴与标注不一致')
            if index % stride == 0 or index in boundaries:
                image = frame.to_image()
                image.thumbnail((640, 480))
                buffer = BytesIO()
                image.save(buffer, format='JPEG', quality=85)
                yield index, buffer.getvalue()
    if count != data['frames']:
        raise ValueError('源视频帧数与标注不一致')


def _rerun(request, target):
    import rerun as rr
    import rerun.blueprint as blueprint
    data = request['data']
    recording = rr.RecordingStream('tomato-annotation-review')
    views = [blueprint.Spatial2DView(origin='/cameras/'+key.rsplit('.', 1)[-1],
             name=data['camera_status'][key]['label']) for key in request['cameras']]
    layout = blueprint.Blueprint(blueprint.Horizontal(
        blueprint.Vertical(*views),
        blueprint.Vertical(blueprint.TextDocumentView(origin='/annotations/current', name='当前动作'),
                           blueprint.TextLogView(origin='/annotations/events', name='动作边界'))),
        blueprint.TimePanel(timeline='episode_time', expanded=True))
    recording.save(target, default_blueprint=layout)
    try:
        recording.log('/metadata', rr.TextDocument(json.dumps(data, ensure_ascii=False, indent=2)), static=True)
        for number, step in enumerate(data['steps']):
            recording.set_time('episode_time', duration=step['start_frame']/data['fps'])
            document = f"# {number+1}. {step.get('phase', '')}\n\n{step['text']}\n\n```json\n{json.dumps(step, ensure_ascii=False, indent=2)}\n```"
            recording.log('/annotations/current', rr.TextDocument(document, media_type='text/markdown'))
            recording.log('/annotations/events', rr.TextLog(f"{number+1}: {step.get('phase', '')} [{step['start_frame']}, {step['end_frame']})"))
        for camera, path in request['cameras'].items():
            entity = '/cameras/'+camera.rsplit('.', 1)[-1]
            for index, jpeg in sampled_images(path, data):
                _budget(target)
                recording.set_time('episode_time', duration=index/data['fps'])
                recording.log(entity, rr.EncodedImage(contents=jpeg, media_type='image/jpeg'))
        recording.set_time('episode_time', duration=data['frames']/data['fps'])
        recording.log('/annotations/current', rr.TextDocument('Episode 已结束'))
        recording.flush(timeout_sec=30)
    finally:
        recording.disconnect()


def _foxglove(request, target):
    import foxglove
    from foxglove.channels import CompressedImageChannel
    from foxglove.messages import CompressedImage, Timestamp
    data = request['data']
    context = foxglove.Context()
    schema = {'type': 'object', 'properties': {
        'step_index': {'type': 'integer'}, 'start_frame': {'type': 'integer'},
        'end_frame': {'type': 'integer'}, 'phase': {'type': 'string'},
        'text': {'type': 'string'}, 'target_id': {'type': ['string', 'null']},
        'decision': {'type': ['object', 'null']}, 'return_condition': {'type': ['object', 'null']}}}
    annotations = foxglove.Channel('/annotations/steps', schema=schema, context=context)
    channels = {key: CompressedImageChannel('/cameras/'+key.rsplit('.', 1)[-1], context=context,
                metadata={'camera': data['camera_status'][key]['label']}) for key in request['cameras']}
    with foxglove.open_mcap(target, context=context) as writer:
        writer.write_metadata('annotation-review', {'sample_fps': str(SAMPLE_FPS),
            'dataset_fps': str(data['fps']), 'time_origin': 'episode-local zero',
            'cam1': 'right wrist', 'cam2': 'left wrist'})
        writer.attach(log_time=0, create_time=0, name='annotations.json', media_type='application/json',
                      data=json.dumps(data, ensure_ascii=False).encode())
        for number, step in enumerate(data['steps']):
            ns = round(step['start_frame']/data['fps']*1e9)
            annotations.log({'step_index': number+1, **step}, log_time=ns)
        for key, path in request['cameras'].items():
            for index, jpeg in sampled_images(path, data):
                _budget(target)
                ns = round(index/data['fps']*1e9)
                channels[key].log(CompressedImage(timestamp=Timestamp(ns//10**9, ns%10**9),
                    frame_id=key.rsplit('.', 1)[-1], format='jpeg', data=jpeg), log_time=ns)


def export(request):
    output = Path(request['output'])
    target = output.with_name(output.stem+'.partial'+output.suffix)
    if output.exists():
        raise FileExistsError(output)
    target.unlink(missing_ok=True)
    try:
        _budget(target)
        {'rrd': _rerun, 'mcap': _foxglove}[request['format']](request, target)
        _budget(target)
        with target.open('rb') as stream:
            os.fsync(stream.fileno())
        target.rename(output)
    finally:
        target.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Local annotation visualization export worker')
    parser.add_argument('request')
    args = parser.parse_args(argv)
    export(read_json(args.request))


if __name__ == '__main__':
    main()
