"""Identity keys for parties that arrive without a stable identifier.

Belgian legal persons are easy: the filing carries their CBE number, so they
join straight onto the existing `Company.cbe_number` constraint. Everyone else
needs a key constructed here, and the constructions are deliberately
conservative — a wrong merge silently invents a relationship between two real
companies, which is worse than leaving two nodes unmerged.
"""

from .address import normalise_text


def person_key(
    last_name: str | None,
    first_name: str | None,
    address: dict | None,
    fallback: str,
) -> tuple[str, str] | None:
    """Build a key for a natural person, plus the basis it was built on.

    Filings give a surname and a first name and no identifier — correctly, since
    the national register number is not public. So identity has to be
    constructed, and it cannot be constructed from the name alone: "Jan Peeters"
    is not one person.

    A discriminator is therefore always appended:

    * the home post code when the filing gives an address, which is strong
      enough to merge the same individual across the several companies they sit
      in — the common and interesting case;
    * otherwise the CBE number of the company the mandate is held in, which
      deliberately confines the node to that one company rather than risking a
      merge on a name collision.

    The basis is returned so the graph can record how much to trust the key, and
    so the UI can offer "these may be the same person" as a suggestion instead
    of doing it silently.

    The two name parts are **sorted** into the key. Filers do not agree on which
    field is which: Achilles Dott files surnames in the surname dimension, while
    Korys NV filed Willem Colruyt as surname "Willem", first name "Colruyt".
    Sorting makes both spellings collide on one node instead of silently
    creating two people. The cost is that a genuine "Thomas James" and "James
    Thomas" would merge — rare, and much less damaging than splitting one
    director across every company that files their name the other way round.
    The display name keeps the filing's own order.
    """
    last = normalise_text(last_name)
    first = normalise_text(first_name)
    if not last and not first:
        return None
    name = "|".join(sorted(p for p in (last, first) if p))

    post_code = normalise_text((address or {}).get("post_code"))
    if post_code:
        return f"{name}|{post_code}", "post_code"
    return f"{name}|cbe:{fallback}", "company"


def external_key(
    identifier: str | None, country_code: str | None, name: str | None
) -> str | None:
    """Key for a party that is not a Belgian company.

    Foreign entities put their own national identifier in the same field as a
    CBE number (`CHE-109.996.904`, `NO 0917351538`), so they get their own key
    namespace rather than being forced into one they do not belong to. Parties
    with no identifier at all fall back to their name, which is weak — hence the
    explicit `name:` marker in the key, so a later cleanup can find them.
    """
    country = (normalise_text(country_code) or "xx").upper()
    ident = normalise_text(identifier).replace(" ", "")
    if ident:
        return f"{country}|{ident}"
    named = normalise_text(name)
    return f"{country}|name:{named}" if named else None
