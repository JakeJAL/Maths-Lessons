"""Slide builder agent — maps maths content to template slide shapes."""

import json
from agents.base import call_agent, MODEL_SMART


SLIDE_BUILDER_PROMPT = """\
You are a slide content mapper. Your job is to take maths content and a template \
slide structure, and produce the exact text_replacements needed for each slide.

Your output must be valid JSON: a list of slide objects, one per template slide.
Each slide object has "text_replacements" — a list of replacements.

CRITICAL RULES:
- Every replacement MUST have a non-empty "new" value or a non-empty "new_list".
- Each replacement targets shapes by "role" (equation, answer, instruction, label, heading, body).
- NEVER target "banner" or "date" shapes.
- For slides with multiple shapes of the same role, use "new_list" with ALL items.
- For slides you don't want to change, use {"text_replacements": []}.

ROLE DEFINITIONS:
- "equation" = a QUESTION to solve (e.g. "2(3x + 1) = 14"). NOT the answer.
- "answer" = the SOLUTION (e.g. "x = 2"). NOT the question.
- If a slide has 10 equation AND 10 answer shapes, send BOTH new_lists.
- NEVER put answers in "equation" role or questions in "answer" role.

CONTENT MAPPING:
- For "equation" shapes: QUESTION only. No prefixes unless template has them.
- For "answer" shapes: ANSWER only.
- For "instruction" shapes: "Solve:" or similar.
- For "label" shapes: "Example 1", etc.
- For "heading" shapes: topic name or section title.
- For "body" shapes: descriptive text.
- Include speaker_notes as a string with solution steps.

Return ONLY valid JSON — no markdown, no explanation.
"""


def _clean_json(raw: str) -> str:
    """Strip markdown fences from JSON response."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1]
        cleaned = cleaned.rsplit("```", 1)[0]
    return cleaned.strip()


def build_slide_replacements(maths_content: dict, template_slides: list) -> list:
    """Map maths content to template shapes using batched agent calls.

    Returns a list of slide dicts with text_replacements for each template slide.
    """
    MAX_SHAPES_PER_BATCH = 25
    batches = []
    current_batch = []
    current_count = 0

    for s in template_slides:
        shape_count = 0
        if s.get("text_elements"):
            for te in s["text_elements"]:
                if te.get("role") not in ("banner", "date", "unknown"):
                    shape_count += 1
        shape_count = max(shape_count, 1)

        if current_batch and current_count + shape_count > MAX_SHAPES_PER_BATCH:
            batches.append(current_batch)
            current_batch = []
            current_count = 0

        current_batch.append(s)
        current_count += shape_count

    if current_batch:
        batches.append(current_batch)

    print(f"[Slide Builder] Split {len(template_slides)} slides into {len(batches)} batches")
    all_results = []

    for batch_idx, batch_slides in enumerate(batches):
        batch_start = batch_slides[0]["index"]
        batch_end = batch_slides[-1]["index"]
        print(f"[Slide Builder] Processing batch {batch_idx+1}/{len(batches)} (slides {batch_start}-{batch_end})...")

        prompt = _build_batch_prompt(maths_content, batch_slides, batch_start, batch_end)
        raw = call_agent(SLIDE_BUILDER_PROMPT, prompt, model=MODEL_SMART)
        print(f"[Slide Builder] Batch {batch_idx}: response ({len(raw)} chars)")

        try:
            batch_result = json.loads(_clean_json(raw))
            if not isinstance(batch_result, list):
                batch_result = [batch_result]
            while len(batch_result) < len(batch_slides):
                batch_result.append({"text_replacements": []})
            all_results.extend(batch_result[:len(batch_slides)])
        except json.JSONDecodeError as e:
            print(f"[Slide Builder] JSON parse error for batch {batch_idx}: {e}")
            for _ in batch_slides:
                all_results.append({"text_replacements": []})

    while len(all_results) < len(template_slides):
        all_results.append({"text_replacements": []})

    return all_results


def _build_batch_prompt(maths_content, batch_slides, batch_start, batch_end):
    """Build the prompt for a single batch of slides."""
    prompt = f"Map this maths content to template slides {batch_start} to {batch_end}.\n\n"
    prompt += f"Topic: {maths_content.get('topic', 'Unknown')}\n"
    prompt += f"Year group: {maths_content.get('year_group', 'Unknown')}\n"

    for section, label in [
        ("worked_examples", "WORKED EXAMPLES (for Example slides)"),
        ("guided_practice", "GUIDED PRACTICE (for Do Now / starter slides)"),
        ("independent_practice", "INDEPENDENT PRACTICE (for Build/Apply slides)"),
    ]:
        items = maths_content.get(section, [])
        if items:
            prompt += f"\n{label}:\n"
            for i, item in enumerate(items):
                prompt += f"  {i+1}) question='{item.get('question', '')}' answer='{item.get('answer', '')}'\n"

    prompt += "\nTEMPLATE SLIDES:\n"
    for s in batch_slides:
        prompt += f"\nSlide {s['index']}:"
        if s.get("text_elements"):
            role_counts = {}
            for te in s["text_elements"]:
                r = te.get("role", "unknown")
                role_counts[r] = role_counts.get(r, 0) + 1
            prompt += f" [{', '.join(f'{c}x {r}' for r, c in role_counts.items())}]"
            for te in s["text_elements"]:
                prompt += f"\n  - {te['role']}: {te['text'][:80]}"
        else:
            prompt += f" {s.get('content', '')[:100]}"

    prompt += f"\n\nReturn JSON array with {len(batch_slides)} objects."
    return prompt
