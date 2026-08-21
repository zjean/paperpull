"""What makes the Youfone app different — everything else is paperpull_core.

Youfone's declaration of its own facts: the folders it files into, how a document
routes to one, its CSV columns, and its config defaults.

To repair Youfone's *page* behaviour, edit `youfone_site.py` instead.
"""
from __future__ import annotations

from pathlib import Path

from paperpull_core import storage as _core
from paperpull_core.spec import (AppSpec, CsvSpec, DOCUMENT, Folder,
                                 INFRASTRUCTURE_FOLDERS)

# One row per downloaded Youfone document. Deliberately records NO amounts,
# customer number, phone number, IBAN, or mandate reference — all of which
# appear on the invoice itself. Only what is needed to find and verify a file
# locally. "Source URL" holds this app's own document handle (kind|tab|invoice
# number), because a Youfone document has no URL — see youfone_site.py.
DOCUMENT_INDEX_COLUMNS = [
    "Account Holder",
    "Document Date", "Category", "Document Summary", "Document Title",
    "Period", "PDF Filename", "PDF Full Path", "PDF File Size",
    "PDF Page Count", "Source URL", "Classification Confidence",
    "Downloaded At", "Verified At", "Processing Status", "Notes",
]

SPEC = AppSpec(
    provider="Youfone",
    project_dir=Path(__file__).resolve().parents[0],
    kind=DOCUMENT,
    folders=[
        Folder("statements", "Statements"),
        # Youfone issues a monthly invoice and its usage specification, and
        # nothing else - both are statements. These routes stay so a surprise
        # document is still filed somewhere sensible, but their folders are
        # created only when one actually arrives.
        Folder("tax_documents", "Tax Documents", precreate=False),
        Folder("other_documents", "Other Documents", precreate=False),
        *INFRASTRUCTURE_FOLDERS,
    ],
    routes={
        "Statement": "statements",
        "Tax Document": "tax_documents",
    },
    default_route="other_documents",
    csv_files=[
        CsvSpec("document_index_csv", "{provider} Document Index.csv",
                DOCUMENT_INDEX_COLUMNS),
    ],
    config_defaults={
        "min_pdf_bytes": 2000,
        "delay_min_seconds": 2.5,
        "delay_max_seconds": 5.0,
        "pilot_count": 5,
        "document_types": ["Statement"],
    },
    base_url="https://my.youfone.nl/",
    rules_filename="document_rules.json",
    # Nobody has confirmed how long a signed-in MyYoufone tab survives idle -
    # unlike Simyo, there is no observed ~10-minute timeout to declare here.
    # None means "days" (AppSpec.session_lifetime_minutes's own docstring),
    # which is what lets the scheduler run this account on plain cron rather
    # than queuing it for a human sitting. This is a stated ASSUMPTION, not a
    # verified fact, and it is deliberately the cheap side to be wrong on: if
    # Youfone actually times out sooner, a scheduled run against a dead
    # session finds no signed-in tab (or a signed-out one), parks via
    # sentinel.park, and exits 0 - visibly, in that run's own log line and in
    # the panel's account list ("needs sign-in"). Nothing is corrupted and no
    # document is misfiled; the only cost is one skipped night. That is how a
    # reader would find out this guess was wrong - a pattern of parked runs in
    # the log or the panel - at which point set a real number here instead.
    session_lifetime_minutes=None,
    # Concurrency stays at the class default of 1, for a related but distinct
    # reason: this is also unverified, not confirmed safe. The module docstring
    # (point 3) says a NEW Youfone tab starts signed OUT, which is a weaker
    # claim than Simyo's rule that a second tab signs the FIRST one out - so
    # Youfone may well tolerate two live sessions where Simyo cannot. But
    # "may well" is not "does": nobody has run two Youfone accounts signed in
    # at once and checked. At 1 the only cost of being wrong is serialising
    # two accounts' runs through one session slot; a wrong 2 could let both
    # write progress.json against an overlapping session. Raise it only after
    # that has actually been tested.
    concurrency=1,
)

_core.bind(SPEC)

PROJECT_DIR = SPEC.project_dir

# The shared API, re-exported so the orchestrator's imports read the same as
# they do in every other app.
from paperpull_core.storage import (  # noqa: E402  (must follow bind)
    CsvFile, JsonStore, Paths, atomic_write_json, atomic_write_text,
    backup_file, build_pdf_filename, ensure_owner, load_config, now_iso,
    sanitize_component, set_filename_owner, title_case, unique_path,
)
from paperpull_core import identity, locks, sentinel  # noqa: E402

__all__ = [
    "SPEC", "PROJECT_DIR", "DOCUMENT_INDEX_COLUMNS",
    "CsvFile", "JsonStore", "Paths", "atomic_write_json", "atomic_write_text",
    "backup_file", "build_pdf_filename", "ensure_owner", "load_config",
    "now_iso", "sanitize_component", "set_filename_owner", "title_case",
    "unique_path", "identity", "locks", "sentinel",
]
