"""ALL Youfone URLs, page structure and behaviour live here.

When Youfone changes its site, repair this file only.

WHAT MAKES YOUFONE DIFFERENT FROM THE OTHER PROVIDERS HERE
----------------------------------------------------------
1. **The API cannot be called; the page has to be used.** Behind the Angular
   SPA sits a JSON API (`/api/prov/...`), and its invoice endpoints look
   invitingly simple:

       POST /api/prov/Pdf/GetInvoice        {"customerId": N,
       POST /api/prov/Pdf/GetSpecification   "invoiceNumber": "..."}
           -> {"fileName": "invoice<number>", "content": "<base64 PDF>"}

   They are not callable from outside the page. Every `/api/prov` request the
   SPA makes carries a `securitykey` header it computes per request (a 344-char
   blob the server echoes back), and a request without a valid one is refused
   by nginx with a bare **403** before the application ever sees it — with the
   session cookies attached, and with the `_token` from `sessionStorage` as a
   bearer token, both were tried and both got the 403.

   So this app does what a person does: it clicks the row's own **Factuur** or
   **Specificaties** button and reads the JSON that comes back. That is also
   why `download_document` takes a handle rather than a URL — there is no URL
   to fetch, only a row to press.

   The `securitykey` is never read, logged, stored, or reconstructed. This app
   would not benefit from it: forging one would mean shipping Youfone's own
   crypto, which is exactly the kind of thing a read-only tool should not do.

2. **Two documents per month, not one.** Each invoice row offers the invoice
   itself (*Factuur*) and its usage breakdown (*Specificaties*), from two
   endpoints of the same shape. Both are collected; they land side by side as
   `<date> Factuur.pdf` and `<date> Specificatie.pdf`.

3. **The session lives in the tab, so this app never opens one.** MyYoufone
   keeps its token in that tab's `sessionStorage` (`_token`, encrypted with
   CryptoJS — the same `U2FsdGVkX1...` shape as `msisdnForInvoice`). A new tab
   starts without it. Navigation is therefore limited to what a person's click
   would do: the SPA's own router links and the back button, both of which
   were verified to keep the session. There is no `page.goto` and no
   `new_page` anywhere in this file.

4. **One invoice list per subscription.** `/facturen` is a redirect: it asks
   `Invoice/GetSimOnlyOptions` which subscriptions exist and lands on the first
   one's tab (`/facturen/thuis` for internet/TV). An account with a SIM-only
   line as well has more than one tab, so `collect_documents` walks every tab
   in the strip rather than only the one the user happened to leave open.

   **Only the *Thuis* tab has been seen on real hardware.** The tab strip is
   read as router links (`a[href^="/facturen/"]`), which is what Thuis renders;
   if Youfone renders a mobile line as a Material tab *button* instead, the
   strip will come back with one entry and the run will quietly cover only that
   subscription. So the tab list is logged on every discovery — an account with
   two subscriptions that reports one tab is this paragraph, not a clean run.

5. **Clicking a download button navigates.** Pressing *Factuur* both fetches
   the PDF and routes the SPA to a viewer page (`/facturen/i/<...>`;
   *Specificaties* goes to `/facturen/s/<...>`). The trailing segment of those
   routes changes on every click, so they are never treated as document URLs.
   After each download the app steps back to the list with the back button.

6. **The page offers real damage, in Dutch, next to the downloads.** Under
   "Direct regelen" sit *Rekeningnummer wijzigen* (change the bank account),
   *Incassomoment aanpassen* (move the direct-debit date), *BTW factuur
   aanvragen* and *Oudere facturen opvragen*. The last two are the reason
   `opvragen|aanvragen` is in the blocklist: both contain the word "factuur",
   so the document allowlist alone would have waved them through, and both
   raise a request with Youfone's back office rather than reading anything.

7. **Dutch dates, written out.** A row reads "14 augustus 2026". Cells are read
   with `text_content()` rather than `inner_text()` on purpose: the invoice
   number cell carries the class `hide-mobile`, so in a narrow window the
   rendered text is empty while the DOM text is not.

WHAT IS FETCHED
---------------
Every invoice Youfone still serves, as two PDFs each: the *Factuur* and its
*Specificaties*. Youfone issues no tax forms.

**History stops at six months, and there is nothing to click for more.** The
page says so itself ("al je facturen van de laatste 6 maanden"), the table
renders every row it has — no pagination, no "load more", no year filter — and
older invoices exist only behind *Oudere facturen opvragen*, which opens a
request with Youfone rather than serving a document. It is on the blocklist and
is never pressed. Nothing here caps anything: if Youfone shows it, this fetches
it.
"""
from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

