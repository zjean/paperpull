/* PaperPull control panel.
 *
 * The page answers one question first - what needs me? - and only then offers
 * something to press. That is the whole shape of this file: /api/state comes
 * back as one flat, already-ordered list of accounts, the register draws it,
 * and selecting a line draws that account's facts and the actions that make
 * sense for it.
 *
 * Everything that decides what a person is told is a pure function near the
 * top (sessionText, identityText, rowNote, pathFor, nextStep). They take an
 * account row and return text, never touching the DOM, so the wording and the
 * rules behind it are readable and testable on their own - which the previous
 * version's logic, spliced into DOM handlers, was not.
 */
'use strict';

let STATE = null;          // the last /api/state
let SELECTED = null;       // "app/account" of the selected row
let es = null;             // the live EventSource, if a run is going
let RUN = null;            // the run id the server gave us
let promptTimer = null, promptFrom = 0, stopping = false;
let clock = null;          // ticks the session meter while the page is open

// A sitting walks several accounts, one run at a time. `sitting` is true for
// the whole walk; `sittingAbort` is how "End the sitting" (or Stop mid-run)
// ends the whole walk rather than just the run in flight. `onRunEnd` is the
// resolver of whichever run's promise is currently outstanding - set by
// startRun, called once by endRun - which is how a sitting's
// `await startRun(...)` wakes up only once the run has actually ended.
let sitting = false, sittingAbort = false, onRunEnd = null, onSitGo = null;

const $ = id => document.getElementById(id);
const key = a => a.app + '/' + a.account;

// ---------------------------------------------------------------------------
// What an account's state means, in words. Pure functions: no DOM, no fetch.
// ---------------------------------------------------------------------------

/* How long this account's signed-in session has left.
 *
 * A provider that declares no session lifetime gets no meter at all rather
 * than a full one: "unknown" and "plenty" are different facts, and only one
 * of them is true. The ones that do declare it are the reason the sitting
 * exists - a ten-minute Simyo session cannot wait behind nine that last for
 * days - so this is the one number on the page that ticks down on its own.
 */
function sessionText(a, nowMs) {
  if (a.state === 'parked') {
    const why = a.parked_reason ? ' ' + a.parked_reason : '';
    return {text: 'Signed out.' + why, pct: 0, cls: 'gone'};
  }
  if (!a.last_alive) {
    // An absence, not a failure: nobody has signed this account in since the
    // sentinel existed. Vermilion is for a session the provider took away.
    return {text: 'Never seen signed in.', pct: null, cls: ''};
  }
  const seen = Date.parse(a.last_alive);
  const when = isNaN(seen) ? a.last_alive : a.last_alive.slice(0, 16).replace('T', ' ');
  const life = a.session_lifetime_minutes;
  if (life === null || life === undefined) {
    return {text: 'Signed in at ' + when +
                  '. This provider never says how long that lasts.',
            pct: null, cls: ''};
  }
  if (isNaN(seen)) return {text: 'Signed in at ' + when, pct: null, cls: ''};
  const left = Math.round(life - (nowMs - seen) / 60000);
  if (left <= 0) {
    return {text: 'Gone. A ' + life + '-minute session, signed in at ' + when +
                  '. Sign in again before running anything.',
            pct: 0, cls: 'gone'};
  }
  return {text: left + ' of ' + life + ' minutes left, from ' + when + '.',
          pct: Math.max(2, Math.min(100, Math.round(100 * left / life))),
          cls: left <= Math.max(2, life * 0.34) ? 'low' : ''};
}

/* What is known about which account this really is.
 *
 * Only apps whose entry script accepts --adopt-identity have an identity gate
 * (`identity_gated`, decided server-side). For every other app `identified`
 * is permanently false - not because a check failed but because nothing ever
 * asked the question - so telling those users to "press Confirm this account"
 * would point at a button that does not exist for them. That was a real dead
 * end on sixteen of eighteen apps.
 */
function identityText(a) {
  if (a.anchors && a.anchors.length) {
    // The anchors themselves, not just a tick. Adopting is the one moment a
    // run takes on trust that the tab it can see really is this config's
    // account, so the person who did the adopting has to be able to check
    // WHAT was recorded against the documents in front of them.
    const shown = a.anchors.map(x => x.id + ' (' + x.date + ')').join(', ');
    return {text: 'Recognised by ' + shown + '. Check those belong to it.',
            gone: false};
  }
  if (a.identity_gated) {
    return {text: 'Not recorded yet. Sign in, check the browser really shows '
                  + 'THIS account, then confirm it once.', gone: true};
  }
  return {text: 'This provider does not ask.', gone: false};
}

