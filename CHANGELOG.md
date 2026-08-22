# Changelog

All notable changes to PaperPull are recorded here. Versioning follows
[Semantic Versioning](https://semver.org):

- **PATCH** — bug fixes, or repairing an app after a provider changes its site
- **MINOR** — a new app, or a cross-app feature
- **MAJOR** — breaking changes (repo layout, config format, removing an app)

## [Unreleased]

### Changed
- **The control panel is now about your accounts, not about flags.** It used to
  answer one question — "which flag do you want to pass to which app?" — with
  two dropdowns over eighteen apps and six identically-styled buttons reading
  Login / Discover / Pilot / Run All / Resume / Verify. Nothing on the page
  said what any of them did (the word "Pilot" was explained nowhere at all),
  nothing said which to press first, and two of the six were filled the same
  blue, so "primary" had stopped meaning anything. The question a person
  actually opens the panel with — *what needs me?* — had no answer on screen:
  an account's session state was a suffix inside an `<option>`, so learning it
  meant selecting all eighteen apps in turn while 85% of the window sat empty.

  What replaced it:

  - **A register of every account**, one line each, grouped by what it needs
    (*Needs a sign-in* → *Needs confirming* → *Due* → *Nothing to do*) and
    ordered most-perishable-first inside each group, the same rule
    `paperpull_core.due` sorts by. The first row is the one to open, so the
    panel opens it. The masthead counts them.
  - **Every action says what it does**, in a sentence, on the button. The three
    that form the ordinary path through a provider (**Sign in** → **Try a few**
    → **Download everything new**) are numbered, because they genuinely are a
    sequence; **Confirm this account** joins them as step 2 for the two apps
    that have an identity gate. The rest fold away under *Other things you can
    run*. Exactly one action is ever highlighted, and only when there is a real
    next step for that account.
  - **A session meter.** An account whose provider declares a session lifetime
    shows how much of it is left, counting down. A provider that declares none
    shows no meter rather than a full one — those are different facts.
  - **The sitting is on the page.** It was three chained `confirm()` dialogs,
    so the queue could not be reviewed before it started or read while it ran,
    and `alert()` reported the result. It is now a panel with the queue, where
    it has got to, and the two buttons that drive it.
  - **Providers you have never set up are out of the register**, and behind a
    **+ Add an account** button instead. They were seventeen of eighteen rows
    on a fresh checkout, each reported as a thing wrong with your install, and
    most of them are providers a given person will never use.
  - **Buttons renamed** to what they do rather than what they pass: Login →
    **Sign in**, Pilot → **Try a few**, Run All → **Download everything new**,
    Discover → **List what is there**, Resume → **Continue last run**, Verify →
    **Re-check saved files**, Adopt identity → **Confirm this account**.

- **`gui/app.py` is a backend again.** The page moved out of a 400-line quoted
  string in the middle of the request handlers into `gui/static/`
  (`index.html`, `panel.css`, `panel.js`), served `no-store` and read per
  request, so editing the page is a reload rather than a restart.

### Added
- **`GET /api/state`** — everything the page draws itself from, in one request:
  the flat account list with its bucket, session, identity and dates already
  resolved, plus every action's label and blurb. One endpoint rather than the
  page cross-joining `/api/apps` and `/api/due` itself, because the buckets are
  derived from both and computing them twice is how a sidebar starts
  disagreeing with the detail view beside it. It reports `due_known: false`
  when `paperpull_core` is not importable, and the page says so plainly — an
  empty due list would read as "nothing is due", a different and false claim
  from "this panel cannot tell you".
- **`POST /api/accounts`** — creates one account's config file, and the
  panel's only write. Two cases, one operation: a provider you have never set
  up (copy its tracked `config.example.json`), and a second person's account of
  one you already use (copy *your* config, then give the new account its own
  `output_dir`, its own `profile_dir` inside it, and natively its own debugging
  port). That last part is not a new rule — it is what each app's own
  `add_account.py` does and what the READMEs promise, and it matters because
  two accounts sharing an `output_dir` share `progress.json` and
  `sentinel.json`, so each keeps overwriting the other's record of what it had
  downloaded and whether it was signed in. In the container the port is left
  alone, because there is one browser and every app's `cdp_url` points at it on
  purpose; the response says so. The app name is checked against the apps
  actually discovered rather than pattern-matched, the label is refused rather
  than rewritten (lowercase `[a-z0-9_-]`, 1–32 chars — `config.Spouse.json` and
  `config.spouse.json` are one file on macOS and two on Linux), and an existing
  config is never overwritten.
- **A warning when two accounts of one provider share a browser.** Nothing
  anywhere warned about this before — not the apps, not the scheduler, not the
  panel. Every app finds its tab by matching the provider's host and takes the
  first one, nothing ties that choice to a config, and the account holder
  stamped on every PDF and CSV row comes from the config rather than the page —
  so a run that reads the wrong tab files those documents under the wrong
  person. `simyo` and `youfone` catch it (`paperpull_core.identity` refuses);
  the other sixteen apps do not check. Natively it cannot happen by accident,
  because every account gets its own browser on its own port; in the container
  it is the default. `/api/state` now reports, per account, which other
  accounts of the same app attach to the same browser, and the page marks them
  in the register and explains it on the account. Two *different* providers on
  one browser is the intended container layout and is not flagged.
- **`gui/tests/test_add_account.py`** — 45 tests over that route: every refusal,
  every field it derives, and the shared-browser detection in both directions.
- **`gui/tests/test_state.py`** — the buckets, the ordering, the degraded
  no-core case, the page's own files, and a test that no action can ship as a
  bare verb with no sentence explaining it. That last one is the regression
  this redesign exists to prevent.
- An inline SVG favicon. There is no `/favicon.ico` route, so the browser
  asked for one on every page load and logged a 404 in the console on every
  one. A `data:` URI also keeps the promise the rest of the page makes: a panel
  driving signed-in financial accounts fetches nothing from anywhere, and an
  icon file is still a request.
- `docs/control-panel.png`, a current still of the panel.

### Fixed
- **The identity warning no longer has two ways to lie.** `identity_gated` is
  now decided server-side from the app's own accepted flags, so the sixteen
  apps with no identity gate cannot land in the *Needs confirming* bucket, and
  neither `identityText` nor `nextStep` can point their users at a button that
  does not exist for them.
- **"Never seen signed in" is no longer printed in the same vermilion as a
  session the provider killed.** An absence is not a failure.
- The register no longer prints "Nothing to do" on every one of sixteen rows
  under a heading that already says it.

## [0.11.0] — 2026-08-22

### Added
- **An ntfy notification from the scheduler, when an unattended pass finds
  something a person has to act on.** Until now nothing in PaperPull ever sent
  anything anywhere; this is the first outbound call in the project, made only
  by the `scheduler` container. It fires when a pass parks an account (dead
  session, no signed-in tab, a security challenge, an unprovable identity), a
  perishable account (Simyo) is due — it is never started unattended, so it
  produces no exit code and no sentinel state, but it is exactly the case this
  feature exists for — or an account exits non-zero, including exit 4 (a busy
  provider) and the pass itself raising. One digest per pass, only when there
  is something to say: a clean run, a quiet run, exit 143 (stopped), an app
  skipped for lacking `--unattended`, and anything a person started themselves
  from the panel or a terminal all stay silent, deliberately — a push about
  something already on your screen is noise, not signal.

  A message names the provider, the account label, and the reason —
  `youfone/primary - no signed-in tab` — and nothing else: never a document,
  an amount, a customer number, or anything read off a page. On ntfy.sh a
  topic has no authentication and **the topic name is the credential** —
  anyone who learns it can read every message ever sent to it, which is enough
  to know which providers you use. `PAPERPULL_NTFY_TOKEN` exists for a topic
  that requires a bearer token, but nothing requires you to set it, and an
  unguessable topic name (or a server of your own) is the actual protection.
  `PAPERPULL_NTFY_URL` is added to the `scheduler` service and to
  `.env.example`; empty is the default, and empty means nothing is ever sent.

  `core/paperpull_core/notify.py` is the new module: it never raises, never
  blocks long, and does nothing when unconfigured. Standard library only
  (`urllib.request`) — no new dependency.

### Changed
- `tools/schedule.py --once` now exits non-zero when the pass it ran raised,
  rather than always returning 0. Consistent with the rest of the project's
  "non-zero means broken" rule, but a cron wrapper written against the old
  behavior would need to account for it.

## [0.10.0] — 2026-08-21

### Added
- **The identity gate, `--unattended`, and the provider lock, on Youfone.**
  Phase 1 of 0.9.0's multi-account work covered Simyo only; this is the
  second provider. `--discover --adopt-identity` records which account a
  config's tab belongs to, the panel's **Adopt identity** button now works
  for Youfone too, and `--unattended --all --yes` parks a dead session,
  missing tab, security challenge or unprovable identity and exits 0 instead
  of asking.

  Youfone is not Simyo, and the port says so where it differs rather than
  copying Simyo's constraints across: navigation is expected here (the SPA's
  own router links, verified to keep the session), not forbidden; each
  invoice's Factuur and Specificaties share one invoice number, which
  `paperpull_core.identity.pick_anchors`'s existing id-keyed dedup collapses
  to a single anchor (verified with dedicated tests, not assumed); and
  Youfone's history stops at six months rather than Simyo's twelve, so
  anchors age out roughly twice as fast — an account left unrun that long
  will need `--adopt-identity` run again, which is correct, not a defect.
  `session_lifetime_minutes` stays `None` and `concurrency` stays at its
  default of 1, both recorded as stated, unverified assumptions rather than
  guessed facts — nobody has confirmed Youfone's idle timeout or whether it
  tolerates two live sessions.

  Simyo and Youfone only; the other 16 apps behave as before.

