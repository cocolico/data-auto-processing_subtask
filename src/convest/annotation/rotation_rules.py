"""Operator-defined production rotation rules, applied inside one model response."""

NINE_SECOND_RULE = '''PRODUCTION RULE: NINE-SECOND RULE FOR RIGHT-HAND TOMATO ADJUSTMENT
Apply this rule ONLY when a candidate segment would otherwise be GRASP_CLUSTER
("Grasp the tomato truss securely with the right hand."). If end_sec - start_sec >= 9.0 seconds,
prioritize REORIENT_STEM ("Adjust the tomato truss using the right hand.") as the candidate interpretation. Exactly 9.0 seconds qualifies.
This is an operator-supplied heuristic for this workstation: an unusually long
apparent whole-truss grasp may actually contain tomato repositioning and finger-driven
reorientation. Re-examine the full segment and its neighboring frames for those
motions; do not dismiss them as routine grasp alignment solely because the hand
continues to hold the truss.
When adjustment is supported, use REORIENT_STEM. If grasp and adjustment are
separately visible, split at their shared boundary. Do not change boundaries
merely to reach nine seconds. Duration alone does not prove adjustment; re-examine
ambiguous segments, and retain the visible action if there is clear contrary evidence.
Keep target_id=null and return_condition=null for REORIENT_STEM.
Only output the compact seven-field schema; do not add evidence fields.
'''
