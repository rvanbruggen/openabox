"""Address canonicalisation tests.

If these break, shared-address detection silently degrades: companies at the
same building stop meeting on one Address node and the overlap queries quietly
return fewer results rather than failing loudly. Hence the emphasis here.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.address import (  # noqa: E402
    address_key,
    address_properties,
    city_key,
    normalise_number,
    normalise_street,
)


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


def test_compound_and_split_street_types_fold():
    # Both spellings occur in live register data for the same street type.
    for joined, split in [
        ("Brusselsesteenweg", "Brusselse steenweg"),
        ("Gentsesteenweg", "Gentse Steenweg"),
        ("Ninoofsesteenweg", "Ninoofse Steenweg"),
        ("Liersesteenweg", "Lierse steenweg"),
    ]:
        a = address_key({"street": joined, "street_number": "1", "post_code": "9000"})
        b = address_key({"street": split, "street_number": "1", "post_code": "9000"})
        assert a == b, f"{joined} != {split}: {a} vs {b}"


def test_municipality_disambiguator_is_stripped():
    a = address_key({"street": "Kerkstraat(STE)", "street_number": "159", "post_code": "9190"})
    b = address_key({"street": "Kerkstraat", "street_number": "159", "post_code": "9190"})
    assert a == b == "BE|9190|kerkstraat|159"


def test_french_street_type_is_not_joined():
    # The type leads in French names, so the trailing-token join must not fire.
    assert normalise_street("Rue de la Station") == "rue de la station"
    assert normalise_street("Chaussée de Bruxelles") == "chaussee de bruxelles"
    assert normalise_street("Place du Marché") == "place du marche"


def test_sint_variants_fold():
    a = address_key({"street": "St.-Jansplein", "street_number": "1", "post_code": "2060"})
    b = address_key({"street": "Sint-Jansplein", "street_number": "1", "post_code": "2060"})
    assert a == b


def test_one_postcode_with_different_names_is_one_city():
    # Live data: 1040 is filed as both "Etterbeek" and "Brussel". Keying on
    # the name would split one locality into two nodes.
    a = city_key({"post_code": "1040", "city": "Etterbeek", "country_code": "BE"})
    b = city_key({"post_code": "1040", "city": "Brussel", "country_code": "BE"})
    assert a == b == "BE|1040"


def test_one_name_across_postcodes_stays_separate():
    # Live data: Antwerpen spans nine post codes. These are distinct postal
    # areas and must not collapse onto one node.
    keys = {
        city_key({"post_code": pc, "city": "Antwerpen", "country_code": "BE"})
        for pc in ["2000", "2018", "2100", "2170", "2610"]
    }
    assert len(keys) == 5, keys


def test_city_key_separates_countries():
    assert city_key({"post_code": "2411", "city": "Luxembourg", "country_code": "LU"}) == "LU|2411"
    # Same digits in another country must not collide.
    assert city_key({"post_code": "2411", "city": "Elsewhere", "country_code": "BE"}) == "BE|2411"


def test_city_key_falls_back_to_name_without_postcode():
    assert city_key({"city": "Hyderabad", "country_code": "IN"}) == "IN|hyderabad"
    assert city_key({}) is None


def test_address_no_longer_carries_postcode_or_city():
    # They live on the City node now; keeping copies would let the two drift.
    props = address_properties(
        {"street": "Kerkstraat", "street_number": "1", "post_code": "9000", "city": "Gent"}
    )
    assert "post_code" not in props and "city" not in props
    assert props["street"] == "Kerkstraat"


def test_address_key_still_anchors_on_locality():
    # The key keeps the locality inline so existing keys stay stable even
    # though the properties moved.
    assert address_key(
        {"street": "Kerkstraat", "street_number": "1", "post_code": "9000", "city": "Gent"}
    ) == "BE|9000|kerkstraat|1"


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
