"""Shared AI text generation: Gemini first (BRAIN.md ai_models), then free backup providers (ai_fallbacks).

When every Gemini model is out of quota or busy, the same prompt goes to the backup providers in order, so a run
keeps making videos. Backups are OpenAI-compatible chat APIs; one whose key isn't set is skipped.
"""
import os
import time

import requests

DEFAULT_MODELS = ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash", "gemini-flash-latest"]

# Busy (503), rate-limited (429) or retired (404) models are skipped in favour of the next one;
# dropped connections and timeouts are retried the same way
RETRYABLE = ("503", "500", "429", "404", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "NOT_FOUND", "INTERNAL",
             "disconnected", "Connection", "timed out", "Timeout")
QUOTA = ("429", "RESOURCE_EXHAUSTED")
# Spacing between requests keeps a run under the per-minute request limit
MIN_GAP_SECONDS = 4
# Per-minute limits clear within a minute, so when every model is rate-limited wait once and try again
QUOTA_WAIT_SECONDS = 65

# OpenAI-compatible providers that can stand in for Gemini, and the key each needs
PROVIDERS = {
    "groq": {"url": "https://api.groq.com/openai/v1/chat/completions", "env": "GROQ_API_KEY"},
    "openrouter": {"url": "https://openrouter.ai/api/v1/chat/completions", "env": "OPENROUTER_API_KEY"},
}
DEFAULT_FALLBACKS = [{"provider": "groq", "models": ["openai/gpt-oss-120b", "qwen/qwen3.8-27b"]}]

_state = {"last_call": 0.0, "out_of_quota": False, "fallbacks": DEFAULT_FALLBACKS, "written_by": None}


def configure(config):
    """Use the backup providers listed under ai_fallbacks in BRAIN.md."""
    if config.get("ai_fallbacks") is not None:
        _state["fallbacks"] = config["ai_fallbacks"] or []


def last_writer():
    """Which provider and model wrote the most recent reply, e.g. "gemini gemini-3.8-flash"."""
    return _state["written_by"]


def backup_status():
    """e.g. "groq (key set)" for each configured backup, for the run log; never prints a key."""
    return ", ".join(f"{b['provider']} ({'key set' if os.getenv(PROVIDERS[b['provider']]['env']) else 'NO KEY'})"
                     for b in _state["fallbacks"] if b.get("provider") in PROVIDERS) or "none"


def _try_fallbacks(prompt, errors):
    """One pass over the backup providers' models. Returns the text, or None."""
    for backup in _state["fallbacks"]:
        provider = PROVIDERS.get(backup.get("provider"))
        key = provider and os.getenv(provider["env"])
        if not key:
            continue
        for model in backup.get("models") or []:
            try:
                response = requests.post(provider["url"], headers={"Authorization": f"Bearer {key}"},
                                         json={"model": model, "messages": [{"role": "user", "content": prompt}]},
                                         timeout=120)
                if response.status_code == 200:
                    text = (response.json()["choices"][0]["message"].get("content") or "").strip()
                    if text:
                        _state["written_by"] = f"{backup['provider']} {model}"
                        print(f"[AI] Gemini unavailable; written by {backup['provider']} {model}.")
                        return text
                errors.append(f"{backup['provider']} {model}: {response.status_code} {response.text[:150]}")
            except requests.RequestException as e:
                # The exception text never includes the key: it is sent in a header, not the URL
                errors.append(f"{backup['provider']} {model}: {type(e).__name__}")
    return None


def is_retryable(error):
    name = type(error).__name__
    return any(code in str(error) for code in RETRYABLE) or any(
        word in name for word in ("Connect", "Protocol", "Timeout", "Network"))


def _pace():
    wait = _state["last_call"] + MIN_GAP_SECONDS - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _state["last_call"] = time.monotonic()


def _try_models(client, prompt, models, errors):
    """One pass over the models. Returns the text, or None if every model failed with a retryable error."""
    for model in models:
        for attempt in range(2):
            _pace()
            try:
                return client.models.generate_content(model=model, contents=prompt).text.strip()
            except Exception as e:
                message = str(e)
                if not is_retryable(e):
                    raise
                errors.append(f"{model}: {message[:200]}")
                # A retired model or an exhausted quota will not recover in 5 seconds, so go straight to
                # the next model instead of spending a second request on the same one
                if any(code in message for code in ("404", "NOT_FOUND") + QUOTA):
                    break
                time.sleep(5 * (attempt + 1))
    return None


def generate_text(client, prompt, models=None):
    errors = []
    if not _state["out_of_quota"]:
        models = models or DEFAULT_MODELS
        text = _try_models(client, prompt, models, errors)
        if text is not None:
            _state["written_by"] = "gemini"
            return text
    # Gemini can't answer: a backup provider usually can, straight away
    text = _try_fallbacks(prompt, errors)
    if text is not None:
        return text
    if not _state["out_of_quota"] and errors and all(any(code in e for code in QUOTA) for e in errors
                                                     if e.startswith("gemini")):
        print(f"[Gemini] Every model is rate-limited; waiting {QUOTA_WAIT_SECONDS}s for the limit to reset...")
        time.sleep(QUOTA_WAIT_SECONDS)
        retry_errors = []
        text = _try_models(client, prompt, models, retry_errors)
        errors += retry_errors
        if text is not None:
            _state["written_by"] = "gemini"
            return text
        if retry_errors and all(any(code in e for code in QUOTA) for e in retry_errors):
            # Daily quota used up: later requests in this run skip Gemini and go to the backups
            _state["out_of_quota"] = True
    if _state["out_of_quota"] and not errors:
        errors.append("Gemini daily quota used up earlier in this run (429); no backup answered")
    raise RuntimeError("All AI models failed: " + " | ".join(errors))
