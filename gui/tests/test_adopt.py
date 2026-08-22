"""The panel's way through the identity gate.

An app that requires adopted anchors refuses Pilot, Discover, Run All and
Resume until they exist - and nothing in the panel passed `--adopt-identity`,
so an upgraded install had exactly two working buttons (Login and Verify) and
no way to reach the state all the others were refusing over.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as panel  # noqa: E402


def _flags(action: str, login_flag: str = "--login"):
    return [login_flag if f == "__LOGIN__" else f
            for f in panel.ACTIONS[action]["flags"]]


def test_there_is_a_button_that_adopts_an_identity():
    assert "adopt" in panel.ACTIONS
    assert _flags("adopt") == ["--discover", "--adopt-identity"]


def test_adopting_carries_a_real_command():
    """--adopt-identity is a modifier, not an action: on its own the app
    prints its help and exits 0, which would look like success and record
    nothing."""
    flags = _flags("adopt")
    assert any(f in flags for f in ("--discover", "--pilot", "--all")), flags


def test_adopting_is_not_exempt_from_the_provider_lock():
    """It reads the signed-in tab, so it needs the session slot like any
    other run that touches the browser."""
    assert panel._lock_exempt({"login_flag": "--login"}, "adopt") is False


def test_the_panel_offers_adopt_to_the_page():
    """The buttons are built from whatever the panel reports, so this is what
    puts it on screen. Named for what the user is being asked to do rather
    than for the flag behind it - "adopt an identity" is the codebase's word,
    not something a person reads off a screen and understands."""
    assert panel.api_apps()["actions"]["adopt"] == "Confirm this account"
    assert panel.ACTIONS["adopt"]["blurb"], "the button has to say what it does"


def test_the_page_shows_what_was_adopted():
    """The spec's promise about the bootstrap hole: adoption is the one moment
    identity is taken on trust, so the panel has to show what it recorded for
    a person to check once. A boolean cannot be checked against anything.

    What the anchors actually render as is covered properly in
    test_identity_display.py, which executes this function. This only pins
    down that it exists and reads them."""
    assert "function identityText(" in panel.JS
    assert "a.anchors" in panel.JS


def test_verify_and_a_human_signing_in_stay_exempt():
    """Negative control: the exemption list is unchanged by the new action."""
    assert panel._lock_exempt({"login_flag": "--login"}, "verify") is True
    assert panel._lock_exempt({"login_flag": "--open-browser"}, "login") is True
    assert panel._lock_exempt({"login_flag": "--login"}, "login") is False