### Fixed
- **The panel offered "Adopt identity" to all 18 apps, including the 16 that
  cannot accept it.** `ACTIONS` is one global table with no per-app gate, so
  pressing the button on any app other than Simyo reached that script's own
  argparse and failed with "unrecognized arguments" instead of doing
  nothing. `gui/app.py`'s new `_supported_actions` reads each entry script's
  own text — the same trick `_login_flag` already used for `--open-browser`
  and `tools/schedule.py` uses for `--unattended` — and the button is now
  hidden per app instead of shown and failing; `_build_cmd` refuses the same
  way server-side, behind the hidden button.

  The account list had the same shape of bug one layer up: the "no identity
  recorded... press Adopt identity" warning and the "· unidentified" label
  were shown for every account of every app whenever `identified` was false
  — which, for the 16 apps that never call `ensure_identity`, is always.
  Hiding the button without also fixing this would have told a user to press
  a button that no longer exists. Both are now shown only for an app that
  actually has the gate.

## [0.9.0] — 2026-08-21

### Added
- **Several accounts per provider, on a schedule, storing no credential.**
  A `sentinel.json` beside each account's `progress.json` records two things
  that cannot log in to anything: which account a browser tab belongs to (a
  fingerprint of documents that account is known to own) and whether its
  session was alive the last time anything looked. From those,
  `paperpull_core.due` answers who is worth running today and in what order,
  `tools/due.py` and the panel's `/api/due` print it, and `tools/schedule.py`
  runs the accounts that can run themselves.

  The question that started this was "where do the passwords live", and the
  answer is that there are none. A session here is perishable and only a human
  can renew it — Simyo signs out after ten minutes idle, and a second tab
  signs out the first — so a stored password would buy an unattended
  *re-login*, which is the one thing this project exists not to do. What is
  scheduled instead is the human: providers whose sessions last for days run
  unattended, and the ones that do not are queued for a sitting,
  most-perishable-first. `docs/adr/2026-08-21-no-stored-credentials.md`
  records the decision and the six conditions any future change would have to
  meet in one go.

