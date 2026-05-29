"""Vietnamese phone normalization + misc utilities."""
from __future__ import annotations

import re

_PHONE_RE = re.compile(r"\D")


def normalize_vn_phone(phone: str) -> str:
    """
    Normalize Vietnamese phone to "0xxxxxxxxx" (10 digits, leading 0).
    Accepts +84, 84, 0 prefixes. Returns empty string if invalid.
    """
    if not phone:
        return ""
    digits = _PHONE_RE.sub("", phone)
    if digits.startswith("84"):
        digits = "0" + digits[2:]
    elif not digits.startswith("0"):
        digits = "0" + digits
    if len(digits) not in (10, 11):
        return ""
    return digits


def safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
