"""Production scheme B: one shared timeline, exactly seven ordered stages."""
from .stages import CATEGORIES
PROTOCOL_ID = 'tomato-seven-stages-multiview-v1'
TOMATO_WORKFLOW_PROMPT = '''Annotate ONE dual-arm Franka robot with {hand} dexterous hands.
The THREE videos show the SAME synchronized episode, NOT consecutive clips.
cam0: external overview, image-left is robot LEFT, image-right robot RIGHT.
cam1: RIGHT wrist, cam2: LEFT wrist. Track hand identity continuously; wrist
image left/right is not robot left/right. Compare identical timestamps across views.
All views have the same clip-local zero, dataset FPS and frame count.
Use cam0 for global movements, plate and basket; wrists for contact, release,
fruit picking and repositioning. Camera motion alone is not object motion.

Return ONE timeline with EXACTLY seven stages in the specified order, once each.
Do not split individual picks or produce coarse/fine tracks. Stages 2 and 6
include all repeated fruit picks and deposits in that picking round. Stage 1
includes initial approach and grasp; stage 7 includes final release and settling.
There is no END or idle stage. Boundaries must follow visible actions, never
fixed durations or equal spacing. Repositioning need not include rotation.
Do not invent actions to make the template fit. If a required stage is missing
or cannot be identified, return {"steps":[]} so the episode requires manual review.
Only use the canonical phase, zero-based stage_index and exact English text:
'''.replace('{"steps":[]}', '{{"steps":[]}}')
TOMATO_WORKFLOW_PROMPT += '\n'.join(
    f'{i+1}. stage_index={i}; phase={phase}; text="{text}"'
    for i,(phase,_,text) in enumerate(CATEGORIES))
TOMATO_WORKFLOW_PROMPT += '''
Return JSON only: {{"steps":[...]}}. Each step has exactly:
stage_index, phase, text, start_sec, end_sec.
Intervals are clip-local [start_sec,end_sec), consecutive and non-overlapping,
from 0 to the full duration. Each must contain at least one dataset frame.
Do not infer successful detachment from closure or occlusion alone. Keep
picking-round labels factual; do not add target IDs, decisions or fruit counts.
'''
