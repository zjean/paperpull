"""Telling a person that a scheduled run needs them.

This is the only place in PaperPull that makes an outbound call. Everything
else here reads: it attaches to a browser you signed in to yourself and takes
copies of your own documents. So the bar for this file is not "does it work"
but "can it ever hurt the thing it reports on", and the answer has to be no.

Three properties, and they are why this is a module rather than four lines
inside the scheduler:

* It never raises. Any failure - unreachable host, DNS, timeout, a refusal,
  a malformed URL - is logged and returns False. The caller is in the middle
  of a document run; a notification that could break one would be worse than
  no notification at all.
* It never blocks for long, and never retries. A missed message is strictly
  better than a scheduler wedged on a socket until someone notices.
* It does nothing when unconfigured, and says nothing about it. An install
  that never set PAPERPULL_NTFY_URL has not made a mistake.

WHAT A MESSAGE COSTS YOU

ntfy topics on ntfy.sh have no authentication: the topic name IS the
credential, and anyone who learns it can read everything sent to it. What
this project sends names the provider and the account label ("youfone/primary
- no signed-in tab"), which is enough to act on from a phone and enough to
tell a reader which providers you use. Pick an unguessable topic name, or run
your own server. PAPERPULL_NTFY_TOKEN is honoured for a protected topic but
is not required - see SECURITY.md.
"""
from __future__ import annotations

import logging
import os
import urllib.request
from typing import Iterable, Optional

log = logging.getLogger("paperpull_core.notify")

URL_VAR = "PAPERPULL_NTFY_URL"
TOKEN_VAR = "PAPERPULL_NTFY_TOKEN"

# Long enough for a slow phone-home, short enough that a black-holed host
# cannot hold up a scheduled pass. There is deliberately no retry.
TIMEOUT_SECONDS = 10


def _env(env):
    return os.environ if env is None else env


def configured(env=None) -> bool:
    """Has anyone asked to be notified?"""
    return bool((_env(env).get(URL_VAR) or "").strip())


def send(title: str, message: str, tags: Iterable[str] = (),
         priority: Optional[int] = None, *, env=None, opener=None) -> bool:
    """Post one message to the configured topic. True if it was accepted.

    `opener` stands in for urllib.request.urlopen so the tests can assert what
    would be sent without anything leaving the machine.
    """
    url = (_env(env).get(URL_VAR) or "").strip()
    if not url:
        return False

    headers = {"Title": title, "Content-Type": "text/plain; charset=utf-8"}
    tags = tuple(tags)
    if tags:
        headers["Tags"] = ",".join(tags)
    if priority is not None:
        headers["Priority"] = str(priority)
    token = (_env(env).get(TOKEN_VAR) or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(
        url, data=message.encode("utf-8"), headers=headers, method="POST")
    open_it = urllib.request.urlopen if opener is None else opener

    # Deliberately every exception, not a chosen few. urllib raises URLError,
    # HTTPError, socket.timeout, ssl.SSLError and UnicodeEncodeError (a
    # non-ASCII title is a header, and headers are latin-1) for causes that
    # all mean the same thing here: the message did not arrive, and the run
    # this is reporting on must carry on regardless.
    try:
        with open_it(request, timeout=TIMEOUT_SECONDS) as response:
            status = getattr(response, "status", 0) or 0
    except Exception as e:
        log.warning("could not notify: %s", e)
        return False

    if not 200 <= status < 300:
        log.warning("notification refused: HTTP %s", status)
        return False
    return True
