# Multi-account, scheduling and credentials — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let PaperPull run several accounts per provider on a schedule, unattended, without storing anything that can log in to anything.

**Architecture:** Three new provider-agnostic modules in `paperpull_core` hold the *decisions* as pure functions over data (which account is this tab, is this account due, may this provider run now); each app keeps the *plumbing* in its own `<provider>_docs.py`. Two new declared facts on `AppSpec` (`session_lifetime_minutes`, `concurrency`) split providers that can run on plain cron from ones that need a human present. Credentials are answered by storing none: a per-account `sentinel.json` records identity anchors and session liveness, and an unattended run that finds a dead session parks and exits 0.

**Tech Stack:** Python 3.11+, pytest, Playwright (CDP attach only), FastAPI (the control panel), Docker Compose.

**Spec:** `docs/superpowers/specs/2026-08-21-multi-account-scheduling-credentials.md` — read it first. It records why anchors rather than a whoami endpoint, why upstream's per-account browser profiles cannot work in this fork, and why phase 6 is a gate rather than a build.

## Global Constraints

- **Python 3.11+** (`core/pyproject.toml` `requires-python = ">=3.11"`). No syntax newer than 3.11.
- **No new runtime dependencies.** `paperpull-core` depends only on `pypdf[crypto]`. Nothing in phases 1–5 may add a dependency. If a task seems to need one, stop and ask.
- **Read-only, always.** Never add a write, payment, logout or personal-data endpoint to any provider's URL allowlist (`simyo_site.py:190` `is_safe_url`). Never automate a login. Never navigate a Simyo tab (`simyo_site.py:7-15`).
- **No secret is ever written to disk in phases 1–5.** No password, cookie, token or session id. If a task's output could be replayed against a provider, it is wrong.
- **Commit style follows this repo**, not conventional commits: imperative sentence case, no `feat:`/`fix:` prefix. See `git log --oneline` — e.g. "Answer a run's prompts from the control panel".
- **Tests** live in `core/tests/` and run with `cd core && python -m pytest`. They must not need a browser, a network or a provider.
- **Comment density matches the surrounding code.** This codebase explains *why* at length in module docstrings and inline where a reader would otherwise be surprised. Match it; do not strip it down.
- Simyo's invoice history is a **rolling ~12-month window** (`simyo_site.py:76-84`). Any check that assumes a document stays visible forever is wrong.

---

## File Structure

**Created:**

| File | Responsibility |
|---|---|
| `core/paperpull_core/identity.py` | Pure verdict: do these recorded anchors match what this tab is showing? No browser, no I/O. |
| `core/paperpull_core/sentinel.py` | Read/write the per-account `sentinel.json` records (identity anchors, session liveness). Thin wrapper over `JsonStore`. |
| `core/paperpull_core/due.py` | Pure scheduling decision: which accounts are worth running now, most perishable first. |
| `core/paperpull_core/appload.py` | Load one app's `AppSpec` from its `storage.py` without the module-name collision every app's `storage.py` would otherwise cause. |
| `core/paperpull_core/locks.py` | One-live-session-per-provider, enforced with lock files so it survives a panel restart and a direct CLI run. |
| `core/tests/test_identity.py` | Tests for `identity.py`. |
| `core/tests/test_sentinel.py` | Tests for `sentinel.py`. |
| `core/tests/test_due.py` | Tests for `due.py`. |
| `core/tests/test_locks.py` | Tests for `locks.py`. |
| `tools/due.py` | CLI: print the due list read off the real config and data trees. |
| `tools/schedule.py` | The scheduler loop: run the long-session providers unattended, list the short-session ones for a human sitting. |
| `docs/adr/2026-08-21-no-stored-credentials.md` | The phase 6 decision and the gate any future credential store must pass. |

**Modified:**

| File | Change |
|---|---|
| `core/paperpull_core/storage.py:132-134` | Add `Paths.sentinel_json`. |
| `core/paperpull_core/spec.py:56-84` | Add `AppSpec.session_lifetime_minutes` and `AppSpec.concurrency` + validation. |
| `apps/simyo/storage.py:28-60` | Declare `session_lifetime_minutes=10` on Simyo's spec. |
| `apps/simyo/simyo_docs.py` | Sentinel store, identity verification, `--adopt-identity`, `--unattended`, provider lock. |
| `gui/app.py` | `/api/due`, per-account state column, lock-aware run refusal, the sitting loop. |
| `docker-compose.yml` | The `scheduler` service. |
| `docs/docker.md` | Document scheduling and the sitting. |

