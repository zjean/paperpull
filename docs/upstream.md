# Staying in sync with upstream

This is a fork of [rheeloaded/paperpull](https://github.com/rheeloaded/paperpull)
that adds Docker. Upstream keeps adding and fixing **providers**, which is the
whole reason to keep merging.

The divergence is deliberately small so that stays cheap. Everything this fork
adds is a new file upstream has no version of, with one exception: **the
control panel is this fork's own now.** `gui/app.py` was an edited copy of
upstream's until the panel was rebuilt around accounts rather than flags, and
the page it serves lives in `gui/static/`, which upstream has no counterpart
for at all. Treat the panel the way you treat `docker/` — ours — and see
*Resolving conflicts* below for what that means in practice.

## Branches

| Branch | Publishes | For |
|---|---|---|
| `develop` | `ghcr.io/zjean/paperpull:beta` | where work lands, including upstream merges |
| `main` | `ghcr.io/zjean/paperpull:latest` | what you run |

Merge upstream into `develop`, run it as `:beta` for a bit, then promote.

## Doing it

Either run the **`merge-upstream`** skill in Claude Code — it does all of the
below and gates on the container smoke test — or by hand:

```bash
git remote add upstream https://github.com/rheeloaded/paperpull.git
git fetch --no-tags upstream main
git switch develop && git switch -c merge-upstream-$(date +%F)

# What is coming, and which files can actually conflict.
base=$(git merge-base HEAD upstream/main)
git log --no-merges --oneline "$base..upstream/main"
comm -12 <(git diff --name-only "$base..HEAD"          | sort -u) \
         <(git diff --name-only "$base..upstream/main" | sort -u)

git merge upstream/main
```

That `comm` is the whole trick: it computes the overlap rather than trusting a
list someone has to remember to update. Empty output means the merge is
mechanical.

Then verify — all three, every time:

```bash
docker build -t paperpull:dev .
docker run --rm paperpull:dev python -m pytest core/tests gui/tests -q -p no:cacheprovider
docker compose up -d && docker compose exec -T paperpull python /app/tools/docker_smoke.py
```

Upstream never touches `docker/browser/` — that image is linuxserver/chrome plus
a socat service and contains no PaperPull code — so a merge cannot break it.

## The watcher, and why it may be silent

`.github/workflows/upstream-watch.yml` runs the same scan script the skill does
— `.claude/skills/merge-upstream/scripts/upstream_scan.sh` — writes the report
to the run summary, and files it as an issue labelled `upstream`.

Two things can make it produce nothing, and neither is a bug in the workflow:

- **A `schedule:` only fires from the repository's default branch.** These
  workflows live on `develop`; the default branch is `main`, which has no
  `.github/workflows/` directory at all. So the Monday cron does not run, and
  the workflow does not even appear in the Actions UI. It starts working the
  moment `develop` is promoted to `main` — or immediately, if the default
  branch is switched to `develop`. Until then, use `workflow_dispatch` from
  `develop`, or just run the skill locally.
- **Issues can be switched off on a fork**, and they currently are here. The
  workflow checks `has_issues` first and warns instead of failing, so the scan
  still lands in the run summary. Settings → General → Features to turn them
  back on.

## Resolving conflicts

**A provider always wins.** Anything under `apps/<slug>/` — `*_site.py`,
`*_docs.py`, `document_rules.json`, `config.example.json` — take upstream's
version wholesale. This fork has no opinion about how a provider works.

**The control panel is ours. Keep this side.** This used to say "take
upstream's version, then re-apply these three" — do not do that any more. It
would delete a panel that no longer resembles upstream's: the page is three
files in `gui/static/` rather than a string in `app.py`, it draws itself from
`/api/state` rather than `/api/apps`, and the account-first register, the
buckets, the numbered path and the on-page sitting have no upstream equivalent
to merge with.

So on a conflict in `gui/app.py`, take **ours**, and then read upstream's
version for one thing only: whether it has taught the panel something we would
want. In practice that is a change to the command surface, not to the page —

- a new entry in `ACTIONS` (a flag upstream's apps grew), which here also needs
  a `label` and a `blurb`, and a `step` only if it belongs on the ordinary path;
- a change to how a command is built in `_build_cmd` (a new `--config`
  convention, say);
- a change to how an app is discovered (`_entry_script`, `ENTRY_RE`).

Everything else in upstream's `gui/app.py` — the HTML, the CSS, the JS, the
`/api/apps` shape — is superseded here.

`gui/tests/test_remote_browser.py` still tells you the Docker switches survived
in both directions, and `gui/tests/test_state.py` tells you the panel still
explains itself: it fails if an action arrives with no sentence saying what it
does, which is exactly the state upstream's panel is in.

**Never resolve a conflict by deleting Docker support, or by deleting an
upstream file.** Both make the next merge worse.

## Why a stale GIF sits in docs/

`docs/control-panel.gif` is upstream's animated demo of upstream's panel, and
it no longer shows a UI that exists here. Nothing references it — the READMEs
point at `docs/control-panel.png`, a still of this fork's panel. The GIF stays
on disk for the same reason the launchers below do: it is an upstream file, and
deleting one produces a delete/modify conflict on every future merge, forever.
Ignore it.

## Why the launchers are still here

The `.bat` and `.command` launchers and `setup-all.*` are unmaintained — Docker
is the supported path. They are kept anyway, because deleting a file upstream
still edits produces a delete/modify conflict on *every* future merge, forever.
Leaving them costs nothing. Ignore them.

## A new provider needs no Docker work

The entrypoint discovers any directory under `apps/` holding a `*_docs.py` or
`*_receipts.py`, seeds a `config.json` from its `config.example.json`, and links
it in. So a provider that arrives from upstream — or one you write yourself —
appears in the panel with no changes here.

The one thing to check is the port. Upstream gives every app its own debugging
port; this fork has one shared browser, so the entrypoint rewrites `cdp_url` to
`http://localhost:9222`. If a new app gets its port from somewhere other than
`cdp_url`, that rewrite misses it.
