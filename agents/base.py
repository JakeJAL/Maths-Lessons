"""Shared agent utilities — API calling, model config."""

import os
import requests
from dotenv import load_dotenv

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL_DEFAULT = "google/gemini-2.0-flash-001"
MODEL_SMART = "google/gemini-2.5-flash"


def call_agent(system_prompt: str, user_prompt: str, model: str = None) -> str:
    """Call an agent with a system prompt and user message, return text."""
    resp = requests.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": model or MODEL_DEFAULT,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.3,
        },
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"].get("content") or ""
