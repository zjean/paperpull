#!/bin/sh
# Bring up the control panel inside the container.
#
# Three jobs, in order: open the path to the browser's DevTools port, give
# every app a config and a place to write, then serve the panel.
set -eu

CDP_PORT="${PAPERPULL_CDP_PORT:-9222}"
BRIDGE="${PAPERPULL_BRIDGE:-browser:9223}"
CONFIG_ROOT="${PAPERPULL_CONFIG_ROOT:-/config}"
DATA_ROOT="${PAPERPULL_DATA_ROOT:-/data}"
APPS="${APPS_ROOT:-/app/apps}"
PORT="${PAPERPULL_PORT:-8765}"

log() { echo "[paperpull] $*"; }

# ---------------------------------------------------------------------------
# 1. The DevTools hop.
#
# Chrome binds --remote-debugging-port to 127.0.0.1 only (it ignores
# --remote-debugging-address), and it answers /json/version with "Host header
# is specified and is not an IP address or localhost" for anything else. So
# the chain is:
#
#     us -> localhost:9222 -> browser:9223 -> 127.0.0.1:9222
#            (this socat)     (the cdp-bridge sidecar, in the browser's
#                              network namespace, the only place that can
#                              reach that loopback port)
#
# socat is a byte pipe, so the Host header the apps send arrives unchanged —
# which is the point. Every app's cdp_url stays http://localhost:9222, exactly
# as it is on a native install.
# ---------------------------------------------------------------------------
log "bridging CDP: localhost:${CDP_PORT} -> ${BRIDGE}"
socat "TCP-LISTEN:${CDP_PORT},fork,reuseaddr" "TCP:${BRIDGE}" &
CDP_SOCAT=$!
trap 'kill "$CDP_SOCAT" 2>/dev/null || true' EXIT INT TERM

# ---------------------------------------------------------------------------
# 2. Configs and output folders.
#
# An app reads config.json from its own directory, which lives in the image
# and is therefore thrown away on every rebuild. So the real files live on the
# /config volume and get linked in. A first run seeds one from the app's
# tracked config.example.json with the three values that differ in a
# container; everything after that is yours to edit and is never overwritten.
# ---------------------------------------------------------------------------
# On Linux, Docker creates a missing bind-mount directory as root, and we run
# as uid 1000 — so a first `docker compose up` would die on the first mkdir
# below with nothing but "Permission denied" to go on. Say what to do instead.
# (macOS and Windows bind mounts are permissive, which is exactly why this is
# easy to miss until the first real server deploy.)
for d in "$CONFIG_ROOT" "$DATA_ROOT"; do
    if [ ! -w "$d" ]; then
        log "FATAL: $d is not writable by uid $(id -u)."
        log ""
        log "  Its bind mount was created by root. From the compose directory:"
        log ""
        log "    mkdir -p config data browser-profile"
        log "    sudo chown -R 1000:1000 config data browser-profile"
        log ""
        log "  1000 is not arbitrary: it has to match the browser container's"
        log "  PUID, or downloads arrive as zero-byte files. See docs/docker.md."
        exit 1
    fi
done

seeded=0
linked=0
for app_dir in "$APPS"/*; do
    [ -d "$app_dir" ] || continue
    app="$(basename "$app_dir")"

    # Same rule the panel uses to decide what is an app at all.
    has_entry=0
    for entry in "$app_dir"/*_docs.py "$app_dir"/*_receipts.py; do
        [ -f "$entry" ] && has_entry=1 && break
    done
    [ "$has_entry" = "1" ] || continue

    mkdir -p "$CONFIG_ROOT/$app" "$DATA_ROOT/$app"

    if [ ! -f "$CONFIG_ROOT/$app/config.json" ] && [ -f "$app_dir/config.example.json" ]; then
        APP="$app" SRC="$app_dir/config.example.json" \
        DST="$CONFIG_ROOT/$app/config.json" DATA="$DATA_ROOT/$app" \
        CDP="http://localhost:${CDP_PORT}" python3 - <<'PY'
import json, os
cfg = json.load(open(os.environ["SRC"], encoding="utf-8"))
cfg["output_dir"] = os.environ["DATA"]          # PDFs *and* the state files
cfg["cdp_url"] = os.environ["CDP"]              # the shared browser
# Unused while attached over CDP (nothing here launches a browser), but keep
# it on the volume rather than pointing at a path inside the image.
cfg["profile_dir"] = f"/config/{os.environ['APP']}/browser-profile"
with open(os.environ["DST"], "w", encoding="utf-8") as fh:
    json.dump(cfg, fh, indent=2)
    fh.write("\n")
PY
        seeded=$((seeded + 1))
    fi

    # Link every config this app has on the volume, so `--config
    # config.spouse.json` works by dropping that file into /config/<app>/.
    for cfg in "$CONFIG_ROOT/$app"/config*.json; do
        [ -f "$cfg" ] || continue
        ln -sf "$cfg" "$app_dir/$(basename "$cfg")"
        linked=$((linked + 1))
    done
done
log "configs: seeded ${seeded}, linked ${linked}"

# A sanity check worth its two lines: a 0700 artifact dir owned by the wrong
# uid is how cross-container downloads silently become zero-byte files.
if [ ! -w "${TMPDIR:-/tmp}" ]; then
    log "WARNING: TMPDIR ${TMPDIR:-/tmp} is not writable — downloads will fail."
    log "         It must be a volume shared with the browser container, and"
    log "         both containers must run as the same uid (1000)."
fi

# ---------------------------------------------------------------------------
# 3. Whatever we were asked to do.
#
# A command passed to `docker run` / `docker compose run` wins, and gets the
# bridge and the linked configs set up above — which is what makes a one-off
#     docker compose run --rm paperpull python apps/ally/ally_docs.py --discover
# behave exactly like the same action pressed in the panel. With no command,
# serve the panel.
# ---------------------------------------------------------------------------
if [ "$#" -gt 0 ]; then
    log "running: $*"
    cd /app
    exec "$@"
fi

log "serving the control panel on 0.0.0.0:${PORT}"
cd /app/gui
exec python3 -m uvicorn app:app --host 0.0.0.0 --port "$PORT" --no-access-log
