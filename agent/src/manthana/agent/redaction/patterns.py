"""Detection patterns for redaction and governance.

The ``SECRET_PATTERNS``, ``APPROVAL_COMMANDS``, and ``SENSITIVE_PATHS`` literals
are copied verbatim from affaan-m/ECC ``scripts/hooks/governance-capture.js``
(MIT, 2026 Affaan Mustafa) and translated from JavaScript regex literals to
Python ``re`` patterns (same source expression, same flags). The original JS
source is reproduced in a comment beside each so the copy is auditable.
``PII_PATTERNS`` are Manthana additions (the ECC_clone_instruction calls for
extending the ECC starter kit with email/phone/PII).

SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import re

# ─── Copied verbatim from ECC governance-capture.js (MIT, 2026 Affaan Mustafa) ───

# Verbatim JS regex sources (ECC governance-capture.js); Python translations below:
#   aws_key         /(?:AKIA|ASIA)[A-Z0-9]{16}/i
#   generic_secret  /(?:secret|password|token|api[_-]?key)\s*[:=]\s*["'][^"']{8,}/i
#   private_key     /-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----/
#   jwt             /eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}/
#   github_token    /gh[pousr]_[A-Za-z0-9_]{36,}/
SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws_key", re.compile(r"(?:AKIA|ASIA)[A-Z0-9]{16}", re.IGNORECASE)),
    (
        "generic_secret",
        re.compile(
            r"""(?:secret|password|token|api[_-]?key)\s*[:=]\s*["'][^"']{8,}""",
            re.IGNORECASE,
        ),
    ),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9_]{36,}")),
]

# JS APPROVAL_COMMANDS: /git\s+push\s+.*--force/, /git\s+reset\s+--hard/,
#    /rm\s+-rf?\s/, /DROP\s+(?:TABLE|DATABASE)/i, /DELETE\s+FROM\s+\w+\s*(?:;|$)/i
APPROVAL_COMMANDS: list[re.Pattern[str]] = [
    re.compile(r"git\s+push\s+.*--force"),
    re.compile(r"git\s+reset\s+--hard"),
    re.compile(r"rm\s+-rf?\s"),
    re.compile(r"DROP\s+(?:TABLE|DATABASE)", re.IGNORECASE),
    re.compile(r"DELETE\s+FROM\s+\w+\s*(?:;|$)", re.IGNORECASE),
]

# JS SENSITIVE_PATHS: /\.env(?:\.|$)/, /credentials/i, /secrets?\./i,
#    /\.pem$/, /\.key$/, /id_rsa/
SENSITIVE_PATHS: list[re.Pattern[str]] = [
    re.compile(r"\.env(?:\.|$)"),
    re.compile(r"credentials", re.IGNORECASE),
    re.compile(r"secrets?\.", re.IGNORECASE),
    re.compile(r"\.pem$"),
    re.compile(r"\.key$"),
    re.compile(r"id_rsa"),
]

# ─── end ECC verbatim ───

# Manthana PII additions (best-effort scrubbing on release; configurable).
PII_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    (
        "phone",
        re.compile(r"(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}"),
    ),
]


__all__ = ["SECRET_PATTERNS", "APPROVAL_COMMANDS", "SENSITIVE_PATHS", "PII_PATTERNS"]
