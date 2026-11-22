FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


# Prefix values a spreadsheet would run as formulas, so they stay plain text.
def neutralize_csv_formula(value):

    text = "" if value is None else str(value)

    if text.startswith(FORMULA_PREFIXES):
        return "'" + text

    return text
