"""Address canonicalisation tests.

If these break, shared-address detection silently degrades: companies at the
same building stop meeting on one Address node and the overlap queries quietly
return fewer results rather than failing loudly. Hence the emphasis here.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.address import address_key, normalise_number, normalise_street  # noqa: E402


def test_case_punctuation_and_accents_fold_together():
    variants = [
        {"street": "Rue de l'Église", "street_number": "12", "post_code": "1000"},
        {"street": "rue de l eglise", "street_number": "12", "post_code": "1000"},
        {"street": "RUE DE L'EGLISE", "street_number": "12", "post_code": "1000"},
    ]
    keys = {address_key(v) for v in variants}
    assert len(keys) == 1, keys


def test_street_type_abbreviations_fold():
    a = address_key({"street": "Kerkstr.", "street_number": "5", "post_code": "2000"})
    b = address_key({"street": "Kerkstraat", "street_number": "5", "post_code": "2000"})
    assert a == b == "BE|2000|kerkstraat|5"


def test_house_number_variants():
    assert normalise_number("5") == "5"
    assert normalise_number("05") == "5"
    assert normalise_number("5A") == "5a"
    assert normalise_number("5 a") == "5a"
    assert normalise_number("5-7") == "5"


def test_box_is_excluded_so_units_share_a_building():
    unit_a = {"street": "Havenlaan", "street_number": "86", "box": "1",
              "post_code": "1000", "city": "Brussel"}
    unit_b = {"street": "Havenlaan", "street_number": "86", "box": "42",
              "post_code": "1000", "city": "Brussel"}
    assert address_key(unit_a) == address_key(unit_b)


def test_foreign_addresses_keep_their_country():
    key = address_key({
        "street": "Rue F.W. Raiffeisen", "street_number": "5",
        "post_code": "2411", "city": "Luxembourg", "country_code": "LU",
    })
    assert key.startswith("LU|2411|")


def test_insufficient_address_returns_none():
    assert address_key({}) is None
    assert address_key({"city": "Brussel"}) is None          # no street
    assert address_key({"street": "Kerkstraat"}) is None      # no locality anchor


def test_missing_house_number_still_keys():
    # Some register entries genuinely have no number; they should still merge.
    key = address_key({"street": "Grote Markt", "post_code": "1000"})
    assert key == "BE|1000|grote markt|"


def test_sint_variants_fold():
    a = address_key({"street": "St.-Jansplein", "street_number": "1", "post_code": "2060"})
    b = address_key({"street": "Sint-Jansplein", "street_number": "1", "post_code": "2060"})
    assert a == b


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
    print(f"\n{'FAILED' if failures else 'All address tests passed'} ({failures} failure(s))")
    sys.exit(1 if failures else 0)
