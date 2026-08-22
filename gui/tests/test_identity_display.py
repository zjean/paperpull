"""What the panel tells you about identity must not lie to the sixteen apps
that have no identity gate.

The original defect: the panel's "No identity recorded... press Adopt
identity" warning and its "· unidentified" account suffix were shown for
EVERY app whenever `identified` was false - which, for every app except Simyo
and Youfone, is *always*, because nothing ever calls `ensure_identity` for
them. Hiding the button (gui/app.py's `_supported_actions`) made that worse
rather than neutral: the button the warning told you to press no longer
existed, for the same sixteen apps the warning still nagged at.

Two things carry the fix now, and this file reaches both:

  server side  `_needs()` decides which bucket an account is in, and only
               puts one in "confirm" when the app is actually gated. The
               page draws whatever that says, so a wrong answer here would
               reach the screen no matter what the JS does.

  page side    `identityText()` and `rowNote()` are pure functions - account
               row in, words out - so they can be executed directly under
               Node with no DOM at all. The previous version of this file
               had to extract DOM-coupled handlers and stub out `$`,
               `Option` and `META` to reach the same wording; the functions
               being pure is why that scaffolding is gone.

What this file does NOT do: render the page in a real browser or click the
real elements. That gap is unchanged, and is why the wording lives in pure
functions in the first place.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as panel  # noqa: E402

NODE = shutil.which("node")


def _extract_function(source: str, name: str) -> str:
    """The literal source of `function <name>(...) { ... }` inside panel.JS.

    Brace-balanced, not a one-line regex: these functions contain nested `{}`
    (object literals, arrow functions, if-blocks), so a naive "up to the first
    closing brace" match would truncate them. Raises if the function cannot be
    found, rather than silently running an empty stub - a rename in panel.js
    must fail this test, not pass it vacuously.
    """
    marker = f"function {name}("
    start = source.index(marker)  # raises ValueError if absent - intentional
    brace_start = source.index("{", start)
    depth = 0
    i = brace_start
    while i < len(source):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start:i + 1]
        i += 1
    raise AssertionError(f"unbalanced braces extracting {name}()")


def _call(fn: str, *args, needs: list = ("identityText",)):
    """Run one of the page's own pure functions on one account row.

    `needs` names every function that has to be present for `fn` to run -
    nextStep leans on pathFor, so both are extracted.
    """
    if NODE is None:
        pytest.skip("node is not available in this environment")
    bodies = "\n".join(_extract_function(panel.JS, n) for n in needs)
    script = (bodies + "\nconsole.log(JSON.stringify("
              + fn + "(" + ", ".join(json.dumps(a) for a in args) + ")));")
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True,
                         timeout=15)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


def _account(name="primary", gated=False, identified=False, needs="ok",
             state="", anchors=None, **extra):
    row = {"app": "provider", "account": name, "needs": needs, "state": state,
           "last_alive": "", "parked_reason": "", "identity_gated": gated,
           "identified": identified, "anchors": anchors or [],
           "session_lifetime_minutes": None, "newest_document_date": "",
           "last_checked_date": "",
           "supported_actions": ["login", "pilot", "all", "discover",
                                 "resume", "verify"],
           "has_venv": True}
    row.update(extra)
    return row


# -- an app WITHOUT the identity gate (sixteen of eighteen, e.g. amex) -------


def test_an_ungated_app_is_never_told_to_confirm_anything():
    """The dead end, on the server side: an account of an app with no gate
    must not land in the bucket whose whole text is "go and confirm it"."""
    assert panel._needs(
        {"configured": True, "state": "", "identified": False},
        gated=False, due=False) == "ok"


def test_an_ungated_apps_identity_line_does_not_send_you_hunting():
    """Before the fix this told the user to press a button that does not
    exist for this app."""
    out = _call("identityText", _account(gated=False))
    assert "confirm" not in out["text"].lower()
    assert out["gone"] is False


def test_an_ungated_apps_register_line_says_nothing_alarming():
    out = _call("rowNote", _account(gated=False, needs="ok"),
                needs=["rowNote"])
    assert "confirm" not in out["text"].lower()
    assert "unidentified" not in out["text"].lower()


def test_an_ungated_app_still_shows_anchors_if_it_somehow_has_any():
    """The guard falls through to the SAME anchors branch a gated app uses -
    it does not blanket-suppress the identity line, only the false "you must
    confirm" claim. If an ungated app's sentinel ever carried anchors (it
    should not today, but nothing enforces that), they would still be shown.
    """
    out = _call("identityText",
                _account(gated=False, identified=True,
                         anchors=[{"id": "123", "date": "2026-01-01"}]))
    assert "123" in out["text"]


def test_an_ungated_app_is_never_pointed_at_a_button_it_does_not_have():
    """nextStep is what fills the one highlighted button on the bench, so it
    is the other place a person could be sent to a non-existent action."""
    out = _call("nextStep", _account(gated=False, needs="due", state="warm"),
                needs=["pathFor", "nextStep"])
    assert out != "adopt"


# -- an app WITH the identity gate (Simyo, Youfone) --------------------------


def test_a_gated_apps_unconfirmed_account_is_bucketed_as_needing_confirming():
    """Negative control on the server side: the guard must not silence the
    real case."""
    assert panel._needs(
        {"configured": True, "state": "", "identified": False},
        gated=True, due=False) == "confirm"


def test_a_gated_apps_unconfirmed_account_gets_the_real_instruction():
    out = _call("identityText", _account(gated=True))
    assert "confirm it once" in out["text"]
    assert out["gone"] is True


def test_a_gated_apps_parked_account_is_told_to_sign_in_not_to_confirm():
    """Parked outranks unconfirmed, because confirming needs a signed-in tab
    to confirm against - so an account that is both must be sent to the
    sign-in first."""
    assert panel._needs(
        {"configured": True, "state": "parked", "identified": False},
        gated=True, due=False) == "signin"


def test_a_gated_apps_confirmed_account_shows_its_anchors():
    out = _call("identityText",
                _account(gated=True, identified=True,
                         anchors=[{"id": "555", "date": "2026-08-14"}]))
    assert "555" in out["text"]
    assert "2026-08-14" in out["text"]


def test_the_bench_points_a_gated_signed_in_account_at_confirming():
    """The positive control for nextStep: Simyo's own Confirm button really
    is the thing to press, and it is the one that gets highlighted."""
    row = _account(gated=True, state="warm", needs="due",
                   supported_actions=["login", "adopt", "pilot", "all"])
    assert _call("nextStep", row, needs=["pathFor", "nextStep"]) == "adopt"