/* The one line under a provider's name in the register. */
function rowNote(a) {
  if (a.needs === 'signin') {
    return {text: a.parked_reason || 'Signed out - needs a sign-in.',
            cls: 'row-note-signin'};
  }
  if (a.needs === 'confirm') {
    return {text: 'Confirm which account this is.', cls: 'row-note-confirm'};
  }
  if (a.needs === 'due') {
    return {text: a.newest_document_date
              ? 'Due. Newest so far ' + a.newest_document_date + '.'
              : 'Never run.', cls: ''};
  }
  if (a.needs === 'setup') {
    return {text: 'No config.json yet.', cls: ''};
  }
  // Under a "Nothing to do" heading, a "Nothing to do" note on every one of
  // sixteen rows is noise. An empty note draws no line at all.
  return {text: a.last_checked_date ? 'Looked ' + a.last_checked_date : '',
          cls: ''};
}

/* The ordinary path through a provider, in order, minus anything this app's
 * entry script does not accept.
 *
 * `adopt` earns a place in the path rather than sitting under "Other things"
 * for the two apps that have an identity gate, because for them it genuinely
 * is step two - runs refuse to file documents until it is done.
 */
function pathFor(a) {
  const wanted = ['login', 'adopt', 'pilot', 'all'];
  const supported = new Set(a.supported_actions || []);
  return wanted.filter(k => supported.has(k));
}

/* The one action to press now, or null when there is nothing to do.
 *
 * Exactly one thing on the bench is ever highlighted, and only when there is
 * a real next step. The old panel filled two buttons blue at all times, which
 * said "these two are equally the point" about an account that might need
 * neither.
 */
function nextStep(a) {
  // Every action passes --config or relies on the default one being there, so
  // highlighting any of them would be sending someone at a run that cannot
  // start. The account's own "why" line says what to do instead.
  if (a.needs === 'setup') return null;
  const path = new Set(pathFor(a));
  // Nothing runs against a session that is not there, so this outranks every
  // other consideration - including an identity that still needs confirming,
  // which itself needs a signed-in tab to confirm against.
  if (a.state !== 'warm' && path.has('login')) return 'login';
  if (a.identity_gated && !a.identified && path.has('adopt')) return 'adopt';
  if (a.needs === 'ok') return null;
  return path.has('all') ? 'all' : null;
}

// ---------------------------------------------------------------------------
// Drawing it
// ---------------------------------------------------------------------------

// The register draws these, in this order. `setup` is deliberately absent:
// an account with no config.json is a provider you have not set up, and on a
// fresh checkout that is seventeen of eighteen rows, most of which nobody will
// ever use. /api/state still reports them - the data stays complete - and they
// are what fills the Add an account picker instead.
const GROUPS = [
  ['signin', 'Needs a sign-in'],
  ['confirm', 'Needs confirming'],
  ['due', 'Due'],
  ['ok', 'Nothing to do'],
];

function accountsNeeding(need) {
  return STATE.accounts.filter(a => a.needs === need);
}

function drawTally() {
  const n = need => accountsNeeding(need).length;
  const attention = n('signin') + n('confirm');
  const due = n('due');
  const total = STATE.accounts.length - n('setup');
  const bits = [];
  if (attention) bits.push('<span class="n-signin">' + attention + '</span> need you now');
  if (due) bits.push('<span class="n-due">' + due + '</span> due');
  if (!attention && !due) bits.push('<b>nothing needs you</b>');
  // `total` counts what the register shows, not every row /api/state returns:
  // the providers you have never set up are not accounts you have, and
  // counting them here read as "you have eighteen accounts and seventeen of
  // them are broken".
  $('tally').innerHTML = bits.join(' &middot; ') + ' &middot; ' + total +
    ' account' + (total === 1 ? '' : 's') + ' set up';
}

function notice(text, warn) {
  const el = document.createElement('p');
  el.className = 'notice' + (warn ? ' notice-warn' : '');
  el.innerHTML = text;
  $('notices').append(el);
}

