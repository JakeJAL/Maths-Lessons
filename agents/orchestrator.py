"""AI agent — tool definitions, system prompt, and the reasoning loop."""

import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "google/gemini-2.0-flash-001"
MAX_AGENT_TURNS = 20

# ---------------------------------------------------------------------------
# Tool schemas (OpenAI function-calling format)
# ---------------------------------------------------------------------------

def _tool(name: str, description: str, properties: dict, required: list) -> dict:
    """Helper to build a tool definition with less boilerplate."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


_SLIDE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Slide heading."},
        "body":  {"type": "string", "description": "Slide body text."},
        "text_replacements": {
            "type": "array",
            "description": "Per-shape replacements from build_slide_replacements. Pass through as-is.",
            "items": {"type": "object"},
        },
        "speaker_notes": {"type": "string", "description": "Speaker notes for the teacher."},
    },
    "required": [],
}

TOOLS = [
    _tool("create_presentation",
          "Create a brand-new Google Slides presentation from scratch.",
          {"title": {"type": "string", "description": "Presentation title."},
           "slides": {"type": "array", "description": "Ordered slides.", "items": _SLIDE_SCHEMA}},
          ["title", "slides"]),

    _tool("add_slides",
          "Add one or more new slides to the current presentation.",
          {"slides": {"type": "array", "items": {
              "type": "object",
              "properties": {
                  "index": {"type": "integer", "description": "0-based insert position. Omit to append."},
                  "title": {"type": "string"},
                  "body":  {"type": "string"},
              },
              "required": ["title", "body"],
          }}},
          ["slides"]),

    _tool("update_slides",
          "Update the content of one or more existing slides. "
          "Use text_replacements for role-based edits (e.g. change just the equation). "
          "Use title/body for simple text replacement.",
          {"updates": {"type": "array", "items": {
              "type": "object",
              "properties": {
                  "index": {"type": "integer", "description": "0-based slide index."},
                  "title": {"type": "string", "description": "New title (omit to keep current)."},
                  "body":  {"type": "string", "description": "New body (omit to keep current)."},
                  "text_replacements": {
                      "type": "array",
                      "description": "Role-based replacements. Each targets a shape by role.",
                      "items": {"type": "object"},
                  },
              },
              "required": ["index"],
          }}},
          ["updates"]),

    _tool("delete_slides",
          "Delete one or more slides by index. Cannot delete the last remaining slide.",
          {"indices": {"type": "array", "items": {"type": "integer"},
                       "description": "0-based indices to delete."}},
          ["indices"]),

    _tool("rename_presentation",
          "Change the presentation title.",
          {"title": {"type": "string", "description": "New title."}},
          ["title"]),

    _tool("get_current_slides",
          "Retrieve the current slide outline (titles and bodies).",
          {}, []),

    _tool("set_slide_background",
          "Set a slide's background to an image from a public URL (stretches to fill).",
          {"slide_index": {"type": "integer", "description": "0-based slide index."},
           "image_url":   {"type": "string",  "description": "Public image URL."}},
          ["slide_index", "image_url"]),

    _tool("insert_image",
          "Insert an image onto a slide at a given position and size.",
          {"slide_index": {"type": "integer", "description": "0-based slide index."},
           "image_url":   {"type": "string",  "description": "Public image URL."},
           "x":      {"type": "number", "description": "X position in PT (default 200)."},
           "y":      {"type": "number", "description": "Y position in PT (default 150)."},
           "width":  {"type": "number", "description": "Width in PT (default 300)."},
           "height": {"type": "number", "description": "Height in PT (default 200)."}},
          ["slide_index", "image_url"]),

    _tool("insert_equation",
          "Render a LaTeX equation as an image and insert it onto a slide. "
          "Use this for all mathematical equations — they will look professionally typeset. "
          "Use standard LaTeX math notation (e.g. '3x + 5 = 17', '\\frac{2}{3}', 'x^2 + 3x - 7 = 0').",
          {"slide_index": {"type": "integer", "description": "0-based slide index."},
           "latex":       {"type": "string",  "description": "LaTeX math expression (e.g. '3x + 5 = 17')."},
           "x":      {"type": "number", "description": "X position in PT (default 200)."},
           "y":      {"type": "number", "description": "Y position in PT (default 150)."},
           "width":  {"type": "number", "description": "Width in PT (default 300)."},
           "height": {"type": "number", "description": "Height in PT (default 80)."},
           "font_size": {"type": "integer", "description": "Font size for rendering (default 20)."},
           "color":  {"type": "string", "description": "Hex color without # (default '000000' for black)."}},
          ["slide_index", "latex"]),

    _tool("search_images",
          "Search for real photos using Unsplash. ALWAYS call this before inserting images "
          "or setting backgrounds — never guess image URLs. Returns a list of usable URLs.",
          {"query": {"type": "string", "description": "Search term (e.g. 'gorilla', 'sunset ocean')."},
           "count": {"type": "integer", "description": "Number of results (1-10, default 5)."}},
          ["query"]),

    _tool("generate_maths_content",
          "Generate curriculum-aligned maths questions, worked examples, and practice problems. "
          "ALWAYS use this when creating maths lesson slides — it produces pedagogically sound, "
          "correctly answered content for the right year group. Returns structured content you "
          "should then format into slides.",
          {"topic":      {"type": "string", "description": "Maths topic (e.g. 'two-step equations', 'fractions')."},
           "year_group": {"type": "string", "description": "Year group or key stage (e.g. 'Year 8', 'KS3', 'GCSE')."},
           "num_examples": {"type": "integer", "description": "Number of worked examples (default 2)."},
           "num_practice": {"type": "integer", "description": "Number of practice questions per section (default 4)."},
           "difficulty":   {"type": "string", "description": "easy, medium, or hard (default medium)."},
           "reference_content": {"type": "string", "description": "Optional reference material to base difficulty on."}},
          ["topic", "year_group"]),

    _tool("review_maths_content",
          "Send maths content to a specialist reviewer who checks all answers are correct, "
          "difficulty is appropriate, and pedagogy is sound. Use this AFTER generating maths "
          "content and BEFORE putting it on slides.",
          {"content":    {"type": "object", "description": "The maths content object from generate_maths_content."},
           "year_group": {"type": "string", "description": "Year group for age-appropriateness check."}},
          ["content", "year_group"]),

    _tool("build_slide_replacements",
          "Send maths content to a specialist agent that maps it to the template slide structure. "
          "This agent knows the template's shape roles and produces the exact text_replacements "
          "for each slide. ALWAYS use this after review_maths_content and BEFORE create_presentation "
          "when a template is uploaded. Pass the result directly as the 'slides' parameter to "
          "create_presentation.",
          {"maths_content": {"type": "object", "description": "The maths content object from generate_maths_content (after review)."}},
          ["maths_content"]),

    _tool("search_curriculum_content",
          "Search the curriculum knowledge base for relevant lesson content. "
          "Returns matching lessons with topic, lesson name, and slide content. "
          "Use this BEFORE generate_maths_content to find reference material that "
          "matches the user's requested topic. Pass the results as reference_content "
          "to generate_maths_content for better quality questions.",
          {"query": {"type": "string", "description": "Search query (e.g. 'solving two step equations', 'fractions addition')."},
           "n_results": {"type": "integer", "description": "Number of results (default 5)."}},
          ["query"]),
]

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """\
You are a presentation design agent for maths teachers. You help create and refine \
Google Slides presentations by calling the tools available to you.

