"""Test one existing LeRobot episode without starting workflow/upload workers.

Default: read-only local preflight. --call-model uploads cam0 to Ark and may
incur API charges. No SSH/rsync or data/training-server delivery is invoked.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import tempfile

from convest.annotation import credentials
from convest.annotation.native import inspect, path_for, export_dataset, validate_output
from convest.annotation.provider import ArkProvider, redact
from convest.config import WORKSPACE
from convest.training.adatempo.lerobot_video import inspect_video


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True,
                        help='Existing complete LeRobot v2.1 dataset; read only')
    parser.add_argument('--episode', type=int, default=0)
    parser.add_argument('--model', default='doubao-seed-2-0-lite-260428')
    parser.add_argument('--fps', type=float, default=5.0,
                        help='Model video sampling FPS, not dataset FPS')
    parser.add_argument('--call-model', action='store_true',
                        help='Explicitly permit one real Ark annotation attempt')
    args = parser.parse_args()
    if not 0.2 <= args.fps <= 5:
        parser.error('--fps must be between 0.2 and 5')
    source = inspect(args.dataset)
    if not 0 <= args.episode < len(source['episodes']):
        parser.error('--episode is outside this dataset')
    info = source['info']
    robot = {'dual_fr3_wuji': 'wuji', 'dual_fr3_sharpa': 'sharpa'}.get(info.get('robot_type'))
    if robot is None:
        parser.error('Expected a Wuji or Sharpa dataset from this converter')
    episode = source['episodes'][args.episode]
    video = source['root'] / path_for(info, args.episode,
                                    video_key='observation.images.cam0')
    inspect_video(video, episode['length'], info['fps'],
                  info['features']['observation.images.cam0']['shape'])
    if video.stat().st_size > 512 * 1024**2:
        parser.error('cam0 exceeds the adapter upload limit of 512 MiB')
    key = credentials.load(WORKSPACE / 'outputs/gui')
    if not key:
        parser.error('No configured Ark credential')
    print(json.dumps({'dataset': str(source['root']), 'episode': args.episode,
                      'video': str(video), 'duration_s': episode['length']/info['fps'],
                      'model': args.model, 'model_sampling_fps': args.fps,
                      'local_preflight': 'passed'}, ensure_ascii=False, indent=2))
    if not args.call_model:
        print('仅完成本地预检查；未发起模型请求，未启动上传任务。')
        return
    base = WORKSPACE / 'outputs/annotation-smoke'
    base.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix=datetime.now().strftime('%Y%m%d-%H%M%S-'), dir=base))
    print('本地结果目录：', run, flush=True)
    job = {'video': str(video), 'length': episode['length'], 'fps': info['fps'],
           'task': episode['tasks'], 'robot': robot,
           'source': str(source['root']), 'source_ep': args.episode,
           'settings': {'model': args.model, 'fps': args.fps, 'retries': 0}}
    try:
        labels, usage = ArkProvider().annotate(
            job, key, run / 'request',
            lambda **status: print(json.dumps(status, ensure_ascii=False), flush=True))
        (run / 'labels.json').write_text(json.dumps(
            {'source': str(source['root']), 'episode': args.episode,
             'dataset_fps': info['fps'], 'steps': labels, 'usage': usage},
            ensure_ascii=False, indent=2) + '\n')
        validate_ending_motion(source['root'], args.episode, labels)
        validate_action_motion(source['root'], args.episode, labels)
        selected = {args.episode: labels}
        export_dataset(source, run / 'dataset', selected)
        report = validate_output(source, run / 'dataset', selected)
        (run / 'validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        print('标注与本地导出校验通过。双粒度结果已保存；未上传至数据/训练服务器。')
    except Exception as exc:
        (run / 'error.json').write_text(json.dumps(
            {'type': type(exc).__name__, 'message': redact(exc, key)},
            ensure_ascii=False, indent=2) + '\n')
        raise SystemExit('测试未通过；详情见本地 error.json。没有自动重试。') from None


if __name__ == '__main__':
    main()
