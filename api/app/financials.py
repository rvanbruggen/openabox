"""Key financial metrics from a filed annual account.

The balance sheet and P&L are in the XBRL like everything else, but they are
dimensionally encoded — reconstructing "turnover" would mean mapping a
combination of `bas`/`ntr`/`prd` members for every line item. The NBB already
publishes that flattening itself: the Consult CSV export gives the standard
Belgian *rubriek* codes directly, two columns, `code,value`. That is the source
used here, because a documented numbering scheme is a far more stable contract
than a reverse-engineered dimensional one.

**Rubriek codes are not comparable across filing models**, which is the trap
this module exists to avoid. Verified on live filings:

| Code | Colruyt (VOL) | Achilles Dott (VKT) | CGMI (VKT, capital-less) |
|---|---|---|---|
| `70` turnover | present | present | absent |
| `9900` | absent | present — but means *gross margin* here, not operating result |
| `9901` operating result | present | present | present |
| `10` capital | present | absent | absent |

So `9900` is deliberately not used: in the full scheme it is unused and in the
abbreviated scheme it is a different measure, so charting it across models
would silently compare two unlike things. Only codes that mean the same thing
in every scheme are treated as comparable.
"""

import csv
import io
import logging

logger = logging.getLogger(__name__)

# Metric -> (rubriek code, label, comparable across models?)
#
# "Comparable" drives whether a series is safe to chart across years in which
# the company changed filing model — which happens when a company grows past a
# size threshold, and would otherwise produce a fake step change.
METRICS = {
    "turnover":        ("70",    "Turnover", True),
    "operating_result": ("9901", "Operating result", True),
    "result":          ("9904",  "Result for the period", True),
    "equity":          ("10/15", "Equity", True),
    "liabilities":     ("17/49", "Liabilities", True),
    "total_assets":    ("20/58", "Total assets", True),
    "capital":         ("10",    "Capital / contribution", True),
    "staff_costs":     ("62",    "Staff costs", True),
    "employees_fte":   ("9087",  "Employees (FTE)", True),
}

# Header rows in the CSV that are not rubriek codes.
_HEADER_PREFIXES = ("Reference", "Entity", "Accounting", "Model", "Deposit",
                    "Language", "Currency", "General", "Liquidation",
                    "Correction", "Legal")


def parse_csv(raw: bytes) -> dict[str, float]:
    """Turn the NBB CSV export into {rubriek code: value}.

    Non-numeric rows (the identification header) are dropped rather than
    coerced, so a metric is either a real number or absent — never zero
    standing in for "not filed", which would plot as a genuine collapse.
    """
    text = raw.decode("utf-8-sig", errors="replace")
    codes: dict[str, float] = {}
    for row in csv.reader(io.StringIO(text)):
        if len(row) != 2:
            continue
        code, value = row[0].strip(), row[1].strip()
        if not code or code.startswith(_HEADER_PREFIXES):
            continue
        try:
            codes[code] = float(value)
        except ValueError:
            continue
    return codes


def extract(raw: bytes) -> dict[str, float]:
    """Pull the curated metric set out of one filing's CSV."""
    codes = parse_csv(raw)
    out = {}
    for name, (code, _label, _comparable) in METRICS.items():
        if code in codes:
            out[name] = codes[code]
    return out


def derive(metrics: dict[str, float]) -> dict[str, float]:
    """Ratios worth having that are not filed directly.

    Equity ratio is the one that answers "how much of this company is actually
    owned rather than borrowed" — the question behind looking at capital
    structure at all.
    """
    out = dict(metrics)
    equity, assets = metrics.get("equity"), metrics.get("total_assets")
    if equity is not None and assets:
        out["equity_ratio"] = round(equity / assets * 100, 2)
    if metrics.get("turnover") and metrics.get("result") is not None:
        out["net_margin"] = round(metrics["result"] / metrics["turnover"] * 100, 2)
    return out