from paperpull_core import controls

log = logging.getLogger("youfone_docs.site")

BASE = "https://my.youfone.nl"
HOST = "my.youfone.nl"
# The one host this app may ever request. A set, and named this, because the
# repo-wide guard test in core/tests reads it to build the hostile URLs it
# expects every app to refuse — a provider that keeps its allowed host to
# itself is one the test can only check the host-independent shapes against.
ALLOWED_HOSTS = {HOST}

URLS = {
    "home": f"{BASE}/",
    "documents": f"{BASE}/facturen",
}

# The route that lists the subscriptions, and the two routes a download click
# lands on afterwards. The detail routes end in an id that changes every click,
# so they are recognised in order to be ignored - never followed, never stored.
DOCUMENTS_ROUTE = "/facturen"
DETAIL_ROUTE_RE = re.compile(r"^/facturen/[is]/", re.I)

# The two document kinds a row offers. Each is: the cell its button sits in,
# the button's label, the endpoint the click calls, and the word this project
# files it under (`document_rules.json` matches on that word).
INVOICE = "invoice"
SPECIFICATION = "specification"

KINDS: Dict[str, Dict[str, str]] = {
    INVOICE: {
        "cell": "td.mat-column-invoice",
        "label": "Factuur",
        "endpoint": "/api/prov/Pdf/GetInvoice",
        "summary": "Factuur",
    },
    SPECIFICATION: {
        "cell": "td.mat-column-specification",
        "label": "Specificaties",
        "endpoint": "/api/prov/Pdf/GetSpecification",
        "summary": "Specificatie",
    },
}

# The only two endpoints this tool has any business reading. Their siblings
# behind /api/prov include Payment/*, authentication/login and the account
# mutations - none of which is ever requested or listened for.
ALLOWED_ENDPOINTS = frozenset(k["endpoint"] for k in KINDS.values())

# The invoice table, as Angular Material renders it.
ROW_SEL = "table tr.mat-mdc-row"
DATE_CELL = "td.mat-column-date"
NUMBER_CELL = "td.mat-column-number"

LOGIN_URL_MARKERS = ["/inloggen", "/login", "/auth/", "returnurl"]

SECURITY_CHALLENGE_MARKERS = [
    "verifieer", "verificatiecode", "controlecode", "tweestapsverificatie",
    "vul de code in", "we hebben een code", "sms-code",
    "verify your identity", "one-time passcode", "two-factor",
]


# ---------------------------------------------------------------------------
# The read-only guard
# ---------------------------------------------------------------------------

