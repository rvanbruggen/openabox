"""Financial-metric extraction tests.

The failure mode these guard against is silent and plausible: a metric that is
absent from a filing must stay absent, never become 0. A zero plots as a real
collapse in turnover or equity, and nothing downstream can tell it apart from
a company that genuinely earned nothing.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.financials import METRICS, derive, extract, parse_csv  # noqa: E402

# Shaped like a real NBB export: an identification header, then code,value rows.
FULL_MODEL = b"""
"Reference number","2025-00539072"
"Entity number","0400378485"
"Model code","m02-f"
"70","866398904.13"
"9901","747964.42"
"9904","2146487911.64"
"10/15","6529762047.59"
"17/49","2110825152.82"
"20/58","8641113802.52"
"10","384689455.45"
"9087","4127.9"
"""

# An abbreviated filing: no turnover, no capital, and 9900 present — where it
# means gross margin, not operating result.
ABBREVIATED = b"""
"Reference number","2025-00111111"
"Model code","m81-f"
"9900","-6975.49"
"9901","-7225.49"
"9904","288333.88"
"10/15","9093279.90"
"17/49","1229.27"
"20/58","9094509.17"
"""


def test_header_rows_are_not_read_as_rubriek_codes():
    codes = parse_csv(FULL_MODEL)
    assert "Reference number" not in codes
    assert "Entity number" not in codes
    assert codes["70"] == 866398904.13


def test_full_model_metrics_are_extracted():
    m = extract(FULL_MODEL)
    assert m["turnover"] == 866398904.13
    assert m["result"] == 2146487911.64
    assert m["equity"] == 6529762047.59
    assert m["capital"] == 384689455.45
    assert m["employees_fte"] == 4127.9


def test_undisclosed_metrics_are_absent_not_zero():
    """The whole point: a missing bar must mean "not filed", never "zero"."""
    m = extract(ABBREVIATED)
    assert "turnover" not in m
    assert "capital" not in m
    assert "employees_fte" not in m
    # ...while the ones that are filed still come through.
    assert m["result"] == 288333.88
    assert m["equity"] == 9093279.90


def test_gross_margin_is_never_read_as_operating_result():
    """Code 9900 is unused in the full scheme and means gross margin in the
    abbreviated one, so charting it across models would compare unlike things.
    Only 9901 is treated as the operating result."""
    assert "9900" not in {code for code, _l, _c in METRICS.values()}
    assert extract(ABBREVIATED)["operating_result"] == -7225.49


def test_equity_ratio_is_derived_from_equity_over_assets():
    d = derive(extract(ABBREVIATED))
    assert d["equity_ratio"] == 99.99


def test_derived_ratios_are_skipped_when_the_inputs_are_missing():
    d = derive(extract(ABBREVIATED))
    assert "net_margin" not in d  # no turnover filed, so no margin


def test_zero_assets_do_not_raise():
    d = derive({"equity": 100.0, "total_assets": 0.0})
    assert "equity_ratio" not in d


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS  {name}")
            except AssertionError as exc:
                failures += 1
                print(f"  FAIL  {name}: {exc}")
    print(f"\n{'FAILED' if failures else 'All financials tests passed'} ({failures} failure(s))")
    sys.exit(1 if failures else 0)
