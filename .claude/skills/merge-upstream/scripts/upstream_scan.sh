#!/usr/bin/env bash
# What is coming from upstream, and which of this fork's surfaces it lands in.
#
# Read-only: fetches nothing, merges nothing, writes nothing but stdout. Run it
# before deciding anything. Everything it prints is computed from the two trees
# — no hand-maintained list of "files we changed" that goes stale the first time
# somebody forgets to update it.
#
#   ./upstream_scan.sh                 # against upstream/main
#   ./upstream_scan.sh upstream/main   # explicit
set -euo pipefail

UP="${1:-upstream/main}"
git rev-parse --verify -q "$UP" >/dev/null || {
  echo "No such ref: $UP. Fetch it first:" >&2
  echo "  git remote add upstream https://github.com/rheeloaded/paperpull.git" >&2
  echo "  git fetch --no-tags upstream main" >&2
  exit 2
}

base="$(git merge-base HEAD "$UP")"
new="$(git rev-list --count "$base..$UP")"

echo "# Upstream scan"
echo
echo "- ours: \`$(git rev-parse --abbrev-ref HEAD)\` @ $(git rev-parse --short HEAD)"
echo "- theirs: \`$UP\` @ $(git rev-parse --short "$UP")"
echo "- common ancestor: $(git rev-parse --short "$base")"
echo "- new upstream commits: **$new**"
echo

[ "$new" = "0" ] && { echo "Up to date. Nothing to do."; exit 0; }

echo "## Commits"
echo
git log --no-merges --pretty='- %h %s' "$base..$UP"
echo

# ---------------------------------------------------------------------------
# 1. Overlap. Only a file both sides touched can conflict.
# ---------------------------------------------------------------------------
git diff --name-only "$base..HEAD" | sort -u > /tmp/ours.$$
git diff --name-only "$base..$UP"  | sort -u > /tmp/theirs.$$
comm -12 /tmp/ours.$$ /tmp/theirs.$$ > /tmp/overlap.$$

echo "## Files changed on both sides"
echo
if [ -s /tmp/overlap.$$ ]; then
  while read -r f; do echo "- \`$f\`"; done < /tmp/overlap.$$
else
  echo "None — the merge is mechanical."
fi
echo

# The panel's ACTIONS table, read out of gui/app.py rather than copied here, so
# this cannot drift from what the panel will actually offer.
actions_py=$(cat <<'PY'
import re, sys, pathlib
src = pathlib.Path("gui/app.py").read_text(encoding="utf-8")
m = re.search(r"^ACTIONS = \{(.*?)^\}", src, re.S | re.M)
body = m.group(1) if m else ""
for key, flags in re.findall(r'"([a-z]+)":\s*\{[^}]*"flags":\s*\[([^\]]*)\]', body, re.S):
  fl = [f.strip().strip('"') for f in flags.split(",") if f.strip()]
  print(key + "\t" + " ".join(fl))
PY
)

# ---------------------------------------------------------------------------
# 2. New providers. A directory under apps/ that does not exist here yet.
# ---------------------------------------------------------------------------
echo "## New providers"
echo
newapps=""
for slug in $(git ls-tree --name-only "$UP" apps/ | sed 's|apps/||;s|/$||' | sort -u); do
  [ -n "$slug" ] || continue
  if ! git cat-file -e "HEAD:apps/$slug" 2>/dev/null; then
    newapps="$newapps $slug"
  fi
done

if [ -z "$newapps" ]; then
  echo "None."
