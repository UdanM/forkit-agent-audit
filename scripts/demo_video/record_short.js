// Records the 60-second AutoDiscover cut: drives the app in headless Chromium, adds captions and
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

  // ── 60-second cut: five pauses, one-line captions ──
  await dv('big', true);
  const P = async (n, line, pos = 'bottom') => { await cap(`PAUSE ${n}`, line, '', false, pos); await dv('pause', `⏸ PAUSE ${n}/5`); };
  const endP = async () => { await dv('pause', ''); await noCap(); await clearHl(); await card(''); };
  chapter('Intro');
  await title('Forkit AI hackathon', 'AutoDiscover in 60 seconds', 'Every AI tool gets a passport. Every action gets a trail. Nothing is guessed.', '', 2200);

  chapter('Pause 1 · Secrets redacted, UNKNOWN stays UNKNOWN');
  await click(page.getByRole('button', { name: 'Scan for AI tools' }), { wait: 1800 });
  await scrollTo(page.locator('[data-testid="stDataFrame"]').first(), 70);
  await hl([{ loc: page.locator('[data-testid="stDataFrame"]').first(), label: 'Owner: UNKNOWN' },
            { loc: page.locator('.card', { hasText: 'Secrets stripped at discovery' }).or(alert('Disk check')), label: 'Password → [REDACTED]' }]);
  await P(1, 'Secrets are redacted before saving. Unknown facts stay UNKNOWN.');
  await sleep(holdMs(8)); await endP();

  chapter('Pause 2 · Same ID after restart, no false merges');
  await click(tab('Passports'), { wait: 1000 });
  for (let i = 0; i < 3; i++) await click(page.getByRole('button', { name: 'Register tool' }).first(), { wait: 1100 });
  await click(sb.getByRole('button', { name: 'Simulate app restart' }), { wait: 2000 });
  const coll = page.locator('[class*="st-key-idcard_collision_"]');
  await scrollTo(alert('Restart recognized'), 20);
  await hl([{ loc: alert('Restart recognized'), label: 'Restart: same 3 IDs' },
            { loc: coll.nth(0), label: 'postgres-mcp · Claude Desktop' }, { loc: coll.nth(1), label: 'postgres-mcp · Cursor' }]);
  await P(2, 'Same IDs after a restart. Same name, different config: two passports.');
  await sleep(holdMs(8)); await endP();

  chapter('Pause 3 · Unapproved action caught and linked');
  await click(tab('Event review'), { wait: 1000 });
  await scrollTo(tab('Event review'), 20);
  await pick('ev_select', 'evt-0002');
  await click(page.getByText('I give consent to link this action to the tool'), { wait: 700 });
  await click(page.getByRole('button', { name: 'Link action to tool' }), { wait: 2000 });
  await scrollTo(page.locator('.card.incident'), 90);
  await hl([{ loc: page.locator('.card.incident'), label: 'INCIDENT → parent passport → settings file' }]);
  await P(3, 'Unapproved write: incident, traced to its parent passport and config file.');
  await sleep(holdMs(8)); await endP();

  chapter('Pause 4 · Provenance map');
  await click(tab('Map'), { wait: 1600 });
  const mapFrame = page.locator('.st-key-graphframe_live');
  await scrollTo(mapFrame, 150);
  await hl([{ loc: mapFrame, label: 'settings file → tool → passport → event → incident', pad: 4 }]);
  await cap('PAUSE 4', 'The whole system on one screen. Read each row left to right.', '', false, 'top');
  await dv('pause', '');
  await sleep(holdMs(15)); await endP();

  chapter('Pause 5 · Policy-as-code and audit ZIP');
  const zipBtn = sb.getByRole('button', { name: 'Download audit report (ZIP)' });
  await zipBtn.scrollIntoViewIfNeeded();
  const dlP = page.waitForEvent('download');
  await click(zipBtn, { wait: 600 });
  const dl = await dlP; const zp = path.join(OUT, '..', 'audit.zip'); await dl.saveAs(zp);
  const listing = execSync(`python3 ${path.join(__dirname, 'zipinfo.py')} ${zp}`).toString();
  await card(`<h5>Audit ZIP · tamper-evident</h5><pre>${listing}</pre>`, { top: '20px', left: '320px', right: 'auto', width: '560px' });
  await dv('pause', '');
  await page.evaluate(html => { const c = document.createElement('div'); c.id = 'dv-card2'; c.innerHTML = html;
    Object.assign(c.style, { position: 'fixed', right: '28px', top: '20px', width: '500px', background: 'rgba(12,14,30,.97)', border: '1px solid #6d5fd6',
      borderRadius: '14px', padding: '18px 20px', color: '#e7e8f7', zIndex: 2147483006, font: '15px/1.45 Inter,system-ui,sans-serif' });
    document.body.appendChild(c); }, `<div style="color:#BEAFFF;font-weight:700;letter-spacing:.04em;margin-bottom:8px">POLICY-AS-CODE · fixtures/policies.json</div>
<pre style="background:#05060d;border:1px solid #262a4a;border-radius:10px;padding:12px;font:13px/1.5 ui-monospace,monospace;color:#d8dcff;margin:0 0 8px 0">{ "rule_id": "POL-005",
  "title": "Mutating action without approval",
  "severity": "critical",
  "applies_to": "event",
  "when": { "action_mutating": true,
            "approval": ["missing"] } }</pre><b>18 declarative rules.</b> Nothing in the file is executed.`);
  await P(5, 'Rules as code. A one-click audit ZIP, checksums verified, secrets fail-closed.');
  await sleep(holdMs(8)); await endP(); await page.evaluate(() => document.getElementById('dv-card2')?.remove());

  chapter('Summary');
  await title('AutoDiscover', 'Every tool gets a passport.<br>Every action gets a trail.<br>Nothing is guessed.', '',
    'forkit-autodiscover-hackathon.streamlit.app', 2500);
  await dv('title', 'AutoDiscover', 'Every tool gets a passport.<br>Every action gets a trail.<br>Nothing is guessed.', '', 'forkit-autodiscover-hackathon.streamlit.app');
  await page.waitForTimeout(300);
  const tEnd = now();
  await cdp.send('Page.stopScreencast');
  await page.waitForTimeout(300);
  fs.writeFileSync(path.join(OUT, '..', 'timeline.json'), JSON.stringify({ frames, chapters, tEnd, marks }, null, 1));
  await browser.close();
  console.log('frames', frames.length, 'duration', (tEnd - frames[0].t).toFixed(1));
})().catch(e => { console.error(e); process.exit(1); });
