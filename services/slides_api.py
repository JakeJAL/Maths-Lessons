"""Google Slides API wrapper — create, read, modify presentations."""

import os
import uuid
import requests as http_requests
from dotenv import load_dotenv
from googleapiclient.discovery import build

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/presentations",
    "https://www.googleapis.com/auth/drive",
]
UNSPLASH_ACCESS_KEY = os.getenv("UNSPLASH_ACCESS_KEY")


# ---------------------------------------------------------------------------
# Auth — per-user credentials from Flask session
# ---------------------------------------------------------------------------
def _get_creds():
    """Get credentials for the current user from their session."""
    from services.auth import get_user_creds
    creds = get_user_creds()
    if not creds:
        raise RuntimeError("Not authenticated. Please sign in with Google first.")
    return creds


def get_slides_service():
    """Return a Google Slides API service for the current user."""
    return build("slides", "v1", credentials=_get_creds())


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------
def get_presentation_slides(presentation_id: str) -> tuple[str, list]:
    """Fetch presentation state. Returns (title, list of {title, body} dicts)."""
    service = get_slides_service()
    pres = service.presentations().get(presentationId=presentation_id).execute()
    title = pres.get("title", "Untitled")

    slides = []
    for slide in pres.get("slides", []):
        text_boxes = []
        for elem in slide.get("pageElements", []):
            shape = elem.get("shape", {})
            if "text" in shape:
                text = "".join(
                    te.get("textRun", {}).get("content", "")
                    for te in shape["text"].get("textElements", [])
                    if "textRun" in te
                ).strip()
                text_boxes.append(text)
        slides.append({
            "title": text_boxes[0] if len(text_boxes) > 0 else "",
            "body":  text_boxes[1] if len(text_boxes) > 1 else "",
        })
    return title, slides


# ---------------------------------------------------------------------------
# Template support
# ---------------------------------------------------------------------------
def _get_drive_service():
    """Get a Google Drive API service instance."""
    return build("drive", "v3", credentials=_get_creds())


