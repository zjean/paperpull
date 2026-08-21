"""Youfone's own facts, and the guards that matter on a Dutch telecom portal.

Most of these exist because of how Youfone's portal behaves rather than because
of anything generic: its API refuses every request that does not carry the
`securitykey` header its own app computes, so documents are *clicked* rather
than fetched — and a tool that clicks needs the read-only guard to be exactly
right.
"""
import base64
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import storage
import youfone_site as site

APP_DIR = Path(__file__).resolve().parents[1]


# --- the provider's facts ---------------------------------------------------

def test_provider_and_csv_name(tmp_path):
    storage.set_filename_owner("")
    assert storage.build_pdf_filename("2026-06-01", "Factuur", "") == \
        "2026-06-01 Youfone Factuur.pdf"
    assert storage.Paths(tmp_path).document_index_csv.name == "Youfone Document Index.csv"


def test_the_two_documents_of_one_invoice_do_not_collide(tmp_path):
    """Each invoice yields a factuur AND a specificatie, both dated the same
    day. The summary is the only thing keeping them apart on disk, which is why
    document_rules.json must map the two Dutch words to different summaries."""
    storage.set_filename_owner("")
    factuur = storage.build_pdf_filename("2026-08-14", "Factuur", "")
    spec = storage.build_pdf_filename("2026-08-14", "Specificatie", "")
    assert factuur != spec


def test_precreated_folders(tmp_path):
    paths = storage.Paths(tmp_path)
    paths.ensure()
    made = sorted(p.name for p in tmp_path.iterdir() if p.is_dir())
    # Youfone issues invoices and their specifications, and nothing else. The
    # other routes stay declared so a surprise document is still filed, but
    # their folders are made on demand rather than sitting empty forever.
    assert made == ["Backups", "Diagnostics", "Logs", "Manual Review",
                    "Statements"]


def test_routing(tmp_path):
    paths = storage.Paths(tmp_path)
    assert paths.folder_for("Statement").name == "Statements"
    assert paths.folder_for("Tax Document").name == "Tax Documents"
    assert paths.folder_for("Something Else").name == "Other Documents"


def test_the_orchestrator_imports():
    import importlib
    module = importlib.import_module("youfone_docs")
    assert hasattr(module, "main") and hasattr(module, "App")


def test_the_index_records_which_invoice_a_file_came_from():
    """"Source URL" is the only trace in the CSV of where a PDF came from. A
    Youfone document has no URL, so it holds this app's own handle — and a row
    built from `href` instead would leave that column empty on every line."""
    import youfone_docs

    handle = site.make_handle(site.INVOICE, "/facturen/thuis", "1388375202608")
    doc = youfone_docs.Document(
        title="Factuur 1388375202608", category="Statement", summary="Factuur",
        date="2026-08-14", source_url=handle,
        pdf_filename="2026-08-14 Youfone Factuur.pdf")
    row = youfone_docs.index_row(doc, owner="", status="Downloaded",
                                 processing="Completed")

    assert row["Source URL"] == handle
    assert row["Document Date"] == "2026-08-14"
    assert row["Document Title"] == "Factuur 1388375202608"
    assert set(row) == set(storage.DOCUMENT_INDEX_COLUMNS)


# --- Dutch dates -----------------------------------------------------------

def test_dutch_month_names_are_understood():
    """What the table actually prints: "14 augustus 2026"."""
    assert site.parse_date("14 augustus 2026") == "2026-08-14"
    assert site.parse_date("17 maart 2026") == "2026-03-17"
    assert site.parse_date("1 mei 2026") == "2026-05-01"
    assert site.parse_date("Juni 2026") == "2026-06-01"


def test_dutch_numeric_dates_are_day_first():
    """08-07-2026 on a Dutch invoice is 8 July, not 7 August. Reading it the
    American way silently moves documents to the wrong month."""
    assert site.parse_date("08-07-2026") == "2026-07-08"
    assert site.parse_date("31-12-2025") == "2025-12-31"


def test_unparseable_dates_return_nothing():
    assert site.parse_date("") is None
    assert site.parse_date("some Tuesday") is None
    assert site.parse_period_date("nonsense") == ("", "")


# --- document handles ------------------------------------------------------

def test_a_handle_survives_the_round_trip():
    """Discovery stores a handle; the download parses it back weeks later. It
    carries the three facts a click needs: which document, which subscription's
    tab, which invoice."""
    handle = site.make_handle(site.SPECIFICATION, "/facturen/thuis", "1388375202608")
    assert site.parse_handle(handle) == \
        (site.SPECIFICATION, "/facturen/thuis", "1388375202608")


