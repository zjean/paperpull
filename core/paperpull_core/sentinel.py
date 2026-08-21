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

# The two fields inside the session record that anything outside this module
# reads. Named because paperpull_core.appload reads the same file as plain
# JSON - it answers "who is due?" for the scheduler and the panel without a
# JsonStore - and a string literal over there could not follow a rename here.
STATE_KEY = "state"
LAST_ALIVE_KEY = "last_verified_alive"

WARM = "warm"
PARKED = "parked"


def read_anchors(store) -> List[dict]:
    return list((store.get(IDENTITY_KEY) or {}).get("anchors") or [])


def write_anchors(store, anchors: Iterable[dict]) -> None:
    store.update(IDENTITY_KEY, {"anchors": [dict(a) for a in anchors]})


def mark_warm(store, when: str) -> None:
    """The session was alive at `when`, and is no longer parked."""
    store.update(SESSION_KEY, {STATE_KEY: WARM, LAST_ALIVE_KEY: when,
                               "parked_reason": ""})


def park(store, reason: str, when: str) -> None:
    """This account needs a human. Not an error - a state."""
    store.update(SESSION_KEY, {STATE_KEY: PARKED, "parked_reason": reason,
                               "parked_at": when})


def session_state(store) -> str:
    return (store.get(SESSION_KEY) or {}).get(STATE_KEY) or ""


def last_verified_alive(store) -> str:
    return (store.get(SESSION_KEY) or {}).get(LAST_ALIVE_KEY) or ""
