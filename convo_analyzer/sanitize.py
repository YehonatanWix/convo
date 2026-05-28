from __future__ import annotations
import os
import re

HOME = os.path.expanduser("~")

PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"AKIA[0-9A-Z]{16}"),                          "[REDACTED:aws]"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),                "[REDACTED:github]"),
    (re.compile(r"Bearer\s+[A-Za-z0-9._\-]+"),                 "Bearer [REDACTED:bearer]"),
    (re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"), "[REDACTED:jwt]"),
    (re.compile(r"(?m)^([A-Z][A-Z0-9_]*(?:PASSWORD|SECRET|TOKEN|KEY))=.+$"), r"\1=[REDACTED:env]"),
]

def scrub(text: str) -> str:
    if not text:
        return text
    out = text
    for rx, repl in PATTERNS:
        out = rx.sub(repl, out)
    return out

def normalize_path(s: str) -> str:
    if HOME in s:
        return s.replace(HOME, "~")
    return s