- **An identity gate on Simyo.** In the Docker layout one Chrome holds every
  provider's session and a tab is found by matching the provider's host, so
  with two Simyo accounts signed in a run could read the wrong tab and file
  its invoices under the other account's owner — silently, because the owner
  comes from the config and never from the page. A run now refuses unless it
  can prove the tab is this config's account. Adopting that proof is a
  deliberate, watched, once-per-account act: `--discover --adopt-identity`, or
  the panel's new **Adopt identity** button, which then shows what it recorded
  so it can be checked. Simyo only; the other 17 apps behave as before.

- **One live session per provider, enforced in a file.** `AppSpec.concurrency`
  declares how many sessions a provider tolerates and
  `paperpull_core.locks` holds them on disk, so the rule survives a panel
  restart and also covers someone running a downloader straight from a
  terminal. A lock older than six hours is taken over, because a pid means
  nothing across containers.

- **`--unattended`**, on Simyo so far: a dead session, a missing tab, a
  security challenge or an unprovable identity park the account and exit 0
  instead of asking a question nobody is present to answer. A parked account
  is a state, not a failure, and it stays at the top of the due list until a
  person deals with it.

### Fixed
- **Neither the scheduler nor the panel's sitting could ever download a new
  document.** Both ran `resume`, which selects from the `discovery.json` an
  account already has and never asks the provider what exists — `cmd_discover`
  is reached from `--pilot` and `--all` only. So a sitting spent the sign-in
  it had just asked a person for, printed "Nothing to resume", and reported
  itself done; the scheduler had no working unattended path at all. Both now
  run `--all` (with `--yes`, which answers the confirmation that was the only
  reason `--all` needed a person), and `--unattended` admits that combination.
  Nothing is re-fetched: each app's own already-downloaded memory makes a pass
  discover-plus-new-only.

  A resume that finds nothing now still verifies the session first, so
  `last_verified_alive` is written on that path too. Without it the account
  looked unchecked, came back due tomorrow, and asked for a sign-in daily,
  forever, to do nothing.

