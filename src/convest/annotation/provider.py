"""Whole-video Ark adapter, with durable request/response checkpoints."""
import json
import math
from pathlib import Path
import re
import time

from .native import atomic_json
from .events import EVENT_FIELDS, validate_events

from .prompts import TOMATO_WORKFLOW_PROMPT as PROMPT
from . import workflow



class Failure(Exception):
    def __init__(self, kind, message, retry_after=None):
        super().__init__(message); self.kind = kind; self.retry_after = retry_after


PHASES = frozenset(('GRASP_CLUSTER', 'PRESENT_CLUSTER', 'HOLD_STABLE',
    'SELECT_TARGET', 'APPROACH_TARGET', 'GRASP_ATTEMPT', 'RETRY_TARGET', 'DETACH_TARGET', 'PLACE_IN_TRAY',
    'ASSESS_REMAINING', 'RETURN_CLUSTER', 'REGRASP_AND_REORIENT',
    'RECOVERY', 'IDLE', 'END'))
DECISIONS = frozenset(('CONTINUE_PICK', 'RETRY_SAME_TARGET', 'REGRASP', 'STOP', 'UNKNOWN'))
DECISION_PHASES = frozenset(('SELECT_TARGET', 'APPROACH_TARGET', 'RETRY_TARGET', 'ASSESS_REMAINING',
    'RETURN_CLUSTER', 'REGRASP_AND_REORIENT', 'END'))
STEP_FIELDS = frozenset(('start_sec', 'end_sec', 'text', 'evidence_status'))
STRUCTURED_FIELDS = frozenset(('phase', 'target_id', 'left_action', 'right_action', 'decision'))
DECISION_FIELDS = frozenset(('choice', 'target_id', 'picked_ids', 'remaining_ids',
    'reachable_ids', 'blocked_ids', 'blocked_reasons', 'right_grasp', 'reason',
    'intended_effect'))


def _tomato_id(value):
    return isinstance(value, str) and re.fullmatch(r'T[1-9][0-9]*', value) is not None


def _optional_text(value):
    return value is None or isinstance(value, str) and bool(value.strip())


def _id_list(value):
    return value is None or (isinstance(value, list) and all(_tomato_id(x) for x in value)
                             and len(value) == len(set(value)))


def _validate_decision(value):
    if value is None:
        return
    if not isinstance(value, dict) or set(value) != DECISION_FIELDS:
        raise ValueError('决策字段无效')
    if value['choice'] not in DECISIONS or not (value['target_id'] is None or _tomato_id(value['target_id'])):
        raise ValueError('决策类型或目标无效')
    for name in ('picked_ids', 'remaining_ids', 'reachable_ids', 'blocked_ids'):
        if not _id_list(value[name]):
            raise ValueError('决策记忆中的番茄 ID 无效')
    if any(not _optional_text(value[k]) for k in ('right_grasp', 'reason', 'intended_effect')):
        raise ValueError('决策说明无效')
    reasons = value['blocked_reasons']
    if reasons is not None and (not isinstance(reasons, dict) or
        any(not _tomato_id(k) or not isinstance(v, str) or not v.strip() for k, v in reasons.items())):
        raise ValueError('遮挡原因无效')
    picked, remaining = value['picked_ids'], value['remaining_ids']
    if picked is not None and remaining is not None and set(picked) & set(remaining):
        raise ValueError('已摘和剩余番茄不能重叠')
    if remaining is not None:
        for name in ('reachable_ids', 'blocked_ids'):
            if value[name] is not None and not set(value[name]) <= set(remaining):
                raise ValueError('可达或受阻目标必须属于剩余番茄')
    if reasons is not None and value['blocked_ids'] is not None and not set(reasons) <= set(value['blocked_ids']):
        raise ValueError('遮挡原因目标不在受阻列表')
    if value['target_id'] is not None and remaining is not None and value['target_id'] not in remaining:
        raise ValueError('决策目标不在剩余番茄中')


