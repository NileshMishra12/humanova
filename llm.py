"""Thin Claude wrapper. Raises LLMError so callers can fall back to plain rules."""
import json

import config


MOCK = config.MOCK
_client = None


class LLMError(Exception):
    pass


def client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic(api_key=config.API_KEY, timeout=60.0, max_retries=2)  # fail fast in a demo
    return _client


def complete_text(prompt, system="", max_tokens=600, model=config.FAST_MODEL):
    if config.MOCK:
        raise LLMError("Mock mode: no API call made")
    try:
        kwargs = {"system": system} if system else {}
        reply = client().messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
            **kwargs,
        )
        return "".join(b.text for b in reply.content if b.type == "text")
    except Exception as e:
        raise LLMError(str(e)) from e


def _first_json_object(text):
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object found")
    depth, in_str, escaped = 0, False, False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("unterminated JSON object")


def complete_json(prompt, system="", max_tokens=600, model=config.FAST_MODEL):
    text = complete_text(prompt, system, max_tokens, model)
    try:
        return _first_json_object(text)
    except ValueError:
        pass
    text = complete_text(prompt + "\n\nReply with JSON only, no other text.", system, max_tokens, model)
    try:
        return _first_json_object(text)
    except ValueError as e:
        raise LLMError(f"Reply was not valid JSON: {e}") from e