# Controls that must NEVER be activated. A telecom portal can pay a bill, buy
# a bundle, swap or block a SIM, extend or cancel the contract, and change
# where the money comes from. Two entries are specific to this page and were
# added after reading it: `opvragen|aanvragen`, because "Oudere facturen
# opvragen" and "BTW factuur aanvragen" both contain "factuur" and would
# otherwise clear the document allowlist while actually filing a request with
# Youfone; and `incassomoment`, which moves the direct-debit date.
# "Uitloggen" is in here too: signing out mid-run is destructive on this site,
# because the session cannot be recreated without the user.
FORBIDDEN_CONTROL_RE = re.compile(
    r"(betaal|betalen|betaling|ideal|overboek|"
    r"incasso|incassomoment|machtiging|rekeningnummer|iban|"
    r"opvraag|opvragen|aanvraag|aanvragen|verzoek|"
    r"opzeg|be[eë]indig|wijzig|aanpassen|verander|"
    r"verleng|upgrade|downgrade|overstap|"
    r"bijkoop|bijkopen|kopen|koop|bestel|besteld|aanschaf|"
    r"opwaarder|tegoed|bundel|extra\s+data|"
    r"simkaart|sim\s+vervang|blokkeer|deblokkeer|activeer|deactiveer|"
    r"verwijder|annuleer|intrek|"
    r"wachtwoord|inloggegevens|persoonlijke\s+gegevens|"
    r"uitloggen|afmelden|"
    r"pay|transfer|cancel|delete|remove|submit|order|buy|upgrade|request)", re.I)

SAFE_DOC_CONTROL_RE = re.compile(
    r"(download|factuur|facturen|specificatie|specificaties|"
    r"verbruiksoverzicht|betalingsoverzicht|pdf|print|opslaan\s+als\s+pdf)",
    re.I)


def is_safe_control(name: str) -> bool:
    """Deny by default: clear the blocklist AND match the document allowlist.

    Both halves matter, and on this page both halves are load-bearing.
    "Oudere facturen opvragen" is on the document allowlist by virtue of the
    word facturen, and the blocklist is what refuses it.

    The blocklist is `paperpull_core.controls`' shared vocabulary *plus* the
    Dutch one above, not the Dutch one alone. The blocklist here is in Dutch
    because Youfone's pages are, and a control that says "Sign in to download
    your PDF" would otherwise clear both halves of the guard - an English
    sign-in prompt with the word "download" in it. Which is very close to the
    mistake `controls` exists to record: two apps refused every money-movement
    control and both went on to touch a control inside a sign-in form, because
    refusing *that* had not occurred to either.
    """
    name = (name or "").strip()
    if not name:
        return False
    if controls.is_forbidden_context(name, FORBIDDEN_CONTROL_RE):
        return False
    return bool(SAFE_DOC_CONTROL_RE.search(name))


def is_safe_route(href: str) -> bool:
    """Is this a route this app is willing to open by clicking its link?

    Same-site invoice routes only, and never a document *detail* route: the
    trailing id in `/facturen/i/<...>` is minted per click, so following one is
    following a URL we were handed rather than a place we meant to go.

    Absolute URLs are rejected outright, including ones on Youfone's own host.
    The tab strip uses relative router links; an absolute href is a full page
    load, which reboots the SPA, and the header's own "Facturen" link is
    exactly that (it carries Google's `_gl` tracking parameter).
    """
    href = (href or "").strip()
    if not href.startswith("/"):
        return False
    if "//" in href or "\\" in href:
        return False
    path = href.split("?", 1)[0].split("#", 1)[0]
    if DETAIL_ROUTE_RE.match(path):
        return False
    return path == DOCUMENTS_ROUTE or path.startswith(DOCUMENTS_ROUTE + "/")


def is_safe_url(url: str) -> bool:
    """Is this a URL this app is willing to request at all?

    Host only, deliberately: what may be *fetched* and what a document
    response may *be* are two different questions, and `is_safe_pdf_url`
    below answers the second by adding the endpoint rule to this one.

    Compared by parsed host, never by prefix — `https://my.youfone.nl.evil.test/`
    and `https://my.youfone.nl@evil.test/` both start with the right address
    while pointing somewhere else. Credentials in a URL are never legitimate
    here and are the classic way to disguise the real host; a port other than
    443 on an https URL is not something this portal ever serves.
    """
    try:
        got = urlparse(url or "")
    except ValueError:
        return False
    if got.scheme != "https" or not got.hostname:
        return False
    if got.hostname.lower() not in ALLOWED_HOSTS:
        return False
    if got.port not in (None, 443):
        return False
    if got.username or got.password:
        return False
    return True


