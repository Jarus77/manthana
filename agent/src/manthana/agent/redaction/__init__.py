"""Redaction pipeline for the Manthana agent.

Verbatim ECC detection literals live in ``patterns``; the ``Redactor`` applies
them (plus Manthana PII patterns) to text and turns.

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

from .patterns import APPROVAL_COMMANDS, PII_PATTERNS, SECRET_PATTERNS, SENSITIVE_PATHS
from .redactor import PLACEHOLDER, RedactionConfig, Redactor

__all__ = [
    "Redactor",
    "RedactionConfig",
    "PLACEHOLDER",
    "SECRET_PATTERNS",
    "APPROVAL_COMMANDS",
    "SENSITIVE_PATHS",
    "PII_PATTERNS",
]
