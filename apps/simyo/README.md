# Simyo Facturen Downloader (local, supervised)

Downloads your monthly **invoices (facturen)** from
[mijn.simyo.nl](https://mijn.simyo.nl/facturen) and saves them as PDFs, plus an
index CSV:

- `Simyo Document Index.csv` — one row per downloaded PDF

Simyo is a Dutch SIM-only mobile provider (a KPN brand). Everything runs
**locally**. Nothing is sent to any external AI API or third-party service. You
sign in to Simyo **yourself**; the tool never touches your credentials.

> ⚠️ A Simyo invoice carries your name, home address, phone number, customer
> number, IBAN and SEPA mandate reference. The index CSV deliberately records
> none of that — only what is needed to find and verify a file locally. Never
> commit the PDFs, the CSV, or your `config.json`.

## One tab. Really.

This is the one rule that makes Simyo different from every other app here.

Mijn Simyo is a single-page app that keeps its session in the **tab's own**
`sessionStorage`. A second tab starts without it, its first API call comes back
`401`, and Simyo's own error handler answers a `401` by calling `signOff()` —
which POSTs `/auth/logout` and destroys the **server** session too. So opening
a second Mijn Simyo tab signs you out of the one you were using.

Consequences, all of them deliberate:

- This app **attaches** to the tab you signed in with and never opens one. If
  it cannot find a Mijn Simyo tab it stops and says so, rather than opening one
  and signing you out.
- It **never navigates** — no `page.goto`, not even to the invoice page.
- It **clicks nothing at all.** The invoice list and the PDFs both come from
  plain GETs on your session (below), which is also safer than using the page:
  a raw request that comes back `401` is reported, where the SPA's own fetch
  would have signed you out.

## Sign in, then run promptly

Simyo's session also **times out while idle**, and does it quietly: the tab
either redirects itself to `/inloggen?uitgelogd=2` or keeps showing the invoice
list you were looking at while the server session is already gone. So the page
is never taken as evidence — the app asks `/auth/is-logged-in` instead — and
the practical advice is to sign in and start the run in the same sitting. A run
that gets signed out halfway stops and asks you to sign in again; `resume`
continues it, and nothing already downloaded is fetched twice.

## How the invoices are found

Behind the SPA is the same JSON API its own Download button calls:

```
GET /api/get?endpoint=listAllPostpaid
    -> {"result": [{invoiceNumber, date, paymentStatus, concept, ...}]}

GET /api/get?endpoint=downloadPostpaidPdf&args=<invoiceNumber>
    -> application/pdf
```

One call returns the whole history. The page shows five rows and a **"Toon meer
facturen"** button, which looks like pagination hiding your older invoices — it
isn't. That call returned thirteen invoices while the page displayed five; the
button only flips a flag in the SPA to render rows it already has, and the
"Jaar" filter does the same. So nothing is missed by not clicking it.

The **running month** is skipped: it arrives as `invoiceNumber: "current"` with
`concept: true`, Simyo's own UI hides its download button, and the PDF endpoint
answers HTTP 500 for it.

## Setup / workflow

| Step | Windows | macOS / Linux | What it does |
|------|---------|---------------|--------------|
| 1 | `setup.bat` | `./setup.command` | Creates `.venv`, installs Playwright + the shared core |
| 2 | copy `config.example.json` → `config.json` | same | Defaults are fine |
| 3 | `login.bat` | `./login.command` | Opens a browser at Facturen; sign in, **leave it open** |
| 4 | `run_pilot.bat` | `./run_pilot.command` | The 5 newest invoices, then **stops** for your inspection |
| 5 | inspect the PDFs/CSV | same | You approve before anything bigger runs |
| 6 | `run_all.bat` | `./run_all.command` | Everything available (asks for confirmation) |
| any time | `resume.bat` | `./resume.command` | Continue after an interruption; never redoes finished work |
| any time | `verify_documents.bat` | `./verify_documents.command` | Re-validate every indexed PDF |
| if it breaks | `diagnose.bat` | `./diagnose.command` | Dumps what the API returned. Downloads nothing |

Port **9237** keeps this separate from the other apps, so several signed-in
browsers can be open at once. `cdp_url` is **required** here, not optional:
there is no "let the app drive its own browser" mode, because that mode would
have to navigate.

### A second Simyo account

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

- `Statements\` — the invoices
- `Manual Review\` — anything that failed validation

Filenames are `YYYY-MM-DD Simyo Factuur.pdf`, dated by the **invoice period**
— the month the portal labels the row with (`Juni 2026` → `2026-06-01`). Note
that the *Factuurdatum* printed inside the PDF is about five weeks later; the
period is used because it is what you see on screen and what makes a monthly
archive sort correctly.

## What Simyo keeps, and for how long

Roughly the last twelve months, and that limit is Simyo's, not this app's. The
Facturen page says so itself ("alleen je betalingen tot een jaar geleden"), the
year filter offers only the years that survive, and an account whose SEPA
mandate dates from November 2024 is offered nothing older than the following
August. There is no endpoint, button or year parameter that brings the rest
back — once Simyo drops an invoice, the portal no longer has it.

Within that window nothing is capped: if Simyo serves the PDF, this fetches it.
The **BTW specification** inside the PDF is dropped after six months (EU
rules), so the oldest invoices — the ones flagged `isOutdated` — still download
but carry less detail.

The practical conclusion: run this at least a couple of times a year, or the
older invoices are gone from Simyo before they are ever archived.

Simyo issues no tax forms, so `Tax Documents\` is only created if one ever
unexpectedly arrives. Prepaid accounts are a different product with their own
endpoints and are **not** read yet.

## Safety

- **Read-only, deny-by-default.** Nothing is clicked in normal operation at
  all. The guard exists anyway, because a future repair might reach for a
  control and on a telecom portal it should have to argue with a blocklist
  first: paying, iDEAL, direct-debit and mandate changes, buying bundles or
  extra data, topping up, replacing or blocking a SIM, extending or cancelling
  the contract, changing your details or password, and signing out are all
  explicitly refused — and anything unrecognised is refused too.
- **The URL guard is an allowlist of two endpoints.** Simyo's whole API is one
  path with the verb in a query parameter, so `/api/get?endpoint=...` is
  checked against exactly `listAllPostpaid` and `downloadPostpaidPdf`.
  `/api/post`, `/api/put`, `/api/delete` and everything else — including
  `createIdealPaymentRequestForInvoice` and `sessionLogout` — are refused.
- Each invoice row carries a `payURL`: a pre-authenticated one-click payment
  link. It is never followed and never stored.
- You sign in; the tool never handles credentials.
- Sequential processing with polite randomized delays.
- Progress written atomically after every document; interrupted runs continue
  with `resume`.
- Existing PDFs are never overwritten.

## When Simyo changes its site

Everything provider-specific lives in **`simyo_site.py`** only. Run
`python simyo_docs.py --diagnose` while signed in to capture what the API
returns into `Diagnostics\`, then repair that one file. The diagnostics record
the API's own field names, so a rename shows up as a diff rather than as an
empty run.

## Tests

```
.venv\Scripts\activate
pytest
```
