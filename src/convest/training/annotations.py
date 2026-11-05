"""Preserve compact dual labels and remap them on the actual video sampling grid."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from convest.annotation.granularity import FILENAME, derive
from convest.annotation.native import validate_segments
from .adatempo.sampling import nearest_frames


def digest(root):
    return hashlib.sha256((Path(root)/'meta'/FILENAME).read_bytes()).hexdigest()


def load(root):
    path = Path(root)/'meta'/FILENAME
    if not path.exists():
        return None
    rows = [json.loads(s) for s in path.read_text().splitlines() if s.strip()]
    ids = [r['episode_index'] for r in rows]
    if ids != list(range(len(rows))):
        raise ValueError('Annotation episode IDs must be contiguous and unique')
    return rows


def save(root, rows):
    path = Path(root)/'meta'/FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(r, ensure_ascii=False, allow_nan=False)+'\n' for r in rows))


def validate_dataset(root):
    """Independently compare every frame label with its fine segment and vocabulary."""
    root = Path(root)
    rows = load(root)
    if rows is None:
        return None
    info = json.loads((root/'meta/info.json').read_text())
    episodes = [json.loads(s) for s in (root/'meta/episodes.jsonl').read_text().splitlines() if s.strip()]
    tasks = {r['task_index']: r['task'] for r in
             (json.loads(s) for s in (root/'meta/tasks.jsonl').read_text().splitlines() if s.strip())}
    if len(rows) != len(episodes) or len(rows) != info['total_episodes']:
        raise ValueError('Annotation episode count mismatch')
    for record, ep in zip(rows, episodes):
        if record['length'] != ep['length'] or record['fps'] != info['fps'] or record['episode_index'] != ep['episode_index']:
            raise ValueError('Annotation length/fps/episode mismatch')
        fine = record['fine_steps']
        validate_segments(fine, ep['length'])
        expected = derive(fine, info['fps'], ep['length'])
        if set(record) != set(expected) | {'episode_index', 'source_episode_index', 'source_recording_id'}:
            raise ValueError('Unexpected annotation schema')
        if any(record[k] != v for k, v in expected.items()):
            raise ValueError('Coarse/fine annotation content mismatch')
        path = root/info['data_path'].format(episode_index=ep['episode_index'], episode_chunk=ep['episode_index']//info['chunks_size'])
        table = pq.read_table(path, columns=['task_index'])
        ids = table['task_index'].to_pylist()
        if len(ids) != ep['length']:
            raise ValueError('Annotation/table length mismatch')
        for step in fine:
            if any(tasks.get(t) != step['text'].strip() for t in ids[step['start_frame']:step['end_frame']]):
                raise ValueError('Frame task_index differs from fine annotation')
    return rows


def source_step_indices(record, obs):
    """Source segment identity for each output frame, on the unchanged video grid."""
    ends = np.asarray([s['end_frame'] for s in record['fine_steps']])
    return np.searchsorted(ends, nearest_frames(obs), side='right')


def alignment(record, obs, source_hash):
    kept = np.unique(source_step_indices(record, obs)).tolist()
    return {'policy': 'original-sampling-nearest-frame-v1',
            'source_annotations_sha256': source_hash,
            'source_frame_index': nearest_frames(obs).tolist(),
            'fine_step_source_indices': kept,
            'skipped_fine_step_source_indices': [i for i in range(len(record['fine_steps'])) if i not in kept]}


def remap(record, obs):
    nearest = nearest_frames(obs)
    if len(nearest) == 0 or np.any(np.diff(nearest) <= 0) or nearest[0] < 0 or nearest[-1] >= record['length']:
        raise ValueError('Invalid discrete annotation sampling')
    source_steps = record['fine_steps']
    assignment = source_step_indices(record, obs)
    fine = []
    for index, step in enumerate(source_steps):
        hits = np.flatnonzero(assignment == index)
        if not len(hits):
            continue
        mapped = deepcopy(step)
        mapped.update(start_frame=int(hits[0]), end_frame=int(hits[-1]+1))
        fine.append(mapped)
    result = {k: record[k] for k in ('episode_index', 'source_episode_index', 'source_recording_id')}
    result.update(derive(fine, record['fps'], len(obs)))
    return result


def validate_pair(source, output, *, speedup):
    original = validate_dataset(source)
    actual = validate_dataset(output)
    if original is None:
        if actual is not None:
            raise ValueError('Output annotations have no source')
        return {'status': 'not_present'}
    if actual is None:
        raise ValueError('Output dual-granularity annotations are missing')
    if not speedup:
        if (Path(source)/'meta'/FILENAME).read_bytes() != (Path(output)/'meta'/FILENAME).read_bytes():
            raise ValueError('Normal annotations changed')
    else:
        for old, new in zip(original, actual):
            mapping = json.loads((Path(output)/f"processing/episode_{old['episode_index']:06d}.json").read_text())
            obs = np.asarray(mapping['source_index'])
            if remap(old, obs) != new:
                raise ValueError('Speedup annotations differ from source-frame mapping')
            if mapping['annotation_alignment'] != alignment(old, obs, digest(source)):
                raise ValueError('Annotation alignment provenance mismatch')
    return {'status': 'passed', 'episodes': len(actual),
            'fine_steps': sum(len(r['fine_steps']) for r in actual),
            'coarse_steps': sum(len(r['coarse_steps']) for r in actual),
            'all_frame_labels_and_segments_checked': True}