- **One Stop, or one closed browser tab, wedged the panel's Run button for
  that provider.** Nothing caught `SIGTERM`, so a run died without releasing
  its session slot, and the panel counted every lock file it found with no
  staleness rule — refusing that provider for six hours while a terminal
  three feet away ran the same account fine. The apps now release the slot on
  `SIGTERM`, and the panel applies the same ageing rule the CLI does.

- **The panel and the CLI computed different lock directories**, so neither
  could see the other's lock and the guard silently guarded nothing. Both were
  deriving a per-provider invariant from `output_dir`, which is per account and
  chosen by the user; it now comes from `PAPERPULL_DATA_ROOT` or the app's own
  directory, once, in `paperpull_core.appload`.

- **`appload` did not resolve a relative `output_dir`**, and all 18 shipped
  `config.example.json` files say `"output_dir": "."`. Every account then
  reported no documents and no park record, so natively every account was
  always due and none was ever parked — while the panel showed the opposite
  about the same account.

- **A parked account vanished from the due list for the rest of the day.**
  Parking does not clear `last_verified_alive`, and the due check dropped
  anything already looked at today — which is exactly when an account gets
  parked: warm at 09:00, session dies, parked at 09:20. Parked is now read
  first.

- **The scheduler launched every app on its own interpreter** rather than the
  app's `.venv`, where `playwright` and `paperpull_core` actually are, so
  natively no scheduled run got past the app's first import.
  `PAPERPULL_SCHEDULE_HOUR` is now range-checked too: `25` parsed fine and
  produced a scheduler that ran forever and did nothing.

### Changed
- `sentinel.json` and `.locks/` are gitignored. A sentinel holds real invoice
  numbers and dates for a real account — the same class of data as
  `progress.json`, which was already listed by name.
- The `scheduler` container declares no healthcheck. The image's own check
  curls the panel, which this container does not serve, so it sat permanently
  `unhealthy` under `restart: unless-stopped`.
- Core is 0.2.0. `apps/simyo/storage.py` now hard-imports three new
  `paperpull_core` modules, so an install pinned to an older core fails on
  every command — this is the version to pin to instead.

## [0.8.0] — 2026-08-21

### Added
- **The control guard moved into `paperpull_core.controls`**, so an app no
  longer decides for itself whether a control on the page may be touched. It
  declares its own provider vocabulary and inherits everything that is true of
  every provider.

  This is the fix behind 0.7.2 rather than another patch of it. The judgement
  had been written three times, in three apps, and only the third one written
  considered that a control might belong to a sign-in form rather than a
  money-movement widget. A shared rule means the next provider inherits that
  lesson instead of rediscovering it, which is how it was found in the first
  place.

  The module is deliberately opinionated about two things. It fails closed, so
  an identity that could not be read is unsafe rather than safe, because that
  is what a detached or mid-navigation element looks like. And it is tested in
  both directions, because a guard that refuses the year picker does not
  announce itself, it just makes discovery return nothing and an empty run
  looks like an empty account.

  Ally and Chase now delegate to it. Core is 0.1.5.

### Changed
- Core tests include a check that no shared pattern contains a control
  character. Writing a regex through a shell heredoc has twice turned a
  word-boundary escape into a literal backspace in this repo, which still
  compiles and then matches nothing.

## [0.7.2] — 2026-08-21

### Fixed
- **Ally and Chase could read and write a control inside a sign-in form.**
  Both apps refused a dropdown only when it looked like part of a
  money-movement widget, so a control whose identity said `login-form` or
  `signin-form` was treated as ordinary and could be selected. Nothing was
  ever submitted and no credential was touched, but setting a value inside a
  login form is not reading, and reading is all these tools do.

  It was reachable. Both apps navigate to guessed document URLs, and
  `--diagnose` recorded whether that navigation succeeded and then carried on
  regardless, inspecting whatever page it had landed on. A missed guess lands
  on a public or sign-in page.

  A control is now refused for belonging to a sign-in or registration form as
  well as for moving money, every control is refused outright while a password
  field is on screen, and `--diagnose` no longer inspects controls unless it
  is on a signed-in documents page. All four cases are pinned by tests in both
  apps.

  Found by David Rudnick while building the Discover provider, where the same
  defect had the app select inside a marketing site's login dropdown after a
  wrong URL guess. Backported here rather than left to land with that app.

