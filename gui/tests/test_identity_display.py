"""The identity warning must not lie to the 16 apps that have no gate.

Finding 1 from code review: before this fix, `showIdentity()`'s "No identity
recorded... press Adopt identity" warning and `accountLabel`'s "· unidentified"
suffix were shown for EVERY app whenever `identified` is false - which, for
every app except Simyo and Youfone, is *always*, because nothing ever calls
`ensure_identity` for them. Hiding the Adopt identity button (the fix in
gui/app.py's `_supported_actions` / `updateActionButtons`) made this WORSE,
not neutral: the button that the warning tells you to press no longer
exists, for the same 16 apps the warning still nags at.

The fix is `identityGated(m)` - true only when `m.supported_actions`
includes "adopt" - gating both false claims. This is client-side JS with no
existing browser-test harness in this suite (see gui/tests' other
`panel.HTML` checks, which are plain substring assertions). Rather than write
an assertion that cannot actually fail if the logic were deleted, this file
extracts the REAL function bodies out of `panel.HTML` by name and executes
them under Node (present in this environment - `node --version` succeeds),
with a minimal DOM/`$`/`META` stub. That is "reaching" the fix: if a future
edit renames or deletes `identityGated`, `onApp`, or `showIdentity`, or
changes what they do, the extraction or the assertions below fail - a hand-
copied duplicate of the JS would not catch either kind of drift.

What this file does NOT do: render the page in a real browser (no Playwright
browser context is wired into this suite) or click through the actual
`<select>`/button elements. That gap is real; the extraction-and-eval
approach below is the closest reach available without adding a browser
dependency to a panel that has never needed one.
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


def _extract_function(html: str, name: str) -> str:
    """The literal source of `function <name>(...) { ... }` inside panel.HTML.

    Brace-balanced, not a one-line regex: onApp() and showIdentity() both
    contain nested `{}` (object literals, arrow functions, if-blocks), so a
    naive "up to the first closing brace" match would truncate them.
    Raises if the function cannot be found, rather than silently running an
    empty stub - a rename in gui/app.py must fail this test, not pass it
    vacuously.
    """
    marker = f"function {name}("
    start = html.index(marker)  # raises ValueError if absent - intentional
    brace_start = html.index("{", start)
    depth = 0
    i = brace_start
    while i < len(html):
        if html[i] == "{":
            depth += 1
        elif html[i] == "}":
            depth -= 1
            if depth == 0:
                return html[start:i + 1]
        i += 1
    raise AssertionError(f"unbalanced braces extracting {name}()")


# The DOM/global stubs every extracted function needs. `actionButtons` is
# deliberately a no-op stub (not the real querySelectorAll-based one, which
# needs a real DOM) - button visibility is already covered by
# test_action_gating.py against the real `supported_actions` data; this file
# is only about the identity text and label, which don't depend on it.
_HARNESS_PRELUDE = """
const _store = {};
function $(id) {
  if (!(id in _store)) _store[id] = {};
  return _store[id];
}
class Option { constructor(text, value) { this.text = text; this.value = value; } }
function actionButtons() { return []; }
"""


def _run(app_meta: dict, accounts: list, selected_account: str) -> dict:
    """Run the real onApp() + showIdentity() extracted from panel.HTML,
    against one fake app/account list, and report what a user would see:
    every account's <option> label, and the identity paragraph's text."""
    if NODE is None:
        pytest.skip("node is not available in this environment")

    meta = {"root_app": {**app_meta, "accounts": accounts}}
    script = "\n".join([
        _HARNESS_PRELUDE,
        f"let META = {json.dumps({'apps': meta})};",
        "_store['app'] = { value: 'root_app' };",
        f"_store['account'] = {{ value: {json.dumps(selected_account)}, "
        "innerHTML: '', appended: [], append(opt) { this.appended.push(opt); } };",
        "_store['identity'] = { textContent: '' };",
        "_store['venvwarn'] = { style: {}, textContent: '' };",
        "_store['actions'] = { innerHTML: '' };",
        _extract_function(panel.HTML, "identityGated"),
        _extract_function(panel.HTML, "updateActionButtons"),
        _extract_function(panel.HTML, "onApp"),
        _extract_function(panel.HTML, "showIdentity"),
        "onApp();",
        "console.log(JSON.stringify({"
        "  labels: _store['account'].appended.map(o => o.text),"
        "  identityText: _store['identity'].textContent"
        "}));",
    ])
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True,
                            timeout=15)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def _account(name="primary", identified=False, state="", anchors=None):
    return {"name": name, "state": state, "last_alive": "",
            "parked_reason": "", "identified": identified,
            "anchors": anchors or []}


# -- an app WITHOUT the identity gate (16 of 18, e.g. amex) -----------------


def test_an_ungated_apps_account_label_carries_no_unidentified_suffix():
    """Before the fix this said "primary · unidentified" for every account of
    every one of these 16 apps, permanently - `identified` is always false
    there because nothing ever calls ensure_identity."""
    out = _run({"supported_actions": ["login", "discover", "pilot", "all",
                                      "resume", "verify"]},
              [_account(identified=False)], "primary")
    assert out["labels"] == ["primary"]


def test_an_ungated_apps_identity_text_is_silent_not_a_dead_end():
    """Before the fix this told the user to press a button that does not
    exist for this app - the dead end code review flagged."""
    out = _run({"supported_actions": ["login", "discover", "pilot", "all",
                                      "resume", "verify"]},
              [_account(identified=False)], "primary")
    assert out["identityText"] == ""
    assert "Adopt identity" not in out["identityText"]


def test_an_ungated_app_still_shows_anchors_if_it_somehow_has_any():
    """The guard falls through to the SAME anchors branch a gated app uses -
    it does not blanket-suppress the identity paragraph, only the false
    "you must adopt" claim. If an ungated app's sentinel ever carried
    anchors (it shouldn't today, but nothing enforces that), they would
    still be shown."""
    out = _run({"supported_actions": ["login"]},
              [_account(identified=True,
                        anchors=[{"id": "123", "date": "2026-01-01"}])],
              "primary")
    assert "123" in out["identityText"]


# -- an app WITH the identity gate (Simyo, Youfone) --------------------------


def test_a_gated_apps_unidentified_account_still_gets_the_real_warning():
    """Negative control: the guard must not silence the warning where it is
    still true and actionable - Simyo/Youfone's own Adopt identity button
    really is there to press."""
    out = _run({"supported_actions": ["login", "discover", "adopt"]},
              [_account(identified=False)], "primary")
    assert out["labels"] == ["primary · unidentified"]
    assert "Adopt identity" in out["identityText"]


def test_a_gated_apps_parked_account_still_shows_parked_not_unidentified():
    """identified=True (already adopted) with state=parked must show the
    session state, not be masked by the identity guard."""
    out = _run({"supported_actions": ["login", "discover", "adopt"]},
              [_account(identified=True, state="parked")], "primary")
    assert out["labels"] == ["primary · needs sign-in"]


def test_a_gated_apps_identified_account_shows_its_anchors():
    out = _run({"supported_actions": ["login", "discover", "adopt"]},
              [_account(identified=True,
                        anchors=[{"id": "555", "date": "2026-08-14"}])],
              "primary")
    assert "555" in out["identityText"]
    assert "2026-08-14" in out["identityText"]