function drawNotices() {
  $('notices').innerHTML = '';
  if (!STATE.accounts.length) {
    notice('No downloaders found under <b>' + escapeHtml(STATE.apps_root) +
           '</b>. Point <b>APPS_ROOT</b> at your downloaders folder and reload.',
           true);
    return;
  }
  if (!STATE.due_known) {
    // An empty due list and "cannot work out the due list" are different
    // facts, and only saying the first would be a lie by omission: the page
    // would show every account as "Nothing to do".
    notice('Cannot work out what is due here - <b>paperpull_core</b> is not '
         + 'importable by the panel. Every action still runs; the Due grouping '
         + 'and the sitting are unavailable.', true);
  }
  if (STATE.remote_browser && !STATE.browser_url) {
    notice('The sign-in browser is in another container, but no '
         + '<b>PAPERPULL_BROWSER_URL</b> is set - so the panel cannot link you '
         + 'to it. Sign in on that browser’s own desktop.', true);
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, c =>
    ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
}

function drawRegister() {
  const q = $('filter').value.trim().toLowerCase();
  const list = $('register');
  list.innerHTML = '';
  let shown = 0;
  for (const [need, title] of GROUPS) {
    const rows = accountsNeeding(need)
      .filter(a => !q || key(a).toLowerCase().includes(q));
    if (!rows.length) continue;
    const head = document.createElement('div');
    head.className = 'group-head';
    head.innerHTML = '<span>' + escapeHtml(title) +
                     '</span><span class="group-count">' + rows.length + '</span>';
    list.append(head);
    for (const a of rows) { list.append(rowFor(a)); shown++; }
  }
  if (!shown) {
    const p = document.createElement('div');
    p.className = 'group-head';
    p.textContent = q ? 'nothing matches ' + q : 'no accounts';
    list.append(p);
  }
}

function rowFor(a) {
  const note = rowNote(a);
  const b = document.createElement('button');
  b.className = 'row row-' + a.needs;
  b.setAttribute('role', 'option');
  b.setAttribute('aria-selected', key(a) === SELECTED ? 'true' : 'false');
  b.dataset.key = key(a);

  const top = document.createElement('div');
  top.className = 'row-top';
  const prov = document.createElement('span');
  prov.className = 'row-provider';
  prov.textContent = a.app;
  const acc = document.createElement('span');
  acc.className = 'row-account';
  acc.textContent = a.account;
  top.append(prov, acc);
  if (a.session_lifetime_minutes !== null && a.session_lifetime_minutes !== undefined) {
    const life = document.createElement('span');
    life.className = 'row-life';
    life.textContent = a.session_lifetime_minutes + 'm session';
    top.append(life);
  }

  b.append(top);
  if (note.text) {
    const sub = document.createElement('div');
    sub.className = 'row-note ' + note.cls;
    sub.textContent = note.text;
    b.append(sub);
  }
  b.onclick = () => select(key(a));
  return b;
}

function selectedAccount() {
  return STATE.accounts.find(a => key(a) === SELECTED) || null;
}

function select(k) {
  SELECTED = k;
  // Moving to another account is the moment the last run's output stops being
  // the subject of the page, so the console gives its room back. It does NOT
  // shrink the instant a run finishes - that is when you most want to read it.
  if (!es) document.body.classList.remove('running');
  drawRegister();
  drawDetail();
}

function drawDetail() {
  const a = selectedAccount();
  $('detail').hidden = !a;
  $('benchempty').hidden = !!a;
  if (!a) return;

  $('dprovider').textContent = a.app;
  $('daccount').textContent = a.account;
  const stamp = $('dstamp');
  stamp.className = 'stamp stamp-' + a.needs;
  stamp.textContent = (STATE.needs[a.needs] || {}).stamp || a.needs;
  $('dwhy').textContent = (STATE.needs[a.needs] || {}).why || '';

  drawSession(a);

  const id = identityText(a);
  const idEl = $('fidentity');
  idEl.textContent = id.text;
  idEl.className = id.gone ? 'gone' : 'anchor';

  $('fnewest').textContent = a.newest_document_date || 'nothing downloaded yet';
  $('flooked').textContent = a.last_checked_date || 'never';

  const warn = $('venvwarn');
  warn.hidden = !(STATE.expect_venvs && !a.has_venv);
  if (!warn.hidden) {
    warn.textContent = 'This app has no .venv yet. Run its setup script once, '
      + 'or a run here will fail on its imports.';
  }

  drawActions(a);
}

function drawSession(a) {
  const s = sessionText(a, Date.now());
  $('fsessiontext').textContent = s.text;
  $('fsessiontext').className = s.cls === 'gone' ? 'gone' : '';
  const meter = $('meter');
  meter.hidden = s.pct === null;
  if (s.pct !== null) {
    $('meterfill').style.width = s.pct + '%';
    $('meterfill').className = 'meter-fill ' + s.cls;
  }
}

function actionSpec(k) {
  return STATE.actions[k] || {label: k, blurb: ''};
}

function drawActions(a) {
  const next = nextStep(a);
  const path = pathFor(a);
  const grid = $('path');
  grid.innerHTML = '';
  path.forEach((k, i) => {
    const spec = actionSpec(k);
    const b = document.createElement('button');
    b.className = 'step' + (k === next ? ' next' : '');
    b.dataset.action = k;
    const n = document.createElement('span');
    n.className = 'step-n';
    n.textContent = i + 1;
    const label = document.createElement('span');
    label.className = 'step-label';
    label.textContent = spec.label;
    const blurb = document.createElement('span');
    blurb.className = 'step-blurb';
    blurb.textContent = spec.blurb;
    b.append(n, label, blurb);
    b.onclick = () => run(k);
    grid.append(b);
  });

  const inPath = new Set(path);
  const rest = Object.keys(STATE.actions)
    .filter(k => !inPath.has(k) && (a.supported_actions || []).includes(k));
  const more = $('more');
  more.hidden = !rest.length;
  const mg = $('moregrid');
  mg.innerHTML = '';
  for (const k of rest) {
    const spec = actionSpec(k);
    const b = document.createElement('button');
    b.className = 'side';
    b.dataset.action = k;
    const label = document.createElement('span');
    label.className = 'side-label';
    label.textContent = spec.label;
    const blurb = document.createElement('span');
    blurb.className = 'side-blurb';
    blurb.textContent = spec.blurb;
    b.append(label, blurb);
    b.onclick = () => run(k);
    mg.append(b);
  }
  setActionsEnabled(!es);
}

function actionButtons() {
  return document.querySelectorAll('#path button, #moregrid button');
}

function setActionsEnabled(on) {
  actionButtons().forEach(b => { b.disabled = !on; });
  drawSitButton();
  drawAddButton();
}

// ---------------------------------------------------------------------------
// Loading
// ---------------------------------------------------------------------------

async function load() {
  let res;
  try {
    res = await fetch('/api/state');
  } catch (e) {
    notice('The panel is not answering. Is it still running?', true);
    return;
  }
  if (!res.ok) { notice('The panel refused to describe itself (HTTP ' + res.status + ').', true); return; }
  STATE = await res.json();
  $('version').textContent = 'v' + STATE.version;
  $('root').textContent = STATE.apps_root;
  if (STATE.browser_url) {
    const d = $('desktop');
    d.href = STATE.browser_url;
    d.hidden = false;
  }
  drawNotices();
  drawTally();
  // Keep whatever was selected across a refresh; otherwise open the account
  // that most needs a person, which is the first row by construction.
  if (!SELECTED || !selectedAccount()) {
    // The first row the register actually shows - STATE.accounts[0] can be a
    // `setup` row, which the register does not draw, so selecting it would
    // open a detail view with nothing highlighted in the list beside it.
    const first = STATE.accounts.find(a => a.needs !== 'setup');
    SELECTED = first ? key(first) : null;
  }
  drawRegister();
  drawDetail();
  drawSitButton();
  drawAddButton();
  if (!clock) {
    // The session meter is a countdown, so it has to move without a reload.
    clock = setInterval(() => { const a = selectedAccount(); if (a) drawSession(a); }, 15000);
  }
}

/* Re-read the state after a run, because a run is exactly what changes it: a
 * sign-in warms a session, a download moves the newest date, a sign-out parks
 * the account. Without this the register kept describing the world as it was
 * when the page was opened. */
async function refresh() {
  if (es) return;               // mid-run the numbers would only flicker
  await load();
}

$('filter').oninput = () => drawRegister();

// ---------------------------------------------------------------------------
// The console, and the prompts that appear in it
// ---------------------------------------------------------------------------
//
// Output arrives as raw chunks rather than whole lines, because input() writes
// its prompt without a newline. That is also how a prompt is spotted: output
// that stops mid-line and stays that way is an app waiting for an answer.

function setStatus(cls, text) {
  $('dot').className = 'dot ' + cls;
  $('statustext').textContent = text;
}

function append(text) {
  const con = $('console');
  con.textContent += text;
  con.scrollTop = con.scrollHeight;
}

function tailLine() {
  const t = $('console').textContent;
  return t.slice(t.lastIndexOf('\n') + 1);
}

function watchForPrompt() {
  clearTimeout(promptTimer);
  if ($('console').textContent.endsWith('\n')) { clearPrompt(); return; }
  promptTimer = setTimeout(markPrompt, 400);
}

function markPrompt() {
  if (!RUN) return;
  $('reply').classList.add('pending');
  $('prompt').textContent = tailLine().trim() || 'Waiting for an answer.';
  setStatus('wait', 'waiting for you');
  $('answer').focus();
  // A sign-out or a challenge is fixed in the browser itself, which in a
  // container is somewhere else entirely - so say where. Only what the app
  // printed since the LAST answer counts: read further back and the sign-out
  // wording from an earlier pause still matches, and every later prompt (the
  // re-check pass asks once per receipt) wrongly points at the desktop.
  const recent = $('console').textContent.slice(promptFrom);
  const needsBrowser = /signed you out|sign in|challenge|verification|captcha|robot/i.test(recent);
  const link = $('signin');
  if (needsBrowser && STATE.browser_url) { link.href = STATE.browser_url; link.hidden = false; }
  else link.hidden = true;
}

function clearPrompt() {
  clearTimeout(promptTimer);
  $('reply').classList.remove('pending');
  $('prompt').textContent = '';
  $('signin').hidden = true;
}

async function answer() {
  if (!RUN) return;
  const text = $('answer').value;
  const r = await fetch('/api/answer', {method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({run: RUN, text})});
  if (!r.ok) { append('\n[nothing is waiting for an answer]\n'); clearPrompt(); return; }
  // The run has a pipe, not a terminal, so it never echoes what we sent.
  append(text + '\n');
  promptFrom = $('console').textContent.length;
  $('answer').value = '';
  syncAnswerButton();
  clearPrompt();
  setStatus('run', $('statustext').dataset.running || 'running');
}

async function stopRun() {
  if (!RUN) return;
  stopping = true;
  // Mid-sitting, Stop has to end the whole sitting - not just this one run,
  // leaving the loop free to sign the next account in regardless. Otherwise
  // the one button a person reaches for to bail out would not actually bail
  // out of anything but the current account.
  if (sitting) sittingAbort = true;
  setStatus('run', 'stopping');
  await fetch('/api/stop', {method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({run: RUN})});
}

function syncAnswerButton() {
  $('continue').textContent = $('answer').value ? 'Send' : 'Continue';
}

$('continue').onclick = answer;
$('stop').onclick = stopRun;
$('answer').oninput = syncAnswerButton;
$('answer').onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); answer(); } };