## [0.7.1] — 2026-08-21

### Fixed
- **A run started from the control panel could hang showing nothing at all.**
  App subprocesses inherited the panel server's stdin, so `sys.stdin.isatty()`
  was true and an app on its first run asked for the account holder's name,
  waiting for input into a terminal nobody was looking at. Because `input()`
  writes its prompt without a newline, and the panel reads whole lines, the
  prompt was never shown either. The page displayed the command and then
  nothing, with every button disabled. Apps now get no stdin, so the prompt
  cannot happen and the run ends instead of hanging. Contributed by David
  Rudnick in #7.

### Changed
- The control panel's README records that an app run from the panel cannot ask
  for the account holder's name, so that column stays blank until it is set
  from a terminal or in `config.json`.

## [0.7.0] — 2026-08-21

### Added
- **Ally Bank — the fifteenth provider** (`apps/ally`, CDP port 9235). Account
  statements and tax forms, read-only and delete-safe. Verified against a live
  account: 198 statements across 2020–2026 and 12 tax forms.

  Ally needed two things no earlier app did:

  - **Statements cannot be told apart by their metadata.** Ally posts several
    on the same date — one per account grouping, plus a copy of each joint
    statement addressed to each accountholder — and describes them
    identically: same `documentName`, same row label, no account information.
    Only `documentId` differs. So a downloaded statement is named from **its
    own first page**, whose account table and addressee are parsed
    structurally (by Ally's template text and the masked account-number
    column, never by a list of expected account nicknames — those are chosen
    by each customer). Unrecognised layout keeps the metadata name and says
    so; nothing is guessed.
  - **Every download is verified.** Because several rows look identical, the
    row clicked is an inference — so the app watches which `documentId` Ally
    actually serves and discards the file if it is not the one requested. This
    caught two real mismatches during development that would otherwise have
    filed one document under another's name.

  Tax forms come from the same endpoint with `docType=TAXFORMS`, found by
  opening the page's own tax tab and capturing the request rather than
  assuming the parameter. They file by **tax year, not posting date** (the
  2025 1099-INT is issued in January 2026), and a `corrected` form is flagged
  so it cannot be mistaken for the original.

- **Chase credit cards — the sixteenth provider** (`apps/chase`, CDP port
  9236). Card statements only, read-only and delete-safe, in a **real
  Edge/Chrome** window (the `verizon`/`walmart` pattern) rather than the
  bundled Chromium. Verified against a live account: 333 statements across 6
  cards, 2019–2026, each filename checked against the account number printed
  inside the PDF.

  Chase's document centre is one accordion per card with a styled "View:"
  year picker. Two things it taught:

  - **Attribute a document from its row, not from the API reply.** Every row
    names itself in full — "Aug 09, 2026 Statement SAPPHIRE RESERVE (...1234)
    Saves document" — while the JSON reply carries no account field, and
    collapsing, expanding and changing the year all hit the same endpoint. A
    listener that tagged "the next reply" with "the current card" filed one
    card's statements under its neighbour; matching on the row cannot.
  - **A card that is already expanded never re-fetches.** The first live run
    silently missed one of six cards for exactly that reason, with a total
    that looked perfectly plausible. Every card is now collapsed before it is
    opened.

  Tax documents and year-end summaries are deliberately out of scope for this
  app.

## [0.6.4] — 2026-08-19

### Fixed
- **The control panel's Login button never finished, leaving every button
  disabled.** The sign-in browser inherited the launcher's stdout, and since
  the user is told to keep that window open, the panel's stream never reached
  end-of-file. The browser is now started with its stdio detached (which also
  stops its updater/crash-handler chatter flooding the console). On Windows it
  is additionally detached from the launcher's process group, so closing the
  launching console no longer takes the sign-in window with it.
- **On macOS, no app could find the bundled Chromium.** Playwright renamed its
  macOS bundle from `Chromium.app/Contents/MacOS/Chromium` to `Google Chrome
  for Testing.app/Contents/MacOS/Google Chrome for Testing`; only the old name
  was matched. On an up-to-date install every app silently launched Edge or
  Chrome instead — and on a Mac with neither, reported that no browser was
  installed while Playwright's Chromium sat right there. The tests missed it
  because they only ever constructed the old layout. Both are matched now, and
  the new one is covered by tests. Core is 0.1.4 so `check_installs.py` can
  tell an install still running the old lookup.
- **`.gitignore` did not cover hand-made copies of the state files.** A file
  such as `discovery.json.pre-fix` or `progress.json.bak` holds the same real
  account data as the original, but only the exact names were ignored — one
  such copy was nearly committed while building a new app. Suffixed copies
  and `*.json.bak` / `*.json.orig` / `*.csv.bak` are now ignored too.

