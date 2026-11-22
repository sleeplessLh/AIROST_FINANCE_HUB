"""CSV formula injection unit tests (B5).
Run: python -m pytest tests/test_csv_safety.py -v
These tests do not use the database or the Flask app.
"""

import pytest

from utils.csv_safety import neutralize_csv_formula


@pytest.mark.parametrize(
    "dangerous",
    [
        "=1+1",
        "+SUM(1,1)",
        "-1+2",
        "@SUM(1,1)",
        "\t=1+1",
        "\r=1+1",
        '=HYPERLINK("http://evil.example","Click")',
    ],
)
def test_formula_prefixes_are_neutralized(dangerous):
    result = neutralize_csv_formula(dangerous)
    assert result == "'" + dangerous


@pytest.mark.parametrize(
    "safe",
    [
        "Alice Tan",
        "AIROST Annual Dinner 2026",
        "Ahmad bin Ali",
        "1+1=2",
        "",
    ],
)
def test_normal_text_is_unchanged(safe):
    assert neutralize_csv_formula(safe) == safe


def test_none_becomes_empty_string():
    assert neutralize_csv_formula(None) == ""


def test_non_string_values_are_converted():
    assert neutralize_csv_formula(123) == "123"
    assert neutralize_csv_formula(-5) == "'-5"
