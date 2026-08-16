"""Extract parties from an NBB Central Balance Sheet Office XBRL filing.

The CBSO taxonomy is *fully dimensional*, which is the one thing to understand
before reading this file. A 1.8 MB annual account uses only about eighteen
distinct element names — generic metrics such as `met:pct1` (a percentage),
`met:str2` (a string), `met:int2` (an integer). None of them say what they mean.
All meaning lives in the dimensions attached to the fact's *context*:

    <met:pct1 contextRef="c123">0.6444</met:pct1>

    <context id="c123">
      <xbrldi:typedMember dimension="dim:snlp">Korys NV</xbrldi:typedMember>
      <xbrldi:explicitMember dimension="dim:psn">psn:m20</xbrldi:explicitMember>
      <xbrldi:explicitMember dimension="dim:bas">bas:m117</xbrldi:explicitMember>
    </context>

which reads as "the party named Korys NV, in the role Shareholder structure -
legal person, measured on the Social rights axis, is 0.6444". So there is no
`<Shareholder>` tag to search for: extraction means resolving every fact's
context to its dimension set and grouping facts by the *typed* dimension that
carries the party's name.

The dimension and member identifiers below are not guesses. They are taken from
the label linkbases in NBB's own taxonomy package (nbb-cbso-26.0.15), and the
English labels are quoted next to each constant so the mapping can be audited
without downloading 61 MB of taxonomy.

Verified against live filings for Colruyt Group NV (0400378485, full model),
CGMI BV (0779301067, abbreviated) and Achilles Dott BV (0691752926,
abbreviated).
"""

import logging
import re
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)

XBRLI = "{http://www.xbrl.org/2003/instance}"
XBRLDI = "{http://xbrl.org/2006/xbrldi}"
LINK = "{http://www.xbrl.org/2003/linkbase}"

# Typed dimensions that carry a party's name, mapped to (role, kind, firstname
# dimension). The role is what the edge becomes in the graph; `kind` decides
# whether the party lands on a Company or a Person node.
#
# Labels are from dim-label.xml:
#   snlp "Shareholder name - legal person"    snnp/sfnp "... natural person"
#   anlp "Administrator name - legal person"  annp/afnp "... natural person"
#   ptpn "Participant name"                   petn      "Parent entity name"
#   aclp "Accountant name - legal person"     acnp/acfn "... natural person"
#   sanl "Supplementary auditor name - legal person"  sann/safn "... natural"
_PARTY_DIMS = {
    "dim:snlp": ("SHAREHOLDER", "company", None),
    "dim:snnp": ("SHAREHOLDER", "person", "dim:sfnp"),
    "dim:anlp": ("DIRECTOR", "company", None),
    "dim:annp": ("DIRECTOR", "person", "dim:afnp"),
    "dim:ptpn": ("PARTICIPATION", "company", None),
    "dim:petn": ("PARENT", "company", None),
    "dim:aclp": ("ACCOUNTANT", "company", None),
    "dim:acnp": ("ACCOUNTANT", "person", "dim:acfn"),
    "dim:sanl": ("AUDITOR", "company", None),
    "dim:sann": ("AUDITOR", "person", "dim:safn"),
}

# Dimensions naming the natural person who signs on behalf of a legal-person
# party. These do not identify a party of their own — they qualify the party in
# the same context — so they are folded onto it as `represented_by`.
#   aprn/aprf "Administrator - Participant representative name/firstname"
#   cprn/cprf "Accountant - Participant representative ..."
#   sprn/sprf "Supplementary auditor name - Participant representative ..."
_REPRESENTATIVE_DIMS = {
    "dim:aprn": "dim:aprf",
    "dim:cprn": "dim:cprf",
    "dim:sprn": "dim:sprf",
}

# psn = "Person", the role axis. Where present it overrides the default role
# implied by the typed dimension, because the same name-dimension is reused for
# roles the taxonomy distinguishes only here — `dim:aclp` is labelled
# "Accountant" but carries the statutory auditor under psn:m13.
_ROLE_BY_PSN = {
    "psn:m10": "DIRECTOR",     # Directors & managers - legal person
    "psn:m12": "DIRECTOR",     # Directors & managers - natural person
    "psn:m13": "AUDITOR",      # Auditor - legal person
    "psn:m15": "AUDITOR",      # Auditor - natural person
    "psn:m20": "SHAREHOLDER",  # Shareholder structure - legal person
    "psn:m22": "SHAREHOLDER",  # Shareholder structure - individual
}

# bas = "Basic category", the axis saying which attribute of the party a fact is.
_BAS_IDENTIFIER = "bas:m26"   # "Identifier"
_BAS_LEGAL_FORM = "bas:m30"   # "Legal form"
_BAS_ADDRESS = "bas:m31"      # "Address"
_BAS_MEMBERSHIP = "bas:m34"   # "Number" (IRE/IBR membership number)
_BAS_EQUITY = "bas:m37"       # "Vermogen"
_BAS_RESULT = "bas:m44"       # "Resultaat"
_BAS_VOTING_RIGHTS = "bas:m71"
_BAS_MANDATE = "bas:m115"     # "Mandate"
_BAS_SOCIAL_RIGHTS = "bas:m117"  # "Social rights" — where percentages live

