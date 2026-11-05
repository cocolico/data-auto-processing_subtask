"""Isolated one-video trial; no workflow startup or dataset delivery."""
from pathlib import Path
import json, shutil, hashlib, tempfile
from datetime import datetime
from convest.annotation.credentials import load
from convest.annotation.provider import ArkProvider, redact
from convest.annotation.native import path_for, validate_ending_motion, validate_action_motion
from convest.training.adatempo.lerobot_video import inspect_video
from convest.config import WORKSPACE
root=Path('/run/user/1000/gvfs/sftp:host=192.168.10.202/data/data-demo/real/tomato/data/lerobot/wuji/full-block-more/A/1031_28_137_data_lerobot')
base=WORKSPACE/'outputs/annotation-smoke';base.mkdir(parents=True,exist_ok=True)
run=Path(tempfile.mkdtemp(prefix=datetime.now().strftime('%Y%m%d-%H%M%S-new-ep0-'),dir=base))
(base/'latest-trial.txt').write_text(str(run))
print('RESULT_DIRECTORY',run,flush=True)
info=json.loads((root/'meta/info.json').read_text())
with (root/'meta/episodes.jsonl').open() as f:ep=json.loads(next(f))
assert ep['episode_index']==0
relative=path_for(info,0,video_key='observation.images.cam0');video=run/'episode_000000_cam0.mp4'
shutil.copyfile(root/relative,video)
inspect_video(video,ep['length'],info['fps'],info['features']['observation.images.cam0']['shape'])
job={'video':str(video),'length':ep['length'],'fps':info['fps'],'task':ep['tasks'],'robot':'wuji','source':str(root),'source_ep':0,'settings':{'model':'doubao-seed-2-0-lite-260428','fps':5.0,'retries':0}}
(run/'input.json').write_text(json.dumps({'job':job,'remote_video':relative,'sha256':hashlib.sha256(video.read_bytes()).hexdigest(),'source_package':json.loads((WORKSPACE/'LOCAL_PACKAGE_SOURCE.json').read_text())},indent=2))
print('PREFLIGHT_OK',video.stat().st_size,flush=True)
key=load(WORKSPACE/'outputs/gui');assert key
provider=ArkProvider()
try:
 labels,usage=provider.annotate(job,key,run/'request',lambda **s:print(json.dumps(s),flush=True))
 result={'episode':0,'dataset_fps':info['fps'],'duration_s':ep['length']/info['fps'],'steps':[{**s,'start_sec':s['start_frame']/info['fps'],'end_sec':s['end_frame']/info['fps']} for s in labels],'usage':usage}
 (run/'labels.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
 issues=[]; checks={}
 for name,fn in [('ending_motion',validate_ending_motion),('action_motion',validate_action_motion)]:
  try:fn(root,0,labels);checks[name]={'status':'passed'}
  except Exception as e:checks[name]={'status':'failed','message':redact(e,key)}
 report={'status':'checks_failed' if any(c['status']=='failed' for c in checks.values()) else 'checks_passed','issues':issues,'motion_checks':checks,'delivery':False}
 (run/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
 print('RESULT',json.dumps({'steps':len(labels),'validation':report,'usage':usage},ensure_ascii=False),flush=True)
except Exception as e:
 error={'type':type(e).__name__,'kind':getattr(e,'kind',None),'message':redact(e,key)}
 (run/'error.json').write_text(json.dumps(error,ensure_ascii=False,indent=2)+'\n')
 print('FAILED',json.dumps(error,ensure_ascii=False),flush=True)
finally:
 provider.cleanup(key,run/'request')
 print('DONE',run,flush=True)