## [0.6.3] — 2026-08-19

### Fixed
- **The control panel left a downloader running after you closed its tab.**
  `/api/run` streams a run's output over SSE; when the browser disconnected,
  nothing stopped the child process. The downloader kept going unseen - still
  driving your signed-in browser over CDP, still writing PDFs and
  `progress.json` - with no output on screen. Believing it had stopped, you
  could press Run again and put two runs on one `progress.json`, one CDP port
  and one output folder, which is the collision that has previously mixed two
  accounts. Closing the tab now stops the run. Nothing is lost: `downloaded_ok`
  is only set once a document is saved, so the next run resumes and re-fetches
  nothing.

  The stream had to become an async generator to fix this. With a sync one,
  Starlette wraps it in `iterate_in_threadpool`, which never calls `.close()`
  on it - so a `try/finally` around the loop looks correct, and still never
  runs. Verified over a real socket against a throwaway app, both for the
  disconnect path and for a normal run's exit code.
- `gui/app.py` raised `SyntaxWarning: invalid escape sequence '\S'` on every
  import - a `\Scripts` path inside a non-raw docstring. Harmless today, a
  `SyntaxError` in a future Python.

### Changed
- The control panel states the project's Python floor (3.11+) and checks it at
  startup, failing with one sentence rather than something obscure. Nothing
  under `gui/` had recorded which Python version it targets.


## [0.6.2] — 2026-08-18

### Fixed
- **The Dominion app was a Robinhood clone whose text and rules were never
  rewritten.** Dominion Energy is a residential utility, but the app described
  itself as "a brokerage / crypto account", and `login.bat` promised the user
  it "NEVER buys, sells, trades, ... moves crypto" — telling them the wrong
  thing about what it does on their account. Its `document_rules.json` was
  Robinhood's whole vocabulary (consolidated 1099, crypto 1099, 1042-S, 5498,
  480.6, prospectus, trade confirmations), and its tests asserted that a power
  company issues "Crypto Statement" and "1099-B" — and passed. Rules, tests,
  docstrings and the sign-in text now describe a utility that posts bills.
  This mattered beyond one app: `docs/adding-a-provider.md` recommends cloning
  `dominion` for statement providers, so every new app inherited it.
- **Contributor docs sent people onto a port already in use.** The issue
  template and PR checklist said "9222–9232 are taken; use 9233+" and
  CONTRIBUTING said "9234+", but Gap is 9233 and UKG is 9234. A colliding port
  makes two apps share one browser profile, which has previously merged two
  accounts' documents. All four documents now say 9222–9234 taken, 9235+ free.
- **`.gitignore` covered every output folder except `Pay Statements`** — the
  UKG one, holding the most sensitive documents in the project. PDFs were
  already ignored by `*.pdf`, so nothing leaked, but the folder was the only
  one not named.
- Five site modules claimed, two lines apart, both "verified working against
  the live site" and "best-guess scaffolding written WITHOUT having seen the
  signed-in pages" (Dominion, Navy Federal, Robinhood, USAA, Verizon). The
  stale half is gone.
- Dominion, RedCard, T-Mobile and Verizon each described themselves as a
  "statement & tax-document downloader" and precreated a `Tax Documents`
  folder, though none has any tax discovery at all — the same permanently
  empty folder 0.4.1 removed elsewhere and the UKG audit fixed for UKG. The
  routes remain, so a surprise tax document is still filed rather than dropped.

### Changed
- Eight statement apps carried `include_invoices`, `pilot_online` and
  `pilot_instore` in their config defaults. All three are receipt-app concepts
  and none was ever read by a statement app; they are replaced by the
  `pilot_count` those apps actually use.
- Removed two functions with no callers anywhere: `ensure_statements_page`
  (Amex, an alias) and `find_download_control` (Wealthfront), plus the unread
  `tax_center` URL in Dominion and Verizon — another Robinhood leftover, in
  both cases pointing at the billing page.

## [0.6.1] — 2026-08-18

### Fixed
- **`setup-all.bat` never installed the shared core, so a fresh Windows clone
  produced fourteen virtual environments that all failed at startup with
  `ModuleNotFoundError: paperpull_core`.** Every app has imported the core
  since 0.5.0, and each app's own `setup.bat` was updated to install it; the
  one-shot script was missed. `setup-all.command` on macOS was unaffected, so
  Windows was the broken path. It installs the core from `core/` in a repo
  checkout and falls back to the bundled wheel in a standalone copy.
