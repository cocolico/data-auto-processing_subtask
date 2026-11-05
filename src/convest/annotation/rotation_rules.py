"""Operator-defined production rotation rules, applied inside one model response."""

NINE_SECOND_RULE = '''PRODUCTION RULE: NINE-SECOND RULE FOR RIGHT-HAND STEM ROTATION
Apply this rule ONLY when a candidate segment would otherwise be GRASP_CLUSTER
("Grasp the tomato truss securely with the right hand."). If end_sec - start_sec >= 9.0 seconds,
prioritize REORIENT_STEM ("Move and rotate the tomato stem using the fingers of the right hand.") as the candidate interpretation. Exactly 9.0 seconds qualifies.
This is an operator-supplied heuristic for this workstation: an unusually long
apparent whole-truss grasp may actually contain stem rotation and finger-driven
reorientation. Re-examine the full segment and its neighboring frames for those
motions; do not dismiss them as routine grasp alignment solely because the hand
continues to hold the truss.
When rotation is supported, use REORIENT_STEM. If grasp and rotation are
separately visible, split at their shared boundary. Do not change boundaries
merely to reach nine seconds. Duration alone does not prove rotation; re-examine
ambiguous segments, and retain the visible action if there is clear contrary evidence.
Keep target_id=null and return_condition=null for REORIENT_STEM.
Only output the compact seven-field schema; do not add evidence fields.
'''

ROTATION_TAIL_RULE = '''PRODUCTION SECOND PASS: RESERVE THE LAST 2.8 SECONDS OF LONG STEM ROTATION FOR GRASP
Perform two internal annotation passes in this ONE response; output only the
final timeline, not two alternatives and not a new API request.

PASS 1: Build the full chronological timeline using the phase definitions,
canonical language templates and nine-second rotation heuristic above.

PASS 2: Take a snapshot of the PASS-1 segments. For EACH REORIENT_STEM segment
whose original duration is at least 9.0 seconds, split it exactly once:
  original [start, end)
  REORIENT_STEM [start, end - 2.8)
  GRASP_CLUSTER [end - 2.8, end)
Exactly 9.0 seconds qualifies. Do not apply this rule to shorter rotations,
other phases, or newly created segments. Do not recursively split the shortened
rotation or re-run the nine-second classifier on the final timeline.
At this dataset's 30 FPS, 2.8 seconds is exactly 84 frames. Compute the split
using the original end frame: split_frame = original_end_frame - 84; convert
back to seconds using the dataset FPS. These are original dataset frames, NOT
the model's 5-FPS video samples. Do not change the dataset or model sampling rate.
Use the canonical REORIENT_STEM sentence for the first part and exactly
"Grasp the tomato truss securely with the right hand." for the last 2.8 seconds.
GRASP_CLUSTER is action category 1; this does NOT move the segment to the start
of the recording. Keep it immediately after the shortened rotation.

This explicit annotation convention overrides the earlier instruction to omit a
separate GRASP_CLUSTER when rotation directly transitions into lifting, and
any earlier preference for a visually chosen rotation/grasp split, ONLY for
these qualifying PASS-1 rotation segments. Keep all other phase rules intact.
Keep the original outer start/end and neighboring segments unchanged. Do not
merge the new 2.8-second grasp with an adjacent grasp: keep the imposed boundary
visible in the output. Preserve complete, gap-free, non-overlapping frame coverage.

This is a fixed-duration labeling convention, not visual proof of an exact
boundary. Both segments retain target_id=null and return_condition=null.
The new GRASP_CLUSTER has decision=null; keep the original rotation decision
only at its unchanged start. Output exactly the compact seven required fields.

FINAL CHECK: every qualifying original rotation yields exactly one shortened
rotation followed by exactly 84 original frames of GRASP_CLUSTER. First frame,
last frame, total duration and the union of covered frames are unchanged.
Example of arithmetic only (not an observation): a PASS-1 rotation [30.0,44.6)
becomes rotation [30.0,41.8) then grasp [41.8,44.6). Do not copy these times into
an unrelated video; compute from each detected segment's own end frame.
'''
