from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import os

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def normalize_supabase_url(url: str) -> str:
    cleaned = url.strip()
    cleaned = cleaned.removesuffix("/")
    cleaned = cleaned.removesuffix("/rest/v1")
    return cleaned


@dataclass(frozen=True)
class Settings:
    supabase_url: str
    supabase_secret_key: str
    supabase_publishable_key: str | None
    openai_api_key: str
    openai_model: str

    @property
    def supabase_project_url(self) -> str:
        return normalize_supabase_url(self.supabase_url)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    load_dotenv(override=False)

    supabase_url = os.environ.get("SUPABASE_URL", "").strip()
    supabase_secret_key = os.environ.get("SUPABASE_SECRET_KEY", "").strip()
    supabase_publishable_key = os.environ.get("SUPABASE_PUBLISHABLE_KEY", "").strip() or None
    openai_api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    openai_model = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini").strip() or "gpt-4.1-mini"

    if not supabase_url:
        raise RuntimeError("SUPABASE_URL is missing from the environment.")
    if not supabase_secret_key:
        raise RuntimeError("SUPABASE_SECRET_KEY is missing from the environment.")
    if not openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is missing from the environment.")

    return Settings(
        supabase_url=supabase_url,
        supabase_secret_key=supabase_secret_key,
        supabase_publishable_key=supabase_publishable_key,
        openai_api_key=openai_api_key,
        openai_model=openai_model,
    )


def repo_path(*parts: str) -> Path:
    return PROJECT_ROOT.joinpath(*parts)

