"""Youfone document classification: the titles are Dutch, the categories are not.

The folders and categories stay in the project's own vocabulary (Statement ->
Statements) so filing works the same as the other providers; only the summary
that lands in the filename is the word Youfone itself uses.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import storage  # binds Youfone's AppSpec (the rules file is found through it)
from paperpull_core import doc_types

RULES = doc_types.load_rules()


@pytest.mark.parametrize("title,summary", [
    ("Factuur 1388375202608", "Factuur"),
    ("Factuur", "Factuur"),
    ("factuur juni 2026", "Factuur"),
])
def test_invoices_classify_as_statements(title, summary):
    cat, got, conf = doc_types.classify_document(title, RULES)
    assert cat == doc_types.STATEMENT, title
    assert got == summary, (title, got)


@pytest.mark.parametrize("title", [
    "Specificatie 1388375202608", "Specificatie juni 2026", "Specificaties",
])
def test_a_usage_specification_is_a_statement_too(title):
    """Every invoice row offers one, so this is fetched as often as the invoice
    itself - and it must not classify as a Factuur, or the two PDFs of one
    month would fight over the same filename."""
    cat, summary, _ = doc_types.classify_document(title, RULES)
    assert cat == doc_types.STATEMENT
    assert summary == "Specificatie"


def test_dutch_noise_is_skipped():
    for title in ("Nieuwsbrief augustus", "Privacyverklaring",
                  "Algemene voorwaarden"):
        assert doc_types.should_skip(title, RULES), title
    assert not doc_types.should_skip("Factuur 1388375202608", RULES)


def test_only_configured_categories_are_wanted():
    cfg = {"document_types": ["Statement"]}
    assert doc_types.wanted(doc_types.STATEMENT, cfg)
    assert not doc_types.wanted(doc_types.TAX, cfg)
    assert not doc_types.wanted(doc_types.OTHER, cfg)


def test_an_invoice_routes_to_the_statements_folder(tmp_path):
    """The classifier speaks in categories; the folder carries the name on
    disk. This is the join between the two."""
    cat, _, _ = doc_types.classify_document("Factuur 1388375202608", RULES)
    assert storage.Paths(tmp_path).folder_for(cat).name == "Statements"
