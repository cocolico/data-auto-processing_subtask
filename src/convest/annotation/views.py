"""Camera identities and paths on the shared exported episode timeline."""
from pathlib import Path

from .native import path_for, read_json

VIEWS = {
    'cam0': 'External overview; image-left is robot LEFT, image-right is robot RIGHT.',
    'cam1': 'RIGHT wrist camera, mounted on the robot RIGHT hand/arm.',
    'cam2': 'LEFT wrist camera, mounted on the robot LEFT hand/arm.',
}


def videos_for(job):
    # Resolve from metadata so pending jobs saved by the cam0-only version also
    # receive all three views without replacing their queue identities.
    source = Path(job['source'])
    info = read_json(source / 'meta/info.json')
    result = {}
    for camera in VIEWS:
        key = 'observation.images.' + camera
        if key not in info['features'] or info['features'][key]['dtype'] != 'video':
            raise ValueError(f'三视角标注缺少视频特征：{key}')
        video = source / path_for(info, job['source_ep'], video_key=key)
        if not video.is_file():
            raise ValueError(f'三视角标注源视频不可用：{camera}')
        result[camera] = video
    return result
