# PaperPull

![Version](https://img.shields.io/github/v/tag/rheeloaded/paperpull?sort=semver&label=version&color=blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)
![Platforms](https://img.shields.io/badge/platforms-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)
[![Support on Ko-fi](https://img.shields.io/badge/Ko--fi-support%20this%20project-FF5E5B?logo=ko-fi&logoColor=white)](https://ko-fi.com/rheeloaded)

**Receipt & Statement Downloader** — a family of small, **read-only** tools that log in *alongside you* to your own
accounts and download your **statements and receipts** as PDFs — so you can
archive them (e.g. into [paperless-ngx](https://docs.paperless-ngx.com/)) instead
of clicking through each site by hand.

Runs on **Windows and macOS** (and Linux), with the same commands on each.

Sixteen providers are supported today, all built on the same pattern:

| App | Provider | Documents | Notes |
|-----|----------|-----------|-------|
| [`ally`](apps/ally) | Ally Bank | Account statements, tax forms | JSON API; same-dated statements named from the PDF |
| [`amazon`](apps/amazon) | Amazon | Order invoices (full history) | Per-year order pagination |
| [`amex`](apps/amex) | American Express | Statements, Year-End Summary | Click-nav SPA; in-memory session |
| [`chase`](apps/chase) | Chase (credit cards) | Card statements | Real Edge/Chrome; per-card accordions + year picker |
| [`dominion`](apps/dominion) | Dominion Energy (VA) | Billing statements | Paginated MUI accordion; ~18-month limit |
| [`gap`](apps/gap) | Gap Inc. (Gap, Old Navy, Banana Republic, Athleta) | Order receipts | Lazy-loading history; ~13-month limit |
| [`navyfederal`](apps/navyfederal) | Navy Federal CU | Account statements | Per-account accordions; blob-tab PDFs |
| [`redcard`](apps/redcard) | Target RedCard / Circle Card (TD Bank) | Billing statements | Statements table; per-year switcher |
| [`robinhood`](apps/robinhood) | Robinhood | Account statements, tax docs | "View More" pagination |
| [`target`](apps/target) | Target | Receipts (Online + In-Store) | Print-capture |
| [`tmobile`](apps/tmobile) | T-Mobile | Bill statements | Bill-history page; detailed-bill download |
| [`ukg`](apps/ukg) | UKG Pro / UltiPro | **Pay statements** | Per-employer tenant; JSON-API, nothing clicked |
| [`usaa`](apps/usaa) | USAA | Statements | JSON-API enumeration |
| [`verizon`](apps/verizon) | Verizon (Fios) | Bill statements | Real Edge (bot block); dropdown + CDP download |
| [`walmart`](apps/walmart) | Walmart | Receipts | Hardened against bot detection |
| [`wealthfront`](apps/wealthfront) | Wealthfront | Statements, tax docs | |

> ⚠️ **Read this first:** these tools drive real, signed-in financial accounts.
> See [SECURITY.md](SECURITY.md) before you run *or* publish anything. In short:
> never commit your `*-browser-profile/` folder, your `config.json`, or any
> downloaded PDF. The `.gitignore` blocks them — don't override it.

## How it works (the shared design)

### The one decision everything follows from

Your documents live on the provider's site, and it will only hand them to a
browser that is already signed in. So PaperPull never tries to *be* you — it
works *beside* you. You sign in yourself, in a real browser window, and the
tool attaches to that window afterwards and reads.

```mermaid
flowchart TB
    you(["You"]) -->|"sign in · 2FA · device approval"| br["A real browser window<br/>its own profile · its own debugging port"]
    br -.->|"attaches over CDP — reads, never authenticates"| app
    subgraph app ["One app = one provider"]
        orch["Orchestrator<br/>discover → download → verify<br/>the same in all sixteen apps"]
        site["provider_site.py<br/>selectors · URLs · download quirks"]
        core["paperpull-core<br/>naming · filing · state · CSV · PDF checks"]
        orch --> site
        orch --> core
    end
    app --> out[("Your folders<br/>PDFs + an index CSV")]
```

That single choice is why there is no password anywhere in this project, why
2FA and device approvals are never an obstacle, and why a provider tightening
its login breaks nothing here.

In practice that first step is `login.bat` (or `./login.command`), which opens
the browser for you — a plain Chromium for most apps, or your own installed
Edge/Chrome for the few sites whose bot detection turns a fresh Chromium away
(Walmart, Verizon). Each app gets its own profile and its own debugging port,
so several signed-in browsers can sit open at once without colliding.

**Everything a provider knows lives in one file.** `provider_site.py` holds
every selector, URL and download quirk for that site. The orchestrator around
it is the same in all sixteen apps, and `paperpull-core` underneath it is
shared. When a provider redesigns, the repair is one file — never a rewrite,
and never a change to how documents get named, filed or tracked.

### What one run actually does

```mermaid
flowchart TB
    D["Discover<br/>list what the provider still has"] --> Q{"Already downloaded?"}
    Q -->|yes| S["Skip it"]
    Q -->|no| DL["Download the PDF"]
    DL --> V{"Is it a real PDF?"}
    V -->|no| MR["Manual Review<br/>flagged, never silently lost"]
    V -->|yes| F["Classify, name, file<br/>+ append to the index CSV"]
    F --> OK["Mark downloaded_ok<br/>sticky — survives deletion"]
```

Three plain-text files carry the state, and you can read all of them:

| File | Holds |
|------|-------|
| `discovery.json` | what the provider showed us this run |
| `progress.json` | what happened to each document — including the sticky `downloaded_ok` |
| `<Provider> Index.csv` | one row per saved document, for humans and spreadsheets |

That last step is what makes a re-run safe. `downloaded_ok` is keyed to the
document, not to the file on disk — so you can import everything into
paperless-ngx, delete the PDFs, and the next run still skips them. It only
fetches what is genuinely new, and lists it in `new-this-run.txt`.

### Read-only by construction

Nothing that buys, sells, transfers, pays, deletes, or changes a setting is
ever clicked, and all site interaction lives in `provider_site.py` where it can
be read in one sitting. The statement apps enforce this deny-by-default — a
control must clear a blocklist (`FORBIDDEN_CONTROL_RE`) *and* match a document
allowlist (`SAFE_DOC_CONTROL_RE`). The receipt apps screen a narrow
print/invoice pattern against the blocklist. Gap and UKG click nothing at all.
[SECURITY.md](SECURITY.md) spells out which app does which.

### One app, more than one person

A `--config config.<name>.json` flag lets one app serve a second person's
account with its own profile, port and output folders, so no data mixes. The
launchers take the account label as an argument (`login.bat spouse` /
`./login.command spouse`).

## Deploy it to a server

**This fork's supported path.** The control panel becomes a web UI you reach
from any device, and the sign-in browser becomes a real Google Chrome with a web
desktop in its own container. You still sign in yourself; PaperPull still
attaches afterwards over the DevTools protocol and never sees a password.

Two containers, behind your own reverse proxy:

| Service | Is | Proxy it as |
|---|---|---|
| `browser` | real Google Chrome + a web desktop. **You sign in here.** | `browser.<you>` → `browser:3000` |
| `paperpull` | the control panel and all sixteen apps | `paperpull.<you>` → `paperpull:8765` |

> ⚠️ **Read the Docker section of [SECURITY.md](SECURITY.md) first.** This puts a
> browser that is *already signed in to your bank* on your network. Whoever
> reaches it needs no password and faces no 2FA. That is a different threat
> model from a localhost-only install, and it deserves a real password, real
> auth in front of the panel, and no exposure to the open internet.

### What you need

- Docker with Compose v2, on **amd64 or arm64** (both are built).
- A reverse proxy on a Docker network named `proxy`.
- **~6 GB of disk** for the images — the browser one is 4.5 GB, because it
  contains a real Chrome and a desktop.
- Two DNS names pointing at the server.

### 1. Publish the images (once)

CI builds them, but nothing exists until a branch is pushed:

```bash
git push -u origin develop     # builds ghcr.io/zjean/paperpull:beta
# happy with :beta? then
git switch main && git merge develop && git push   # builds :latest
```

**Then make the two packages public**, or the server cannot pull them. GHCR
publishes as *private* even from a public repo, and a `docker compose pull` that
fails with `denied` or `unauthorized` is almost always this:

> github.com/zjean?tab=packages → `paperpull` → Package settings → Change
> visibility → Public. Repeat for `paperpull-browser`.

Prefer to keep them private? Then on the server, once:

```bash
echo "$GHCR_READ_TOKEN" | docker login ghcr.io -u zjean --password-stdin
```
with a classic PAT carrying only `read:packages`.

### 2. Set it up on the server

```bash
git clone https://github.com/zjean/paperpull.git /opt/paperpull
cd /opt/paperpull

# Bind-mount directories must be writable by uid 1000 before the first start.
# Docker would otherwise create them as root, and both containers run as 1000.
mkdir -p config data browser-profile
sudo chown -R 1000:1000 config data browser-profile

cp .env.example .env
$EDITOR .env
```

Fill in at least these:

```ini
BROWSER_PASSWORD=<a real password — it guards a signed-in bank session>
PAPERPULL_ALLOWED_HOSTS=paperpull.example.com
PAPERPULL_BROWSER_URL=https://browser.example.com/
TZ=Europe/Amsterdam
```

`PAPERPULL_ALLOWED_HOSTS` is not optional: the panel refuses any request whose
`Origin` it does not recognise, so without your hostname there every click
returns 403. It is an exact-match allowlist, which is what stops another site
you have open in the same browser from driving the panel.

Then:

```bash
docker network create proxy      # skip if your proxy already has one
docker compose up -d
```

### 3. Point your proxy at it

Copy the two blocks from [`docker/Caddyfile.example`](docker/Caddyfile.example),
substituting your hostnames. Two settings there are load-bearing rather than
taste:

- **`flush_interval -1`** on the panel. It streams a run's output as
  server-sent events, and a buffering proxy turns that into a long silence
  followed by everything at once.
- **`basic_auth`** on the panel. It has no login of its own.

Caddy proxies the desktop's websockets with no extra configuration. If you use
nginx or Nginx Proxy Manager instead, enable websocket support explicitly.

### 4. First run

The browser needs a minute after a cold start — the desktop comes up, then
Chrome. Until then the panel reports it cannot connect, which is impatience
rather than an error.

1. Open **`browser.<you>`** and log in with `BROWSER_USER` / `BROWSER_PASSWORD`.
   You get a real Chrome. Sign in to a provider — 2FA, device approval, bank
   app, DigiD, whatever it takes — and **leave the tab open**.
2. Open **`paperpull.<you>`**, pick that app, press **Login**. It should report
   that it connected and can see your documents.
3. **Pilot** downloads the newest few. Then **Run All**.

Sign in to as many providers as you like in that one Chrome; each app finds its
own tab. Your PDFs land in `./data/<app>/`, next to that app's `progress.json`
and index CSV.

Confirm the plumbing whenever you change the stack:

```bash
docker compose exec paperpull python /app/tools/docker_smoke.py
```

### Running it

```bash
docker compose logs -f paperpull            # what a run is doing
docker compose pull && docker compose up -d # update
docker compose restart browser              # that's all — nothing else needs it
```

Rolling back is why every build also gets a `:sha-<short>` tag — put one in
`PAPERPULL_IMAGE` and `up -d`.

**Back up `./browser-profile` and `./data`.** The first holds live session
cookies for every provider you have signed into, so losing it means signing in
everywhere again — and it is as sensitive as a password. The second holds your
statements. Neither is committable; `.gitignore` and `.dockerignore` block both.

### When something is wrong

| Symptom | Cause |
|---|---|
| `denied` / `unauthorized` on pull | GHCR packages are still private — step 1. |
| Container exits: `/config is not writable` | The `chown -R 1000:1000` in step 2 was skipped. |
| Every panel click returns 403 | Your hostname is missing from `PAPERPULL_ALLOWED_HOSTS`. |
| Panel shows nothing during a run, then everything | The proxy is buffering; `flush_interval -1`. |
| **Login** says it cannot connect | Give the browser a minute. If it persists, `docker compose logs browser`. |
| Downloads are 0 bytes | The `pw-artifacts` volume or the uid match broke — gotcha #4 in [docs/docker.md](docs/docker.md). |

Full reference, including the five silent Chrome and Playwright behaviours this
design works around: **[docs/docker.md](docs/docker.md)**.

This fork tracks [rheeloaded/paperpull](https://github.com/rheeloaded/paperpull)
for new providers — see [docs/upstream.md](docs/upstream.md).

## Quick start (native)

> The `.bat` / `.command` launchers below are upstream's, and still work, but
> are **unmaintained in this fork**. Docker is the supported path.


![Quick start](docs/quickstart.gif)

**One-shot setup** (creates a venv for every app + the GUI, installs the browser):

```bat
setup-all.bat        REM Windows
```

```bash
./setup-all.command  # macOS / Linux
```

Then either drive everything from the **[GUI control panel](gui)** — pick an
app and account, click an action, and watch the live output:

```bat
gui\run_gui.bat
```

![PaperPull control panel](docs/control-panel.gif)

…or run a single app directly (using `amex` as the example):

```bat
cd apps\amex
copy config.example.json config.json    REM then edit paths as needed
login.bat                 REM opens Chromium — sign in yourself, leave it OPEN
run_pilot.bat             REM download the newest few as a test
run_all.bat               REM download everything available
```

Each app also has its own README with provider-specific details and quirks.
(Prefer to set apps up one at a time? Each has its own `setup.bat` / `setup.command`.)

## Windows and macOS

One download covers both. Every app ships two launchers with the same names
and the same behaviour — `.bat` for Windows, `.command` for macOS and Linux —
so the instructions in this README and in each app's own README apply
wherever you are:

| Task | Windows | macOS / Linux |
|------|---------|---------------|
| One-shot setup | `setup-all.bat` | `./setup-all.command` |
| Set up one app | `setup.bat` | `./setup.command` |
| Sign in | `login.bat` | `./login.command` |
| Test run | `run_pilot.bat` | `./run_pilot.command` |
| Full run | `run_all.bat` | `./run_all.command` |
| Control panel | `gui\run_gui.bat` | `gui/run_gui.command` |

A second account is the same on both: `run_all.bat spouse` /
`./run_all.command spouse`.

Only one thing genuinely differs. macOS keeps Playwright's browser inside an
app bundle and in a different cache directory, and a couple of providers need
a branded Edge/Chrome to get past their bot protection — that lookup lives in
`paperpull_core.browser` and is handled for you.

### Getting it onto a Mac

**`git clone` is the smoothest route** — it preserves the scripts' executable
bit and macOS does not quarantine it.

If you download a release archive instead, prefer the **`.tar.gz`**: it keeps
the executable bit, while a `.zip` drops it. After unpacking a download,
macOS may also quarantine the scripts, so a double-click reports *"cannot be
opened because it is from an unidentified developer."* Both are cleared in one
go:

```bash
xattr -dr com.apple.quarantine .
chmod +x setup-all.command apps/*/*.command gui/*.command
```

## Requirements

- **Windows, macOS, or Linux**
- Python 3.11+
- Playwright (installed per app by the setup script)

## Contributing — add your provider

No one has accounts everywhere, so **PaperPull grows when people add the
providers they use.** If a bank, card, brokerage, utility, telecom, or retailer
you use isn't here yet, you're the ideal person to add it:

- 📖 **[Adding a provider](docs/adding-a-provider.md)** — a step-by-step guide
  (clone the closest app, rewrite one file, stay read-only, test, submit).
- 📋 **[PROVIDERS.md](PROVIDERS.md)** — what's supported and what's requested;
  claim one so nobody builds it twice.
- 📥 Can't build it yourself? [Request a provider](https://github.com/rheeloaded/paperpull/issues/new/choose)
  and someone with that account may pick it up.

Every contribution keeps the **read-only, local, no-credentials** design — see
[CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).

## Status & roadmap

- ✅ All **sixteen** apps work and are in regular use.
- 🔜 **More providers:** community-driven — see [PROVIDERS.md](PROVIDERS.md).
- 🔜 **Scheduled/assisted runs:** a monthly "nudge + sweep" (e.g. the 1st) that
  opens the login browsers and then runs discover + resume across every app once
  you've signed in — delete-safe, so it only grabs what's new. Fully unattended
  runs stay out of scope by design: the tools never store credentials or bypass
  2FA, so a human sign-in stays in the loop (long-session retailer apps may
  tolerate more automation than banks/cards).
- ✅ **Shared core:** the support code the apps used to duplicate now lives once
  in [`core/`](core) as `paperpull-core`. An app declares an `AppSpec` — its
  folders, routing, CSV columns and config defaults — and keeps only its
  orchestrator and its `*_site.py`. `tools/check_installs.py` reports whether
  your installs have drifted from the repo.

## Support

If PaperPull saves you time, you can support its development on Ko-fi:
**[ko-fi.com/rheeloaded](https://ko-fi.com/rheeloaded)** ☕. Entirely optional and
much appreciated — it doesn't change anything below.

## Legal

This project is for **personal archival of your own records**. It is not
affiliated with, endorsed by, or sponsored by any of the companies listed.
All product names and trademarks are the property of their respective owners.
Automating access to a website may be restricted by that site's Terms of
Service — you are responsible for how you use these tools. Provided **as-is,
without warranty of any kind** (see [LICENSE](LICENSE)).
