"""What the sitting actually runs.

The command surface is data (`ACTIONS`) and the page's script is a file
(`panel.JS`), so both are readable from here - which matters, because this
defect was invisible in any single file's diff. The sitting ran `resume`, an action that selects from the
`discovery.json` an account already has and never asks the provider what
exists. So a sitting spent a person's sign-in, printed "Nothing to resume",
and reported "Sitting done."
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as panel  # noqa: E402


def _flags(action: str, login_flag: str = "--login"):
    """The argv flags an action resolves to, the way _build_cmd resolves it."""
    return [login_flag if f == "__LOGIN__" else f
            for f in panel.ACTIONS[action]["flags"]]


def _sitting_action() -> str:
    """The action the page's sitting loop actually starts, read off the page.

    Read rather than assumed: the whole defect was that this one string said
    something different from what the feature needed, and a test that restated
    the string could not have caught it.
    """
    match = re.search(r"await startRun\(a\.app, a\.account, '([a-z]+)'\)",
                      panel.JS)
    assert match, "the sitting no longer starts a run the way this test reads"
    return match.group(1)


def test_the_sitting_runs_an_action_that_asks_the_provider_what_exists():
    """`--all` is the only action whose command discovers before downloading
    (simyo_docs.py's cmd_run calls cmd_discover; cmd_resume does not). A
    sitting exists to spend a fresh, perishable sign-in on new documents, so
    it has to be one of those."""
    assert _flags(_sitting_action()) == ["--all", "--yes"]


def test_the_sitting_answers_the_confirmation_it_would_otherwise_wait_for():
    """--all prompts for a typed YES. In a sitting the person is present but
    the prompt is not what they are there for, and --yes is also what lets the
    identical command run unattended from the scheduler."""
    assert "--yes" in _flags(_sitting_action())


def test_the_sitting_is_not_exempt_from_the_provider_lock():
    """It drives the shared, single-session browser, so it must queue behind
    (or be refused by) anything else that is using it."""
    assert panel._lock_exempt({"login_flag": "--login"},
                              _sitting_action()) is False
