// Injected into every page load: fake cursor, caption bar, highlight boxes, title cards, info card.
(() => {
  const css = `
  [data-testid="stMainBlockContainer"]{padding-bottom:300px !important}
  #dv-root{position:fixed;inset:0;pointer-events:none;z-index:2147483000;font-family:Inter,"Source Sans Pro",system-ui,sans-serif}
  #dv-cursor{position:fixed;left:0;top:0;width:22px;height:22px;margin:-3px 0 0 -3px;transition:transform .55s cubic-bezier(.4,0,.2,1);z-index:5}
  #dv-cursor svg{filter:drop-shadow(0 2px 4px rgba(0,0,0,.6))}
  .dv-ripple{position:fixed;width:44px;height:44px;margin:-22px 0 0 -22px;border-radius:50%;border:3px solid #BEAFFF;opacity:.9;
    animation:dvr .6s ease-out forwards;z-index:4}
  @keyframes dvr{from{transform:scale(.3);opacity:1}to{transform:scale(1.4);opacity:0}}
  #dv-cap{position:fixed;left:316px;right:24px;bottom:22px;background:rgba(14,16,34,.94);border:1px solid #6d5fd6;border-radius:14px;
    padding:14px 20px;color:#eef;box-shadow:0 10px 40px rgba(0,0,0,.55);display:none;gap:16px;align-items:flex-start;z-index:3}
  #dv-cap .tag{flex:none;background:#BEAFFF;color:#120f2a;font-weight:800;font-size:13px;letter-spacing:.06em;border-radius:8px;padding:6px 10px;margin-top:2px}
  #dv-cap .ttl{font-size:19px;font-weight:700;margin-bottom:3px;color:#fff}
  #dv-cap .txt{font-size:15.5px;line-height:1.42;color:#d6d8ee}
  #dv-cap .txt b{color:#fff}
  #dv-cap .hold{flex:none;margin-left:auto;align-self:center;font-size:13px;font-weight:700;color:#ffd27a;border:1.5px solid #ffd27a;border-radius:999px;padding:5px 12px;display:none;white-space:nowrap}
  .dv-hl{position:fixed;border:3px solid #ffd27a;border-radius:12px;box-shadow:0 0 0 4000px rgba(5,6,15,.38),0 0 24px rgba(255,210,122,.55);
    transition:all .35s ease;z-index:2}
  .dv-hl.multi{box-shadow:0 0 24px rgba(255,210,122,.55)}
  .dv-hl .lbl{position:absolute;top:-30px;left:-3px;background:#ffd27a;color:#1a1400;font-weight:800;font-size:13px;padding:4px 10px;border-radius:8px 8px 8px 0;white-space:nowrap}
  #dv-title{position:fixed;inset:0;background:radial-gradient(ellipse at 30% 20%,#2a2160 0%,#0b0d1d 60%,#07080f 100%);display:flex;flex-direction:column;
    justify-content:center;padding:0 140px;opacity:0;transition:opacity .45s ease;z-index:10}
  #dv-title.on{opacity:1}
  #dv-title .k{color:#BEAFFF;font-weight:800;letter-spacing:.18em;font-size:18px;text-transform:uppercase;margin-bottom:18px}
  #dv-title .t{color:#fff;font-size:58px;font-weight:800;line-height:1.08;letter-spacing:-.02em;max-width:1100px}
  #dv-title .s{color:#c9cbe6;font-size:24px;line-height:1.45;margin-top:22px;max-width:1050px}
  #dv-title .f{position:absolute;left:140px;bottom:56px;color:#8e90b5;font-size:16px}
  #dv-pause{position:fixed;right:26px;bottom:96px;background:#ffd27a;color:#1a1400;font-weight:900;font-size:26px;letter-spacing:.06em;
    border-radius:14px;padding:10px 22px;box-shadow:0 10px 30px rgba(0,0,0,.55);display:none;z-index:7}
  #dv-cap.big .ttl{font-size:24px;margin:0}#dv-cap.big{align-items:center}
  #dv-card{position:fixed;right:28px;top:96px;width:560px;background:rgba(12,14,30,.97);border:1px solid #6d5fd6;border-radius:14px;padding:18px 20px;
    color:#e7e8f7;box-shadow:0 14px 50px rgba(0,0,0,.6);display:none;z-index:6;font-size:15px;line-height:1.45}
  #dv-card h5{margin:0 0 10px 0;font-size:16px;color:#BEAFFF;letter-spacing:.04em;text-transform:uppercase}
  #dv-card pre{background:#05060d;border:1px solid #262a4a;border-radius:10px;padding:12px;font:13px/1.5 "JetBrains Mono",ui-monospace,monospace;color:#d8dcff;white-space:pre-wrap;margin:8px 0}
  #dv-card .bad{color:#ff8c8c}#dv-card .ok{color:#7be0a5}#dv-card .warn{color:#ffd27a}
  `;
  function init() {
    if (document.getElementById('dv-root')) return;
    const st = document.createElement('style'); st.textContent = css; document.head.appendChild(st);
    const r = document.createElement('div'); r.id = 'dv-root';
    r.innerHTML = `<div id="dv-cursor"><svg width="26" height="26" viewBox="0 0 24 24"><path d="M3 2l7.5 19 2.6-7.9L21 10.5z" fill="#fff" stroke="#111" stroke-width="1.4" stroke-linejoin="round"/></svg></div>
      <div id="dv-cap"><div class="tag"></div><div><div class="ttl"></div><div class="txt"></div></div><div class="hold">⏸ PAUSE HERE</div></div>
      <div id="dv-title"><div class="k"></div><div class="t"></div><div class="s"></div><div class="f"></div></div>
      <div id="dv-card"></div><div id="dv-pause"></div>`;
    document.body.appendChild(r);
    let hls = [];
    function loop() {
      for (const h of hls) {
        const els = h.els.filter(e => e.isConnected);
        if (!els.length) continue;
        let L = 1e9, T = 1e9, R = -1e9, B = -1e9;
        for (const e of els) { const b = e.getBoundingClientRect(); L = Math.min(L, b.left); T = Math.min(T, b.top); R = Math.max(R, b.right); B = Math.max(B, b.bottom); }
        const p = h.pad, s = `${L - p}|${T - p}|${R - L + 2 * p}|${B - T + 2 * p}`;
        if (s !== h.last) { h.last = s; Object.assign(h.box.style, { left: L - p + 'px', top: T - p + 'px', width: R - L + 2 * p + 'px', height: B - T + 2 * p + 'px' }); }
      }
      requestAnimationFrame(loop);
    }
    requestAnimationFrame(loop);
    window.__dv = {
      cursor(x, y) { document.getElementById('dv-cursor').style.transform = `translate(${x}px,${y}px)`; },
      ripple(x, y) { const d = document.createElement('div'); d.className = 'dv-ripple'; d.style.left = x + 'px'; d.style.top = y + 'px'; r.appendChild(d); setTimeout(() => d.remove(), 700); },
      caption(tag, ttl, txt, hold, pos) {
        const c = document.getElementById('dv-cap');
        if (!ttl) { c.style.display = 'none'; return; }
        c.querySelector('.tag').textContent = tag; c.querySelector('.ttl').innerHTML = ttl; c.querySelector('.txt').innerHTML = txt;
        c.querySelector('.hold').style.display = hold ? 'block' : 'none'; c.style.display = 'flex';
        if (pos === 'top') { c.style.top = '18px'; c.style.bottom = 'auto'; } else { c.style.top = 'auto'; c.style.bottom = '22px'; }
      },
      hl(groups) { // groups: [{els:[...], label, pad}]
        for (const h of hls) h.box.remove(); hls = [];
        const multi = groups.length > 1;
        for (const g of groups) {
          const box = document.createElement('div'); box.className = 'dv-hl' + (multi ? ' multi' : '');
          if (g.label) box.innerHTML = `<div class="lbl">${g.label}</div>`;
          r.appendChild(box); hls.push({ els: g.els, pad: g.pad ?? 8, box, last: '' });
        }
      },
      title(k, t, s, f) {
        const el = document.getElementById('dv-title');
        if (!t) { el.classList.remove('on'); return; }
        el.querySelector('.k').textContent = k; el.querySelector('.t').innerHTML = t; el.querySelector('.s').innerHTML = s || ''; el.querySelector('.f').innerHTML = f || '';
        el.classList.add('on');
      },
      pause(label) { const p = document.getElementById('dv-pause'); p.textContent = label || ''; p.style.display = label ? 'block' : 'none'; },
      big(on) { document.getElementById('dv-cap').classList.toggle('big', !!on); },
      card(html, pos) {
        const c = document.getElementById('dv-card');
        if (!html) { c.style.display = 'none'; return; }
        c.innerHTML = html; c.style.display = 'block';
        Object.assign(c.style, { top: '96px', right: '28px', left: 'auto', bottom: 'auto' }, pos || {});
      },
    };
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
