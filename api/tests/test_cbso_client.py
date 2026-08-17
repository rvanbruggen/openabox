"""Citation-link tests for the CBSO client.

The panel presents derived figures, so it links back to the filings they came
from. A dead citation link is worse than no link — it implies the source was
checked. These tests guard the one case where a link cannot be built.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.cbso_client import deposit_urls, enterprise_url  # noqa: E402

GUID = "215f9683-a3c6-11f0-81e6-4924d7e6c7b2"


def test_consult_guid_yields_all_three_formats():
    urls = deposit_urls(GUID)
    assert set(urls) == {"pdf", "xbrl", "csv"}
    assert urls["pdf"].endswith(f"/deposits/pdf/{GUID}")
    assert urls["xbrl"].endswith(f"/deposits/xbrl/{GUID}")
    assert urls["csv"].endswith(f"/deposits/consult/csv/{GUID}")
    assert all(u.startswith("https://consult.cbso.nbb.be/") for u in urls.values())


def test_official_api_reference_number_yields_no_public_link():
    """The two backends identify the same filing differently.

    Consult addresses a deposit by GUID; the official web services return a
    reference number like "2025-00539072". Only the GUID resolves on the public
    portal, so a reference number must produce no link rather than a URL that
    404s — the UI falls back to the company's Consult page.
    """
    assert deposit_urls("2025-00539072") is None


def test_missing_or_malformed_ids_yield_no_link():
    assert deposit_urls(None) is None
    assert deposit_urls("") is None
    assert deposit_urls("not-a-guid") is None


def test_enterprise_url_is_always_available_as_a_fallback():
    assert enterprise_url("0400378485") == (
        "https://consult.cbso.nbb.be/consult-enterprise/0400378485"
    )


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
    print(f"\n{'FAILED' if failures else 'All CBSO client tests passed'} ({failures} failure(s))")
    sys.exit(1 if failures else 0)
