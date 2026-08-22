---
name: merge-upstream
description: Sync this Docker fork with rheeloaded/paperpull — scan what upstream changed, assess what it costs or gains each fork surface (the container, the control panel, the scheduler), merge it, verify it, and report which new upstream work is worth wiring into Docker/panel/cron. Use this whenever the user says "merge upstream", "pull upstream changes", "sync with upstream", "check upstream", "what's new upstream", "get the new providers", asks whether an upstream change affects the container, web UI or the schedule, or asks whether the fork should also implement something upstream just added; also when acting on an issue labelled `upstream` or after the upstream-watch workflow reports new commits.
---

# Syncing this fork with upstream

This repo is a fork of [rheeloaded/paperpull](https://github.com/rheeloaded/paperpull).
Upstream is a collection of per-provider CLI downloaders. This fork wraps them
in three surfaces upstream has no idea exist:

| Surface | Lives in | What breaks it |
|---|---|---|
| **The container** | `Dockerfile`, `docker/entrypoint.sh`, `docker-compose.yml`, `docker/browser/` | an app that finds its browser somewhere other than `cdp_url`, or ships no `config.example.json` |
| **The control panel** | `gui/app.py` (the one upstream file this fork edits) | a new action or flag the `ACTIONS` table doesn't know; upstream re-taking the `stdin=PIPE` prompt channel |
| **The scheduler** (the "cron") | `tools/schedule.py`, the `scheduler` compose service | an app that can't run with nobody present, or a new `AppSpec` field that changes what "due" means |

It also owns **two providers upstream doesn't have** — `apps/simyo` and
`apps/youfone` — and they are the only two carrying the fork's own features:
the multi-account identity gate, `--unattended` parking, and the per-provider
session lock. Upstream's providers have none of that, and that is the standing
question every merge has to answer: *does the thing that just arrived need it?*

So a merge here is two jobs, not one. Merging is the easy half. The half worth
your attention is deciding what the new upstream work means for those three
surfaces — and that decision belongs to your human partner, so your job is to
put it in front of them with enough evidence to make it cheaply.

## Before you start

Confirm which branch to merge into. Default to `develop`: that is where this
fork's work happens, and its image publishes to `:beta`, so a merge can be run
for real before it becomes `:latest`.

Make a todo per step below and work through them in order.

## 1. Get upstream, on a branch

```bash
git remote add upstream https://github.com/rheeloaded/paperpull.git 2>/dev/null
git fetch --no-tags upstream main
git switch develop && git pull --ff-only
git switch -c merge-upstream-$(date +%F)
```

Never merge straight onto `develop` — the assessment in step 3 can end in "we
should not take this yet", and that has to stay possible.

## 2. Scan

```bash
bash .claude/skills/merge-upstream/scripts/upstream_scan.sh
```

Read-only. It computes, rather than trusting any hand-maintained list:

- the new commits;
- **the overlap** — files changed on both sides, which is exactly the set that
  can conflict;
- **new providers**, and for each one: whether the entrypoint can seed it,
  whether its debugging port hides somewhere the entrypoint's `cdp_url` rewrite
  won't find, which panel buttons it will actually show, and which of the
  fork's four features it lacks;
- **flags new to the whole project**, which are the candidates for new panel
  actions or scheduler behaviour;
- which fork surfaces the range reaches into at all.

If the script fails or you need something it doesn't compute, fall back to the
primitives — `git merge-base`, `git diff --name-only`, `comm -12` — rather than
guessing. `docs/upstream.md` spells them out.

## 3. Assess the impact — the part that isn't mechanical

The scan gives you facts. Turn them into a judgement by answering four
questions in order. Nothing here modifies the tree; you are still deciding
whether and how to merge.

### Q1 — Does anything conflict?

Empty overlap means the merge is mechanical and you can move on. A non-empty
overlap is not alarming by itself — `README.md`, `CHANGELOG.md`, `VERSION` and
the other docs overlap on almost every merge because both sides append to them.
What matters is whether `gui/app.py`, `core/paperpull_core/spec.py` or
`core/paperpull_core/storage.py` are in there; see the table at the bottom.

### Q2 — Does a new provider work in the container, unchanged?

Usually yes, and that is by design: the entrypoint discovers any directory
under `apps/` holding a `*_docs.py` or `*_receipts.py`, seeds
`/config/<slug>/config.json` from its `config.example.json`, rewrites
`output_dir`, `profile_dir` and `cdp_url`, and the panel passes that path as an
absolute `--config`. Nothing is written into the app directory.

The two ways that fails, both of which the scan checks:

- **No `config.example.json`.** Nothing gets seeded, so the app runs against
  whatever it compiles in and writes inside the image, where a rebuild throws it
  away.
- **A debugging port that isn't in `cdp_url`.** Upstream gives every app its own
  port (Ally 9235, AAFMAA 9238…) so several browsers can be open natively. This
  fork has one shared browser on `localhost:9222`, and the entrypoint rewrites
  `cdp_url` to reach it. A port read from anywhere else — a module constant, a
  separate key — silently misses the rewrite, and the app fails to attach with
  nothing useful in the log.

### Q3 — Does the new work degrade gracefully, or does it lie?

The panel and the scheduler both already gate per app, by reading each entry
script's own text rather than consulting a table that could drift:
`gui/app.py`'s `_supported_actions` hides a button whose flags the script
doesn't accept (and `_build_cmd` refuses it server-side too, so a direct POST
can't get past a hidden button), and `tools/schedule.py`'s
`_supports_unattended` skips an app that can't run alone — *loudly*, naming it,
because that line is what tells a human which provider to teach next.

So the honest default is: a new upstream provider shows up in the panel, can be
driven by hand, and is named-and-skipped by the scheduler. Check that this is
what actually happens rather than assuming it. When the gate is missing —
a new flag reaching a script that doesn't take it, an action offered to every
app — that is a bug in this fork's gating, and it is worth fixing in the merge
itself rather than filing.

### Q4 — Is the new upstream work worth extending into the fork's surfaces?

This is the question the user actually wants answered, and it has three shapes:

- **A new provider that could run on the schedule.** If its
  `session_lifetime_minutes` is `None`, its session is patient, so it *could*
  run unattended nightly — it just needs `--unattended` parking, and probably
  the identity gate and the lock with it. That is a real port: look at
  `apps/youfone/youfone_docs.py`'s history for the shape of one, and note what
  the Youfone commit did well — it adapted each piece to where Youfone differs
  from Simyo instead of copying Simyo's constraints across, and it recorded
  unverified assumptions as assumptions.
- **A flag new to the project.** Decide where it belongs: a panel action in
  `ACTIONS`, a flag in the scheduler's `UNATTENDED_FLAGS`, or deliberately
  CLI-only. "CLI-only" is a real answer — `docker compose run --rm paperpull
  python apps/<slug>/<slug>_docs.py --whatever` already works through the
  entrypoint, so a rarely-used flag does not need a button.
- **A new `AppSpec` field or core capability.** Anything the scheduler's
  `due.plan` or the panel's app listing could read is a candidate. Say so; do
  not wire it in unprompted.

**Report; don't build.** Write the gap list, hand it over, and let your human
partner choose. A merge PR that also ports three providers is a PR nobody can
review. If they want the work tracked rather than done, offer to open an issue
per gap — the `upstream`-labelled issue flow already exists.

## 4. Merge

```bash
git merge upstream/main
```

Two rules that override any local cleverness:

- **A new or changed provider always wins.** `apps/<slug>/*_site.py`,
  `*_docs.py`, `document_rules.json`, `config.example.json` — take upstream's
  version wholesale. This fork has no opinion about how a provider works.
  (`apps/simyo` and `apps/youfone` are ours; upstream has no version to take.)
- **Never resolve a conflict by deleting Docker support, and never by deleting
  an upstream file.** Both make the *next* merge worse.

Then resolve using the table at the bottom.

## 5. Verify

Run all of it. Do not report success on a subset.

```bash
docker build -t paperpull:dev .
docker run --rm paperpull:dev python -m pytest core/tests gui/tests -q -p no:cacheprovider
```

Then the surface checks — one per surface, each answering a question the unit
tests cannot:

```bash
# Container: every provider, including the new ones, got a config seeded, and
# every seeded config points at the shared browser.
docker run --rm paperpull:dev sh -c 'ls /config && grep -h cdp_url /config/*/config.json | sort -u'

# Panel: the new app is discovered, and its buttons match what it can accept.
docker run --rm -d --name pp-check -p 8765:8765 -e PAPERPULL_ALLOWED_HOSTS=localhost paperpull:dev
curl -s localhost:8765/api/apps | python3 -m json.tool | grep -A3 '"<new-slug>"'
docker rm -f pp-check

# Scheduler: one pass, planned but not run. Every account is either planned or
# named as skipped-and-why; an app that vanishes silently is the bug to catch.
docker run --rm paperpull:dev python /app/tools/schedule.py --once
```

`grep -h cdp_url` finding anything other than `http://localhost:9222` is Q2's
failure, caught. `schedule.py --once` on a fresh container has no accounts to
run, which is fine — you are reading its *reasoning*, not its downloads.

Finally the whole stack, including a real cross-container download. The browser
can take a minute to be ready after a cold start.

```bash
docker compose up -d
docker compose exec -T paperpull python /app/tools/docker_smoke.py
docker compose down
```

Two failures that are never upstream's fault, so don't go looking there:

- **The browser never opens its DevTools port** — `CHROME_CLI` lost its
  explicit `--user-data-dir`, or the `browser` service lost its pinned
  `hostname`. Both in `docs/docker.md`.
- **The download check reports 0 bytes** — something changed about `TMPDIR`,
  the shared `pw-artifacts` volume, or the uid match between the containers.
  Also `docs/docker.md`.

## 6. Report, then hand over

Show the real output — not a summary of it. Then one report, in this shape:

```markdown
## Merged
<n> upstream commits. Conflicts: <files, or "none">. Tests, smoke: <result>.

## Arrived and works as-is
- `<slug>`: seeded, appears in the panel, <n> buttons, scheduler names it as
  skipped (no `--unattended`).

## Gaps, worth deciding
| # | Gap | Surface | Why it matters | Rough size |
|---|---|---|---|---|
| 1 | `<slug>` can't run unattended | scheduler | patient session, so nightly runs are possible; today it's hand-driven only | port of the Youfone work: parking, identity gate, lock, tests |

## Recommend
<one or two sentences: what you'd do first, and what you'd leave.>
```

Then:

```bash
git push -u origin HEAD
```

Open a PR into `develop`. Close the `upstream`-labelled issue if the
upstream-watch workflow opened one, and offer to open an issue per gap.

## Where the divergence lives

| File | This fork | On conflict |
|---|---|---|
| `gui/app.py` | **The most-edited upstream file.** `_allowed_hosts()` for the reverse-proxy hostname; `_remote_browser()` so Login means `--login`; `_supported_actions()` gating buttons per app; extra `/api/apps` keys (`remote_browser`, `browser_url`, `expect_venvs`, `supported_actions`, `login_flag`); the prompt channel below; the desktop link, gated venv warning and reply row in the HTML. | Take upstream's version, then re-apply all of it. `gui/tests/test_remote_browser.py`, `test_action_gating.py` and `test_prompts.py` tell you when you're done. |
| `core/paperpull_core/spec.py` | `AppSpec.session_lifetime_minutes` and `concurrency`, plus their validation. These two fields are what route a provider to unattended cron or to a human sitting. | Keep both sides. Losing a field breaks `due.plan` and every app's spec. |
| `core/paperpull_core/storage.py` | `ensure_owner(..., unattended=False)` and `Paths.sentinel_json`. | Keep both sides. Dropping the `unattended` parameter means a scheduled run can block on `input()` forever. |
| `core/paperpull_core/__init__.py`, `core/pyproject.toml` | core bumped to `0.2.0` for the modules the fork added | Keep the higher version — the fork's API is a superset. |
| `VERSION`, `CHANGELOG.md` | **Both sides release independently, and have already collided at `0.10.0`.** | Keep both sets of entries under distinct headings and bump this fork past upstream's number. Never squash one side's release notes into the other's. |
| `README.md`, `SECURITY.md`, `CONTRIBUTING.md`, `PROVIDERS.md`, `.gitignore`, `docs/adding-a-provider.md`, `gui/README.md` | Docker/panel/scheduler sections added or paragraphs rewritten | Keep both sides; ours is additive. Re-apply the rewritten paragraphs by hand. |
| `core/paperpull_core/{appload,due,identity,locks,sentinel}.py`, `tools/schedule.py`, `Dockerfile`, `docker-compose.yml`, `docker/**`, `.dockerignore`, `.env.example`, `tools/docker_smoke.py`, `gui/tests/**`, `docs/docker.md`, `docs/upstream.md`, `docs/adr/**`, `.github/workflows/**`, `.claude/skills/**` | New here; upstream has no version | Cannot conflict — unless upstream adds a file with the same name, which is an add/add conflict and needs a real decision, not a resolution. |
| `apps/simyo/**`, `apps/youfone/**` | Ours; upstream has neither | Cannot conflict. |
| everything else under `apps/`, `core/`, `tools/`, `docs/` | Untouched | Take upstream. |

### The one silent regression to watch for

Upstream decided in 0.7.1 that a panel run gets **no stdin**
(`stdin=DEVNULL`), because natively nothing could answer a prompt. This fork
overrides that: the run gets a `PIPE`, output is read as chunks rather than
lines, and `/api/answer` writes the reply. Take upstream's `Popen` call by
accident and the container regresses to "a sign-out ends the run" — with every
test still green except `gui/tests/test_prompts.py`. If any hunk touches
`stdin=`, `readline`, or `_chunk`, read the whole `stream()` function before
accepting it.

Note the mirror-image case in `tools/schedule.py`, which passes
`stdin=DEVNULL` *deliberately*: a scheduled run has no human by definition, and
an inherited tty is exactly how `ensure_owner`'s `isatty()` check waves a
prompt through and blocks forever. Same symbol, opposite correct answer —
which is why both carry a comment saying so.

### Why the launchers are still here

The `.bat` and `.command` launchers and `setup-all.*` are **deliberately
unmaintained**. Docker is the supported path, but deleting files upstream still
edits would mean a delete/modify conflict on every future merge, forever.
Leave them alone.
