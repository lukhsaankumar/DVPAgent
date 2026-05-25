from __future__ import annotations

from openai import OpenAI

from .config import get_settings


def generate_meeting_prep(prompt: str) -> str:
    settings = get_settings()
    client = OpenAI(api_key=settings.openai_api_key)
    response = client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": "You write clear internal meeting prep documents for advisor conversations."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
    )
    content = response.choices[0].message.content
    if not content:
        raise RuntimeError("OpenAI returned an empty response.")
    return content.strip()