def upload_pptx_as_template(file_path: str, filename: str) -> str:
    """Upload a .pptx/.pptm file to Drive, convert to Google Slides. Returns the presentation ID."""
    from googleapiclient.http import MediaFileUpload

    drive = _get_drive_service()
    media = MediaFileUpload(
        file_path,
        mimetype="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )
    result = drive.files().create(
        body={
            "name": f"[Template] {filename}",
            "mimeType": "application/vnd.google-apps.presentation",
        },
        media_body=media,
        fields="id",
    ).execute()
    return result["id"]


def create_from_template(template_id: str, title: str, slide_contents: list) -> str:
    """
    Copy a template presentation, then replace text on each slide.

    slide_contents: list of slide dicts. Each can contain:
      - {"text_replacements": [{"original": str, "new": str}, ...]} for content-matched replacement
      - {"title": str, "body": str} as fallback
    Template slides are NEVER deleted — unused slides keep their original content.

    Returns the URL of the new presentation.
    """
    drive = _get_drive_service()
    service = get_slides_service()

    # Copy the template
    copy = drive.files().copy(
        fileId=template_id,
        body={"name": title},
    ).execute()
    new_id = copy["id"]

    # Get the template slides
    pres = service.presentations().get(presentationId=new_id).execute()
    template_slides = pres.get("slides", [])

    # Replace content on existing template slides
    for i, content in enumerate(slide_contents):
        if i < len(template_slides):
            _replace_slide_text(service, new_id, template_slides[i], content)
        # Don't add extra slides beyond the template — just skip

    # Rename and make viewable for embed
    drive.files().update(fileId=new_id, body={"name": title}).execute()
    try:
        drive.permissions().create(
            fileId=new_id,
            body={"type": "anyone", "role": "reader"},
        ).execute()
    except Exception:
        pass  # Non-critical — embed just won't work without sign-in

    return f"https://docs.google.com/presentation/d/{new_id}"


def _normalize_math_text(text: str) -> str:
    """Normalize unicode math characters to ASCII for matching.

    Mathematical fonts in PowerPoint use special unicode ranges for italic,
    bold, script letters etc. This maps them all back to plain ASCII so
    '5𝒙 + 11 = 29' matches '5x + 11 = 29'.
    """
    import unicodedata
    result = []
    for ch in text:
        cp = ord(ch)
        # Skip zero-width and invisible characters
        if cp in (0x200b, 0x200c, 0x200d, 0x2060, 0xfeff):
            continue
        # Map all unicode whitespace to regular space
        if unicodedata.category(ch) in ('Zs', 'Zl', 'Zp') or ch in ('\t', '\xa0'):
            result.append(' ')
            continue
        # Try NFKD decomposition — handles most math alphanumerics
        decomposed = unicodedata.normalize("NFKD", ch)
        ascii_chars = decomposed.encode("ascii", "ignore").decode("ascii")
        if ascii_chars:
            result.append(ascii_chars)
        else:
            # Manual mapping for common math symbols
            MATH_MAP = {
                '×': '*', '÷': '/', '−': '-', '–': '-', '—': '-',
                '≤': '<=', '≥': '>=', '≠': '!=', '±': '+-',
                '·': '.', '∙': '.', '⋅': '.',
            }
            mapped = MATH_MAP.get(ch)
            if mapped:
                result.append(mapped)
            # else: skip unmappable characters entirely
    # Collapse whitespace and strip
    return " ".join("".join(result).split())


def _replace_slide_text(service, pres_id, slide_obj, content):
    """Replace text in slide shapes using role-based matching against actual slide content.

    content can be:
      - New format: {"text_replacements": [{"role": str, "new": str}, ...]}
        Matches shapes by classifying their current text content into roles.
      - Old format: {"title": str, "body": str}
    """
    slide_id = slide_obj["objectId"]
    reqs = []
    notes_parts = []

    if "text_replacements" in content:
        replacements = content.get("text_replacements") or []
        if replacements:
            # Build a list of text shapes with their current text and classified role
            text_shapes = []
            for el in slide_obj.get("pageElements", []):
                shape = el.get("shape", {})
                if "text" in shape:
                    text = _get_element_text(el)
                    if text:
                        role = _classify_slide_shape(text)
                        text_shapes.append({"id": el["objectId"], "text": text, "role": role})

            # Match replacements to shapes by role
            # Group shapes by role for ordered matching
            shapes_by_role = {}
            for ts in text_shapes:
                shapes_by_role.setdefault(ts["role"], []).append(ts)

            # Track which occurrence of each role we're on
            role_counters = {}
            used_ids = set()

            # First pass: expand any "new_list" entries into individual replacements
            expanded = []
            for replacement in replacements:
                if "new_list" in replacement:
                    target_role = replacement.get("role", "").lower()
                    for item in replacement["new_list"]:
                        expanded.append({"role": target_role, "new": item})
                else:
                    expanded.append(replacement)

            for replacement in expanded:
                # Handle various key names the agent might use
                new_text = (
                    replacement.get("new", "") or
                    replacement.get("text", "") or
                    replacement.get("new_text", "") or
                    replacement.get("content", "") or
                    replacement.get("value", "") or
                    ""
                ).strip()
                target_role = replacement.get("role", "").lower()
                if not new_text or not target_role:
                    continue

                # Auto-reclassify: if text looks like an answer (e.g. "x = 3")
                # but role is "equation", fix it to "answer" — and vice versa
                import re as _re
                norm_new = _normalize_math_text(new_text).strip()
                # Strip any number/letter prefix for classification check
                stripped = _re.sub(r'^[a-z0-9]+\)\s*', '', norm_new)
                if target_role == "equation":
                    # Check if this looks like an answer rather than a question
                    is_answer = bool(
                        _re.match(r'^[a-z]\s*=\s*-?\d', stripped) or  # x = 3
                        _re.match(r'^[a-z]\s*=\s*-?\s*\d', stripped) or  # x = - 3
                        _re.match(r'^[a-z]\s*=\s*\d', stripped)  # x =3
                    )
                    # Also check: short text with just variable = number
                    if not is_answer and len(stripped) < 15 and '=' in stripped:
                        parts = stripped.split('=')
                        if len(parts) == 2 and len(parts[0].strip()) <= 2:
                            is_answer = True
                    if is_answer:
                        print(f"[Slides] Reclassifying '{new_text[:30]}' from equation to answer")
                        target_role = "answer"
                elif target_role == "answer" and not _re.match(r'^[a-z]\s*=\s*-?\d', stripped):
                    if _re.search(r'\d+[a-z]\s*[+\-*/=]|\([^)]+\)', stripped):
                        print(f"[Slides] Reclassifying '{new_text[:30]}' from answer to equation")
                        target_role = "equation"

                # Never touch banner or date shapes
                if target_role in ("banner", "date"):
                    print(f"[Slides] Skipping {target_role} replacement on slide {slide_id}")
                    continue

                # Get the next unused shape for this role
                counter = role_counters.get(target_role, 0)
                candidates = shapes_by_role.get(target_role, [])

                matched = None
                while counter < len(candidates):
                    if candidates[counter]["id"] not in used_ids:
                        matched = candidates[counter]
                        break
                    counter += 1
                role_counters[target_role] = counter + 1

                if matched:
                    used_ids.add(matched["id"])
                    obj_id = matched["id"]

                    # Preserve number/letter prefix from original text
                    import re as _re
                    original_norm = _normalize_math_text(matched["text"])
                    original_has_prefix = _re.match(r'^([a-z0-9]+\)\s*)', original_norm)
                    new_norm = _normalize_math_text(new_text)
                    new_has_prefix = _re.match(r'^([a-z0-9]+\)\s*)', new_norm)

                    if original_has_prefix and not new_has_prefix:
                        # Original had prefix, new doesn't — add it
                        new_text = original_has_prefix.group(1) + new_text
                    elif not original_has_prefix and new_has_prefix:
                        # Original had no prefix, new has one — strip it
                        new_text = new_text[new_has_prefix.end():]

                    print(f"[Slides] Replacing {target_role}[{counter}] shape {obj_id}: {matched['text'][:40]!r} -> {new_text[:40]!r}")
                    reqs.append({"deleteText": {"objectId": obj_id, "textRange": {"type": "ALL"}}})
                    reqs.append({"insertText": {"objectId": obj_id, "text": new_text}})

                    if target_role in ("steps", "answer", "solution", "worked_example"):
                        notes_parts.append(new_text)
                else:
                    print(f"[Slides] Warning: No more shapes with role '{target_role}' on slide {slide_id} (wanted occurrence {counter})")

    else:
        # Fallback: old title/body format
        text_boxes = [
            el for el in slide_obj.get("pageElements", [])
            if el.get("shape", {}).get("shapeType") == "TEXT_BOX" and "text" in el.get("shape", {})
        ]
        body_text = content.get("body", "")
        title_text = content.get("title", "")

        if len(text_boxes) == 1:
            combined = title_text
            if body_text:
                combined = (combined + "\n" + body_text) if combined else body_text
            obj_id = text_boxes[0]["objectId"]
            old = _get_element_text(text_boxes[0])
            if old:
                reqs.append({"deleteText": {"objectId": obj_id, "textRange": {"type": "ALL"}}})
            if combined:
                reqs.append({"insertText": {"objectId": obj_id, "text": combined}})
        elif len(text_boxes) >= 2:
            for idx, elem in enumerate(text_boxes):
                obj_id = elem["objectId"]
                old = _get_element_text(elem)
                if old:
                    reqs.append({"deleteText": {"objectId": obj_id, "textRange": {"type": "ALL"}}})
                if idx == 0 and title_text:
                    reqs.append({"insertText": {"objectId": obj_id, "text": title_text}})
                elif idx == 1 and body_text:
                    reqs.append({"insertText": {"objectId": obj_id, "text": body_text}})

        if body_text:
            notes_parts.append(body_text)

    if content.get("speaker_notes"):
        sn = content["speaker_notes"]
        if isinstance(sn, list):
            notes_parts.extend(str(s) for s in sn)
        else:
            notes_parts.append(str(sn))

    if reqs:
        try:
            service.presentations().batchUpdate(
                presentationId=pres_id, body={"requests": reqs},
            ).execute()
        except Exception as e:
            print(f"[Slides] Error replacing text on slide {slide_id}: {e}")

    if notes_parts:
        # Flatten any lists in notes_parts
        flat_notes = []
        for part in notes_parts:
            if isinstance(part, list):
                flat_notes.extend(str(p) for p in part)
            else:
                flat_notes.append(str(part))
        notes_text = "\n\n".join(flat_notes)
        notes_id = _get_notes_shape_id(slide_obj)
        if notes_id:
            notes_reqs = []
            existing_notes = _get_notes_text(slide_obj)
            if existing_notes:
                notes_reqs.append({"deleteText": {"objectId": notes_id, "textRange": {"type": "ALL"}}})
            notes_reqs.append({"insertText": {"objectId": notes_id, "text": notes_text}})
            try:
                service.presentations().batchUpdate(
                    presentationId=pres_id, body={"requests": notes_reqs},
                ).execute()
            except Exception as e:
                print(f"[Slides] Warning: Failed to write speaker notes: {e}")


def _classify_slide_shape(text: str) -> str:
    """Classify a Google Slides shape's role based on its text content.
    Uses normalized text for matching to handle unicode math characters."""
    import re as _re
    norm = _normalize_math_text(text).lower().strip()

    # Banner/navigation labels
    if norm in ("do now", "engage", "learn", "build", "apply", "review", "ll"):
        return "banner"

    # Date patterns — check BEFORE equations so "25/07/2024" isn't misclassified
    if _re.match(r'^\d{1,2}/\d{1,2}/\d{2,4}$', norm):
        return "date"

    # Answer patterns: "x = 11.5" or "a = 2" or "y = -3" (single variable = number)
    # These are short and just show a result, not a full equation to solve
    if _re.match(r'^[a-z]\s*=\s*-?\d+\.?\d*$', norm):
        return "answer"

    # Numbered equations with answers: "1) x = 5" style
    if _re.match(r'^\d+\)\s*[a-z]\s*=\s*-?\d', norm):
        return "answer"

    # Lettered equations with answers: "a) x = 5" style
    if _re.match(r'^[a-z]\)\s*[a-z]\s*=\s*-?\d', norm):
        return "answer"

    # Equations: contains math patterns like "5x + 3 = 17" or "2(3x - 1) = 10"
    if _re.search(r'\d+[a-z]\s*[+\-*/=]|\d+\s*[+\-*/]\s*\d+\s*=|\([^)]+\)\s*[+\-*/=]', norm):
        return "equation"

    # Numbered/lettered equation items: "a) 8x + 5 = 29" or "1) 2x - 5 = 18"
    if _re.match(r'^[a-z0-9]+\)', norm):
        return "equation"

    # Labels like "Example 1", "Question 1"
    if _re.match(r'^(example|question|task|exercise|problem|worked example|guided practice|independent practice|do now|apply|review)\s*\d*$', norm):
        return "label"

    # Instructions
    if _re.match(r'^(solve|calculate|work out|simplify|evaluate|find|expand|factorise)', norm):
        return "instruction"

    # Short heading-like text
    if len(norm) < 60 and '\n' not in norm:
        return "heading"

    # Default
    return "body"


def _get_notes_shape_id(slide_obj):
    """Get the object ID of the speaker notes text shape for a slide."""
    notes_page = slide_obj.get("slideProperties", {}).get("notesPage", {})
    for el in notes_page.get("pageElements", []):
        shape = el.get("shape", {})
        if shape.get("placeholder", {}).get("type") == "BODY":
            return el["objectId"]
    return None


def _get_notes_text(slide_obj):
    """Get existing text from the speaker notes shape."""
    notes_page = slide_obj.get("slideProperties", {}).get("notesPage", {})
    for el in notes_page.get("pageElements", []):
        shape = el.get("shape", {})
        if shape.get("placeholder", {}).get("type") == "BODY":
            return "".join(
                te.get("textRun", {}).get("content", "")
                for te in shape.get("text", {}).get("textElements", [])
                if "textRun" in te
            ).strip()
    return ""


def _get_element_text(elem):
    """Extract raw text from a slide element."""
    return "".join(
        te.get("textRun", {}).get("content", "")
        for te in elem.get("shape", {}).get("text", {}).get("textElements", [])
        if "textRun" in te
    ).strip()


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------
def create_presentation(slide_data: dict, style: dict = None) -> str:
    """Create a new presentation from structured data. Returns the URL."""
    service = get_slides_service()
    style = style or {}

    pres = service.presentations().create(
        body={"title": slide_data["title"]}
    ).execute()
    pres_id = pres["presentationId"]

    # Re-fetch to get the default first slide
    pres = service.presentations().get(presentationId=pres_id).execute()
    first_slide = pres["slides"][0]
    first_slide_id = first_slide["objectId"]

    # Remove default placeholders
    reqs = [
        {"deleteObject": {"objectId": el["objectId"]}}
        for el in first_slide.get("pageElements", [])
    ]

    # Background color for first slide
    if style.get("background_color"):
        reqs.append(_bg_color_request(first_slide_id, style["background_color"]))

    # Title slide (index 0)
    if slide_data["slides"]:
        s = slide_data["slides"][0]
        reqs.extend(_title_slide_requests(
            first_slide_id, "title_slide_title", "title_slide_subtitle",
            s["title"], s.get("body", ""), style,
        ))

    # Content slides (index 1+)
    for i, s in enumerate(slide_data["slides"][1:], start=1):
        sid = f"slide_{i}"
        reqs.append({"createSlide": {"objectId": sid, "insertionIndex": i}})
        if style.get("background_color"):
            reqs.append(_bg_color_request(sid, style["background_color"]))
        reqs.extend(_content_slide_requests(
            sid, f"{sid}_heading", f"{sid}_body",
            s["title"], s.get("body", ""), style,
        ))

    if reqs:
        service.presentations().batchUpdate(
            presentationId=pres_id, body={"requests": reqs}
        ).execute()

    # Make viewable for embed
    try:
        drive = _get_drive_service()
        drive.permissions().create(
            fileId=pres_id,
            body={"type": "anyone", "role": "reader"},
        ).execute()
    except Exception:
        pass

    return f"https://docs.google.com/presentation/d/{pres_id}"


# ---------------------------------------------------------------------------
# Modify
# ---------------------------------------------------------------------------
def modify_presentation(presentation_id: str, operations: list) -> None:
    """Apply a list of operations: add, update, delete, update_title."""
    service = get_slides_service()
    pres = service.presentations().get(presentationId=presentation_id).execute()
    current = pres.get("slides", [])

    # Process deletes highest-index-first to avoid shifting
    sorted_ops = sorted(
        operations,
        key=lambda op: (
            0 if op["type"] == "delete" else 1,
            -(op.get("index", 0)) if op["type"] == "delete" else 0,
        ),
    )

    for op in sorted_ops:
        t = op["type"]

        if t == "update_title":
            _update_presentation_title(presentation_id, op["title"])

        elif t == "delete":
            idx = op["index"]
            if idx < len(current):
                service.presentations().batchUpdate(
                    presentationId=presentation_id,
                    body={"requests": [{"deleteObject": {"objectId": current[idx]["objectId"]}}]},
                ).execute()
                pres = service.presentations().get(presentationId=presentation_id).execute()
                current = pres.get("slides", [])

        elif t == "update":
            idx = op["index"]
            if idx < len(current):
                _update_slide_content(service, presentation_id, current[idx], op["slide"])
                pres = service.presentations().get(presentationId=presentation_id).execute()
                current = pres.get("slides", [])

        elif t == "add":
            idx = op.get("index", len(current))
            _add_slide(service, presentation_id, idx, op["slide"])
            pres = service.presentations().get(presentationId=presentation_id).execute()
            current = pres.get("slides", [])


def set_slide_background(presentation_id: str, slide_index: int, image_url: str) -> None:
    """Set a slide's background to a stretched image."""
    service = get_slides_service()
    pres = service.presentations().get(presentationId=presentation_id).execute()
    slides = pres.get("slides", [])
    if slide_index >= len(slides):
        raise ValueError(f"Slide {slide_index} out of range ({len(slides)} slides)")

    service.presentations().batchUpdate(
        presentationId=presentation_id,
        body={"requests": [{
            "updatePageProperties": {
                "objectId": slides[slide_index]["objectId"],
                "pageProperties": {
                    "pageBackgroundFill": {
                        "stretchedPictureFill": {"contentUrl": image_url}
                    }
                },
                "fields": "pageBackgroundFill",
            }
        }]},
    ).execute()


def insert_image(
    presentation_id: str, slide_index: int, image_url: str,
    x: float = 200, y: float = 150, width: float = 300, height: float = 200,
) -> None:
    """Insert an image onto a slide at the given position/size (PT)."""
    service = get_slides_service()
    pres = service.presentations().get(presentationId=presentation_id).execute()
    slides = pres.get("slides", [])
    if slide_index >= len(slides):
        raise ValueError(f"Slide {slide_index} out of range ({len(slides)} slides)")

    service.presentations().batchUpdate(
        presentationId=presentation_id,
        body={"requests": [{
            "createImage": {
                "objectId": f"img_{uuid.uuid4().hex[:8]}",
                "url": image_url,
                "elementProperties": {
                    "pageObjectId": slides[slide_index]["objectId"],
                    "size": {
                        "height": {"magnitude": height, "unit": "PT"},
                        "width":  {"magnitude": width,  "unit": "PT"},
                    },
                    "transform": {
                        "scaleX": 1, "scaleY": 1,
                        "translateX": x, "translateY": y,
                        "unit": "PT",
                    },
                },
            }
        }]},
    ).execute()


def insert_equation(
    presentation_id: str, slide_index: int, latex: str,
    x: float = 200, y: float = 150, width: float = 300, height: float = 80,
    font_size: int = 20, color: str = "000000",
) -> None:
    """Render a LaTeX equation to an image and insert it onto a slide."""
    from urllib.parse import quote

    # Build CodeCogs URL — renders LaTeX to PNG
    encoded = quote(latex)
    image_url = (
        f"https://latex.codecogs.com/png.image?"
        f"\\dpi{{200}}\\large\\color{{{color}}}"
        f"{encoded}"
    )

    insert_image(presentation_id, slide_index, image_url, x, y, width, height)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _update_presentation_title(presentation_id: str, new_title: str):
    """Update the presentation title via the Drive API."""
    try:
        drive = _get_drive_service()
        drive.files().update(fileId=presentation_id, body={"name": new_title}).execute()
    except Exception:
        pass


def _update_slide_content(service, presentation_id: str, slide_obj: dict, new_content: dict):
    """Replace text in a slide's text boxes. Only touches boxes with new content provided."""
    text_boxes = [
        el for el in slide_obj.get("pageElements", [])
        if "text" in el.get("shape", {})
    ]
    mapping = {}
    if len(text_boxes) > 0:
        mapping[0] = "title"
    if len(text_boxes) > 1:
        mapping[1] = "body"

    reqs = []
    for idx, key in mapping.items():
        if key not in new_content:
            continue

        obj_id = text_boxes[idx]["objectId"]
        old_text = "".join(
            te.get("textRun", {}).get("content", "")
            for te in text_boxes[idx]["shape"]["text"].get("textElements", [])
            if "textRun" in te
        )
        if old_text:
            reqs.append({"deleteText": {"objectId": obj_id, "textRange": {"type": "ALL"}}})

        if new_content[key]:
            reqs.append({"insertText": {"objectId": obj_id, "text": new_content[key]}})
            if key == "title":
                reqs.append({"updateTextStyle": {
                    "objectId": obj_id,
                    "style": {"fontSize": {"magnitude": 28, "unit": "PT"}, "bold": True},
                    "fields": "fontSize,bold",
                }})
            else:
                reqs.append({"updateTextStyle": {
                    "objectId": obj_id,
                    "style": {"fontSize": {"magnitude": 16, "unit": "PT"}},
                    "fields": "fontSize",
                }})

    if reqs:
        service.presentations().batchUpdate(
            presentationId=presentation_id, body={"requests": reqs}
        ).execute()


def _add_slide(service, presentation_id: str, index: int, slide_data: dict, style: dict = None):
    """Insert a new content slide at the given index."""
    sid = f"added_{uuid.uuid4().hex[:8]}"
    reqs = [{"createSlide": {"objectId": sid, "insertionIndex": index}}]
    if style and style.get("background_color"):
        reqs.append(_bg_color_request(sid, style["background_color"]))
    reqs.extend(_content_slide_requests(
        sid, f"{sid}_heading", f"{sid}_body",
        slide_data.get("title", ""), slide_data.get("body", ""), style,
    ))
    service.presentations().batchUpdate(
        presentationId=presentation_id, body={"requests": reqs}
    ).execute()

    # Add speaker notes if provided
    notes_text = slide_data.get("speaker_notes", "")
    if notes_text:
        pres = service.presentations().get(presentationId=presentation_id).execute()
        for slide in pres.get("slides", []):
            if slide["objectId"] == sid:
                notes_id = _get_notes_shape_id(slide)
                if notes_id:
                    service.presentations().batchUpdate(
                        presentationId=presentation_id,
                        body={"requests": [
                            {"insertText": {"objectId": notes_id, "text": notes_text}},
                        ]},
                    ).execute()
                break


def _title_slide_requests(page_id, title_id, subtitle_id, title_text, subtitle_text, style=None):
    """Build API requests for a centered title slide."""
    style = style or {}
    title_size = style.get("heading_font_size", 42)
    subtitle_size = style.get("body_font_size", 20)
    title_color = style.get("heading_color")
    subtitle_color = style.get("body_color", {"red": 0.4, "green": 0.4, "blue": 0.4})
    title_bold = style.get("heading_bold", True)

    reqs = [
        _text_box_request(title_id, page_id, 50, 120, 620, 120),
        _text_box_request(subtitle_id, page_id, 50, 250, 620, 60),
    ]
    if title_text:
        reqs.append({"insertText": {"objectId": title_id, "text": title_text}})
        title_style = {"fontSize": {"magnitude": title_size, "unit": "PT"}, "bold": title_bold}
        fields = "fontSize,bold"
        if title_color:
            title_style["foregroundColor"] = {"opaqueColor": {"rgbColor": title_color}}
            fields += ",foregroundColor"
        reqs.append({"updateTextStyle": {"objectId": title_id, "style": title_style, "fields": fields}})
        reqs.append({"updateParagraphStyle": {
            "objectId": title_id, "style": {"alignment": "CENTER"}, "fields": "alignment",
        }})
    if subtitle_text:
        reqs.append({"insertText": {"objectId": subtitle_id, "text": subtitle_text}})
        sub_style = {
            "fontSize": {"magnitude": subtitle_size, "unit": "PT"},
            "foregroundColor": {"opaqueColor": {"rgbColor": subtitle_color}},
        }
        reqs.append({"updateTextStyle": {
            "objectId": subtitle_id, "style": sub_style, "fields": "fontSize,foregroundColor",
        }})
        reqs.append({"updateParagraphStyle": {
            "objectId": subtitle_id, "style": {"alignment": "CENTER"}, "fields": "alignment",
        }})
    return reqs


def _content_slide_requests(page_id, heading_id, body_id, heading_text, body_text, style=None):
    """Build API requests for a content slide with heading + body."""
    style = style or {}
    heading_size = style.get("heading_font_size", 28)
    body_size = style.get("body_font_size", 16)
    heading_color = style.get("heading_color")
    body_color = style.get("body_color")
    heading_bold = style.get("heading_bold", True)

    reqs = [
        _text_box_request(heading_id, page_id, 40, 30, 640, 60),
        _text_box_request(body_id, page_id, 40, 100, 640, 300),
    ]
    if heading_text:
        reqs.append({"insertText": {"objectId": heading_id, "text": heading_text}})
        h_style = {"fontSize": {"magnitude": heading_size, "unit": "PT"}, "bold": heading_bold}
        fields = "fontSize,bold"
        if heading_color:
            h_style["foregroundColor"] = {"opaqueColor": {"rgbColor": heading_color}}
            fields += ",foregroundColor"
        reqs.append({"updateTextStyle": {"objectId": heading_id, "style": h_style, "fields": fields}})
    if body_text:
        reqs.append({"insertText": {"objectId": body_id, "text": body_text}})
        b_style = {"fontSize": {"magnitude": body_size, "unit": "PT"}}
        fields = "fontSize"
        if body_color:
            b_style["foregroundColor"] = {"opaqueColor": {"rgbColor": body_color}}
            fields += ",foregroundColor"
        reqs.append({"updateTextStyle": {"objectId": body_id, "style": b_style, "fields": fields}})
    return reqs


def _bg_color_request(page_id, rgb):
    """Build a request to set a solid background color on a slide."""
    return {
        "updatePageProperties": {
            "objectId": page_id,
            "pageProperties": {
                "pageBackgroundFill": {
                    "solidFill": {
                        "color": {"rgbColor": rgb}
                    }
                }
            },
            "fields": "pageBackgroundFill",
        }
    }


def _text_box_request(obj_id, page_id, x, y, w, h):
    """Build a createShape request for a text box."""
    return {
        "createShape": {
            "objectId": obj_id,
            "shapeType": "TEXT_BOX",
            "elementProperties": {
                "pageObjectId": page_id,
                "size": {
                    "height": {"magnitude": h, "unit": "PT"},
                    "width":  {"magnitude": w, "unit": "PT"},
                },
                "transform": {
                    "scaleX": 1, "scaleY": 1,
                    "translateX": x, "translateY": y,
                    "unit": "PT",
                },
            },
        }
    }


# ---------------------------------------------------------------------------
# Image search
# ---------------------------------------------------------------------------
def search_images(query: str, count: int = 5) -> list[dict]:
    """
    Search Unsplash for images matching a query.
    Returns a list of {url, description, photographer} dicts.
    """
    resp = http_requests.get(
        "https://api.unsplash.com/search/photos",
        params={"query": query, "per_page": count, "orientation": "landscape"},
        headers={"Authorization": "Client-ID " + (UNSPLASH_ACCESS_KEY or "")},
    )
    resp.raise_for_status()
    results = []
    for photo in resp.json().get("results", []):
        results.append({
            "url": photo["urls"]["regular"],
            "description": photo.get("alt_description", ""),
            "photographer": photo["user"]["name"],
        })
    return results