TEMPLATE USAGE:
- When a reference PowerPoint is uploaded, a template is created automatically.
- Your slides inherit the formatting from the uploaded file.
- Never delete template slides.

EDITING vs CREATING:
- If a presentation ALREADY EXISTS (shown in context below), use update_slides to modify it. \
NEVER create a new presentation when one already exists.
- update_slides supports text_replacements for role-based edits (e.g. change just the equations).
- Only use create_presentation when starting fresh with no existing presentation.

MATHS LESSON WORKFLOW (new presentation):
1. Call search_curriculum_content to find relevant reference material for the topic.
2. Call generate_maths_content with the topic, year group, and reference content from step 1. \
Request enough practice questions for the template (e.g. num_practice=10+).
3. Call review_maths_content to verify correctness.
4. Call build_slide_replacements with the maths content.
5. Call create_presentation with the title and slides.

EDITING WORKFLOW (existing presentation):
1. If new maths content is needed, call generate_maths_content.
2. Use update_slides with text_replacements to change specific slides. \
Slide indices are 0-based (slide 1 = index 0, slide 10 = index 9). \
Use role-based replacements like: \
{"index": 9, "text_replacements": [{"role": "equation", "new_list": ["eq1", "eq2", ...]}]}
3. Do NOT call create_presentation or build_slide_replacements for edits.