- `setup-all.bat` downloaded Playwright's Chromium once per app. It is a
  single shared install, so thirteen of the fourteen downloads were redundant
  — and it is by far the slowest step.
- `setup-all.bat` reported "All set - 9 apps" regardless of how many it had
  set up; the count was hardcoded when there were nine. It counts now.
- The failure summary in `setup-all.bat` began `echo !!`, and `!` is the
  delayed-expansion escape, so `cmd` consumed the marker *and* the list of
  failed apps with it — the one line that says what went wrong printed as a
  bare `FAILED`.

### Changed
- Both `setup-all` scripts reuse an existing virtual environment instead of
  rebuilding it. Rebuilding one that is in use fails with a permission error,
  which is exactly the situation in which someone re-runs setup.

## [0.6.0] — 2026-08-18

### Added
- **UKG Pro / UltiPro — pay statements (14th provider), and a new category:
  payroll.** UKG is the first provider without a fixed address: every employer
  runs its own tenant, so the site is read from `base_url` in `config.json`
  rather than hardcoded — which also keeps it out of the repo, since a tenant
  address identifies the employer. Sign-in varies too (a UKG username and
  password, or corporate SSO with MFA); neither involves the tool.

  Statements and PDFs both come from the JSON API that UKG's own mobile app
  uses, over the ordinary session, so **on a site that can also change direct
  deposit and tax withholding this app never activates a control at all.** It
  additionally refuses any URL whose path says `EDIT` rather than `VIEW`,
  which is how UKG Pro itself separates the two.

  W-2s and other tax forms are *not* fetched yet; the routing and rules for
  them are in place, so adding them is a change to `ukg_site.py` alone.

### Fixed
- A first run with no `config.json` died with a `FileNotFoundError` traceback.
  It now names the file, gives the copy command for the platform, and explains
  why the file is not shipped. Malformed JSON reports the syntax error, and a
  config saved from Notepad with a BOM now loads. This is every app's first
  run, not just the new one.
- **UKG:** two pay runs sharing a date (a regular and an off-cycle) collapsed
  into one record, silently losing a statement. Repeated dates are now
  disambiguated by document number.
- **UKG:** the tenant guard compared URLs with a string prefix, so
  `https://tenant.example.com.evil.test/` and
  `https://tenant.example.com@evil.test/` both passed it. It now parses the
  URL and compares scheme, host and port, and rejects embedded credentials.

### Changed
- **UKG:** records store the API path rather than the full URL, so the
  employer's tenant address no longer reaches `discovery.json`,
  `progress.json` or the index CSV.
- The provider tables no longer claim UKG downloads W-2s, which it does not.

## [0.5.0] — 2026-08-17

### Added
- **macOS and Linux support.** Every app ships a `.command` launcher beside
  each `.bat`, with the same names and behaviour, plus `setup-all.command` and
  `gui/run_gui.command`. Browser discovery is platform-aware: Playwright keeps
  Chromium under `LOCALAPPDATA` on Windows, `~/Library/Caches` on macOS (inside
  `Chromium.app`) and `~/.cache` on Linux, and the Edge/Chrome lookup that two
  bot-protected providers rely on knows where those live on each OS.
- **`paperpull-core`** — the support code the apps used to duplicate now lives
  once in `core/`. An app declares an `AppSpec` (its folders, routing, CSV
  columns and config defaults) and keeps only its orchestrator and `*_site.py`.
  About 15,400 duplicated lines became a 1,500-line core plus short
  declarations, so a fix lands once instead of thirteen times.
- **`tools/check_installs.py`** reports whether standalone installs have
  drifted from the repo. It reads only code — never config, state, CSVs, PDFs
  or browser profiles.

### Fixed
- AES-encrypted PDFs failed validation because pypdf needs its optional crypto
  extra; some providers issue them. Depending on `pypdf[crypto]` fixes it
  everywhere at once.
- Browser discovery picked the *oldest* installed Playwright Chromium, and
  sorted lexicographically so `chromium-1000` ranked below `chromium-999`.
  Newest build now wins.
- The macOS launchers referred users to `.bat` files, and their banner text was
  interpolated into double quotes — mangling output, and executing anything
  shaped like `$(...)` had a `.bat` ever contained it. Banners are now properly
  single-quoted.
- `setup-all.command` used an empty-array expansion that errors under `set -u`
  on the bash 3.2 macOS still ships.
- Running a launcher before setup gave a bare "No such file or directory"; it
  now names the script to run.

