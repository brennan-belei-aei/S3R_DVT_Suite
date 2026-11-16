"""Parsing helpers for comma-separated numeric GUI fields."""
from __future__ import annotations

import math


def format_number_list(values) -> str:
    return ", ".join(f"{float(value):g}" for value in values)


def parse_number_list(text: str, field_name: str, integer_only: bool = False) -> list:
    tokens = [token.strip() for token in str(text).replace(";", ",").split(",")]
    tokens = [token for token in tokens if token]
    if not tokens:
        raise ValueError(f"{field_name} cannot be empty.")
    values = []
    for token in tokens:
        try:
            value = float(token)
        except ValueError as error:
            raise ValueError(
                f"Invalid value {token!r} in {field_name}. Use comma-separated numbers."
            ) from error
        if not math.isfinite(value):
            raise ValueError(f"{field_name} contains a non-finite value.")
        if integer_only:
            if not value.is_integer():
                raise ValueError(f"{field_name} values must be whole numbers.")
            value = int(value)
        values.append(value)
    return values
