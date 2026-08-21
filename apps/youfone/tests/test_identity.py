"""Youfone-specific identity behaviour: two documents, one anchor.

`site.collect_documents` yields TWO RawDocs per invoice - the Factuur and its
Specificaties (youfone_site.py, module docstring point 2) - and both carry the
SAME `invoice_number` and `date_text`. App._anchor_records turns each RawDoc
into one `{"id", "date"}` record, so a single invoice with both documents
produces two records with an identical id. This file checks - rather than
assumes - that paperpull_core.identity.pick_anchors collapses that pair to one
anchor, and that App.ensure_identity's ADOPT path records the collapsed
count, not the doubled one.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from paperpull_core import identity

import youfone_docs
import youfone_site as site
from storage import sentinel


def _app(tmp_path, unattended: bool = False, adopt_identity: bool = False) -> youfone_docs.App:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "output_dir": str(tmp_path / "output"),
        "cdp_url": "http://localhost:9999",   # never dialled
    }), encoding="utf-8")
    args = argparse.Namespace(config=str(config_path), unattended=unattended,
                              adopt_identity=adopt_identity)
    app = youfone_docs.App(args)
    app.browser = lambda: object()
    return app


def _invoice_pair(number: str, date_text: str) -> list:
    """The two RawDocs one Youfone invoice produces, exactly as
    site.collect_documents builds them - same invoice_number, same
    date_text, different kind and title."""
    return [
        site.RawDoc(title=f"Factuur {number}", date_text=date_text,
                   pdf_url=site.make_handle(site.INVOICE, "/facturen/thuis", number),
                   invoice_number=number, kind=site.INVOICE, tab="/facturen/thuis"),
        site.RawDoc(title=f"Specificatie {number}", date_text=date_text,
                   pdf_url=site.make_handle(site.SPECIFICATION, "/facturen/thuis", number),
                   invoice_number=number, kind=site.SPECIFICATION, tab="/facturen/thuis"),
    ]


def test_a_facturen_and_specificatie_pair_yields_two_anchor_records_before_dedup():
    """_anchor_records does not itself dedup - it is a straight map over the
    documents it is given. Establishing that is what makes the next test's
    "collapses to one" meaningful: the collapse has to come from
    identity.pick_anchors, not from this method quietly dropping one."""
    app_records = youfone_docs.App._anchor_records
    docs = _invoice_pair("123456", "2026-08-14")
    # Call the unbound method directly - it only reads its `docs` argument.
    records = app_records(None, docs)
    assert records == [{"id": "123456", "date": "2026-08-14"},
                       {"id": "123456", "date": "2026-08-14"}]


def test_the_pair_collapses_to_one_anchor_via_pick_anchors():
    """The actual claim: identity.pick_anchors's own dedup - keyed on `id`
    alone - is what turns those two identical-id records into one anchor."""
    docs = _invoice_pair("123456", "2026-08-14")
    records = youfone_docs.App._anchor_records(None, docs)
    anchors = identity.pick_anchors(records)
    assert anchors == [{"id": "123456", "date": "2026-08-14"}]


def test_three_invoices_of_two_documents_each_still_pick_three_anchors(tmp_path):
    """A realistic discovery: three invoices, six RawDocs. ANCHOR_COUNT (3)
    should end up counting invoices, not documents - if the pair did not
    collapse, three invoices' six records would fill all three anchor slots
    with only the two OLDEST invoices, silently dropping the third."""
    docs = (_invoice_pair("101", "2026-01-14")
            + _invoice_pair("102", "2026-02-14")
            + _invoice_pair("103", "2026-03-14"))
    records = youfone_docs.App._anchor_records(None, docs)
    anchors = identity.pick_anchors(records)
    assert [a["id"] for a in anchors] == ["101", "102", "103"]


def test_ensure_identity_adopts_one_anchor_per_invoice_not_per_document(tmp_path):
    """End to end through the real App method: adopting identity from a
    two-document invoice records ONE anchor, not two."""
    app = _app(tmp_path, adopt_identity=True)
    docs = _invoice_pair("123456", "2026-08-14")

    app.ensure_identity(page=object(), docs=docs)

    anchors = sentinel.read_anchors(app.sentinel)
    assert anchors == [{"id": "123456", "date": "2026-08-14"}]
