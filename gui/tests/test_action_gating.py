"""ACTIONS is one global table; the flags behind it are not universal.

`--adopt-identity` exists only on the entry scripts this feature has reached
(Simyo, then Youfone) - but ACTIONS is a module-level dict with no per-app
gate, so the panel offered an "Adopt identity" button for every one of the 18
apps regardless. Pressing it on any app whose script does not accept
`--adopt-identity` reached that script's own argparse, which refused with
"unrecognized arguments" - a real failure on screen, not a no-op.

`_supported_actions` (read the script's own text, same trick `_login_flag`
uses a few lines above it, and the one `tools/schedule.py`'s
`_supports_unattended` uses for `--unattended`) is what closes that: an
action is only offered - and, in `_build_cmd`, only accepted - when every one
of its real flags is literally present in the script.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as panel  # noqa: E402


def _script(tmp_path, text: str, name: str = "provider_docs.py") -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


# -- _supported_actions ------------------------------------------------------


def test_a_script_with_no_adopt_identity_does_not_offer_adopt(tmp_path):
    """The actual bug: an app that never gained --adopt-identity must not
    advertise the action that would fail on it."""
    script = _script(tmp_path, "ap.add_argument('--discover')\n"
                              "ap.add_argument('--pilot')\n")
    assert "adopt" not in panel._supported_actions(script)


def test_a_script_with_adopt_identity_offers_adopt(tmp_path):
    """The positive control: Simyo's and Youfone's own scripts both carry
    literally this flag, so both must be offered the button."""
    script = _script(tmp_path,
                     "ap.add_argument('--discover')\n"
                     "ap.add_argument('--adopt-identity', action='store_true')\n")
    assert "adopt" in panel._supported_actions(script)


def test_adopt_requires_discover_too_not_adopt_identity_alone(tmp_path):
    """adopt's flags are ["--discover", "--adopt-identity"] - both have to be
    present, the same way _build_cmd would actually invoke them together."""
    script = _script(tmp_path, "ap.add_argument('--adopt-identity')\n")
    assert "adopt" not in panel._supported_actions(script)


def test_login_is_always_offered_regardless_of_script_text(tmp_path):
    """login's only flag is the __LOGIN__ placeholder, resolved by
    _login_flag rather than searched for literally - it must never be
    disqualified by a script that happens to say neither --login nor
    --open-browser in so many words (every real one does, one way or the
    other, but this proves the placeholder itself isn't compared)."""
    script = _script(tmp_path, "print('nothing relevant here')\n")
    assert "login" in panel._supported_actions(script)


def test_an_unreadable_script_offers_only_login(tmp_path):
    """Mirrors _login_flag's own guard: a script this process cannot read
    must degrade to the safest answer, not raise out of discover_apps()."""
    missing = tmp_path / "does_not_exist.py"
    assert panel._supported_actions(missing) == ["login"]


def test_real_apps_gate_adopt_to_simyo_and_youfone_only():
    """The end-to-end check against the actual apps this repository ships,
    not a fake one. Whatever the exact list of providers is today, exactly
    the two that carry --adopt-identity in their own source may offer it."""
    apps = panel.discover_apps()
    if "simyo" not in apps or "youfone" not in apps:
        pytest.skip("simyo/youfone not present under this APPS_ROOT")
    assert "adopt" in apps["simyo"]["supported_actions"]
    assert "adopt" in apps["youfone"]["supported_actions"]
    others = [name for name in apps if name not in ("simyo", "youfone")]
    assert others, "expected at least one other shipped app to compare against"
    for name in others:
        assert "adopt" not in apps[name]["supported_actions"], (
            f"{name} does not implement --adopt-identity and must not "
            f"offer the Adopt identity button")


# -- _build_cmd refuses server-side too --------------------------------------


def _meta(tmp_path, supported):
    script = _script(tmp_path, "")
    return {"name": "fake", "dir": str(tmp_path), "script": script.name,
            "python": "python", "login_flag": "--login",
            "supported_actions": supported,
            "accounts": [{"name": "primary"}], "has_venv": False}


def test_build_cmd_refuses_an_action_the_script_does_not_support(tmp_path):
    """Defense in depth behind the hidden button: even a direct call must not
    reach a subprocess for an action this app never advertised."""
    with pytest.raises(HTTPException):
        panel._build_cmd(_meta(tmp_path, ["login", "discover"]), "primary", "adopt")


def test_build_cmd_allows_an_action_the_script_does_support(tmp_path):
    cmd = panel._build_cmd(_meta(tmp_path, ["login", "discover", "adopt"]),
                           "primary", "adopt")
    assert "--adopt-identity" in cmd
