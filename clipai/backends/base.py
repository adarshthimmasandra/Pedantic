"""Backend interface and output-token sizing.

The application talks to this interface only, which keeps the hotkey pipeline
testable without a network and leaves room for another provider later.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass

# A rough characters-per-token ratio for English prose. Only used to size the
# output budget, never to bill or to reject input, so approximation is fine.
CHARS_PER_TOKEN = 4

# The output of a rewrite is roughly the size of its input, but "make this a
# bulleted list" or "draft a reply" can legitimately grow it.
OUTPUT_HEADROOM = 1.6
MIN_OUTPUT_TOKENS = 256


class BackendError(Exception):
    """Base class for backend failures that already carry a user-facing message."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class TransformRequest:
    """Everything the backend needs for one transformation."""

    text: str
    system_prompt: str
    temperature: float
    model: str
    max_tokens: int


@dataclass(frozen=True)
class TransformResult:
    """A completed transformation and what it cost."""

    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    attempts: int = 1

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


def estimate_tokens(text: str) -> int:
    """Approximate the token count of a string."""
    if not text:
        return 0
    return max(1, -(-len(text) // CHARS_PER_TOKEN))


def size_max_tokens(text: str, ceiling: int) -> int:
    """Choose an output-token budget for the given input.

    The budget scales with the input so a two-word fix does not reserve the
    whole ceiling, but it never drops below :data:`MIN_OUTPUT_TOKENS` and never
    exceeds the configured ceiling.
    """
    if ceiling <= 0:
        raise ValueError("ceiling must be positive")
    scaled = int(estimate_tokens(text) * OUTPUT_HEADROOM)
    floor = min(MIN_OUTPUT_TOKENS, ceiling)
    return max(floor, min(scaled, ceiling))


class Backend(abc.ABC):
    """A text-transformation provider."""

    name = "backend"

    @abc.abstractmethod
    def transform(self, request: TransformRequest) -> TransformResult:
        """Run one transformation. Raises :class:`BackendError` on failure."""

    def validate_credentials(self) -> bool:
        """Cheaply confirm the credentials work. Overridden where supported."""
        return True

    def close(self) -> None:
        """Release transport resources."""
