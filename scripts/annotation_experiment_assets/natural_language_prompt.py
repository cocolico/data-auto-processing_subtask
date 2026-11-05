"""Test-only label templates. Does not modify production annotation code."""
from pathlib import Path
import json
LABELS=json.loads(Path(__file__).with_name('natural-language-labels.json').read_text())

def rewrite_labels(text):
    # Handle the wrapped legacy quote inside the test rotation rule as well.
    text=text.replace('right robot hand: move and rotate the tomato stem with\nits fingers.', LABELS['REORIENT_STEM']['en'])
    for item in LABELS.values():
        text=text.replace(item['legacy'], item['en'])
    return text

def build_prompt(coverage, semantic_prompt, rotation_rule, footer):
    contract='''\nTEST-ONLY LANGUAGE STYLE: CANONICAL SUBTASK INSTRUCTIONS
The text field is a concise actionable instruction, not a retrospective caption.
Use exactly the fixed English sentence for its phase below. Do not paraphrase,
use "left hand: ...; right hand: ...", or write "the hand is doing ...".
For the two cooperative picking phases, use one coherent sentence with "while"
to express the simultaneous left-hand operation and right-hand support.
Do not invent a task for an inactive hand to make the sentence symmetric.
The instruction specifies the subtask being attempted; it does NOT assert that
it succeeded. Do not add result claims, IDs or duration explanations to text.
This changes label wording only: retain phase definitions, temporal boundaries,
full-frame coverage, the nine-second heuristic, and every required output field.
The text remains English for training; Chinese equivalents are for local display.
CANONICAL TEXT BY PHASE:\n'''
    contract+='\n'.join(f'{phase}: "{item["en"]}"' for phase,item in LABELS.items())
    from convest.annotation.rotation_rules import NINE_SECOND_RULE, ROTATION_TAIL_RULE
    if NINE_SECOND_RULE not in semantic_prompt:
        semantic_prompt += '\n' + NINE_SECOND_RULE + '\n' + ROTATION_TAIL_RULE
    return coverage+rewrite_labels(semantic_prompt)+'\n'+contract+'\n'+footer