def is_safe_pdf_url(url: str) -> bool:
    """Is this the response of one of the two document endpoints?

    Used to check what a click actually called, so an unexpected answer is
    reported instead of written to disk as if it were an invoice.
    """
    if not is_safe_url(url):
        return False
    try:
        path = urlparse(url).path or ""
    except ValueError:
        return False
    return path in ALLOWED_ENDPOINTS


# ---------------------------------------------------------------------------
# Document handles
# ---------------------------------------------------------------------------
# A document here has no URL - it is a button in a row on a tab. The
# orchestrator stores one opaque string per document ("source_url"), so that
# string carries the three facts a click needs.

HANDLE_SEP = "|"


def make_handle(kind: str, tab: str, invoice_number: str) -> str:
    return f"{kind}{HANDLE_SEP}{tab}{HANDLE_SEP}{invoice_number}"


def parse_handle(handle: str) -> Optional[Tuple[str, str, str]]:
    """(kind, tab, invoice_number) - or None if it is not one of ours."""
    parts = (handle or "").split(HANDLE_SEP)
    if len(parts) != 3:
        return None
    kind, tab, number = (p.strip() for p in parts)
    if kind not in KINDS:
        return None
    if not is_safe_route(tab) or tab.rstrip("/") == DOCUMENTS_ROUTE:
        return None
    if not number.isdigit():
        return None
    return kind, tab, number


# ---------------------------------------------------------------------------
# Session detection
# ---------------------------------------------------------------------------

def find_signed_in_page(context):
    """The tab that is already signed in to MyYoufone.

    Returns None rather than opening one. Youfone's token lives in the tab's
    own `sessionStorage`, so a fresh tab is signed out no matter what the rest
    of the browser knows - only the user can fix that.
    """
    live = [p for p in context.pages if not p.is_closed()]
    for page in live:
        if HOST in (page.url or ""):
            return page
    return None


def no_page_help() -> str:
    return (
        "No MyYoufone tab is open in that browser.\n"
        "Run login.command, sign in at https://my.youfone.nl/facturen, and\n"
        "leave that tab open. Keep it to one tab: Youfone keeps its session in\n"
        "the tab it was opened in, so a second tab starts signed out.")


def looks_signed_out(page) -> bool:
    """Has the session visibly gone?

    Unlike Simyo there is no server-side question to ask: `/auth/is-logged-in`
    has no counterpart here, and every `/api/prov` call needs the `securitykey`
    header this app deliberately cannot produce. So the evidence is what the
    SPA shows - it redirects to `/inloggen` when its token is refused - plus a
    sign-in form on the page.

    That makes this an *optimistic* check: a session already dead on the server
    can still look fine until the next click. The other half of the answer is
    `download_document`, which reports a click that did not return a PDF
    instead of writing one, so `--resume` can pick the run up after a fresh
    sign-in.
    """
    url = (page.url or "").lower()
    if any(m in url for m in LOGIN_URL_MARKERS):
        return True
    try:
        return page.locator("input[type=password]").first.is_visible(timeout=1500)
    except Exception:
        return False


def detect_security_challenge(page) -> Optional[str]:
    try:
        body = page.locator("body").inner_text(timeout=5000).lower()
    except Exception:
        return None
    for marker in SECURITY_CHALLENGE_MARKERS:
        if marker in body:
            return f"Sign-in verification step detected: '{marker}'"
    return None


# ---------------------------------------------------------------------------
# Getting to the invoices - by clicking, never by navigating
# ---------------------------------------------------------------------------

def _route_of(page) -> str:
    try:
        return urlparse(page.url or "").path or ""
    except ValueError:
        return ""


def _list_is_showing(page) -> bool:
    try:
        return page.locator(ROW_SEL).first.is_visible(timeout=2000)
    except Exception:
        return False


