"""Reading the invoice table, against a stand-in for the page.

Youfone has no API this tool may call, so the rows come out of the DOM and the
logic that reads them is the thing most likely to break when Youfone restyles
the page. These tests drive `read_rows` / `collect_documents` through a fake
locator tree shaped like the real one (Angular Material: `tr.mat-mdc-row` with
`mat-column-*` cells).

The fake earns its keep on one point in particular: **the multi-subscription
walk cannot be tested on real hardware unless you own two subscriptions.** The
account this app was built against has only "Thuis", so the two-tab case is
proven here or nowhere.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import youfone_site as site


# --- a stand-in for the bits of Playwright the site layer reads -------------

class _TextLoc:
    """A cell. `None` means the cell is absent from the row."""

    def __init__(self, text):
        self._text = text

    def count(self):
        return 0 if self._text is None else 1

    @property
    def first(self):
        return self

    def text_content(self, timeout=None):
        if self._text is None:
            raise RuntimeError("no such cell")
        return self._text


class _CountLoc:
    def __init__(self, n):
        self.n = n

    def count(self):
        return self.n


class FakeRow:
    def __init__(self, date_text, number, kinds):
        self.date_text, self.number, self.kinds = date_text, number, kinds

    def locator(self, selector):
        if selector == site.DATE_CELL:
            return _TextLoc(self.date_text)
        if selector == site.NUMBER_CELL:
            return _TextLoc(self.number)
        for kind, meta in site.KINDS.items():
            if selector == f"{meta['cell']} button":
                return _CountLoc(1 if kind in self.kinds else 0)
        return _CountLoc(0)


class _RowsLoc:
    def __init__(self, rows):
        self.rows = rows

    def count(self):
        return len(self.rows)

    def nth(self, i):
        return self.rows[i]

    @property
    def first(self):
        return self

    def is_visible(self, timeout=None):
        return bool(self.rows)


class _LinkLoc:
    def __init__(self, page, href, exists):
        self.page, self.href, self.exists = page, href, exists

    @property
    def first(self):
        return self

    def click(self, timeout=None):
        if not self.exists:
            raise RuntimeError(f"no link to {self.href}")
        self.page.clicked.append(self.href)
        self.page.path = self.page.tab_of(self.href)


class FakePage:
    """A Facturen page with one tab per subscription."""

    def __init__(self, rows_by_tab, path=None, tab_links=None):
        self.rows_by_tab = rows_by_tab
        self.tabs = list(rows_by_tab)
        self.path = path or self.tabs[0]
        # What the tab strip renders. Defaults to a router link per tab, which
        # is what the real page does for "Thuis".
        self.tab_links = self.tabs if tab_links is None else tab_links
        self.clicked = []

    # -- what the site layer calls --
    @property
    def url(self):
        return f"https://my.youfone.nl{self.path}"

    def tab_of(self, href):
        return self.tabs[0] if href == site.DOCUMENTS_ROUTE else href

    def locator(self, selector):
        if selector == site.ROW_SEL:
            return _RowsLoc(self.rows_by_tab.get(self.path, []))
        if selector.startswith('a[href='):
            href = selector.split('"')[1]
            known = list(self.tab_links) + [site.DOCUMENTS_ROUTE]
            return _LinkLoc(self, href, href in known)
        if selector == "input[type=password]":
            return _RowsLoc([])          # nothing visible: not a sign-in page
        return _CountLoc(0)

    def eval_on_selector_all(self, selector, script):
        return list(self.tab_links)

    def wait_for_selector(self, selector, timeout=None):
        return None


def _rows_thuis():
    return [FakeRow("14 augustus 2026", " 1388375202608 ", ["invoice", "specification"]),
            FakeRow("17 maart 2026", " 1388375202603 ", ["invoice", "specification"])]


# --- reading one tab -------------------------------------------------------

def test_rows_are_read_with_their_dutch_dates():
    page = FakePage({"/facturen/thuis": _rows_thuis()})
    rows = site.read_rows(page, "/facturen/thuis")
    assert [r["invoice_number"] for r in rows] == ["1388375202608", "1388375202603"]
    assert [r["date"] for r in rows] == ["2026-08-14", "2026-03-17"]


def test_a_row_with_no_invoice_number_is_skipped_loudly():
    """The number is what the download click needs to find the row again. A row
    without one is unusable, and quietly dropping it would look like a provider
    with fewer invoices."""
    page = FakePage({"/facturen/thuis": [
        FakeRow("14 augustus 2026", "", ["invoice"]),
        FakeRow("17 maart 2026", "1388375202603", ["invoice"])]})
    rows = site.read_rows(page, "/facturen/thuis")
    assert [r["invoice_number"] for r in rows] == ["1388375202603"]


def test_each_invoice_yields_both_of_its_documents():
    page = FakePage({"/facturen/thuis": _rows_thuis()})
    docs = site.collect_documents(page)
    assert [(d.title, d.date_text) for d in docs] == [
        ("Specificatie 1388375202608", "2026-08-14"),
        ("Factuur 1388375202608", "2026-08-14"),
        ("Specificatie 1388375202603", "2026-03-17"),
        ("Factuur 1388375202603", "2026-03-17"),
    ]


def test_a_row_offering_only_an_invoice_yields_only_that():
    """Not seen in the wild, but the specification column exists per row, so a
    row without that button must not produce a document that cannot be
    clicked."""
    page = FakePage({"/facturen/thuis": [
        FakeRow("14 augustus 2026", "1388375202608", ["invoice"])]})
    docs = site.collect_documents(page)
    assert [d.title for d in docs] == ["Factuur 1388375202608"]


def test_documents_come_back_newest_first():
    page = FakePage({"/facturen/thuis": [
        FakeRow("17 maart 2026", "1388375202603", ["invoice"]),
        FakeRow("14 augustus 2026", "1388375202608", ["invoice"]),
        FakeRow("15 mei 2026", "1388375202605", ["invoice"])]})
    docs = site.collect_documents(page)
    assert [d.date_text for d in docs] == ["2026-08-14", "2026-05-15", "2026-03-17"]


def test_a_document_carries_the_handle_its_download_needs():
    page = FakePage({"/facturen/thuis": _rows_thuis()})
    doc = [d for d in site.collect_documents(page) if d.kind == site.INVOICE][0]
    assert site.parse_handle(doc.pdf_url) == \
        (site.INVOICE, "/facturen/thuis", "1388375202608")


def test_no_amounts_are_recorded():
    """The index says what a file IS, never what it says. Every row shows the
    amount billed; none of it may reach a record on disk."""
    page = FakePage({"/facturen/thuis": _rows_thuis()})
    for doc in site.collect_documents(page):
        blob = f"{doc.title} {doc.date_text} {doc.pdf_url} {doc.invoice_number}"
        assert "47,52" not in blob and "\u20ac" not in blob


# --- more than one subscription -------------------------------------------

def test_every_subscription_tab_is_walked():
    """An account with a SIM-only line as well has a second invoice list, and
    landing on `/facturen` only ever shows the first. Discovery walks the strip
    so the mobile invoices are not silently missing."""
    page = FakePage({
        "/facturen/thuis": [FakeRow("14 augustus 2026", "1388375202608", ["invoice"])],
        "/facturen/mobiel": [FakeRow("14 augustus 2026", "1388375202708", ["invoice"])],
    })
    docs = site.collect_documents(page)
    assert sorted(d.tab for d in docs) == ["/facturen/mobiel", "/facturen/thuis"]
    assert page.clicked  # it had to click to get to the second tab


def test_the_tab_a_download_needs_is_the_tab_it_was_found_on():
    page = FakePage({
        "/facturen/thuis": [FakeRow("14 augustus 2026", "1388375202608", ["invoice"])],
        "/facturen/mobiel": [FakeRow("14 augustus 2026", "1388375202708", ["invoice"])],
    })
    by_number = {d.invoice_number: d for d in site.collect_documents(page)}
    assert site.parse_handle(by_number["1388375202708"].pdf_url)[1] == "/facturen/mobiel"


def test_a_tab_that_is_only_a_material_tab_is_not_invented():
    """The strip is read as router links, which is what Thuis renders. If
    Youfone renders a second subscription some other way, the honest outcome is
    one tab and a log line — not a guessed URL. This test pins that down so a
    future repair adds real tab reading instead of a guess."""
    page = FakePage({"/facturen/thuis": _rows_thuis()},
                    tab_links=["/facturen/thuis"])
    assert site.subscription_tabs(page) == ["/facturen/thuis"]


def test_the_landing_route_is_not_mistaken_for_a_tab():
    """`/facturen` is a redirect to the first subscription. Treating it as a tab
    would read the same invoices twice."""
    page = FakePage({"/facturen/thuis": _rows_thuis()},
                    tab_links=["/facturen", "/facturen/thuis"])
    assert site.subscription_tabs(page) == ["/facturen/thuis"]


def test_detail_routes_are_not_mistaken_for_tabs():
    """After a download click the page carries links to `/facturen/i/<id>`.
    Those are viewer pages for a single document, and the id changes per
    click."""
    page = FakePage({"/facturen/thuis": _rows_thuis()},
                    tab_links=["/facturen/thuis",
                               "/facturen/i/1388375202603I1388375I88614",
                               "/facturen/s/1388375202603I1388375I61167"])
    assert site.subscription_tabs(page) == ["/facturen/thuis"]


def test_the_current_tab_is_used_when_the_strip_cannot_be_read():
    """Worst case the strip is unreadable, but the user left the tab on an
    invoice list. That list is still worth reading."""
    page = FakePage({"/facturen/thuis": _rows_thuis()}, tab_links=[])
    assert site.subscription_tabs(page) == ["/facturen/thuis"]