// ---------------------------------------------------------------------------
// Running one thing
// ---------------------------------------------------------------------------

function endRun(cls, text) {
  setStatus(cls, text);
  RUN = null;
  clearPrompt();
  $('reply').hidden = true;
  if (es) { es.close(); es = null; }
  setActionsEnabled(true);
  // This is the one place a run is decided to be over - reached from the
  // server's "done" event and from a lost connection alike (see startRun).
  // Waking a sitting's `await startRun(...)` here, rather than anywhere else,
  // is what stops it from ever signing the next account in while this one's
  // subprocess might still be holding the provider's session slot.
  if (onRunEnd) { const resolve = onRunEnd; onRunEnd = null; resolve(); }
  refresh();
}

function run(action) {
  const a = selectedAccount();
  if (a) startRun(a.app, a.account, action);
}

/* Starts one run and returns a Promise that resolves once endRun has been
 * called for it - i.e. once the server has actually said "done" (or the
 * connection was lost), never merely once the request went out. A sitting
 * awaits this before touching the next account.
 *
 * There is no "end" SSE event in this protocol, only "run" (the very first
 * frame, carrying the run id) and "done" (the last, carrying the exit code,
 * sent right before the server closes the stream - see the `finally` in
 * api_run's stream()). onerror below is therefore not the normal end-of-run
 * signal, it is what fires if the connection drops with no "done" ever
 * having arrived. That is also why endRun always closes `es` itself: an
 * EventSource whose stream the *server* ends still auto-reconnects unless
 * something on this side calls .close() first.
 */
