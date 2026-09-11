import re
from typing import Optional


def sanitize_dtmf_digits(raw_value: Optional[str]) -> str:
    """
    Keep only DTMF-safe characters from a provided extension value.
    Supported characters: 0-9, *, #.
    """
    if raw_value in [None, ""]:
        return ""

    return "".join(ch for ch in str(raw_value).strip() if ch in "0123456789*#")


def resolve_extension_digits(raw_value: Optional[str]) -> str:
    """
    Resolve a 4-digit extension from raw user data.
    If more than 4 digits are present, prefer the trailing 4 digits.
    """
    if raw_value in [None, ""]:
        return ""

    raw_text = str(raw_value).strip()
    decimal_extension_match = re.fullmatch(r"(\d+)\.0+", raw_text)
    if decimal_extension_match:
        digits_only = decimal_extension_match.group(1)
    else:
        embedded_decimal_extensions = re.findall(r"(?<!\d)(\d{4})\.0+(?!\d)", raw_text)
        if embedded_decimal_extensions:
            return embedded_decimal_extensions[-1]

        cleaned = sanitize_dtmf_digits(raw_text)
        digits_only = "".join(ch for ch in cleaned if ch.isdigit())

    if len(digits_only) == 4:
        return digits_only

    if len(digits_only) > 4:
        return digits_only[-4:]

    return ""