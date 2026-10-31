"""Thin client for OpenAI-compatible chat APIs (Grok, Groq, OpenRouter, Gemini)."""
from __future__ import annotations

import json
import re

import requests

from .config import secret

PROVIDERS = {
    "xai": ("https://api.x.ai/v1", "XAI_API_KEY"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY"),
}


class LLM:
    def __init__(self, provider: str, model: str, temperature: float = 0.2, timeout: int = 90):
        if provider not in PROVIDERS:
            raise ValueError(f"Unknown LLM provider {provider!r}; use one of {list(PROVIDERS)}")
        self.base_url, key_name = PROVIDERS[provider]
        self.api_key = secret(key_name)
        if not self.api_key:
            raise RuntimeError(f"{key_name} is not set")
        self.provider = provider
        self.model = model
        self.temperature = temperature
        self.timeout = timeout

    def chat_json(self, system: str, user: str) -> dict:
        """Ask for a JSON object back and parse it."""
        resp = requests.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "temperature": self.temperature,
                "messages": [
                    {"role": "system", "content": system + "\nReply with one JSON object only."},
                    {"role": "user", "content": user},
                ],
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
        return parse_json(text)


def parse_json(text: str) -> dict:
    """Pull the first JSON object out of a model reply, tolerating code fences."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object in model reply: {text[:200]}")
    return json.loads(match.group(0))


def make_llm(cfg: dict) -> LLM | None:
    """Returns None (rules-only mode) when no provider or key is configured."""
    llm_cfg = cfg.get("llm") or {}
    provider = llm_cfg.get("provider", "none")
    if provider in (None, "none"):
        return None
    try:
        return LLM(provider, llm_cfg["model"], llm_cfg.get("temperature", 0.2))
    except RuntimeError:
        return None