@pytest.mark.parametrize("handle", [
    "",
    "invoice|/facturen/thuis",                      # missing the number
    "invoice|/facturen/thuis|not-a-number",
    "payment|/facturen/thuis|1388375202608",        # not a document kind
    "invoice|/gegevens|1388375202608",              # not an invoice route
    "invoice|/facturen|1388375202608",              # the redirect, not a tab
    "invoice|/facturen/i/1388I1388I123|1388375202608",   # a per-click detail route
    "invoice|https://my.youfone.nl/facturen/thuis|1388375202608",
])
def test_a_handle_that_is_not_ours_is_refused(handle):
    """A handle comes back out of discovery.json, which is a file on disk. It
    is re-validated rather than trusted, because acting on it means clicking."""
    assert site.parse_handle(handle) is None


# --- which routes may be opened -------------------------------------------

@pytest.mark.parametrize("href", ["/facturen", "/facturen/thuis",
                                  "/facturen/mobiel", "/facturen/0612345678"])
def test_invoice_routes_may_be_opened(href):
    assert site.is_safe_route(href) is True


@pytest.mark.parametrize("href", [
    "/facturen/i/1388375202603I1388375I88614",   # where a download click lands
    "/facturen/s/1388375202603I1388375I61167",
    "/gegevens", "/klantvoordeel/youcoins", "/inloggen",
    "https://my.youfone.nl/facturen/thuis",      # absolute = full page load
    "//evil.test/facturen", "", "facturen",
])
def test_other_routes_are_refused(href):
    """Two reasons. A detail route's trailing id is minted per click, so
    following one is following a URL we were handed. And an absolute href — even
    on Youfone's own host — is a full page load, which reboots the SPA; the
    header's own "Facturen" link is exactly that, complete with a `_gl`
    tracking parameter."""
    assert site.is_safe_route(href) is False


# --- which answers may be read --------------------------------------------

def test_only_the_two_document_endpoints_are_read():
    assert site.ALLOWED_ENDPOINTS == {
        "/api/prov/Pdf/GetInvoice", "/api/prov/Pdf/GetSpecification"}
    for endpoint in site.ALLOWED_ENDPOINTS:
        assert site.is_safe_pdf_url(f"https://my.youfone.nl{endpoint}") is True


@pytest.mark.parametrize("url", [
    "https://my.youfone.nl/api/prov/Payment/CreateIdealPayment",
    "https://my.youfone.nl/api/prov/authentication/login",
    "https://my.youfone.nl/api/prov/Invoice/GetInvoices",
    "https://my.youfone.nl/api/prov/Customer/UpdateIban",
    "https://my.youfone.nl/api/prov/Pdf/GetInvoice/../../Payment",
])
def test_other_endpoints_are_not_treated_as_documents(url):
    assert site.is_safe_pdf_url(url) is False


@pytest.mark.parametrize("url", [
    "https://my.youfone.nl.evil.test/api/prov/Pdf/GetInvoice",
    "https://my.youfone.nl@evil.test/api/prov/Pdf/GetInvoice",
    "http://my.youfone.nl/api/prov/Pdf/GetInvoice",
    "https://www.youfone.nl/api/prov/Pdf/GetInvoice",
])
def test_off_host_answers_are_refused(url):
    """Compared by parsed host and scheme, never by prefix: both of the first
    two *start with* the right address while pointing elsewhere."""
    assert site.is_safe_pdf_url(url) is False


# --- what may be written to disk ------------------------------------------

def _payload(pdf: bytes, name: str = "invoice1388375202608") -> dict:
    return {"fileName": name, "content": base64.b64encode(pdf).decode()}


def test_a_real_pdf_answer_is_accepted():
    body = site.pdf_from_payload(_payload(b"%PDF-1.7\nbody"), "1388375202608")
    assert body == b"%PDF-1.7\nbody"


def test_an_answer_for_another_invoice_is_never_saved():
    """The filename is the only thing tying an answer back to the row that was
    clicked. A mismatch would file one month's invoice under another month's
    date, which is worse than a failure - it looks like a clean run."""
    assert site.pdf_from_payload(
        _payload(b"%PDF-1.7\nx", "invoice1388375202607"), "1388375202608") is None


@pytest.mark.parametrize("payload", [
    None, {}, "not a dict",
    {"fileName": "invoice1", "content": ""},
    {"fileName": "invoice1", "content": "not base64 !!"},
    {"fileName": "invoice1"},
])
def test_a_malformed_answer_is_refused(payload):
    assert site.pdf_from_payload(payload) is None


def test_an_error_page_is_not_a_document():
    """A dead session answers with HTML, which base64-decodes perfectly well.
    Only the %PDF header separates it from an invoice."""
    html = b"<html><body>Log in om verder te gaan</body></html>"
    assert site.pdf_from_payload(_payload(html)) is None


# --- the read-only guard --------------------------------------------------

