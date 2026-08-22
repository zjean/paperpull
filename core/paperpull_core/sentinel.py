"""Per-account state that cannot log in to anything.

Beside progress.json, one small file per account records two things: WHICH
account this is (the identity anchors - see paperpull_core.identity) and
WHETHER its session was alive the last time anything looked.

Nothing in it authenticates anything. There is no password here, no cookie, no
token and no session id, and nothing in this file can be replayed against a
provider. That is the point: an unattended run needs to answer "is it worth
trying?" and a scheduler needs to answer "who is due?", and neither question
requires a secret. A run that finds a dead session parks here instead of
asking a question nobody is present to answer.

Two records in one JsonStore, kept separate so writing one never clobbers the
other:

  identity : {"anchors": [{"id","date"}, ...]}
  session  : {"state": "warm"|"parked", "last_verified_alive": iso,
              "parked_reason": str, "parked_at": iso}
"""
from __future__ import annotations

from typing import Iterable, List

IDENTITY_KEY = "identity"
SESSION_KEY = "session"

# The three fields inside the session record that anything outside this module
# reads. Named because paperpull_core.appload reads the same file as plain
# JSON - it answers "who is due?" for the scheduler and the panel without a
# JsonStore - and a string literal over there could not follow a rename here.
STATE_KEY = "state"
LAST_ALIVE_KEY = "last_verified_alive"
PARKED_REASON_KEY = "parked_reason"

WARM = "warm"
PARKED = "parked"


def read_anchors(store) -> List[dict]:
    return list((store.get(IDENTITY_KEY) or {}).get("anchors") or [])


def write_anchors(store, anchors: Iterable[dict]) -> None:
    store.update(IDENTITY_KEY, {"anchors": [dict(a) for a in anchors]})


def mark_warm(store, when: str) -> None:
    """The session was alive at `when`, and is no longer parked."""
    store.update(SESSION_KEY, {STATE_KEY: WARM, LAST_ALIVE_KEY: when,
                               PARKED_REASON_KEY: ""})


def park(store, reason: str, when: str) -> None:
    """This account needs a human. Not an error - a state."""
    store.update(SESSION_KEY, {STATE_KEY: PARKED, PARKED_REASON_KEY: reason,
                               "parked_at": when})


def session_state(store) -> str:
    return (store.get(SESSION_KEY) or {}).get(STATE_KEY) or ""


def parked_reason(store) -> str:
    """Why this account is parked, in the app's own words.

    Written by park() since parking existed and read by nothing until the
    scheduler needed to tell a person WHY they are needed rather than only
    that they are. Empty for a warm session, because mark_warm clears it -
    so this doubles as "is there anything to say about this account?".
    """
    return (store.get(SESSION_KEY) or {}).get(PARKED_REASON_KEY) or ""


def last_verified_alive(store) -> str:
    return (store.get(SESSION_KEY) or {}).get(LAST_ALIVE_KEY) or ""