# ctc = "Contact", the address component axis.
_ADDRESS_PARTS = {
    "ctc:m1": "street",
    "ctc:m2": "street_number",
    "ctc:m3": "box",
    "ctc:m4": "post_code",
    "ctc:m5": "city",
    "ctc:m6": "country_code",
}

_QLT_REGISTRATION_NUMBER = "qlt:m1"      # "Company registration number"
_SPEC_HELD_DIRECTLY = "spec:m87"         # "Held directly"
_SPEC_VIA_SUBSIDIARIES = "spec:m32"      # "Held by subsidiaries"
# Share counts are filed twice on the voting-rights axis, split between rights
# that are attached to securities and rights that are not. Only the first is the
# shareholding; taking whichever fact arrives last silently zeroes it, because
# the "not linked" figure is usually 0.
_SPEC_LINKED_TO_SECURITIES = "spec:m88"      # "Linked to securities"
_SPEC_NOT_LINKED_TO_SECURITIES = "spec:m89"  # "Not linked to securities"
_DCL_DESCRIPTION = "dcl:m27"  # free-text role ("Bestuurder", "Zaakvoerder")
# Describes what is held: a share class ("Gewone aandelen") in some filings, the
# nature of the right ("Volle eigendom") in others.
_DCL_SHARE_NATURE = "dcl:m7"

# Members arrive prefixed and numbered: cty:mBE, pcd:m1500, lgf:m014.
_MEMBER = re.compile(r"^[a-z0-9]+:m(.*)$")

# A Belgian enterprise number: ten digits beginning 0 or 1. Foreign parties put
# their own national identifier in the same field (CHE-109.996.904,
# NO 0917351538), so the format check is what keeps them out of the CBE
# namespace rather than any flag in the filing.
_CBE = re.compile(r"^[01]\d{9}$")


def _member_value(text: str | None) -> str | None:
    """Strip the domain prefix from an explicit member: `cty:mBE` -> `BE`."""
    if not text:
        return None
    match = _MEMBER.match(text.strip())
    return match.group(1) if match else text.strip()


def normalise_cbe(value: str | None) -> str | None:
    """Return a bare 10-digit CBE number, or None if this is not one.

    Filings write the number with and without dots and occasionally with a BE
    prefix; anything that does not reduce to the Belgian format is a foreign
    identifier and must not be treated as a CBE number.
    """
    if not value:
        return None
    digits = re.sub(r"[.\s]", "", str(value)).upper().removeprefix("BE")
    return digits if _CBE.match(digits) else None


def _to_float(value: str | None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: str | None) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _parse_contexts(root) -> dict[str, dict[str, str]]:
    """Map each context id to its {dimension: member} set.

    Explicit members carry a domain member (`psn:m20`); typed members carry
    free text (the party's name). Both are flattened into one dict because
    downstream code only ever asks "what is this fact about?".
    """
    contexts: dict[str, dict[str, str]] = {}
    for context in root.findall(XBRLI + "context"):
        dims: dict[str, str] = {}
        for member in context.iter():
            if member.tag == XBRLDI + "explicitMember":
                dims[member.get("dimension")] = (member.text or "").strip()
            elif member.tag == XBRLDI + "typedMember":
                children = list(member)
                text = (children[0].text or "") if children else ""
                dims[member.get("dimension")] = text.strip()
        contexts[context.get("id")] = dims
    return contexts


