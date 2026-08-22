# ntfy notifications for unattended runs — design

**Date:** 2026-08-22
**Status:** approved

## Context a fresh reader needs

PaperPull is a family of ~18 read-only document downloaders. Its founding
principle, stated throughout `README.md` and `SECURITY.md`, is that it **never
authenticates**: the human signs in to a real browser themselves, and the tool
attaches over the Chrome DevTools Protocol and only reads. No password exists
anywhere in the project.

Two pieces of earlier work matter here, both merged:

- **`docs/superpowers/specs/2026-08-21-multi-account-scheduling-credentials.md`**
  added multiple accounts per provider, a `scheduler` container that runs one
  unattended pass a day, and an identity gate that refuses to file one account's
  documents under another account's owner. It answers "where do the credentials
  live?" by storing none.
- A follow-up ported the same to Youfone, which is currently the only provider
  the scheduler actually pulls (Simyo is declared perishable and never runs
  unattended).

The mechanism this feature exists to surface is **parking**. An unattended run
that finds a dead session, no signed-in tab, a security challenge, or an
identity it cannot prove does not fail — it records the reason in that
account's `sentinel.json` and **exits 0**, deliberately, so that cron alerting
stays worth reading (`sentinel.park()`, and the `--unattended` handling in each
app's `main()`).

That design is correct and it has one consequence: **a parked account is
currently invisible unless somebody goes looking.** The state is in
`sentinel.json`, `tools/due.py` will list it, and `docker compose logs
scheduler` will show it — but nothing tells the human. An account can therefore
sit parked, needing thirty seconds of attention, for as long as nobody thinks
to check.

## Decision

Send an **ntfy** notification from the scheduler when an unattended pass
produces something a human must act on. Nothing else notifies.

### What notifies

| Event | Notifies |
|---|---|
| An account parked (exit 0 **and** its sentinel says parked) | **needs a human** |
| A perishable account is due (Simyo) — it is never started, so it can never park | **needs a human** |
| Provider busy (exit 4) | **error** |
| Bad flag combination (exit 2), or any other non-zero exit | **error** |
| Clean run, nothing parked (exit 0) | silent |
| Terminated by SIGTERM (exit 143) | silent |
| App skipped because its entry script has no `--unattended` | silent |
| The pass itself raises | **error** |

Two of those are judgement calls, recorded so they can be revisited rather than
rediscovered:

- **Exit 4 counts as an error.** A busy provider is transient and self-resolving,
  so silence is defensible. But it is rare by design, and if it stops being rare
  a lock is wedged and that account silently never runs again — which is exactly
  the class of invisible failure this feature exists to end. Noise here is
  cheaper than silence.
- **The pass sends one digest, not one push per account.** A four-account bad
  night is one message rather than four. The cost is that individual items
  cannot be dismissed separately.
- **A due perishable account counts as "needs a human" even though it never
  parks.** The scheduler deliberately never starts one — its session would be
  dead before a download finished — so it produces no exit code and no sentinel
  state to read. Left out, the one provider that can *only* be served by a
  person would be the one that never asks for one, which would invert the
  feature's purpose. It is not a park in the sentinel's sense, and the code
  says so where it is added.

### What does NOT notify

- **Panel-driven and hand-launched runs.** If a person pressed the button or
  typed the command, they are already watching the output; a push would buzz
  about something on their screen.
- **Quiet passes.** A pass with nothing due, or nothing to report, sends
  nothing. A daily "all fine" would be muted within a week and would then hide
  the messages that matter.

### Payload

Messages name the **provider and the account label**, plus the parked reason —
e.g. `youfone/primary — no signed-in tab`. This was chosen over an opaque
"1 account needs you, open the panel" because it is actionable from a phone
without opening anything.

The consequence, which `SECURITY.md` must state plainly: on ntfy.sh a topic has
no authentication and **the topic name is the credential**. Anyone who learns it
can read every message, which reveals which providers you use, your account
labels, and roughly when. Use an unguessable topic name, or a server of your
own. `PAPERPULL_NTFY_TOKEN` is supported for protected topics but is not
required.

## Architecture

A notifier module in the core, with the scheduler as its only caller.

```
apps/<provider>/<provider>_docs.py     writes the reason  ─┐
  sentinel.park(store, reason, when)                       │
                                                           ▼
                                             data/<app>/sentinel.json
                                                           │
                                              reads it back│
tools/schedule.py  ── one pass ──────────────────────────► appload.accounts()
      │                                                    (parked, parked_reason)
      │ builds one digest
      ▼
core/paperpull_core/notify.py ── HTTP POST ──► your ntfy topic
```

The reason string is already written by the app at the moment it parks, so the
scheduler reads back the same information the app had, without any egress in
the app layer.

### Why here and not in the app

Putting the send in `_park()` would give the same information one step earlier
and would also cover a hand-launched unattended run. It was rejected because it
places outbound network calls in the app layer — eventually eighteen copies,
each of which must independently be careful that a failed notification never
breaks a document run. One tested function enforcing that is worth more than the
one case it gives up.

### Components

**`core/paperpull_core/notify.py`** (new)

```python
configured(env=None) -> bool
send(title, message, tags=(), priority=None, *, env=None, opener=None) -> bool
```

Three properties, and they are the reason this is a module rather than a few
lines in the scheduler:

1. **It never raises.** Any failure — unreachable host, DNS, timeout, non-2xx,
   malformed config — is logged and returns `False`.
2. **It never blocks long.** A short timeout, and no retries. A missed
   notification is strictly better than a scheduler wedged on a socket.
3. **It is a no-op when unconfigured**, returning `False` without logging an
   error. An install that has not set the variable is not misconfigured.

`opener` is injected so tests never touch the network. Standard library only
(`urllib.request`) — `tools/docker_smoke.py` already sets that precedent, and
the no-new-dependencies constraint from the earlier work still holds.

**`core/paperpull_core/sentinel.py`** (extend): add `PARKED_REASON_KEY` and a
`parked_reason(store)` accessor. `park()` already writes the field; nothing
reads it.

**`core/paperpull_core/appload.py`** (extend): add `parked_reason` to the
records `accounts()` returns. Follow the convention already in that function,
which reads the sentinel through `sentinel.SESSION_KEY`, `sentinel.STATE_KEY`
and `sentinel.LAST_ALIVE_KEY` rather than string literals — a review of the
earlier work established that, precisely so the schema cannot desynchronise
silently. The new field uses the new constant the same way.

**`tools/schedule.py`** (extend): after each account's run, classify the outcome;
at the end of the pass, build one digest and send it if there is anything to
say.

### Configuration

| Variable | Meaning |
|---|---|
| `PAPERPULL_NTFY_URL` | Full topic URL, e.g. `https://ntfy.sh/<topic>` or `https://ntfy.example.com/<topic>`. Absent or empty disables the feature. |
| `PAPERPULL_NTFY_TOKEN` | Optional bearer token for a protected topic. |

Both are added to the `scheduler` service in `docker-compose.yml` and to
`.env.example`. `.env` is already gitignored.

### Message shape

```
Title:  PaperPull: 2 need you, 1 error

Needs you:
  youfone/primary — no signed-in tab
  simyo/jane — identity not adopted; press Adopt identity

Errors:
  ally/primary — exited 1
```

One message carries both classes. Tags and priority distinguish them at a
glance; the exact values are an implementation detail, not a contract.

## Testing

**No test may make a network call.** The notifier takes an injected `opener`;
the scheduler's digest is asserted by inspecting what it would send.

The cases that must be covered:

- Unconfigured is a silent no-op that returns `False`.
- A configured send builds the right URL, headers (including the token when set,
  and no auth header when not) and body.
- An opener that raises returns `False` and does not propagate.
- A non-2xx response returns `False` and does not raise.
- The scheduler sends nothing for a pass with nothing to report.
- The scheduler's digest names each parked account with its reason, and each
  errored account with its exit code.
- A pass that raises still produces an error notification.

## Documentation and release

- **`SECURITY.md`** — a new paragraph. This is the first thing the stack sends
  out; say what it contains, and that the topic name is the credential.
- **`docs/docker.md`** — setup, and what will and will not notify.
- **`CHANGELOG.md` / `VERSION`** — MINOR, per the changelog's own stated rule
  ("a cross-app feature"), taking the repo to 0.11.0. `core/pyproject.toml`
  gains a module, so it takes a bump too.

## Out of scope

- Notifying for panel-driven or hand-launched runs.
- Any notifier other than ntfy.
- Per-account or per-event pushes.
- Retries, queuing, or storing undelivered notifications.
- Notifying on success or on a quiet pass.