def normalize(raw, length, fps, *, require_structured=False, require_events=False,
              require_workflow=False):
    from .native import validate_segments
    text = raw.strip()
    if text.startswith('```') and text.endswith('```'):
        text = '\n'.join(text.splitlines()[1:-1])
    value = json.loads(text)
    if not isinstance(value, dict) or set(value) != {'steps'} or not isinstance(value['steps'], list):
        raise ValueError('标注格式缺少 steps')
    from .compact import MODEL_FIELDS, validate as validate_compact
    if value['steps'] and all(isinstance(row, dict) and set(row) == MODEL_FIELDS for row in value['steps']):
        result = []
        for row in value['steps']:
            a, b = row['start_sec'], row['end_sec']
            if any(type(x) not in (int, float) or not math.isfinite(x) for x in (a, b)) or not 0 <= a < b:
                raise ValueError('步骤时间无效')
            segment = {k: v for k, v in row.items() if k not in ('start_sec', 'end_sec')}
            segment.update(start_frame=round(a*fps), end_frame=round(b*fps))
            result.append(segment)
        validate_segments(result, length)
        validate_compact(result)
        for row in result:
            row['text'] = row['text'].strip()
        return result
    result = []
    structured = None
    grouped = None
    for row in value['steps']:
        # Read old model replies without making their former queue policy active.
        if isinstance(row, dict) and 'review_reason' in row and 'evidence_note' not in row:
            row = dict(row)
            row['evidence_note'] = row.pop('review_reason')
        if not isinstance(row, dict) or set(row) not in (STEP_FIELDS, STEP_FIELDS | STRUCTURED_FIELDS,
                STEP_FIELDS | STRUCTURED_FIELDS | EVENT_FIELDS,
                STEP_FIELDS | STRUCTURED_FIELDS | EVENT_FIELDS | workflow.WORKFLOW_FIELDS):
            raise ValueError('步骤字段无效')
        a,b = row['start_sec'],row['end_sec']
        if any(type(x) not in (int,float) or not math.isfinite(x) for x in (a,b)) or not 0 <= a < b:
            raise ValueError('步骤时间无效')
        has_structure = 'phase' in row
        if structured is not None and structured != has_structure:
            raise ValueError('同一轨迹不能混用结构化与旧版步骤')
        structured = has_structure
        has_workflow = workflow.WORKFLOW_FIELDS <= row.keys()
        if grouped is not None and grouped != has_workflow:
            raise ValueError('同一轨迹不能混用七动作与旧版协议')
        grouped = has_workflow
        segment={'start_frame':round(a*fps), 'end_frame':round(b*fps),
                 'text':row['text'], 'evidence_status':row['evidence_status']}
        if 'phase' in row:
            phases = workflow.PHASES if has_workflow else PHASES
            if row['phase'] not in phases or not (row['target_id'] is None or _tomato_id(row['target_id'])):
                raise ValueError('阶段或番茄目标无效')
            if not _optional_text(row['left_action']) or not _optional_text(row['right_action']):
                raise ValueError('双臂动作无效')
            _validate_decision(row['decision'])
            decision_phases = workflow.DECISION_PHASES if has_workflow else DECISION_PHASES
            if row['decision'] is not None and row['phase'] not in decision_phases:
                raise ValueError(f'第{len(result)+1}段({row["phase"]})：决策必须放在选择、接近、检查、放回、换抓或结束阶段的起点')
            if row['decision'] is not None and row['target_id'] is not None and row['decision']['target_id'] not in (None,row['target_id']):
                raise ValueError('阶段与决策目标不一致')
            segment.update({k:row[k] for k in STRUCTURED_FIELDS})
            if EVENT_FIELDS <= row.keys():
                segment.update({k:row[k] for k in EVENT_FIELDS})
            if has_workflow:
                segment.update({k:row[k] for k in workflow.WORKFLOW_FIELDS})
        result.append(segment)
    if require_structured and not structured:
        raise ValueError('模型结果缺少番茄采摘阶段及决策字段')
    if require_workflow and not grouped:
        raise ValueError('模型结果缺少七动作协议的证据说明与放回条件字段')
    validate_segments(result,length)
    if grouped:
        workflow.validate_workflow(result)
    elif require_events or any('attempt_id' in row for row in result):
        validate_events(result)
    for row in result:
        row['text']=row['text'].strip()
    # Identical labels can denote separate completed operations or retries.
    # Preserve model-provided event boundaries rather than deduplicating by text.
    return result


def redact(message, key=None):
    value = str(message)
    if key: value = value.replace(key, '[REDACTED]')
    return re.sub(r'(?:ark-[\w-]{20,}|sk-[\w-]{20,}|Bearer\s+[^\s\"\']+)', '[REDACTED]', value)[:2000]


def classify(exc, key=None, sent=False):
    if isinstance(exc, Failure): return exc
    status = getattr(exc, 'status_code', None)
    text = redact(exc,key)
    lower = text.lower()
    if status in (401,403): return Failure('credentials',text)
    if status == 402 or any(s in lower for s in ('insufficient_quota','insufficient balance','arrear','quota exhausted')):
        return Failure('quota',text)
    if status == 429:
        response = getattr(exc,'response',None)
        try: delay = float(response.headers.get('retry-after','30'))
        except (ValueError,AttributeError): delay = 30
        return Failure('network',text,max(1,min(delay,3600)))
    if status == 413 or any(s in lower for s in ('context length','context_length','maximum context','video too large')):
        return Failure('unsupported',text)
    if status is not None and status >= 500: return Failure('network',text)
    if 'timeout' in type(exc).__name__.lower() or 'timed out' in lower:
        return Failure('unknown' if sent else 'network',text)
    if status is None and any(s in type(exc).__name__.lower() for s in ('connection','connect','network','oserror')):
        return Failure('unknown' if sent else 'network',text)
    return Failure('result',text)


