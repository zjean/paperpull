"""ALL Simyo URLs, API details and page behaviour live here.

When Simyo changes its site, repair this file only.

WHAT MAKES SIMYO DIFFERENT FROM THE OTHER PROVIDERS HERE
--------------------------------------------------------
1. **A second tab signs you out — so this app never opens one, and never
   navigates.** Mijn Simyo is a Nuxt 3 single-page app that keeps its session
   in the tab's own `sessionStorage`. A new tab (or a `page.goto`, which boots
   the SPA from scratch) starts with none, so its first API call comes back
   401, and the app's own response interceptor answers a 401 by calling
   `signOff()` — which POSTs `/auth/logout` and destroys the *server* session
   as well. One stray navigation therefore signs you out of the tab you were
   using. Every function below reads through the tab that is already open, and
   there is no `page.goto`, no `new_page`, and no click anywhere in this file.

2. **Nothing is clicked at all.** Behind the SPA sits the JSON API its own
   Download button uses, and it answers a plain GET carrying the ordinary
   session cookie:

       GET /api/get?endpoint=listAllPostpaid
           -> {"result": [{invoiceNumber, invoiceID, date, paymentStatus,
                           processDate, dueDate, concept, isOutdated,
                           total, payURL, ...}]}

       GET /api/get?endpoint=downloadPostpaidPdf&args=<invoiceNumber>
           -> application/pdf   (Content-Disposition: <invoiceNumber>.pdf)

   Reading it directly is also *safer* than using the page: a raw request that
   comes back 401 is simply reported, while the SPA's own fetch would have
   signed the user out (see 1).

3. **The whole API is one path with the verb in a query parameter.** Every
   call is `/api/get?endpoint=<name>`, so `is_safe_url()` has to read that
   parameter rather than the path. The same portal that lists invoices also
   offers `createIdealPaymentRequestForInvoice` (starts a payment),
   `sessionLogout`, `updateAddress` and `createSimcard` through the identical
   URL shape — so the guard is an allowlist of exactly two read endpoints, and
   `/api/post`, `/api/put` and `/api/delete` are refused outright.

4. **Each invoice row carries a pre-authenticated payment link.** `payURL`
   points at api.talos.kpn.com and pays the bill with one click. It is never
   read, never followed, and never stored — see `collect_documents`.

5. **The running month is not a document.** It arrives with
   `invoiceNumber: "current"`, `invoiceID: null` and `concept: true`; Simyo's
   own UI hides its download button (it gates on the invoice number being
   numeric) and the PDF endpoint answers HTTP 500 for it. It is skipped.

6. **The session times out while idle, silently.** Left alone for a while, the
   tab redirects itself to `/inloggen?uitgelogd=2` — or keeps showing the
   invoice list while the server session is already gone. So sign in and run
   promptly, and treat the page as no evidence: `looks_signed_out()` asks
   `/auth/is-logged-in` instead of reading the DOM. A run that gets signed out
   halfway stops and asks, and `--resume` picks it up afterwards.

7. **Dutch dates.** `08-07-2026` on a Simyo invoice is 8 July, not 7 August,
   and the month names are Dutch. The API's own dates carry an Amsterdam
   offset (`2026-06-01T00:00:00+02:00`) and are read as calendar days rather
   than instants — converting that to UTC would move every invoice into the
   previous month.

WHAT IS FETCHED
---------------
The monthly invoice PDF, which is what `/facturen` offers. Simyo issues no tax
forms.

**There is no "load more" to click, and clicking it would find nothing.**
`/facturen` renders five rows and a "Toon meer facturen" button, which is
tempting to read as pagination hiding the older invoices. It is not: one
`listAllPostpaid` call returns the whole list — thirteen invoices where the
page showed five — and that button only flips a local `showMoreInvoices` flag
in the SPA to render rows it already has. The "Jaar" filter is the same thing.
So this app cannot miss an invoice by not clicking, and a future repair should
not add DOM paging to "fix" a gap that is not there.

Where the history really stops is Simyo's server. It keeps roughly the last
twelve months and drops the rest: the page says so ("alleen je betalingen tot
een jaar geleden"), the year filter offers only the two years that survive,
and an account whose SEPA mandate dates from November 2024 is offered nothing
older than the previous August. `isOutdated` marks those last months; their
PDFs still download, they simply carry less detail, because the VAT
specification inside is dropped after six months under EU rules. Nothing here
caps anything — if Simyo serves it, this fetches it.

Prepaid accounts are a different product with their own endpoints
(`listAllPrepaidPayments`, `getPrepaidPaymentInvoicePdf`) and are not read yet;
`collect_documents` says so rather than returning an empty list.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import parse_qs, urlencode, urlparse

from paperpull_core import controls

log = logging.getLogger("simyo_docs.site")

BASE = "https://mijn.simyo.nl"
HOST = "mijn.simyo.nl"
API_PATH = "/api/get"

URLS = {
    "home": f"{BASE}/",
    "documents": f"{BASE}/facturen",
}

# The only two endpoints this tool has any business calling. Everything else
# behind /api/get is either a write, a payment, personal data, or the logout
# that would end the session we depend on.
INVOICE_LIST_ENDPOINT = "listAllPostpaid"
INVOICE_PDF_ENDPOINT = "downloadPostpaidPdf"
ALLOWED_ENDPOINTS = frozenset({INVOICE_LIST_ENDPOINT, INVOICE_PDF_ENDPOINT})

# Prepaid lives behind these; recognised so the app can say "not supported"
# instead of "no invoices found".
PREPAID_LIST_ENDPOINT = "listAllPrepaidPayments"

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
# a bundle, top up credit, swap or block a SIM, extend or cancel the contract,
# and change where the money comes from. "Uitloggen" is in here too: signing
# out mid-run is destructive on this site, because the session cannot be
# recreated without the user.
FORBIDDEN_CONTROL_RE = re.compile(
    r"(betaal|betalen|betaling|ideal|overboek|"
    r"incasso|machtiging|rekeningnummer|iban|"
    r"opzeg|be[eë]indig|wijzig|aanpassen|verander|"
    r"verleng|upgrade|downgrade|overstap|"
    r"bijkoop|bijkopen|kopen|koop|bestel|besteld|aanschaf|"
    r"opwaarder|tegoed|bundel|extra\s+data|"
    r"simkaart|sim\s+vervang|blokkeer|deblokkeer|activeer|deactiveer|"
    r"verwijder|annuleer|intrek|"
    r"wachtwoord|inloggegevens|persoonlijke\s+gegevens|"
    r"uitloggen|afmelden|"
    r"pay|transfer|cancel|delete|remove|submit|order|buy|upgrade)", re.I)

SAFE_DOC_CONTROL_RE = re.compile(
    r"(download|factuur|facturen|specificatie|verbruiksoverzicht|"
    r"betalingsoverzicht|pdf|print|opslaan\s+als\s+pdf)", re.I)


def is_safe_control(name: str) -> bool:
    """Deny by default: clear the blocklist AND match the document allowlist.

    Both halves matter. "Factuur betalen" is on the document allowlist by
    virtue of the word factuur, and the blocklist is what refuses it.

    The blocklist is `paperpull_core.controls`' shared vocabulary *plus* the
    Dutch one above, not the Dutch one alone. The blocklist here is in Dutch
    because Simyo's pages are, and a control that says "Sign in to download
    your PDF" would otherwise clear both halves of the guard — an English
    sign-in prompt with the word "download" in it. Which is very close to the
    mistake `controls` exists to record: two apps refused every
    money-movement control and both went on to touch a control inside a
    sign-in form, because refusing *that* had not occurred to either.
    """
    name = (name or "").strip()
    if not name:
        return False
    if controls.is_forbidden_context(name, FORBIDDEN_CONTROL_RE):
        return False
    return bool(SAFE_DOC_CONTROL_RE.search(name))


def api_url(endpoint: str, args: Optional[str] = None) -> str:
    """Build one Mijn Simyo API URL."""
    params = {"endpoint": endpoint}
    if args is not None:
        params["args"] = str(args)
    return f"{BASE}{API_PATH}?{urlencode(params)}"


def is_safe_url(url: str) -> bool:
    """Is this a URL we are willing to request?

    Four rules, all of which have to hold:

    * `https`, and the host is exactly mijn.simyo.nl. Compared by parsed host,
      never by prefix: `https://mijn.simyo.nl.evil.test/` and
      `https://mijn.simyo.nl@evil.test/` both *start with* the right address
      while pointing somewhere else entirely.
    * No credentials in the URL — never legitimate here, and a classic way to
      disguise the real host.
    * The path is exactly /api/get. The sibling /api/post, /api/put and
      /api/delete routes are how this portal changes things, and /auth/logout
      is how it ends the session.
    * The `endpoint` parameter names one of the two read endpoints above.
      Simyo puts the verb there, so this is the rule that actually separates
      "list my invoices" from "start an iDEAL payment".
    """
    try:
        got = urlparse(url or "")
    except ValueError:
        return False
    if got.scheme != "https":
        return False
    if (got.hostname or "").lower() != HOST:
        return False
    if got.port not in (None, 443):
        return False
    if got.username or got.password:
        return False
    if (got.path or "").rstrip("/") != API_PATH:
        return False
    endpoints = parse_qs(got.query or "").get("endpoint", [])
    return len(endpoints) == 1 and endpoints[0] in ALLOWED_ENDPOINTS


# ---------------------------------------------------------------------------
# Session detection
# ---------------------------------------------------------------------------

def find_signed_in_page(context):
    """The tab that is already signed in to Mijn Simyo.

    Returns None rather than opening one. Every other app in this project may
    fall back to a fresh tab; here that fresh tab would sign the user out (see
    the module docstring), so the caller has to ask the user instead.
    """
    live = [p for p in context.pages if not p.is_closed()]
    for page in live:
        if HOST in (page.url or ""):
            return page
    return None


def no_page_help() -> str:
    return (
        "No Mijn Simyo tab is open in that browser.\n"
        "Run login.command, sign in at https://mijn.simyo.nl/facturen, and\n"
        "leave that tab open. Do not open a second Mijn Simyo tab: Simyo keeps\n"
        "its session in the tab, and a second one signs you out of the first.")


def looks_signed_out(page) -> bool:
    """Ask the server, not the page.

    The SPA keeps showing a cached invoice list after the session is gone, so
    the URL and the DOM both lie. `/auth/is-logged-in` answers `true`/`false`
    and is a plain read.
    """
    url = (page.url or "").lower()
    if any(m in url for m in LOGIN_URL_MARKERS):
        return True
    try:
        resp = page.context.request.get(f"{BASE}/auth/is-logged-in")
    except Exception:
        return False
    if not resp.ok:
        return False
    return resp.text().strip().strip('"').lower() != "true"


def detect_security_challenge(page) -> Optional[str]:
    try:
        body = page.locator("body").inner_text(timeout=5000).lower()
    except Exception:
        return None
    for marker in SECURITY_CHALLENGE_MARKERS:
        if marker in body:
            return f"Sign-in verification step detected: '{marker}'"
    return None


def goto_documents(page) -> bool:
    """Confirm the invoices are reachable — WITHOUT navigating.

    Named for the interface every app's orchestrator expects. On Simyo it is
    deliberately not a navigation: it only checks that the session is alive,
    because loading `/facturen` would restart the SPA and sign the user out.
    """
    return not looks_signed_out(page)


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
# `(?!\d)` rather than `\b` on the tail: the API's dates continue straight
# into a time ("2026-06-01T00:00:00+02:00"), and `1` followed by `T` is not a
# word boundary — so a `\b` here matches nothing at all.
_ISO_RE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")


def iso_from_api_date(value) -> str:
    """Simyo's own date field -> YYYY-MM-DD, read as a calendar day.

    The API sends `2026-06-01T00:00:00+02:00`: midnight in Amsterdam. Treated
    as an instant and converted to UTC that is 2026-05-31T22:00Z, which would
    file every invoice a day early — and, on the first of the month, in the
    *previous month*, so "Juni 2026" would land under May. The date part is
    taken literally instead, which is what the portal itself displays.
    """
    if not isinstance(value, str):
        return ""
    m = _ISO_RE.match(value.strip())
    if not m:
        return ""
    try:
        datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return ""
    return m.group(0)


def parse_date(text: str) -> Optional[str]:
    """Parse a date out of Dutch page text -> YYYY-MM-DD.

    A fallback: the API gives exact dates, so this only catches text scraped
    from the page (by --diagnose, say) or a title the API could not date. A
    bare month and year becomes the first of that month, which is how Simyo
    labels an invoice ("Juni 2026").
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
# The JSON API
# ---------------------------------------------------------------------------