def _click_route(page, href: str, timeout: int = 15000) -> bool:
    """Click the SPA's own router link for `href`. No navigation, no new tab."""
    if not is_safe_route(href):
        log.error("refusing to open route %r", href)
        return False
    link = page.locator(f'a[href="{href}"]').first
    try:
        link.click(timeout=timeout)
    except Exception as e:
        log.warning("could not click the link to %s (%s)", href, type(e).__name__)
        return False
    try:
        page.wait_for_selector(ROW_SEL, timeout=timeout)
    except Exception:
        # /facturen redirects to the first subscription; a tab with no invoices
        # yet renders no table at all, which is not an error.
        log.info("no invoice rows on %s", page.url)
    return True


def goto_documents(page) -> bool:
    """Reach the invoices area. Named for the interface every app expects.

    Deliberately not a navigation: it clicks the SPA's own link, because a
    `page.goto` would reboot the app. Returns True when an invoice list is
    reachable.
    """
    if looks_signed_out(page):
        return False
    if _list_is_showing(page):
        return True
    for href in (DOCUMENTS_ROUTE, *subscription_tabs(page)):
        if _click_route(page, href) and _list_is_showing(page):
            return True
    return _list_is_showing(page)


def subscription_tabs(page) -> List[str]:
    """The invoice tab of each subscription, in the order the page lists them.

    One per subscription (`/facturen/thuis` for internet/TV, a SIM-only line
    gets its own). See point 4 of the module docstring for what this cannot
    see.
    """
    try:
        hrefs = page.eval_on_selector_all(
            'a[href^="/facturen"]',
            "els => els.map(e => e.getAttribute('href'))") or []
    except Exception:
        hrefs = []

    tabs: List[str] = []
    for href in hrefs:
        href = (href or "").split("?", 1)[0]
        if not is_safe_route(href):
            continue
        if href.rstrip("/") == DOCUMENTS_ROUTE:   # the redirect, not a tab
            continue
        if href not in tabs:
            tabs.append(href)

    here = _route_of(page)
    if not tabs and is_safe_route(here) and here.rstrip("/") != DOCUMENTS_ROUTE:
        tabs = [here]
    return tabs


def _open_tab(page, tab: str) -> bool:
    if _route_of(page) == tab and _list_is_showing(page):
        return True
    return _click_route(page, tab) and _list_is_showing(page)


# ---------------------------------------------------------------------------
# Dutch dates
# ---------------------------------------------------------------------------

_MONTHS = {
    "januari": 1, "februari": 2, "maart": 3, "april": 4, "mei": 5, "juni": 6,
    "juli": 7, "augustus": 8, "september": 9, "oktober": 10, "november": 11,
    "december": 12,
    "jan": 1, "feb": 2, "mrt": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "okt": 10, "nov": 11, "dec": 12,
}

_DAY_MONTH_YEAR_RE = re.compile(
    r"\b(\d{1,2})\s+([a-z]+)\.?\s+(\d{4})\b", re.I)
_MONTH_YEAR_RE = re.compile(r"\b([a-z]+)\.?\s+(\d{4})\b", re.I)
# Dutch numeric dates are day-first: 08-07-2026 is 8 July.
_NUMERIC_RE = re.compile(r"\b(\d{1,2})[-/](\d{1,2})[-/](\d{4})\b")
_ISO_RE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")


def parse_date(text: str) -> Optional[str]:
    """Parse a date out of Dutch page text -> YYYY-MM-DD.

    Youfone writes its months out ("14 augustus 2026"), which is what the
    table gives this function. A bare month and year becomes the first of that
    month.
    """
    text = (text or "").strip()
    if not text:
        return None

    m = _ISO_RE.search(text)
    if m:
        return m.group(0)

    m = _DAY_MONTH_YEAR_RE.search(text)
    if m:
        month = _MONTHS.get(m.group(2).lower())
        if month:
            return _iso(int(m.group(3)), month, int(m.group(1)))

    m = _NUMERIC_RE.search(text)
    if m:
        return _iso(int(m.group(3)), int(m.group(2)), int(m.group(1)))

    m = _MONTH_YEAR_RE.search(text)
    if m:
        month = _MONTHS.get(m.group(1).lower())
        if month:
            return _iso(int(m.group(2)), month, 1)

    return None


