"""Compact seven-action annotation instructions; coarse stages are derived in code."""
PROTOCOL_ID = 'tomato-seven-actions-compact-v6'
TOMATO_WORKFLOW_PROMPT = '''Annotate ONE dual-arm Franka robot with {hand} dexterous hands.
Use visible video actions, not a guessed plan. cam0 image-left is the robot's
LEFT hand and image-right its RIGHT hand. Track identity across the image center.
Do not infer successful picking from finger closure, occlusion or an empty hand
visiting a tray. Label the action being performed without adding an outcome claim.

Use exactly these seven phases and canonical English text labels:
1. GRASP_CLUSTER
"Grasp the tomato truss securely with the right hand."
Include approach, alignment and closure to establish the whole-truss grasp.
End before lifting. Routine alignment does not include finger-driven stem rotation.
2. PRESENT_CLUSTER
"Lift the tomato truss to the center with the right hand."
From the start of lifting until arrival in the central working position.
3. APPROACH_AND_GRASP
"Approach and grasp one tomato with the left hand while holding the truss steady with the right hand."
From approach to one tomato through alignment and closure, ending before detachment.
A direct return from the preceding deposit towards the next fruit belongs here.
4. DETACH_AND_PLACE
"Detach the tomato and place it in the left tray with the left hand while holding the truss steady with the right hand."
From the FIRST detaching pull/twist through transport and placement in the left
tray. Keep this operation together, but keep separate fruits in separate segments.
5. RETURN_AND_RELEASE
"Return the tomato truss to the right white plate and release it with the right hand."
From movement towards the right plate through lowering and finger release,
ending before movement serving stem rotation. Record return_condition with ONLY
left_side_count: the attached tomato count on the cam0 image-left side of the
RIGHT thumb/index grasp at the START of return. The operator's intended count
is zero, but report the actual nonnegative count or null if obscured. This is
not the total remaining count. A visible return does not prove the count is zero.
6. REORIENT_STEM
"Move and rotate the tomato stem using the fingers of the right hand."
Include movement, contact and finger adjustments serving one continuous stem
rotation. Connected-truss orientation changes can show rotation when the thin
stem is obscured. Wrist turning, translation, fruit swinging or closure alone
are not proof. Examine the interval after return and before lift, but do not
infer rotation solely from this sequence. End when rotation finishes. A separate
subsequent grasp is GRASP_CLUSTER; direct lift can transition to PRESENT_CLUSTER.
7. END
"Keep both hands still in their low resting positions."
Only after BOTH hands are low and stationary through the final interval.
Movement toward rest belongs to the preceding action. Never force END when
motion continues to the last frame. END, if present, must be the final segment.

TIMELINE
Cover every frame exactly once with consecutive non-overlapping intervals.
Include ordinary settling in the action it completes. Keep each individual
fruit's approach and detachment/placement distinct; do not collapse repeated
picks into one fine label. Keep supporting right-hand holding in the same
sentence as the active left-hand operation. Do not invent extra transition,
idle or decision phases. Use only the seven phases, with concise canonical text.
Do not invent hidden events or frame-perfect visual precision from sparse samples.

IDENTITY AND DECISION
Use stable target_id values T1,T2,... for reliably distinguishable fruit across
rotation, or null when unknown. Whole-truss actions and END use target_id=null.
Optional decision is null or an object with exactly choice and target_id.
choice is CONTINUE_PICK, RETRY_SAME_TARGET, REGRASP, STOP or UNKNOWN.
A non-null decision describes the START of APPROACH_AND_GRASP,
RETURN_AND_RELEASE, REORIENT_STEM or END only. STOP is allowed only at END.
The decision target must agree with the action target when both are specified.
Use decision=null when no reliable decision can be inferred from the video.

OUTPUT
Return JSON only with one steps array. Each step has exactly seven keys:
start_sec, end_sec, phase, text, target_id, decision, return_condition.
Intervals are clip-local [start_sec,end_sec), from zero to the end of the clip.
return_condition is null except for RETURN_AND_RELEASE, where it is exactly
{{"left_side_count":0}} with the actual count or null in place of the example zero.
Do not output extra fields, explanations or a second coarse timeline: coarse
labels will be deterministically derived from the fine phases by the program.
Example shape (not a video observation):
{{"steps":[{{"start_sec":0,"end_sec":1,"phase":"GRASP_CLUSTER",
"text":"Grasp the tomato truss securely with the right hand.","target_id":null,
"decision":null,"return_condition":null}}]}}
'''

# Insert before OUTPUT so the canonical JSON example remains the final prompt content.
from .rotation_rules import NINE_SECOND_RULE, ROTATION_TAIL_RULE
TOMATO_WORKFLOW_PROMPT = TOMATO_WORKFLOW_PROMPT.replace(
    '\nOUTPUT\n', '\n' + NINE_SECOND_RULE + '\n' + ROTATION_TAIL_RULE + '\nOUTPUT\n')
