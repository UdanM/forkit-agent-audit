// Captures high-resolution stills for the projector cut (compose_projector.py turns them into video).
// Usage: node capture_stills.js <out_dir>   (app running on localhost:8501, demo mode)
const { chromium } = require('playwright');
const fs = require('fs'); const path = require('path'); const { execSync } = require('child_process');
const OUT = process.argv[2] || 'stills';
const URL = 'http://localhost:8501';

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2, acceptDownloads: true });
  const page = await ctx.newPage();
  const sb = page.locator('section[data-testid="stSidebar"]');
  const settle = async (ms = 1500) => { await page.waitForTimeout(ms); };
  const tab = name => page.getByRole('tab', { name, exact: true });
  const alert = text => page.locator('[data-testid="stAlert"]', { hasText: text }).first();
  const beats = [];
  const css = '.nextstep{display:none!important} [data-testid="stHeader"]{display:none!important} ' +
              '[data-testid="stMainBlockContainer"]{padding-bottom:400px!important} [data-testid="stToolbar"]{display:none!important}';
  const addCss = () => page.addStyleTag({ content: css });
  async function scrollTo(loc, offset = 40) {
    await loc.first().evaluate((el, offset) => {
      let p = el.parentElement;
      while (p && !(p.scrollHeight > p.clientHeight + 4 && /(auto|scroll)/.test(getComputedStyle(p).overflowY))) p = p.parentElement;
      (p || document.scrollingElement).scrollTop += el.getBoundingClientRect().top - offset;
    }, offset);
    await page.waitForTimeout(500);
  }
  const box = async loc => { const b = await loc.first().boundingBox(); return [b.x, b.y, b.width, b.height]; };
  const union = rs => { const x = Math.min(...rs.map(r => r[0])), y = Math.min(...rs.map(r => r[1]));
    return [x, y, Math.max(...rs.map(r => r[0] + r[2])) - x, Math.max(...rs.map(r => r[1] + r[3])) - y]; };
  async function shot(id, focus, hl = []) {
    await page.mouse.move(1430, 890);
    await page.waitForTimeout(400);
    const file = path.join(OUT, id + '.png');
    await page.screenshot({ path: file });
    beats.push({ id, file, focus, hl });
    console.log('shot', id);
  }
  const MAIN = [316, 0, 1124, 900];   // main column, without the sidebar

  await page.goto(URL); await settle(4000); await addCss();
  await sb.getByRole('button', { name: 'Start the demo over' }).click(); await settle(2000);
  await page.reload(); await settle(3000); await addCss();

  // Pause 1: scan, secrets removed, UNKNOWN
  await scrollTo(page.getByText('Discovered tools'), 30);
  await shot('p1_before', MAIN);
  await page.getByRole('button', { name: 'Scan for AI tools' }).click(); await settle(2500);
  await scrollTo(page.locator('[data-testid="stDataFrame"]'), 60);
  const table = await box(page.locator('[data-testid="stDataFrame"]'));
  const secrets = await box(page.locator('.card', { hasText: 'Secrets stripped at discovery' }));
  const disk = await box(alert('Disk check'));
  await shot('p1_after', union([table, secrets, disk]), [table, union([secrets, disk])]);

  // Pause 2: restart keeps IDs; same name stays two tools
  await tab('Passports').click(); await settle(1500);
  for (let i = 0; i < 3; i++) { await page.getByRole('button', { name: 'Register tool' }).first().click(); await settle(1300); }
  await sb.getByRole('button', { name: 'Simulate app restart' }).click(); await settle(2500);
  await scrollTo(alert('Restart recognized'), 60);
  await shot('p2_restart', await box(alert('Restart recognized')), [await box(alert('Restart recognized'))]);
  const coll = page.locator('[class*="st-key-idcard_collision_"]');
  await scrollTo(coll, 40);
  const c0 = await box(coll.nth(0)), c1 = await box(coll.nth(1));
  await shot('p2_cards', union([c0, c1]), [c0, c1]);

  // Pause 3: unapproved write becomes an incident, traced to its source
  await tab('Event review').click(); await settle(1500);
  await scrollTo(page.getByText('Action a tool took (runtime event)'), 120);
  await page.locator('.st-key-ev_select [role="combobox"]').click({ position: { x: 60, y: 15 } });
  await page.keyboard.type('evt-0002', { delay: 50 }); await page.keyboard.press('Enter'); await settle(1500);
  await page.getByText('I give consent to link this action to the tool').click(); await settle(900);
  await scrollTo(page.getByText('Action a tool took (runtime event)'), 120);
  const sel = union([await box(page.locator('.st-key-ev_select')), await box(page.getByRole('button', { name: 'Link action to tool' }))]);
  await shot('p3_before', sel, [await box(page.locator('.st-key-ev_select'))]);
  await page.getByRole('button', { name: 'Link action to tool' }).click(); await settle(2500);
  await scrollTo(page.locator('.card.incident'), 60);
  const inc = await box(page.locator('.card.incident'));
  const parent = await box(page.locator('.card.incident .kv', { hasText: 'Parent passport' }));
  const appr = await box(page.locator('.card.incident .kv', { hasText: 'Approval' }));
  await shot('p3_after', inc, [parent, appr]);

  // Pause 4: provenance map
  await tab('Map').click(); await settle(2000);
  const frame = page.locator('.st-key-graphframe_live');
  await scrollTo(frame, 60);
  const fb = await box(frame);
  const par = l => l.first().locator('xpath=..');
  const tool = await box(par(frame.getByText('Command: docker')));
  const rowR = union([tool, await box(par(frame.getByText('evt-0002'))), await box(par(frame.getByText(/^Incident$/))),
                      await box(par(frame.getByText('Mutating action without approval')))]);
  const cfg = await box(par(frame.getByText('claude-desktop').first()));
  const row = [cfg[0], rowR[1], rowR[0] + rowR[2] - cfg[0], rowR[3]];
  await shot('p4_map', fb, [row]);
  beats[beats.length - 1].row = row;

  // Pause 5a: company rules as a file
  await tab('Changes').click(); await settle(1500);
  await scrollTo(page.getByText('Rule checks', { exact: true }), 40);
  const rh = await box(page.getByText('Rule checks', { exact: true }));
  const firstRule = await box(page.locator('[data-testid="stTabPanel"]:visible .card', { hasText: 'Unapproved database MCP server' }));
  await shot('p5_rules', union([rh, firstRule]), [firstRule]);

  // Pause 5b: one-click audit ZIP, checked
  const dlP = page.waitForEvent('download');
  await sb.getByRole('button', { name: 'Download audit report (ZIP)' }).click();
  const dl = await dlP; const zp = path.join(OUT, 'audit.zip'); await dl.saveAs(zp);
  const info = JSON.parse(execSync(`python3 ${path.join(__dirname, 'zipsummary.py')} ${zp}`).toString());
  await page.evaluate(info => {
    const c = document.createElement('div'); c.id = 'zipcard';
    c.innerHTML = `<div style="font-size:15px;letter-spacing:.12em;color:#BEAFFF;font-weight:800">AUDIT REPORT · ONE CLICK</div>
      <div style="font-size:30px;font-weight:800;margin:10px 0 18px;color:#fff">${info.name}</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px 26px;font:16px/1.5 'JetBrains Mono',monospace;color:#d8dcff">${info.files.map(f => `<div>${f}</div>`).join('')}</div>
      <div style="margin-top:22px;font-size:26px;font-weight:800;color:#7be0a5">✓ ${info.ok} of ${info.total} files verified by checksum</div>
      <div style="margin-top:6px;font-size:20px;color:#c9cbe6">Change any byte and the check fails.</div>`;
    Object.assign(c.style, { position: 'fixed', left: '380px', top: '160px', width: '920px', padding: '34px 40px', background: '#0d1024',
      border: '2px solid #6d5fd6', borderRadius: '18px', fontFamily: 'Inter, system-ui, sans-serif', zIndex: 99999, boxShadow: '0 20px 60px rgba(0,0,0,.6)' });
    document.body.appendChild(c);
  }, info);
  await page.waitForTimeout(400);
  const zb = await box(page.locator('#zipcard'));
  await shot('p5_zip', zb, []);

  fs.writeFileSync(path.join(OUT, 'beats.json'), JSON.stringify(beats, null, 1));
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
