"""Youfone invoice downloader (local, supervised).

Youfone is a Dutch telecom provider (SIM-only, plus internet/TV as "Thuis").
This downloads the monthly invoices ("facturen") from my.youfone.nl, each as
two PDFs: the factuur and its specificaties.

Usage:
    python youfone_docs.py --login       verify connection to your browser
    python youfone_docs.py --discover    list available invoices
    python youfone_docs.py --pilot       download the 5 newest, then stop
    python youfone_docs.py --all         download everything in scope
    python youfone_docs.py --resume      continue an interrupted run
    python youfone_docs.py --verify      re-validate every saved PDF
    python youfone_docs.py --diagnose    dump what the page shows (no downloads)
    python youfone_docs.py --dry-run     plan filenames, save nothing

First run of a config, and only ever with you watching:
    python youfone_docs.py --discover --adopt-identity
                                       record WHICH account this config's tab
                                       belongs to. Every other command refuses
                                       to file anything until this has been
                                       done once, because one shared Chrome
                                       plus two Youfone accounts means a run
                                       could otherwise file the wrong tab's
                                       invoices under this config's owner.

Scheduled runs (no person present):
    python youfone_docs.py --unattended --all --yes
                                       discover, then download anything new.
                                       A dead session, a missing tab or an
                                       unprovable identity parks the account
                                       and exits 0 instead of asking.

Filters: --year YYYY  --start-date YYYY-MM-DD  --end-date YYYY-MM-DD
         --max-docs N  --type Statement

READ-ONLY: this tool only reads your invoice list and downloads PDFs that
Youfone already generated. It never pays a bill, buys a bundle, tops up credit,
changes a SIM, or touches any account setting. Everything stays on this
machine; nothing is sent to any external service.

ONE TAB, ROUTER NAVIGATION ONLY: Youfone keeps its session in the browser tab,
so this app attaches to the tab you signed in with and never opens one of its
own - a second MyYoufone tab starts signed out. Unlike Simyo, this app DOES
navigate: it clicks the SPA's own router links and the back button to walk
every subscription's invoice tab, both verified to keep the session. It never
does a `page.goto` or opens a new tab, which would lose it. See youfone_site.py.

CLICKED, NOT FETCHED: Youfone's API refuses any request that does not carry the
`securitykey` header its own app computes, so a PDF cannot be fetched by URL.
This app presses the row's own Factuur / Specificaties button and reads the
answer, the way a person would. See youfone_site.py.
"""
from __future__ import annotations

import argparse
import logging
import random
import re
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from paperpull_core import appload, doc_types, receipt_pdf
from paperpull_core import browser as browser_launcher
import youfone_site as site
from paperpull_core.models import State
from storage import (CsvFile, DOCUMENT_INDEX_COLUMNS, JsonStore, Paths,
                     atomic_write_text, build_pdf_filename, load_config,
                     now_iso, sanitize_component, unique_path)

from storage import ensure_owner, PROJECT_DIR, SPEC, set_filename_owner
from storage import identity, locks, sentinel
log = logging.getLogger("youfone_docs")

DONE_STATES = {State.COMPLETED.value, State.NO_RECEIPT_AVAILABLE.value}


def ask(prompt: str) -> str:
    try:
        return input(prompt)
    except EOFError:
        print("\nNo interactive console available to answer a required prompt.")
        print("Run this from a real console window (use the .bat files).")
        raise SystemExit(3)


def index_row(doc: "Document", owner: str = "", status: str = "",
              processing: str = "") -> dict:
    """One CSV row for one document.

    Kept out of App so it can be tested without a config, a browser or a
    filesystem. "Source URL" reads `source_url` rather than `href`: invoices
    arrive as RawDocs, which carry no href, and a row built from href would
    leave the only trace of where a PDF came from empty on every line.
    """
    return {
        "Account Holder": owner,
        "Document Date": doc.date,
        "Category": doc.category,
        "Document Summary": doc.summary,
        "Document Title": doc.title,
        "Period": doc.period,
        "PDF Filename": doc.pdf_filename,
        "PDF Full Path": doc.pdf_path,
        "PDF File Size": doc.pdf_size,
        "PDF Page Count": doc.pdf_pages,
        "Source URL": doc.source_url or doc.href,
        "Classification Confidence": doc.confidence,
        "Downloaded At": now_iso() if doc.pdf_filename else "",
        "Verified At": now_iso() if doc.pdf_pages else "",
        "Processing Status": processing,
        "Notes": "; ".join(x for x in (doc.notes, status) if x),
    }


class Document:
    """One Youfone document."""

    def __init__(self, title="", category="", summary="", date="", period="",
                 href="", row_index=-1, confidence="", account="",
                 date_text="", document_id="", **kw):
        self.title = title
        self.account = account
        self.category = category
        self.summary = summary
        self.date = date
        self.period = period
        self.date_text = date_text  # the row's raw date string, for re-matching
        self.document_id = document_id  # Youfone's stable per-document UUID
        self.source_url = kw.get("source_url", "")  # page where the doc's download link lives
        # Sticky "was successfully downloaded at least once" marker. Once set,
        # the document is never re-downloaded even if you delete the PDF (e.g.
        # after importing it into paperless-ngx).
        self.downloaded_ok = kw.get("downloaded_ok", False)
        self.href = href
        self.row_index = row_index
        self.confidence = confidence
        self.state = kw.get("state", State.DISCOVERED.value)
        self.pdf_filename = kw.get("pdf_filename", "")
        self.pdf_path = kw.get("pdf_path", "")
        self.pdf_size = kw.get("pdf_size", "")
        self.pdf_pages = kw.get("pdf_pages", "")
        self.notes = kw.get("notes", "")
        self.discovered_at = kw.get("discovered_at", now_iso())

    @property
    def key(self) -> str:
        """Stable identity: category:date:title:account.

        Youfone hands out no document id of its own, and the invoice number is
        not enough on its own - one invoice yields both a factuur and a
        specificatie, which the title is what separates."""
        if self.document_id:
            return f"id:{self.document_id}"
        acct = sanitize_component(self.account or "")[:40]
        return f"{self.category}:{self.date}:{sanitize_component(self.title)[:60]}:{acct}"

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d: dict) -> "Document":
        return cls(**d)


