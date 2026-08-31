"""Who is worth running now, and in what order.

Two separate questions, and they are answered from data on disk rather than
from a clock the scheduler keeps for itself:

  DUE      - has enough time passed since this account's newest document that
             the next one is plausibly there? A monthly invoice checked twice
             a day is nine wasted sign-ins; checked once a month it is late.
             `last_checked_date` stops a second run on the same day.

  ORDER    - most perishable first. An account whose session dies in ten
             minutes cannot queue behind nine accounts whose sessions last for
             days: by the time its turn came, the human who signed it in has
             wandered off and the session is gone. Providers that declare no
             session lifetime sort last, because they are the ones that can
             wait (and the ones a plain cron can pick up later anyway).

A parked account stays in the list on purpose - unconditionally, ahead of
every other test in `_is_due`. It cannot run unattended, but it is exactly
what a human sitting down needs to be told about.
"""
from __future__ import annotations

from datetime import date
from statistics import median
from typing import Iterable, List, Optional

# Most providers here bill monthly. 31 days rather than 30 so a month-end
# invoice does not make an account look due a day early, every month.
DEFAULT_CADENCE_DAYS = 31

# Sorts providers that declare no session lifetime after every provider that
# does, without special-casing None in the comparison.
_PATIENT = 10 ** 9


def _as_date(value: str) -> Optional[date]:
    try:
        return date.fromisoformat(str(value).strip())
    except (TypeError, ValueError):
        return None


def measure_cadence(records: Iterable[dict]) -> Optional[int]:
    """How often this provider issues documents, from this account's own history.

    `DEFAULT_CADENCE_DAYS` is a guess that suits most of the providers here
    and no quarterly one: judged against 31 days, a quarterly statement is
    called due two months early, every quarter, and a weekly one is checked
    long after the next document has landed. The archive already knows the
    answer - `progress.json` holds every document's date - so it is measured
    rather than assumed, and a `cadence_days` in the config still wins.

    Three rules, each of which exists because of a way the naive version is
    wrong:

    * **Per provider-side account, then combined.** One install can cover
      several of the provider's own accounts, each billed monthly. Pooling
      their dates halves every gap and the account reports itself due a
      fortnight after a statement arrives. Grouped, it reads monthly, which
      is the truth. Accounts with too little history of their own fall back
      to the pooled dates rather than to nothing.
    * **The median, not the mean.** One late statement, or one year nobody
      downloaded, must not move the answer.
    * **Only gaps of 1 to 400 days.** Two records dated the same day say
      nothing about a cadence, and a record dated year 0001 - which a
      provider page can genuinely produce - would otherwise measure a cadence
      of several centuries. The window is the filter for both.

    Returns None when the history cannot answer: fewer than three dated
    documents, fewer than two usable gaps, or a receipt archive. Receipt apps
    record `purchase_date`, never `date`, and deliberately get no measurement
    at all: purchases arrive when someone buys something, so a cadence
    measured off them is noise wearing a number.
    """
    by_account: dict = {}
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        when = _as_date(rec.get("date"))
        if when is None:
            continue
        by_account.setdefault(str(rec.get("account") or ""), []).append(when)
    if not by_account:
        return None
    per = [c for c in (_gap_median(v) for v in by_account.values()) if c]
    if not per:
        pooled = [d for dates in by_account.values() for d in dates]
        measured = _gap_median(pooled)
        return int(round(measured)) if measured else None
    return int(round(median(per)))


def _gap_median(dates: List[date]) -> Optional[float]:
    """The typical gap between consecutive documents, or None.

    Only the last 24 gaps count: a provider that moved from monthly to
    quarterly should read as quarterly within a couple of years, not be held
    to an average of its whole history.
    """
    uniq = sorted(set(dates))
    if len(uniq) < 3:
        return None
    gaps = [(b - a).days for a, b in zip(uniq, uniq[1:])
            if 0 < (b - a).days <= 400]
    if len(gaps) < 2:
        return None
    return median(gaps[-24:])


def _is_due(account: dict, today: date) -> bool:
    # Parked comes first, before the same-day short-circuit below. A parked
    # account is the one thing a person sitting down MUST be shown, and
    # parking is normally recorded on a day this account was already looked
    # at: warm at 09:00, session dies, parked at 09:20. Read in the other
    # order, that account would drop out of the list for the rest of the day -
    # parked and invisible at once - and the ten-minute session dying mid-run
    # is the exact case this whole schedule exists for.
    if account.get("parked"):
        return True
    if str(account.get("last_checked_date") or "").strip() == today.isoformat():
        return False
    newest = _as_date(account.get("newest_document_date"))
    if newest is None:
        # Never run, or a date nothing can parse. Both mean "look".
        return True
    cadence = account.get("cadence_days")
    if cadence is None:
        cadence = DEFAULT_CADENCE_DAYS
    return (today - newest).days >= int(cadence)


def _urgency(account: dict) -> tuple:
    lifetime = account.get("session_lifetime_minutes")
    return (_PATIENT if lifetime is None else int(lifetime),
            str(account.get("newest_document_date") or ""),
            str(account.get("app") or ""),
            str(account.get("account") or ""))


def plan(accounts: Iterable[dict], today: str) -> List[dict]:
    """The accounts worth running on `today`, most perishable first."""
    when = _as_date(today)
    if when is None:
        raise ValueError(f"today must be an ISO date, got {today!r}")
    return sorted((a for a in accounts if _is_due(a, when)), key=_urgency)
