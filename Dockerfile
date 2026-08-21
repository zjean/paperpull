# PaperPull — the control panel and the downloader apps.
#
# Deliberately NOT a browser image. The sign-in browser lives in its own
# container (lscr.io/linuxserver/chrome), because a human has to sign in to it
# and a Selkies desktop is the only sane way to offer that over a network.
# This image only attaches to it over the DevTools protocol.
#
# See docs/docker.md for the four Chrome/Playwright gotchas that shape this
# file and docker-compose.yml.
FROM python:3.12-slim

# socat bridges us to Chrome's DevTools port. Chrome binds that port to
# 127.0.0.1 inside the browser container and refuses any Host header that is
# not localhost or a bare IP, so a plain TCP pipe on each end is what keeps
# every app's `cdp_url` at the upstream default of http://localhost:9222.
# curl is here for the container healthcheck.
RUN apt-get update \
 && apt-get install -y --no-install-recommends socat curl \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# No user is created and no uid is baked in. Which uid this runs as is the
# deployment's choice (PUID/PGID in .env, applied to both containers), so that
# files on the host volumes belong to the person who has to read them.
#
# It only has to MATCH the browser container: Playwright's driver creates its
# download-artifact directory mode 0700 under TMPDIR, and Chrome — running as
# the other container's user — must be able to write into it. Mismatched uids
# silently produce zero-byte PDFs. Nothing in this image needs a passwd entry.

WORKDIR /app

# Dependencies before source, so editing an app does not rebuild this layer.
COPY core/ /app/core/
COPY gui/requirements.txt /app/gui/requirements.txt
RUN pip install -r /app/gui/requirements.txt \
 && pip install /app/core \
 && pip install "playwright>=1.44" "pypdf[crypto]>=4.2" "pytest>=8.0"

# No `playwright install`. connect_over_cdp needs the driver, not a browser
# binary — that is the difference between a ~450MB image and a ~1.5GB one.

COPY apps/ /app/apps/
COPY gui/ /app/gui/
COPY tools/ /app/tools/
COPY VERSION /app/VERSION
COPY docker/entrypoint.sh /usr/local/bin/paperpull-entrypoint
RUN chmod +x /usr/local/bin/paperpull-entrypoint \
 # The volume mountpoints have to exist before anything is mounted over them.
 # World-writable so that any uid works when a named volume takes its
 # permissions from here; a bind mount brings the host's own, which is the
 # point of PUID.
 && mkdir -p /config /data /pwtmp \
 && chmod 1777 /config /data /pwtmp

ENV APPS_ROOT=/app/apps \
    PAPERPULL_REMOTE_BROWSER=1 \
    PAPERPULL_CDP_PORT=9222 \
    PAPERPULL_BRIDGE=browser:9223 \
    PAPERPULL_CONFIG_ROOT=/config \
    PAPERPULL_DATA_ROOT=/data \
    PAPERPULL_PORT=8765 \
    TMPDIR=/pwtmp \
    # Any uid can write here, which a baked-in home directory could not
    # promise once the uid became a deployment choice. Nothing needs to
    # persist in it — Playwright's artifacts go to TMPDIR.
    HOME=/tmp

# A default, not a requirement: docker-compose.yml overrides it from PUID/PGID.
USER 1000:1000
WORKDIR /app/gui
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PAPERPULL_PORT}/api/apps" >/dev/null || exit 1

ENTRYPOINT ["/usr/local/bin/paperpull-entrypoint"]