CONTENT GUIDELINES:
- Respond conversationally after tool calls to explain what you did.
- NEVER mention tool or function names in your responses.
- Use the EXACT topic the user specified.
- NEVER make up maths questions yourself — always use generate_maths_content.
"""


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------
def run_agent(
    user_message: str,
    chat_history: list,
    presentation_context: dict,
    execute_tool_fn,
) -> tuple[str, list[dict]]:
    """
    Run the agent reasoning loop.

    Returns (assistant_reply, tool_calls_log).
    """
    messages = [
        {"role": "system", "content": _build_system_message(presentation_context)},
        *chat_history,
        {"role": "user", "content": user_message},
    ]
    tool_log: list[dict] = []

    for turn in range(MAX_AGENT_TURNS):
        resp = _call_openrouter(messages)

        # Handle API errors
        if "choices" not in resp:
            error_msg = resp.get("error", {}).get("message", str(resp))
            print(f"[Agent] Turn {turn}: API error: {error_msg}")
            return f"Sorry, there was an API error: {error_msg}", tool_log

        msg = resp["choices"][0]["message"]

        if not msg.get("tool_calls"):
            content = msg.get("content") or ""
            # If we got a reply but haven't actually created/modified a presentation yet,
            # and there's no active presentation, nudge the model to follow through
            has_presentation_action = any(
                t["tool"] in ("create_presentation", "add_slides", "update_slides", "build_slide_replacements")
                for t in tool_log
            )
            has_active_presentation = presentation_context.get("id") is not None
            if tool_log and not has_presentation_action and not has_active_presentation:
                print(f"[Agent] Turn {turn}: Reply without creating presentation, nudging...")
                messages.append({"role": "assistant", "content": content})
                messages.append({"role": "user", "content": (
                    "You haven't created the presentation yet. Use the tool results above "
                    "to call build_slide_replacements and then create_presentation. "
                    "Do not just describe what you will do — actually call the tool."
                )})
                continue
            # If we got an empty reply but have pending tool results,
            # nudge the model to continue
            if not content and tool_log:
                print(f"[Agent] Turn {turn}: Empty reply after tool calls, nudging...")
                messages.append({"role": "user", "content": "Continue — use the tool results above to proceed with creating the presentation."})
                continue
            print(f"[Agent] Turn {turn}: Final reply ({len(content)} chars)")
            return content, tool_log

        messages.append(msg)
        for tc in msg["tool_calls"]:
            name = tc["function"]["name"]
            args = json.loads(tc["function"]["arguments"])
            print(f"[Agent] Turn {turn}: Calling {name}")
            result = execute_tool_fn(name, args)
            tool_log.append({"tool": name, "arguments": args, "result": result})
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
            # Log errors from tool results
            try:
                parsed = json.loads(result)
                if "error" in parsed:
                    print(f"[Agent] Tool error: {parsed['error']}")
            except Exception:
                pass

    print(f"[Agent] Hit turn limit ({MAX_AGENT_TURNS})")
    return "I've done several steps — let me know if you'd like me to continue.", tool_log


def _build_system_message(ctx: dict) -> str:
    """Combine the system prompt with current presentation state and uploaded references."""
    msg = SYSTEM_PROMPT
    if ctx.get("id"):
        msg += f'\n\nACTIVE PRESENTATION: "{ctx["title"]}"\n'
        msg += "IMPORTANT: A presentation already exists. Use update_slides to modify it. "
        msg += "Do NOT call create_presentation — that would create a duplicate.\n"
        msg += "Slides:\n"
        for i, s in enumerate(ctx.get("slides", [])):
            body_preview = s.get("body", "")[:80]
            ellipsis = "..." if len(s.get("body", "")) > 80 else ""
            msg += f"  [{i}] {s['title']}: {body_preview}{ellipsis}\n"
    else:
        msg += "\nNo presentation is currently active."

    refs = ctx.get("uploaded_references", [])
    if refs:
        msg += "\n\nThe user has uploaded reference PowerPoint files. "
        msg += "Use this content as source material when creating or updating slides.\n"
        for ref in refs:
            msg += f'\n--- Uploaded file: "{ref["filename"]}" ({ref["slide_count"]} slides) ---\n'
            for s in ref["slides"]:
                # Just show role summary, not full content — slide builder handles the detail
                if s.get("text_elements"):
                    role_counts = {}
                    for te in s["text_elements"]:
                        r = te.get("role", "unknown")
                        role_counts[r] = role_counts.get(r, 0) + 1
                    role_summary = ", ".join(f"{count}x {role}" for role, count in role_counts.items())
                    msg += f'  Slide {s["index"]}: [{role_summary}]\n'
                else:
                    content_preview = s.get("content", "")[:80]
                    msg += f'  Slide {s["index"]}: {content_preview}\n'
            if ref.get("style"):
                msg += f'  Detected style: {json.dumps(ref["style"])}\n'
                msg += "  The visual style from this file will be automatically applied to new presentations.\n"
            if ref.get("template_id"):
                msg += f'  Template ID: {ref["template_id"]} — this file has been converted to a Google Slides template.\n'
                msg += f'  When you create a presentation, it will automatically use this template\'s layout, '
                msg += f'colours, banners, and design. The template has {ref["slide_count"]} slides.\n'
                msg += f'  IMPORTANT: Use text_replacements with role-based targeting. '
                msg += f'Check "Shape roles" for each slide to know how many replacements to send per role. '
                msg += f'Never delete template slides — provide entries for all {ref["slide_count"]} slides.\n'

    return msg


def _call_openrouter(messages: list) -> dict:
    """Send a chat completion request to OpenRouter."""
    resp = requests.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": MODEL,
            "messages": messages,
            "tools": TOOLS,
            "temperature": 0.4,
        },
    )
    if resp.status_code != 200:
        print(f"[Agent] OpenRouter error {resp.status_code}: {resp.text[:300]}")
        return {"error": {"message": f"API returned {resp.status_code}"}}
    return resp.json()
