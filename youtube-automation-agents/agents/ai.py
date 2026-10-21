"""Shared Gemini text generation with fallback across the models listed in BRAIN.md (ai_models)."""
import time

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

_state = {"last_call": 0.0, "out_of_quota": False}


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
    if _state["out_of_quota"]:
        # Earlier calls in this run already found the daily quota used up; don't spend more requests
        raise RuntimeError("All Gemini models failed: daily quota used up earlier in this run (429)")
    models = models or DEFAULT_MODELS
    errors = []
    text = _try_models(client, prompt, models, errors)
    if text is None and errors and all(any(code in e for code in QUOTA) for e in errors):
        print(f"[Gemini] Every model is rate-limited; waiting {QUOTA_WAIT_SECONDS}s for the limit to reset...")
        time.sleep(QUOTA_WAIT_SECONDS)
        retry_errors = []
        text = _try_models(client, prompt, models, retry_errors)
        errors += retry_errors
        if text is None and retry_errors and all(any(code in e for code in QUOTA) for e in retry_errors):
            _state["out_of_quota"] = True
    if text is None:
        raise RuntimeError("All Gemini models failed: " + " | ".join(errors))
    return text
