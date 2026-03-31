"""Server-side application state (single-user)."""

state = {
    "messages": [],
    "presentation_id": None,
    "presentation_title": None,
    "current_slides": [],
    "uploaded_references": [],
}


def reset_state():
    state["messages"] = []
    state["presentation_id"] = None
    state["presentation_title"] = None
    state["current_slides"] = []
    state["uploaded_references"] = []