@dataclass
class RawDoc:
    """One Simyo invoice, in the shape the orchestrator records."""
    title: str
    date_text: str
    pdf_url: str
    invoice_number: str = ""


def _get(page, endpoint: str, args: Optional[str] = None):
    """GET one API endpoint using the signed-in session."""
    url = api_url(endpoint, args)
    if not is_safe_url(url):
        raise RuntimeError(f"refusing to request endpoint {endpoint!r}")
    return page.context.request.get(url)


def _get_json(page, endpoint: str):
    resp = _get(page, endpoint)
    if not resp.ok:
        log.warning("API %s returned %s", endpoint, resp.status)
        return None
    try:
        return resp.json()
    except Exception:
        log.warning("API %s did not return JSON", endpoint)
        return None


def _is_downloadable(row: dict) -> bool:
    """Does a PDF exist behind this row?

    Simyo's own UI decides with `!isNaN(parseInt(invoiceNumber))`, which is its
    way of spotting the running month (`invoiceNumber: "current"`). The same
    rule is used here, plus the explicit concept flag.
    """
    if row.get("concept"):
        return False
    return str(row.get("invoiceNumber") or "").strip().isdigit()


def collect_documents(page) -> List[RawDoc]:
    """Every invoice with a PDF behind it, newest first."""
    data = _get_json(page, INVOICE_LIST_ENDPOINT)
    rows = (data or {}).get("result") or []
    log.info("Simyo invoices in the account: %d", len(rows))

    docs: List[RawDoc] = []
    for row in rows:
        if not _is_downloadable(row):
            log.info("skipping %s (no PDF: running month or concept)",
                     row.get("invoiceNumber"))
            continue
        number = str(row["invoiceNumber"]).strip()
        date_text = iso_from_api_date(row.get("date"))
        docs.append(RawDoc(
            # Deliberately nothing from the row but the number and the month.
            # It also carries `total`, `amountAlreadyPaid` and `payURL` — a
            # pre-authenticated payment link — none of which is kept.
            title=f"Factuur {number}",
            date_text=date_text,
            pdf_url=f"{INVOICE_PDF_ENDPOINT}:{number}",
            invoice_number=number))

    docs.sort(key=lambda d: (d.date_text or "", d.invoice_number), reverse=True)
    return docs


def download_document(page, pdf_url: str, out_path) -> bool:
    """Save one invoice's PDF.

    A plain GET with the session cookie — the same request Simyo's own
    Download button makes. No control is clicked, no blob tab, no navigation.
    """
    from pathlib import Path
    number = (pdf_url or "").split(":")[-1].strip()
    if not number.isdigit():
        log.error("refusing to fetch a non-numeric invoice number")
        return False
    resp = _get(page, INVOICE_PDF_ENDPOINT, number)
    if not resp.ok:
        # 500 here is Simyo's answer for an invoice it cannot render — an
        # error page is not a document, so it is never saved.
        log.warning("PDF fetch for %s returned %s", number, resp.status)
        return False
    body = resp.body()
    if not body.startswith(b"%PDF"):
        log.warning("response for %s was not a PDF", number)
        return False
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(body)
    return True
