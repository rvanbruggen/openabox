"""Canonical address keys.

"Which companies sit at the same address?" is a core question for this project,
so addresses are first-class nodes keyed by a canonical string rather than
compared as raw text. The register returns the same physical building with
varying casing, punctuation, abbreviations and NL/FR language variants, and all
of that has to be folded away before two records will meet on one node.

The key is deliberately *building*-level: box/unit numbers are kept on the
establishment rather than in the key, so two companies in different units of
the same building still resolve to a single Address node. That is what makes
shared-premises detection work.
"""

import re
import unicodedata

# Abbreviations seen in CBE address lines, folded to a single spelling per
# street type. Both Dutch and French forms appear, sometimes in the same city.
_STREET_TYPES = {
    "str": "straat",
    "strt": "straat",
    "straat": "straat",
    "ln": "laan",
    "laan": "laan",
    "stwg": "steenweg",
    "stw": "steenweg",
    "steenweg": "steenweg",
    "av": "avenue",
    "ave": "avenue",
    "avenue": "avenue",
    "bd": "boulevard",
    "blvd": "boulevard",
    "boulevard": "boulevard",
    "ch": "chaussee",
    "chee": "chaussee",
    "chaussee": "chaussee",
    "pl": "plein",
    "plein": "plein",
    "place": "place",
    "sq": "square",
    "square": "square",
    "st": "sint",
    "ste": "sint",
    "sint": "sint",
    "saint": "sint",
}

# Dutch street names are compound words, so the street type is a *suffix* of a
# single token ("Kerkstr." / "Kerkstraat") rather than a separate word as in
# French ("Rue de l'Eglise"). Both spellings must land on the same key, so
# abbreviated suffixes are expanded in place.
#
# Ordered longest-first so canonical forms match before their abbreviations and
# are left untouched. Suffixes shorter than three characters are deliberately
# excluded: folding "ln" here would rewrite "koln" to "kolaan".
_SUFFIX_ABBREV = (
    ("steenweg", "steenweg"),
    ("straat", "straat"),
    ("plein", "plein"),
    ("laan", "laan"),
    ("stwg", "steenweg"),
    ("strt", "straat"),
    ("stw", "steenweg"),
    ("str", "straat"),
)

# Street types that form Dutch compounds. The register writes these both joined
# and split for the same street — "Gentsesteenweg" and "Gentse Steenweg" both
# appear in live data — so a trailing type token is glued onto the word before
# it. French names carry their type first ("Rue de ..."), so restricting the
# join to a *trailing* token leaves them alone.
_JOINABLE_TYPES = {"straat", "laan", "steenweg", "plein"}

# The register appends municipality disambiguators to street names:
# "Kerkstraat(STE)", "Kapellensteenweg (C)", "Rue de Trazegnies(CO)". The same
# street also appears without them, so they are dropped. Locality and house
# number remain in the key, so this does not risk merging distinct streets.
_PAREN = re.compile(r"\([^)]*\)")

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_WS = re.compile(r"\s+")


def _strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalise_text(value: str | None) -> str:
    """Lowercase, de-accent, strip punctuation, collapse whitespace."""
    if not value:
        return ""
    text = _strip_accents(str(value)).lower()
    text = _PUNCT.sub(" ", text)
    return _WS.sub(" ", text).strip()


def _fold_token(token: str) -> str:
    """Fold one token: whole-word abbreviations first, then compound suffixes."""
    if token in _STREET_TYPES:
        return _STREET_TYPES[token]
    for suffix, canonical in _SUFFIX_ABBREV:
        if token.endswith(suffix) and len(token) > len(suffix):
            return token[: -len(suffix)] + canonical
    return token


def normalise_street(street: str | None) -> str:
    """Normalise a street name, folding abbreviations and compound spacing."""
    text = normalise_text(_PAREN.sub(" ", street or ""))
    if not text:
        return ""
    tokens = [_fold_token(token) for token in text.split(" ")]
    if len(tokens) > 1 and tokens[-1] in _JOINABLE_TYPES:
        tokens[-2:] = [tokens[-2] + tokens[-1]]
    return " ".join(tokens)


def normalise_number(number: str | None) -> str:
    """Reduce a house number to digits plus any letter suffix.

    Ranges ("5-7") collapse to their first number, which is how the register
    itself usually refers to the building.
    """
    text = normalise_text(number)
    if not text:
        return ""
    match = re.search(r"(\d+)\s*([a-z]?)", text)
    if not match:
        return text.replace(" ", "")
    return f"{int(match.group(1))}{match.group(2)}"


def address_key(address: dict | None) -> str | None:
    """Build a stable, human-readable key for an address payload.

    Returns None when there is too little information to identify a building —
    better to attach nothing than to merge unrelated companies onto a bogus
    shared node.
    """
    if not address:
        return None

    country = (normalise_text(address.get("country_code")) or "be").upper()
    post_code = normalise_text(address.get("post_code"))
    city = normalise_text(address.get("city"))
    street = normalise_street(address.get("street"))
    number = normalise_number(address.get("street_number"))

    # A street with no locality anchor is not identifying enough to merge on.
    if not street or not (post_code or city):
        return None

    locality = post_code or city
    return f"{country}|{locality}|{street}|{number}"


def address_properties(address: dict) -> dict:
    """Display properties stored alongside the key on the Address node."""
    return {
        "street": address.get("street"),
        "street_number": address.get("street_number"),
        "post_code": address.get("post_code"),
        "city": address.get("city"),
        "country_code": address.get("country_code") or "BE",
        "full_address": address.get("full_address"),
    }
