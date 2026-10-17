"""Shared Gemini text generation with fallback across the models listed in BRAIN.md (ai_models)."""
import time

DEFAULT_MODELS = ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash", "gemini-flash-latest"]

# Busy (503), rate-limited (429) or retired (404) models are skipped in favour of the next one
RETRYABLE = ("503", "429", "404", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "NOT_FOUND")


def generate_text(client, prompt, models=None):
    errors = []
    for model in models or DEFAULT_MODELS:
        for attempt in range(2):
            try:
                return client.models.generate_content(model=model, contents=prompt).text.strip()
            except Exception as e:
                message = str(e)
                if not any(code in message for code in RETRYABLE):
                    raise
                errors.append(f"{model}: {message[:80]}")
                if "404" in message or "NOT_FOUND" in message:
                    break
                time.sleep(5 * (attempt + 1))
    raise RuntimeError("All Gemini models failed: " + " | ".join(errors))
