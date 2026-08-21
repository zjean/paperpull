---
name: merge-upstream
description: Merge new work from rheeloaded/paperpull into this Docker fork. Use when the user says "merge upstream", "pull upstream changes", "sync with upstream", "get the new providers", when acting on an issue labelled `upstream`, or after the upstream-watch workflow reports new commits.
---

# Merging upstream into this fork

This repo is a fork of [rheeloaded/paperpull](https://github.com/rheeloaded/paperpull)
that adds Docker: the control panel served as a web UI, and the sign-in browser
in its own container reached over the DevTools protocol.

Upstream adds and fixes **providers**, which is the whole reason to keep
merging. Almost all of that work lands in files this fork never touches, so a
merge is usually boring. The parts that are not boring are listed under
"Where the conflicts live" below.

## Before you start

Confirm with your human partner which branch to merge into. Default to
`develop` — that is where this fork's work happens, and its image publishes to
`:beta`, so a merge can be tried before it becomes `:latest`.

## Steps

Make a todo per step and work through them in order.

### 1. Get upstream

```bash
git remote add upstream https://github.com/rheeloaded/paperpull.git 2>/dev/null
git fetch --no-tags upstream main
git switch develop && git pull --ff-only
```

Work on a branch, never straight on `develop`:

```bash
git switch -c merge-upstream-$(date +%Y-%m-%d)
```

### 2. Find out what is actually coming

Never guess at the overlap — compute it. A hand-maintained list of "files we
changed" goes stale the first time someone forgets to update it.

```bash
base=$(git merge-base HEAD upstream/main)

# What upstream did.
git log --no-merges --oneline "$base..upstream/main"

# Files both sides changed. These, and only these, can conflict.
comm -12 <(git diff --name-only "$base..HEAD"       | sort -u) \
         <(git diff --name-only "$base..upstream/main" | sort -u)
```

Report that overlap to your human partner **before** merging. If it is empty,
say so — that is the good case and the merge is mechanical.

### 3. Merge

```bash
git merge upstream/main
```

Resolve conflicts using "Where the conflicts live" below. Two rules:

- **A new or changed provider always wins.** `apps/<slug>/*_site.py`,
  `*_docs.py`, `document_rules.json`, `config.example.json` — take upstream's
  version wholesale. This fork has no opinion about how a provider works.
- **Never resolve a conflict by deleting Docker support**, and never resolve it
  by deleting an upstream file. Both make the *next* merge worse.

### 4. A new provider needs nothing, but check two things

A provider that arrives from upstream works in the container with no changes:
the entrypoint discovers any app directory holding a `*_docs.py` or
`*_receipts.py` and seeds `/config/<slug>/config.json` from its
`config.example.json`. The panel then passes that path as an absolute
`--config`, so nothing is written into the app directory. Confirm anyway:

```bash
docker build -t paperpull:dev .
docker run --rm paperpull:dev sh -c 'ls /config'
```

The new provider's slug must appear. Then check its seeded config points at
`/data/<slug>` and `http://localhost:9222`:

```bash
docker run --rm paperpull:dev sh -c 'cat /config/<slug>/config.json'
```

`cdp_url` is the one to watch. Upstream gives every app its own port so several
browsers can be open at once; the container has one shared browser, so the
entrypoint rewrites it. If a new app reads its port from somewhere other than
`cdp_url`, that rewrite misses it — check `*_docs.py` for a hardcoded port.

### 5. Verify, then report

Run all three. Do not report success on fewer.

```bash
# Unit tests, in the image.
docker run --rm paperpull:dev python -m pytest core/tests gui/tests -q -p no:cacheprovider

# The whole stack, including a real cross-container download. The browser can
# take a minute to be ready after a cold start.
docker compose up -d
docker compose exec -T paperpull python /app/tools/docker_smoke.py
docker compose down
```

If the browser never opens its DevTools port, that is one of two silent Chrome
behaviours rather than anything upstream did: `CHROME_CLI` losing its explicit
`--user-data-dir`, or the `browser` service losing its pinned `hostname`. Both
are explained in `docs/docker.md`.

If the smoke test's download check fails with a byte count of 0, upstream is
not at fault — something changed about `TMPDIR`, the shared `pw-artifacts`
volume, or the uid match between the containers. `docs/docker.md` explains why
those three matter.

Show your human partner the actual output. Then:

```bash
git push -u origin HEAD
```

and open a PR into `develop`. Close the `upstream`-labelled issue if the
upstream-watch workflow opened one.

## Where the conflicts live

This fork's whole divergence, so you know what to expect:

| File | This fork | On conflict |
|---|---|---|
| `gui/app.py` | **The most-edited upstream file.** Four areas: `_allowed_hosts()` for the reverse-proxy hostname, `_remote_browser()` so Login means `--login`, three extra keys from `/api/apps`, and the prompt channel below. Plus a desktop link, a gated venv warning, and the reply row in the HTML. | Take upstream's version, then re-apply all four. `gui/tests/test_remote_browser.py` and `gui/tests/test_prompts.py` tell you when you're done. |
| `gui/README.md` | Two paragraphs rewritten for the prompt channel: a run can be answered from the panel, and the account-holder question is skipped because a pipe is not a tty. | Take upstream's version, then re-apply. |
| `.gitignore`, `README.md`, `SECURITY.md` | Docker sections appended | Keep both sides; ours is additive. |
| `Dockerfile`, `docker-compose.yml`, `docker/**` (including `docker/browser/`, the derived linuxserver/chrome image), `.dockerignore`, `.env.example`, `tools/docker_smoke.py`, `gui/tests/**`, `docs/docker.md`, `docs/upstream.md`, `.github/workflows/**`, `.claude/skills/**` | New here; upstream has no version | Cannot conflict. |
| `apps/**`, `core/**`, `tools/*.py` (others), `docs/**` | Untouched | Take upstream. |

### The one silent regression to watch for

Upstream decided in 0.7.1 that a panel run gets **no stdin** (`stdin=DEVNULL`),
because natively nothing could answer a prompt. This fork overrides that: the
run gets a `PIPE`, the output is read as chunks rather than lines, and
`/api/answer` writes the reply. Take upstream's version of that `Popen` call by
accident and the container regresses to "a sign-out ends the run", with every
test still green except `gui/tests/test_prompts.py`. If any hunk in the merge
touches `stdin=`, `readline`, or `_chunk`, read the whole `stream()` function
before accepting it.

The `.bat` and `.command` launchers and `setup-all.*` are **deliberately left
in place unmaintained**. Docker is the supported path, but deleting files
upstream still edits would mean a delete/modify conflict on every future merge.
Leave them alone.
