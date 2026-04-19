"""
LLMProvider — Multi-provider LLM client for M4.

Supports all major providers through a single unified interface:
  - Anthropic (Claude) — paid
  - OpenAI (GPT) — paid
  - Google Gemini — paid
  - Mistral AI — paid
  - Ollama — free/local (llama3, mistral, phi3, etc.)

The ExplainerService calls LLMProvider.complete(prompt) and never
knows or cares which provider is responding. Switching providers
is a single config change in .env — zero code changes.

Provider selection order:
  1. Explicit provider argument passed to complete()
  2. M4_DEFAULT_PROVIDER in settings
  3. First provider with a configured API key
  4. Ollama (always available if installed locally)

Each provider normalizes its response to a common format:
  {
    "text": str,           # the response text
    "input_tokens": int,   # tokens consumed in prompt
    "output_tokens": int,  # tokens in response
    "model": str,          # actual model used
    "provider": str,       # which provider responded
  }
"""

import json
import logging
from typing import Optional

import httpx

from core.settings import settings

logger = logging.getLogger(__name__)


# ============================================================
# PROVIDER REGISTRY
# Maps provider name → handler method name
# ============================================================

PROVIDER_HANDLERS = {
    "anthropic": "_call_anthropic",
    "openai":    "_call_openai",
    "gemini":    "_call_gemini",
    "mistral":   "_call_mistral",
    "groq":      "_call_groq",      
    "ollama":    "_call_ollama",
}

# Default models per provider (used when no model explicitly specified)
def _default_models() -> dict:
    """
    Returns default models per provider.
    Called at runtime, not at import time — settings are fully loaded.
    """
    return {
        "anthropic": "claude-haiku-4-5-20251001",
        "openai":    "gpt-4o-mini",
        "gemini":    "gemini-1.5-flash",
        "mistral":   "mistral-small-latest",
        "groq":      "llama-3.3-70b-versatile",
        "ollama":    settings.m4_ollama_model,  
    }


