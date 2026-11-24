"""The cache key: what makes two calls "the same call".

D16 #1 says model + prompt version + input. In full, the key is a SHA-256 of:

- the **candidate** (``provider:model``): another model gives another answer;
- the model's **params** (``reasoning_effort: low``): they change the answer too;
- the **prompt version**: the request's text is already in the key, so new wording
  changes the key by itself. The version covers what the text does not show: the
  step now reads the reply differently (a new field, a fixed parser), or you want
  fresh answers on purpose. Bumping it retires every old reply at once. It is also
  the only label in the cost log saying which prompt a call used, since the log
  keeps no text;
- the **request**: system prompt, prompt, output limit, temperature and JSON schema.

The key is written in the cost log; the prompt is not.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from ekb.platform.gateway.models import ChatRequest

KEY_VERSION = 1
"""Bumped if the key's own recipe changes, so old cache entries stop matching."""


def cache_key(
    candidate: str, params: Mapping[str, Any], prompt_version: str, request: ChatRequest
) -> str:
    payload = {
        "key_version": KEY_VERSION,
        "candidate": candidate,
        "params": dict(params),
        "prompt_version": prompt_version,
        "request": request.model_dump(mode="json"),
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
