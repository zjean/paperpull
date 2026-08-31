# Youfone Facturen Downloader (local, supervised)

Downloads your monthly **invoices (facturen)** and their **specifications
(specificaties)** from [my.youfone.nl](https://my.youfone.nl/facturen) and saves
them as PDFs, plus an index CSV:

- `Youfone Document Index.csv` — one row per downloaded PDF

Youfone is a Dutch telecom provider — SIM-only, plus internet/TV as "Thuis".
Everything runs **locally**. Nothing is sent to any external AI API or
third-party service. You sign in to Youfone **yourself**; the tool never touches
your credentials.

> ⚠️ A Youfone invoice carries your name, home address, phone number, customer
> number, IBAN and SEPA mandate reference. The index CSV deliberately records
> none of that — only what is needed to find and verify a file locally. Never
> commit the PDFs, the CSV, or your `config.json`.

## Two documents a month

Each invoice row offers **Factuur** (the bill) and **Specificaties** (the usage
breakdown behind it). Both are downloaded, and they land side by side:

```
2026-08-14 Youfone Factuur.pdf
2026-08-14 Youfone Specificatie.pdf
```

## Clicked, not fetched

This is what makes Youfone different from the other apps here. Behind the
Angular app sits a perfectly ordinary-looking JSON API:

```
POST /api/prov/Pdf/GetInvoice         {"customerId": N, "invoiceNumber": "..."}
POST /api/prov/Pdf/GetSpecification
    -> {"fileName": "invoice<number>", "content": "<base64 PDF>"}
```

You cannot call it. Every `/api/prov` request Youfone's own app makes carries a
`securitykey` header it computes per request, and a request without a valid one
is refused by nginx with a bare **403** before the application sees it — with
the session cookies attached, and with the tab's `_token` as a bearer token,
both refused.

So this app does what a person does: it presses the row's own **Factuur** or
**Specificaties** button and reads the answer. Consequences, all deliberate:

- The `securitykey` is never read, logged, stored or reconstructed. Forging one
  would mean shipping Youfone's own crypto, which is not something a read-only
  tool should do — and it would break the next time they rotate it.
- A document has **no URL**. What is stored per document is a handle
  (`kind|tab|invoice number`), re-validated before it is ever acted on.
- Pressing a download button also routes the app to a viewer page, so it steps
  **back** to the list afterwards, with the browser's own back button.

## One tab

Youfone keeps its session in the **tab's own** `sessionStorage` (`_token`,
CryptoJS-encrypted). A new tab starts without it and is signed out. So:

- This app **attaches** to the tab you signed in with and never opens one. If
  it cannot find a MyYoufone tab it stops and says so.
- It **never navigates** — no `page.goto`, which would reboot the app. Getting
  around is done the way you do it: the app's own links, and the back button.

## How the invoices are found

`/facturen` is a redirect: Youfone asks which subscriptions you have and lands
you on the first one's tab (`/facturen/thuis` for internet/TV). Discovery walks
**every tab in the strip**, so an account with a SIM-only line as well gets both
invoice lists rather than whichever one you happened to leave open.

The table renders every invoice Youfone still serves — there is no pagination,
no "load more", and no year filter to walk.

> **Only the *Thuis* tab has been exercised against a real account.** The strip
> is read as router links, which is what Thuis renders. If Youfone renders a
> mobile line some other way, discovery reports one tab and covers one
> subscription — so the tab list is logged on every run, and
> `--diagnose` prints it. Two subscriptions and one tab in that log is a bug to
> report, not a clean run.

## Sign in, then run promptly

The session times out while idle, and there is no way to ask the server whether
it is still alive: that question would itself need the `securitykey`. So sign in
and start the run in the same sitting. A click that comes back without a PDF is
reported rather than saved; `resume` continues after you sign in again, and
nothing already downloaded is fetched twice.

## Setup / workflow