def _iso(year: int, month: int, day: int) -> Optional[str]:
    try:
        return datetime(year, month, day).strftime("%Y-%m-%d")
    except ValueError:
        return None


def parse_period_date(text: str):
    """(date, period) for the orchestrator's period column."""
    iso = parse_date(text)
    return (iso or "", "")


# ---------------------------------------------------------------------------
# Reading the table
# ---------------------------------------------------------------------------

@dataclass
class RawDoc:
    """One downloadable document, in the shape the orchestrator records."""
    title: str
    date_text: str
    pdf_url: str          # a handle, not a URL - see make_handle()
    invoice_number: str = ""
    kind: str = ""
    tab: str = ""


def _cell_text(row, selector: str) -> str:
    """The cell's text from the DOM, not from the rendering.

    `text_content()` rather than `inner_text()`: the invoice-number cell is
    `hide-mobile`, so a narrow browser window renders it to nothing while the
    text is still there to read.
    """
    try:
        cell = row.locator(selector).first
        if cell.count() == 0:
            return ""
        return re.sub(r"\s+", " ", (cell.text_content(timeout=4000) or "")).strip()
    except Exception:
        return ""


def _row_has_kind(row, kind: str) -> bool:
    try:
        return row.locator(f"{KINDS[kind]['cell']} button").count() > 0
    except Exception:
        return False


def read_rows(page, tab: str = "") -> List[dict]:
    """The invoice rows of the tab currently showing, as plain dicts.

    Amounts are deliberately not read. They are on the page and on the invoice
    itself, and this project's index records no money.
    """
    rows: List[dict] = []
    loc = page.locator(ROW_SEL)
    try:
        count = loc.count()
    except Exception:
        return rows
    for i in range(count):
        row = loc.nth(i)
        date_text = _cell_text(row, DATE_CELL)
        number = re.sub(r"\D", "", _cell_text(row, NUMBER_CELL))
        if not number:
            log.warning("invoice row %d on %s has no invoice number - skipped",
                        i, tab or page.url)
            continue
        rows.append({
            "invoice_number": number,
            "date_text": date_text,
            "date": parse_date(date_text) or "",
            "kinds": [k for k in KINDS if _row_has_kind(row, k)],
            "tab": tab or _route_of(page),
        })
    return rows


def collect_documents(page) -> List[RawDoc]:
    """Every downloadable document across every subscription, newest first."""
    tabs = subscription_tabs(page)
    # Logged on purpose: an account with two subscriptions that reports one tab
    # is point 4 of the module docstring, not a clean run.
    log.info("Youfone invoice tabs found: %s", tabs or "(none)")

    docs: List[RawDoc] = []
    for tab in tabs:
        if not _open_tab(page, tab):
            log.warning("could not open the invoice tab %s - skipped", tab)
            continue
        rows = read_rows(page, tab)
        log.info("%s: %d invoice row(s)", tab, len(rows))
        for row in rows:
            for kind in row["kinds"]:
                number = row["invoice_number"]
                docs.append(RawDoc(
                    title=f"{KINDS[kind]['summary']} {number}",
                    date_text=row["date"],
                    pdf_url=make_handle(kind, row["tab"], number),
                    invoice_number=number,
                    kind=kind,
                    tab=row["tab"]))

    docs.sort(key=lambda d: (d.date_text or "", d.invoice_number, d.kind),
              reverse=True)
    return docs


# ---------------------------------------------------------------------------
# Downloading - press the row's own button, read what comes back
# ---------------------------------------------------------------------------