@pytest.mark.parametrize("control", [
    "Betalen", "Nu betalen", "Factuur betalen", "Betaling regelen",
    "Overboeken", "Automatische incasso wijzigen", "Machtiging intrekken",
    "Abonnement wijzigen", "Abonnement opzeggen", "Verlengen",
    "Bundel bijkopen", "Extra data kopen", "Opwaarderen", "Tegoed opwaarderen",
    "Simkaart vervangen", "Simkaart blokkeren", "Wachtwoord wijzigen",
    "Gegevens wijzigen", "Verwijderen", "Bestellen", "Telefoon kopen",
    "Uitloggen",
])
def test_dutch_money_and_account_controls_are_refused(control):
    """A telecom portal can pay a bill, buy a bundle, swap a SIM and cancel a
    contract. None of that may ever be clickable — and on this site the guard
    is not theoretical, because this app does click. 'Factuur betalen' is the
    interesting one: it matches the document allowlist too, and the blocklist
    still has to win."""
    assert site.is_safe_control(control) is False


@pytest.mark.parametrize("control", [
    "Oudere facturen opvragen",
    "BTW factuur aanvragen",
    "Rekeningnummer wijzigen",
    "Incassomoment aanpassen",
])
def test_the_controls_beside_the_downloads_are_refused(control):
    """These four sit under "Direct regelen" on the Facturen page itself, an
    inch from the buttons this app presses. The first two are why `opvragen`
    and `aanvragen` are on the blocklist: both contain the word "factuur", so
    the document allowlist alone would have waved them through, and neither
    reads anything — they file a request with Youfone's back office."""
    assert site.is_safe_control(control) is False


@pytest.mark.parametrize("control", [
    "Download", "Download PDF", "Factuur", "Specificaties",
    "Factuur downloaden", "Specificatie",
])
def test_document_controls_are_allowed(control):
    """The two labels in the middle are the only controls this app ever
    presses, so they had better pass."""
    assert site.is_safe_control(control) is True


def test_unrecognised_controls_are_refused():
    """Deny by default: not on the allowlist means not clicked."""
    for name in ("Doe iets", "Verder", "Volgende", "", "   "):
        assert site.is_safe_control(name) is False


@pytest.mark.parametrize("control", [
    # Each of these matches the DOCUMENT allowlist, so deny-by-default does
    # not save us: only a blocklist hit does.
    "Sign in to download your PDF",
    "Log in for facturen",
    "Password required - download factuur",
    "Register to view your specificatie",
    "Download PDF and schedule payment",
    "Transfer to view PDF",
])
def test_signin_and_money_words_beat_the_document_allowlist(control):
    """The lesson from paperpull_core.controls: an app that only knows its own
    provider's words has to relearn it. Ally and Chase both refused
    money-movement controls and both happily touched a control inside a
    SIGN-IN form, because refusing that had not occurred to either. Youfone's
    own blocklist is Dutch, so an English sign-in prompt wrapped around the
    word "download" would otherwise clear both halves of the guard."""
    assert site.is_safe_control(control) is False


def test_a_payment_overview_is_refused_even_though_it_is_a_document():
    """"Betalingsoverzicht" is a real document, and it also contains
    "betaling". Deny-by-default resolves that in the blocklist's favour, which
    costs nothing here: Youfone does not offer one. It is recorded as a test so
    a future repair does not "fix" it by loosening the blocklist."""
    assert site.is_safe_control("Betalingsoverzicht") is False


# --- Youfone's own hard rules ---------------------------------------------

def test_the_site_layer_never_navigates_or_opens_a_tab():
    """Youfone keeps its token in per-tab sessionStorage, so a new tab starts
    signed out and a `page.goto` reboots the SPA. Getting around is done the
    way a person does it: the app's own router links, and the back button."""
    source = (APP_DIR / "youfone_site.py").read_text(encoding="utf-8")
    for forbidden in ("page.goto(", "new_page(", "expect_download"):
        assert forbidden not in source, forbidden
    assert "go_back(" in source


def test_the_site_layer_makes_no_requests_of_its_own():
    """Everything is read from answers the page itself asked for. A request
    built here would need Youfone's `securitykey`, and forging that means
    shipping their crypto — so there is no request-building code at all, and
    nothing to keep in step with their handshake."""
    source = (APP_DIR / "youfone_site.py").read_text(encoding="utf-8")
    for forbidden in ("context.request", ".request.get(", ".request.post(",
                      "urlencode", "api_url("):
        assert forbidden not in source, forbidden


def test_a_missing_youfone_tab_is_reported_not_papered_over():
    """Most apps may open a tab when none is there. Here that tab would be
    signed out, so the engine has to refuse and say what to do instead."""
    source = (APP_DIR / "youfone_docs.py").read_text(encoding="utf-8")
    assert "new_page()" not in source
    assert "site.find_signed_in_page(" in source
