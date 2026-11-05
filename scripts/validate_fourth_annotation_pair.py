"""Explicit local fourth-episode acceptance; no model API, queue, upload or training."""
from datetime import datetime
import json
from pathlib import Path
import shutil
import tempfile

from convest.config import WORKSPACE
from convest.annotation import native
from convest.annotation.compact import project
from convest.capture.transfer import manifest
from convest.training.normal import export as normal_export
from convest.training.adatempo.lerobot_main import process
from convest.training.adatempo.lerobot_io import dump


def main():
    base = WORKSPACE/'outputs/normal-speedup-acceptance'
    base.mkdir(parents=True, exist_ok=True)
    trial = Path((WORKSPACE/'outputs/prompt-experiments/fourth-episode/latest.txt').read_text().strip())
    inp = json.loads((trial/'input.json').read_text())
    checks = json.loads((trial/'validation.json').read_text())
    if checks['coverage']['status'] != 'passed' or checks['schema_and_protocol']['status'] != 'passed':
        raise ValueError('Fourth annotation trial did not pass')
    remote = Path(inp['job']['source'])
    info = native.read_json(remote/'meta/info.json')
    episode = next(e for e in native.rows(remote/'meta/episodes.jsonl') if e['episode_index'] == 3)
    labels = project(native.read_json(trial/'labels.json')['steps'])
    native.validate_segments(labels, episode['length'])
    run = Path(tempfile.mkdtemp(prefix=datetime.now().strftime('%Y%m%d-%H%M%S-fourth-'), dir=base))
    (base/'latest.txt').write_text(str(run))
    cache = run/'input-cache'
    cache.mkdir()
    cameras = [k for k,v in info['features'].items() if v['dtype']=='video']
    paths = [native.path_for(info, 3)] + [native.path_for(info, 3, video_key=k) for k in cameras]
    provenance = []
    for relative in paths:
        print('COPY', relative, flush=True)
        src, dst = remote/relative, cache/relative
        before = (src.stat().st_size, src.stat().st_mtime_ns)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        sha = native.sha256(dst)
        if sha != native.sha256(src) or before != (src.stat().st_size, src.stat().st_mtime_ns):
            raise ValueError('Source changed while copying')
        provenance.append({'path': relative, 'bytes': dst.stat().st_size, 'sha256': sha})
    cam0 = next(p for p in provenance if 'cam0' in p['path'])
    if cam0['sha256'] != inp['sha256']:
        raise ValueError('Video differs from annotated fourth trial')
    source = {'root': cache, 'info': info, 'episodes': [episode],
              'stats': native.rows(remote/'meta/episodes_stats.jsonl'), 'cameras': cameras}
    full = run/'source'
    print('EXPORT_ANNOTATED_SOURCE', run, flush=True)
    native.export_dataset(source, full, {3: labels})
    report = native.validate_output(source, full, {3: labels})
    dump(run/'source.complete.json', report)
    dump(run/'source.json', {'remote': str(remote), 'source_episode_index': 3,
                             'annotation_trial': str(trial), 'annotation_sha256': native.sha256(trial/'labels.json'),
                             'input_files': provenance, 'local_episode_index': 0})
    normal, speedup = run/'normal', run/'speedup'
    normal.mkdir();speedup.mkdir()
    print('NORMAL', flush=True)
    normal_report = normal_export(full, normal, 'wuji')
    dump(run/'normal.complete.json', {'validation': normal_report, 'files': manifest(normal)})
    print('SPEEDUP', flush=True)
    speedup_report = process(normal, speedup, workers=1, cmax=3.)
    dump(run/'speedup.complete.json', {'validation': speedup_report, 'files': manifest(speedup)})
    # Independently verify immutable full source after both transformations.
    native.validate_output(source, full, {3: labels})
    dump(run/'acceptance.json', {'status': 'passed', 'source_frames': episode['length'],
                               'normal': normal_report, 'speedup': speedup_report,
                               'model_called': False, 'uploaded': False, 'training_started': False})
    print('ACCEPTANCE_READY', run, flush=True)


if __name__ == '__main__':
    main()
