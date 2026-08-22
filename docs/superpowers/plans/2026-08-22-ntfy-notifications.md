# ntfy notifications for unattended runs — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tell a person, by ntfy push, when a scheduled unattended pass parks an account or errors — and stay silent otherwise.

**Architecture:** A notifier module in the core (`notify.py`, standard library only, never raises, no-op when unconfigured) with `tools/schedule.py` as its only caller. The scheduler learns *why* an account parked by reading the reason the app already wrote into that account's `sentinel.json`, so no outbound call is ever made from the app layer. One digest per pass, only when there is something to say.

**Tech Stack:** Python 3.11+, standard library `urllib.request`, pytest, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-08-22-ntfy-notifications-design.md` — read it first. It carries the context a reader who has never seen this codebase needs, and the two judgement calls (exit 4 counts as an error; one digest rather than per-account pushes) that this plan implements without re-deciding.

## Global Constraints

- **Python 3.11+** (`core/pyproject.toml`'s `requires-python = ">=3.11"`). No syntax newer than 3.11.
- **No new runtime dependencies.** `paperpull-core` depends only on `pypdf[crypto]`. The notifier uses `urllib.request` from the standard library — `tools/docker_smoke.py` already sets that precedent. If a task seems to need a dependency, stop and ask.
- **`notify.send` must never raise, never block long, and be a no-op when unconfigured.** A failed notification must never turn a working document run into a failed one. This is the reason the notifier is a module rather than a few inline lines.
- **No test may make a network call.** The notifier takes an injected `opener`; the scheduler's digest is asserted by inspecting what it would send.
- **Read-only, always.** Nothing here authenticates, navigates a provider, or touches a provider at all.
- **A parked account is a normal state, not a failure** — it exits 0, deliberately. That is why the exit code alone cannot tell a park from a clean run, and why the sentinel must be read.
- **Use `python3`**, not `python`, for local commands; `.venv/bin/python` where the panel's dependencies are needed. Bare `python` does not exist on this host. Inside the container `python` is correct.
- **Comment density matches the surrounding code.** This codebase explains *why* at length. `core/paperpull_core/sentinel.py` and `tools/schedule.py` are the house style: plain prose, no markdown bold or numbered headers in docstrings.
- **Commit style is this repo's, NOT conventional commits.** Imperative sentence case, no `feat:`/`fix:` prefix. `git log --oneline -8` shows it. End every commit message with:
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`
- Baselines, all green, and the counts must go **up**: `core` 147, `gui` 107, `apps/youfone` 138, `apps/simyo` 97.

---

## File Structure

**Created:**

| File | Responsibility |
|---|---|
| `core/paperpull_core/notify.py` | Send one message to an ntfy topic. The only place in the project that makes an outbound call. |
| `core/tests/test_notify.py` | Tests for it, with an injected opener — never a real request. |

**Modified:**

| File | Change |
|---|---|
| `core/paperpull_core/sentinel.py` | `PARKED_REASON_KEY` constant and a `parked_reason()` accessor. |
| `core/tests/test_sentinel.py` | Tests for the accessor. |
| `core/paperpull_core/appload.py` | Extract `session_record()`; add `parked_reason` to each account record. |
| `core/tests/test_appload.py` | Tests for both. |
| `tools/schedule.py` | Classify each run's outcome, build one digest, send it. |
| `gui/tests/test_scheduler.py` | Tests for the classification and the digest. |
| `docker-compose.yml` | The two env vars on the `scheduler` service. |
| `.env.example` | The same two, documented. |
| `SECURITY.md` | What now leaves your machine. |
| `docs/docker.md` | Setup, and what will and will not notify. |
| `CHANGELOG.md`, `VERSION`, `core/pyproject.toml`, `core/paperpull_core/__init__.py` | Release. |

**Why this shape:** the decision (what does an outcome mean?) and the transport (how is a message sent?) are separate and separately testable; the app layer is not touched at all.

---

### Task 1: The parked reason becomes readable

**Files:**
- Modify: `core/paperpull_core/sentinel.py`
- Test: `core/tests/test_sentinel.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `sentinel.PARKED_REASON_KEY` (str, `"parked_reason"`); `sentinel.parked_reason(store) -> str`.

**Context:** `park()` has written a reason string since parking existed, and nothing has ever read it. The scheduler needs it to say *why* a human is needed rather than only that one is. Note the existing comment above `STATE_KEY`/`LAST_ALIVE_KEY`: those constants exist because `appload` reads the same file as plain JSON and a literal over there could not follow a rename here. The new constant exists for exactly the same reason, and Task 3 is the reader.

- [ ] **Step 1: Write the failing tests**

Append to `core/tests/test_sentinel.py` (it already has the `store_at(tmp_path)` helper — use it, do not redefine it):

```python
def test_the_parked_reason_round_trips(tmp_path):
    """The scheduler says why a human is needed, not merely that one is."""
    store = store_at(tmp_path)
    sentinel.park(store, "no signed-in tab", "2026-08-22T03:00:00")
    assert sentinel.parked_reason(store_at(tmp_path)) == "no signed-in tab"