function startRun(app, account, action) {
  if (es) {
    // With Start a sitting's synchronous re-entrancy guard, and every action
    // button disabled for as long as any run - manual or sitting-driven - is
    // live, every legitimate call site now waits for a previous run to end
    // before starting another, so this should be unreachable. If it ever
    // fires anyway, closing the old stream silently and reassigning
    // `onRunEnd` would abandon whoever was still awaiting it, mid-run, with
    // nothing to show for it - so this is surfaced loudly instead.
    console.error('startRun called while a previous run was still live; '
                 + 'closing it now. This should not be reachable.');
    es.close();
  }
  $('console').textContent = '';
  promptFrom = 0; stopping = false;
  $('answer').value = ''; syncAnswerButton(); clearPrompt();
  const label = actionSpec(action).label;
  const running = label + ' — ' + app + '/' + account;
  $('statustext').dataset.running = running;
  setStatus('run', running);
  $('cmdlabel').textContent = action;
  document.body.classList.add('running');
  setActionsEnabled(false);
  es = new EventSource('/api/run?app=' + encodeURIComponent(app) +
                       '&account=' + encodeURIComponent(account) +
                       '&action=' + encodeURIComponent(action));
  // Arrives before any output, so even a first-line prompt can be answered.
  es.addEventListener('run', e => { RUN = e.data; $('reply').hidden = false; });
  es.onmessage = e => { append(JSON.parse(e.data).t); watchForPrompt(); };
  es.addEventListener('done', e => {
    const code = e.data;
    // A run you stopped yourself did not fail. Terminating it leaves a signal
    // exit code behind, and reporting that as an error is just wrong. The
    // code is what settles the race where a run finished on its own between
    // the click and the request: a clean exit is "finished", not "stopped".
    if (stopping && code !== '0') return endRun('', 'stopped');
    endRun(code === '0' ? 'ok' : 'err',
           code === '0' ? 'finished' : 'exited with code ' + code);
  });
  es.onerror = () => { if (es) endRun('err', 'connection lost'); };
  return new Promise(resolve => { onRunEnd = resolve; });
}

