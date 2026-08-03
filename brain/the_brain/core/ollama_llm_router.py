"""OpenFang-backed token classifier kept under its historical module name.

The Layer 4 token path previously sent requests directly to a local Ollama
endpoint. It now uses the Brain's existing ``brain_fast_reasoning`` role, so
the model, endpoint, retry budget, and fail-closed behavior come from the
shared OpenFang client factory.
"""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Dict, List, Optional

from vibemind_shared import OpenFangUnavailable, get_client_sync, get_model


_TOKEN_CLASSIFICATION_ROLE = "brain_fast_reasoning"
_VALID_CATEGORIES = {
    "ACTION", "EXPLORATION", "CONSTRAINT", "TEMPORAL", "NEGATION",
    "CONFIRMATION", "UNCERTAINTY", "CONTENT", "FILLER", "PUNCTUATION",
}


@dataclass
class OllamaConfig:
    """Compatibility settings retained for existing Layer 4 callers.

    Transport settings are deliberately not used: OpenFang configuration in
    ``llm_config.yml`` is the sole endpoint, retry, and timeout authority.
    """

    host: str = "localhost"
    port: int = 11434
    model: str = "llama3.2:1b"
    timeout: float = 5.0
    retry_count: int = 2
    batch_size: int = 5


class OllamaLLMRouter:
    """Classify Layer 4 tokens through the fail-closed OpenFang boundary."""

    def __init__(self, config: Optional[OllamaConfig] = None):
        self.config = config or OllamaConfig()
        try:
            self.model = self._openfang_model()
            self.client = get_client_sync(_TOKEN_CLASSIFICATION_ROLE)
        except OpenFangUnavailable:
            raise
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            raise OpenFangUnavailable(
                "OpenFang token-classification configuration is unavailable"
            ) from exc
        self.base_url = "openfang"

        self.total_requests = 0
        self.successful_requests = 0
        self.failed_requests = 0
        self.total_tokens_classified = 0
        self.avg_latency_ms = 0.0

        # Client construction validates configuration. It does not make a
        # provider-live health claim or probe a direct provider endpoint.
        self.is_available = True
        self.available_models: List[str] = [self.model]

    @staticmethod
    def _openfang_model() -> str:
        model = str(get_model(_TOKEN_CLASSIFICATION_ROLE))
        if not model.startswith("openfang:"):
            raise ValueError(
                "brain_fast_reasoning is not configured for OpenFang"
            )
        return model

    def _verify_connection(self) -> bool:
        """Return configured availability without performing a direct probe."""
        return self.is_available

    def _load_available_models(self) -> None:
        """Expose the configured model without querying a provider directly."""
        self.available_models = [self.model]

    def _completion_text(
        self, prompt: str, *, temperature: float, max_tokens: int
    ) -> str:
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except OpenFangUnavailable:
            self.failed_requests += 1
            raise
        except Exception as exc:
            self.failed_requests += 1
            raise OpenFangUnavailable(
                "OpenFang token classification failed; no provider fallback is allowed"
            ) from exc

        choices = getattr(response, "choices", [])
        if not choices:
            self.failed_requests += 1
            raise OpenFangUnavailable(
                "OpenFang token classification returned no choices"
            )
        content = getattr(getattr(choices[0], "message", None), "content", None)
        if not isinstance(content, str):
            self.failed_requests += 1
            raise OpenFangUnavailable(
                "OpenFang token classification returned no text"
            )
        return content

    def _record_success(self, start_time: float) -> float:
        latency_ms = (time.perf_counter() - start_time) * 1000
        self.successful_requests += 1
        self.total_tokens_classified += 1
        self._update_latency(latency_ms)
        return latency_ms

    def classify_token(self, token: str) -> Dict[str, Any]:
        prompt = f'''Classify this token into exactly one category:
Token: "{token}"

Categories:
- ACTION (deploy, run, execute, start, build, create, delete)
- EXPLORATION (or, maybe, alternatively, perhaps, could)
- CONSTRAINT (not, never, must, only, limit)
- TEMPORAL (then, after, before, when, until, while)
- NEGATION (no, cancel, stop, abort, deny)
- CONFIRMATION (yes, correct, ok, exactly, absolutely)
- UNCERTAINTY (might, possibly, probably, seems)
- CONTENT (other semantic content like nouns, names)
- FILLER (the, a, is, and, for, to)
- PUNCTUATION (., ,, !, ?, ;)

Reply with ONLY the category name, nothing else.'''

        self.total_requests += 1
        start_time = time.perf_counter()
        category = self._parse_category(
            self._completion_text(prompt, temperature=0.1, max_tokens=15)
        )
        latency_ms = self._record_success(start_time)
        return {
            "token": token,
            "class": category,
            "confidence": 0.85,
            "source": "openfang",
            "latency_ms": latency_ms,
        }

    def _parse_category(self, response: str) -> str:
        response = response.upper().strip()
        if response in _VALID_CATEGORIES:
            return response
        for category in _VALID_CATEGORIES:
            if category in response:
                return category
        return "CONTENT"

    def _update_latency(self, latency_ms: float) -> None:
        if self.successful_requests == 1:
            self.avg_latency_ms = latency_ms
        else:
            self.avg_latency_ms = 0.2 * latency_ms + 0.8 * self.avg_latency_ms

    def classify_batch(self, tokens: List[str]) -> List[Dict[str, Any]]:
        return [self.classify_token(token) for token in tokens]

    def classify_with_context(self, tokens: List[str], target_idx: int) -> Dict[str, Any]:
        if not 0 <= target_idx < len(tokens):
            raise ValueError("target_idx must reference a token")

        context_start = max(0, target_idx - 2)
        context_end = min(len(tokens), target_idx + 3)
        context = " ".join(tokens[context_start:context_end])
        target_token = tokens[target_idx]
        prompt = f'''Classify the token "{target_token}" in this context:

Context: "{context}"
Target token: "{target_token}"

Use exactly one of ACTION, EXPLORATION, CONSTRAINT, TEMPORAL, NEGATION,
CONFIRMATION, UNCERTAINTY, CONTENT, FILLER, or PUNCTUATION. Reply with only
the category name.'''

        self.total_requests += 1
        start_time = time.perf_counter()
        category = self._parse_category(
            self._completion_text(prompt, temperature=0.1, max_tokens=15)
        )
        latency_ms = self._record_success(start_time)
        return {
            "token": target_token,
            "class": category,
            "confidence": 0.9,
            "source": "openfang",
            "context": context,
            "context_used": True,
            "latency_ms": latency_ms,
        }

    def classify_sequence(self, tokens: List[str]) -> List[Dict[str, Any]]:
        return [self.classify_with_context(tokens, index) for index in range(len(tokens))]

    def route(self, function: str, prompt: str, **kwargs: Any) -> str:
        del function
        self.total_requests += 1
        start_time = time.perf_counter()
        response = self._completion_text(
            prompt,
            temperature=float(kwargs.get("temperature", 0.7)),
            max_tokens=int(kwargs.get("max_tokens", 100)),
        )
        self._record_success(start_time)
        return response

    def get_statistics(self) -> Dict[str, Any]:
        return {
            "is_available": self.is_available,
            "base_url": self.base_url,
            "model": self.model,
            "available_models": self.available_models,
            "total_requests": self.total_requests,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "success_rate": self.successful_requests / max(1, self.total_requests),
            "total_tokens_classified": self.total_tokens_classified,
            "avg_latency_ms": self.avg_latency_ms,
        }

    def health_check(self) -> Dict[str, Any]:
        return {
            "status": "configured" if self.is_available else "unavailable",
            "is_available": self.is_available,
            "base_url": self.base_url,
            "model": self.model,
        }
