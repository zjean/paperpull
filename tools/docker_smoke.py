"""Does this container stack actually work? Run it from inside `paperpull`.

    docker compose exec paperpull python /app/tools/docker_smoke.py

Four checks, in the order things break:

1. The panel answers, and knows it is driving a remote browser.
2. The DevTools hop chain reaches Chrome (us -> localhost -> bridge -> Chrome).
3. Playwright can attach to it, and a page can navigate.
4. A download crosses the container boundary with its bytes intact.

Check 4 is the one that matters. Attaching over CDP gives Playwright no way to
stream a download from the browser's filesystem to ours, so it copies from a
local path that only exists if TMPDIR is a volume both containers share and
both run as the same uid. Get that wrong and `download.save_as()` reports
success while writing a zero-byte file - every statement would appear to
download and every PDF would be empty. So this serves a known-size PDF from a
throwaway HTTP server, has Chrome download it, and weighs the result.

Exits non-zero on the first failure, so CI and the merge-upstream skill can
use it as a gate.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from socketserver import TCPServer

CDP_URL = f"http://localhost:{os.environ.get('PAPERPULL_CDP_PORT', '9222')}"
PANEL = f"http://127.0.0.1:{os.environ.get('PAPERPULL_PORT', '8765')}"
SERVE_PORT = 8099

# A tiny but structurally valid PDF, so the size assertion means something.
PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
       b"2 0 obj<</Type/Pages/Count 0/Kids[]>>endobj\n"
       b"trailer<</Root 1 0 R>>\n%%EOF\n")

failures: list[str] = []


def check(label: str, fn) -> object:
    try:
        detail = fn()
    except Exception as e:  # noqa: BLE001 - a smoke test reports, never raises
        print(f"  FAIL  {label}\n          {type(e).__name__}: {e}")
        failures.append(label)
        return None
    note = detail if isinstance(detail, str) else ""
    print(f"  ok    {label}" + (f" — {note}" if note else ""))
    return detail


# -- 1. the panel ----------------------------------------------------------


def panel_reports_remote_browser() -> str:
    with urllib.request.urlopen(f"{PANEL}/api/apps", timeout=10) as r:
        meta = json.load(r)
    if not meta["remote_browser"]:
        raise AssertionError(
            "PAPERPULL_REMOTE_BROWSER is not set, so the panel's Login button "
            "would try to launch a browser in this container")
    if not meta["apps"]:
        raise AssertionError(f"no apps found under {meta['apps_root']}")
    return f"{len(meta['apps'])} apps, login flag {meta['apps']['ally']['login_flag']}"


# -- 2. the DevTools hop ---------------------------------------------------


def cdp_chain_reaches_chrome() -> str:
    with urllib.request.urlopen(f"{CDP_URL}/json/version", timeout=10) as r:
        v = json.load(r)
    return v["Browser"]


# -- 3 and 4. Playwright, and the bytes ------------------------------------


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's name
        if self.path == "/f.pdf":
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            # Chrome only fires a download event for an attachment; a plain PDF
            # opens in its viewer instead.
            self.send_header("Content-Disposition",
                             'attachment; filename="smoke-statement.pdf"')
            self.send_header("Content-Length", str(len(PDF)))
            self.end_headers()
            self.wfile.write(PDF)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        # Same-origin, because Chrome ignores a cross-origin download attribute.
        self.wfile.write(b"<a id='dl' href='/f.pdf' download>statement</a>")

    def log_message(self, *_a):
        pass


def run() -> int:
    print(f"paperpull docker smoke test\n  panel {PANEL}\n  cdp   {CDP_URL}\n")
    check("panel is up and driving a remote browser", panel_reports_remote_browser)
    check("CDP hop chain reaches Chrome", cdp_chain_reaches_chrome)

    if failures:
        print("\nStopping: nothing downstream can pass while the above fails.")
        return 1

    # allow_reuse_address has to be set before the bind, so it belongs on the
    # class - assigning it to the instance happens after __init__ has already
    # bound, and a back-to-back second run then fails on a socket still in
    # TIME_WAIT.
    class _Server(TCPServer):
        allow_reuse_address = True

    server = _Server(("0.0.0.0", SERVE_PORT), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    # Chrome is in the other container, so it cannot reach our localhost.
    # It resolves us by service name on the shared compose network.
    host = os.environ.get("PAPERPULL_SMOKE_HOST") or socket.gethostname()
    origin = f"http://{host}:{SERVE_PORT}"
    print(f"  ..    Chrome will fetch the test PDF from {origin}")

    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    page = None
    try:
        browser = check("Playwright attaches over CDP",
                        lambda: pw.chromium.connect_over_cdp(CDP_URL))
        if browser is None:
            return 1
        if not browser.contexts:
            print("  FAIL  browser has a context to work in")
            failures.append("no context")
            return 1
        ctx = browser.contexts[0]
        page = ctx.new_page()
        check("a page can navigate", lambda: page.goto(
            origin, wait_until="domcontentloaded", timeout=30000) and page.url)

        def download_keeps_its_bytes() -> str:
            out = Path(os.environ.get("TMPDIR", "/tmp")) / "smoke-out.pdf"
            out.unlink(missing_ok=True)
            with page.expect_download(timeout=30000) as dl:
                page.click("#dl")
            dl.value.save_as(out)
            size = out.stat().st_size
            out.unlink(missing_ok=True)
            if size != len(PDF):
                raise AssertionError(
                    f"got {size} bytes, expected {len(PDF)}. "
                    "TMPDIR must be a volume shared with the browser "
                    "container, and both containers must run as uid 1000.")
            return f"{size} bytes intact across the container boundary"

        check("a download crosses containers with its bytes",
              download_keeps_its_bytes)
    finally:
        if page is not None:
            try:
                page.close()
            except Exception:
                pass
        pw.stop()
        server.shutdown()

    print()
    if failures:
        print(f"FAILED: {len(failures)} check(s) — {', '.join(failures)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