// ---------------------------------------------------------------------------
// The sitting: one account at a time, most perishable session first
// ---------------------------------------------------------------------------
//
// The scarce resource here is not CPU, it is a person's attention for signing
// in. So this walks /api/due in the order it comes back (due.plan's own
// ordering, most-perishable-session-first) and, for each account, asks for a
// fresh sign-in and then awaits the FULL run - teardown included - before
// ever asking about the next one. Two runs on one provider at once would sign
// each other's session out from under the other; that is the one outcome this
// whole feature exists to prevent.
//
// The asking used to be three chained confirm() dialogs, which meant the
// queue could not be reviewed before it started, could not be read while it
// ran, and blocked the page it was reporting on. It is a panel at the top of
// the bench now.

function sittingCandidates() {
  // A provider with no declared session lifetime can wait for a plain cron
  // job; only a perishable one needs a person sitting down for it.
  if (!STATE || !STATE.due_known) return null;
  return STATE.accounts.filter(
    a => a.needs !== 'ok' && a.needs !== 'setup' &&
         a.session_lifetime_minutes !== null &&
         a.session_lifetime_minutes !== undefined);
}

function drawSitButton() {
  const b = $('sit');
  const cands = sittingCandidates();
  if (cands === null) {
    b.disabled = true;
    $('sitsub').textContent = 'unavailable - the panel cannot work out what is due';
    b.classList.remove('ready');
    return;
  }
  b.disabled = sitting || !!es || !cands.length;
  b.classList.toggle('ready', !!cands.length && !sitting && !es);
  $('sitsub').textContent = cands.length
    ? cands.length + ' account' + (cands.length === 1 ? '' : 's') +
      ' whose session dies too fast for the scheduler'
    : 'nothing perishable is waiting';
}

function drawSittingQueue(queue, at, done, skipped) {
  const ol = $('sittingqueue');
  ol.innerHTML = '';
  queue.forEach((a, i) => {
    const li = document.createElement('li');
    li.textContent = (i + 1) + '. ' + a.app + '/' + a.account;
    if (skipped) li.className = i < done ? 'done' : 'skipped';
    else if (i < done) li.className = 'done';
    else if (i === at) li.className = 'now';
    ol.append(li);
  });
}

/* Waits for the person to say they have signed in. Resolves true to go ahead,
 * false to end the sitting - which is also what Stop does, via sittingAbort. */
