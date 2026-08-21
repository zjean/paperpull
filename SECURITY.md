# Security & privacy

These tools sign in to **real financial and shopping accounts** and download
**real statements and receipts**. Treat this repository accordingly.

## What must never be committed

The `.gitignore` already blocks all of the following. Do not override it.

- **Browser profile folders** (`*-browser-profile/`) — these hold live logged-in
  sessions: cookies and auth tokens for your bank, brokerage, and Amazon. This
  is the single worst thing that could leak. Anyone with them can act as you.
- **`config.json` / `config.<account>.json`** — your real paths and account
  labels. Only the sanitized `config.example.json` is tracked.
- **Downloaded documents** (`*.pdf`, `*.zip`) — your actual financial records.
- **Runtime state** — `discovery.json`, `progress.json`, `*.log`, `*.csv`,
  `Diagnostics/` (which can contain screenshots of signed-in pages), `Backups/`.

## Before your first commit

1. Confirm nothing sensitive is staged: `git status` should show only source,
   docs, and `config.example.json`.
2. If you ever accidentally commit a secret, deleting it in a later commit is
   **not enough** — it stays in git history. Scrub history (e.g. with
   `git filter-repo`) or start a fresh repo.

## Design safety (what the tools themselves do)

- **Read-only.** There is no code anywhere that submits a form, confirms a
  dialog, moves money, or changes a setting. How that is enforced depends on
  how the provider exposes its documents:

  - **The statement apps** (Amex, Dominion, Navy Federal, RedCard, Robinhood,
    T-Mobile, USAA, Verizon, Wealthfront) click a download control, and gate it
    with `is_safe_control()`: a hard blocklist (`FORBIDDEN_CONTROL_RE` —
    buy/sell/transfer/pay/delete/change-setting/…) **plus** a document
    allowlist (`SAFE_DOC_CONTROL_RE`). A control must pass **both**, so
    anything unrecognised is refused — deny by default.
  - **The receipt apps** (Amazon, Target, Walmart) click a print/invoice
    control matched by a narrow pattern and screened against the same
    blocklist. There is no separate allowlist in these three, so the guard is
    blocklist-only.
  - **Gap** and **UKG** click nothing at all. Gap navigates to the order page
    and renders it; UKG reads its pay statements and PDFs from the same JSON
    API its own mobile app uses, over the ordinary session. On a site that can
    also change direct deposit and tax withholding, not activating a control
    is the strongest guarantee available - and UKG additionally refuses any
    URL whose path says `EDIT` rather than `VIEW`.
- **You sign in, not the tool.** The tools attach to a browser *you* logged into
  (via Chrome DevTools Protocol). They never handle your password or 2FA.
- **Local only** *(native install; see "Running it in Docker" below, which
  deliberately is not)*. The browser's debugging port and the GUI both listen on
  `127.0.0.1` (localhost) — nothing is exposed to your network. Note that while
  the signed-in browser is open, any program running **on your own machine**
  could attach to that debugging port, so close the browser window when you're
  done downloading. The GUI additionally refuses any request whose `Origin`/
  `Referer` is not localhost, so another website you have open cannot drive it.
- **Delete-safe.** A sticky `downloaded_ok` marker means deleting the PDFs after
  you import them elsewhere will not cause re-downloads.

## Running it in Docker

[docs/docker.md](docs/docker.md) describes a deployment where the control panel
and a signed-in Chrome are both reachable over your network through a reverse
proxy. That is a genuinely different threat model from everything above, and it
is worth stating plainly rather than leaving implied.

**What changes.** The guarantees that come from the *design* all still hold:
no credentials are stored, you still sign in yourself, 2FA is still yours, the
read-only control guard is untouched, and re-runs are still delete-safe. What
no longer holds is the one that came from the *deployment* — "nothing is exposed
to your network."

**What an attacker gets.** Two doors, and they are worth different amounts:

- **The browser desktop** is the serious one. It is a browser that is *already
  signed in* to your bank. Whoever reaches it does not need your password, does
  not face 2FA, and is not limited to reading — they get your session, with
  everything it can do. PaperPull's read-only guard protects you from
  PaperPull; it does not protect a browser from whoever is driving it.
- **The control panel** can start a downloader. It cannot move money, but it
  reveals which providers you use and can write to your output folders. It can
  also type a line into a run that has paused to ask something — which is what
  makes a mid-run sign-out recoverable here, and is limited to answering that
  question: the command line itself is built from a fixed list of actions, and
  nothing from a request reaches a shell.

**So, minimum precautions:**

- A **real password** in `BROWSER_PASSWORD` — treat it as a bank password,
  because functionally it is one. Never the example value.
- **Authentication in front of the panel**, which has none of its own.
  `docker/Caddyfile.example` includes a `basic_auth` block; use it.
- **TLS on both**, terminated at your proxy. The desktop streams your bank
  session; the panel streams your document names.
- **Do not expose either to the internet.** A VPN or tailnet is the right
  answer. If you must, put a second authentication factor in front of it.
- `PAPERPULL_ALLOWED_HOSTS` must list only the hostnames you actually serve the
  panel on. It is an exact-match allowlist, and it is what stops another site
  you have open from driving the panel. Naming a wildcard defeats it.

**Two files are as sensitive as the PDFs.** `./browser-profile` holds live
session cookies for every provider you have signed into — it is a
sign-in-as-you token in a directory. `./data` holds the statements themselves.
Both are gitignored and dockerignored; back them up somewhere encrypted, and
never put either in an image.

**Signing out matters more here.** On a native install the signed-in browser
closes when you close the window. This one stays running until you stop the
container. When you are done with a run of downloads, sign out on the desktop —
or `docker compose stop browser cdp-bridge` — rather than leaving an
authenticated bank session running behind one password indefinitely.

## Reporting

This is a personal-use project with no warranty. If you find a security issue,
open an issue describing it (without including any real credentials or data).