| Step | Windows | macOS / Linux | What it does |
|------|---------|---------------|--------------|
| 1 | `setup.bat` | `./setup.command` | Creates `.venv`, installs Playwright + the shared core |
| 2 | copy `config.example.json` → `config.json` | same | Defaults are fine |
| 3 | `login.bat` | `./login.command` | Opens a browser at Facturen; sign in, **leave it open** |
| 4 | `run_pilot.bat` | `./run_pilot.command` | The 5 newest documents, then **stops** for your inspection |
| 5 | inspect the PDFs/CSV | same | You approve before anything bigger runs |
| 6 | `run_all.bat` | `./run_all.command` | Everything available (asks for confirmation) |
| any time | `resume.bat` | `./resume.command` | Continue after an interruption; never redoes finished work |
| any time | `verify_documents.bat` | `./verify_documents.command` | Re-validate every indexed PDF |
| if it breaks | `diagnose.bat` | `./diagnose.command` | Dumps what the page shows. Downloads nothing |

Port **9243** keeps this separate from the other apps, so several signed-in
browsers can be open at once. `cdp_url` is **required** here, not optional:
there is no "let the app drive its own browser" mode.

### A second Youfone account

Each account needs its own browser, on its own port, because the session lives
in that browser. Copy `config.json` to `config.partner.json`, give it a
different `output_dir` and `cdp_url` port, then pass the label to any launcher:

```
login.bat partner          ./login.command partner
run_pilot.bat partner      ./run_pilot.command partner
```

Progress, PDFs and the CSV stay separate per account, and nothing already
downloaded is affected.

## Where files land

- `Statements\` — the invoices and their specifications
- `Manual Review\` — anything that failed validation

Filenames are `YYYY-MM-DD Youfone Factuur.pdf` / `... Specificatie.pdf`, dated
by the invoice date the table shows ("14 augustus 2026" → `2026-08-14`).

## What Youfone keeps, and for how long

**Six months.** That limit is Youfone's, not this app's: the page says so
("al je facturen van de laatste 6 maanden"), and the table shows everything it
has. Older invoices exist only behind **"Oudere facturen opvragen"**, which
opens a request with Youfone rather than serving a document — it is on the
blocklist and is never pressed.

Within that window nothing is capped: if Youfone shows it, this fetches it.

The practical conclusion: run this **at least twice a year**, or invoices are
gone from Youfone before they are ever archived.

Youfone issues no tax forms, so `Tax Documents\` is only created if one ever
unexpectedly arrives.

## Safety

- **Read-only, deny-by-default.** Two controls are ever pressed — a row's
  *Factuur* and *Specificaties* buttons — and both are checked against the
  guard by their actual label first. Paying, iDEAL, direct-debit and mandate
  changes, buying bundles or extra data, topping up, replacing or blocking a
  SIM, extending or cancelling the contract, changing your details or password,
  and signing out are all explicitly refused — and anything unrecognised is
  refused too.
- **The controls next to the downloads are the dangerous ones.** Under "Direct
  regelen" the Facturen page itself offers *Rekeningnummer wijzigen*,
  *Incassomoment aanpassen*, *BTW factuur aanvragen* and *Oudere facturen
  opvragen*. The last two contain the word "factuur", so the document allowlist
  alone would have waved them through; `opvragen|aanvragen` is on the blocklist
  precisely because of them.
- **Only two answers are ever read.** `Pdf/GetInvoice` and
  `Pdf/GetSpecification`, on `my.youfone.nl` over https, checked by parsed host.
  Anything else that comes back is reported, not saved.
- **An answer is checked before it becomes a file.** It has to decode as
  base64, start with `%PDF`, and name the invoice that was asked for — that
  last one is what stops a mismatched answer being filed under another month's
  date.
- **Routes are checked before they are opened.** Never a per-click detail route
  (`/facturen/i/<id>`), never an absolute URL — that would be a full page load,
  which reboots the app.
- You sign in; the tool never handles credentials.
- Sequential processing with polite randomized delays.
- Progress written atomically after every document; interrupted runs continue
  with `resume`.
- Existing PDFs are never overwritten.

## When Youfone changes its site

Everything provider-specific lives in **`youfone_site.py`** only. Run
`python youfone_docs.py --diagnose` while signed in to capture what the page
shows into `Diagnostics\`, then repair that one file. The diagnostics record the
tab strip, the rows per tab and how each document classified, so a restyle shows
up as a diff rather than as an empty run.

The most likely thing to break is the table's selectors: Youfone uses Angular
Material, so the rows are `tr.mat-mdc-row` and the cells are
`td.mat-column-date` / `-number` / `-invoice` / `-specification`. They are all
declared at the top of `youfone_site.py`.

## Tests

```
.venv\Scripts\activate
pytest
```