def pdf_from_payload(payload, invoice_number: str = "") -> Optional[bytes]:
    """The PDF inside one `{fileName, content}` answer, or None.

    Three things have to hold before any bytes reach the disk: the payload
    decodes as base64, the result really is a PDF, and its `fileName` names
    the invoice that was asked for. The last one is what stops a stale or
    mismatched answer from being filed under another month's date.
    """
    if not isinstance(payload, dict):
        return None
    content = payload.get("content")
    if not isinstance(content, str) or not content:
        return None
    try:
        body = base64.b64decode(content, validate=True)
    except Exception:
        return None
    if not body.startswith(b"%PDF"):
        return None
    name = str(payload.get("fileName") or "")
    if invoice_number and name and invoice_number not in name:
        log.warning("answer named %r does not match invoice %s", name, invoice_number)
        return None
    return body


def _button_for(row, kind: str):
    """The row's own download button for this kind of document.

    Checked against the read-only guard by its label before it is returned:
    the guard is the contract, so it is applied to the thing actually clicked
    rather than trusted from the selector.
    """
    meta = KINDS[kind]
    button = row.locator(f"{meta['cell']} button").first
    try:
        label = (button.text_content(timeout=4000) or "").strip()
    except Exception:
        label = ""
    if not is_safe_control(label or meta["label"]):
        log.error("refusing to click %r - the read-only guard denies it", label)
        return None
    return button


def download_document(page, handle: str, out_path) -> bool:
    """Save one document's PDF by pressing the button that produces it.

    There is no URL to fetch (module docstring, point 1), so this finds the
    row on its own tab, clicks *Factuur* or *Specificaties*, and reads the
    JSON the SPA reads. The click also routes the app to a viewer page, so it
    steps back to the list afterwards - leaving the tab where the next
    download expects to find it.
    """
    from pathlib import Path

    parsed = parse_handle(handle)
    if not parsed:
        log.error("refusing to act on the document handle %r", handle)
        return False
    kind, tab, number = parsed

    if not _open_tab(page, tab):
        log.warning("could not open the invoice tab %s", tab)
        return False

    row = page.locator(ROW_SEL).filter(has_text=number).first
    try:
        if row.count() == 0:
            log.warning("invoice %s is no longer listed on %s", number, tab)
            return False
    except Exception:
        return False

    button = _button_for(row, kind)
    if button is None:
        return False

    endpoint = KINDS[kind]["endpoint"]

    def wanted(response) -> bool:
        return (response.request.method == "POST"
                and urlparse(response.url).path == endpoint)

    try:
        with page.expect_response(wanted, timeout=45000) as answer:
            button.click(timeout=15000)
        response = answer.value
    except Exception as e:
        log.warning("no answer from %s for invoice %s (%s)",
                    endpoint, number, type(e).__name__)
        _back_to_list(page, tab)
        return False

    ok = False
    try:
        if not is_safe_pdf_url(response.url):
            log.error("unexpected answer from %s - not saved", response.url)
        elif not response.ok:
            # A dead session shows up here as a 401/403 rather than as a
            # signed-out page. An error is not a document, so nothing is saved.
            log.warning("%s answered %s for invoice %s",
                        endpoint, response.status, number)
        else:
            body = pdf_from_payload(_json_of(response), number)
            if body is None:
                log.warning("the answer for invoice %s held no usable PDF", number)
            else:
                out = Path(out_path)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(body)
                ok = True
    finally:
        _back_to_list(page, tab)
    return ok


def _json_of(response):
    try:
        return response.json()
    except Exception:
        log.warning("the answer was not JSON")
        return None


def _back_to_list(page, tab: str) -> None:
    """Return the tab to the invoice list after a download click.

    History only. If going back does not land on a list (the user navigated in
    the meantime, say), the tab's own link is clicked instead.
    """
    if _route_of(page) == tab and _list_is_showing(page):
        return
    try:
        page.go_back(timeout=15000)
    except Exception:
        pass
    if not _list_is_showing(page):
        _open_tab(page, tab)
