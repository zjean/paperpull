# Multi-account, scheduling, and credentials — design spec

**Date:** 2026-08-21
**Status:** accepted for phases 1–5; phase 6 gated (see below)

## The problem

Three asks:

1. **Multiple accounts per provider** — two Simyo subscriptions, two Amex cards,
   a spouse's account alongside your own.
2. **Scheduled, unattended runs** — nobody pressing buttons in the panel.
3. **Secure credential storage** — because sessions expire fast. Simyo signs
   out after ~10 minutes idle, and a second Mijn Simyo tab signs out the first.

## The reframe

The binding constraint is not storage. It is that **a session is perishable and
the only thing that can renew it is a human** (2FA, device approval, DigiD).
Storing a password does not change that for the provider that motivated the
question: Simyo enforces one live session and a ~10-minute idle timeout, so a
stored password buys an unattended *re-login*, not a longer session — and an
unattended re-login is precisely the thing this project was built not to do.

So the credential answer is: **store nothing that can log in.** Store who is
due and whether the session is alive, and schedule the human instead.

## What already exists

Multi-account is half-built and it is worth not rebuilding it:

- `<provider>_docs.py` accepts `--config`, so one copy of the code serves
  several accounts (`simyo_docs.py:143-149`).
- The panel enumerates `config.<account>.json` per app and passes an absolute
  `--config` (`gui/app.py:166-177`, `217-249`).
- Each config carries its own `output_dir`, so `progress.json`, the CSV and the
  PDFs never mix.

## What is actually missing

### 1. Nothing verifies which account a tab belongs to

`find_signed_in_page()` returns the first live page whose URL contains the
provider host (`simyo_site.py:230-242`), and in the Docker fork every app points
at **one** Chrome with **one** profile (`docker-compose.yml`, `--user-data-dir=
/config/pp-profile`). The owner stamped on every document and CSV row comes from
the config file and is never cross-checked against the page
(`storage.py:92-109`, `set_filename_owner`). With two accounts of the same
provider signed in, a run reads whichever tab it finds and files those documents
under the *config's* owner. There is no error. This is a silent
data-corruption bug, and it is the reason phase 1 comes first.

Upstream's `add_account.py` solves this with a profile dir and a port offset per
account. **That cannot work here**: one Chrome, one `--user-data-dir`, one
DevTools port. A second Chrome profile spawns a process without the debug port.

### 2. Nothing is safe to run unattended

`check_session()` answers a dead session with `ask()` — a blocking `input()`
(`simyo_docs.py:261-275`). Under the panel that waits forever on a pipe; under
cron it would hang a container. Unattended runs need a mode that parks and
exits cleanly instead of asking.

### 3. There is no scheduler, and no fact to schedule against

Nothing declares that Simyo's session lasts ~10 minutes while most providers
hold for days. That single fact is what splits "can run on plain cron" from
"needs a human present."

### 4. Nothing enforces one live session per provider

`_RUNS` in the panel is an in-memory dict (`gui/app.py:254`) that survives
nothing, and a direct CLI invocation bypasses it entirely.

## Design decisions

### D1 — Identity by document anchors, not by a whoami endpoint

Verify a tab by checking that documents the account is *known* to own are
present in what the tab is showing.

**Why not a whoami call:** Simyo's `is_safe_url()` allowlists exactly two read
endpoints and refuses everything else, deliberately, because the same
`/api/get` path also serves `createIdealPaymentRequestForInvoice`,
`sessionLogout` and `updateAddress` (`simyo_site.py:190-223`). Adding a
personal-data endpoint to that allowlist to learn a phone number conflicts with
the project's own minimalism — the CSV deliberately records no phone number,
customer number or IBAN (`apps/simyo/storage.py:19-26`). Anchors need no new
endpoint and no new data.

**The window problem:** Simyo's server keeps roughly the last twelve months and
drops the rest (`simyo_site.py:76-84`). So the oldest invoice numbers **age
out**, and a naive "these anchors must always be present" check would start
failing on its own after a year.

**The rule**, therefore, distinguishes the two reasons an anchor can be
missing:

