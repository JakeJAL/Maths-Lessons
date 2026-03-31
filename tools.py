"""Tool executor — bridges agent tool calls to the Google Slides API."""

import json
from state import state
from services.slides_api import (
    create_presentation,
    create_from_template,
    modify_presentation,
    get_presentation_slides,
    set_slide_background,
    insert_image,
    insert_equation,
    search_images,
)
from agents.maths import generate_maths_content, review_maths_content
from agents.slide_builder import build_slide_replacements
from services.rag import search_curriculum


def _require_presentation() -> str | None:
    """Return an error JSON string if no presentation is active, else None."""
    if not state["presentation_id"]:
        return json.dumps({"error": "No active presentation. Create one first."})
    return None


def _refresh_slides() -> tuple[str, list]:
    """Re-fetch slides from the API and update state."""
    title, slides = get_presentation_slides(state["presentation_id"])
    state["presentation_title"] = title
    state["current_slides"] = slides
    return title, slides


def execute_tool(tool_name: str, arguments: dict) -> str:
    """Execute a single tool call and return a JSON result string."""

    if tool_name == "create_presentation":
        # Use template if one was uploaded
        template_id = None
        ref_style = None
        if state.get("uploaded_references"):
            for ref in state["uploaded_references"]:
                if ref.get("template_id"):
                    template_id = ref["template_id"]
                if ref.get("style"):
                    ref_style = ref["style"]

        try:
            if template_id:
                print(f"[Tools] Using template {template_id}")
                # Log what the agent is sending for each slide
                for i, slide in enumerate(arguments.get("slides", [])):
                    replacements = slide.get("text_replacements", [])
                    if replacements:
                        print(f"[Tools] Slide {i}: {len(replacements)} replacements")
                        for r in replacements:
                            new_val = r.get('new', '')
                            new_list = r.get('new_list', [])
                            if new_list:
                                print(f"[Tools]   role={r.get('role', '?')}: new_list with {len(new_list)} items, first={new_list[0][:40] if new_list else '?'}")
                            else:
                                print(f"[Tools]   role={r.get('role', '?')}: {new_val[:60] if new_val else '(EMPTY)'}")
                    else:
                        t = (slide.get('title') or '')[:40] if slide else ''
                        b = (slide.get('body') or '')[:40] if slide else ''
                        print(f"[Tools] Slide {i}: title={t} body={b}")
                url = create_from_template(
                    template_id,
                    arguments["title"],
                    arguments["slides"],
                )
            else:
                print("[Tools] No template, creating from scratch")
                url = create_presentation(
                    {"title": arguments["title"], "slides": arguments["slides"]},
                    style=ref_style,
                )
            pid = url.split("/d/")[1]
            state["presentation_id"] = pid
            title, slides = get_presentation_slides(pid)
            state["presentation_title"] = title
            state["current_slides"] = slides
            return json.dumps({
                "status": "created",
                "title": title,
                "slide_count": len(slides),
                "url": url,
            })
        except Exception as e:
            import traceback
            traceback.print_exc()
            return json.dumps({"error": f"Failed to create presentation: {str(e)}"})

    if tool_name == "add_slides":
        if err := _require_presentation():
            return err
        ops = []
        for s in arguments["slides"]:
            op = {"type": "add", "slide": {"title": s["title"], "body": s["body"]}}
            if "index" in s:
                op["index"] = s["index"]
            ops.append(op)
        modify_presentation(state["presentation_id"], ops)
        _refresh_slides()
        return json.dumps({"status": "added", "slides_added": len(ops)})

    if tool_name == "update_slides":
        if err := _require_presentation():
            return err
        from services.slides_api import get_slides_service, _replace_slide_text
        try:
            service = get_slides_service()
            pres = service.presentations().get(presentationId=state["presentation_id"]).execute()
            current_slides = pres.get("slides", [])

            updated = 0
            for u in arguments["updates"]:
                idx = u["index"]
                print(f"[Tools] update_slides: slide {idx}, keys={list(u.keys())}")
                if idx >= len(current_slides):
                    print(f"[Tools] Slide {idx} out of range ({len(current_slides)} slides)")
                    continue

                # If text_replacements provided, use role-based replacement
                if "text_replacements" in u and u["text_replacements"]:
                    print(f"[Tools] Using text_replacements for slide {idx}: {len(u['text_replacements'])} replacements")
                    _replace_slide_text(service, state["presentation_id"], current_slides[idx], u)
                    updated += 1
                else:
                    # Fallback: old title/body format via modify_presentation
                    slide = {}
                    if "title" in u:
                        slide["title"] = u["title"]
                    if "body" in u:
                        slide["body"] = u["body"]
                    if slide:
                        modify_presentation(state["presentation_id"], [{"type": "update", "index": idx, "slide": slide}])
                        updated += 1

            _refresh_slides()
            return json.dumps({"status": "updated", "slides_updated": updated})
        except Exception as e:
            import traceback
            traceback.print_exc()
            return json.dumps({"error": f"Failed to update slides: {str(e)}"})

    if tool_name == "delete_slides":
        if err := _require_presentation():
            return err
        ops = [{"type": "delete", "index": idx} for idx in arguments["indices"]]
        modify_presentation(state["presentation_id"], ops)
        _, slides = _refresh_slides()
        return json.dumps({"status": "deleted", "slides_deleted": len(ops), "remaining": len(slides)})

    if tool_name == "rename_presentation":
        if err := _require_presentation():
            return err
        modify_presentation(
            state["presentation_id"],
            [{"type": "update_title", "title": arguments["title"]}],
        )
        state["presentation_title"] = arguments["title"]
        return json.dumps({"status": "renamed", "new_title": arguments["title"]})

    if tool_name == "get_current_slides":
        if err := _require_presentation():
            return err
        title, slides = _refresh_slides()
        return json.dumps({
            "title": title,
            "slides": [{"index": i, **s} for i, s in enumerate(slides)],
        })

    if tool_name == "set_slide_background":
        if err := _require_presentation():
            return err
        try:
            set_slide_background(
                state["presentation_id"],
                arguments["slide_index"],
                arguments["image_url"],
            )
            return json.dumps({"status": "background_set", "slide_index": arguments["slide_index"]})
        except Exception as e:
            return json.dumps({"error": str(e)})

    if tool_name == "insert_image":
        if err := _require_presentation():
            return err
        try:
            insert_image(
                state["presentation_id"],
                arguments["slide_index"],
                arguments["image_url"],
                x=arguments.get("x", 200),
                y=arguments.get("y", 150),
                width=arguments.get("width", 300),
                height=arguments.get("height", 200),
            )
            return json.dumps({"status": "image_inserted", "slide_index": arguments["slide_index"]})
        except Exception as e:
            return json.dumps({"error": str(e)})

    if tool_name == "insert_equation":
        if err := _require_presentation():
            return err
        try:
            insert_equation(
                state["presentation_id"],
                arguments["slide_index"],
                arguments["latex"],
                x=arguments.get("x", 200),
                y=arguments.get("y", 150),
                width=arguments.get("width", 300),
                height=arguments.get("height", 80),
                font_size=arguments.get("font_size", 20),
                color=arguments.get("color", "000000"),
            )
            return json.dumps({"status": "equation_inserted", "slide_index": arguments["slide_index"], "latex": arguments["latex"]})
        except Exception as e:
            return json.dumps({"error": str(e)})

    if tool_name == "search_images":
        try:
            results = search_images(
                arguments["query"],
                count=arguments.get("count", 5),
            )
            return json.dumps({"status": "ok", "images": results})
        except Exception as e:
            return json.dumps({"error": str(e)})

    if tool_name == "generate_maths_content":
        try:
            content = generate_maths_content(
                topic=arguments["topic"],
                year_group=arguments["year_group"],
                num_examples=arguments.get("num_examples", 2),
                num_practice=arguments.get("num_practice", 4),
                difficulty=arguments.get("difficulty", "medium"),
                reference_content=arguments.get("reference_content", ""),
            )
            return json.dumps({"status": "ok", "content": content})
        except Exception as e:
            return json.dumps({"error": str(e)})

    if tool_name == "review_maths_content":
        try:
            review = review_maths_content(
                content=arguments["content"],
                year_group=arguments["year_group"],
            )
            return json.dumps({"status": "ok", "review": review})
        except Exception as e:
            return json.dumps({"error": str(e)})

    if tool_name == "build_slide_replacements":
        try:
            # Get template slides from uploaded references
            template_slides = []
            if state.get("uploaded_references"):
                for ref in state["uploaded_references"]:
                    if ref.get("slides"):
                        template_slides = ref["slides"]
                        break
            if not template_slides:
                return json.dumps({"error": "No template slides found. Upload a reference file first."})

            result = build_slide_replacements(
                maths_content=arguments["maths_content"],
                template_slides=template_slides,
            )

            # Post-process: ensure answer shapes get replacements
            # Extract all answers from maths content
            all_answers = []
            mc = arguments["maths_content"]
            for we in mc.get("worked_examples", []):
                if we.get("answer"):
                    all_answers.append(we["answer"])
            for gp in mc.get("guided_practice", []):
                if gp.get("answer"):
                    all_answers.append(gp["answer"])
            for ip in mc.get("independent_practice", []):
                if ip.get("answer"):
                    all_answers.append(ip["answer"])

            # For each slide, check if it has answer shapes in the template
            # but no answer replacements in the result
            answer_idx = 0
            for slide_idx, (slide_result, template_slide) in enumerate(zip(result, template_slides)):
                if not template_slide.get("text_elements"):
                    continue
                # Count answer shapes in template
                answer_count = sum(
                    1 for te in template_slide["text_elements"]
                    if te.get("role") == "answer"
                )
                if answer_count == 0:
                    continue

                # Check if result already has answer replacements
                reps = slide_result.get("text_replacements", [])
                has_answers = any(
                    r.get("role", "").lower() == "answer"
                    for r in reps
                )
                if has_answers:
                    # Count how many answers are provided
                    for r in reps:
                        if r.get("role", "").lower() == "answer":
                            nl = r.get("new_list", [])
                            answer_idx += len(nl) if nl else 1
                    continue

                # Inject answer replacements from maths content
                answers_for_slide = all_answers[answer_idx:answer_idx + answer_count]
                answer_idx += answer_count
                if answers_for_slide:
                    print(f"[Tools] Injecting {len(answers_for_slide)} answers for slide {slide_idx}")
                    reps.append({
                        "role": "answer",
                        "new_list": answers_for_slide,
                    })
                    slide_result["text_replacements"] = reps

            return json.dumps({"status": "ok", "slides": result, "slide_count": len(result)})
        except Exception as e:
            import traceback
            traceback.print_exc()
            return json.dumps({"error": str(e)})

    if tool_name == "search_curriculum_content":
        try:
            results = search_curriculum(
                query=arguments["query"],
                n_results=arguments.get("n_results", 5),
            )
            return json.dumps({"status": "ok", "results": results, "count": len(results)})
        except Exception as e:
            return json.dumps({"error": str(e)})

    return json.dumps({"error": f"Unknown tool: {tool_name}"})