def test_a_warm_session_has_no_parked_reason(tmp_path):
    store = store_at(tmp_path)
    sentinel.park(store, "no signed-in tab", "2026-08-22T03:00:00")
    sentinel.mark_warm(store, "2026-08-22T04:00:00")
    assert sentinel.parked_reason(store_at(tmp_path)) == ""


def test_an_account_with_no_session_yet_has_no_parked_reason(tmp_path):
    assert sentinel.parked_reason(store_at(tmp_path)) == ""
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd core && python3 -m pytest tests/test_sentinel.py -v`
Expected: FAIL with `AttributeError: module 'paperpull_core.sentinel' has no attribute 'parked_reason'`

- [ ] **Step 3: Add the constant and the accessor**

In `core/paperpull_core/sentinel.py`, add `PARKED_REASON_KEY` to the existing constant block, extending its comment to cover the third field:

```python
STATE_KEY = "state"
LAST_ALIVE_KEY = "last_verified_alive"
PARKED_REASON_KEY = "parked_reason"
```

Replace the two `"parked_reason"` string literals already in `mark_warm` and `park` with `PARKED_REASON_KEY`, so the constant is the single spelling. Then add, after `session_state`:

```python
def parked_reason(store) -> str:
    """Why this account is parked, in the app's own words.

    Written by park() since parking existed and read by nothing until the
    scheduler needed to tell a person WHY they are needed rather than only
    that they are. Empty for a warm session, because mark_warm clears it -
    so this doubles as "is there anything to say about this account?".
    """
    return (store.get(SESSION_KEY) or {}).get(PARKED_REASON_KEY) or ""
```

- [ ] **Step 4: Run them to verify they pass**

Run: `cd core && python3 -m pytest tests/test_sentinel.py -v`
Expected: PASS

Then the full suite: `cd core && python3 -m pytest -q` → 147 before, 150 after.

- [ ] **Step 5: Commit**

```bash
git add core/paperpull_core/sentinel.py core/tests/test_sentinel.py
git commit -m "Let something read the reason an account parked

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: The notifier

**Files:**
- Create: `core/paperpull_core/notify.py`
- Test: `core/tests/test_notify.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `notify.URL_VAR` (`"PAPERPULL_NTFY_URL"`), `notify.TOKEN_VAR` (`"PAPERPULL_NTFY_TOKEN"`), `notify.TIMEOUT_SECONDS` (int); `notify.configured(env=None) -> bool`; `notify.send(title: str, message: str, tags=(), priority=None, *, env=None, opener=None) -> bool`.

**Context:** this is the only place in the project that makes an outbound call, in a project whose stated identity is that it reads and never phones home. Its three properties are the reason it is a module: it never raises, it never blocks long, and it does nothing at all when unconfigured.

- [ ] **Step 1: Write the failing tests**

Create `core/tests/test_notify.py`:

```python
"""The one place PaperPull makes an outbound call.

Nothing here touches the network: `send` takes an `opener`, and every test
passes a fake one. A test that reached ntfy.sh would be a test that fails
when someone runs the suite on a train.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paperpull_core import notify


class FakeResponse:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """Stands in for urllib.request.urlopen, and remembers what it was given."""

    def __init__(self, status=200, raises=None):
        self.status = status
        self.raises = raises
        self.calls = []

    def __call__(self, request, timeout=None):
        self.calls.append((request, timeout))
        if self.raises is not None:
            raise self.raises
        return FakeResponse(self.status)


CONFIGURED = {"PAPERPULL_NTFY_URL": "https://ntfy.example.com/paperpull"}


def test_an_install_that_set_nothing_is_not_configured():
    assert notify.configured({}) is False
    assert notify.configured({"PAPERPULL_NTFY_URL": "   "}) is False


def test_a_url_makes_it_configured():
    assert notify.configured(CONFIGURED) is True


def test_sending_while_unconfigured_does_nothing_at_all():
    """Not an error. An install that never set the variable is not broken."""
    opener = FakeOpener()
    assert notify.send("t", "m", env={}, opener=opener) is False
    assert opener.calls == []


def test_a_send_posts_the_message_to_the_topic():
    opener = FakeOpener()
    assert notify.send("Two need you", "youfone/primary - no signed-in tab",
                       env=CONFIGURED, opener=opener) is True
    request, timeout = opener.calls[0]
    assert request.full_url == "https://ntfy.example.com/paperpull"
    assert request.get_method() == "POST"
    assert request.data == b"youfone/primary - no signed-in tab"
    assert request.get_header("Title") == "Two need you"
    assert timeout == notify.TIMEOUT_SECONDS


def test_tags_and_priority_ride_along_when_given():
    opener = FakeOpener()
    notify.send("t", "m", tags=("warning", "bell"), priority=4,
                env=CONFIGURED, opener=opener)
    request, _ = opener.calls[0]
    assert request.get_header("Tags") == "warning,bell"
    assert request.get_header("Priority") == "4"


def test_no_tags_or_priority_means_no_such_headers():
    opener = FakeOpener()
    notify.send("t", "m", env=CONFIGURED, opener=opener)
    request, _ = opener.calls[0]
    assert request.get_header("Tags") is None
    assert request.get_header("Priority") is None


def test_a_token_becomes_a_bearer_header():
    opener = FakeOpener()
    notify.send("t", "m", opener=opener,
                env={**CONFIGURED, "PAPERPULL_NTFY_TOKEN": "tk_secret"})
    request, _ = opener.calls[0]
    assert request.get_header("Authorization") == "Bearer tk_secret"


def test_no_token_means_no_authorization_header():
    opener = FakeOpener()
    notify.send("t", "m", env=CONFIGURED, opener=opener)
    request, _ = opener.calls[0]
    assert request.get_header("Authorization") is None


def test_an_unreachable_server_is_reported_not_raised():
    """The whole point of this module. A missed notification must never
    turn a working document run into a failed one."""
    opener = FakeOpener(raises=OSError("no route to host"))
    assert notify.send("t", "m", env=CONFIGURED, opener=opener) is False


def test_a_refused_message_is_reported_not_raised():
    opener = FakeOpener(status=403)
    assert notify.send("t", "m", env=CONFIGURED, opener=opener) is False
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd core && python3 -m pytest tests/test_notify.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'paperpull_core.notify'`

- [ ] **Step 3: Write the module**

Create `core/paperpull_core/notify.py`:

```python
"""Telling a person that a scheduled run needs them.

