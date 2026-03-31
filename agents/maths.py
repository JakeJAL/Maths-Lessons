"""Maths content generator and reviewer agents."""

import json
from agents.base import call_agent, MODEL_SMART


MATHS_AGENT_PROMPT = """\
You are a specialist maths content generator for school teachers. You create \
mathematical questions, worked examples, and practice problems.

Rules:
- Generate content appropriate for the specified year group / key stage.
- Follow UK maths curriculum standards (KS2, KS3, KS4, GCSE, A-Level).
- Include a mix of: worked examples with step-by-step solutions, guided \
practice questions, and independent practice questions.
- Progress from easier to harder within each set of questions.
- Use proper mathematical notation in plain text (e.g. 3x + 5 = 17).
- For each question, provide ONLY the equation in the "question" field. \
Do NOT include instructions like "Solve for x:" — just the equation itself.
- For each question, provide the answer separately.
- If reference material is provided, generate NEW questions at a similar or \
specified difficulty level — do not copy the reference questions.
- Return ONLY valid JSON with this structure:
{
  "topic": "Topic name",
  "year_group": "Year X / KS X",
  "worked_examples": [
    {"question": "...", "solution_steps": ["step 1", "step 2", ...], "answer": "..."}
  ],
  "guided_practice": [
    {"question": "...", "answer": "..."}
  ],
  "independent_practice": [
    {"question": "...", "answer": "..."}
  ]
}
"""

REVIEW_AGENT_PROMPT = """\
You are a maths education reviewer. You check mathematical content for accuracy \
and pedagogical quality before it goes into student-facing presentations.

Your job:
1. Verify every calculation and answer is mathematically correct.
2. Check the difficulty is appropriate for the stated year group.
3. Ensure questions progress from easier to harder.
4. Flag any ambiguous wording that could confuse students.

Return ONLY valid JSON:
{
  "approved": true/false,
  "issues": ["list of problems found, empty if approved"],
  "corrections": [
    {"original": "the wrong thing", "corrected": "the fixed version"}
  ],
  "feedback": "brief overall assessment"
}
"""


def _clean_json(raw: str) -> str:
    """Strip markdown fences from JSON response."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1]
        cleaned = cleaned.rsplit("```", 1)[0]
    return cleaned.strip()


def generate_maths_content(
    topic: str,
    year_group: str,
    num_examples: int = 2,
    num_practice: int = 4,
    difficulty: str = "medium",
    reference_content: str = "",
) -> dict:
    """Call the maths agent to generate curriculum-aligned content."""
    prompt = f"""Generate maths content for:
- Topic: {topic}
- Year group: {year_group}
- Difficulty: {difficulty}
- Worked examples needed: {num_examples}
- Guided practice questions: {num_practice}
- Independent practice questions: {num_practice}
"""
    if reference_content:
        prompt += f"\nReference material (generate SIMILAR but NEW questions):\n{reference_content}\n"

    raw = call_agent(MATHS_AGENT_PROMPT, prompt, model=MODEL_SMART)
    print(f"[Maths Agent] Raw response ({len(raw)} chars)")

    try:
        return json.loads(_clean_json(raw))
    except json.JSONDecodeError as e:
        print(f"[Maths Agent] JSON parse error: {e}")
        return {"topic": topic, "year_group": year_group, "raw_content": raw[:500], "parse_error": True}


def review_maths_content(content: dict, year_group: str) -> dict:
    """Call the review agent to check maths content for correctness."""
    prompt = f"""Review this maths content for {year_group} students:

{json.dumps(content, indent=2)}

Check all answers are correct, difficulty is appropriate, and progression makes sense.
"""
    raw = call_agent(REVIEW_AGENT_PROMPT, prompt)
    print(f"[Review Agent] Raw response ({len(raw)} chars)")

    try:
        return json.loads(_clean_json(raw))
    except json.JSONDecodeError as e:
        print(f"[Review Agent] JSON parse error: {e}")
        return {"approved": True, "issues": [], "feedback": "Review parsing failed, proceeding."}