class _Party:
    """Facts accumulated for one named party in the filing."""

    def __init__(self, role: str, kind: str, name: str):
        self.role = role
        self.kind = kind
        self.name = name
        self.first_name: str | None = None
        self.identifier: str | None = None
        self.legal_form: str | None = None
        self.membership_number: str | None = None
        self.address: dict[str, str] = {}
        self.pct: float | None = None
        self.pct_direct: float | None = None
        self.pct_via_subsidiaries: float | None = None
        self.shares: int | None = None
        self.voting_rights_unlinked: int | None = None
        self.share_nature: str | None = None
        self.equity: float | None = None
        self.result: float | None = None
        self.mandate: str | None = None
        self.role_label: str | None = None
        self.represented_by: list[str] = []

    def add(self, local_name: str, value: str, dims: dict[str, str]) -> None:
        bas = dims.get("dim:bas")
        spec = dims.get("dim:spec")
        dcl = dims.get("dim:dcl")

        if bas == _BAS_IDENTIFIER and dims.get("dim:qlt") == _QLT_REGISTRATION_NUMBER:
            self.identifier = value
        elif bas == _BAS_ADDRESS and dims.get("dim:ctc") in _ADDRESS_PARTS:
            self.address[_ADDRESS_PARTS[dims["dim:ctc"]]] = _member_value(value) or value
        elif bas == _BAS_LEGAL_FORM:
            self.legal_form = _member_value(value)
        elif bas == _BAS_MEMBERSHIP:
            self.membership_number = value
        elif bas == _BAS_EQUITY:
            self.equity = _to_float(value)
        elif bas == _BAS_RESULT:
            self.result = _to_float(value)
        elif bas == _BAS_MANDATE:
            self.mandate = _member_value(value)
        elif bas == _BAS_SOCIAL_RIGHTS:
            if local_name == "pct1":
                # Shareholder rows carry a bare percentage; participation rows
                # split it across "held directly" and "held via subsidiaries".
                if spec == _SPEC_HELD_DIRECTLY:
                    self.pct_direct = _to_float(value)
                elif spec == _SPEC_VIA_SUBSIDIARIES:
                    self.pct_via_subsidiaries = _to_float(value)
                else:
                    self.pct = _to_float(value)
            elif local_name in ("int2", "int3", "int4"):
                self.shares = _to_int(value)
            elif dcl == _DCL_SHARE_NATURE:
                self.share_nature = value
        elif bas == _BAS_VOTING_RIGHTS and local_name in ("int2", "int3", "int4"):
            if spec == _SPEC_LINKED_TO_SECURITIES:
                self.shares = _to_int(value)
            elif spec == _SPEC_NOT_LINKED_TO_SECURITIES:
                self.voting_rights_unlinked = _to_int(value)
        elif dcl == _DCL_DESCRIPTION:
            self.role_label = value

    def as_dict(self) -> dict:
        # Percentages are filed as fractions (0.6444). They are converted once,
        # here, so that everything downstream — Cypher, the API, the UI — deals
        # in the percentages a human would write, and nothing has to remember
        # which representation it is holding.
        def pct(value):
            return round(value * 100, 4) if value is not None else None

        full_name = " ".join(p for p in (self.first_name, self.name) if p).strip()
        return {
            "role": self.role,
            "kind": self.kind,
            "name": full_name or self.name,
            "last_name": self.name if self.kind == "person" else None,
            "first_name": self.first_name,
            "identifier": self.identifier,
            "cbe_number": normalise_cbe(self.identifier),
            "legal_form": self.legal_form,
            "membership_number": self.membership_number,
            "address": self.address or None,
            "pct": pct(self.pct if self.pct is not None else self.pct_direct),
            "pct_via_subsidiaries": pct(self.pct_via_subsidiaries),
            "shares": self.shares,
            "voting_rights_unlinked": self.voting_rights_unlinked,
            "share_nature": self.share_nature,
            "equity": self.equity,
            "result": self.result,
            "mandate": self.mandate,
            "role_label": self.role_label,
            "represented_by": sorted(set(self.represented_by)) or None,
        }


def _party_slot(dims: dict[str, str]) -> tuple[str, str, str, str] | None:
    """Identify which party a context is about.

    Returns (dimension, name, role, kind), or None when the context is not
    about a named party at all — which is the overwhelming majority of facts in
    a filing, since the balance sheet itself has no parties.
    """
    for dim, (default_role, kind, first_dim) in _PARTY_DIMS.items():
        name = dims.get(dim)
        if not name:
            continue
        role = _ROLE_BY_PSN.get(dims.get("dim:psn", ""), default_role)
        return dim, name, role, kind
    return None


def parse_parties(source) -> list[dict]:
    """Extract every named party from an XBRL instance.

    `source` is anything ElementTree accepts — a path, a file object, or bytes
    wrapped in BytesIO.
    """
    root = ET.parse(source).getroot()
    contexts = _parse_contexts(root)

    parties: dict[tuple, _Party] = {}
    representatives: list[tuple[tuple, str]] = []

    for fact in root:
        if "}" not in fact.tag:
            continue
        namespace, local_name = fact.tag[1:].split("}")
        if "instance" in namespace or "linkbase" in namespace:
            continue

        dims = contexts.get(fact.get("contextRef"))
        if not dims:
            continue
        slot = _party_slot(dims)
        if not slot:
            continue
        dim, name, role, kind = slot
        key = (dim, name)

        # A context naming a representative describes the signatory of the
        # party, not a party in its own right. Record the link and move on so
        # the representative's own address does not overwrite the company's.
        rep_name = next(
            (
                " ".join(p for p in (dims.get(first), dims.get(rep)) if p).strip()
                for rep, first in _REPRESENTATIVE_DIMS.items()
                if dims.get(rep)
            ),
            None,
        )
        if rep_name:
            representatives.append((key, rep_name))
            continue

        party = parties.get(key)
        if party is None:
            party = parties[key] = _Party(role, kind, name)
            first_dim = _PARTY_DIMS[dim][2]
            if first_dim:
                party.first_name = dims.get(first_dim) or None
        elif role != party.role and dims.get("dim:psn") in _ROLE_BY_PSN:
            # Trust an explicit psn member over the dimension's default.
            party.role = role

        party.add(local_name, (fact.text or "").strip(), dims)

    for key, rep_name in representatives:
        if key in parties:
            parties[key].represented_by.append(rep_name)

    return [party.as_dict() for party in parties.values()]


def summarise(parties: list[dict]) -> dict[str, int]:
    """Count parties by role — used to log what a filing actually yielded."""
    counts: dict[str, int] = {}
    for party in parties:
        counts[party["role"]] = counts.get(party["role"], 0) + 1
    return counts
