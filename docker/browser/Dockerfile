# The sign-in browser: linuxserver/chrome plus a way in to its DevTools port.
#
# Chrome binds --remote-debugging-port to 127.0.0.1 and ignores
# --remote-debugging-address, so that port is reachable only from inside this
# container. Something in here has to forward it, and socat is the smallest
# thing that can.
#
# It used to be a separate container sharing this one's network namespace,
# which worked but left an operational trap: restarting the browser stranded
# the sidecar in a namespace that no longer existed, so the two always had to
# be restarted together. Moving it in here removes the trap — s6 supervises
# it, so it starts, stops and restarts exactly when the browser does.
FROM lscr.io/linuxserver/chrome:latest

RUN apt-get update \
 && apt-get install -y --no-install-recommends socat \
 && rm -rf /var/lib/apt/lists/*

# An s6 longrun, not a /custom-cont-init.d script: init scripts are expected to
# run to completion, and a process backgrounded from one is unsupervised. As a
# service it gets restarted if it ever dies.
COPY s6/svc-cdp-bridge/ /etc/s6-overlay/s6-rc.d/svc-cdp-bridge/
# An empty file here is how s6-rc is told to bring the service up with the rest.
COPY s6-enable/ /etc/s6-overlay/s6-rc.d/user/contents.d/

# Runs during init, before the desktop starts Chrome. See the script for why a
# persisted Chrome profile otherwise stops opening after a container recreate.
COPY custom-cont-init.d/ /custom-cont-init.d/
RUN chmod +x /custom-cont-init.d/*

EXPOSE 3000 3001 9223