else
  # The panel's ACTIONS table, read out of gui/app.py rather than copied here,
  # so this cannot drift from what the panel will actually offer.
  for slug in $newapps; do
    entry=$(git ls-tree --name-only -r "$UP" "apps/$slug/" | grep -E '_(docs|receipts)\.py$' | head -1)
    echo "### \`$slug\`"
    echo
    if [ -z "$entry" ]; then
      echo "- no \`*_docs.py\` / \`*_receipts.py\` — the entrypoint and the panel will both"
      echo "  ignore it, so it does not appear in the container at all. Probably a scaffold."
      echo
      continue
    fi
    text=$(git show "$UP:$entry")
    echo "- entry script: \`$entry\`"

    if git cat-file -e "$UP:apps/$slug/config.example.json" 2>/dev/null; then
      port=$(git show "$UP:apps/$slug/config.example.json" | grep -o '"cdp_url":[^,]*' || true)
      echo "- \`config.example.json\`: present — the entrypoint will seed \`/config/$slug/config.json\`. ${port:+($port, rewritten to :9222)}"
    else
      echo "- **no \`config.example.json\`** — the entrypoint seeds nothing, so the app runs"
      echo "  against whatever default it compiles in, writing inside the image. Needs work."
    fi

    stray=$(printf '%s' "$text" | grep -nE '9(2[0-9][0-9])' | grep -v cdp_url || true)
    if [ -n "$stray" ]; then
      echo "- **hardcoded debugging port outside \`cdp_url\`** — the entrypoint's rewrite misses it:"
      printf '%s\n' "$stray" | sed 's/^/      /'
    fi

    printf '%s' "$text" | grep -q 'session_lifetime_minutes' \
      && echo "- declares \`session_lifetime_minutes\` — check the value: non-None means the scheduler will never start it unattended (it needs a human present)." \
      || echo "- no \`session_lifetime_minutes\` — patient session, so the scheduler *would* run it if it took \`--unattended\`."

    echo "- fork-feature parity:"
    for probe in "--unattended:scheduler can run it at all" \
                 "--adopt-identity:panel can offer Adopt identity" \
                 "ensure_identity:multi-account identity gate" \
                 "locks.hold:per-provider session lock"; do
      needle="${probe%%:*}"; why="${probe#*:}"
      if printf '%s' "$text" | grep -q -- "$needle"; then
        echo "    - [x] \`$needle\` ($why)"
      else
        echo "    - [ ] \`$needle\` — absent ($why)"
      fi
    done

    echo "- panel buttons it will show:"
    # Resolve ACTIONS against this script's text, the same substring rule
    # gui/app.py's _supported_actions uses.
    while IFS=$'\t' read -r key flags; do
      [ -n "$key" ] || continue
      ok=1
      for f in $flags; do
        [ "$f" = "__LOGIN__" ] && continue
        printf '%s' "$text" | grep -q -- "$f" || ok=0
      done
      [ "$ok" = "1" ] && echo "    - $key" || echo "    - ~~$key~~ (hidden)"
    done <<< "$(python3 -c "$actions_py")"
    echo
  done
fi

# ---------------------------------------------------------------------------
# 3. New flags upstream taught its apps, that this fork's surfaces do not know.
# ---------------------------------------------------------------------------
echo "## Flags new to the project"
echo
# A flag on an added line is not news if the project already has it somewhere -
# every app carries --start-date, --open-browser and friends. What matters is a
# flag this fork has never seen, because that is a capability nobody here has
# decided where to put yet.
added=$(git diff "$base..$UP" -- apps core | grep -E '^\+' | grep -oE '"--[a-z0-9][a-z0-9-]*"' | tr -d '"' | sort -u || true)
ours_text=$(git grep -h -oE -- '--[a-z0-9][a-z0-9-]*' HEAD -- apps core gui tools 2>/dev/null | sort -u || true)
panel=$(python3 -c "$actions_py" 2>/dev/null | cut -f2 | tr ' ' '\n' | sort -u)
sched=$(grep -oE '"--[a-z0-9-]+"' tools/schedule.py | tr -d '"' | sort -u)

any_new=0
for f in $added; do
  grep -qx -- "$f" <<< "$ours_text" && continue
  any_new=1
  where=""
  grep -qx -- "$f" <<< "$panel" && where="panel"
  grep -qx -- "$f" <<< "$sched" && where="${where:+$where, }scheduler"
  if [ -n "$where" ]; then
    echo "- \`$f\` — new, and already reachable from the $where."
  else
    echo "- \`$f\` — **new, and reachable from nothing here.** Decide where it belongs:"
    echo "  a panel action in \`ACTIONS\`, a scheduler flag in \`UNATTENDED_FLAGS\`, or"
    echo "  deliberately CLI-only (a one-off \`docker compose run\` is a real answer)."
  fi
done
[ "$any_new" = "0" ] && echo "None — every flag on an added line already exists somewhere in this tree."
echo

# ---------------------------------------------------------------------------
# 4. Fork surfaces, and whether this range reaches into them.
# ---------------------------------------------------------------------------
echo "## Fork surfaces touched"
echo
touched() { git diff --name-only "$base..$UP" -- "$@" | sed 's/^/  - `/;s/$/`/'; }
for pair in "gui/:control panel" "core/:shared core" "tools/:CLI tools incl. the scheduler" \
            "docs/:docs" "Dockerfile docker/ docker-compose.yml .env.example:container"; do
  paths="${pair%%:*}"; label="${pair#*:}"
  out=$(git diff --name-only "$base..$UP" -- $paths || true)
  if [ -n "$out" ]; then
    echo "- **$label** — upstream changed:"
    printf '%s\n' "$out" | sed 's/^/    - `/;s/$/`/'
  fi
done
echo
echo "(Anything not listed, upstream did not touch.)"

rm -f /tmp/ours.$$ /tmp/theirs.$$ /tmp/overlap.$$