**Why these boundaries:** every decision that can be wrong is a pure function in `paperpull_core` with a unit test, and everything that touches a browser stays in the app. That is the existing split (`spec.py`'s docstring: "An app declares one; the shared core does everything else") and it is what makes these changes testable without a provider.

---

# Phase 1 — The manifest: stop silent misfiling

Do this first. It fixes a live data-corruption bug: with two accounts of one provider signed in to the one shared Chrome, a run reads whichever tab it finds and files those documents under the *config's* owner, with no error anywhere.

### Task 1: The identity verdict

**Files:**
- Create: `core/paperpull_core/identity.py`
- Test: `core/tests/test_identity.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `identity.OK`, `identity.MISMATCH`, `identity.UNKNOWN`, `identity.AGED` (all `str`); `identity.pick_anchors(records: Iterable[dict], count: int = 3) -> list[dict]`; `identity.check(recorded: Iterable[dict], observed: Iterable[dict]) -> str`. A *record* is `{"id": str, "date": str}` where `date` is `YYYY-MM-DD` or `""`.

- [ ] **Step 1: Write the failing tests**

```python
# core/tests/test_identity.py
"""Which account is this tab? The decision, with no browser in sight.

The realistic failure this guards against: two Simyo accounts signed in to the
one shared Chrome, and a run for account A reading account B's tab.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paperpull_core import identity


def rec(ident, date=""):
    return {"id": str(ident), "date": date}


def test_anchors_are_the_oldest_three_sorted_and_deduped():
    observed = [rec(105, "2026-05-01"), rec(101, "2026-01-01"),
                rec(103, "2026-03-01"), rec(102, "2026-02-01"),
                rec(101, "2026-01-01")]
    assert identity.pick_anchors(observed) == [
        rec(101, "2026-01-01"), rec(102, "2026-02-01"), rec(103, "2026-03-01")]


def test_nothing_recorded_is_not_a_verdict():
    assert identity.check([], [rec(101, "2026-01-01")]) == identity.UNKNOWN


def test_an_empty_tab_is_not_a_verdict():
    assert identity.check([rec(101, "2026-01-01")], []) == identity.UNKNOWN


def test_all_anchors_present_is_ok():
    anchors = [rec(101, "2026-01-01"), rec(102, "2026-02-01")]
    observed = anchors + [rec(103, "2026-03-01")]
    assert identity.check(anchors, observed) == identity.OK


def test_an_anchor_that_aged_out_of_the_window_is_tolerated():
    """Simyo keeps ~12 months and drops the rest, so old anchors vanish."""
    anchors = [rec(101, "2025-01-01"), rec(102, "2025-02-01")]
    observed = [rec(102, "2025-02-01"), rec(114, "2026-02-01")]
    assert identity.check(anchors, observed) == identity.OK


def test_an_anchor_missing_from_inside_the_window_is_a_mismatch():
    """A document cannot leave the middle of an account's history."""
    anchors = [rec(101, "2026-01-01"), rec(102, "2026-02-01")]
    observed = [rec(102, "2026-02-01"), rec(103, "2026-03-01"),
                rec(100, "2025-12-01")]
    assert identity.check(anchors, observed) == identity.MISMATCH


def test_a_different_account_entirely_is_a_mismatch():
    ours = [rec(101, "2026-01-01"), rec(102, "2026-02-01")]
    theirs = [rec(901, "2026-01-01"), rec(902, "2026-02-01")]
    assert identity.check(ours, theirs) == identity.MISMATCH


def test_every_anchor_aged_out_proves_nothing():
    anchors = [rec(101, "2024-01-01"), rec(102, "2024-02-01")]
    observed = [rec(140, "2026-06-01"), rec(141, "2026-07-01")]
    assert identity.check(anchors, observed) == identity.AGED


def test_an_undated_anchor_that_is_absent_cannot_be_excused():
    assert identity.check([rec(101)], [rec(140, "2026-06-01")]) == identity.MISMATCH
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd core && python -m pytest tests/test_identity.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'paperpull_core.identity'`

- [ ] **Step 3: Write the implementation**

```python
# core/paperpull_core/identity.py
"""Which account is this tab actually signed in as?

PaperPull attaches to a browser you signed in to yourself, and in the Docker
layout every app points at ONE Chrome. Each provider's `find_signed_in_page()`
picks a tab by matching the provider's host, so with two accounts of the same
provider signed in, a run can read the *other* account's tab. Nothing
downstream would notice: the owner stamped on every document and every CSV row
comes from the config file (`storage.ensure_owner`), never from the page - so
the documents would be filed under the wrong person, silently.

The decision here is deliberately provider-agnostic and needs no browser:
given the document identifiers an account is *known* to own (its anchors) and
the identifiers the tab is showing right now, does this tab belong to this
account?

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
MISMATCH = "mismatch"      # an anchor is missing from inside the window
UNKNOWN = "unknown"        # nothing recorded, or the tab shows nothing
AGED = "aged"              # every anchor aged out: no positive proof either way

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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd core && python -m pytest tests/test_identity.py -v`
Expected: PASS, 9 tests.

- [ ] **Step 5: Commit**

```bash
git add core/paperpull_core/identity.py core/tests/test_identity.py
git commit -m "Decide which account a tab belongs to, from its oldest documents"
```

---

### Task 2: The sentinel file

**Files:**
- Create: `core/paperpull_core/sentinel.py`
- Create: `core/tests/test_sentinel.py`
- Modify: `core/paperpull_core/storage.py:132-134` (inside `Paths.__init__`)
- Test: `core/tests/test_spec_and_paths.py` (add one test)

**Interfaces:**
- Consumes: `storage.JsonStore` (`storage.py:389`), `storage.Paths`.
- Produces: `Paths.sentinel_json` (a `Path`); `sentinel.WARM`, `sentinel.PARKED`; `sentinel.read_anchors(store) -> list[dict]`; `sentinel.write_anchors(store, anchors) -> None`; `sentinel.mark_warm(store, when: str) -> None`; `sentinel.park(store, reason: str, when: str) -> None`; `sentinel.session_state(store) -> str`; `sentinel.last_verified_alive(store) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
# core/tests/test_sentinel.py
"""The per-account state file that cannot log in to anything."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paperpull_core import sentinel
from paperpull_core.storage import JsonStore


def store_at(tmp_path):
    return JsonStore(tmp_path / "sentinel.json")


def test_anchors_round_trip(tmp_path):
    store = store_at(tmp_path)
    anchors = [{"id": "101", "date": "2026-01-01"}]
    sentinel.write_anchors(store, anchors)
    assert sentinel.read_anchors(store_at(tmp_path)) == anchors


def test_no_anchors_yet_reads_as_empty(tmp_path):
    assert sentinel.read_anchors(store_at(tmp_path)) == []


def test_warm_records_when_it_was_verified(tmp_path):
    store = store_at(tmp_path)
    sentinel.mark_warm(store, "2026-08-21T10:00:00")
    fresh = store_at(tmp_path)
    assert sentinel.session_state(fresh) == sentinel.WARM
    assert sentinel.last_verified_alive(fresh) == "2026-08-21T10:00:00"


def test_parking_records_the_reason(tmp_path):
    store = store_at(tmp_path)
    sentinel.park(store, "signed out", "2026-08-21T10:05:00")
    fresh = store_at(tmp_path)
    assert sentinel.session_state(fresh) == sentinel.PARKED
    assert (fresh.get(sentinel.SESSION_KEY) or {})["parked_reason"] == "signed out"


def test_parking_then_warming_clears_the_reason(tmp_path):
    store = store_at(tmp_path)
    sentinel.park(store, "signed out", "2026-08-21T10:05:00")
    sentinel.mark_warm(store, "2026-08-21T11:00:00")
    fresh = store_at(tmp_path)
    assert sentinel.session_state(fresh) == sentinel.WARM
    assert (fresh.get(sentinel.SESSION_KEY) or {})["parked_reason"] == ""


def test_identity_and_session_do_not_overwrite_each_other(tmp_path):
    store = store_at(tmp_path)
    sentinel.write_anchors(store, [{"id": "101", "date": "2026-01-01"}])
    sentinel.park(store, "signed out", "2026-08-21T10:05:00")
    fresh = store_at(tmp_path)
    assert sentinel.read_anchors(fresh) == [{"id": "101", "date": "2026-01-01"}]
    assert sentinel.session_state(fresh) == sentinel.PARKED
```

And add to `core/tests/test_spec_and_paths.py`:

```python
def test_paths_include_the_sentinel_file(tmp_path):
    paths = storage.Paths(tmp_path / "out")
    assert paths.sentinel_json == tmp_path / "out" / "sentinel.json"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd core && python -m pytest tests/test_sentinel.py tests/test_spec_and_paths.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'paperpull_core.sentinel'`, and `AttributeError: 'Paths' object has no attribute 'sentinel_json'`.

- [ ] **Step 3: Write the implementation**

```python
# core/paperpull_core/sentinel.py
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

WARM = "warm"
PARKED = "parked"


def read_anchors(store) -> List[dict]:
    return list((store.get(IDENTITY_KEY) or {}).get("anchors") or [])


def write_anchors(store, anchors: Iterable[dict]) -> None:
    store.update(IDENTITY_KEY, {"anchors": [dict(a) for a in anchors]})


def mark_warm(store, when: str) -> None:
    """The session was alive at `when`, and is no longer parked."""
    store.update(SESSION_KEY, {"state": WARM, "last_verified_alive": when,
                               "parked_reason": ""})


def park(store, reason: str, when: str) -> None:
    """This account needs a human. Not an error - a state."""
    store.update(SESSION_KEY, {"state": PARKED, "parked_reason": reason,
                               "parked_at": when})


def session_state(store) -> str:
    return (store.get(SESSION_KEY) or {}).get("state") or ""


def last_verified_alive(store) -> str:
    return (store.get(SESSION_KEY) or {}).get("last_verified_alive") or ""
```

In `core/paperpull_core/storage.py`, inside `Paths.__init__`, after line 133:

```python
        self.progress_json = self.root / "progress.json"
        self.discovery_json = self.root / "discovery.json"
        # Which account this is, and whether its session was alive last time.
        # Holds no secret - see paperpull_core.sentinel.
        self.sentinel_json = self.root / "sentinel.json"
        self.run_summary = self.root / "run-summary.txt"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd core && python -m pytest tests/test_sentinel.py tests/test_spec_and_paths.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/paperpull_core/sentinel.py core/paperpull_core/storage.py core/tests/test_sentinel.py core/tests/test_spec_and_paths.py
git commit -m "Record per-account identity and session liveness, and no secret"
```

---

### Task 3: Refuse to file another account's invoices (Simyo)

**Files:**
- Modify: `apps/simyo/simyo_docs.py` — imports (~line 41), `App.__init__` (~line 155), new `ensure_identity`, `cmd_discover` (line 406), `process` (line 476), `build_parser` (line 780)
- Modify: `apps/simyo/storage.py:66-79` — re-export the two new core modules

**Interfaces:**
- Consumes: `identity.check`, `identity.pick_anchors`, `identity.OK/MISMATCH/UNKNOWN/AGED`, `sentinel.read_anchors`, `sentinel.write_anchors`, `Paths.sentinel_json`.
- Produces: `App.ensure_identity(page, docs=None) -> None` (raises `SystemExit` on refusal); the `--adopt-identity` CLI flag.

- [ ] **Step 1: Write the failing test**

There is no test harness for the app layer, and building one for a Playwright page is out of scope here. Test the part that can be tested — the refusal decision, driven through a fake store — by adding to `core/tests/test_identity.py`:

```python
def test_the_refusal_path_a_caller_must_implement():
    """The three-way branch every app's ensure_identity has to make.

    Kept here as executable documentation: OK re-anchors, MISMATCH refuses,
    and UNKNOWN/AGED are only allowed to adopt when a human asked for it.
    """
    ours = [rec(101, "2026-01-01")]
    theirs = [rec(901, "2026-01-01")]
    assert identity.check(ours, theirs) == identity.MISMATCH      # refuse
    assert identity.check([], theirs) == identity.UNKNOWN         # adopt only if asked
    assert identity.check(ours, ours) == identity.OK              # re-anchor
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd core && python -m pytest tests/test_identity.py::test_the_refusal_path_a_caller_must_implement -v`
Expected: PASS immediately if Task 1 is done (this test documents Task 1's API rather than driving new core code). If it fails, Task 1 is wrong — fix Task 1 before continuing.

- [ ] **Step 3: Wire it into the Simyo app**

In `apps/simyo/storage.py`, extend the re-export block (line 66) and `__all__` so the orchestrator's imports keep reading the same as every other app's:

```python
from paperpull_core.storage import (  # noqa: E402  (must follow bind)
    CsvFile, JsonStore, Paths, atomic_write_json, atomic_write_text,
    backup_file, build_pdf_filename, ensure_owner, load_config, now_iso,
    sanitize_component, set_filename_owner, title_case, unique_path,
)
from paperpull_core import identity, sentinel  # noqa: E402
```

```python
__all__ = [
    "SPEC", "PROJECT_DIR", "DOCUMENT_INDEX_COLUMNS",
    "CsvFile", "JsonStore", "Paths", "atomic_write_json", "atomic_write_text",
    "backup_file", "build_pdf_filename", "ensure_owner", "load_config",
    "now_iso", "sanitize_component", "set_filename_owner", "title_case",
    "unique_path", "identity", "sentinel",
]
```

In `apps/simyo/simyo_docs.py`, add to the import block after line 46:

```python
from storage import ensure_owner, PROJECT_DIR, set_filename_owner
from storage import identity, sentinel
```

In `App.__init__`, after line 158 (`self.discovery.load()`):

```python
        # Which account this config belongs to, and whether its session was
        # alive last time. No secret lives here - see paperpull_core.sentinel.
        self.sentinel = JsonStore(self.paths.sentinel_json, self.paths.backups)
        self.sentinel.load()
        self._identity_verified = False
```

Add these two methods to `App`, immediately after `check_session` (i.e. after line 275):

```python
    # -- account identity --------------------------------------------------

    def _anchor_records(self, docs) -> List[dict]:
        """The identity records a Simyo invoice list yields.

        Only the invoice number and its date. Both are already written to
        progress.json and the index CSV, so this records nothing new about the
        account - no phone number, no customer number, no amount.
        """
        return [{"id": d.invoice_number, "date": d.date_text}
                for d in docs if getattr(d, "invoice_number", "")]

    def ensure_identity(self, page, docs=None) -> None:
        """Refuse to file this tab's invoices unless it IS this account.

        Every app here points at one shared Chrome in the Docker layout, and
        `site.find_signed_in_page` picks a tab by host - so with a second Simyo
        account signed in, this run could read the wrong tab and file its
        invoices under this config's `owner`. Nothing downstream would notice,
        because the owner comes from the config and never from the page.

        Verified once per run: the invoice list is one API call, and the
        download path would otherwise re-ask it for every document.
        """
        if self._identity_verified:
            return
        if docs is None:
            docs = site.collect_documents(page)
        observed = self._anchor_records(docs)
        recorded = sentinel.read_anchors(self.sentinel)
        verdict = identity.check(recorded, observed)

        if verdict == identity.MISMATCH:
            raise SystemExit(
                "\n!! This tab is NOT the account this config belongs to.\n"
                f"   config : {getattr(self.args, 'config', None) or 'config.json'}\n"
                f"   owner  : {self.config.get('owner') or '(unset)'}\n"
                "   Refusing to file another account's invoices under that owner.\n"
                "   Sign THIS account in - in one tab, Simyo allows no more -\n"
                "   and run again.")

        if verdict == identity.OK:
            # Re-anchor: Simyo drops invoices older than ~12 months, so the
            # fingerprint has to roll forward with that window or it expires.
            sentinel.write_anchors(self.sentinel, identity.pick_anchors(observed))
            self._identity_verified = True
            return

        # UNKNOWN (nothing recorded yet, or an empty list) or AGED (every
        # anchor has fallen out of Simyo's window): no proof either way.
        if not getattr(self.args, "adopt_identity", False):
            raise SystemExit(
                f"\n!! Cannot tell which account this tab belongs to ({verdict}).\n"
                "   Sign in yourself, check the browser really shows the account\n"
                "   this config is for, then run once with --adopt-identity to\n"
                "   record it. That is the one moment this is taken on trust, so\n"
                "   it is never done for you and never done unattended.")
        sentinel.write_anchors(self.sentinel, identity.pick_anchors(observed))
        self._identity_verified = True
        print("Recorded this account's identity:")
        for anchor in sentinel.read_anchors(self.sentinel):
            print(f"  invoice {anchor['id']}  ({anchor['date']})")
```

In `cmd_discover`, replace line 414 (`docs = site.collect_documents(page)`) with:

```python
        docs = site.collect_documents(page)
        self.ensure_identity(page, docs)
```

In `process`, after line 477 (`page = self.page()`) — this is what covers `--resume`, which does **not** call `cmd_discover` and is the path cron will use:

```python
        page = self.page()
        self.ensure_identity(page)
```

In `build_parser`, after the `--config` argument (line 804):

```python
    ap.add_argument("--adopt-identity", action="store_true",
                    help="record which account this config's tab belongs to. "
                         "Do this once, on a tab you just signed in yourself.")
```

- [ ] **Step 4: Verify by hand against the real browser**

```bash
cd apps/simyo
# 1. Sign in to Simyo yourself, one tab, then:
python simyo_docs.py --discover --config config.json
```
Expected: refusal, "Cannot tell which account this tab belongs to (unknown)".

```bash
python simyo_docs.py --discover --config config.json --adopt-identity
```
Expected: prints "Recorded this account's identity:" and three invoice numbers; `data/simyo/sentinel.json` now has an `identity` record.

```bash
python simyo_docs.py --discover --config config.json
```
Expected: no refusal, discovery runs normally.

Now prove the refusal fires. Edit `data/simyo/sentinel.json` and change one anchor id to a number that is not in the account (e.g. `"999999"`), keeping its date, then:
```bash
python simyo_docs.py --discover --config config.json
```
Expected: "This tab is NOT the account this config belongs to." and a non-zero exit. Restore the file afterwards (or re-run `--adopt-identity`).

- [ ] **Step 5: Run the core tests**

Run: `cd core && python -m pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add apps/simyo/simyo_docs.py apps/simyo/storage.py core/tests/test_identity.py
git commit -m "Refuse to file a Simyo tab's invoices under the wrong account"
```

---

### Task 4: Show identity and session state in the panel

**Files:**
- Modify: `gui/app.py` — `_accounts()` (line 166), `discover_apps()` (line 179), the HTML in `index()` (line 459)

**Interfaces:**
- Consumes: the on-disk `sentinel.json` per account (read directly as JSON — the panel must not import the app layer).
- Produces: each account entry in `/api/apps` gains `{"state": "warm"|"parked"|"", "last_alive": iso, "identified": bool}`.

- [ ] **Step 1: Change the account list to carry state**

`_accounts()` currently returns a list of names. Return dicts instead, and read each account's `output_dir` from its config so the sentinel can be found. Replace `_accounts` (lines 166-177):

```python
def _account_names(app_dir: Path):
    """Just the labels: 'primary' plus every config.<name>.json."""
    names = ["primary"]
    cfg_dir = _config_dir(app_dir)
    if not cfg_dir.is_dir():
        return names
    for cfg in sorted(cfg_dir.glob("config.*.json")):
        if cfg.name == "config.example.json":
            continue
        names.append(cfg.name[len("config."):-len(".json")])
    return names


def _sentinel_for(app_dir: Path, account: str) -> dict:
    """This account's sentinel record, or {} if there is none yet.

    Read as plain JSON on purpose: the panel drives the apps as subprocesses
    and must not import their code or the core, so that it still runs from
    gui/requirements.txt alone on a native install.
    """
    name = "config.json" if account == "primary" else f"config.{account}.json"
    cfg_path = _config_dir(app_dir) / name
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
        raw = (Path(cfg["output_dir"]) / "sentinel.json").read_text(encoding="utf-8")
        return json.loads(raw)
    except Exception:
        # No config, no output dir yet, no sentinel, or unreadable: all of
        # which mean "nothing known", never an error the panel should show.
        return {}


def _accounts(app_dir: Path):
    out = []
    for name in _account_names(app_dir):
        sent = _sentinel_for(app_dir, name)
        session = sent.get("session") or {}
        identity_rec = sent.get("identity") or {}
        out.append({
            "name": name,
            "state": session.get("state", ""),
            "last_alive": session.get("last_verified_alive", ""),
            "parked_reason": session.get("parked_reason", ""),
            "identified": bool(identity_rec.get("anchors")),
        })
    return out
```

`_build_cmd` validates `account not in app_meta["accounts"]` (line 220). Accounts are now dicts, so change that line to:

```python
    if account not in [a["name"] for a in app_meta["accounts"]]:
        raise HTTPException(400, "unknown account")
```

- [ ] **Step 2: Update the page's JavaScript**

In `index()`'s script, wherever accounts are rendered into the per-app account picker, the entries are now objects. Use `a.name` for the value and append a state marker:

```javascript
  const accountLabel = (a) => {
    if (!a.identified) return a.name + ' · unidentified';
    if (a.state === 'parked') return a.name + ' · needs sign-in';
    if (a.state === 'warm') return a.name + ' · alive ' + (a.last_alive || '').slice(0, 16);
    return a.name;
  };
```

and build each option as `<option value="${a.name}">${accountLabel(a)}</option>`.

- [ ] **Step 3: Verify in the browser**

```bash
cd gui && python -m uvicorn app:app --port 8765
```
Open `http://127.0.0.1:8765`. Expected: the Simyo account picker shows `primary · unidentified` before Task 3's adopt step and `primary` (or `· alive …` after phase 2) afterwards. `curl -s localhost:8765/api/apps | python -m json.tool` shows the new fields.

- [ ] **Step 4: Commit**

```bash
git add gui/app.py
git commit -m "Show each account's identity and session state in the panel"
```

---

# Phase 2 — Unattended runs that park instead of asking

### Task 5: `--unattended` parks and exits 0

**Files:**
- Modify: `apps/simyo/simyo_docs.py` — `check_session` (line 261), `build_parser` (line 780), `main` (line 811)

**Interfaces:**
- Consumes: `sentinel.park`, `sentinel.mark_warm`, `now_iso`.
- Produces: the `--unattended` flag; `App.Parked` exception; exit code 0 on park, 2 on a bad flag combination.

- [ ] **Step 1: Write the failing test**

Add to `core/tests/test_sentinel.py` — the contract the app must honour:

```python
def test_a_parked_account_is_a_state_not_an_error(tmp_path):
    """An unattended run that finds a dead session parks and exits 0.

    Exit 0 matters: cron must not treat "the human needs to sign in again" as
    a failure, or every alert becomes noise and real failures get ignored.
    """
    store = store_at(tmp_path)
    sentinel.park(store, "signed out", "2026-08-21T03:00:00")
    assert sentinel.session_state(store_at(tmp_path)) == sentinel.PARKED
```

- [ ] **Step 2: Run it to verify it passes against Task 2's module**

Run: `cd core && python -m pytest tests/test_sentinel.py -v`
Expected: PASS.

- [ ] **Step 3: Implement the mode**

In `apps/simyo/simyo_docs.py`, add the exception just above `class App` (line 138):

```python
class Parked(Exception):
    """This account needs a human, and no human is present.

    Raised only in --unattended mode. Not a failure: main() turns it into a
    printed line and exit code 0, so a scheduled run that finds a dead
    session is quiet rather than alarming. Real failures keep their non-zero
    codes, which is what keeps cron alerting worth reading.
    """
```

Replace `check_session` (lines 261-275) with:

```python
    def check_session(self, page) -> None:
        unattended = getattr(self.args, "unattended", False)
        challenge = site.detect_security_challenge(page)
        if challenge:
            self.progress.save(backup=True)
            if unattended:
                self._park(f"security challenge: {challenge}")
            print(f"\n!! {challenge}")
            print("Stopped. Please resolve it yourself in the browser window.")
            print("I will NOT attempt to bypass any security check.")
            ask("Press Enter once the page looks normal (or Ctrl+C to quit)... ")
        if site.looks_signed_out(page):
            self.progress.save(backup=True)
            if unattended:
                self._park("signed out")
            print("\n!! Simyo appears to have signed you out.")
            print("Please sign in again in the open browser window.")
            print("Sign in in the SAME tab, and do not open a second one -")
            print("a second Mijn Simyo tab signs you out of both.")
            ask("Press Enter after you are signed in... ")
        if unattended:
            # Worth recording: a scheduler wants to know the session was alive
            # at a known moment, not just that a run happened.
            sentinel.mark_warm(self.sentinel, now_iso())

    def _park(self, reason: str):
        sentinel.park(self.sentinel, reason, now_iso())
        raise Parked(reason)
```

In `build_parser`, after `--adopt-identity`:

```python
    ap.add_argument("--unattended", action="store_true",
                    help="never ask a question: if the session is dead, park "
                         "this account and exit 0. For scheduled runs.")
```

In `main`, validate the combination and catch the park. Replace the dispatch block's opening and add the handler:

```python
def main(argv=None):
    args = build_parser().parse_args(argv)
    for d in (args.start_date, args.end_date):
        if d and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
            print(f"Bad date '{d}': use YYYY-MM-DD")
            return 2
    if args.unattended:
        # These are the only commands that never need an answer from a person.
        # --all asks for confirmation, --adopt-identity is the one moment
        # identity is taken on trust, and --open-browser is a human sitting
        # down. Refusing here beats hanging in a container at 3am.
        if not (args.discover or args.resume or args.verify):
            print("--unattended works with --discover, --resume or --verify only.")
            return 2
        if args.adopt_identity:
            print("--adopt-identity is never done unattended.")
            return 2
    app = App(args)
    try:
        ...unchanged dispatch...
    except Parked as e:
        print(f"\nParked: {e}. This account needs a sign-in; nothing was run.")
        return 0
    except KeyboardInterrupt:
        ...unchanged...
```

(Keep the existing `except KeyboardInterrupt` and any other handlers exactly as they are; `except Parked` goes immediately before them.)

- [ ] **Step 4: Verify by hand**

```bash
cd apps/simyo
python simyo_docs.py --unattended --all ; echo "exit=$?"
```
Expected: `--unattended works with --discover, --resume or --verify only.` and `exit=2`.

With a live signed-in tab:
```bash
python simyo_docs.py --unattended --resume ; echo "exit=$?"
```
Expected: runs normally, `exit=0`, and `data/simyo/sentinel.json` has `session.state == "warm"` with a fresh `last_verified_alive`.

Now sign out in the browser (or wait out the 10 minutes) and repeat:
```bash
python simyo_docs.py --unattended --resume ; echo "exit=$?"
```
Expected: `Parked: signed out. This account needs a sign-in; nothing was run.` and `exit=0`, and `session.state == "parked"`. Critically: **no prompt, no hang.**

- [ ] **Step 5: Commit**

```bash
git add apps/simyo/simyo_docs.py core/tests/test_sentinel.py
git commit -m "Park an unattended run instead of asking a question nobody hears"
```

---

# Phase 3 — The fact that splits cron from the milk run

### Task 6: Declare how long a provider's session lasts

**Files:**
- Modify: `core/paperpull_core/spec.py:56-98` (`AppSpec` fields and `__post_init__`)
- Modify: `apps/simyo/storage.py:28-60` (Simyo's `SPEC`)
- Test: `core/tests/test_spec_and_paths.py`

**Interfaces:**
- Produces: `AppSpec.session_lifetime_minutes: Optional[int] = None` — `None` means "holds for days, safe on plain cron"; an integer means the session dies that fast and a human must be present.

- [ ] **Step 1: Write the failing tests**

Add to `core/tests/test_spec_and_paths.py`:

```python
def test_session_lifetime_defaults_to_long(tmp_path):
    """None means 'holds for days': the majority case, safe on plain cron."""
    assert document_spec(tmp_path).session_lifetime_minutes is None


def test_session_lifetime_can_be_declared(tmp_path):
    spec = AppSpec(provider="Simyo", project_dir=tmp_path, kind=DOCUMENT,
                   folders=[Folder("statements", "Statements")],
                   routes={"Statement": "statements"},
                   session_lifetime_minutes=10)
    assert spec.session_lifetime_minutes == 10


def test_a_nonsense_session_lifetime_is_refused(tmp_path):
    with pytest.raises(ValueError, match="session_lifetime_minutes"):
        AppSpec(provider="Simyo", project_dir=tmp_path, kind=DOCUMENT,
                folders=[Folder("statements", "Statements")],
                routes={"Statement": "statements"},
                session_lifetime_minutes=0)
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd core && python -m pytest tests/test_spec_and_paths.py -v -k session`
Expected: FAIL — `AttributeError` / `TypeError: unexpected keyword argument 'session_lifetime_minutes'`.

- [ ] **Step 3: Add the field**

In `core/paperpull_core/spec.py`, in the `AppSpec` field block (after `rules_filename`, line 82):

```python
    # How long this provider's signed-in session survives while idle.
    #
    # None means "days" - the ordinary case, and the one that can run on a
    # plain schedule with nobody present. A number means the session dies that
    # fast (Simyo: about ten minutes, and a second tab signs the first out),
    # so a run has to happen while a human is still sitting there. This single
    # fact is what routes a provider to unattended cron or to a human sitting;
    # nothing else about the two paths differs.
    session_lifetime_minutes: Optional[int] = None
```

In `__post_init__`, after the `default_route` check:

```python
        if self.session_lifetime_minutes is not None and \
                int(self.session_lifetime_minutes) < 1:
            raise ValueError(
                "AppSpec.session_lifetime_minutes must be None or at least 1")
```

In `apps/simyo/storage.py`, add to the `SPEC = AppSpec(...)` call, after `rules_filename`:

```python
    # Mijn Simyo signs you out after roughly ten minutes idle, and a second
    # tab signs out the first - so this account can never be pulled unattended
    # on a timer. See simyo_site.py's notes 1 and 6.
    session_lifetime_minutes=10,
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd core && python -m pytest tests/test_spec_and_paths.py -v`
Expected: PASS.

Also confirm the app still starts: `cd apps/simyo && python simyo_docs.py --help` → prints help, no traceback.

- [ ] **Step 5: Commit**

```bash
git add core/paperpull_core/spec.py apps/simyo/storage.py core/tests/test_spec_and_paths.py
git commit -m "Declare how long a provider's session survives, and say Simyo's is ten minutes"
```

---

### Task 7: Decide who is due

**Files:**
- Create: `core/paperpull_core/due.py`
- Create: `core/tests/test_due.py`

**Interfaces:**
- Consumes: nothing (pure).
- Produces: `due.DEFAULT_CADENCE_DAYS` (int, 31); `due.plan(accounts: Iterable[dict], today: str) -> list[dict]`. An *account* is `{"app": str, "account": str, "session_lifetime_minutes": int|None, "cadence_days": int|None, "newest_document_date": str, "last_checked_date": str, "parked": bool}`.

- [ ] **Step 1: Write the failing tests**

```python
# core/tests/test_due.py
"""Who is worth running now, and in what order.

The ordering is the interesting part: a session with ten minutes of life left
cannot sit behind nine accounts whose sessions last for days.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paperpull_core import due


def acct(app, account="primary", lifetime=None, newest="", checked="",
         cadence=None, parked=False):
    return {"app": app, "account": account,
            "session_lifetime_minutes": lifetime,
            "cadence_days": cadence, "newest_document_date": newest,
            "last_checked_date": checked, "parked": parked}


def names(plan):
    return [(a["app"], a["account"]) for a in plan]


def test_an_account_with_a_fresh_document_is_not_due():
    plan = due.plan([acct("simyo", newest="2026-08-15")], "2026-08-21")
    assert plan == []


def test_an_account_whose_next_document_is_overdue_is_due():
    plan = due.plan([acct("simyo", newest="2026-07-01")], "2026-08-21")
    assert names(plan) == [("simyo", "primary")]


def test_an_account_never_run_is_due():
    plan = due.plan([acct("simyo", newest="")], "2026-08-21")
    assert names(plan) == [("simyo", "primary")]


def test_an_account_already_checked_today_is_left_alone():
    plan = due.plan([acct("simyo", newest="", checked="2026-08-21")],
                    "2026-08-21")
    assert plan == []


def test_the_shortest_lived_session_goes_first():
    accounts = [acct("amex", lifetime=None, newest="2026-06-01"),
                acct("simyo", lifetime=10, newest="2026-06-01"),
                acct("ukg", lifetime=60, newest="2026-06-01")]
    assert [a["app"] for a in due.plan(accounts, "2026-08-21")] == \
        ["simyo", "ukg", "amex"]


def test_within_one_provider_the_stalest_account_goes_first():
    accounts = [acct("simyo", "jane", lifetime=10, newest="2026-07-01"),
                acct("simyo", "primary", lifetime=10, newest="2026-05-01")]
    assert names(due.plan(accounts, "2026-08-21")) == \
        [("simyo", "primary"), ("simyo", "jane")]


def test_a_parked_account_is_still_due_because_it_needs_a_human():
    plan = due.plan([acct("simyo", newest="2026-06-01", parked=True)],
                    "2026-08-21")
    assert names(plan) == [("simyo", "primary")]


def test_a_provider_specific_cadence_beats_the_default():
    weekly = acct("gap", newest="2026-08-14", cadence=7)
    assert names(due.plan([weekly], "2026-08-21")) == [("gap", "primary")]


def test_an_unreadable_date_does_not_take_the_scheduler_down():
    plan = due.plan([acct("simyo", newest="not-a-date")], "2026-08-21")
    assert names(plan) == [("simyo", "primary")]
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd core && python -m pytest tests/test_due.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'paperpull_core.due'`.

- [ ] **Step 3: Write the implementation**

```python
# core/paperpull_core/due.py
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

A parked account stays in the list on purpose. It cannot run unattended, but
it is exactly what a human sitting down needs to be told about.
"""
from __future__ import annotations

from datetime import date
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


def _is_due(account: dict, today: date) -> bool:
    if str(account.get("last_checked_date") or "").strip() == today.isoformat():
        return False
    newest = _as_date(account.get("newest_document_date"))
    if newest is None:
        # Never run, or a date nothing can parse. Both mean "look".
        return True
    cadence = account.get("cadence_days") or DEFAULT_CADENCE_DAYS
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
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd core && python -m pytest tests/test_due.py -v`
Expected: PASS, 9 tests.

- [ ] **Step 5: Commit**

```bash
git add core/paperpull_core/due.py core/tests/test_due.py
git commit -m "Decide which accounts are due, most perishable session first"
```

---

### Task 8: Read the real trees and print the due list

**Files:**
- Create: `core/paperpull_core/appload.py`
- Create: `tools/due.py`
- Test: `core/tests/test_due.py` (extend)

**Interfaces:**
- Consumes: `due.plan`, `sentinel`, each app's `storage.py`.
- Produces: `appload.load_spec(app_dir: Path) -> AppSpec`; `appload.accounts(apps_root: Path, config_root: Path|None) -> list[dict]` returning records shaped for `due.plan`.

- [ ] **Step 1: Write the failing test**

Add to `core/tests/test_due.py`:

```python
def test_accounts_are_read_off_the_real_trees(tmp_path, monkeypatch):
    """A config points at an output_dir; that dir holds the state we read."""
    from paperpull_core import appload

    app_dir = tmp_path / "apps" / "testco"
    app_dir.mkdir(parents=True)
    (app_dir / "testco_docs.py").write_text("", encoding="utf-8")
    out = tmp_path / "data" / "testco"
    (out).mkdir(parents=True)
    (app_dir / "config.json").write_text(
        '{"output_dir": "%s"}' % out.as_posix(), encoding="utf-8")
    (out / "progress.json").write_text(
        '{"a": {"date": "2026-06-01"}, "b": {"date": "2026-07-01"}}',
        encoding="utf-8")
    (out / "sentinel.json").write_text(
        '{"session": {"state": "parked", "last_verified_alive": '
        '"2026-07-02T10:00:00"}}', encoding="utf-8")

    found = appload.accounts(tmp_path / "apps", None)
    assert len(found) == 1
    rec = found[0]
    assert rec["app"] == "testco"
    assert rec["account"] == "primary"
    assert rec["newest_document_date"] == "2026-07-01"
    assert rec["parked"] is True
    assert rec["last_checked_date"] == "2026-07-02"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd core && python -m pytest tests/test_due.py -k real_trees -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'paperpull_core.appload'`.

- [ ] **Step 3: Write `appload.py`**

```python
# core/paperpull_core/appload.py
"""Read an app's declared facts and its accounts' state, from outside the app.

A scheduler has to know two things no single app can tell it: what every app
declares about itself, and what every account of every app has on disk. Both
are already recorded - the first in each app's `storage.py` SPEC, the second
under each config's `output_dir` - so nothing new is stored to answer this.

Loading those SPECs takes one piece of care. Every app has a module called
`storage.py`, so a plain `import storage` would hand back whichever one was
imported first for all of them. They are loaded here under distinct module
names via importlib instead. Each one calls `paperpull_core.storage.bind()` on
import, which is global and last-writer-wins - fine, because only the returned
SPEC object is used and nothing here goes on to file a document.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import List, Optional


def load_spec(app_dir: Path):
    """The AppSpec an app declares in its own storage.py."""
    app_dir = Path(app_dir)
    storage_py = app_dir / "storage.py"
    if not storage_py.is_file():
        raise FileNotFoundError(storage_py)
    module_name = f"_pp_spec_{app_dir.name}"
    spec = importlib.util.spec_from_file_location(module_name, storage_py)
    module = importlib.util.module_from_spec(spec)
    # The app's storage.py imports its own package-local names, so its
    # directory has to be importable while it executes - and must not be left
    # on sys.path afterwards, or the next app's storage.py would find this
    # one's siblings.
    sys.path.insert(0, str(app_dir))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(app_dir))
        sys.modules.pop(module_name, None)
    return module.SPEC


def _newest_document_date(progress_json: Path) -> str:
    """The date of the newest document this account has ever recorded."""
    try:
        data = json.loads(progress_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    dates = [str((rec or {}).get("date") or "").strip()
             for rec in (data or {}).values()]
    dates = [d for d in dates if d]
    return max(dates) if dates else ""


def _config_files(app_dir: Path, config_root: Optional[Path]):
    """(account label, config path) for every account of this app."""
    cfg_dir = Path(config_root) / app_dir.name if config_root else app_dir
    if not cfg_dir.is_dir():
        return []
    found = []
    primary = cfg_dir / "config.json"
    if primary.is_file():
        found.append(("primary", primary))
    for path in sorted(cfg_dir.glob("config.*.json")):
        if path.name == "config.example.json":
            continue
        found.append((path.name[len("config."):-len(".json")], path))
    return found


def accounts(apps_root: Path, config_root: Optional[Path]) -> List[dict]:
    """Every app/account pair, shaped for paperpull_core.due.plan."""
    out: List[dict] = []
    apps_root = Path(apps_root)
    if not apps_root.is_dir():
        return out
    for app_dir in sorted(p for p in apps_root.iterdir() if p.is_dir()):
        if not (app_dir / "storage.py").is_file():
            continue
        try:
            spec = load_spec(app_dir)
            lifetime = spec.session_lifetime_minutes
        except Exception:
            # A provider whose spec will not load must not take the whole
            # schedule down; treat it as patient and let its own run fail
            # loudly if someone asks for it.
            lifetime = None
        for label, cfg_path in _config_files(app_dir, config_root):
            try:
                cfg = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                continue
            output_dir = Path(cfg.get("output_dir") or "")
            session = {}
            try:
                session = (json.loads(
                    (output_dir / "sentinel.json").read_text(encoding="utf-8"))
                    .get("session") or {})
            except (OSError, json.JSONDecodeError):
                session = {}
            last_alive = str(session.get("last_verified_alive") or "")
            out.append({
                "app": app_dir.name,
                "account": label,
                "config": str(cfg_path),
                "output_dir": str(output_dir),
                "session_lifetime_minutes": lifetime,
                "cadence_days": cfg.get("cadence_days"),
                "newest_document_date": _newest_document_date(
                    output_dir / "progress.json"),
                "last_checked_date": last_alive[:10],
                "parked": session.get("state") == "parked",
            })
    return out
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd core && python -m pytest tests/test_due.py -v`
Expected: PASS, 10 tests.

- [ ] **Step 5: Write the CLI**

```python
# tools/due.py
"""Which accounts are due a run, most perishable session first.

Reads only what is already on disk: each app's declared facts, each config's
output_dir, each account's progress.json and sentinel.json. Runs nothing,
opens no browser, and needs no credentials.

    python tools/due.py
    PAPERPULL_CONFIG_ROOT=/config python tools/due.py
"""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "core"))

from paperpull_core import appload, due  # noqa: E402


def main(argv=None) -> int:
    apps_root = Path(os.environ.get("APPS_ROOT", HERE.parent / "apps"))
    root = os.environ.get("PAPERPULL_CONFIG_ROOT", "").strip()
    config_root = Path(root) if root else None

    accounts = appload.accounts(apps_root, config_root)
    today = date.today().isoformat()
    plan = due.plan(accounts, today)

    print(f"{len(plan)} of {len(accounts)} account(s) due on {today}\n")
    if not plan:
        return 0
    print(f"{'app':<14} {'account':<12} {'session':<10} "
          f"{'newest doc':<12} state")
    for a in plan:
        lifetime = a["session_lifetime_minutes"]
        session = "days" if lifetime is None else f"{lifetime} min"
        state = "PARKED - needs sign-in" if a["parked"] else "ok"
        print(f"{a['app']:<14} {a['account']:<12} {session:<10} "
              f"{a['newest_document_date'] or '(never)':<12} {state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Verify by hand**

Run: `python tools/due.py`
Expected: a table listing your real accounts, with `simyo` showing `10 min` and every other provider showing `days`. Sanity-check the `newest doc` column against `data/<app>/progress.json`.

- [ ] **Step 7: Commit**

```bash
git add core/paperpull_core/appload.py tools/due.py core/tests/test_due.py
git commit -m "Say which accounts are due, read off the config and data trees"
```

---

# Phase 4 — Run the patient providers on a schedule

This is where the work starts paying: every provider whose session lasts for days gets pulled with nobody present. Simyo and anything else short-lived is only *listed*, for a human to pick up.

### Task 9: The scheduler loop and its container

**Files:**
- Create: `tools/schedule.py`
- Modify: `docker-compose.yml` (a `scheduler` service)
- Modify: `docs/docker.md` (a "Scheduling" section)

**Interfaces:**
- Consumes: `appload.accounts`, `due.plan`, each app's `<provider>_docs.py --unattended --resume`.
- Produces: nothing importable; a long-running process.

- [ ] **Step 1: Write the scheduler**

```python
# tools/schedule.py
"""Run the accounts that can run themselves; list the ones that cannot.

One loop, no new dependency, and no Docker socket: this runs inside the same
image as the panel and invokes the same app CLIs the panel does.

The split is one declared fact. A provider whose AppSpec leaves
`session_lifetime_minutes` as None holds its session for days, so a scheduled
`--unattended --resume` will usually find it alive - and if it does not, the
run parks and exits 0 and nothing is woken up. A provider that declares a
lifetime (Simyo: ten minutes) can only be pulled while a human is sitting
there, so it is never started here; it is printed, for the panel's sitting and
for whatever notifier you point at this log.

    python tools/schedule.py --once      # one pass, then exit
    python tools/schedule.py             # loop, one pass per PAPERPULL_SCHEDULE_HOUR
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "core"))

from paperpull_core import appload, due  # noqa: E402

POLL_SECONDS = 600


def _entry_script(app_dir: Path):
    for path in sorted(app_dir.glob("*_docs.py")) + \
            sorted(app_dir.glob("*_receipts.py")):
        return path
    return None


def run_one(account: dict, apps_root: Path) -> int:
    app_dir = apps_root / account["app"]
    script = _entry_script(app_dir)
    if script is None:
        print(f"  {account['app']}: no entry script, skipped")
        return 0
    cmd = [sys.executable, script.name, "--unattended", "--resume"]
    if account["account"] != "primary" or os.environ.get("PAPERPULL_CONFIG_ROOT"):
        cmd += ["--config", account["config"]]
    print(f"  $ {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=str(app_dir))
    return proc.returncode


def one_pass(apps_root: Path, config_root, today: str) -> None:
    accounts = appload.accounts(apps_root, config_root)
    plan = due.plan(accounts, today)
    patient = [a for a in plan if a["session_lifetime_minutes"] is None]
    perishable = [a for a in plan if a["session_lifetime_minutes"] is not None]

    print(f"[{datetime.now():%Y-%m-%d %H:%M}] "
          f"{len(plan)} due: {len(patient)} unattended, "
          f"{len(perishable)} need a person")
    for account in patient:
        print(f"  {account['app']}/{account['account']}")
        code = run_one(account, apps_root)
        if code:
            print(f"  ! exited {code}")
    for account in perishable:
        # Deliberately not started. Its session would be dead before a
        # download finished, and a failed attempt teaches the provider's fraud
        # model something about us for nothing.
        print(f"  waiting for a person: {account['app']}/{account['account']} "
              f"(session lasts {account['session_lifetime_minutes']} min)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--once", action="store_true", help="one pass, then exit")
    args = ap.parse_args(argv)

    apps_root = Path(os.environ.get("APPS_ROOT", HERE.parent / "apps"))
    root = os.environ.get("PAPERPULL_CONFIG_ROOT", "").strip()
    config_root = Path(root) if root else None
    hour = int(os.environ.get("PAPERPULL_SCHEDULE_HOUR", "7"))

    if args.once:
        one_pass(apps_root, config_root, date.today().isoformat())
        return 0

    print(f"Scheduler up. One pass a day at {hour:02d}:00 local time.")
    ran_on = ""
    while True:
        now = datetime.now()
        today = now.date().isoformat()
        if now.hour == hour and ran_on != today:
            one_pass(apps_root, config_root, today)
            ran_on = today
        # A poll rather than a sleep-until: the container can be restarted at
        # any moment, and `ran_on` being in memory means a restart inside the
        # hour would repeat the pass. due.plan's last_checked_date is what
        # actually prevents that, which is why it is read from disk.
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Verify one pass by hand, before any container**

Run: `python tools/schedule.py --once`
Expected: a dated line, the patient accounts actually running (`$ …/ally_docs.py --unattended --resume`), and Simyo printed as `waiting for a person`. Nothing hangs. Nothing prompts.

- [ ] **Step 3: Add the service**

In `docker-compose.yml`, after the `paperpull` service:

```yaml
  # Runs the providers whose sessions last for days, and lists the ones that
  # need a person. Same image and same volumes as the panel: it invokes the
  # app CLIs directly, so there is no Docker socket here and nothing new to
  # trust. Stop it and nothing else changes.
  scheduler:
    image: ${PAPERPULL_IMAGE:-ghcr.io/zjean/paperpull:latest}
    container_name: paperpull-scheduler
    depends_on: [browser]
    user: "${PUID:-1000}:${PGID:-1000}"
    entrypoint: ["python", "/app/tools/schedule.py"]
    environment:
      TZ: ${TZ:-Europe/Amsterdam}
      PAPERPULL_CONFIG_ROOT: /config
      # Local hour for the daily pass. Pick one you are usually awake for:
      # a parked account is only actionable by you.
      PAPERPULL_SCHEDULE_HOUR: ${PAPERPULL_SCHEDULE_HOUR:-7}
    volumes:
      - ./config:/config
      - ./data:/data
      - pw-artifacts:/pwtmp
    networks: [internal]
    restart: unless-stopped
```

Add to `.env.example`:

```bash
# Local hour (0-23) for the scheduler's daily pass.
PAPERPULL_SCHEDULE_HOUR=7
```

- [ ] **Step 4: Verify in Docker**

```bash
docker compose up -d scheduler
docker compose exec scheduler python /app/tools/due.py
docker compose exec scheduler python /app/tools/schedule.py --once
docker compose logs -f scheduler
```
Expected: `due.py` lists your accounts; `--once` runs the patient ones and lists Simyo; the log shows "Scheduler up. One pass a day at 07:00 local time."

- [ ] **Step 5: Document it**

Add a "Scheduling" section to `docs/docker.md` after "One shared browser", covering: what the scheduler does and does not run, why Simyo can never be in the first group, `PAPERPULL_SCHEDULE_HOUR`, how to read a parked account (`docker compose exec scheduler python /app/tools/due.py`), and that a parked account is a state and not a failure.

- [ ] **Step 6: Commit**

```bash
git add tools/schedule.py docker-compose.yml .env.example docs/docker.md
git commit -m "Run the long-session providers on a schedule, and list the rest"
```

---

## Decision gate — stop here and re-read

Phases 1–4 deliver scheduled multi-account pulls with no credential stored anywhere. **Before starting phase 5, run for a week and answer:**

- Did any account get filed under the wrong owner? (Phase 1 should make this impossible — confirm the refusal has never fired *spuriously*, which would mean the anchor rule is wrong.)
- How often does Simyo actually need a sitting? If it is monthly, the sitting UI (Task 12) may not be worth building — a notification and the existing buttons may be enough.
- Did two runs of the same provider ever overlap? If never, Task 10/11's lock is speculative — build it when it bites.

Phases 5 and 6 are written out below so the decision is informed, not so they are inevitable.

---

# Phase 5 — One live session per provider, and the sitting

### Task 10: Declare and enforce concurrency

**Files:**
- Modify: `core/paperpull_core/spec.py` (`AppSpec.concurrency` + validation)
- Create: `core/paperpull_core/locks.py`
- Create: `core/tests/test_locks.py`
- Test: `core/tests/test_spec_and_paths.py`

**Interfaces:**
- Produces: `AppSpec.concurrency: int = 1`; `locks.ProviderBusy` (exception); `locks.acquire(lock_dir, slug, capacity, holder, now=None) -> Path`; `locks.release(path) -> None`; `locks.holders(lock_dir, slug, capacity) -> list[dict]`; `locks.hold(...)` (context manager); `locks.STALE_AFTER` (`timedelta`).

- [ ] **Step 1: Write the failing tests**

```python
# core/tests/test_locks.py
"""One live session per provider, enforced on disk.

On disk and not in memory because the panel restarts, and because someone can
always run <provider>_docs.py straight from a terminal - the in-memory _RUNS
dict in gui/app.py sees neither.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paperpull_core import locks


def test_one_holder_at_capacity_one(tmp_path):
    first = locks.acquire(tmp_path, "simyo", 1, "primary")
    with pytest.raises(locks.ProviderBusy):
        locks.acquire(tmp_path, "simyo", 1, "jane")
    locks.release(first)
    second = locks.acquire(tmp_path, "simyo", 1, "jane")
    assert second.exists()


def test_capacity_two_admits_two_and_refuses_the_third(tmp_path):
    locks.acquire(tmp_path, "amex", 2, "a")
    locks.acquire(tmp_path, "amex", 2, "b")
    with pytest.raises(locks.ProviderBusy):
        locks.acquire(tmp_path, "amex", 2, "c")


def test_different_providers_do_not_block_each_other(tmp_path):
    locks.acquire(tmp_path, "simyo", 1, "primary")
    assert locks.acquire(tmp_path, "youfone", 1, "primary").exists()


def test_a_stale_lock_is_taken_over(tmp_path):
    """A killed container leaves its lock behind; it must not be permanent."""
    locks.acquire(tmp_path, "simyo", 1, "dead")
    later = datetime.now() + locks.STALE_AFTER + timedelta(minutes=1)
    assert locks.acquire(tmp_path, "simyo", 1, "live", now=later).exists()


def test_holders_say_who_is_in_there(tmp_path):
    locks.acquire(tmp_path, "simyo", 1, "jane")
    who = locks.holders(tmp_path, "simyo", 1)
    assert [h["holder"] for h in who] == ["jane"]


def test_the_context_manager_releases_on_the_way_out(tmp_path):
    with locks.hold(tmp_path, "simyo", 1, "primary"):
        with pytest.raises(locks.ProviderBusy):
            locks.acquire(tmp_path, "simyo", 1, "jane")
    assert locks.acquire(tmp_path, "simyo", 1, "jane").exists()


def test_the_context_manager_releases_after_a_crash(tmp_path):
    with pytest.raises(RuntimeError):
        with locks.hold(tmp_path, "simyo", 1, "primary"):
            raise RuntimeError("boom")
    assert locks.acquire(tmp_path, "simyo", 1, "jane").exists()
```

Add to `core/tests/test_spec_and_paths.py`:

```python
def test_concurrency_defaults_to_one(tmp_path):
    assert document_spec(tmp_path).concurrency == 1


def test_a_nonsense_concurrency_is_refused(tmp_path):
    with pytest.raises(ValueError, match="concurrency"):
        AppSpec(provider="Simyo", project_dir=tmp_path, kind=DOCUMENT,
                folders=[Folder("statements", "Statements")],
                routes={"Statement": "statements"}, concurrency=0)
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd core && python -m pytest tests/test_locks.py tests/test_spec_and_paths.py -v`
Expected: FAIL — no `paperpull_core.locks`, no `AppSpec.concurrency`.

- [ ] **Step 3: Write the implementation**

In `core/paperpull_core/spec.py`, beside `session_lifetime_minutes`:

```python
    # How many of this provider's sessions may be live at once, across every
    # account. One is not a conservative default, it is Simyo's actual rule:
    # a second Mijn Simyo tab signs the first one out, server-side. Providers
    # that genuinely tolerate parallel sessions can raise it.
    concurrency: int = 1
```

and in `__post_init__`:

```python
        if int(self.concurrency) < 1:
            raise ValueError("AppSpec.concurrency must be at least 1")
```

```python
# core/paperpull_core/locks.py
"""One live session per provider, enforced with files.

Some providers allow exactly one signed-in session and end the older one when
a second appears - Simyo does this, server-side, and it is why two Simyo
accounts can never be pulled at the same moment. Making that a scheduling
contract rather than a race is cheaper than discovering it mid-run.

Files rather than memory, because the two places that would otherwise track
it both fail: gui/app.py's `_RUNS` dict does not survive a panel restart, and
neither the panel nor the scheduler sees someone running
`<provider>_docs.py` straight from a terminal. A file in a directory shared by
all of a provider's accounts is visible to all three.

Stale locks are aged out, not pid-checked. A pid means nothing across
containers - the panel, the scheduler and a shell all have their own pid
namespace, so "is 431 alive?" has no shared answer. An hour count does.
"""
from __future__ import annotations

import json
import os
import socket
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

# Longer than any real run and shorter than a working day: a lock older than
# this belongs to something that died.
STALE_AFTER = timedelta(hours=6)


class ProviderBusy(RuntimeError):
    """Another account of this provider is already signed in and running."""


def _lock_path(lock_dir: Path, slug: str, index: int) -> Path:
    return Path(lock_dir) / f"{slug}.{index}.lock"


def _claim(path: Path, holder: str) -> None:
    """Create the lock file, or raise FileExistsError. O_EXCL is the whole
    mechanism: on every filesystem this runs on, exactly one caller wins."""
    fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({"holder": holder, "pid": os.getpid(),
                   "host": socket.gethostname(),
                   "at": datetime.now().isoformat(timespec="seconds")}, handle)


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _is_stale(path: Path, now: datetime) -> bool:
    record = _read(path)
    try:
        held_since = datetime.fromisoformat(record["at"])
    except (KeyError, TypeError, ValueError):
        return True      # unreadable: assume nothing is really holding it
    return now - held_since > STALE_AFTER


def acquire(lock_dir, slug: str, capacity: int, holder: str,
            now: Optional[datetime] = None) -> Path:
    """Take one of this provider's session slots, or raise ProviderBusy."""
    now = now or datetime.now()
    directory = Path(lock_dir)
    directory.mkdir(parents=True, exist_ok=True)
    for index in range(max(1, int(capacity))):
        path = _lock_path(directory, slug, index)
        try:
            _claim(path, holder)
            return path
        except FileExistsError:
            if _is_stale(path, now):
                path.unlink(missing_ok=True)
                try:
                    _claim(path, holder)
                    return path
                except FileExistsError:
                    continue
    busy = ", ".join(h.get("holder", "?") for h in
                     holders(directory, slug, capacity)) or "another run"
    raise ProviderBusy(
        f"{slug}: all {capacity} session slot(s) are in use by {busy}. "
        f"This provider signs the older session out when a second one starts, "
        f"so this run would break both.")


def holders(lock_dir, slug: str, capacity: int) -> List[dict]:
    """Who currently holds this provider's slots."""
    out = []
    for index in range(max(1, int(capacity))):
        path = _lock_path(Path(lock_dir), slug, index)
        if path.exists():
            out.append(_read(path))
    return out


def release(path) -> None:
    Path(path).unlink(missing_ok=True)


@contextmanager
def hold(lock_dir, slug: str, capacity: int, holder: str):
    path = acquire(lock_dir, slug, capacity, holder)
    try:
        yield path
    finally:
        release(path)
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd core && python -m pytest tests/test_locks.py tests/test_spec_and_paths.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add core/paperpull_core/locks.py core/paperpull_core/spec.py core/tests/test_locks.py core/tests/test_spec_and_paths.py
git commit -m "Hold one session slot per provider, in a file the panel cannot lose"
```

---

### Task 11: Take the lock in the app, and report it in the panel

**Files:**
- Modify: `apps/simyo/simyo_docs.py` — `main` (line 811)
- Modify: `apps/simyo/storage.py` — re-export `locks`
- Modify: `gui/app.py` — `api_run` (line 365)

**Interfaces:**
- Consumes: `locks.hold`, `locks.holders`, `locks.ProviderBusy`, `SPEC.concurrency`, `SPEC.slug`.
- Produces: the `lock_dir` config key (optional; defaults to `<output_dir>/../.locks`); exit code 4 when a provider is busy.

- [ ] **Step 1: Take the lock around the commands that attach**

In `apps/simyo/storage.py`, add `locks` to the core import and `__all__` exactly as `identity` and `sentinel` were added in Task 3.

In `apps/simyo/simyo_docs.py`, add `locks` to the `from storage import identity, sentinel` line, and wrap the dispatch in `main`:

```python
    app = App(args)
    # The slot is per PROVIDER, not per account: Simyo ends the older session
    # when a second one starts, so two accounts must not overlap. The default
    # directory is one level above this account's output_dir, which is what
    # every account of a provider has in common.
    lock_dir = Path(app.config.get("lock_dir") or
                    (Path(app.config["output_dir"]).parent / ".locks"))
    needs_browser = not (args.verify or getattr(args, "open_browser", False))
    holder = f"{args.config or 'config.json'}"
    try:
        if needs_browser:
            with locks.hold(lock_dir, SPEC.slug, SPEC.concurrency, holder):
                return _dispatch(app, args)
        return _dispatch(app, args)
    except locks.ProviderBusy as e:
        print(f"\n!! {e}")
        return 4
    except Parked as e:
        print(f"\nParked: {e}. This account needs a sign-in; nothing was run.")
        return 0
    except KeyboardInterrupt:
        ...unchanged...
```

Extract the existing `if/elif` chain into a module-level `_dispatch(app, args)` that returns `0`, leaving its behaviour identical. Import `SPEC` from `storage` alongside `PROJECT_DIR`.

- [ ] **Step 2: Verify by hand**

Two terminals, both signed-in configs of Simyo:
```bash
# terminal 1
cd apps/simyo && python simyo_docs.py --all --yes
# terminal 2, while the first is running
cd apps/simyo && python simyo_docs.py --resume --config config.jane.json ; echo "exit=$?"
```
Expected: terminal 2 prints `simyo: all 1 session slot(s) are in use by config.json…` and `exit=4`, having opened no browser and downloaded nothing. Then confirm the lock is gone after terminal 1 finishes and terminal 2's command works.

- [ ] **Step 3: Make the panel say so before starting anything**

In `gui/app.py`, before `_build_cmd` is executed in `api_run`, check the lock. The core may not be importable on a native install, so guard it:

```python
def _busy_holder(app_dir: Path, account: str):
    """Who holds this provider's session slot, if anyone.

    Guarded: the panel is meant to run from gui/requirements.txt alone, so a
    missing paperpull_core must degrade to "cannot tell" rather than break the
    Run button.
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
        from paperpull_core import appload, locks
    except Exception:
        return None
    try:
        spec = appload.load_spec(app_dir)
        name = "config.json" if account == "primary" else f"config.{account}.json"
        cfg_path = _config_dir(app_dir) / name
        cfg = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
        lock_dir = Path(cfg.get("lock_dir") or
                        (Path(cfg["output_dir"]).parent / ".locks"))
        held = locks.holders(lock_dir, spec.slug, spec.concurrency)
        if len(held) >= spec.concurrency and held:
            return held[0].get("holder") or "another run"
    except Exception:
        return None
    return None
```

and at the top of `api_run`, after `meta = apps[app]`:

```python
    busy = _busy_holder(Path(meta["dir"]), account)
    if busy:
        raise HTTPException(409, f"{app} is already running as {busy}. "
                                 f"This provider allows one session at a time.")
```

- [ ] **Step 4: Verify in the panel**

Start a Simyo run from the panel, then try to start the other account. Expected: a 409 with the message naming the holding config, and no second process started (`ps` shows one `simyo_docs.py`).

- [ ] **Step 5: Commit**

```bash
git add apps/simyo/simyo_docs.py apps/simyo/storage.py gui/app.py
git commit -m "Refuse a second Simyo run while one is already signed in"
```

---

### Task 12: The sitting

**Files:**
- Modify: `gui/app.py` — new `/api/due` route, and the page's script

**Interfaces:**
- Consumes: `appload.accounts`, `due.plan` (both behind the same guarded import as Task 11).
- Produces: `GET /api/due` → `{"today": iso, "due": [{app, account, session_lifetime_minutes, parked, newest_document_date}]}`.

- [ ] **Step 1: Add the endpoint**

```python
@app.get("/api/due", dependencies=[Depends(_same_origin_only)])
def api_due():
    """Which accounts are due, most perishable session first.

    Same answer as `python tools/due.py`, so the panel and the scheduler never
    disagree about who is waiting.
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
        from paperpull_core import appload, due as due_mod
    except Exception:
        raise HTTPException(503, "paperpull_core is not importable here")
    root = _config_root()
    accounts = appload.accounts(APPS_ROOT, root)
    today = date.today().isoformat()
    return {"today": today, "due": due_mod.plan(accounts, today)}
```

Add `from datetime import date` to the imports.

- [ ] **Step 2: Add the client loop**

In `index()`'s script, add a "Start sitting" button that walks the queue using the run stream that already exists — one account at a time, waiting for each stream to close before starting the next, because the provider allows one session:

```javascript
async function startSitting() {
  const res = await fetch('/api/due');
  if (!res.ok) { alert('Cannot read the due list here.'); return; }
  const {due} = await res.json();
  const queue = due.filter(a => a.session_lifetime_minutes !== null);
  if (!queue.length) { alert('Nothing needs a person right now.'); return; }
  for (const a of queue) {
    // Sign-in first: a perishable session has to be fresh when the pull runs,
    // and only a person can make it fresh.
    if (!confirm(`Sign in to ${a.app} (${a.account}) in the browser desktop, `
               + `in ONE tab. Press OK when you are signed in.`)) return;
    await runToCompletion(a.app, a.account, 'resume');
  }
  alert('Sitting done.');
}

function runToCompletion(app, account, action) {
  // Resolves when the run's stream closes - which is the run's teardown, and
  // therefore the moment the next account may safely sign in.
  return new Promise((resolve) => {
    const src = new EventSource(
      `/api/run?app=${encodeURIComponent(app)}`
      + `&account=${encodeURIComponent(account)}&action=${action}`);
    src.onerror = () => { src.close(); resolve(); };
    src.addEventListener('end', () => { src.close(); resolve(); });
  });
}
```

Wire `runToCompletion` to whatever the existing run handler uses to append output, so a sitting streams into the same console. If the current stream has no `end` event, close on `onerror` only — that is what fires when the server closes the stream.

- [ ] **Step 3: Verify in the panel**

Sign out of everything, make Simyo due, click "Start sitting". Expected: one prompt per queued account, each run streaming into the console, and no two runs overlapping (`ps` shows at most one `simyo_docs.py`).

- [ ] **Step 4: Commit**

```bash
git add gui/app.py
git commit -m "Walk the due accounts in one sitting, one session at a time"
```

---

# Phase 6 — The credential store, gated

### Task 13: Record the decision, and the gate

**Files:**
- Create: `docs/adr/2026-08-21-no-stored-credentials.md`

**This task deliberately ships no code.** A blocklist guarding a feature that does not exist is a guard nobody runs; the guard belongs in the same change as the thing it guards. What is worth landing now is the decision and the conditions under which it could be revisited, because that is what stops the question being re-litigated from scratch in six months.

- [ ] **Step 1: Write the ADR**

Content it must contain:

- **Decision:** PaperPull stores no credential. Phases 1–4 deliver scheduled multi-account pulls without one, so the vault has no job to do.
- **Why the obvious version does not help:** a stored Simyo password would buy an unattended re-login, not a longer session — Simyo enforces one live session and a ~10-minute idle timeout. The provider that motivated the request is the one a vault cannot serve.
- **The key problem, stated:** an unattended container must be able to read the sealing key at boot with no human present, so anyone with root or compose access on the box can decrypt it. That is the same access that already reaches plaintext cookies in `./browser-profile` (`SECURITY.md`), so a vault does not raise the ceiling of a full compromise. It widens a *partial* one — someone who reads `./config` but never `./browser-profile` — from "session cookies that expire" to "reusable passwords and TOTP seeds."
- **The gate.** Any future credential store must, in one change: be opt-in per provider with the default off; refuse the financial providers outright in code (`amex`, `chase`, `usaa`, `robinhood`, `wealthfront`, `navyfederal`, `ally`, `redcard`) and not merely in documentation; store one sealed envelope per account rather than one blob; write an audit line per unseal; type into the CDP-attached tab rather than replay an API; and never apply to a provider whose `session_lifetime_minutes` is set, because a short session is not an authentication problem.
- **What to do instead when a provider is painful:** raise its cadence, or add it to the sitting. Both are cheaper and neither stores a secret.

- [ ] **Step 2: Commit**

```bash
mkdir -p docs/adr
git add docs/adr/2026-08-21-no-stored-credentials.md
git commit -m "Record why PaperPull stores no credential, and what would have to be true to change that"
```

---

## Self-review

**Spec coverage.** D1 (anchors, aged-out rule, bootstrap hole) → Tasks 1, 3. D2 (sentinel) → Task 2. D3 (`--unattended`, exit 0) → Task 5. D4 (`session_lifetime_minutes`, cron/sitting split) → Tasks 6, 7, 8, 9. D5 (`concurrency`, on-disk lock) → Tasks 10, 11. D6 (gate) → Task 13. The panel visibility the spec asks for ("the panel shows the adopted anchors so a human can sanity-check them once") → Task 4. Milk-run ordering → Task 7's `_urgency`, surfaced in Tasks 8, 9, 12.

**Type consistency.** An identity record is `{"id": str, "date": str}` everywhere: produced by `App._anchor_records`, consumed by `identity.pick_anchors`/`identity.check`, stored by `sentinel.write_anchors`. A due record's keys are fixed in Task 7's docstring and produced by `appload.accounts` in Task 8 — `session_lifetime_minutes`, `cadence_days`, `newest_document_date`, `last_checked_date`, `parked`, plus `config` and `output_dir` which only the CLIs use. `locks.acquire` returns the `Path` that `locks.release` takes.

**Known gaps, deliberately left.**
- Only Simyo is wired up (Tasks 3, 5, 11). The other seventeen apps keep working unchanged: `session_lifetime_minutes` and `concurrency` default to the patient/one values, and an app with no sentinel simply reports "nothing known". Rolling identity verification out to the rest is a follow-up per provider, and each needs its own answer to "what identifies an account here".
- `--unattended` is Simyo-only, so Task 9's scheduler will fail on any other app until that app gets the flag. **Mitigation to apply while doing Task 9:** the scheduler's `run_one` should treat a non-zero exit with `unrecognized arguments` in it as "not wired yet, skip", or the flag should be added to every app in the same change. Decide this at Task 9 and say which you chose in the commit message.
- The bootstrap hole in D1 is real and unclosed: the first `--adopt-identity` is taken on trust. Task 4's panel display is the compensating control.