function askSignedIn(a) {
  const ask = $('sittingask');
  ask.hidden = false;
  $('sittingasktext').textContent =
    'Sign in to ' + a.app + ' (' + a.account + ') in ONE tab, then press '
    + 'the button. Its session lasts about ' + a.session_lifetime_minutes
    + ' minutes, so this runs straight after.';
  const link = $('sitdesktop');
  if (STATE.browser_url) { link.href = STATE.browser_url; link.hidden = false; }
  else link.hidden = true;
  $('sitgo').focus();
  return new Promise(resolve => { onSitGo = resolve; });
}

function answerSit(ok) {
  $('sittingask').hidden = true;
  if (onSitGo) { const r = onSitGo; onSitGo = null; r(ok); }
}

$('sitgo').onclick = () => answerSit(true);
$('sitstop').onclick = () => {
  sittingAbort = true;
  answerSit(false);
  if (RUN) stopRun();
};

async function startSitting() {
  // Re-entrancy guard - and it MUST be the very first statement, before any
  // `await` in this function. A second click calls this function again; JS
  // runs synchronously up to the first await, so `sitting` is already true by
  // the time that second call is dispatched. Putting this check anywhere
  // later leaves exactly that window open: a second invocation would reach
  // askSignedIn()/startRun() for the account the first is still running, and
  // startRun's `if (es) es.close()` would silently tear down the first
  // invocation's live run and steal its `onRunEnd`.
  if (sitting) return;
  sitting = true;
  sittingAbort = false;
  drawSitButton();
  const panel = $('sitting');
  panel.hidden = false;
  let queue = [], completed = 0;
  try {
    const res = await fetch('/api/due');
    if (!res.ok) {
      // A failed fetch (503: paperpull_core is not importable here) is not
      // the same fact as "nobody is due" - it means the panel cannot tell,
      // and must not be read as "stand down".
      $('sittingcount').textContent = 'cannot read the due list here';
      return;
    }
    const {due} = await res.json();
    queue = due.filter(a => a.session_lifetime_minutes !== null);
    if (!queue.length) {
      $('sittingcount').textContent = 'nothing needs a person right now';
      return;
    }
    for (let i = 0; i < queue.length; i++) {
      if (sittingAbort) break;
      const a = queue[i];
      $('sittingcount').textContent = (i + 1) + ' of ' + queue.length;
      drawSittingQueue(queue, i, completed, false);
      select(a.app + '/' + a.account);
      // Sign-in first: a perishable session has to be fresh when the pull
      // runs, and only a person can make it fresh.
      if (!await askSignedIn(a)) break;
      if (sittingAbort) break;
      // 'all', not 'resume'. Resume selects from the discovery.json this
      // account already has on disk and never asks the provider what exists,
      // so a sitting built on it spent the sign-in it had just asked a person
      // for, printed "Nothing to resume", and called itself done. Run All
      // discovers first, and every app's own "already downloaded" memory
      // skips what is on disk - so this is discover-plus-new-only, which is
      // what a sitting was always meant to be.
      await startRun(a.app, a.account, 'all');
      completed++;
    }
  } finally {
    // A sitting cut short - ended on a sign-in prompt, or Stop mid-run - is
    // not "done": most of the point of this feature is telling a person what
    // still needs them, and treating a half-finished sitting as complete
    // would say the opposite of that.
    const short = queue.length && completed < queue.length;
    $('sittingcount').textContent = !queue.length
      ? $('sittingcount').textContent
      : short
        ? 'stopped after ' + completed + ' of ' + queue.length + ' - '
          + (queue.length - completed) + ' still need a person'
        : 'done - all ' + queue.length + ' sat with';
    drawSittingQueue(queue, -1, completed, short);
    $('sittingask').hidden = true;
    sitting = false;
    onSitGo = null;
    drawSitButton();
    refresh();
  }
}

$('sit').onclick = startSitting;

// ---------------------------------------------------------------------------
// Adding an account
// ---------------------------------------------------------------------------
//
// Two things are being added here, and they are the same operation: a provider
// you have never set up (its `primary`), and a second person's account of a
// provider you already use (`config.<label>.json`). Both were a hand-copied
// JSON file before this, and the panel's only way of mentioning either was to
// list the un-set-up one as a problem.

/* Providers you have not set up, from the rows the register hides. */
function unconfigured() {
  return STATE.accounts.filter(a => a.needs === 'setup');
}

/* Every provider, and whether it is already set up - the picker offers both,
 * because adding a second account of one you use is the same operation. */
