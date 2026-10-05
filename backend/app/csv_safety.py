"""Spreadsheet formula-injection (CSV injection) protection, shared by import and export."""

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def is_formula(value: str) -> bool:
    return value.startswith(FORMULA_PREFIXES)


def safe_cell(value: object) -> str:
    """Neutralize a cell for export by prefixing formula-like text with a quote."""
    text = "" if value is None else str(value)
    return "'" + text if is_formula(text) else text
