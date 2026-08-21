"""Which account is this tab actually signed in as, and what should the caller do?

PaperPull attaches to a browser you signed in to yourself, and in the Docker
layout every app points at ONE Chrome. Each provider's `find_signed_in_page()`
picks a tab by matching the provider's host, so with two accounts of the same
provider signed in, a run can read the *other* account's tab. Nothing
downstream would notice: the owner stamped on every document and every CSV row
comes from the config file (`storage.ensure_owner`), never from the page - so
the documents would be filed under the wrong person, silently.

The module answers two questions:

1. **Which account?** The `check()` decision is deliberately provider-agnostic
   and needs no browser: given the document identifiers an account is *known*
   to own (its anchors) and the identifiers the tab is showing right now, does
   this tab belong to this account?

2. **What to do about it?** The `action()` function translates a verdict into
   a concrete decision: proceed, adopt the new identity, or refuse.

Anchors are the provider's own OLDEST document identifiers, because new
documents arrive at the other end - an anchor can only ever fall off the far
edge of the provider's history, never vanish from the middle of it.

That far edge is real: Simyo's server keeps roughly the last twelve months and
drops the rest, so anchors DO age out. An absent anchor is therefore two very
different things, and telling them apart is the whole job of this module:

  * absent, and dated older than anything the tab still shows -> it aged out
    of the provider's window. Tolerated.
  * absent, and dated inside the window the tab is showing -> a document
    disappeared from the middle of this account's history, which does not
    happen. This is a different account.
"""
from __future__ import annotations

from typing import Iterable, List

OK = "ok"                 # at least one anchor present, none contradicted
MISMATCH = "mismatch"     # an anchor is missing from inside the window
UNKNOWN = "unknown"       # nothing recorded, or the tab shows nothing
AGED = "aged"             # every anchor aged out: no positive proof either way

PROCEED = "proceed"       # verified: re-anchor and carry on
ADOPT = "adopt"           # no proof either way, and the human asked to record it
REFUSE = "refuse"         # stop, and say why

ANCHOR_COUNT = 3


def _clean(records: Iterable[dict]) -> List[dict]:
    """Normalise to {'id','date'} with stripped strings, dropping the idless."""
    out: List[dict] = []
    for raw in records or []:
        rec = raw or {}
        ident = str(rec.get("id") or "").strip()
        if not ident:
            continue
        out.append({"id": ident, "date": str(rec.get("date") or "").strip()})
    return out


def pick_anchors(records: Iterable[dict], count: int = ANCHOR_COUNT) -> List[dict]:
    """The oldest `count` documents, as this account's fingerprint.

    Sorted by (date, id) so the order a provider happened to list them in
    cannot change the fingerprint, and deduplicated because a list that
    repeats a document should not spend two anchor slots on it.
    """
    unique = {r["id"]: r for r in _clean(records)}
    return sorted(unique.values(), key=lambda r: (r["date"], r["id"]))[:count]


def check(recorded: Iterable[dict], observed: Iterable[dict]) -> str:
    """Does the tab showing `observed` belong to the account behind `recorded`?

    Returns OK, MISMATCH, UNKNOWN or AGED. UNKNOWN and AGED are both "no
    claim": the caller decides whether that is grounds to adopt an identity or
    grounds to refuse, and those are different answers for an interactive run
    and an unattended one.
    """
    rec = _clean(recorded)
    obs = _clean(observed)
    if not rec or not obs:
        return UNKNOWN

    observed_ids = {r["id"] for r in obs}
    observed_dates = [r["date"] for r in obs if r["date"]]
    # ISO dates compare correctly as text, which is why they are stored as text.
    oldest_shown = min(observed_dates) if observed_dates else ""

    present = aged = 0
    for anchor in rec:
        if anchor["id"] in observed_ids:
            present += 1
        elif anchor["date"] and oldest_shown and anchor["date"] < oldest_shown:
            aged += 1
        else:
            # Missing, and we cannot excuse it as having aged out.
            return MISMATCH
    if present:
        return OK
    return AGED if aged else UNKNOWN


def action(verdict: str, adopt: bool) -> str:
    """What a caller should DO about a verdict.

    Split out from `check` so the decision lives here with its tests rather
    than being re-derived in every app's orchestrator. The caller still reads
    the raw verdict to word its own message - a MISMATCH and an unproven
    identity need very different sentences - but the branch itself is here.
    """
    if verdict == MISMATCH:
        return REFUSE
    if verdict == OK:
        return PROCEED
    return ADOPT if adopt else REFUSE
