"""
Input sanitization utilities for user-supplied text.

Strips dangerous HTML/script tags from free-text fields before
they are stored in the database or rendered in the frontend.

This is a defense-in-depth layer — the frontend should also
escape output, but we sanitize on the server side to prevent
stored XSS attacks.
"""

import re
from typing import Optional


# Tags that are always stripped (case-insensitive)
_DANGEROUS_TAGS = re.compile(
    r"<\s*/?\s*(?:script|iframe|object|embed|form|input|button|link|meta|style|svg|math|base)"
    r"[^>]*>",
    re.IGNORECASE,
)

# Event handlers in attributes (onclick, onerror, onload, etc.)
_EVENT_HANDLERS = re.compile(
    r"\s+on\w+\s*=\s*[\"'][^\"']*[\"']",
    re.IGNORECASE,
)

# javascript: and data: URIs in href/src attributes
_DANGEROUS_URIS = re.compile(
    r"(?:href|src|action)\s*=\s*[\"']\s*(?:javascript|data|vbscript)\s*:",
    re.IGNORECASE,
)

# HTML entities that could be used for obfuscation
_HTML_ENTITY_SCRIPT = re.compile(
    r"&#x?[0-9a-fA-F]+;",
    re.IGNORECASE,
)


def sanitize_text(text: Optional[str], max_length: int = 10000) -> Optional[str]:
    """
    Sanitize free-text input by removing dangerous HTML constructs.

    - Strips <script>, <iframe>, <object>, <embed>, <form> etc.
    - Removes event handler attributes (onclick, onerror, etc.)
    - Removes javascript: and data: URIs
    - Truncates to max_length to prevent abuse

    Returns None if input is None, empty string if input is empty.
    """
    if text is None:
        return None

    if not isinstance(text, str):
        text = str(text)

    # Truncate to prevent oversized payloads
    text = text[:max_length]

    # Strip dangerous tags
    text = _DANGEROUS_TAGS.sub("", text)

    # Strip event handlers
    text = _EVENT_HANDLERS.sub("", text)

    # Strip dangerous URIs
    text = _DANGEROUS_URIS.sub("", text)

    return text.strip()


def sanitize_identifier(text: Optional[str], max_length: int = 100) -> Optional[str]:
    """
    Sanitize identifiers (tenant_id, rule_type, model_name, etc.).

    Only allows alphanumeric, hyphens, underscores, dots, and slashes.
    """
    if text is None:
        return None

    if not isinstance(text, str):
        text = str(text)

    text = text[:max_length].strip()

    # Allow only safe characters for identifiers
    text = re.sub(r"[^a-zA-Z0-9_\-./: ]", "", text)

    return text if text else None