class ArkProvider:
    def annotate(self, job, key, work, progress=lambda **kw:None):
        work=Path(work);work.mkdir(parents=True,exist_ok=True)
        response_file=work/'response.json'
        if response_file.exists():
            response=json.loads(response_file.read_text())
            return normalize(response['text'],job['length'],job['fps'],require_structured=True,require_events=True,require_workflow=True),response.get('usage',{})
        # Previously returned raw replies can be revalidated without another bill.
        try:
            from .client import create_client
            client=create_client(key,timeout=420)
        except ImportError:raise Failure('credentials','当前运行环境缺少 annotation 依赖，请安装后重新开始任务')
        video=Path(job['video'])
        if video.stat().st_size > 512*1024*1024:
            client.close()
            raise Failure('unsupported','视频超过当前 Files API 本地上传限制，未自动切段')
        sent=False;file_id=None
        request_file=work/'request.json'
        request=json.loads(request_file.read_text()) if request_file.exists() else {}
        try:
            # Recovery is conditional: query only a provider-issued response ID.
            if request.get('response_id'):
                progress(phase='recovering_response')
                response=client.responses.retrieve(request['response_id'])
            else:
                atomic_json(request_file,{'phase':'uploading','started_ns':time.time_ns()})
                progress(phase='uploading_video')
                with video.open('rb') as stream:
                    file=client.files.create(file=stream,purpose='user_data',extra_body={
                        'preprocess_configs':{'video':{'fps':job['settings']['fps']}}})
                file_id=file.id
                if not file_id:raise Failure('network','Files API 未返回文件 ID')
                atomic_json(work/'remote_file.json',{'file_id':file_id,'cleanup':'pending'})
                deadline=time.monotonic()+420
                while True:
                    file=client.files.retrieve(file_id)
                    status=str(getattr(file,'status','')).lower()
                    if status in ('error','failed','expired','cancelled','canceled'):
                        raise Failure('unsupported','视频预处理失败：'+str(getattr(file,'status','')))
                    if status not in ('processing','pending'):break
                    if time.monotonic()>deadline: raise Failure('network','视频预处理等待超时')
                    time.sleep(1)
                request={'phase':'sent','file_id':file_id,'started_ns':time.time_ns()}
                atomic_json(request_file,request)
                progress(phase='waiting_model')
                sent=True
                response=client.responses.create(model=job['settings']['model'],
                    instructions=PROMPT.format(hand='Wuji' if job['robot']=='wuji' else 'Sharpa'),
                    input=[{'role':'user','content':[
                        {'type':'input_text','text':f"Duration={job['length']/job['fps']:.9f} seconds. Task context: {json.dumps(job['task'])}"+('\nPrevious validation error: '+job['retry_feedback'] if job.get('retry_feedback') else '')},
                        {'type':'input_video','file_id':file_id}]}],
                    max_output_tokens=24000,reasoning={'effort':'medium'},extra_body={'thinking':{'type':'enabled'}})
            request.update(phase='returned',response_id=getattr(response,'id',None))
            atomic_json(request_file,request)
            text=getattr(response,'output_text',None)
            if not text:
                text=''.join(getattr(c,'text','') for item in getattr(response,'output',[]) for c in getattr(item,'content',[]))
            usage=getattr(response,'usage',None)
            usage=usage.model_dump() if hasattr(usage,'model_dump') else {}
            # Save raw response durably BEFORE schema validation.
            atomic_json(response_file,{'text':text,'usage':usage,'response_id':getattr(response,'id',None)})
            return normalize(text,job['length'],job['fps'],require_structured=True,require_events=True,require_workflow=True),usage
        except Exception as exc:
            raise classify(exc,key,sent) from exc
        finally:
            if file_id:
                try:
                    client.with_options(timeout=15).files.delete(file_id)
                    atomic_json(work/'remote_file.json',{'file_id':file_id,'cleanup':'completed'})
                except Exception:
                    pass  # Durable pending registry is retried by worker cleanup.
            client.close()

    def cleanup(self, key, work):
        from .client import create_client
        for p in Path(work).rglob('remote_file.json'):
            record=json.loads(p.read_text())
            if record.get('cleanup')!='pending':continue
            try:
                with create_client(key,timeout=15) as client:
                    client.files.delete(record['file_id'])
                atomic_json(p,{**record,'cleanup':'completed'})
            except Exception: break