class Parked(Exception):
    """This account needs a human, and no human is present.

    Raised only in --unattended mode. Not a failure: main() turns it into a
    printed line and exit code 0, so a scheduled run that finds a dead
    session is quiet rather than alarming. Real failures keep their non-zero
    codes, which is what keeps cron alerting worth reading.
    """


class Terminated(KeyboardInterrupt):
    """SIGTERM arrived: something outside asked this run to stop.

    It exists so that the ordinary way a run ends unwinds the stack like
    every other way. Nothing here catches a signal by default, so SIGTERM
    killed the process outright and the `finally` in `locks.hold` never ran -
    which left this provider's session slot held by a process that no longer
    existed, until STALE_AFTER (six hours) let the next run steal it. That is
    the common path, not an edge case: the panel's Stop button sends SIGTERM,
    and so does closing the browser tab a run is streaming to.

    A subclass of KeyboardInterrupt because it is the same event as Ctrl+C
    from the code's point of view - `process()` already unwinds cleanly on
    one, saving progress on the way out - and only main() needs to tell the
    two apart, to report the right exit code.
    """


def _stop_on_sigterm() -> None:
    """Turn SIGTERM into an exception, so `finally` blocks run.

    Installed by main() for every run. Raising from the handler is what makes
    the difference: it unwinds through `locks.hold`, whose `finally` releases
    this run's session slot, and through main()'s own `finally`, which saves
    progress.json. Without it the slot survived the process, and gui/app.py's
    Run button then refused this provider until the lock aged out.

    Best-effort: signal.signal only works on the main thread, and SIGTERM
    does not exist on every platform this code may be read on. A run that
    cannot install the handler is exactly as safe as it was before, so a
    failure here must not stop it starting.
    """
    def handler(signum, frame):
        raise Terminated(f"signal {signum}")

    try:
        signal.signal(signal.SIGTERM, handler)
    except (AttributeError, ValueError, OSError):
        pass