### Changed
- `SECURITY.md` and the README described the read-only guard as a blocklist
  **and** an allowlist for every app. That is true of the nine statement apps,
  which refuse any control not on the allowlist; the three receipt apps have no
  allowlist and screen a narrow print/invoice pattern against the blocklist,
  and Gap clicks nothing at all. Both documents now say what each app actually
  enforces.
- Test fixtures and code comments no longer carry real order numbers or a real
  carrier tracking number; they use same-shaped fakes.

## [0.4.1] — 2026-08-16

### Fixed
- **Gap** — in-store purchases are now separated from online orders. Gap's
  history page mixes the two; they were all being typed "Online" and filed in
  `Online\`. There is now an `In-Store\` folder (matching the Target and
  Walmart apps), online orders record the Gap Inc. brand that shipped them,
  in-store purchases record the store, and `--online` / `--instore` run one
  kind. Also fixes card-boundary detection: card text was bounded by length,
  so on an account whose cards are sparse the walk captured the whole list and
  every purchase inherited the first card's date. A card now ends at the first
  sibling order id.

### Changed
- **Gap** gained `run_online.bat` / `run_instore.bat`, matching Target and
  Walmart.
- **Amazon** drops the same dead invoice branch as Gap: `_handle_no_receipt`
  was never called, so the `Invoices\` folder and the `include_invoices` knob
  it depended on could never be reached. What Amazon saves is unchanged — its
  printable order summary is captured as the receipt, as it always was.
- **Every app** now creates only the document folders it can actually fill.
  Each app was cloned from the nearest existing one and inherited that app's
  whole folder list, so installs grew permanently-empty folders — `Insurance
  Documents` (real only for USAA, which is also an insurer), `Other Documents`
  (never a configurable document type), and `Invoices` (reachable only in the
  Target and Walmart apps). Routing is unchanged and now creates a folder on
  demand, so a category that is reachable but rare still gets its folder the
  moment a document lands there. Nothing that holds documents is affected.

## [0.4.0] — 2026-08-16

### Added
- **Gap Inc.** — order receipts (13th provider). One Gap login covers Gap, Old
  Navy, Banana Republic, Athleta and Gap Factory, and a single order history
  holds orders from all of them; the brand is recorded per order. The order
  history lazy-loads on scroll rather than paginating by year, so discovery is a
  single scrolled pass over everything Gap still exposes (about the last 13
  months). Gap ships no printable invoice and no print stylesheet, so each
  order's own details page is captured: the app waits for the page to load its
  data, hides everything outside the purchase-summary block (a display-only
  change to the local page), and renders the result with `printToPDF` — a
  receipt with the purchase header, line items and charge summary, and none of
  the site navigation.

## [0.3.1] — 2026-08-16

### Security
- **GUI control panel** now refuses any request whose `Origin`/`Referer` host is
  not localhost, closing a cross-site "trigger a run" vector on the command API
  (`/api/apps`, `/api/run`). The server already binds to `127.0.0.1` only and
  has no CORS; `SECURITY.md` now documents the localhost/CDP posture (close the
  signed-in browser when you're done — while it is open, any local process could
  attach to its debugging port).

## [0.3.0] — 2026-08-16

### Added
- **Verizon (Fios)** — Fios / Home Internet bill statements (10th provider).
  Uses your installed Microsoft Edge (T-Mobile-style bot protection blocks the
  bundled Chromium) and captures downloads via a controlled directory over CDP.
- **T-Mobile** — monthly bill statements (11th provider). Reads the bill-history
  page and downloads each period's detailed-bill PDF via a real download event.
- **Target RedCard / Target Circle Card** — monthly billing statements (12th
  provider). The RedCard credit account is serviced by TD Bank USA; reads the
  statements table (per-year switcher) at mytargetcirclecard.target.com and
  downloads each row's statement PDF via a real download event.

## [0.1.0] — 2026-08-15

First tagged release.

### Apps (9 providers)
- **Amazon** — order invoices, full order history
- **American Express** — statements + year-end summary
- **Dominion Energy (VA)** — billing statements
- **Navy Federal Credit Union** — account statements
- **Robinhood** — account statements + tax documents
- **Target** — receipts
- **USAA** — statements
- **Walmart** — receipts
- **Wealthfront** — statements + tax documents

### Features
- Read-only, connect-to-your-browser design — you sign in yourself; the tool
  never handles credentials or bypasses 2FA
- Delete-safe skip — deleting PDFs after importing them elsewhere never causes
  a re-download
- Account-holder ("owner") tagging: a first-run prompt plus an "Account Holder"
  column in the index CSV
- Multi-account support via `--config`
- Local FastAPI **control-panel GUI** that drives every app
