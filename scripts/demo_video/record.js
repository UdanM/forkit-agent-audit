// Records the AutoDiscover guided demo: drives the app in headless Chromium, adds captions and
// highlights (overlay.js), and saves screencast frames for build.py. See README.md.
const { chromium } = require('playwright');
const fs = require('fs'); const path = require('path'); const { execSync } = require('child_process');
const OUT = process.argv[2] || 'frames';
const URL = 'http://localhost:8501';
const FAST = process.env.FAST === '1';          // quick dry runs: short holds
const sleep = ms => new Promise(r => setTimeout(r, FAST ? Math.min(ms, 400) : ms));

(async () => {
  fs.rmSync(OUT, { recursive: true, force: true }); fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 810 }, deviceScaleFactor: 4 / 3, acceptDownloads: true });
  await ctx.addInitScript({ path: path.join(__dirname, 'overlay.js') });
  const page = await ctx.newPage();
  const sb = page.locator('section[data-testid="stSidebar"]');
  const settle = async (ms = 1600) => {
    await page.waitForTimeout(400);
    await page.waitForFunction(() => !document.querySelector('[data-testid="stStatusWidget"] [data-testid="stStatusWidgetRunningIcon"], [data-testid="stStatusWidget"] img'), null, { timeout: 30000 }).catch(() => {});
    await page.waitForTimeout(ms);
  };

  // ── prepare a clean demo before recording ──
  await page.goto(URL); await page.waitForTimeout(4000);
  await sb.getByRole('button', { name: 'Start the demo over' }).click(); await settle(1500);
  await page.reload(); await settle(2500);

  // ── screencast ──
  const cdp = await ctx.newCDPSession(page);
  const frames = [];
  let n = 0;
  cdp.on('Page.screencastFrame', async f => {
    const file = path.join(OUT, `f${String(n++).padStart(6, '0')}.jpg`);
    fs.writeFileSync(file, Buffer.from(f.data, 'base64'));
    frames.push({ file, t: f.metadata.timestamp });
    cdp.send('Page.screencastFrameAck', { sessionId: f.sessionId }).catch(() => {});
  });
  const chapters = [];
  let t0 = null;
  const now = () => Date.now() / 1000;
  const chapter = title => chapters.push({ title, t: now() });

  // ── helpers ──
  let cur = { x: 720, y: 400 };
  const dv = (fn, ...args) => page.evaluate(([fn, args]) => window.__dv[fn](...args), [fn, args]);
  async function moveTo(x, y) { await dv('cursor', x, y); cur = { x, y }; await sleep(650); }
  async function click(loc, { wait = 1100 } = {}) {
    await loc.scrollIntoViewIfNeeded(); await page.waitForTimeout(250);
    const b = await loc.boundingBox();
    const x = b.x + Math.min(b.width / 2, 60), y = b.y + b.height / 2;
    await moveTo(x, y); await dv('ripple', x, y);
    await loc.click({ position: { x: x - b.x, y: y - b.y } });
    if (x < 300) { await page.mouse.move(800, 380); await dv('cursor', 800, 380); }
    await settle(wait);
  }
  async function scrollTo(loc, offset = 90) {
    await loc.evaluate((el, offset) => {
      let p = el.parentElement;
      while (p && !(p.scrollHeight > p.clientHeight + 4 && /(auto|scroll)/.test(getComputedStyle(p).overflowY))) p = p.parentElement;
      if (!p) { el.scrollIntoView({ behavior: 'smooth', block: 'start' }); return; }
      const start = p.scrollTop;
      const end = Math.max(0, Math.min(p.scrollHeight - p.clientHeight, start + el.getBoundingClientRect().top - p.getBoundingClientRect().top - offset));
      const dur = Math.min(900, 250 + Math.abs(end - start) * 0.6), t0 = performance.now();
      return new Promise(res => { const step = now => { const k = Math.min(1, (now - t0) / dur), e = k < .5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
        p.scrollTop = start + (end - start) * e; k < 1 ? requestAnimationFrame(step) : res(); }; requestAnimationFrame(step); });
    }, offset);
    await page.waitForTimeout(250);
  }
  async function hl(groups) {
    const hs = [];
    for (const g of groups) hs.push({ els: await g.loc.elementHandles(), label: g.label || '', pad: g.pad ?? 8 });
    await page.evaluate(gs => window.__dv.hl(gs), hs);
  }
  const clearHl = () => page.evaluate(() => window.__dv.hl([]));
  const cap = (tag, ttl, txt, hold = false, pos = 'bottom') => dv('caption', tag, ttl, txt, hold, pos);
  const noCap = () => dv('caption', '', '', '', false);
  async function title(k, t, s, f, ms = 3300) { marks.push({ t: now(), s: (FAST ? 0.4 : ms / 1000) + 1.0, kind: 'title' }); await noCap(); await clearHl(); await dv('card', ''); await dv('title', k, t, s, f); await page.waitForTimeout(500); await sleep(ms); await dv('title', ''); await page.waitForTimeout(500); }
  const card = (html, pos) => dv('card', html, pos);
  const tab = name => page.getByRole('tab', { name, exact: true });
  const alert = text => page.locator('[data-testid="stAlert"]', { hasText: text }).first();
  async function pick(selKey, optRe) {
    await click(page.locator(`.st-key-${selKey} [role="combobox"]`), { wait: 300 });
    await page.keyboard.type(optRe, { delay: 70 }); await page.waitForTimeout(500);
    await page.keyboard.press('Enter'); await settle(1500);
  }
  const marks = [];
  const holdMs = s => { marks.push({ t: now(), s }); return s * 1000; };

  await cdp.send('Page.startScreencast', { format: 'jpeg', quality: 88, maxWidth: 1920, maxHeight: 1080, everyNthFrame: 1 });
  await page.waitForTimeout(300); t0 = now();

  // ── INTRO ──
  chapter('Intro');
  await title('Forkit AI hackathon · recorded demo', 'AutoDiscover', 'Safe onboarding of AI agents and tools.<br>Every tool gets a passport, every action gets a trail, and nothing is guessed.',
    'Five pause points · github.com/eren-mutlu/forkit-autodiscover-hackathon', 4200);
  await cap('START', 'A clean start', 'Demo mode reads two sample settings files: <b>Claude Desktop</b> and <b>Cursor</b>. Nothing is registered yet.');
  await moveTo(700, 300); await sleep(3000);

  // ── PAUSE 1 ──
  chapter('Pause 1 · Metadata-only discovery');
  await title('Pause 1 of 5', 'Metadata-only discovery', 'Secrets are stripped before anything is saved.<br>What the settings do not say stays UNKNOWN. Zero guessing.');
  await cap('PAUSE 1', 'What the settings file really contains', 'This sample Claude Desktop config holds a database password inside an environment variable.');
  await card(`<h5>Input · fixtures/claude_desktop_config.json</h5><pre>"postgres-mcp": {
  "command": "npx",
  "args": ["-y", "@modelcontextprotocol/server-postgres"],
  "env": {
    "POSTGRES_URL": "postgresql://analytics_admin:<span class="bad">FAKE_s3cr3t_DO_NOT_USE_9f3k</span>@db-analytics.internal…"
  }
}</pre>No owner. No version. Let's scan it.`);
  await sleep(6000); await card('');
  await click(page.getByRole('button', { name: 'Scan for AI tools' }), { wait: 2200 });
  await scrollTo(page.locator('[data-testid="stDataFrame"]').first(), 120);
  await hl([{ loc: page.locator('[data-testid="stDataFrame"]').first(), label: 'Owner and access: UNKNOWN when not stated' }]);
  await cap('PAUSE 1', '3 tools found, nothing guessed', '<b>Owner</b> is UNKNOWN for all three, and <b>access</b> is read-only only where the settings prove it (the <code>ro</code> mount). Everything else stays UNKNOWN.', true);
  await sleep(holdMs(6));
  await clearHl();
  await click(page.getByText('More details: command, version, arguments, environment, fingerprint'), { wait: 1200 });
  await hl([{ loc: page.locator('[data-testid="stDataFrame"]').nth(1), label: 'Version and Enabled: UNKNOWN' }]);
  await cap('PAUSE 1', 'More facts, same rule', 'No config states a version or whether the tool is enabled, so both say <b>UNKNOWN</b>. The transport is stdio because the settings show a local command.');
  await sleep(holdMs(5));
  const secrets = page.locator('.card', { hasText: 'Secrets stripped at discovery' });
  await scrollTo(secrets, 160);
  await hl([{ loc: page.locator('.card', { hasText: 'Secrets stripped at discovery' }).or(alert('Disk check')), label: 'Proof on disk' }]);
  await cap('PAUSE 1', 'Proof, not a promise', 'The app searches its own saved files for every raw secret value it saw: <b>0 of 2</b> found. The sidebar check <b>No secrets stored</b> says PASS.', true);
  await sleep(holdMs(6)); await clearHl();

  // ── PAUSE 2 ──
  chapter('Pause 2 · Persistent ID and no false merges');
  await title('Pause 2 of 5', 'Persistent IDs, no false merges', 'Identities survive restarts and machines.<br>Two tools with the same name in different configs stay two tools.');
  await click(tab('Passports'), { wait: 1500 });
  await scrollTo(page.getByText('How the passport ID is calculated'), 120);
  await click(page.getByText('How the passport ID is calculated'), { wait: 900 });
  await hl([{ loc: page.locator('[data-testid="stExpander"]', { hasText: 'How the passport ID is calculated' }), label: 'Forkit Core compute_id' }]);
  await cap('PAUSE 2', 'Where the ID comes from', 'The ID uses only <b>where</b> a tool is configured: settings file (relative path), tool name and command. No username, no machine name, no clock. Same tool, same ID, on any computer.');
  await sleep(holdMs(6)); await clearHl();
  await click(page.getByText('How the passport ID is calculated'), { wait: 900 });
  for (let i = 0; i < 3; i++) await click(page.getByRole('button', { name: 'Register tool' }).first(), { wait: 1500 });
  const coll = page.locator('[class*="st-key-idcard_collision_"]');
  await scrollTo(coll.first(), 130);
  await hl([{ loc: coll.nth(0), label: 'postgres-mcp · Claude Desktop' }, { loc: coll.nth(1), label: 'postgres-mcp · Cursor' }]);
  await cap('PAUSE 2', 'Same name, two passports', 'Both are called <b>postgres-mcp</b>, but they come from different settings files, so they get <b>different passport IDs</b> (feb915… vs 927e4b…). No false merge.', true);
  await sleep(holdMs(7)); await clearHl();
  await click(sb.getByRole('button', { name: 'Simulate app restart' }), { wait: 2200 });
  await scrollTo(alert('Restart recognized'), 140);
  await hl([{ loc: alert('Restart recognized'), label: 'Memory wiped, then re-scanned' }]);
  await cap('PAUSE 2', 'Restart: same IDs, no duplicates', 'Everything in memory was cleared and the settings were scanned again. <b>3 of 3</b> tools matched their existing passports. Nothing was registered twice.', true);
  await sleep(holdMs(6)); await clearHl();
  // drift keeps the same ID
  await click(sb.getByText('Simulate a settings edit'), { wait: 2200 });
  await click(tab('Changes'), { wait: 1500 });
  await scrollTo(page.locator('[data-testid="stTabPanel"]:visible .card').first(), 110);
  await cap('PAUSE 2', 'An edit is a change, not a new tool', 'Someone edited filesystem-mcp in the settings. It keeps the <b>same passport ID</b>. The diff is recorded, and access drops to UNKNOWN because read-only is no longer proven.');
  const changeCard = page.locator('[data-testid="stTabPanel"]:visible .card').first();
  if (await changeCard.count()) await hl([{ loc: changeCard, label: 'Same passport, change flagged' }]);
  await sleep(holdMs(6)); await clearHl();
  await click(sb.getByText('Simulate a settings edit'), { wait: 2000 });

  // ── PAUSE 3 ──
  chapter('Pause 3 · Out-of-expected behavior and lineage');
  await title('Pause 3 of 5', 'Out-of-expected behavior and lineage', 'An unauthorized action is caught as an incident<br>and traced back to the exact passport, and the settings file, it came from.');
  await click(tab('Event review'), { wait: 1500 });
  await scrollTo(tab('Event review'), 20);
  await pick('ev_select', 'evt-0002');
  await cap('PAUSE 3', 'A runtime event: filesystem-mcp tried to write', '<b>evt-0002</b>: <code>fs.write_file</code> on q3_summary.md. This tool is approved for reads only. First, try to link it without consent.');
  await sleep(holdMs(3));
  await click(page.getByRole('button', { name: 'Link action to tool' }), { wait: 1800 });
  await hl([{ loc: alert('Blocked'), label: 'No consent, no link' }]);
  await cap('PAUSE 3', 'No consent, no link', 'Linking an action to a passport needs explicit operator consent. Without it, nothing is written.');
  await sleep(holdMs(4)); await clearHl();
  await click(page.getByText('I give consent to link this action to the tool'), { wait: 900 });
  await click(page.getByRole('button', { name: 'Link action to tool' }), { wait: 2200 });
  await scrollTo(page.locator('.card.audit'), 110);
  await hl([{ loc: page.locator('.card.audit'), label: 'MISSING APPROVAL' }]);
  await cap('PAUSE 3', 'Linked, and flagged', 'The audit record shows <b>NO APPROVAL ON FILE</b> for a write, and policy rule <b>POL-005</b> (mutating action without approval). Runtime consent is UNKNOWN because the log does not say.');
  await sleep(holdMs(5)); await clearHl();
  await scrollTo(page.locator('.card.incident'), 110);
  await hl([{ loc: page.locator('.card.incident'), label: 'INCIDENT · out of expected behavior' }]);
  await cap('PAUSE 3', 'Incident, traced to its parent passport', 'A read-only tool tried to write. The incident names its <b>parent passport</b> and the <b>settings file</b> that declared the tool: event → passport → config. This is the link people say cannot be done.', true);
  await sleep(holdMs(8)); await clearHl();
  // wrong-tool link is refused
  await scrollTo(tab('Event review'), 20);
  await pick('ev_select', 'evt-0003');
  await pick('target_evt-0003', 'filesystem-mcp');
  await click(page.getByText('I give consent to link this action to the tool'), { wait: 900 });
  await click(page.getByRole('button', { name: 'Link action to tool' }), { wait: 1800 });
  await hl([{ loc: alert('Blocked'), label: 'Wrong passport refused' }]);
  await cap('PAUSE 3', 'No false links either', '<b>evt-0003</b> came from postgres-mcp in Cursor. Forcing it onto the filesystem-mcp passport is refused, even with consent.');
  await sleep(holdMs(4)); await clearHl();
  // delegation lineage on the activity timeline
  await click(tab('Activity timeline'), { wait: 2000 });
  const helper = page.locator('li', { hasText: 'Report helper' }).first();
  await scrollTo(helper, 220);
  await hl([{ loc: helper, label: 'Helper → parent agent' }]);
  await cap('PAUSE 3', 'Delegated work keeps its lineage', 'A helper process wrote to a protected file. An independent observer ties it back to the agent that <b>delegated</b> it (Report assistant), and flags no approval, protected file, read-only run wrote. Unknown actors stay UNKNOWN.', true);
  await sleep(holdMs(7)); await clearHl();

  // ── PAUSE 4 ──
  chapter('Pause 4 · Visual provenance map');
  await title('Pause 4 of 5', 'The whole system on one screen', 'Read each row left to right:<br>settings file → tool → passport → runtime event → incident → open checks.');
  await click(tab('Map'), { wait: 2000 });
  const mapFrame = page.locator('.st-key-graphframe_live');
  await scrollTo(page.getByText('In plain words').first(), 115);
  await cap('PAUSE 4', 'In plain words', 'One line per tool. filesystem-mcp has <b>1 INCIDENT</b>, every tool is registered, and each has open checks for a person.');
  await hl([{ loc: page.locator('.card', { hasText: 'In plain words' }).first(), label: 'Summary' }]);
  await sleep(holdMs(4)); await clearHl();
  await noCap();
  await scrollTo(mapFrame, 150);
  await cap('PAUSE 4', 'Provenance map', 'Follow the <b>evt-0002</b> row: Claude Desktop settings → filesystem-mcp → passport 9b360ff7 → write with no approval → incident. Same-name postgres-mcp tools sit on separate rows.', true, 'top');
  await hl([{ loc: mapFrame, label: 'Live provenance map', pad: 4 }]);
  await sleep(holdMs(22)); await clearHl();

  // ── PAUSE 5 ──
  chapter('Pause 5 · Extras beyond the brief');
  await title('Pause 5 of 5', 'Beyond the brief', 'Policy-as-code · tamper-evident logs and audit ZIP · real local machine scan');
  // policy as code
  await click(tab('Passports'), { wait: 1500 });
  await scrollTo(page.locator('[class*="st-key-idcard_"]').first(), 110);
  await card(`<h5>Policy-as-code · fixtures/policies.json</h5><pre>{
  "rule_id": "POL-001",
  "title": "Unapproved database MCP server",
  "severity": "high",
  "applies_to": "passport",
  "when": { "category_in": ["database"],
            "approval": [...] }
}</pre><b>18 declarative rules</b> (POL-001 to POL-018) for passports, changes, events and activity.<br><span class="warn">Nothing in the file is executed as code.</span>`, { top: '330px' });
  await hl([{ loc: page.locator('[class*="st-key-idcard_"]').first(), label: 'Rule results on every passport' }]);
  await cap('EXTRA 1', 'Policy-as-code', 'Rules live in a JSON file a security team can review in a pull request. Results appear on every passport card, in the map and in the audit report.', true);
  await sleep(holdMs(7)); await clearHl(); await card('');
  // tamper-evident log
  await click(tab('Activity timeline'), { wait: 2000 });
  await scrollTo(page.getByRole('button', { name: 'Save log checkpoint' }), 120);
  await click(page.getByRole('button', { name: 'Save log checkpoint' }), { wait: 1800 });
  await click(page.getByRole('button', { name: 'Simulate log tampering' }), { wait: 2200 });
  const summaryCard = page.locator('.card', { hasText: 'In plain words' }).first();
  await scrollTo(summaryCard, 110); await page.waitForTimeout(700);
  await hl([{ loc: summaryCard, label: 'Tampering detected' }]); await page.waitForTimeout(500);
  await hl([{ loc: summaryCard, label: 'Tampering detected' }]);
  await cap('EXTRA 2', 'Tamper-evident activity log', 'A checkpoint hash was saved, then someone rewrote an old log line. The app notices at once: <b>Someone rewrote the log</b>, a critical finding.', true);
  await sleep(holdMs(6)); await clearHl();
  await click(page.getByRole('button', { name: 'Restore original log' }), { wait: 1800 });
  // audit ZIP
  const zipBtn = sb.getByRole('button', { name: 'Download audit report (ZIP)' });
  await zipBtn.scrollIntoViewIfNeeded();
  const dlP = page.waitForEvent('download');
  await click(zipBtn, { wait: 800 });
  const dl = await dlP; const zp = path.join(OUT, '..', 'audit.zip'); await dl.saveAs(zp);
  const listing = execSync(`python3 ${path.join(__dirname, 'zipinfo.py')} ${zp}`).toString();
  await card(`<h5>Downloaded · ${dl.suggestedFilename()}</h5><pre>${listing}</pre>`, { top: '120px', left: '330px', right: 'auto', width: '640px' });
  await hl([{ loc: sb.locator('.metrics').last(), label: 'Integrity checks' }]);
  await cap('EXTRA 3', 'Audit report as a ZIP', 'One click exports the report, the registry and the inputs, with a SHA-256 over the report. The export refuses to build if any raw secret would leak (fail-closed).', true);
  await sleep(holdMs(7)); await clearHl(); await card('');
  // real local machine scan
  await click(sb.getByText('Scan this computer', { exact: true }), { wait: 2500 });
  await click(tab('Tools'), { wait: 1200 });
  await click(page.getByRole('button', { name: 'Scan this computer', exact: true }), { wait: 3000 });
  await scrollTo(page.getByText('Locations checked'), 120);
  await hl([{ loc: page.locator('[data-testid="stDataFrame"]').first(), label: 'Well-known config locations' }]);
  await cap('EXTRA 4', 'Real, read-only scan of this computer', 'The same engine reads the real Claude Desktop, Claude Code, Cursor, VS Code, Windsurf and Codex locations, plus installed AI CLIs. Usernames become <b>~</b>, secrets are redacted, nothing is written to those files.', true);
  await sleep(holdMs(6)); await clearHl();
  await scrollTo(page.locator('[data-testid="stDataFrame"]').nth(1), 120);
  await hl([{ loc: page.locator('[data-testid="stDataFrame"]').nth(1), label: 'Found on the recording machine' }]);
  await cap('EXTRA 4', 'Found on this machine', 'MCP servers from Cursor, VS Code and Codex, and the <b>claude</b> CLI on PATH. A CLI gets its own passport type, so it never merges with an MCP server of the same name.');
  await sleep(holdMs(5)); await clearHl();
  await click(sb.getByText('Demo with sample data', { exact: true }), { wait: 2000 });

  // ── OUTRO ──
  chapter('Summary');
  await title('AutoDiscover', 'Every tool gets a passport.<br>Every action gets a trail.<br>Nothing is guessed.',
    'Metadata-only discovery · persistent IDs · no false merges · incidents with lineage · provenance map · policy-as-code · tamper-evident audit',
    'Try it: forkit-autodiscover-hackathon.streamlit.app · Code: github.com/eren-mutlu/forkit-autodiscover-hackathon', 7000);
  await dv('title', 'AutoDiscover', 'Every tool gets a passport.<br>Every action gets a trail.<br>Nothing is guessed.',
    'Metadata-only discovery · persistent IDs · no false merges · incidents with lineage · provenance map · policy-as-code · tamper-evident audit',
    'Try it: forkit-autodiscover-hackathon.streamlit.app · Code: github.com/eren-mutlu/forkit-autodiscover-hackathon');
  marks.push({ t: now() - 8.5, s: 10, kind: 'title' });
  await page.waitForTimeout(1500);
  const tEnd = now();
  await cdp.send('Page.stopScreencast');
  await page.waitForTimeout(300);
  fs.writeFileSync(path.join(OUT, '..', 'timeline.json'), JSON.stringify({ frames, chapters, tEnd, marks }, null, 1));
  await browser.close();
  console.log('frames', frames.length, 'duration', (tEnd - frames[0].t).toFixed(1));
})().catch(e => { console.error(e); process.exit(1); });
