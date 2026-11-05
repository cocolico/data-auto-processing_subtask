"""Read-only inputs; annotate frame tasks and reindex successful episode subsets."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import shutil

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:
    pa = pq = None


def read_json(path):
    return json.loads(Path(path).read_text())


def atomic_json(path, value):
    from convest.ingest.batches import durable_json
    durable_json(Path(path),value)


def rows(path):
    return [json.loads(s) for s in Path(path).read_text().splitlines() if s.strip()]


def jsonl(path, values):
    Path(path).write_text("".join(json.dumps(v, ensure_ascii=False, allow_nan=False) + "\n" for v in values))


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def artifact(root, relative):
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Unsafe dataset path: {relative}")
    path = Path(root) / relative
    if path.resolve() != path.absolute():
        raise ValueError(f"Dataset artifact must not contain symlinks: {relative}")
    return path


def path_for(info, episode, *, video_key=None):
    fields = {"episode_index": episode, "episode_chunk": episode // info["chunks_size"]}
    if video_key is not None:
        fields["video_key"] = video_key
    return info["video_path" if video_key is not None else "data_path"].format(**fields)


def inspect(root):
    if pq is None:raise RuntimeError('请安装 annotation/conversion 可选依赖后启用数据标注')
    root = Path(root).resolve(strict=True)
    info = read_json(root / "meta/info.json")
    if info.get("codebase_version") != "v2.1":
        raise ValueError("This adapter supports LeRobot v2.1 only")
    if type(info.get("chunks_size")) is not int or info["chunks_size"] <= 0:
        raise ValueError("Invalid chunks_size")
    if not isinstance(info.get("fps"), (int, float)) or not math.isfinite(info["fps"]) or info["fps"] <= 0:
        raise ValueError("Invalid FPS")
    episodes = rows(root / "meta/episodes.jsonl")
    tasks = rows(root / "meta/tasks.jsonl")
    stats = rows(root / "meta/episodes_stats.jsonl")
    if not episodes or [ep["episode_index"] for ep in episodes] != list(range(len(episodes))):
        raise ValueError("Episodes must be nonempty, contiguous and ordered from zero")
    if [r["episode_index"] for r in stats] != list(range(len(episodes))):
        raise ValueError("Episode statistics must cover exactly the episodes in order")
    if [t["task_index"] for t in tasks] != list(range(len(tasks))) or any(not isinstance(t.get("task"), str) or not t["task"].strip() for t in tasks):
        raise ValueError("Invalid task vocabulary")
    if info["total_episodes"] != len(episodes) or info["total_frames"] != sum(ep["length"] for ep in episodes) or info["total_tasks"] != len(tasks):
        raise ValueError("Dataset metadata counts disagree")
    cameras = [name for name, value in info["features"].items() if value["dtype"] == "video"]
    if not cameras:
        raise ValueError("Dataset has no video features")
    artifacts = []
    total = 0
    for ep in episodes:
        if type(ep["length"]) is not int or ep["length"] <= 0:
            raise ValueError("Empty/invalid episode")
        relative = path_for(info, ep["episode_index"])
        file = artifact(root, relative)
        with pq.ParquetFile(file) as parquet:
            if parquet.metadata.num_rows != ep["length"]:
                raise ValueError(f"Row count differs: {relative}")
            for name in ("frame_index", "episode_index", "index", "task_index"):
                if not pa.types.is_int64(parquet.schema_arrow.field(name).type):
                    raise ValueError(f"Expected scalar int64 column: {name}")
            expected = set(info["features"]) - set(cameras)
            if set(parquet.schema_arrow.names) != expected:
                raise ValueError(f"Feature/schema mismatch: {relative}")
        for name in [relative] + [path_for(info, ep["episode_index"], video_key=c) for c in cameras]:
            file = artifact(root, name)
            if not file.is_file():
                raise ValueError(f"Missing artifact: {name}")
            stat = file.stat()
            artifacts.append({"path": name, "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns})
            total += stat.st_size
    if info.get("total_videos") != len(episodes) * len(cameras):
        raise ValueError("Video count differs")
    metadata_hashes = {name: sha256(root / "meta" / name) for name in
                       ("info.json", "episodes.jsonl", "tasks.jsonl", "episodes_stats.jsonl")}
    from .granularity import FILENAME
    for name in ("annotation_events.jsonl", FILENAME):
        if (root / "meta" / name).is_file():
            metadata_hashes[name] = sha256(root / "meta" / name)
    identity = {"metadata": metadata_hashes, "artifacts": artifacts}
    signature = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    return {"root": root, "info": info, "episodes": episodes, "tasks": tasks,
            "stats": stats, "cameras": cameras, "artifacts": artifacts,
            "bytes": total, "signature": signature, "metadata_hashes": metadata_hashes}


def validate_segments(segments, length):
    if not isinstance(segments, list) or not segments:
        raise ValueError("A complete nonempty step list is required")
    cursor = 0
    for segment in segments:
        start, end = segment.get("start_frame"), segment.get("end_frame")
        if type(start) is not int or type(end) is not int or not (start == cursor < end <= length):
            raise ValueError("Steps must cover every frame exactly once, using half-open intervals")
        if not isinstance(segment.get("text"), str) or not segment["text"].strip():
            raise ValueError("Step text is empty")
        cursor = end
    if cursor != length:
        raise ValueError("Unannotated episode tail")


def validate_ending_motion(source_root, episode_index, segments, *, max_joint_range=0.3):
    """Reject an END label that covers substantial recorded joint movement.

    This is a conservative motion check, not visual proof of semantic completion.
    It reads only the small observation.state Parquet column.
    """
    ending = next((s for s in segments if s.get('phase') == 'END'), None)
    if ending is None:
        return
    root = Path(source_root)
    info = read_json(root / 'meta/info.json')
    feature = info['features'].get('observation.state', {})
    names = feature.get('names') or []
    positions = [i for i, name in enumerate(names) if name.endswith('.position')]
    if not positions or pq is None:
        raise ValueError('END 动作核验需要 observation.state 关节位置列')
    file = artifact(root, path_for(info, episode_index))
    values = pq.read_table(file, columns=['observation.state'])['observation.state'].to_pylist()
    if len(values) < ending['end_frame']:
        raise ValueError('END 时段超出关节状态帧数')
    selected = values[ending['start_frame']:ending['end_frame']]
    if not selected:
        raise ValueError('END 时段为空')
    for i in positions:
        joint = [float(row[i]) for row in selected]
        if not all(math.isfinite(v) for v in joint):
            raise ValueError('END 时段的关节状态不可核验')
        if max(joint) - min(joint) > max_joint_range:
            raise ValueError(f'END 时段仍有明显关节运动：{names[i]}，请继续标注尾段动作')


def validate_action_motion(source_root, episode_index, segments, *, min_place_range=0.3,
                           max_unlabeled_range=0.3):
    """Flag gross contradictions between arm motion and tomato subtask labels.

    Joint excursion is only a plausibility check for this center-truss/left-tray
    setup. It cannot establish contact, detachment, tomato identity or success.
    """
    relevant = [(i, s) for i, s in enumerate(segments) if
                (s.get('phase') in ('PLACE_IN_TRAY', 'DETACH_AND_PLACE') and s.get('outcome') == 'success') or
                (s.get('phase') in ('RETURN_CLUSTER', 'HOLD_STABLE', 'RETURN_AND_RELEASE', 'REORIENT_STEM') and s.get('left_action') is None)]
    if not relevant:
        return
    root = Path(source_root)
    info = read_json(root / 'meta/info.json')
    names = info['features'].get('observation.state', {}).get('names') or []
    left = [i for i, name in enumerate(names) if name.startswith('left_fr3_joint') and name.endswith('.position')]
    if not left or pq is None:
        raise ValueError('动作核验需要左臂 observation.state 关节位置列')
    file = artifact(root, path_for(info, episode_index))
    values = pq.read_table(file, columns=['observation.state'])['observation.state'].to_pylist()
    for index, segment in relevant:
        a, b = segment['start_frame'], segment['end_frame']
        if not 0 <= a < b <= len(values):
            raise ValueError(f'第{index+1}段超出关节状态帧数')
        selected = values[a:b]
        ranges = []
        for joint_index in left:
            joint = [float(row[joint_index]) for row in selected]
            if not all(math.isfinite(v) for v in joint):
                raise ValueError(f'第{index+1}段关节状态不可核验')
            ranges.append(max(joint) - min(joint))
        excursion = max(ranges)
        if segment['phase'] in ('PLACE_IN_TRAY', 'DETACH_AND_PLACE') and excursion < min_place_range:
            raise ValueError(f'第{index+1}段声称送盘成功，但左臂关节位移仅 {excursion:.3f} rad；请核验是否仍在抓取或重试')
        if segment['phase'] in ('RETURN_CLUSTER', 'HOLD_STABLE', 'RETURN_AND_RELEASE', 'REORIENT_STEM') and excursion > max_unlabeled_range:
            raise ValueError(f'第{index+1}段未标左手动作，但左臂关节位移达 {excursion:.3f} rad；请拆分或补充动作')


def select_prefix(source, limit):
    """Read a leading subset directly from source; no input files are copied.

    A prefix preserves episode_index/global index without reindexing sensor rows.
    Output metadata explicitly describes the selected subset, not the full input.
    """
    if limit is None:
        return source
    if type(limit) is not int or not 1 <= limit <= len(source["episodes"]):
        raise ValueError("episode-limit must be between 1 and the source episode count")
    result = dict(source)
    result["original_info"] = source["info"]
    result["episodes"] = source["episodes"][:limit]
    result["stats"] = source["stats"][:limit]
    info = deepcopy(source["info"])
    info.update(total_episodes=limit, total_frames=sum(ep["length"] for ep in result["episodes"]),
                total_videos=limit * len(source["cameras"]),
                total_chunks=(limit + info["chunks_size"] - 1) // info["chunks_size"])
    splits = {}
    for name, span in info["splits"].items():
        a, b = map(int, span.split(":"))
        if min(a, limit) < min(b, limit):
            splits[name] = f"{min(a,limit)}:{min(b,limit)}"
    info["splits"] = splits
    result["info"] = info
    paths = set()
    for ep in result["episodes"]:
        paths.add(path_for(info, ep["episode_index"]))
        paths.update(path_for(info, ep["episode_index"], video_key=c) for c in source["cameras"])
    result["artifacts"] = [row for row in source["artifacts"] if row["path"] in paths]
    result["bytes"] = sum(row["bytes"] for row in result["artifacts"])
    return result


def task_stats(indices):
    n = len(indices)
    mean = sum(indices) / n
    return {"min": [min(indices)], "max": [max(indices)], "mean": [mean],
            "std": [math.sqrt(sum((v - mean) ** 2 for v in indices) / n)], "count": [n]}


def projected_info(source, selected):
    info = deepcopy(source["info"])
    info.update(total_episodes=len(selected), total_frames=sum(ep["length"] for ep in selected),
                total_videos=len(selected)*len(source["cameras"]),
                total_chunks=(len(selected)+info["chunks_size"]-1)//info["chunks_size"])
    mapping={ep["episode_index"]:i for i,ep in enumerate(selected)}
    splits={}
    for name,span in info["splits"].items():
        a,b=map(int,span.split(":"));ids=[mapping[i] for i in mapping if a<=i<b]
        if ids:splits[name]=f"{min(ids)}:{max(ids)+1}"
    info["splits"]=splits
    return info


def annotation_events(selected, labels):
    return [{"episode_index": new_ep,
             "source_episode_index": ep["episode_index"],
             "source_recording_id": ep.get("source_recording_id"),
             "steps": labels[ep["episode_index"]]}
            for new_ep, ep in enumerate(selected)]


def export_dataset(source, destination, labels):
    """Deliver only successful episodes; reindex subset, preserve sensor values."""
    from .compact import project
    labels = {ep: project(steps) for ep, steps in labels.items()}
    selected=[ep for ep in source["episodes"] if ep["episode_index"] in labels]
    if not selected:raise ValueError("No successful episode to deliver")
    for ep in selected:validate_segments(labels[ep["episode_index"]],ep["length"])
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=False)
    (destination/"meta").mkdir()
    info=projected_info(source,selected)
    tasks=[];vocabulary={};episodes_out=[];stats_out=[];global_offset=0
    for new_ep,episode in enumerate(selected):
        old_ep=episode["episode_index"];indices=[];names=[]
        for segment in labels[old_ep]:
            text=segment["text"].strip()
            if text not in vocabulary:
                vocabulary[text]=len(tasks);tasks.append({"task_index":vocabulary[text],"task":text})
            indices.extend([vocabulary[text]]*(segment["end_frame"]-segment["start_frame"]))
            if text not in names:names.append(text)
        output=artifact(destination,path_for(info,new_ep));output.parent.mkdir(parents=True,exist_ok=True)
        with pq.ParquetFile(artifact(source["root"],path_for(source["info"],old_ep))) as parquet:
            with pq.ParquetWriter(output,parquet.schema_arrow,compression="zstd") as writer:
                offset=0
                for batch in parquet.iter_batches(batch_size=8,use_threads=False):
                    table=pa.Table.from_batches([batch])
                    for name,values in [("task_index",indices[offset:offset+len(batch)]),
                        ("episode_index",[new_ep]*len(batch)),("index",list(range(global_offset+offset,global_offset+offset+len(batch))))]:
                        field=table.schema.field(name)
                        table=table.set_column(table.schema.get_field_index(name),field,pa.array(values,type=field.type))
                    writer.write_table(table);offset+=len(batch)
        for camera in source["cameras"]:
            target=artifact(destination,path_for(info,new_ep,video_key=camera));target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(artifact(source["root"],path_for(source["info"],old_ep,video_key=camera)),target)
        row=deepcopy(episode);row.update(episode_index=new_ep,tasks=names);row.pop("task_index",None)
        if len(names)==1:row["task_index"]=vocabulary[names[0]]
        episodes_out.append(row)
        stat=deepcopy(source["stats"][old_ep]);stat["episode_index"]=new_ep
        for key,values in [("task_index",indices),("episode_index",[new_ep]*episode["length"]),
                           ("index",list(range(global_offset,global_offset+episode["length"])) )]:
            stat["stats"][key]=task_stats(values)
        stats_out.append(stat);global_offset+=episode["length"]
    info["total_tasks"]=len(tasks)
    jsonl(destination/"meta/tasks.jsonl",tasks)
    jsonl(destination/"meta/episodes.jsonl",episodes_out)
    jsonl(destination/"meta/episodes_stats.jsonl",stats_out)
    atomic_json(destination/"meta/info.json",info)
    from .granularity import FILENAME, records
    jsonl(destination/"meta"/FILENAME, records(selected, labels, info["fps"]))


def validate_output(source, output, labels):
    delivered=inspect(output)
    from .compact import project
    labels = {ep: project(steps) for ep, steps in labels.items()}
    selected=[ep for ep in source["episodes"] if ep["episode_index"] in labels]
    if (Path(output)/"meta/annotation_events.jsonl").exists():
        raise ValueError("Duplicate legacy annotation event file in compact export")
    from .granularity import FILENAME, records
    dual = Path(output)/"meta"/FILENAME
    if not dual.is_file() or rows(dual) != records(selected, labels, source["info"]["fps"]):
        raise ValueError("Dual-granularity annotation data missing or changed")
    expected=projected_info(source,selected);expected["total_tasks"]=delivered["info"]["total_tasks"]
    if delivered["info"]!=expected:raise ValueError("Unexpected info changes")
    tasks={r["task_index"]:r["task"] for r in delivered["tasks"]};offset=0
    for new_ep,old in enumerate(selected):
        old_ep=old["episode_index"];count=0;actual_indices=[]
        text=[s["text"].strip() for s in labels[old_ep] for _ in range(s["end_frame"]-s["start_frame"])]
        row=delivered["episodes"][new_ep]
        if row["tasks"]!=list(dict.fromkeys(text)):raise ValueError("Episode task list mismatch")
        for k,v in old.items():
            if k not in {"episode_index","task_index","tasks"} and row.get(k)!=v:raise ValueError("Source identity changed")
        with pq.ParquetFile(artifact(source["root"],path_for(source["info"],old_ep))) as a, pq.ParquetFile(artifact(output,path_for(delivered["info"],new_ep))) as b:
            if not a.schema_arrow.equals(b.schema_arrow,check_metadata=True):raise ValueError("Arrow schema changed")
            for before,after in zip(a.iter_batches(batch_size=8,use_threads=False),b.iter_batches(batch_size=8,use_threads=False)):
                for name in before.schema.names:
                    if name not in {"task_index","index","episode_index"} and not before.column(name).equals(after.column(name)):
                        raise ValueError("Sensor data changed: "+name)
                n=len(after)
                if after.column("index").to_pylist()!=list(range(offset+count,offset+count+n)):raise ValueError("Global index mismatch")
                if after.column("episode_index").to_pylist()!=[new_ep]*n:raise ValueError("Episode index mismatch")
                if after.column("frame_index").to_pylist()!=list(range(count,count+n)):raise ValueError("Frame index mismatch")
                ids=after.column("task_index").to_pylist()
                if [tasks.get(i) for i in ids]!=text[count:count+n]:raise ValueError("Task/frame mapping mismatch")
                actual_indices.extend(ids);count+=n
        if count!=old["length"]:raise ValueError("Frame count mismatch")
        expected_stats=deepcopy(source["stats"][old_ep]);expected_stats["episode_index"]=new_ep
        for name,values in [("task_index",actual_indices),("episode_index",[new_ep]*count),("index",list(range(offset,offset+count)))]:
            expected_stats["stats"][name]=task_stats(values)
        if expected_stats!=delivered["stats"][new_ep]:raise ValueError("Statistics mismatch")
        for camera in source["cameras"]:
            if sha256(artifact(source["root"],path_for(source["info"],old_ep,video_key=camera)))!=sha256(artifact(output,path_for(delivered["info"],new_ep,video_key=camera))):
                raise ValueError("Video bytes changed")
        offset+=count
    return {"status":"passed","episodes":len(selected),"frames":offset,"sensor_values_equal":True,"videos_equal":True,
            "source_mapping":[{"episode_index":i,"source_episode_index":ep["episode_index"],"source_recording_id":ep.get("source_recording_id")} for i,ep in enumerate(selected)],
            "files":{str(p.relative_to(output)):sha256(p) for p in Path(output).rglob("*") if p.is_file()}}
