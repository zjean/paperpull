# Changelog

All notable changes to PaperPull are recorded here. Versioning follows
[Semantic Versioning](https://semver.org):

- **PATCH** — bug fixes, or repairing an app after a provider changes its site
- **MINOR** — a new app, or a cross-app feature
- **MAJOR** — breaking changes (repo layout, config format, removing an app)

## [0.18.0] — 2026-08-31

### Merged
- **Upstream `rheeloaded/paperpull` 0.8.1 → 0.17.1, 56 commits.** The first
  upstream sync since this fork started, and the reason the version jumps past
  0.11.0: upstream's numbering had gone further than ours, and one version line
  cannot run backwards. Upstream's own notes for those releases are kept
  verbatim at the bottom of this file rather than folded into ours — the two
  lines collide at 0.9.0, 0.10.0 and 0.11.0, which are three different pairs of
  releases.

  What arrived: **five new providers** — `aafmaa` (AAFMAA), `discovercard`
  (Discover), `mtb` (M&T Bank), `mypay` (DFAS myPay) and `paylocity`
  (Paylocity), taking the total to 23 — a repo-wide guard hardening pass, a
  `tools/status.py` archive-status tracker, and fixes across Chase, Ally and
  Wealthfront. All five providers seed, appear in the panel and are driven by
  hand; none of them can run on the schedule yet, because none takes
  `--unattended`. The scheduler names each one it skips.

  Two things the merge had to settle:

  - **`simyo` and `youfone` moved to ports 9242 and 9243.** Upstream handed
    9237 to Discover and 9238 to AAFMAA — the two ports this fork's own
    providers already used. In Docker nothing would have noticed, because the
    entrypoint rewrites every `cdp_url` to the one shared browser; on a native
    install the collision is real, and it is exactly the wrong-tab failure the
    identity gate exists to catch.
  - **`youfone` gained the URL guard upstream now requires of every app.**
    Its 0.17.0 added `core/tests/test_every_app_guard.py`, which walks every
    directory under `apps/` and fails one that has no `is_safe_url` — and
    Youfone's host check was real but lived under a different name
    (`is_safe_pdf_url`, host *and* endpoint in one function). It is now two:
    `is_safe_url` answers "may this be fetched at all", `is_safe_pdf_url`
    adds the endpoint rule on top, and `ALLOWED_HOSTS` is declared so the
    repo-wide test builds its hostile URLs from Youfone's own host rather
    than only checking the host-independent shapes. `simyo` already passed.
  - **The account facts upstream scrubbed from its old release notes stay
    scrubbed here.** Its 0.17.0 removed statement counts and date ranges from
    entries this fork had copied verbatim; taking our older wording back would
    have re-published them.

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
  - **Only accounts you actually have are in the register.** Providers with no
    config, and configs nobody has ever used, sit behind one `N not in use ·
    show` line — on a fresh install that is seventeen of eighteen rows, each
    otherwise reported as a thing wrong with your setup. **+ Add an account**
    is the other way in.
  - **No Due group.** Sorting the list by whether an account is worth running
    now turned it into a to-do list; `due` and `ok` share one **Ready**
    heading (named that, rather than "Nothing to do", because a due account is
    in it). The signal keeps its two honest homes: the masthead's count, and
    the account's own stamp and highlighted action.
  - **Buttons renamed** to what they do rather than what they pass: Login →
    **Sign in**, Pilot → **Try a few**, Run All → **Download everything new**,
    Discover → **List what is there**, Resume → **Continue last run**, Verify →
    **Re-check saved files**, Adopt identity → **Confirm this account**.

- **`gui/app.py` is a backend again.** The page moved out of a 400-line quoted
  string in the middle of the request handlers into `gui/static/`
  (`index.html`, `panel.css`, `panel.js`), served `no-store` and read per
  request, so editing the page is a reload rather than a restart.

- **How often a provider issues documents is now measured, not assumed.**
  `due` judged every account against a flat 31 days unless its config named a
  `cadence_days`, and almost none do. That is a fair guess for the monthly
  providers here and wrong for every other shape: a quarterly account was
  called due two months early, every quarter — nine wasted sign-ins a year for
  one provider, and for the scheduler, nine unattended passes that could only
  ever find nothing.

  The archive already holds the answer, so `due.measure_cadence` reads it:
  the median gap between consecutive documents in `progress.json`, measured
  per provider-side account and then combined. Per account matters — one
  install can cover several of the provider's own accounts, each billed
  monthly, and pooling their dates halves every gap until the account reports
  itself due a fortnight after a statement lands. The median rather than the
  mean, so one late statement cannot move it; only gaps of 1 to 400 days, so
  a record dated year 0001 (which a provider page can genuinely produce)
  cannot measure a cadence of several centuries.

  A `cadence_days` in the config still wins — it is a person's answer, and
  `0` ("always due") is a real value there, so it is checked for `None`
  rather than for truth. Receipt archives get no measurement at all: they
  record `purchase_date`, not `date`, and purchases arrive when someone buys
  something. The panel and the scheduler both read `due.plan`, so both get
  this without a change of their own.

  The same measurement is what upstream's new `tools/status.py` does for its
  own report; this is the half of it the register and the nightly pass can
  act on. That tool also runs in the container as it stands —
  `docker compose run --rm paperpull python /app/tools/status.py --root /data`
  — and is worth a look for the one thing nothing here answers: periods
  missing from the *middle* of a history.
- **The counts in the panel's own commentary say twenty-three.** Merging
  upstream took the provider count from eighteen to twenty-three, and the
  apps without an identity gate from sixteen to twenty-one — numbers that
  appear in `gui/app.py`, `gui/README.md`, `docs/docker.md` and three test
  docstrings as present-tense claims about what protects you. The sentences
  written in the past tense, about defects that were fixed when the count
  really was eighteen, are left as they are.

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
- **Updating PaperPull no longer signs you out of every provider.** `docker
  compose pull && docker compose up -d` — what the README and docs both told you
  to run — pulls the *browser* image as well, which tracks
  `linuxserver/chrome:latest` and rebuilds weekly. So most weeks an ordinary
  update recreated that container, and restarting Chrome was the one thing in
  this stack that could cost a session.

  It cost every session, because of a Chrome behaviour nothing here accounted
  for: a provider login is usually a cookie with no expiry, and Chrome drops
  those on exit unless **On startup** is set to *Continue where you left off* —
  the setting that also loads the previous session's cookies back in. Chrome's
  default is the New Tab page. The profile volume survived every restart
  perfectly and told the sites nothing; `Default/Cookies` was never even
  written to.

  Three changes, none of which need a person to remember anything:

  - The browser image ships a **recommended** Chrome policy setting
    `RestoreOnStartup` to restore the last session, so a fresh profile — and
    any profile whose owner never touched the setting — keeps its cookies and
    its tabs across a restart. Recommended, so **Settings → On startup** still
    switches; policy rather than a seeded `Default/Preferences`, because
    `session.restore_on_startup` is one of Chrome's tracked preferences and a
    value written from outside the browser is silently reset.
    `--restore-last-session` in `CHROME_CLI` covers the same ground after an
    unclean exit.
  - **`stop_grace_period: 30s` on the browser.** Compose's default 10s is not
    enough for s6 to bring the desktop and Chrome down in order, so Chrome was
    being SIGKILLed with its session unflushed — visible as
    `profile.exit_type: "Crashed"` in the profile, on a container that had been
    stopped politely.
  - **The documented update command names its services**
    (`docker compose pull paperpull scheduler && docker compose up -d paperpull
    scheduler`), because the panel and the scheduler are what change when
    PaperPull changes. `depends_on` starts a stopped browser but never
    recreates a running one, so the session is left alone. Updating the browser
    deliberately — for a new Chrome — is its own line, in its own section.
- **A `config.json` is no longer taken as evidence that anyone set an account
  up.** The container's entrypoint writes one for every app in the image on its
  first run, and the panel counted all of them: a household with two providers
  saw eighteen accounts, sixteen of them US banks it will never open — each
  listed as due, and each offered by Add an account as "already set up". An
  account now needs actual evidence (a sign-in, a confirmed identity, or a
  downloaded document) to count, and one without any is filed under **Never
  used** and kept out of the register.

  Being new is not on its own grounds for hiding an account: needing a person
  outranks having a history, so a parked or unconfirmed account stays in the
  register however fresh it is. The obvious rule would have hidden the very
  account this change was made while setting up.
- **Add an account can no longer offer to create a config that already
  exists.** Each provider is listed with how many accounts it has, and a label
  that is taken disables Create it and says where to find the account instead
  — previously the picker called a seeded provider "not set up", suggested
  `primary`, and the server answered 409 with nothing else to press.
- **The identity warning no longer has two ways to lie.** `identity_gated` is
  now decided server-side from the app's own accepted flags, so the sixteen
  apps with no identity gate cannot land in the *Needs confirming* bucket, and
  neither `identityText` nor `nextStep` can point their users at a button that
  does not exist for them.
- **"Never seen signed in" is no longer printed in the same vermilion as a
  session the provider killed.** An absence is not a failure.
- The register no longer prints "Nothing to do" on every one of sixteen rows
  under a heading that already says it.
- **`pytest core/tests gui/tests` collects again.** Two test files were both
  named `test_due.py`, and pytest's default import mode names a module by its
  basename, so collecting the second one errored out on the first. CI never
  saw it because it runs the two suites as separate invocations; the combined
  command in the docs — the one a person runs before merging — has been
  failing since `gui/tests/test_due.py` was added. The panel's copy is now
  `gui/tests/test_api_due.py`, which is what it actually tests.

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

---

# Upstream releases 0.8.1 → 0.17.1

Upstream's release notes, verbatim, for everything merged at 0.18.0
above. **These are upstream's version numbers, not this fork's** — both
lines have a 0.9.0, a 0.10.0 and a 0.11.0, and they are different
releases. This fork's own history continues below this section.

## [0.17.1] - 2026-08-30

### Fixed
- **A failed download no longer leaves an empty file wearing a real
  statement's name.** Playwright creates the target file before the bytes
  arrive, so a capture that failed left a zero byte PDF sitting in the output
  folder looking exactly like a genuine download. Five of those turned up in a
  real archive. The run had reported them as needing review, but the folder
  said otherwise, and the folder is what people look at. All seven apps that
  download this way now remove the file they could not fill. The check also
  rejects a file that exists but does not start with the PDF marker, which
  catches an error page saved under a PDF name. It runs only on the failure
  path, so a tax archive that legitimately arrives as a ZIP is untouched.
- **Chase skipped every card whose name contains "rewards".** The card header
  was judged against the general control blocklist, which refuses "rewards"
  because "Redeem rewards" is a real button on a card page. Amazon Prime
  Rewards, Southwest Rapid Rewards and IHG One Rewards were dropped along with
  every statement they held, leaving one line in the log while the run still
  reported success. A card is now judged on action verbs instead, so a product
  name is not mistaken for a button.
- **The word "edit" matched inside "Credit".** The settings guard would have
  refused to open a document titled "Credit Card Statement". The pattern now
  requires a word boundary.
- **Signing in no longer fails silently when the browser is already open.**
  Launching Edge or Chrome while a copy is already running hands the address to
  the existing window and drops the settings the tool needs. A window opened,
  the sign-in was spent, and nothing revealed the problem until the next
  command. The debugging port is now confirmed at login, where it can still be
  explained.
- Chase kept only the first 40 characters of a card name as its key, so two
  cards of the same product collided and the second card's whole history was
  discarded as a duplicate. The last four digits are kept in the key now.
- Chase could click an unlabelled pagination element, because a selector
  matched any element whose class contained "next" and an empty label passed
  the blocklist trivially. It now requires a readable label.
- Chase host-checks the address before fetching a document with the signed-in
  session, rather than trusting whatever a click opened.

### Testing
- Guard coverage runs across every app at once rather than per app. Per-app
  tests are how these problems drifted into separate copies in the first place.
  765 tests.

## [0.17.0] — 2026-08-30

### Security
- **Every app now has a parsed host allowlist**, up from two out of
  twenty-one. A review found the rest would fetch or navigate to whatever URL
  a stored record or a page attribute contained, using the live signed-in
  session. One bank app accepted any href containing ".pdf" or "statement",
  including a fully off-host one. Four apps ran `page.goto` on a stored value
  unchecked. Twelve chose which browser tab to drive with a substring test, so
  "provider.com" also matched "provider.com.phish.example". Each allowlist is
  derived from that app's own base URL, and every app was checked to confirm
  the hosts it genuinely uses still pass.
- **Guards that existed but never ran.** Six apps defined a control guard and
  never called it in production. One called it with a hardcoded string, so it
  always returned true and gated nothing; it reads the control's real label
  now.
- **Settings controls could be clicked in every app.** Only bare verb stems
  were matched, so "Save Changes", "Document Removal" and "Loss Mitigation
  Application" all passed. The shared core now carries a verb-led pattern and
  every app consults it, so the next improvement lands everywhere at once.
- **One repo-wide test** now checks all of this across every app together.
  Testing it per app is what allowed the drift, since each app's tests only
  ever knew about that app.

### Changed
- The documentation no longer publishes account facts. It stated not just that
  a provider is supported but that a real person holds an account there, with
  document counts and date spans. The "verified against a live account" claim
  stays, because that is about the code. The tallies, spans and account
  inventories are gone, along with two real statement dates that revealed a
  billing cycle day, two real document identifiers, a personal default date
  shipped in a shared example config, and example paths naming private folders.

### Notes
- Two mistakes made and caught while doing the guard work, recorded because
  the shape of them matters. Folding money NOUNS into the label guard refused
  real documents in five apps ("Detailed Bill PDF", "Pay Statement"), so the
  guard is verb-led and holds none. Blocking a bare "save" also refused
  "Save PDF", so the rule was narrowed to the commit-shaped forms. Both were
  found by re-checking each app against its own document labels rather than
  trusting the change.
- Receipt apps keep a blocklist used inline rather than an allowlist, on
  purpose: they must still click "Load more", which a document-word allowlist
  would refuse.

## [0.16.0] — 2026-08-30

### Added
- **A status tracker** (`tools/status.py`, `tools/status.bat`). Reads the state
  each app already keeps and reports how current every archive is. Prints a
  table, and with `--html` writes a self-contained dashboard. Pure stdlib,
  reads local state only, downloads nothing and changes nothing.

  It answers "is something new probably waiting" rather than "when did I last
  run this". A run that only verified existing files still updates a timestamp
  while saying nothing about whether a new statement exists, so the signal is
  the date of the newest document actually held, measured against how often
  that provider issues them. The cadence comes from the archive's own history,
  measured per account, so nothing needs configuring.

  It reports the archives that EXIST. Nobody holds an account with every
  provider, so an unused folder or one left by a closed account is left out
  rather than shown as missing.

- **Gap detection.** Being up to date is not the same as being complete. An
  archive can hold a document from last week and still be missing whole years
  behind it, which happened twice in this project. Each series is now checked
  for periods missing from the middle. A series must earn an opinion before it
  gets one, because plenty of real documents arrive irregularly and flagging
  those would train you to ignore the report.

### Fixed
- **Six ways the status tool could report all-clear over a damaged archive**,
  found by red-teaming it. `--quiet` hid the gap report entirely, so the mode
  meant for routine checking printed "everything is current" over an archive
  missing a whole year. A single future-dated record masked a stale archive. A
  truncated state file made a provider vanish from the report rather than be
  flagged. One record carrying a timezone offset beside one without raised an
  error that destroyed the report for every provider. Text the console cannot
  encode killed the run before the dashboard was written. Each was reproduced
  first, re-attacked after, and pinned by a test.
- **Two code-execution vectors in the launcher**, both demonstrated working
  first. It is documented to live in a data folder, which is a plausible place
  for other software to drop a file, so a planted `py.bat` could run instead of
  Python and a planted `statistics.py` could be imported instead of the real
  module. The launcher now blocks both.
- A `.gitignore` gap: `config.json.save` and similar backup copies were not
  ignored, though they hold the owner name and local paths exactly as
  `config.json` does.

### Security
- **Repository history was rewritten** to remove a value that should never
  have been committed. An existing clone will not fast-forward, so re-clone
  rather than pull.

## [0.15.0] — 2026-08-29

### Added
- **DFAS myPay now serves active-duty accounts too**, not just retirees.
  Leave and Earnings Statements (LES), W-2s and corrected W-2Cs are enumerated
  using myPay's own document-type numbers, over the same API the retiree
  documents are proven on. One app covers both: a document type that does not
  apply to an account returns nothing and is skipped, so a retiree run is
  unchanged (re-verified against a live account).

  **This is untested against a real active-duty account** and is labelled that
  way in the app README and in the code. It should work and it may not. Run
  `diagnose.bat`, then `run_pilot.bat`, and check the PDFs before a full run.

### Fixed
- A corrected **W-2C would have been filed as an ordinary W-2**, making the
  correction and the original indistinguishable on disk. The W-2C rule is now
  matched first.
- **A regex corruption check that could not see the corruption.** Rules files
  have repeatedly been written with one backslash level eaten, leaving a
  literal control character where a word boundary belongs, so the pattern
  silently matches nothing. The existing check read the file's bytes, but JSON
  escapes a control character as two ordinary characters, so a corrupted file
  looked clean. The new check reads the PARSED values, confirms every pattern
  compiles, and runs across all 21 apps. It was verified by deliberately
  reintroducing the corruption and watching it fail.

## [0.14.0] — 2026-08-29

### Added
- **DFAS myPay retiree documents (21st provider)** (`apps/mypay`, CDP port
  9241). Monthly Retiree Account Statements (eRAS), CRSC pay statements, annual
  RAS, 1099-R and IRS 1095 forms. Read-only and delete-safe. Verified end to end
  against a live account, every document a valid and byte-unique PDF.

  myPay exposes a clean JSON API, so **nothing on the page is ever clicked,
  no form is submitted and nothing is navigated** - enforced by a test. On a
  system where direct deposit, federal and state withholding, allotments and
  SBP elections sit one nav click from the documents, not activating a control
  at all is the strongest guarantee available.

  **The session token never leaves the browser.** myPay authenticates with a
  bearer token plus three identifying headers. Rather than lift that
  government credential into this process, every call runs inside the page and
  reads the token in the same expression that uses it. It is never logged and
  never written to disk.

  **A document is identified by its type and date, not by myPay's numeric Id.**
  The first live run proved why: for generated documents that Id is a transient
  handle that does not survive the session, so stored ones returned 404 and
  every eRAS and 1099-R was recorded a second time under a new Id. The numeric
  Id is now looked up fresh at download time.

  The guard is built for a military pay account: direct deposit, routing and
  account numbers, allotments, withholding and W-4, SBP, SGLI, TSP,
  beneficiary, address, login ID and password, and every change / update /
  start / stop / consent / agree / submit / certify variant. The SSN field on
  the sign-in page is only ever detected as a signed-out signal, never read
  from and never typed into. `diagnose` writes no screenshot here, because a
  myPay page shows pay figures and identifiers.

## [0.13.1] — 2026-08-23

Hardening pass over the new M&T app, from a line-by-line review of it. Every
item below is a real defect that was found and fixed, not a precaution.

### Fixed (security)
- **A crafted on-host link could be queued as a document.** The collector
  accepted any URL merely *containing* a document endpoint name, so an on-host
  route carrying that name as a query parameter passed the host allowlist and
  would have been fetched with the live session cookie. Endpoints are matched
  against the URL path now.
- **The collector read, and clicked inside, every tab in the browser.** It
  attaches to an ordinary browser, so that meant unrelated sites. Every frame
  is host-checked before it is read or clicked.
- **The one click on the live path had no guard at all**, while the README
  claimed every click was checked. The year-expander click now checks its label
  against the blocklist, and the claim in the README was rewritten to describe
  what the code actually does.
- **The blocklist let settings and payment controls through.** "Save Changes",
  "Request Payoff Statement", "Open Escrow Options", "Loss Mitigation
  Application", "Document Removal" and others passed, because verbs were
  matched without their endings. Verb families and settings words are covered
  now, and a bare "Save" is refused rather than treated as a document action.
- **Redirects were followed unchecked.** They are capped, and the final URL is
  re-validated before anything is written.
- **Removed 282 lines of dead code** cloned from another provider, including
  four functions that clicked page controls with no check, one that rebuilt a
  stale deep link, and one whose own comment described a wrong-document bug it
  would have re-armed.

### Fixed (correctness and honesty)
- **An expired session produced a run that looked successful.** M&T answers
  with a sign-in page at HTTP 200, so every document was filed as "needs manual
  review" and the tool exited 0 having saved nothing. It now recognises that
  response, stops, explains what happened, and exits non-zero.
- **A run that saves nothing no longer exits 0.**
- **1098 tax forms took their year from the availability date**, so a form for
  one year could be titled and dated as the next.
- An empty href resolved to M&T's home page and was downloaded as a document.
- The work tab was chosen by substring, which could select an unrelated tab.

## [0.13.0] — 2026-08-23

### Added
- **M&T Bank mortgage documents (20th provider)** (`apps/mtb`, CDP port 9240).
  Mortgage statements, year-end statements and 1098 tax forms from M&T's own
  online banking. Read-only and delete-safe. Verified end to end against a live
  account, all documents valid PDFs.

  M&T services its mortgages in-house (onlinebanking.mtb.com), not a
  subservicer. Documents are server-rendered with real per-document download
  URLs, so downloading is a plain host-checked GET of each document's own href
  and nothing on the page is clicked. Tax forms live on a second M&T host
  (m.mtb.com); both are allowlisted, parsed, never by prefix.

  Two things a mortgage portal forced, both handled read-only. The statement
  list only appears after you select the account and click View, a form submit
  this app does not perform, so you list it and the app reads whatever tab
  holds it. And the statements are split into collapsed year sections, only
  the current year open by default; the app expands each one (a read-only
  request that lists that year) so the full history is read. An earlier build
  silently captured only the current year, which is exactly the failure the
  pilot-then-inspect step exists to catch.

  The safety guard is tuned for a mortgage: it refuses paying the loan,
  autopay, payoff requests, escrow changes, refinance, recast and the rest,
  and a bare Edit/Update/Change too. The index records no balances or amounts.

## [0.12.0] — 2026-08-22

### Changed
- **Paylocity now fetches the whole pay-statement history, not just the
  current year.** The Pay History list endpoint defaults to a year-to-date
  view, but it takes a start date; the app now passes a far-past one and asks
  for everything. Confirmed against a live account.
  returned all 81 where before it saw 12. `--year` and `--start-date` still
  narrow the result.

### Notes
- **W-2 PDFs remain out of scope, and now the reason is precise.** Paylocity
  does not serve the W-2 as a PDF from its API. The form's link is a SAML
  single-sign-on redirect into a separate content system, and automating an
  SSO handshake into a third-party host on a payroll account is not something
  this project does. The finding is recorded in the app README so it is not
  re-investigated from scratch.

## [0.11.0] — 2026-08-22

### Added
- **Paylocity, the nineteenth provider** (`apps/paylocity`, CDP port 9239).
  Pay statements from the Paylocity Pay History area, read-only and
  delete-safe. The second payroll app after UKG, and the opposite kind of
  site: one fixed public address (access.paylocity.com), not a per-employer
  tenant. The employer is identified by the Company ID typed at sign-in, which
  the app never handles or stores.

  Nothing on the page is clicked. Discovery and download are plain GETs to the
  JSON endpoints Paylocity's own Pay History screen uses. A statement PDF is
  generated on demand, so download is a three-step flow the site itself
  follows: enqueue a report, poll until a download URL comes back, then fetch
  the PDF from it. On a site that can change direct deposit and withholding,
  not activating a control at all is the strongest guarantee available.

  A statement is identified by companyId, employeeId and history id, packed
  together rather than as a URL, so a query-string change cannot silently
  fetch the wrong file. The index CSV records no amounts, and the identity
  (which carries the employee id) is kept in discovery/progress only, never
  written to the CSV. Both are pinned by tests, alongside the payroll guard
  that refuses direct deposit, withholding, W-4, beneficiary and the rest.

  A run collects the current calendar year: Paylocity's Pay History
  defaults to a year-to-date view and this app reads that default. Older
  years sit behind the page's year filter, not wired up yet. W-2s are not
  fetched either, since Paylocity returns W-2 data as JSON rather than a
  PDF; the routing and folder are in place for both.

## [0.10.0] — 2026-08-22

### Added
- **AAFMAA (Armed Forces Mutual), the eighteenth provider** (`apps/aafmaa`,
  CDP port 9238). Annual statements and policy documents from the Member
  Center, read-only and delete-safe. Verified against a live account with a
  full run against a live account.

  The Member Center is classic ASP.NET WebForms, and it taught this repo
  three lessons the hard way:

  - **A postback name is not an identity.** WebForms names repeater controls
    by row position, so the same control name exists on every pager page and
    means "row 2 of whatever is showing". Documents are identified by title,
    date and policy, the pager is normalised to page 1 before every walk, and
    each download re-finds its row by content before clicking anything.
  - **Every saved statement must prove who it belongs to.** During a broken
    early run, a manually released PDF was captured under a different
    insured's filename, with a correct name, plausible size, and a clean
    validation pass. After download the file is read back and must contain
    its own row's policy number, or it goes to Manual Review with the reason
    stated.
  - **One dialog is answered, the only one in the project.** AAFMAA
    interposes a disclosure ("I confirm that I have read the message above")
    between the View control and some documents. The app answers it under a
    hard gate: matching dialog id, the disclosure's own sentence in the text,
    and not one money-related word, or it refuses. A dialog left over from an
    earlier document is cleared by reloading, never answered, because its
    View button belongs to a different document. SECURITY.md states the
    exception plainly.

  Only the default MY DOCUMENTS section is read so far. The Insurance
  Documents and Digital Vault sections are separate postback views, recorded
  as unimplemented in the app README.

### Fixed
- The build-a-provider issue template still told contributors ports 9237 and
  up were free while Discover holds 9237. It now says 9239+, matching the
  other three port documents.

## [0.9.0] — 2026-08-21

### Added
- **Discover credit cards — the seventeenth provider** (`apps/discovercard`,
  CDP port 9237). Card statements only, read-only and delete-safe, in a **real
  Edge/Chrome** window. Verified end to end against a live account
  (about two years of history), downloaded and checked, with a delete-safe
  re-run confirmed.

  Discover is the simplest bank-style provider so far, and the app is
  correspondingly small:

  - **Discovery is one read.** Every statement period's row, each with its own
    PDF link, is already in the DOM on plain page load - 24 links before any
    interaction, the same 24 after opening the period chooser. Nothing is
    clicked, no accordion expanded, no year swept. The Chase app's machinery
    exists because its rows only exist while one card's accordion is open on
    one year; none of it was carried over.
  - **A statement is served directly** at `stmtPDF?view=true&date=YYYYMMDD`, so
    the bytes are fetched with the signed-in context's own cookies using the
    href *read from the page* - never a URL built from a template, so a change
    to the query string cannot silently fetch the wrong period.
  - There is **no `<select>`** on the page: the period chooser is a link-based
    dropdown, which is why a select-based lookup finds nothing.

  The app slug is `discovercard` rather than `discover` because `--discover` is
  the CLI's own verb and the control panel has a **Discover** button; `redcard`
  sets the precedent of naming by the card product.

  A login with more than one Discover card is **unverified** and documented as
  such: the account this was built against has one card, so the page names none.

  On a full run, 22 of 24 listed periods downloaded and the two oldest returned
  `text/html` instead of a PDF. The app refuses to write a non-PDF body, so
  those are flagged for manual review rather than saved broken; the cause (a
  retention limit shorter than the listed periods, or rate limiting at the tail
  of a long run) is not established and is documented as open.

### Fixed
- **The statement-URL guard checks the host, not just the path.** The
  download fetch carries the signed-in session's cookies, and the old check
  accepted any absolute href (`startswith("http")`) so long as the path
  pattern and the date matched - review demonstrated a fetch from
  `evil.test` walking straight through it. The URL is now parsed and its
  scheme and host compared against Discover's own, which also refuses a
  suffix host (`card.discover.com.evil.test`) and a userinfo host
  (`card.discover.com@evil.test`), the two shapes that once walked through
  the UKG app's prefix-compared tenant guard. Found in review.

### Changed
- The Discover app's control guard - written for this app, backported to
  Ally and Chase as 0.7.2, then generalised into `paperpull_core.controls`
  in 0.8.0 - is deleted here in favour of delegating to that core module.
  The app keeps only its own vocabulary: `FORBIDDEN_CONTROL_RE`, and
  `PRODUCT_PICKER_RE` passed as an extra rule with the picker's options
  checked against it. The sign-in-form hole the local copy fixed is recorded
  under 0.7.2 and 0.8.0 below.

## [0.8.1] — 2026-08-21

### Fixed
- **Ally's and Chase's `--diagnose` never reported a refused dropdown.** When
  the control guard moved into `paperpull_core.controls` in 0.8.0, the
  verdict key in `describe_selects` became `refused`, but both apps' diagnose
  summaries still filtered on the old per-app key
  (`refused_as_money_control`), which the core never sets - so the "dropdowns
  refused" line could not appear, however many were refused. The JSON report
  itself was always right; only the printed summary read the dead key. Found
  while delegating the Discover app's guard to the core in #8.

- Core tests pin the key names `describe_selects` returns. Apps read them by
  name, so a rename deletes a caller's output without failing anything, which
  is exactly what happened above.

---

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
  statements and tax forms, read-only and delete-safe. Verified end to end against a
  live account.

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
  bundled Chromium. Verified end to end against a live account.
  

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
