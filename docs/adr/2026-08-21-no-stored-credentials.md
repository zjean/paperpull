# ADR: PaperPull stores no credential

**Date:** 2026-08-21
**Status:** accepted
**Related:** [`docs/superpowers/specs/2026-08-21-multi-account-scheduling-credentials.md`](../superpowers/specs/2026-08-21-multi-account-scheduling-credentials.md) (decision D6 and its Rejected table), [`SECURITY.md`](../../SECURITY.md), [`README.md`](../../README.md)

## Context

This work added scheduled, unattended multi-account runs: several accounts per
provider, a scheduler that decides who is due
(`core/paperpull_core/due.py`, `core/paperpull_core/appload.py`), and an
`--unattended` mode that parks a dead session instead of blocking on a prompt
(`apps/simyo/simyo_docs.py`).

That raised the obvious next question: if nobody is sitting at the browser,
what gets a session back when it dies? A stored credential — a password, a
TOTP seed — is the obvious answer, and it is the one this ADR rejects.

PaperPull's founding design is that it never authenticates: a human signs in
to a real browser themselves, and the tool attaches over the Chrome DevTools
Protocol and only reads (`README.md`, "The one decision everything follows
from"; `SECURITY.md`, "You sign in, not the tool"). No password exists
anywhere in the project today. Storing one would be a first for this
codebase, not an extension of something already there, so it deserves a
recorded decision rather than a quiet accretion.

## Decision

**PaperPull stores no credential.** Phases 1–4 of this work deliver scheduled
multi-account pulls without one:

- `core/paperpull_core/sentinel.py` persists, per account, an identity (the
  document anchors that fingerprint which account a tab belongs to) and a
  session state (`warm` or `parked`, and when it was last verified alive).
  Its own docstring says the constraint plainly: "Nothing in it authenticates
  anything. There is no password here, no cookie, no token and no session id,
  and nothing in this file can be replayed against a provider."
- `core/paperpull_core/due.py` and `core/paperpull_core/appload.py` answer
  "who is due, and in what order" from that sentinel plus each account's
  `progress.json` and each app's declared `session_lifetime_minutes`
  (`core/paperpull_core/spec.py:91`) — no secret is consulted or needed to
  answer either question.
- `apps/simyo/simyo_docs.py`'s `--unattended` mode turns a dead session or a
  missing signed-in tab into a parked account and exit code 0, not a failure
  and not a prompt.

A vault therefore has no job to do: everything scheduling needs — who is due,
whether the session was alive last time, how urgently a provider should be
sat with — is already answerable from facts that cannot log in to anything.

## Why the obvious version does not help

The provider that prompted this question is Simyo, and Simyo is precisely the
case a credential store cannot serve. Mijn Simyo enforces **one live session**
per account — a second tab signs the first one out — and times that session
out after roughly ten minutes idle (`apps/simyo/simyo_site.py`, module
docstring notes 1 and 6; `AppSpec.session_lifetime_minutes=10` in
`apps/simyo/storage.py:63`). A stored password does not extend a live
session; the session is already dead by the time anything would use it. What
it would buy is an unattended **re-login** — and an unattended re-login is
exactly the thing this project was built not to do (`README.md`: "PaperPull
never tries to *be* you"). Re-authenticating as a bot, on a schedule, against
a telecom provider's login form is also the kind of traffic pattern a fraud
system is built to catch, which is a cost with no matching benefit here: the
session it buys back still dies in ten minutes.

So the case that motivated "store a credential" is the one case where storing
a credential would not fix the actual problem, which is a perishable session,
not a missing password.

## The key problem, stated plainly

Set the motivating case aside; suppose some future provider's pain really is
"a human isn't present to type a password," not "the session is too short."
The problem with a credential store is not implementation difficulty. It is
that an unattended container has to be able to decrypt its own vault at boot,
with no human present to type an unlock passphrase — which means the sealing
key has to be readable by the container unattended, which means **anyone with
root or compose access on the host can read it too**. There is no design that
avoids this: a secret an unattended process can use without a human is, by
construction, a secret that anything running with that process's privileges
can also use.

That is not a new exposure this project would be inventing. It is the same
access that already reaches `./browser-profile`, which holds live,
plaintext, replayable session cookies for every signed-in provider —
`SECURITY.md` already names this the worst thing that could leak and says
`./browser-profile` is "as sensitive as the PDFs." The deployment this
matters for (`docker-compose.yml`: three containers — `browser`, `paperpull`
and the `scheduler` this work added — two of them behind a reverse proxy, each
with `restart: unless-stopped`) already puts that file where root or compose
access on the box reaches it. So a credential vault would **not raise the
ceiling of a full compromise** — whoever already reads `./browser-profile`
already has everything a vault could additionally hand them, in a more
directly usable form (a live cookie, not a password to type in).

What a vault *would* do is widen a **partial** compromise. Someone who reads
`./config` (say, a backup that captures one volume but not the other, or a
misconfigured share) but never reaches `./browser-profile` today gets nothing
useful — a leaked config is account labels and paths. Add a credential store
and that same partial reach becomes reusable passwords and TOTP seeds: a
strictly worse thing to hand over than a cookie that eventually expires. A
vault is therefore not a security improvement sitting idle; it is a new
liability with no user it currently serves.

## The gate

This decision is not "never." It is "not yet, and not without all of the
following landing in one change" — because a partial version of a credential
store is worse than none: it would carry the risk above without the
discipline that makes the risk worth taking.

Before any future work stores a credential of any kind, it must, in the same
change:

1. **Be opt-in per provider, default off.** No account gets a stored
   credential just because the feature exists.
2. **Refuse the financial providers in code, not in documentation** — `amex`,
   `chase`, `usaa`, `robinhood`, `wealthfront`, `navyfederal`, `ally`,
   `redcard`. A comment saying "don't use this for banks" is not a gate; a
   check that raises before a bank's config can enable it is.
3. **Store one sealed envelope per account**, never one blob covering several
   accounts — so reading one account's secret never exposes another's, and
   revoking one account's access never touches the rest.
4. **Write an audit line per unseal** — every time the vault is opened, a
   record of when and for which account, so a compromise is at least visible
   after the fact even though it cannot be prevented at the OS-privilege
   level described above.
5. **Type into the CDP-attached tab, rather than replay an API request.**
   Consistent with how every app here already works: a credential store would
   automate the sign-in *form*, not become a second, unsanctioned API client
   speaking on the user's behalf outside the browser.
6. **Never apply to a provider whose `session_lifetime_minutes` is set.** A
   short-lived session is not an authentication problem, and a vault does not
   fix it — see "Why the obvious version does not help" above. A future
   change that tries to use a credential store to solve a short-session
   provider's pain has misdiagnosed the problem, no matter how the store
   itself is built.

Any change that stores a credential but skips one of these is not a smaller
version of this decision — it is the risk in "The key problem" above, taken
on without the mitigations that would justify it.

## What to do instead when a provider is painful

Two levers already exist and neither stores a secret:

- **Raise the provider's cadence.** `cadence_days` (read in
  `core/paperpull_core/due.py`, default 31) controls how many days pass
  before an account is due again. Widening it for a painful provider means
  fewer sittings are asked for in the first place — a monthly biller checked
  every two months instead of every month, say — which is a cheaper answer to
  "this session is annoying to renew" than making the renewal unattended.
- **Add it to the sitting.** A provider with a short `session_lifetime_minutes`
  is, by design (D4 in the spec), queued for a human sitting rather than run
  on plain cron, ordered most-perishable-first. The unattended path already
  exists in the scheduler for the providers it fits; the providers it does not
  fit are meant to ask a person to sit down, not to grow a workaround that
  removes the person.

Both are cheaper than building and operating a vault, and both leave the
project's central guarantee — no password exists anywhere in this project —
intact.

## Consequences

- Some providers with short sessions (today, only Simyo) will keep needing a
  human sitting rather than running fully unattended. That is accepted, not a
  gap to be engineered away.
- Any future proposal to store a credential must be evaluated against the
  gate above as a whole, in one change. A proposal that satisfies some of the
  six conditions and defers the rest is not a partial win; defer the whole
  proposal until all six are ready together.
- This decision can be revisited if a concrete provider needs it and can meet
  every condition in "The gate" — not on the general appeal of "fewer sittings
  would be nice."
