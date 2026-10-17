"""Unit tests for the Granite number check (no model needed).
Run:  python tests/test_brief.py      (or: pytest tests/)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.brief import check_numbers  # noqa: E402

PAYLOAD = {"station": "Gorton", "failure_rate_pct": 19, "failure_ci_pct": [3, 36],
           "adjusted_test": {"rho": -0.14, "ci": [-0.37, 0.08]}, "rank_worst": 64,
           "caveats": ["The data covers 10 weekdays (5 to 16 October 2026)."]}


def test_numbers_from_input_pass():
    ok, bad = check_numbers("At Gorton 19% of trains failed (interval 3% to 36%), ranking 64th; "
                            "rho was -0.14 (95% interval −0.37 to 0.08) over 10 weekdays.", PAYLOAD)
    assert ok, bad


def test_invented_number_fails():
    ok, bad = check_numbers("About 1 in 5 trains failed, 19% overall.", PAYLOAD)
    assert not ok and bad == [1.0]


def test_rounded_or_computed_number_fails():
    ok, bad = check_numbers("The rate was 19.4%, 17 points better than expected.", PAYLOAD)
    assert not ok and bad == [17.0, 19.4]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"PASS {name}")