class App:
    def __init__(self, args):
        self.args = args
        # --config lets one copy of the code serve several people/accounts:
        # each config points at its own output_dir, profile_dir and port, so
        # progress.json, the index CSV, the PDFs and the browser session are
        # all kept separate. Nothing is ever re-downloaded across accounts.
        cfg_path = Path(args.config) if getattr(args, "config", None) \
            else (PROJECT_DIR / "config.json")
        self.config = load_config(cfg_path)
        # unattended=...: this is the only app that can reach here with no
        # human attached (see main()'s --unattended guard above _dispatch),
        # so it is the only call site that has anything to tell ensure_owner
        # beyond its own two-argument default.
        ensure_owner(self.config, cfg_path,
                     unattended=getattr(args, "unattended", False))
        set_filename_owner(self.config.get("owner", "") if self.config.get("owner_in_filename") else "")
        self.paths = Paths(Path(self.config["output_dir"]))
        self.paths.ensure()
        self._setup_logging()

        self.progress = JsonStore(self.paths.progress_json, self.paths.backups)
        self.discovery = JsonStore(self.paths.discovery_json, self.paths.backups)
        self.progress.load()
        self.discovery.load()
        # Which account this config belongs to, and whether its session was
        # alive last time. No secret lives here - see paperpull_core.sentinel.
        self.sentinel = JsonStore(self.paths.sentinel_json, self.paths.backups)
        self.sentinel.load()
        self._identity_verified = False
        self._warm_marked = False
        self.index_csv = CsvFile(self.paths.document_index_csv,
                                 DOCUMENT_INDEX_COLUMNS, self.paths.backups)
        self.rules = doc_types.load_rules()

        self._pw = None
        self._browser = None
        self._context = None
        self._work_page = None
        self._cdp_mode = False
        self.stats = {
            "mode": "", "started": now_iso(), "ended": "",
            "discovered": 0, "statements": 0, "tax_documents": 0,
            "insurance_documents": 0,
            "other": 0, "skipped_completed": 0, "skipped_out_of_scope": 0,
            "manual_review": 0, "failed": 0, "duplicate_filenames": 0,
            "validation_failures": 0, "dates": [], "new_files": [],
        }

    # -- infrastructure ----------------------------------------------------

    def _setup_logging(self):
        logfile = self.paths.logs / f"run-{datetime.now():%Y%m%d-%H%M%S}.log"
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
            handlers=[logging.FileHandler(logfile, encoding="utf-8"),
                      logging.StreamHandler(sys.stdout)])
        logging.getLogger("pypdf").setLevel(logging.ERROR)

    def _delay(self, factor: float = 1.0):
        time.sleep(random.uniform(
            float(self.config["delay_min_seconds"]) * factor,
            float(self.config["delay_max_seconds"]) * factor))

    def browser(self):
        if self._context is not None:
            return self._context
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        # Attaching over CDP is the ONLY mode here. Every other app can fall
        # back to launching its own persistent-profile browser and navigating
        # to the provider; on Youfone that navigation is precisely what signs
        # you out, so there is no such fallback to offer.
        cdp_url = self.config.get("cdp_url")
        if not cdp_url:
            self._pw.stop()
            self._pw = None
            raise SystemExit(
                'No "cdp_url" is set in this config.\n'
                "Youfone can only be read through a browser you signed in to\n"
                "yourself, so this app always attaches over the DevTools port.\n"
                'Set "cdp_url": "http://localhost:9238" and run login.command.')
        try:
            self._browser = self._pw.chromium.connect_over_cdp(cdp_url)
        except Exception as e:
            self._pw.stop()
            self._pw = None
            raise SystemExit(
                f"Could not connect to your signed-in browser at {cdp_url}.\n"
                f"Run login.command first and keep that browser window OPEN.\n({e})")
        if not self._browser.contexts:
            raise SystemExit("Connected browser has no context; open a tab and retry.")
        self._context = self._browser.contexts[0]
        self._cdp_mode = True
        self._context.set_default_timeout(30000)
        return self._context

    def page(self):
        """The tab the user signed in with — or a clear refusal.

        Unlike most apps here, this one will NOT open a tab when it cannot
        find one. Youfone keeps its token in the tab's own `sessionStorage`, so
        a fresh tab starts signed out no matter what the rest of the browser
        knows. A missing tab is something only the user can fix.
        """
        ctx = self.browser()
        if self._work_page is not None and not self._work_page.is_closed():
            return self._work_page
        found = site.find_signed_in_page(ctx)
        if found is None:
            if getattr(self.args, "unattended", False):
                # In the Docker layout, no tab open is the single most likely
                # reason a scheduled run finds nothing - the human just
                # hasn't signed in yet today. That is the ordinary case, not
                # a failure, so this parks exactly like a dead session does
                # rather than exiting non-zero and paging someone for it.
                self._park("no signed-in tab")
            raise SystemExit(site.no_page_help())
        self._work_page = found
        # Nothing here arrives as a browser download: Youfone answers the
        # click with the PDF inline, as base64 in a JSON body, so there is no
        # download directory to arrange and no download event to wait for.
        self._dl_dir = None
        return self._work_page

    def close(self):
        # The tab and the browser both belong to the user: detaching is all
        # this does. Closing the tab would take the Youfone session with it.
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._pw = self._browser = self._context = self._work_page = None

    # -- session safety ----------------------------------------------------

    def check_session(self, page) -> None:
        unattended = getattr(self.args, "unattended", False)
        challenge = site.detect_security_challenge(page)
        if challenge:
            self.progress.save(backup=True)
            if unattended:
                self._park(f"security challenge: {challenge}")
            print(f"\n!! {challenge}")
            print("Stopped. Please resolve it yourself in the browser window.")
            print("I will NOT attempt to bypass any security check.")
            ask("Press Enter once the page looks normal (or Ctrl+C to quit)... ")
        if site.looks_signed_out(page):
            self.progress.save(backup=True)
            if unattended:
                self._park("signed out")
            print("\n!! Youfone appears to have signed you out.")
            print("Please sign in again in the open browser window.")
            print("Sign in in the SAME tab you had open - Youfone keeps its")
            print("session in that tab, so a new one starts signed out.")
            ask("Press Enter after you are signed in... ")
        if not self._warm_marked:
            # Once per run, not once per document: check_session runs for
            # every download, and JsonStore rewrites the whole file on each
            # update. A scheduler only needs to know the session was alive at
            # a known moment, and the panel's account list reads the same
            # field - so this is recorded in interactive runs too, not just
            # unattended ones.
            sentinel.mark_warm(self.sentinel, now_iso())
            self._warm_marked = True

    def _park(self, reason: str):
        sentinel.park(self.sentinel, reason, now_iso())
        raise Parked(reason)

    # -- account identity --------------------------------------------------

    def _anchor_records(self, docs) -> List[dict]:
        """The identity records a Youfone invoice list yields.

        Only the invoice number and its date - same two fields Simyo anchors
        on, and both already written to progress.json and the index CSV, so
        this records nothing new about the account.

        Youfone yields TWO RawDocs per invoice (the Factuur and its
        Specificaties - see youfone_site.py, module docstring point 2), and
        both carry the SAME invoice_number and date_text. That looks like it
        should spend two anchor slots on one invoice, but it does not:
        identity.pick_anchors keys its dedup dict on `id` alone
        (`{r["id"]: r for r in _clean(records)}`), so the pair collapses to
        one anchor before ANCHOR_COUNT ever gets to trim the list. See
        core/tests/test_identity.py's
        test_two_records_sharing_an_id_collapse_to_one_anchor for this
        verified directly against identity.pick_anchors, not assumed.
        """
        return [{"id": d.invoice_number, "date": d.date_text}
                for d in docs if getattr(d, "invoice_number", "")]

    def ensure_identity(self, page, docs=None) -> None:
        """Refuse to file this tab's invoices unless it IS this account.

        Every app here points at one shared Chrome in the Docker layout, and
        `site.find_signed_in_page` picks a tab by host - so with a second
        Youfone account signed in, this run could read the wrong tab and file
        its invoices under this config's `owner`. Nothing downstream would
        notice, because the owner comes from the config and never from the
        page.

        Verified once per run - but unlike Simyo, "once" here is not free.
        `site.collect_documents` is not a single GET: it walks every
        subscription tab by clicking the SPA's own router links (module
        docstring point 4), so calling it here is a real navigation, not
        just an API round-trip. That matters for --resume, which reaches
        `process()` (and so this method) without `cmd_discover` ever having
        run: the tab walk happens right here, leaving the browser on
        whichever subscription tab was read last - not necessarily the tab
        the run started on.

        Concluded that this is harmless for what runs next: every download
        this run makes goes through `site.download_document`, which parses
        its own handle's tab and calls `site._open_tab` unconditionally
        before touching a row - so it never assumes the page is already on
        the right tab. The one thing that DOES rely on "the page is where I
        left it" is `check_session`'s `looks_signed_out` / security-challenge
        checks, and both read the page's URL and body text, neither of which
        depends on which subscription tab is showing. So this identity check
        is allowed to leave the tab strip wherever it lands.
        """
        if self._identity_verified:
            return
        if docs is None:
            docs = site.collect_documents(page)
        observed = self._anchor_records(docs)
        recorded = sentinel.read_anchors(self.sentinel)
        verdict = identity.check(recorded, observed)
        act = identity.action(verdict, getattr(self.args, "adopt_identity", False))

        if act == identity.REFUSE:
            if getattr(self.args, "unattended", False):
                # The fourth "a human is needed" condition, and now the same
                # shape as the other three (no tab, signed out, security
                # challenge): park, exit 0, record why. It is the one
                # condition that can never fix itself: nothing a scheduler
                # does will ever adopt an identity, so paging someone every
                # night forever would record nothing on disk saying what for.
                self._park(f"cannot prove this tab's identity ({verdict})")
            if verdict == identity.MISMATCH:
                raise SystemExit(
                    "\n!! This tab is NOT the account this config belongs to.\n"
                    f"   config : {getattr(self.args, 'config', None) or 'config.json'}\n"
                    f"   owner  : {self.config.get('owner') or '(unset)'}\n"
                    "   Refusing to file another account's invoices under that owner.\n"
                    "   Sign THIS account in - in one tab, Youfone starts a second\n"
                    "   tab signed out anyway - and run again.")
            raise SystemExit(
                f"\n!! Cannot tell which account this tab belongs to ({verdict}).\n"
                "   Sign in yourself, check the browser really shows the account\n"
                "   this config is for, then run once with --adopt-identity to\n"
                "   record it. That is the one moment this is taken on trust, so\n"
                "   it is never done for you and never done unattended.")

        if act == identity.PROCEED:
            # Re-anchor: Youfone's own history stops at six months (half
            # Simyo's twelve - see youfone_site.py's module docstring), so
            # anchors age out roughly twice as fast and the fingerprint has to
            # roll forward with that window at least as often.
            sentinel.write_anchors(self.sentinel, identity.pick_anchors(observed))
            self._identity_verified = True
            return

        # ADOPT: no proof either way (nothing recorded yet, or every anchor
        # has aged out of Youfone's six-month window - which, left unrun for
        # six months or more, an account with anchors will eventually reach:
        # verdict AGED, and this same --adopt-identity path is how a human
        # re-establishes identity. That is correct, intended behaviour, not a
        # bug - it is the bootstrap hole the spec names, recurring on a
        # shorter clock than Simyo's.
        sentinel.write_anchors(self.sentinel, identity.pick_anchors(observed))
        self._identity_verified = True
        print("Recorded this account's identity:")
        for anchor in sentinel.read_anchors(self.sentinel):
            print(f"  invoice {anchor['id']}  ({anchor['date']})")

    # -- commands ----------------------------------------------------------

    def cmd_open_browser(self):
        """Open a sign-in window on THIS config's own port and profile.

        A second account opens its own browser, on its own port, with its own
        saved session - so nothing is duplicated in the launcher scripts. You
        sign in; the tool attaches afterwards.
        """
        port = browser_launcher.port_from_cdp_url(self.config.get("cdp_url", ""), "9238")
        profile = self.config["profile_dir"]

        name = browser_launcher.open_signin_browser(
            profile, port, site.URLS["documents"], prefer_real=False)
        if not name:
            return
        print(f"Opened a sign-in browser on port {port} ({name}).")
        print(f"Profile: {profile}")
        print()
        print("Sign in, and stay on the Facturen page.")
        print("Then keep that window OPEN and run the pilot.")
        print()
        print("One tab: Youfone keeps its session in the tab it was opened in,")
        print("so a second MyYoufone tab starts signed out.")

    def cmd_login(self):
        print("Checking the connection to your signed-in Youfone browser...\n")
        page = self.page()
        ok = site.goto_documents(page)
        challenge = site.detect_security_challenge(page)
        if challenge:
            print(f"!! {challenge}\nResolve it in the browser, then re-run --login.")
        elif not ok:
            print("Connected to the browser, but the invoice list is not "
                  "reachable.")
            print("Sign in again in that same tab (keep it OPEN), open your")
            print("Facturen page, then re-run --login.")
        else:
            invoices = site.collect_documents(page)
            print(f"Success: connected, signed in, and {len(invoices)} invoice(s) "
                  "are readable.")
            print("Keep that browser window OPEN, then run run_pilot.command.")
        self.close()

    def _in_scope(self, doc: Document) -> bool:
        a = self.args
        if not doc_types.wanted(doc.category, self.config):
            return False
        if a.type and doc.category.lower() != a.type.lower():
            return False
        if a.year and not (doc.date or "").startswith(str(a.year)):
            return False
        # Hard floor: never process documents before the configured start date
        # (an account may hold invoices older than the window you want).
        floor = a.start_date or self.config.get("default_start_date")
        if floor and (not doc.date or doc.date < floor):
            return False
        if a.end_date and (not doc.date or doc.date > a.end_date):
            return False
        return True

    def _record_raw(self, r, tax_year: str = "") -> int:
        """Turn one scraped row into a discovery record. Returns 1 if new."""
        if doc_types.should_skip(r.title, self.rules):
            self.stats["skipped_out_of_scope"] += 1
            return 0
        category, summary, confidence = doc_types.classify_document(
            r.title, self.rules)
        if not doc_types.wanted(category, self.config):
            self.stats["skipped_out_of_scope"] += 1
            return 0
        if r.date_text:
            date, period = site.parse_period_date(r.date_text)
        elif tax_year:
            # Tax-table rows carry no date; file them at the tax year end.
            date, period = f"{tax_year}-12-31", f"Tax Year {tax_year}"
        else:
            date, period = site.parse_period_date(r.text or r.title)
        # Keep the account in the summary so files stay distinguishable
        # (several accounts produce the same form in the same year).
        acct = (r.account or "").strip()
        full_summary = f"{summary} {acct}".strip() if acct else summary
        doc = Document(title=r.title, category=category, summary=full_summary,
                       date=date or "", period=period, href=r.href,
                       row_index=r.row_index, confidence=confidence,
                       date_text=getattr(r, "date_text", ""))
        doc.account = acct
        if self.discovery.get(doc.key) is None:
            rec = doc.to_dict()
            rec["state"] = State.DISCOVERED.value
            self.discovery.update(doc.key, rec, save=False)
            return 1
        self.discovery.update(doc.key, {"row_index": r.row_index,
                                        "href": r.href}, save=False)
        return 0

    def _record_rawdoc(self, r, source_url: str) -> int:
        """Record one scraped document (a RawDoc) from a Youfone page.
        Returns 1 if new."""
        title = re.sub(r"\s+", " ", (r.title or "")).strip()
        if not title:
            return 0
        if doc_types.should_skip(title, self.rules):
            self.stats["skipped_out_of_scope"] += 1
            return 0
        category, summary, confidence = doc_types.classify_document(title, self.rules)
        if not doc_types.wanted(category, self.config):
            self.stats["skipped_out_of_scope"] += 1
            return 0
        date = (r.date_text or "").strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            date, _ = site.parse_period_date(title)
            date = date or ""
        floor = self.args.start_date or self.config.get("default_start_date")
        if floor and (not date or date < floor):
            self.stats["skipped_out_of_scope"] += 1
            return 0
        doc = Document(title=title, category=category, summary=summary,
                       date=date, confidence=confidence, source_url=source_url)
        if self.discovery.get(doc.key) is None:
            rec = doc.to_dict()
            rec["state"] = State.DISCOVERED.value
            self.discovery.update(doc.key, rec, save=False)
            return 1
        # refresh which page the doc's download link lives on
        self.discovery.update(doc.key, {"source_url": source_url}, save=False)
        return 0

    def cmd_discover(self, quiet: bool = False) -> int:
        page = self.page()
        n_new = 0
        # The table renders every invoice Youfone still serves - there is no
        # paging, no "load more", and no year picker to walk. What discovery
        # does walk is the tab strip: one invoice list per subscription.
        self.check_session(page)
        docs = site.collect_documents(page)
        self.ensure_identity(page, docs)
        for r in docs:
            n_new += self._record_rawdoc(r, r.pdf_url)
        self.discovery.save()
        log.info("invoices: %d with a PDF, %d new", len(docs), n_new)

        self.stats["discovered"] = len(self.discovery.data)

        if not quiet:
            docs = [Document.from_dict(v) for v in self.discovery.data.values()]
            print(f"\nDiscovery complete. Documents known: {len(docs)}")
            by_cat = {}
            for d in docs:
                by_cat.setdefault(d.category, []).append(d)
            for cat, group in sorted(by_cat.items()):
                years = {}
                for d in group:
                    y = (d.date or "?")[:4]
                    years[y] = years.get(y, 0) + 1
                spread = ", ".join(f"{y}: {c}" for y, c in sorted(years.items(), reverse=True))
                print(f"  {cat}: {len(group)}  ({spread})")
            dates = sorted(d.date for d in docs if d.date)
            if dates:
                print(f"  Date range: {dates[0]} .. {dates[-1]}")
            if self.stats["skipped_out_of_scope"]:
                print(f"  Skipped as out of scope: {self.stats['skipped_out_of_scope']}")
        return n_new

    def _select(self, limit: Optional[int] = None) -> List[Document]:
        docs = [Document.from_dict(v) for v in self.discovery.data.values()]
        docs = [d for d in docs if self._in_scope(d)]
        docs.sort(key=lambda d: d.date or "0000", reverse=True)
        limit = limit if limit is not None else self.args.max_docs
        return docs[:limit] if limit else docs

    def _already_done(self, doc: Document) -> bool:
        """Skip documents already handled. A document that was successfully
        downloaded once is done FOR GOOD - it is not re-downloaded even if you
        later delete the PDF (e.g. after importing it into paperless-ngx). Use
        --redownload to override and fetch everything in scope again."""
        if getattr(self.args, "redownload", False):
            return False
        rec = self.progress.get(doc.key)
        if not rec:
            return False
        if rec.get("downloaded_ok"):
            return True
        state = rec.get("state")
        # terminal / already-completed (incl. records from before the
        # downloaded_ok marker existed): done, do not re-download.
        if state in (State.COMPLETED.value, State.PDF_VERIFIED.value,
                     State.NO_RECEIPT_AVAILABLE.value, State.CANCELED.value):
            return True
        # a review copy counts only if its PDF is still present and valid;
        # a quarantined / failed one should be retried.
        if state == State.NEEDS_MANUAL_REVIEW.value:
            p = rec.get("pdf_path", "")
            return bool(p and Path(p).exists()
                        and receipt_pdf.validate_pdf(Path(p), self.config["min_pdf_bytes"]).ok)
        return False

    # -- processing --------------------------------------------------------

    def process(self, docs: List[Document], dry_run: bool = False):
        page = self.page()
        # check_session before ensure_identity, not after: --resume reaches
        # this without ever calling cmd_discover first, so ensure_identity
        # would otherwise be the first thing to touch the page. A dead
        # session makes collect_documents come back empty, which identity.py
        # reads as UNKNOWN rather than "signed out" - the wrong diagnosis and
        # the wrong exit path. Checking the session first gives check_session
        # the first look, so a dead session is parked, not misreported.
        self.check_session(page)
        self.ensure_identity(page)
        for i, doc in enumerate(docs, 1):
            print(f"\n[{i}/{len(docs)}] {doc.date or '(no date)'}  "
                  f"{doc.category}  {doc.summary}")
            if self._already_done(doc):
                print("  Already downloaded and verified - skipping.")
                self.stats["skipped_completed"] += 1
                continue
            filename = build_pdf_filename(doc.date, doc.summary, "")
            if dry_run:
                print(f"  DRY RUN - would save: {filename}")
                continue
            try:
                self.download_one(page, doc, filename)
            except KeyboardInterrupt:
                print("\nInterrupted. Progress saved; run --resume to continue.")
                raise
            except Parked:
                # Not a per-document failure: the session died, and every
                # remaining document would only repeat the same diagnosis.
                # Let it propagate to main(), which prints one line and exits
                # 0 - the whole point of parking instead of asking.
                raise
            except Exception as e:
                log.exception("Failed on %s", doc.key)
                self._record(doc, State.FAILED, notes=str(e))
                self.stats["failed"] += 1
            self._delay()

    def download_one(self, page, doc: Document, filename: str):
        """Download one document PDF by pressing the button that produces it.

        Youfone answers that click with the PDF inline as base64 JSON - there
        is no download event and no URL to fetch."""
        self.check_session(page)
        folder = self.paths.folder_for(doc.category)
        out_path = unique_path(folder, filename, self.config["max_path_length"])
        if out_path.name != filename:
            self.stats["duplicate_filenames"] += 1

        # One click on the row's own download button, guard-checked by label
        # in the site layer before it is pressed.
        saved = site.download_document(page, doc.source_url, out_path)
        if not saved:
            self._record(doc, State.NEEDS_MANUAL_REVIEW,
                         notes="Could not capture the document PDF")
            self._write_row(doc, "Capture failed", "Needs Manual Review")
            self.stats["manual_review"] += 1
            print("  Could not capture this document - marked for manual review.")
            return

        # Some tax forms arrive as a ZIP containing the PDF(s).
        if receipt_pdf.is_zip(out_path):
            extracted = receipt_pdf.extract_pdfs_from_zip(out_path, out_path)
            if not extracted:
                self._record(doc, State.NEEDS_MANUAL_REVIEW,
                             notes="Downloaded archive contained no PDF")
                self._write_row(doc, "Archive with no PDF", "Needs Manual Review")
                self.stats["manual_review"] += 1
                print("  !! Download was an archive with no PDF - manual review.")
                return
            out_path = extracted[0]
            if len(extracted) > 1:
                doc.notes = (doc.notes + "; " if doc.notes else "") + \
                    f"archive contained {len(extracted)} PDFs"
                for extra in extracted[1:]:
                    print(f"  + extracted: {extra.name}")
            log.info("Extracted %d PDF(s) from ZIP for %s", len(extracted), doc.title)

        doc.pdf_path, doc.pdf_filename = str(out_path), out_path.name
        self._record(doc, State.PDF_SAVED)

        result = receipt_pdf.validate_pdf(out_path, self.config["min_pdf_bytes"])
        if not result.ok:
            self.stats["validation_failures"] += 1
            quarantine = unique_path(self.paths.manual_review, out_path.name,
                                     self.config["max_path_length"])
            try:
                out_path.replace(quarantine)
            except OSError:
                quarantine = out_path
            doc.pdf_path, doc.pdf_filename = str(quarantine), quarantine.name
            self._record(doc, State.NEEDS_MANUAL_REVIEW,
                         notes=f"PDF validation failed: {result.reason}")
            self._write_row(doc, "Validation failed", "Needs Manual Review")
            self.stats["manual_review"] += 1
            print(f"  !! Validation failed ({result.reason}); moved to Manual Review.")
            return

        doc.pdf_size, doc.pdf_pages = result.size_bytes, result.page_count
        doc.downloaded_ok = True   # done for good, even if the file is deleted later
        self._record(doc, State.COMPLETED)
        self._write_row(doc, "Downloaded", "Completed")
        self.stats["new_files"].append(str(out_path))
        if doc.date:
            self.stats["dates"].append(doc.date)
        if doc.category == doc_types.TAX:
            self.stats["tax_documents"] += 1
        elif doc.category == doc_types.INSURANCE:
            self.stats["insurance_documents"] += 1
        elif doc.category == doc_types.STATEMENT:
            self.stats["statements"] += 1
        else:
            self.stats["other"] += 1
        print(f"  Saved: {out_path.name}")

    # -- records -----------------------------------------------------------

    def _record(self, doc: Document, state: State, notes: str = ""):
        doc.state = state.value
        if notes:
            doc.notes = (doc.notes + "; " if doc.notes else "") + notes
        self.progress.update(doc.key, doc.to_dict())
        self.discovery.update(doc.key, {"state": state.value})

    def _write_row(self, doc: Document, status: str, processing: str):
        self.index_csv.append_rows([
            index_row(doc, self.config.get("owner", ""), status, processing)])

    # -- modes -------------------------------------------------------------

    def cmd_pilot(self):
        self.stats["mode"] = "pilot"
        print("PILOT MODE - limited supervised test run.\n")
        self.cmd_discover()
        docs = self._select(limit=self.config.get("pilot_count", 5))
        if not docs:
            print("\nNo documents in scope to pilot. Run --diagnose.")
            return
        print(f"\nDownloading {len(docs)} document(s)...")
        self.process(docs, dry_run=self.args.dry_run)
        self._pilot_report(docs)

    def _pilot_report(self, docs: List[Document]):
        print("\n" + "=" * 70)
        print("PILOT RESULTS - inspect these before approving a full run")
        print("=" * 70)
        problems = []
        for d in docs:
            rec = self.progress.get(d.key) or {}
            state = rec.get("state", "?")
            print(f"\n  {rec.get('date', d.date)}  {rec.get('category', d.category)}")
            print(f"    Title:  {rec.get('title', d.title)[:70]}")
            print(f"    State:  {state}   [{rec.get('confidence', '')}]")
            print(f"    PDF:    {rec.get('pdf_filename', '(none)')}"
                  f"  ({rec.get('pdf_size', '?')} bytes, {rec.get('pdf_pages', '?')} pages)")
            if state != State.COMPLETED.value:
                problems.append(f"{d.key}: {state} - {rec.get('notes', '')}")
        print("\n" + "-" * 70)
        if problems:
            print("Needs attention:")
            for p in problems:
                print(f"  ! {p}")
        else:
            print("No problems detected in the pilot.")
        print(f"\nFiles are in:\n  {self.paths.root}")
        print("Nothing further runs until you explicitly start a full command.")

    def cmd_run(self, mode_name: str):
        self.stats["mode"] = mode_name
        if mode_name == "all" and not self.args.yes:
            scope = ", ".join(self.config.get("document_types", []))
            print(f"This downloads ALL available Youfone documents ({scope}).")
            print("Type YES to continue:")
            if ask("> ").strip().upper() != "YES":
                print("Aborted. (Run the pilot first if you haven't: --pilot)")
                return
        self.cmd_discover()
        docs = self._select()
        print(f"\nDownloading {len(docs)} document(s)...")
        self.process(docs, dry_run=self.args.dry_run)

    def cmd_resume(self):
        self.stats["mode"] = "resume"
        # Look at the session BEFORE deciding there is nothing to do. _select()
        # reads discovery.json and nothing else, so a resume with an empty
        # queue used to return without the page ever being touched - and
        # check_session, the only thing that records `last_verified_alive`,
        # never ran. The account then looked unchecked to due.py, came back due
        # tomorrow, and asked a person to sign in again for the same nothing,
        # every day. A session verified alive is a fact worth recording whether
        # or not there turned out to be work behind it.
        #
        # process() checks again below; that costs nothing. The page is cached
        # on the App and check_session writes the sentinel once per run.
        self.check_session(self.page())
        docs = [d for d in self._select() if not self._already_done(d)]
        if not docs:
            print("Nothing to resume - everything in scope is complete.")
            return
        print(f"Resuming: {len(docs)} document(s) remaining.")
        self.process(docs, dry_run=self.args.dry_run)

    def cmd_verify(self):
        self.stats["mode"] = "verify"
        rows = self.index_csv.read_all()
        if not rows:
            print("Document index is empty - nothing to verify.")
            return
        bad = 0
        for row in rows:
            p = row.get("PDF Full Path", "")
            if not p:
                continue
            r = receipt_pdf.validate_pdf(Path(p), self.config["min_pdf_bytes"])
            if not r.ok:
                bad += 1
                print(f"  BAD {row.get('PDF Filename', '')}: {r.reason}")
            else:
                row["Verified At"] = now_iso()
        self.index_csv.rewrite(rows)
        print(f"\nVerified {len(rows)} index rows; {bad} problem(s).")

    def cmd_diagnose(self):
        """Dump what the Facturen page actually shows, and nothing else.

        No downloads, no API calls: Youfone's endpoints refuse anything that
        does not carry their own `securitykey` header, so there is nothing to
        query here. What this reports is what discovery reads - the tab strip,
        the rows in each tab, how each document was classified - plus what the
        read-only guard would say about the controls Youfone puts on the page.

        The one thing it does click is a tab, which is what a person clicks to
        see another subscription's invoices.
        """
        self.stats["mode"] = "diagnose"
        import json as _json
        page = self.page()
        info = {"timestamp": now_iso()}
        try:
            info["url"] = page.url
            info["signed_out"] = site.looks_signed_out(page)
            info["challenge"] = site.detect_security_challenge(page)
            info["reached_documents"] = site.goto_documents(page)

            # One entry per subscription. An account with two subscriptions
            # that reports one tab is youfone_site.py's point 4, not a clean
            # run - which is exactly what this is here to make visible.
            tabs = site.subscription_tabs(page)
            info["tabs"] = tabs
            info["rows_per_tab"] = {}
            for tab in tabs:
                if not site._open_tab(page, tab):
                    info["rows_per_tab"][tab] = "could not open"
                    continue
                rows = site.read_rows(page, tab)
                # Deliberately no amounts: they are on the page and on the
                # invoice, and this project's index records no money.
                info["rows_per_tab"][tab] = [{
                    "invoiceNumber": r["invoice_number"],
                    "date_text": r["date_text"],
                    "date": r["date"],
                    "kinds": r["kinds"],
                } for r in rows]

            docs = site.collect_documents(page)
            info["collected"] = len(docs)
            info["samples"] = []
            for d in docs[:8]:
                cat, summ, conf = doc_types.classify_document(d.title, self.rules)
                info["samples"].append({
                    "title": d.title, "date": d.date_text, "kind": d.kind,
                    "tab": d.tab, "category": cat, "summary": summ,
                    "confidence": conf})

            # What the guard would say about the controls Youfone puts on the
            # page. Read, never clicked - and a "safe": true on anything that
            # spends money or files a request is a bug report.
            controls = []
            for role in ("button", "link"):
                loc = page.get_by_role(role)
                for i in range(min(loc.count(), 60)):
                    try:
                        t = (loc.nth(i).inner_text(timeout=400) or "").strip()[:60]
                    except Exception:
                        t = ""
                    if t:
                        controls.append({"role": role, "text": t,
                                         "safe": site.is_safe_control(t)})
            info["controls"] = controls
        except Exception as e:
            info["error"] = str(e)
        out = self.paths.diagnostics / "diagnose-invoices.json"
        atomic_write_text(out, _json.dumps(info, indent=2, ensure_ascii=False))
        print(f"Wrote {out}")
        if info.get("signed_out"):
            print("Youfone shows a sign-in page - sign in again in that same tab.")
        rows = info.get("rows_per_tab") or {}
        print(f"Subscription tabs: {len(info.get('tabs') or [])}  "
              f"(documents collected: {info.get('collected', '?')})")
        for tab, got in rows.items():
            n = len(got) if isinstance(got, list) else got
            print(f"  {tab}: {n} invoice row(s)")
        for s in info.get("samples", [])[:5]:
            print(f"  [{s['category']}] {s['date']}  {s['summary']}  <- {s['title']}")


    # -- summary -----------------------------------------------------------

    def write_run_summary(self):
        s = self.stats
        s["ended"] = now_iso()
        dates = sorted(d for d in s["dates"] if d)
        new_files = s.get("new_files", [])
        atomic_write_text(self.paths.run_summary, "\n".join([
            "Youfone Invoices - run summary",
            "=" * 40,
            f"Run start:                 {s['started']}",
            f"Run end:                   {s['ended']}",
            f"Mode:                      {s['mode'] or '(none)'}",
            f"Documents known:           {s['discovered']}",
            f"NEW files this run:        {len(new_files)}",
            f"Statements downloaded:     {s['statements']}",
            f"Tax documents downloaded:  {s['tax_documents']}",
            f"Insurance docs downloaded: {s['insurance_documents']}",
            f"Other documents:           {s['other']}",
            f"Skipped (already done):    {s['skipped_completed']}",
            f"Skipped (out of scope):    {s['skipped_out_of_scope']}",
            f"Needs manual review:       {s['manual_review']}",
            f"Failed:                    {s['failed']}",
            f"Duplicate filenames (#'d): {s['duplicate_filenames']}",
            f"PDF validation failures:   {s['validation_failures']}",
            f"Earliest date processed:   {dates[0] if dates else '-'}",
            f"Latest date processed:     {dates[-1] if dates else '-'}",
            "",
        ]))
        # A plain list of exactly the files downloaded THIS run (all new,
        # since already-downloaded documents are skipped). Handy for knowing
        # what to import into paperless-ngx, and safe to ignore/delete.
        if new_files:
            atomic_write_text(
                self.paths.root / "new-this-run.txt",
                f"# {len(new_files)} file(s) downloaded on this run "
                f"({s['ended']}):\n" + "\n".join(sorted(new_files)) + "\n")
            print(f"\n{len(new_files)} NEW file(s) downloaded this run "
                  f"(listed in new-this-run.txt).")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Local supervised Youfone invoice downloader (read-only)")
    for name, help_text in [
            ("login", "verify connection to your signed-in browser"),
            ("discover", "list available invoices; writes discovery.json"),
            ("pilot", "download the 5 newest in-scope documents, then stop"),
            ("all", "download everything in scope (asks for confirmation)"),
            ("resume", "continue an interrupted run"),
            ("verify", "re-validate every saved PDF"),
            ("diagnose", "dump what the Facturen page shows (no downloads)")]:
        ap.add_argument(f"--{name}", action="store_true", help=help_text)
    ap.add_argument("--dry-run", action="store_true",
                    help="plan filenames but download nothing")
    ap.add_argument("--year", type=int)
    ap.add_argument("--start-date")
    ap.add_argument("--end-date")
    ap.add_argument("--max-docs", type=int)
    ap.add_argument("--type", help="Statement or 'Tax Document'")
    ap.add_argument("--yes", action="store_true", help="skip the --all confirmation")
    ap.add_argument("--redownload", action="store_true",
                    help="re-download everything in scope, ignoring the "
                         "'already downloaded' memory (rebuilds deleted files)")
    ap.add_argument("--config", help="use an alternate config file, e.g. "
                                     "config.spouse.json (separate account)")
    ap.add_argument("--adopt-identity", action="store_true",
                    help="record which account this config's tab belongs to. "
                         "Do this once, on a tab you just signed in yourself.")
    ap.add_argument("--unattended", action="store_true",
                    help="never ask a question: if the session is dead, park "
                         "this account and exit 0. For scheduled runs.")
    ap.add_argument("--open-browser", action="store_true",
                    help="launch a sign-in browser using this config's profile/port")
    return ap