This is the only place in PaperPull that makes an outbound call. Everything
else here reads: it attaches to a browser you signed in to yourself and takes
copies of your own documents. So the bar for this file is not "does it work"
but "can it ever hurt the thing it reports on", and the answer has to be no.

Three properties, and they are why this is a module rather than four lines
inside the scheduler:

* It never raises. Any failure - unreachable host, DNS, timeout, a refusal,
  a malformed URL - is logged and returns False. The caller is in the middle
  of a document run; a notification that could break one would be worse than
  no notification at all.
* It never blocks for long, and never retries. A missed message is strictly
  better than a scheduler wedged on a socket until someone notices.
* It does nothing when unconfigured, and says nothing about it. An install
  that never set PAPERPULL_NTFY_URL has not made a mistake.

WHAT A MESSAGE COSTS YOU

ntfy topics on ntfy.sh have no authentication: the topic name IS the
credential, and anyone who learns it can read everything sent to it. What
this project sends names the provider and the account label ("youfone/primary
- no signed-in tab"), which is enough to act on from a phone and enough to
tell a reader which providers you use. Pick an unguessable topic name, or run
your own server. PAPERPULL_NTFY_TOKEN is honoured for a protected topic but
is not required - see SECURITY.md.
"""
from __future__ import annotations

import logging
import os
import urllib.request
from typing import Iterable, Optional

log = logging.getLogger("paperpull_core.notify")

URL_VAR = "PAPERPULL_NTFY_URL"
TOKEN_VAR = "PAPERPULL_NTFY_TOKEN"

# Long enough for a slow phone-home, short enough that a black-holed host
# cannot hold up a scheduled pass. There is deliberately no retry.
TIMEOUT_SECONDS = 10


def _env(env):
    return os.environ if env is None else env


def configured(env=None) -> bool:
    """Has anyone asked to be notified?"""
    return bool((_env(env).get(URL_VAR) or "").strip())


def send(title: str, message: str, tags: Iterable[str] = (),
         priority: Optional[int] = None, *, env=None, opener=None) -> bool:
    """Post one message to the configured topic. True if it was accepted.

    `opener` stands in for urllib.request.urlopen so the tests can assert what
    would be sent without anything leaving the machine.
    """
    url = (_env(env).get(URL_VAR) or "").strip()
    if not url:
        return False

    headers = {"Title": title, "Content-Type": "text/plain; charset=utf-8"}
    tags = tuple(tags)
    if tags:
        headers["Tags"] = ",".join(tags)
    if priority is not None:
        headers["Priority"] = str(priority)
    token = (_env(env).get(TOKEN_VAR) or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(
        url, data=message.encode("utf-8"), headers=headers, method="POST")
    open_it = urllib.request.urlopen if opener is None else opener

    # Deliberately every exception, not a chosen few. urllib raises URLError,
    # HTTPError, socket.timeout, ssl.SSLError and UnicodeEncodeError (a
    # non-ASCII title is a header, and headers are latin-1) for causes that
    # all mean the same thing here: the message did not arrive, and the run
    # this is reporting on must carry on regardless.
    try:
        with open_it(request, timeout=TIMEOUT_SECONDS) as response:
            status = getattr(response, "status", 0) or 0
    except Exception as e:
        log.warning("could not notify: %s", e)
        return False

    if not 200 <= status < 300:
        log.warning("notification refused: HTTP %s", status)
        return False
    return True
```

- [ ] **Step 4: Run them to verify they pass**

Run: `cd core && python3 -m pytest tests/test_notify.py -v`
Expected: PASS, 10 tests.

Then: `cd core && python3 -m pytest -q` → 160.

- [ ] **Step 5: Commit**

```bash
git add core/paperpull_core/notify.py core/tests/test_notify.py
git commit -m "Add the one place PaperPull calls out, and make it unable to break a run

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: The scheduler can ask what happened to one account

**Files:**
- Modify: `core/paperpull_core/appload.py`
- Test: `core/tests/test_appload.py`

**Interfaces:**
- Consumes: `sentinel.SESSION_KEY`, `sentinel.STATE_KEY`, `sentinel.PARKED_REASON_KEY`, `sentinel.PARKED` (Task 1).
- Produces: `appload.session_record(output_dir) -> dict`; every record from `appload.accounts()` gains `"parked_reason": str`.

**Context:** `accounts()` already reads each account's `sentinel.json` inline. Task 4 needs the same answer about **one** account immediately after running it — because a park and a clean run both exit 0, so the exit code cannot tell them apart. Extracting the read gives both callers one implementation instead of two that can drift.

Read the existing comment block above the `output_dir = app_dir / output_dir` line before you touch this function. It records a real bug: unresolved relative paths made every account look never-parked and always-due. Do not disturb that resolution.

- [ ] **Step 1: Write the failing tests**

Append to `core/tests/test_appload.py`, following the fake-tree style already in that file:

```python
def test_the_session_record_is_the_sentinels_session_block(tmp_path):
    (tmp_path / "sentinel.json").write_text(
        '{"session": {"state": "parked", "parked_reason": "no signed-in tab"}}',
        encoding="utf-8")
    assert appload.session_record(tmp_path) == {
        "state": "parked", "parked_reason": "no signed-in tab"}


def test_a_missing_or_broken_sentinel_is_an_empty_record(tmp_path):
    """Never an exception: one unreadable account must not end the pass."""
    assert appload.session_record(tmp_path) == {}
    (tmp_path / "sentinel.json").write_text("[1, 2, 3]", encoding="utf-8")
    assert appload.session_record(tmp_path) == {}
    (tmp_path / "sentinel.json").write_text("{not json", encoding="utf-8")
    assert appload.session_record(tmp_path) == {}


def test_a_non_dict_session_block_is_an_empty_record(tmp_path):
    (tmp_path / "sentinel.json").write_text('{"session": "nope"}',
                                            encoding="utf-8")
    assert appload.session_record(tmp_path) == {}
```

And a test that the reason reaches the account records. Build the fake tree the way the existing tests in this file do — an app directory with an entry script and a `config.json`, plus the sentinel:

```python
def test_accounts_carry_the_parked_reason(tmp_path):
    app_dir = tmp_path / "apps" / "testco"
    app_dir.mkdir(parents=True)
    (app_dir / "testco_docs.py").write_text("", encoding="utf-8")
    out = tmp_path / "data" / "testco"
    out.mkdir(parents=True)
    (app_dir / "config.json").write_text(
        '{"output_dir": "%s"}' % out.as_posix(), encoding="utf-8")
    (out / "sentinel.json").write_text(
        '{"session": {"state": "parked", "parked_reason": "identity unproven"}}',
        encoding="utf-8")

    record = appload.accounts(tmp_path / "apps", None)[0]
    assert record["parked"] is True
    assert record["parked_reason"] == "identity unproven"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd core && python3 -m pytest tests/test_appload.py -v`
Expected: FAIL with `AttributeError: module 'paperpull_core.appload' has no attribute 'session_record'`, and a `KeyError: 'parked_reason'`.

- [ ] **Step 3: Extract the read and add the field**

Add to `core/paperpull_core/appload.py`, above `accounts()`:

```python
def session_record(output_dir) -> dict:
    """The `session` block of this account's sentinel.json, or {}.

    Shared with tools/schedule.py, which asks the same question about a single
    account the moment its run finishes. It has to ask, because the run's exit
    code cannot answer it: a parked run and a clean run both exit 0, on
    purpose, so that cron alerting stays worth reading.

    Every failure is {} rather than an exception. One account with an
    unreadable sentinel must not end a pass that has fifteen others in it.
    """
    try:
        record = json.loads(
            (Path(output_dir) / "sentinel.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(record, dict):
        return {}
    session = record.get(sentinel.SESSION_KEY) or {}
    return session if isinstance(session, dict) else {}
```

In `accounts()`, replace the inline `try/except` that builds `session` with a call to it, keeping the surrounding `output_dir` resolution exactly as it is:

```python
            session = session_record(output_dir)
            last_alive = str(session.get(sentinel.LAST_ALIVE_KEY) or "")
```

and add the new field to the appended record, immediately after `"parked"`:

```python
                "parked": session.get(sentinel.STATE_KEY) == sentinel.PARKED,
                "parked_reason": str(
                    session.get(sentinel.PARKED_REASON_KEY) or ""),
```

- [ ] **Step 4: Run them to verify they pass**

Run: `cd core && python3 -m pytest tests/test_appload.py -v`
Expected: PASS

Then: `cd core && python3 -m pytest -q` → 164.

- [ ] **Step 5: Commit**

```bash
git add core/paperpull_core/appload.py core/tests/test_appload.py
git commit -m "Read one account's session in one place, and carry why it parked

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: The scheduler says what a pass meant

**Files:**
- Modify: `tools/schedule.py`
- Test: `gui/tests/test_scheduler.py`

**Interfaces:**
- Consumes: `notify.send`, `notify.configured` (Task 2); `appload.session_record` (Task 3); `sentinel.STATE_KEY`, `sentinel.PARKED`, `sentinel.PARKED_REASON_KEY` (Task 1).
- Produces: `schedule.PARKED` (`"parked"`), `schedule.ERROR` (`"error"`), `schedule.TERMINATED_EXIT` (`143`); `schedule.outcome(account, code, session) -> tuple[str, str] | None`; `schedule.digest(parked, errors) -> tuple[str, str, tuple, int] | None`; `schedule.one_pass(...) -> tuple[list, list]`; `schedule.pass_and_notify(apps_root, config_root, today) -> int`.

**Context:** `tools/schedule.py` has no test suite of its own — its tests live in `gui/tests/test_scheduler.py`, and that file's own docstring says why: the scheduler and the panel are twins that answer the same two questions independently, and testing them side by side is what makes a divergence visible. Follow that. Nothing in that file starts a real subprocess; `run_one` is tested by replacing `subprocess.run` with a stub.

`one_pass` currently returns `None` and prints as it goes. It gains a return value so the digest can be built from what it saw. Keep every existing `print` — the log is what a person reads when they are already looking.

- [ ] **Step 1: Write the failing tests**

Append to `gui/tests/test_scheduler.py`, using the module-loading helper already in that file (`_schedule()`):

```python
# -- what a run meant, and what gets said about it -------------------------

def _account(app="youfone", account="primary"):
    return {"app": app, "account": account, "output_dir": "/data/youfone",
            "config": "/config/youfone/config.json"}


def test_a_parked_run_is_something_a_person_must_act_on():
    schedule = _schedule()
    session = {"state": "parked", "parked_reason": "no signed-in tab"}
    assert schedule.outcome(_account(), 0, session) == (
        schedule.PARKED, "no signed-in tab")


def test_a_clean_run_says_nothing():
    """The exit code is 0 for both, which is why the session is consulted."""
    schedule = _schedule()
    assert schedule.outcome(_account(), 0, {"state": "warm"}) is None


def test_a_parked_run_with_no_reason_still_asks_for_a_person():
    schedule = _schedule()
    kind, why = schedule.outcome(_account(), 0, {"state": "parked"})
    assert kind == schedule.PARKED
    assert why


def test_being_terminated_is_not_an_error():
    """143 is the container being stopped, or someone pressing Stop."""
    schedule = _schedule()
    assert schedule.outcome(_account(), schedule.TERMINATED_EXIT, {}) is None


def test_a_busy_provider_is_reported():
    """Transient, but if it stops being transient a wedged lock means this
    account silently never runs again - which is what this exists to end."""
    schedule = _schedule()
    kind, why = schedule.outcome(_account(), 4, {})
    assert kind == schedule.ERROR
    assert "4" in why


def test_any_other_non_zero_exit_is_an_error():
    schedule = _schedule()
    assert schedule.outcome(_account(), 1, {})[0] == schedule.ERROR


def test_a_quiet_pass_has_nothing_to_send():
    schedule = _schedule()
    assert schedule.digest([], []) is None


def test_the_digest_names_each_account_and_why():
    schedule = _schedule()
    title, body, tags, priority = schedule.digest(
        [("youfone/primary", "no signed-in tab")],
        [("ally/primary", "exited 1")])
    assert "youfone/primary" in body and "no signed-in tab" in body
    assert "ally/primary" in body and "exited 1" in body
    assert "1" in title
    assert tags and priority


def test_the_digest_says_how_many_need_a_person():
    schedule = _schedule()
    title, _, _, _ = schedule.digest(
        [("a/one", "why"), ("b/two", "why")], [])
    assert "2" in title


def test_a_pass_that_blows_up_still_tells_someone(monkeypatch):
    """Otherwise the one failure that hides every other failure is this
    module's own."""
    schedule = _schedule()
    sent = []
    monkeypatch.setattr(schedule.notify, "send",
                        lambda *a, **k: sent.append((a, k)) or True)
    monkeypatch.setattr(schedule, "one_pass",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    code = schedule.pass_and_notify(Path("/nowhere"), None, "2026-08-22")
    assert code != 0
    assert sent, "a pass that raised sent nothing"
    assert "boom" in str(sent[0])


def test_a_quiet_pass_sends_nothing(monkeypatch):
    schedule = _schedule()
    sent = []
    monkeypatch.setattr(schedule.notify, "send",
                        lambda *a, **k: sent.append((a, k)) or True)
    monkeypatch.setattr(schedule, "one_pass", lambda *a, **k: ([], []))
    assert schedule.pass_and_notify(Path("/nowhere"), None, "2026-08-22") == 0
    assert sent == []
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd gui && ../.venv/bin/python -m pytest tests/test_scheduler.py -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'outcome'`

- [ ] **Step 3: Implement**

In `tools/schedule.py`, extend the import to `from paperpull_core import appload, due, notify, sentinel`, and add near the other module constants:

```python
# What an account's run meant to a person.
PARKED = "parked"
ERROR = "error"

# 128 + SIGTERM: the container was stopped, or someone pressed Stop in the
# panel. Not a failure, and not worth waking anyone for.
TERMINATED_EXIT = 143
```

Add, above `one_pass`:

```python
def outcome(account, code, session):
    """What this run means to a person: a park, an error, or nothing.

    The session is consulted because the exit code cannot answer this on its
    own - a parked run exits 0 exactly like a clean one, deliberately, so
    that a non-zero exit keeps meaning "something is broken".
    """
    if code == 0:
        if session.get(sentinel.STATE_KEY) != sentinel.PARKED:
            return None
        why = session.get(sentinel.PARKED_REASON_KEY) or "needs a person"
        return (PARKED, why)
    if code == TERMINATED_EXIT:
        return None
    return (ERROR, f"exited {code}")


def digest(parked, errors):
    """One message for the whole pass, or None if there is nothing to say.

    One message rather than one per account: a four-account bad night should
    buzz once. The cost is that a single item cannot be dismissed on its own.
    """
    if not parked and not errors:
        return None
    counts = []
    if parked:
        counts.append(f"{len(parked)} need you")
    if errors:
        counts.append(f"{len(errors)} error" + ("s" if len(errors) > 1 else ""))
    title = "PaperPull: " + ", ".join(counts)

    lines = []
    if parked:
        lines.append("Needs you:")
        lines += [f"  {who} - {why}" for who, why in parked]
    if errors:
        if lines:
            lines.append("")
        lines.append("Errors:")
        lines += [f"  {who} - {why}" for who, why in errors]

    tags = ("rotating_light",) if errors else ("warning",)
    priority = 4 if errors else 3
    return (title, "\n".join(lines), tags, priority)
```

Change `one_pass` to collect and return. Keep every existing `print`; add the classification after `run_one`:

```python
def one_pass(apps_root: Path, config_root, today: str):
    """Run everything due that can run itself. Returns (parked, errors)."""
    accounts = appload.accounts(apps_root, config_root)
    plan = due.plan(accounts, today)
    patient = [a for a in plan if a["session_lifetime_minutes"] is None]
    perishable = [a for a in plan if a["session_lifetime_minutes"] is not None]

    print(f"[{datetime.now():%Y-%m-%d %H:%M}] "
          f"{len(plan)} due: {len(patient)} unattended, "
          f"{len(perishable)} need a person")

    parked, errors = [], []
    for account in patient:
        print(f"  {account['app']}/{account['account']}")
        code = run_one(account, apps_root)
        if code:
            print(f"  ! exited {code}")
        # Re-read after the run: the app writes the reason as it parks, and
        # this is where it is read back. See appload.session_record.
        result = outcome(account, code, appload.session_record(
            account["output_dir"]))
        if result is None:
            continue
        kind, why = result
        who = f"{account['app']}/{account['account']}"
        (parked if kind == PARKED else errors).append((who, why))

    for account in perishable:
        # Deliberately not started. Its session would be dead before a
        # download finished, and a failed attempt teaches the provider's fraud
        # model something about us for nothing.
        print(f"  waiting for a person: {account['app']}/{account['account']} "
              f"(session lasts {account['session_lifetime_minutes']} min)")
        parked.append((f"{account['app']}/{account['account']}",
                       "needs a person to sign in"))

    return parked, errors


def pass_and_notify(apps_root: Path, config_root, today: str) -> int:
    """One pass, and one message about it if there is anything to say.

    The try/except is not decoration: without it, the single failure that
    hides every other failure is this module's own. A pass that dies before
    it can report is exactly the silence the notifications exist to end.
    """
    try:
        parked, errors = one_pass(apps_root, config_root, today)
    except Exception as e:
        print(f"  !! the pass failed: {e}")
        notify.send("PaperPull: the scheduled pass failed", str(e),
                    tags=("rotating_light",), priority=5)
        return 1

    said = digest(parked, errors)
    if said is None:
        return 0
    title, body, tags, priority = said
    notify.send(title, body, tags=tags, priority=priority)
    return 0
```

Then replace both call sites of `one_pass` in `main()` — the `--once` branch and the loop — with `pass_and_notify`, returning its code from the `--once` path.

- [ ] **Step 4: Run them to verify they pass**

Run: `cd gui && ../.venv/bin/python -m pytest tests/test_scheduler.py -v`
Expected: PASS

Then all four suites:
```bash
cd gui && ../.venv/bin/python -m pytest tests/ -q          # 107 before, 118 after
cd core && python3 -m pytest -q                             # 164
cd apps/youfone && PYTHONPATH=../../core python3 -m pytest tests/ -q   # 138, unchanged
cd apps/simyo && PYTHONPATH=../../core python3 -m pytest tests/ -q     # 97, unchanged
```

And a real dry run against a throwaway tree, which must send nothing and exit cleanly:
```bash
python3 tools/schedule.py --once
```

- [ ] **Step 5: Commit**

```bash
git add tools/schedule.py gui/tests/test_scheduler.py
git commit -m "Tell a person when a pass parked an account or went wrong

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Configure it, document what it costs, release it

**Files:**
- Modify: `docker-compose.yml`, `.env.example`, `SECURITY.md`, `docs/docker.md`, `CHANGELOG.md`, `VERSION`, `core/pyproject.toml`, `core/paperpull_core/__init__.py`

**Interfaces:**
- Consumes: `notify.URL_VAR`, `notify.TOKEN_VAR` (Task 2).
- Produces: nothing importable.

**Context:** the code is useless until an operator can turn it on, and dangerous to ship silently — this is the first thing the stack sends out. Two release traps this repo has already hit once: `core/paperpull_core/__init__.py`'s `__version__` and `core/pyproject.toml`'s `version` must agree, because `tools/check_installs.py` compares them and a mismatch reports every correctly-updated install as stale; and `CHANGELOG.md`'s own rules classify a cross-app feature as MINOR.

- [ ] **Step 1: Add the two variables to the scheduler service**

In `docker-compose.yml`, in the `scheduler` service's `environment:` block, after `PAPERPULL_SCHEDULE_HOUR`:

```yaml
      # Optional, and off unless you set it. Where to tell a person that a
      # pass parked an account or went wrong - the full topic URL, e.g.
      # https://ntfy.sh/<something-unguessable>. Nothing else in PaperPull
      # makes an outbound call; read the ntfy section of SECURITY.md before
      # you set this, because on ntfy.sh the topic name is the only thing
      # keeping strangers out.
      PAPERPULL_NTFY_URL: ${PAPERPULL_NTFY_URL:-}
      # Only for a topic that requires one - a server of your own, usually.
      PAPERPULL_NTFY_TOKEN: ${PAPERPULL_NTFY_TOKEN:-}
```

- [ ] **Step 2: Document them in `.env.example`**

Before the `# --- image ---` block:

```bash
# --- notifications (optional) --------------------------------------------
# Where to be told that a scheduled pass parked an account or hit an error.
# Empty means no notifications, which is the default. The full topic URL:
#   PAPERPULL_NTFY_URL=https://ntfy.sh/paperpull-8f3c1a9e7b
# On ntfy.sh a topic has no password and the name is the only secret, so
# pick something nobody will guess - or point this at your own server.
PAPERPULL_NTFY_URL=
# Only if your topic requires a token.
PAPERPULL_NTFY_TOKEN=
```

- [ ] **Step 3: Say what now leaves the machine, in `SECURITY.md`**

Add a section near the existing discussion of what is sensitive. It must say, in this document's plain voice: that until now nothing was sent anywhere and this is the first outbound call; that it happens only from the scheduler, only for an unattended pass, and only when a park or an error occurred; that a message names the provider, the account label and the reason (`youfone/primary - no signed-in tab`) and never a document, an amount, a customer number or anything from a page; that on ntfy.sh the topic name is the credential, so an unguessable name or your own server is the protection, and `PAPERPULL_NTFY_TOKEN` exists for a protected topic; and that leaving `PAPERPULL_NTFY_URL` empty means nothing is ever sent.

- [ ] **Step 4: Document the feature in `docs/docker.md`**

In the scheduling section, after the existing explanation of what the scheduler does and does not run, add a short subsection: how to turn notifications on, what a message looks like, and — explicitly — what does **not** notify and why (a clean pass, a quiet pass, and anything you started yourself from the panel or a terminal, because you are already watching it). Match that document's explanatory voice; it explains why, not only what to type.

- [ ] **Step 5: Release**

- `VERSION`: `0.10.0` → `0.11.0` (MINOR — a cross-app feature, per `CHANGELOG.md`'s own stated rules).
- `core/pyproject.toml`: `version = "0.2.0"` → `"0.3.0"` — the core gains a shipped module, matching the precedent that it bumps for modules an app or tool now needs.
- `core/paperpull_core/__init__.py`: `__version__` → `"0.3.0"`. **These two must match.** Check whether that file's `__all__` lists sibling modules and, if it does, add `notify` alongside them; if it does not, change nothing else.
- `CHANGELOG.md`: a `[0.11.0]` entry in the shape of the existing ones, saying what notifies, what deliberately does not, and that the topic name is the credential.

- [ ] **Step 6: Verify the whole thing**

```bash
BROWSER_USER=x BROWSER_PASSWORD=y PAPERPULL_ALLOWED_HOSTS=localhost \
  docker compose config --quiet && echo YAML_OK
cd core && python3 -m pytest -q
cd gui && ../.venv/bin/python -m pytest tests/ -q
cd apps/youfone && PYTHONPATH=../../core python3 -m pytest tests/ -q
cd apps/simyo && PYTHONPATH=../../core python3 -m pytest tests/ -q
grep -rn "0\.2\.0" core/pyproject.toml core/paperpull_core/__init__.py || echo "versions agree"
```

Then prove the feature end to end **without a real topic**, by pointing it at a local listener you control:

```bash
python3 -m http.server 8123 --bind 127.0.0.1 &
PAPERPULL_NTFY_URL=http://127.0.0.1:8123/test python3 -c "
import sys; sys.path.insert(0, 'core')
from paperpull_core import notify
print('sent:', notify.send('PaperPull: 1 needs you', 'youfone/primary - no signed-in tab'))
"
```
`http.server` answers a POST with 501, so `send` must print `sent: False` and **must not raise** — that is the failure path working. Kill the server afterwards.

- [ ] **Step 7: Commit**

```bash
git add docker-compose.yml .env.example SECURITY.md docs/docker.md CHANGELOG.md VERSION core/pyproject.toml core/paperpull_core/__init__.py
git commit -m "Turn notifications on from the compose file, and say what they cost

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage.** Notifier module with its three properties → Task 2. `sentinel.parked_reason` → Task 1. `appload.parked_reason` + shared session read → Task 3. Exit-code classification table (park / 4 / 143 / 2 / other / skipped) → Task 4's `outcome`, one test per row. One digest per pass → Task 4's `digest`. Pass-level guard → Task 4's `pass_and_notify`. Config vars → Task 5. Payload rule (provider + account + reason, nothing from a page) → Task 4's `digest` builds only from `app`, `account` and the reason string; enforced in `SECURITY.md` by Task 5. Docs and release → Task 5. Out-of-scope items (no app-layer egress, no retries, no success notifications) are respected by construction: the app layer is not modified in any task.

**Type consistency.** `outcome` returns `tuple[str, str] | None` and `digest` takes two lists of `(who, why)` pairs — the shape `one_pass` builds. `notify.send`'s signature is identical in Task 2's definition and Task 4's two call sites. `session_record` returns a plain `dict`, which is what both `accounts()` and `outcome`'s third argument expect.

**One gap I am leaving deliberately.** A perishable account (Simyo) that is due is added to `parked` in `one_pass` with "needs a person to sign in", so the notification covers the accounts that can *only* be done by a human — which is the case the spec's "needs a human" class is really about. It is not a park in the sentinel sense, and the code says so at the call site.