function providerOptions() {
  const seen = new Map();
  for (const a of STATE.accounts) {
    const known = seen.get(a.app);
    // "set up" for an app means at least one account of it is.
    seen.set(a.app, (known || false) || a.needs !== 'setup');
  }
  return [...seen.entries()]
    .map(([app, ready]) => ({app, ready}))
    .sort((x, y) => (x.ready - y.ready) || x.app.localeCompare(y.app));
}

function drawAddButton() {
  const n = unconfigured().length;
  $('addsub').textContent = n
    ? n + ' provider' + (n === 1 ? '' : 's') + ' you have not set up yet'
    : 'a second person, or a provider you set up elsewhere';
  $('add').disabled = !!es;
}

function openAdder() {
  const sel = $('addapp');
  sel.innerHTML = '';
  for (const {app, ready} of providerOptions()) {
    sel.append(new Option(ready ? app + ' (already set up)' : app, app));
  }
  $('adder').hidden = false;
  $('addnote').className = 'adder-note';
  syncAdderLabel();
  sel.focus();
}

function closeAdder() {
  $('adder').hidden = true;
  $('addnote').textContent = '';
  $('addlabel').value = '';
}

/* What the label should default to, and what creating it will actually do.
 * Said before the click rather than after: this writes a file. */
function syncAdderLabel() {
  const app = $('addapp').value;
  const mine = STATE.accounts.filter(a => a.app === app);
  const hasPrimary = mine.some(a => a.needs !== 'setup');
  const input = $('addlabel');
  // A provider with no accounts yet gets `primary`, which is the name the
  // whole project uses for "the config.json next to the script". A second
  // account has to be named, so there is nothing to suggest.
  input.dataset.suggest = hasPrimary ? '' : 'primary';
  input.placeholder = hasPrimary ? 'e.g. spouse' : 'primary';
  const label = input.value.trim() || input.dataset.suggest || '<label>';
  const note = $('addnote');
  note.className = 'adder-note';
  if (!hasPrimary) {
    note.innerHTML = 'Copies <code>' + escapeHtml(app) +
      '/config.example.json</code> to its <code>config.json</code>. Nothing ' +
      'is downloaded and nothing is signed in to — the account appears in the ' +
      'register and you sign in from there.';
  } else {
    note.innerHTML = 'Creates <code>' + escapeHtml(app) + '/config.' +
      escapeHtml(label) + '.json</code> from that provider’s existing ' +
      'config, with its own output folder and browser profile so the two ' +
      'accounts never share a download history.';
  }
}

$('add').onclick = () => { if ($('adder').hidden) openAdder(); else closeAdder(); };
$('addcancel').onclick = closeAdder;
$('addapp').onchange = syncAdderLabel;
$('addlabel').oninput = syncAdderLabel;

$('adder').onsubmit = async e => {
  e.preventDefault();
  const app = $('addapp').value;
  // Sent as typed, not lowercased here: the label becomes a filename, and a
  // page that quietly rewrites it creates an account under a name the person
  // did not choose. The server refuses it and says why instead.
  const account = $('addlabel').value.trim() ||
                  $('addlabel').dataset.suggest || '';
  const note = $('addnote');
  if (!account) {
    note.className = 'adder-note bad';
    note.textContent = 'Name the account first — it becomes the config\'s '
      + 'filename, so it has to be something you chose.';
    return;
  }
  $('addgo').disabled = true;
  try {
    const r = await fetch('/api/accounts', {method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({app, account})});
    const body = await r.json().catch(() => ({}));
    if (!r.ok) {
      // The server's own sentence, not a generic failure: it says which rule
      // was broken (the label, a name already taken, a missing template).
      note.className = 'adder-note bad';
      note.textContent = body.detail || ('could not create it (HTTP ' + r.status + ')');
      return;
    }
    closeAdder();
    await load();
    select(body.app + '/' + body.account);
    const extra = body.shared_browser
      ? ' It points at the same browser as the other accounts here — a second '
        + 'person needs their own browser service, and this file pointed at it.'
      : '';
    notice('Created <code>' + escapeHtml(body.config) + '</code>, filing into <code>'
         + escapeHtml(body.output_dir) + '</code>. Edit it if you want it '
         + 'somewhere else, then press Sign in.' + escapeHtml(extra), false);
  } finally {
    $('addgo').disabled = false;
  }
};

load();
