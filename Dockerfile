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

# uid 1000 matches linuxserver/chrome's `abc` user. That is not cosmetic:
# Playwright's driver creates its download-artifact directory mode 0700 under
# TMPDIR, and Chrome — running as that other container's user — has to be able
# to write into it. Mismatched uids silently produce zero-byte PDFs.
RUN useradd --uid 1000 --create-home --shell /bin/bash paperpull

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
 && chown -R 1000:1000 /app/apps \
 # The volume mountpoints. They have to exist and be ours before we drop to
 # uid 1000: an unmounted /config or /data would otherwise be an unwritable
 # root-owned directory, and the entrypoint could not seed a single config.
 && mkdir -p /config /data /pwtmp \
 && chown 1000:1000 /config /data /pwtmp

ENV APPS_ROOT=/app/apps \
    PAPERPULL_REMOTE_BROWSER=1 \
    PAPERPULL_CDP_PORT=9222 \
    PAPERPULL_BRIDGE=browser:9223 \
    PAPERPULL_CONFIG_ROOT=/config \
    PAPERPULL_DATA_ROOT=/data \
    PAPERPULL_PORT=8765 \
    TMPDIR=/pwtmp \
    HOME=/home/paperpull

USER 1000:1000
WORKDIR /app/gui
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PAPERPULL_PORT}/api/apps" >/dev/null || exit 1

ENTRYPOINT ["/usr/local/bin/paperpull-entrypoint"]
