"""Simyo's own facts, and the guards that matter on a Dutch telecom portal.

Two of these tests exist because of how Simyo's portal behaves rather than
because of anything generic: it signs you out if a second tab appears, and its
whole API is one URL with the verb in a query parameter.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import storage
import simyo_site as site

APP_DIR = Path(__file__).resolve().parents[1]


# --- the provider's facts ---------------------------------------------------

def test_provider_and_csv_name(tmp_path):
    storage.set_filename_owner("")
    assert storage.build_pdf_filename("2026-06-01", "Factuur", "") == \
        "2026-06-01 Simyo Factuur.pdf"
    assert storage.Paths(tmp_path).document_index_csv.name == "Simyo Document Index.csv"


def test_precreated_folders(tmp_path):
    paths = storage.Paths(tmp_path)
    paths.ensure()
    made = sorted(p.name for p in tmp_path.iterdir() if p.is_dir())
    # Simyo issues invoices and nothing else. The other routes stay declared
    # so a surprise document is still filed, but their folders are made on
    # demand rather than sitting empty forever.
    assert made == ["Backups", "Diagnostics", "Logs", "Manual Review",
                    "Statements"]


def test_routing(tmp_path):
    paths = storage.Paths(tmp_path)
    assert paths.folder_for("Statement").name == "Statements"
    assert paths.folder_for("Tax Document").name == "Tax Documents"
    assert paths.folder_for("Something Else").name == "Other Documents"


def test_the_orchestrator_imports():
    import importlib
    module = importlib.import_module("simyo_docs")
    assert hasattr(module, "main") and hasattr(module, "App")


def test_the_index_records_which_invoice_a_file_came_from():
    """"Source URL" is the only trace in the CSV of where a PDF came from.
    Invoices are discovered as RawDocs, which never set `href` — so a row
    built from `href` would leave that column empty on every single line."""
    import simyo_docs

    doc = simyo_docs.Document(
        title="Factuur 97102597", category="Statement", summary="Factuur",
        date="2026-06-01", source_url="downloadPostpaidPdf:97102597",
        pdf_filename="2026-06-01 Simyo Factuur.pdf")
    row = simyo_docs.index_row(doc, owner="", status="Downloaded",
                               processing="Completed")

    assert row["Source URL"] == "downloadPostpaidPdf:97102597"
    assert row["Document Date"] == "2026-06-01"
    assert row["Document Title"] == "Factuur 97102597"
    assert set(row) == set(storage.DOCUMENT_INDEX_COLUMNS)


# --- Dutch dates -----------------------------------------------------------

def test_api_dates_keep_their_own_day():
    """Simyo sends '2026-06-01T00:00:00+02:00' - midnight in Amsterdam. Read
    as an instant and converted to UTC that becomes 2026-05-31T22:00Z, and
    every invoice would file one day early, in the *previous month*. The day
    is taken literally instead."""
    assert site.iso_from_api_date("2026-06-01T00:00:00+02:00") == "2026-06-01"
    assert site.iso_from_api_date("2026-01-01T00:00:00+01:00") == "2026-01-01"
    assert site.iso_from_api_date(None) == ""
    assert site.iso_from_api_date("nonsense") == ""


def test_dutch_numeric_dates_are_day_first():
    """08-07-2026 on a Dutch invoice is 8 July, not 7 August. Reading it the
    American way silently moves documents to the wrong month."""
    assert site.parse_date("08-07-2026") == "2026-07-08"
    assert site.parse_date("31-12-2025") == "2025-12-31"


def test_dutch_month_names_are_understood():
    assert site.parse_date("12 augustus 2025") == "2025-08-12"
    assert site.parse_date("1 maart 2026") == "2026-03-01"
    assert site.parse_date("Juni 2026") == "2026-06-01"
    assert site.parse_date("mei 2026") == "2026-05-01"


def test_unparseable_dates_return_nothing():
    assert site.parse_date("") is None
    assert site.parse_date("some Tuesday") is None


# --- what counts as a downloadable invoice ---------------------------------

def _row(**kw):
    row = {"invoiceNumber": "97102597", "invoiceID": "201800000047286565",
           "date": "2026-06-01T00:00:00+02:00", "paymentStatus": "Paid",
           "concept": False, "isOutdated": False, "total": 10.59,
           "totalInclAllCosts": 10.59}
    row.update(kw)
    return row


def test_invoices_are_collected_newest_first(monkeypatch):
    rows = [_row(invoiceNumber="95527336", date="2026-04-01T00:00:00+02:00"),
            _row(invoiceNumber="97102597", date="2026-06-01T00:00:00+02:00"),
            _row(invoiceNumber="95943740", date="2026-05-01T00:00:00+02:00")]
    monkeypatch.setattr(site, "_get_json", lambda page, ep: {"result": rows})

    docs = site.collect_documents(page=None)
    assert [d.date_text for d in docs] == ["2026-06-01", "2026-05-01", "2026-04-01"]
    assert docs[0].title == "Factuur 97102597"


def test_the_current_month_is_not_offered(monkeypatch):
    """The running month is a concept with invoiceNumber 'current' and no PDF
    behind it - Simyo's own UI hides its download button. Asking for it
    returns HTTP 500, so it must never reach the download queue."""
    rows = [_row(),
            _row(invoiceNumber="current", invoiceID=None, concept=True,
                 paymentStatus="NotAvailable", date="2026-08-01T00:00:00+02:00")]
    monkeypatch.setattr(site, "_get_json", lambda page, ep: {"result": rows})

    docs = site.collect_documents(page=None)
    assert [d.title for d in docs] == ["Factuur 97102597"]


def test_older_invoices_are_still_offered(monkeypatch):
    """`isOutdated` marks an invoice older than a year. Simyo still serves its
    PDF, so it stays in scope - dropping it would quietly cap the archive at
    twelve months."""
    rows = [_row(invoiceNumber="90563326", date="2025-08-01T00:00:00+02:00",
                 isOutdated=True)]
    monkeypatch.setattr(site, "_get_json", lambda page, ep: {"result": rows})

    assert len(site.collect_documents(page=None)) == 1


def test_no_amounts_are_recorded(monkeypatch):
    """The index says what a file IS, never what it says. Every invoice row
    carries the amount billed; none of it may reach a record on disk."""
    rows = [_row(total=10.59, totalInclAllCosts=10.59, amountAlreadyPaid=10.59)]
    monkeypatch.setattr(site, "_get_json", lambda page, ep: {"result": rows})

    doc = site.collect_documents(page=None)[0]
    blob = f"{doc.title} {doc.date_text} {doc.pdf_url} {doc.invoice_number}"
    assert "10.59" not in blob
    assert "10,59" not in blob


def test_the_pay_url_is_never_kept(monkeypatch):
    """Each row carries a payURL - a one-click, pre-authenticated payment link
    at api.talos.kpn.com. It must not be written to discovery.json, the CSV,
    or anywhere else this tool stores."""
    rows = [_row(payURL="https://api.talos.kpn.com/Invoice/?k=DEADBEEF")]
    monkeypatch.setattr(site, "_get_json", lambda page, ep: {"result": rows})

    doc = site.collect_documents(page=None)[0]
    blob = f"{doc.title} {doc.date_text} {doc.pdf_url} {doc.invoice_number}"
    assert "talos" not in blob
    assert "DEADBEEF" not in blob


# --- the URL guard ---------------------------------------------------------

def test_only_the_two_read_endpoints_are_allowed():
    """Simyo's whole API is one path with the verb in a query parameter, so
    the guard has to read that parameter. `listAllPostpaid` and
    `downloadPostpaidPdf` are the only two this tool has any business calling."""
    assert site.is_safe_url(site.api_url("listAllPostpaid"))
    assert site.is_safe_url(site.api_url("downloadPostpaidPdf", "97102597"))


@pytest.mark.parametrize("endpoint", [
    "createIdealPaymentRequestForInvoice",   # starts a payment
    "sessionLogout",                         # ends the session we depend on
    "updateVatInvoice", "updateAddress", "updatePassword",
    "createSimcard", "settingsChangeSimcardBlock",
    "getUser", "getUserProfile",             # personal data, not a document
])
def test_other_endpoints_are_refused(endpoint):
    assert site.is_safe_url(f"https://mijn.simyo.nl/api/get?endpoint={endpoint}") is False


@pytest.mark.parametrize("url", [
    "https://mijn.simyo.nl/api/post?endpoint=listAllPostpaid",
    "https://mijn.simyo.nl/api/put?endpoint=listAllPostpaid",
    "https://mijn.simyo.nl/api/delete?endpoint=listAllPostpaid",
    "https://mijn.simyo.nl/auth/logout",
])
def test_writing_paths_are_refused(url):
    assert site.is_safe_url(url) is False


@pytest.mark.parametrize("url", [
    "https://mijn.simyo.nl.evil.test/api/get?endpoint=listAllPostpaid",
    "https://mijn.simyo.nl@evil.test/api/get?endpoint=listAllPostpaid",
    "http://mijn.simyo.nl/api/get?endpoint=listAllPostpaid",
    "https://www.simyo.nl/api/get?endpoint=listAllPostpaid",
])
def test_off_host_urls_are_refused(url):
    """Compared by parsed host and scheme, never by prefix: both of the first
    two *start with* the right address while pointing elsewhere."""
    assert site.is_safe_url(url) is False


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
    contract. None of that may ever be clickable. 'Factuur betalen' is the
    interesting one: it matches the document allowlist too, and the blocklist
    still has to win."""
    assert site.is_safe_control(control) is False


