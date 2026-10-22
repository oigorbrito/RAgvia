from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any


def decimal_amount(value: Any) -> Decimal | None:
    """Parse deterministic monetary values without guessing malformed formats."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None

    rendered = str(value).strip().replace("\u00a0", " ")
    rendered = re.sub(r"^(?:R\$|BRL)\s*", "", rendered, flags=re.IGNORECASE)
    rendered = re.sub(r"\s+", "", rendered)
    if not rendered:
        return None

    if re.fullmatch(r"-?\d{1,3}(?:\.\d{3})+(?:,\d+)?", rendered):
        rendered = rendered.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"-?\d+,\d+", rendered):
        rendered = rendered.replace(",", ".")
    elif not re.fullmatch(r"-?\d+(?:\.\d+)?", rendered):
        return None

    try:
        return Decimal(rendered)
    except InvalidOperation:
        return None
