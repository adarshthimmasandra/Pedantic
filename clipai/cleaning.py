"""Input and output sanitization.

Input cleaning protects the request: clipboard text arrives with stray control
characters, invisible formatting marks, and inconsistent line endings that waste
tokens and confuse the model.

Output cleaning protects the paste: models sometimes wrap an answer in quotes, a
fenced code block, or a conversational preamble. Pasting that verbatim over the
user's selection would be wrong, so those wrappers are removed.
"""

from __future__ import annotations

import re
import unicodedata

# Zero-width and directional marks that are invisible but still consume tokens.
_INVISIBLE: dict[int, str | None] = {
    0x00AD: None,  # soft hyphen
    0x200B: None,  # zero width space
    0x200C: None,  # zero width non-joiner
    0x200D: None,  # zero width joiner
    0x200E: None,  # left-to-right mark
    0x200F: None,  # right-to-left mark
    0x202A: None,
    0x202B: None,
    0x202C: None,
    0x202D: None,
    0x202E: None,  # bidi embedding controls
    0x2060: None,  # word joiner
    0xFEFF: None,  # byte order mark
    0x2028: "\n",  # line separator
    0x2029: "\n",  # paragraph separator
}

_ALLOWED_CONTROL = frozenset({"\t", "\n"})

_TRAILING_SPACE = re.compile(r"[ \t]+$", re.MULTILINE)
_EXCESS_BLANK_LINES = re.compile(r"\n{4,}")

_CODE_FENCE = re.compile(r"\A\s*```[^\n`]*\n(?P<body>.*?)\n?```\s*\Z", re.DOTALL)

# "Here is the corrected text:", "Sure! Here's the revised version -", ...
_PREAMBLE = re.compile(
    r"""\A[ \t]*
    (?:(?:sure|certainly|of\s+course|absolutely|okay|ok)[,!.]?[ \t]*)?
    (?:here(?:'s|\s+is|\s+are)|below\s+is|this\s+is)[ \t]+
    (?:the|your|a|an)?[ \t]*
    (?:corrected|revised|rewritten|edited|updated|fixed|polished|concise|
       shortened|formal|professional|improved|final|requested|bulleted)?[ \t]*
    (?:version|text|message|reply|response|draft|result|email|output|list)?
    [ \t]*(?:of[ \t]+(?:the|your)[ \t]+\w+)?[ \t]*[:\-\u2013\u2014][ \t]*\n+
    """,
    re.IGNORECASE | re.VERBOSE,
)

_QUOTE_PAIRS = (
    ('"', '"'),
    ("'", "'"),
    ("\u201c", "\u201d"),
    ("\u2018", "\u2019"),
    ("\u00ab", "\u00bb"),
)


def strip_invisible(text: str) -> str:
    """Remove zero-width and bidi control characters."""
    return text.translate(_INVISIBLE)


def normalize_newlines(text: str) -> str:
    """Convert CRLF and lone CR line endings to LF."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def strip_control_characters(text: str) -> str:
    """Drop control characters other than tab and newline."""
    return "".join(
        ch for ch in text if ch in _ALLOWED_CONTROL or unicodedata.category(ch) != "Cc"
    )


def sanitize_input(text: str) -> str:
    """Prepare clipboard text for the model.

    Line endings become LF, invisible marks and control characters are removed,
    trailing spaces are trimmed, and long runs of blank lines are collapsed.
    Leading and trailing whitespace is stripped because the paste replaces a
    selection whose own edges the user already chose.
    """
    if not text:
        return ""
    cleaned = normalize_newlines(text)
    cleaned = strip_invisible(cleaned)
    cleaned = strip_control_characters(cleaned)
    cleaned = _TRAILING_SPACE.sub("", cleaned)
    cleaned = _EXCESS_BLANK_LINES.sub("\n\n\n", cleaned)
    return cleaned.strip()


def has_transformable_text(text: str) -> bool:
    """True when the text contains at least one letter or digit.

    Whitespace, punctuation, or a lone bullet character is not worth an API
    request and would usually mean the copy captured nothing useful.
    """
    return any(ch.isalnum() for ch in text)


def strip_code_fence(text: str) -> str:
    """Unwrap a response that is entirely inside one fenced code block."""
    match = _CODE_FENCE.match(text)
    if match:
        return match.group("body")
    return text


def strip_preamble(text: str) -> str:
    """Remove a leading conversational lead-in followed by a line break."""
    return _PREAMBLE.sub("", text, count=1)


def strip_wrapping_quotes(text: str) -> str:
    """Remove one pair of quotes that encloses the whole response.

    Quotes are only removed when the matching closing quote is at the very end
    and the same quote character does not appear inside, so a genuinely quoted
    passage is preserved.
    """
    stripped = text.strip()
    if len(stripped) < 2:
        return text
    for opening, closing in _QUOTE_PAIRS:
        if stripped.startswith(opening) and stripped.endswith(closing):
            inner = stripped[len(opening) : -len(closing)]
            if opening not in inner and closing not in inner:
                return inner
    return text


def clean_output(text: str) -> str:
    """Turn a raw model response into text that is safe to paste."""
    if not text:
        return ""
    cleaned = normalize_newlines(text)
    cleaned = strip_invisible(cleaned)
    cleaned = strip_control_characters(cleaned)
    cleaned = strip_code_fence(cleaned)
    cleaned = strip_preamble(cleaned)
    cleaned = strip_wrapping_quotes(cleaned)
    cleaned = _TRAILING_SPACE.sub("", cleaned)
    return cleaned.strip()


def preview(text: str, limit: int = 80) -> str:
    """Single-line abbreviation used in logs, notifications, and history."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "\u2026"