def _dispatch(app: "App", args) -> int:
    """Run the one command argparse picked out, and nothing else.

    Split out of main() so the lock in main() can wrap this call without
    also wrapping the --unattended guard (which must run before App(args)
    exists at all) or the cleanup in main()'s `finally` (which must run
    whether or not the lock was ever taken).
    """
    if getattr(args, "open_browser", False):
        app.cmd_open_browser()
    elif args.login:
        app.cmd_login()
    elif args.discover:
        app.cmd_discover()
    elif args.pilot:
        app.cmd_pilot()
    elif args.all:
        app.cmd_run("all")
    elif args.resume:
        app.cmd_resume()
    elif args.verify:
        app.cmd_verify()
    elif args.diagnose:
        app.cmd_diagnose()
    elif args.dry_run:
        app.cmd_run("dry-run")
    else:
        build_parser().print_help()
    return 0


def main(argv=None):
    args = build_parser().parse_args(argv)
    for d in (args.start_date, args.end_date):
        if d and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
            print(f"Bad date '{d}': use YYYY-MM-DD")
            return 2
    if args.unattended:
        # These are the only commands that never need an answer from a person.
        # --adopt-identity is the one moment identity is taken on trust and
        # --open-browser is a human sitting down, so neither can appear here.
        # Refusing beats hanging in a container at 3am.
        #
        # --all is admitted, but only WITH --yes. The confirmation prompt is
        # the only reason --all ever needed a person, and --yes answers it
        # ahead of time; without it this would block on a typed YES nobody is
        # there to type. Admitting it is not a convenience: --all is the only
        # command that asks the provider what exists (cmd_run calls
        # cmd_discover first), and --resume selects from the local
        # discovery.json alone. A schedule built on --resume could therefore
        # never download a document it had not already seen - it spent a
        # person's sign-in and printed "Nothing to resume". --all downloads
        # everything IN SCOPE, and _already_done plus progress.json skip what
        # is on disk, so a nightly pass is discover-plus-new-only.
        #
        # This check runs before App(args) below, on purpose: it needs no
        # config, no sentinel and no browser, so a malformed unattended
        # invocation is refused in milliseconds rather than after a config
        # load that might itself fail. test_unattended.py depends on that
        # ordering.
        if not (args.discover or args.resume or args.verify
                or (args.all and args.yes)):
            print("--unattended works with --discover, --resume, --verify, "
                  "or --all --yes.")
            return 2
        if args.adopt_identity:
            print("--adopt-identity is never done unattended.")
            return 2
    # SIGTERM is how this run is normally asked to stop - the panel's Stop
    # button, and the panel's own cleanup when the browser tab streaming a run
    # is closed. Handled, so the `finally` in locks.hold below actually runs
    # and this provider's session slot is given back.
    _stop_on_sigterm()
    app = App(args)
    # The slot this takes is per PROVIDER, not per account: if two Youfone
    # accounts can never safely share a browser session (unverified either
    # way - see storage.py's comment on AppSpec.concurrency), the lock is
    # what actually enforces that rather than hoping nobody runs two at once.
    #
    # Which is why the directory is derived from the app, not from this
    # account's config: appload.lock_dir answers it for the panel and the
    # scheduler too (see its docstring), and a lock the three of them compute
    # differently is a lock that guards nothing.
    lock_dir = appload.lock_dir(SPEC.project_dir)
    # --verify only re-reads PDFs already saved on disk, and --open-browser is
    # a human sitting down to sign in - neither one touches the shared,
    # single-session browser tab this lock protects, so neither should have
    # to queue behind it (or be blocked by a run that is genuinely using it).
    # gui/app.py's _lock_exempt() carries a second, small copy of this same
    # rule (the panel must not import this module) - change that one too if
    # this one changes.
    needs_browser = not (args.verify or getattr(args, "open_browser", False))
    # Whoever ends up refused sees this: which config - and so which account -
    # is holding the slot. No secret, just the file the other run was told
    # to use.
    holder = args.config or "config.json"
    try:
        if needs_browser:
            with locks.hold(lock_dir, SPEC.slug, SPEC.concurrency, holder):
                return _dispatch(app, args)
        return _dispatch(app, args)
    except locks.ProviderBusy as e:
        print(f"\n!! {e}")
        return 4
    except Parked as e:
        print(f"\nParked: {e}. This account needs a sign-in; nothing was run.")
        return 0
    except Terminated:
        # Before KeyboardInterrupt, which this subclasses. 143 is the
        # conventional 128 + SIGTERM, and it has to be non-zero: the panel
        # tells "you stopped this" from "it finished by itself" by the exit
        # code, and reporting a stopped run as finished would be a lie.
        print("\nStopped on request. Progress saved; the session slot is free.")
        return 143
    except KeyboardInterrupt:
        print("\nStopped by user. Progress saved.")
    finally:
        app.progress.save()
        app.discovery.save()
        if app.stats["mode"]:
            app.write_run_summary()
        app.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
