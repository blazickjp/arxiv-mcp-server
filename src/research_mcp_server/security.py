"""Security utilities for MCP tool input/output validation.

Provides sanitization, validation, and size-checking helpers to guard
against prompt injection, cross-tool manipulation, and oversized responses.
"""

import logging
import re
from typing import List

logger = logging.getLogger(__name__)


class SecurityViolation(Exception):
    """Raised when a security check fails hard enough to abort."""


# ---------------------------------------------------------------------------
# Sanitize tool responses
# ---------------------------------------------------------------------------

# HTML / script tags (case-insensitive, handles attributes)
_SCRIPT_TAG_RE = re.compile(r"<\s*script[^>]*>.*?<\s*/\s*script\s*>", re.IGNORECASE | re.DOTALL)
_HTML_TAG_RE = re.compile(r"<\s*/?\s*[a-zA-Z][a-zA-Z0-9]*[^>]*>", re.IGNORECASE)

# Embedded system-prompt-style injections
_SYSTEM_INJECTION_RE = re.compile(
    r"\[SYSTEM\s*:.*?\]",
    re.IGNORECASE | re.DOTALL,
)
_IGNORE_INSTRUCTIONS_RE = re.compile(
    r"ignore\s+previous\s+instructions",
    re.IGNORECASE,
)
_FORGET_INSTRUCTIONS_RE = re.compile(
    r"forget\s+(all\s+)?(previous|prior|above)\s+instructions",
    re.IGNORECASE,
)
_NEW_INSTRUCTIONS_RE = re.compile(
    r"new\s+instructions\s*:",
    re.IGNORECASE,
)


def sanitize_tool_response(text: str) -> str:
    """Strip HTML/script tags and prompt-injection patterns from *text*.

    Returns the cleaned string.  Logs a warning for every pattern removed.
    """
    cleaned = text

    for pattern, label in (
        (_SCRIPT_TAG_RE, "script tag"),
        (_HTML_TAG_RE, "HTML tag"),
        (_SYSTEM_INJECTION_RE, "embedded system instruction"),
        (_IGNORE_INSTRUCTIONS_RE, "ignore-instructions pattern"),
        (_FORGET_INSTRUCTIONS_RE, "forget-instructions pattern"),
        (_NEW_INSTRUCTIONS_RE, "new-instructions pattern"),
    ):
        if pattern.search(cleaned):
            logger.warning("Sanitizer stripped %s from tool response", label)
            cleaned = pattern.sub("", cleaned)

    return cleaned


# ---------------------------------------------------------------------------
# Validate tool descriptions
# ---------------------------------------------------------------------------

_CROSS_TOOL_RE = re.compile(
    r"always\s+call\s+\w+",
    re.IGNORECASE,
)
_AFTER_RESULTS_CALL_RE = re.compile(
    r"after\s+results?\s*,?\s*(always\s+)?call\s+\w+",
    re.IGNORECASE,
)
_IMPORTANT_INSTRUCTION_RE = re.compile(
    r"IMPORTANT\s*:\s*.+",
)
_CREDENTIAL_RE = re.compile(
    r"(user_token|api_key|password|secret|credential)",
    re.IGNORECASE,
)
_DO_NOT_SHOW_RE = re.compile(
    r"do\s+not\s+(show|reveal|display|expose)",
    re.IGNORECASE,
)
_HIDDEN_INSTRUCTION_RE = re.compile(
    r"(must\s+not\s+tell|never\s+reveal|hide\s+this)",
    re.IGNORECASE,
)


def validate_tool_description(description: str) -> List[str]:
    """Check a tool description for suspicious patterns.

    Returns a list of human-readable warnings (empty means clean).
    """
    warnings: List[str] = []

    for pattern, message in (
        (_CROSS_TOOL_RE, "Cross-tool reference detected"),
        (_AFTER_RESULTS_CALL_RE, "Cross-tool reference detected"),
        (_IMPORTANT_INSTRUCTION_RE, "Embedded instruction detected"),
        (_CREDENTIAL_RE, "Credential reference detected"),
        (_DO_NOT_SHOW_RE, "Hidden instruction detected"),
        (_HIDDEN_INSTRUCTION_RE, "Hidden instruction detected"),
    ):
        if pattern.search(description):
            logger.warning("Description validation: %s", message)
            if message not in warnings:
                warnings.append(message)

    return warnings


# ---------------------------------------------------------------------------
# Response size check
# ---------------------------------------------------------------------------


def check_response_size(text: str, max_bytes: int = 500_000) -> bool:
    """Return True if *text* is within *max_bytes*, False otherwise."""
    size = len(text.encode("utf-8"))
    if size > max_bytes:
        logger.warning(
            "Response size %d bytes exceeds limit of %d bytes",
            size,
            max_bytes,
        )
        return False
    return True