@pytest.mark.parametrize("control", [
    "Download", "Download PDF", "Factuur downloaden", "Specificatie",
])
def test_document_controls_are_allowed(control):
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
    SIGN-IN form, because refusing that had not occurred to either. Simyo's own
    blocklist is Dutch, so an English sign-in prompt wrapped around the word
    "download" would otherwise clear both halves of the guard."""
    assert site.is_safe_control(control) is False


def test_a_payment_overview_is_refused_even_though_it_is_a_document():
    """"Betalingsoverzicht" is a real document, and it also contains
    "betaling". Deny-by-default resolves that in the blocklist's favour, which
    costs nothing here: nothing on this site is ever clicked. It is recorded as
    a test so a future repair does not "fix" it by loosening the blocklist."""
    assert site.is_safe_control("Betalingsoverzicht") is False


# --- Simyo's own hard rule: one tab, no navigation ------------------------

def test_the_site_layer_never_navigates_or_opens_a_tab():
    """Simyo keeps its session in per-tab sessionStorage. A second tab boots
    without it, its first API call 401s, and the SPA's own error handler calls
    signOff() - which POSTs /auth/logout and kills the server session for the
    tab you are signed in to as well. So this app reads through the tab you
    already have open and never navigates it."""
    source = (APP_DIR / "simyo_site.py").read_text(encoding="utf-8")
    for forbidden in ("page.goto(", "new_page(", ".click(", "expect_download"):
        assert forbidden not in source, forbidden


def test_every_request_is_a_GET():
    source = (APP_DIR / "simyo_site.py").read_text(encoding="utf-8")
    for verb in (".post(", ".put(", ".patch(", ".delete("):
        assert verb not in source, verb
    assert source.count("request.get(") >= 1


def test_a_missing_simyo_tab_is_reported_not_papered_over():
    """Every other app may open a tab when none is there. Here that would sign
    the user out, so the engine has to refuse and say what to do instead."""
    source = (APP_DIR / "simyo_docs.py").read_text(encoding="utf-8")
    assert "new_page()" not in source
    assert "site.find_signed_in_page(" in source