class LLMProvider:
    """
    Unified LLM client for M4.

    Usage:
        provider = LLMProvider()
        result = await provider.complete(
            prompt="...",
            provider="anthropic",   # optional — uses default if omitted
            model="claude-haiku-4-5-20251001",  # optional
            max_tokens=1000,
        )
        text = result["text"]
        tokens = result["input_tokens"] + result["output_tokens"]
    """

    async def complete(
        self,
        prompt: str,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: int = 1000,
        timeout: float = 30.0,
    ) -> dict:
        """
        Send a prompt to the configured LLM provider and return the response.

        Tries the specified provider first. On failure, tries fallback providers
        in order. Always returns a valid dict — never raises to the caller.

        Returns:
            {
                "text": str,
                "input_tokens": int,
                "output_tokens": int,
                "model": str,
                "provider": str,
                "error": Optional[str],  # set if fallback was used
            }
        """
        resolved_provider = provider or settings.m4_default_provider
        resolved_model = model or self._default_model(resolved_provider)

        handler_name = PROVIDER_HANDLERS.get(resolved_provider)
        if handler_name is None:
            logger.warning(f"Unknown provider '{resolved_provider}' — falling back to Ollama")
            resolved_provider = "ollama"
            resolved_model = settings.m4_ollama_model
            handler_name = "_call_ollama"

        handler = getattr(self, handler_name)

        try:
            result = await handler(
                prompt=prompt,
                model=resolved_model,
                max_tokens=max_tokens,
                timeout=timeout,
            )
            result["provider"] = resolved_provider
            return result

        except Exception as exc:
            logger.warning(
                f"Provider '{resolved_provider}' failed: {exc}. "
                "Trying Ollama fallback."
            )
            # Fallback to Ollama (always available locally)
            try:
                result = await self._call_ollama(
                    prompt=prompt,
                    model=settings.m4_ollama_model,
                    max_tokens=max_tokens,
                    timeout=timeout,
                )
                result["provider"] = "ollama"
                result["error"] = f"Primary provider failed: {exc}"
                return result
            except Exception as ollama_exc:
                logger.warning(f"Ollama fallback also failed: {ollama_exc}")
                return {
                    "text": "",
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "model": "none",
                    "provider": "none",
                    "error": f"All providers failed. Last: {ollama_exc}",
                }

    def _default_model(self, provider: str) -> str:
     if provider == settings.m4_default_provider:
        return settings.m4_default_model
     return _default_models().get(provider, "")

    # ============================================================
    # ANTHROPIC — Claude models
    # https://docs.anthropic.com/en/api/messages
    # ============================================================

    async def _call_anthropic(
        self, prompt: str, model: str, max_tokens: int, timeout: float
    ) -> dict:
        """Call Anthropic Claude API."""
        if not settings.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY not configured")

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": settings.anthropic_api_key,
                    "anthropic-version": "2023-06-01",
                },
                json={
                    "model": model,
                    "max_tokens": max_tokens,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )

        if response.status_code != 200:
            raise ValueError(
                f"Anthropic API error {response.status_code}: {response.text[:200]}"
            )

        data = response.json()
        text = "".join(
            block.get("text", "")
            for block in data.get("content", [])
            if block.get("type") == "text"
        )
        usage = data.get("usage", {})
        return {
            "text": text,
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "model": model,
        }

    # ============================================================
    # OPENAI — GPT models
    # https://platform.openai.com/docs/api-reference/chat
    # ============================================================

    async def _call_openai(
        self, prompt: str, model: str, max_tokens: int, timeout: float
    ) -> dict:
        """Call OpenAI Chat Completions API."""
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY not configured")

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {settings.openai_api_key}",
                },
                json={
                    "model": model,
                    "max_tokens": max_tokens,
                    "messages": [{"role": "user", "content": prompt}],
                    "response_format": {"type": "json_object"},  # enforce JSON output
                },
            )

        if response.status_code != 200:
            raise ValueError(
                f"OpenAI API error {response.status_code}: {response.text[:200]}"
            )

        data = response.json()
        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        return {
            "text": text,
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "model": model,
        }

    # ============================================================
    # GOOGLE GEMINI
    # https://ai.google.dev/api/generate-content
    # ============================================================

    async def _call_gemini(
        self, prompt: str, model: str, max_tokens: int, timeout: float
    ) -> dict:
        """Call Google Gemini API."""
        if not settings.gemini_api_key:
            raise ValueError("GEMINI_API_KEY not configured")

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={settings.gemini_api_key}"
        )

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                url,
                headers={"Content-Type": "application/json"},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "maxOutputTokens": max_tokens,
                        "responseMimeType": "application/json",
                    },
                },
            )

        if response.status_code != 200:
            raise ValueError(
                f"Gemini API error {response.status_code}: {response.text[:200]}"
            )

        data = response.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        usage = data.get("usageMetadata", {})
        return {
            "text": text,
            "input_tokens": usage.get("promptTokenCount", 0),
            "output_tokens": usage.get("candidatesTokenCount", 0),
            "model": model,
        }

    # ============================================================
    # MISTRAL AI
    # https://docs.mistral.ai/api/
    # ============================================================

    async def _call_mistral(
        self, prompt: str, model: str, max_tokens: int, timeout: float
    ) -> dict:
        """Call Mistral AI API."""
        if not settings.mistral_api_key:
            raise ValueError("MISTRAL_API_KEY not configured")

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                "https://api.mistral.ai/v1/chat/completions",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {settings.mistral_api_key}",
                },
                json={
                    "model": model,
                    "max_tokens": max_tokens,
                    "messages": [{"role": "user", "content": prompt}],
                    "response_format": {"type": "json_object"},
                },
            )

        if response.status_code != 200:
            raise ValueError(
                f"Mistral API error {response.status_code}: {response.text[:200]}"
            )

        data = response.json()
        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        return {
            "text": text,
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "model": model,
        }
    # ============================================================
    # GROQ — Free Llama, Mixtral, Gemma via Groq Cloud
    # https://console.groq.com/docs/openai
    # Groq uses the OpenAI-compatible API format — same structure,
    # different base URL and API key header.
    # Free tier: generous rate limits, no credit card needed.
    # Best models for M4/M5:
    #   llama-3.3-70b-versatile   — best quality, free
    #   llama-3.1-8b-instant      — fastest, very low latency
    #   mixtral-8x7b-32768        — good for structured JSON
    # ============================================================

    async def _call_groq(
        self, prompt: str, model: str, max_tokens: int, timeout: float
    ) -> dict:
        """
        Call Groq API — free Llama and Mixtral models.

        Groq uses the OpenAI-compatible chat completions format.
        Get your free API key at https://console.groq.com
        No credit card required. Generous free tier.

        Recommended models:
          llama-3.3-70b-versatile  — best quality (default)
          llama-3.1-8b-instant     — fastest responses
          mixtral-8x7b-32768       — long context, good JSON
        """
        if not settings.groq_api_key:
            raise ValueError("GROQ_API_KEY not configured")

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {settings.groq_api_key}",
                },
                json={
                    "model": model,
                    "max_tokens": max_tokens,
                    "messages": [{"role": "user", "content": prompt}],
                    "response_format": {"type": "json_object"},
                },
            )

        if response.status_code != 200:
            raise ValueError(
                f"Groq API error {response.status_code}: {response.text[:200]}"
            )

        data = response.json()
        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        return {
            "text": text,
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "model": model,
        }

    # ============================================================
    # OLLAMA — Free local models (llama3, mistral, phi3, qwen...)
    # https://ollama.com/blog/openai-compatibility
    # Ollama exposes an OpenAI-compatible API locally
    # ============================================================

    async def _call_ollama(
        self, prompt: str, model: str, max_tokens: int, timeout: float
    ) -> dict:
        """
        Call a local Ollama model.

        Ollama must be running: https://ollama.com
        Install a model: ollama pull llama3.2
        Ollama runs on http://localhost:11434 by default.

        This is completely FREE — no API key needed.
        Models run on your own machine.
        """
        base_url = settings.m4_ollama_base_url.rstrip("/")

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{base_url}/api/chat",
                headers={"Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "options": {"num_predict": max_tokens},
                    "format": "json",  # Ollama JSON mode
                },
            )

        if response.status_code != 200:
            raise ValueError(
                f"Ollama error {response.status_code}: {response.text[:200]}"
            )

        data = response.json()
        text = data.get("message", {}).get("content", "")
        usage = data.get("usage", {})
        # Ollama reports token counts differently
        eval_count = data.get("eval_count", 0)
        prompt_eval_count = data.get("prompt_eval_count", 0)
        return {
            "text": text,
            "input_tokens": prompt_eval_count or usage.get("prompt_tokens", 0),
            "output_tokens": eval_count or usage.get("completion_tokens", 0),
            "model": model,
        }