| Anchor state | Verdict |
|---|---|
| present in what the tab shows | supports `OK` |
| absent, and its date is older than the oldest date the tab now shows | aged out — tolerated, triggers re-anchor |
| absent, and its date falls inside the window the tab is showing | `MISMATCH` — a document cannot leave an account's history |
| nothing recorded yet, or the tab shows nothing | `UNKNOWN` |
| every anchor aged out | `AGED` — no positive proof; treated like `UNKNOWN` |

Anchors are re-recorded after every `OK`, so the fingerprint rolls forward with
the window.

**The bootstrap hole, stated plainly:** on an account's first run there are no
anchors, so nothing can be verified and the first anchors adopted could come
from the wrong tab. Adoption therefore only happens in an explicitly
interactive command, never in an unattended run, and the panel shows the
adopted anchors so a human can sanity-check them once.

### D2 — Store a sentinel, not a credential

Per account, beside `progress.json`, a `sentinel.json` holding:

- `identity`: the anchors and when they were adopted.
- `session`: `warm` / `parked`, `last_verified_alive`, `parked_reason`.

Nothing in it can authenticate anything. It only knows *who is due* and
*whether the session was alive last time we looked*.

### D3 — `--unattended` parks instead of asking, and exits 0

A parked run is a normal outcome, not a failure: exit 0, one printed line, a
sentinel record. Real failures keep their non-zero codes so cron alerting still
means something.

### D4 — One declared fact splits cron from the milk run

`AppSpec.session_lifetime_minutes` — `None` means "days", a number means
short-lived. Simyo = 10. Providers with `None` run on plain cron; short-lived
ones are queued for a human sitting, ordered shortest-lifetime-first.

### D5 — Concurrency is declared, and the lock is on disk

`AppSpec.concurrency` (default 1) says how many live sessions a provider
tolerates. The lock lives in a file shared across an app's accounts, so it
survives a panel restart and also blocks someone running the CLI directly in
two terminals.

### D6 — Tier B (a real secret store) is gated, not built

A vault of passwords and TOTP seeds is *possible* — per-account sealed
envelopes, per-provider opt-in, an audit line per unseal, typing into the
CDP-attached tab rather than replaying an API. The honest problem is the key:
it must be readable by the container at unattended boot with no human present,
so anyone with root or compose access can decrypt it. That is the same access
that already reaches plaintext cookies in `./browser-profile`, which
`SECURITY.md` already calls as sensitive as the PDFs — so Tier B does not raise
the ceiling of a full compromise. What it does is widen a *partial* compromise
(someone who reads `./config` but never touches `./browser-profile`) from
"session cookies that expire" to "reusable passwords and TOTP seeds."

And it would not have a first customer: Simyo, the provider that prompted the
question, must not use it. So phase 6 lands the *guard* (a blocklist that
refuses Tier B for the financial providers) and an ADR recording the decision,
and leaves the envelope machinery unbuilt until a concrete provider needs it.

## Rejected

| Option | Why not |
|---|---|
| Sniff the session token, replay as plain HTTPS, drop the browser | Simyo's session is server-side and ~10 minutes; replaying does not extend it. Solves nothing for the motivating provider, and converts a read-alongside tool into an unsanctioned API client. |
| Per-account Chrome profile dirs + port offsets (upstream's `add_account.py`) | One Chrome, one `--user-data-dir`, one DevTools port in this fork. A second profile spawns a process with no debug port. |
| A forever keepalive heartbeat | Turns a 10-minute session into a permanently live credential on a network-reachable box, and is the cleanest bot signature you could hand a fraud team. |
| Scheduler-minted CDP attach tickets | CDP has no auth model; this needs an authenticating proxy in front of it. Right idea, wrong decade. |
| `provider_site.py` as a sandboxed selector DSL | Throws away the project's actual strength — one readable file of real code per provider — to defend against a threat this repo does not have. |
| Discover accounts from whatever tabs are open | Destroys the deterministic owner-stamping that filing depends on. |
| `window.name` as the pinned tab identity | Survives only because Simyo's site module never navigates; any provider that calls `page.goto` loses it. `targetId` is not stable public Playwright API and dies on container restart. Identity must be re-asserted per run from provider data — which is what D1 does. |

## Scope

Phases 1–4 give scheduled multi-account pulls with no credential stored
anywhere. Phase 5 hardens concurrency and adds the sitting UI. Phase 6 is a
decision gate. **Re-evaluate after phase 4** before committing to 5 and 6.
