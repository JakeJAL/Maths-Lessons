"""Agent modules — maths content generation, review, and slide building."""

from agents.maths import generate_maths_content, review_maths_content
from agents.slide_builder import build_slide_replacements

__all__ = [
    "generate_maths_content",
    "review_maths_content",
    "build_slide_replacements",
]
