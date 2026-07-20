/**
 * RCWalletPicker — a Privy-style "Select your wallet" modal, dependency-free.
 *
 * Discovery is EIP-6963 (Multi Injected Provider Discovery): every installed
 * browser wallet — MetaMask, Coinbase Wallet, Rainbow, Rabby, Brave, Phantom,
 * OKX, … — announces itself with its own name and icon, so the list renders
 * real wallets the user actually has, not a static catalog. Legacy fallback:
 * a wallet that predates 6963 still appears as "Browser wallet" via
 * window.ethereum. No SDK, no external requests, CSP-clean (wallet icons are
 * data: URIs supplied by the wallets themselves).
 *
 * Usage:  const provider = await RCWalletPicker.pick();   // null = cancelled
 * Fast path: exactly one wallet installed → returned immediately, no modal.
 */
(function () {
  'use strict';

  const found = new Map();          // uuid -> { info, provider }

  window.addEventListener('eip6963:announceProvider', (ev) => {
    const d = ev && ev.detail;
    if (d && d.info && d.info.uuid && d.provider) found.set(d.info.uuid, d);
  });
  function discover() {
    try { window.dispatchEvent(new Event('eip6963:requestProvider')); } catch (e) { /* old browsers */ }
  }
  discover();

  function candidates() {
    const list = [...found.values()];
    // Legacy single-injection wallet with no 6963 announcement.
    if (!list.length && window.ethereum) {
      list.push({
        info: { uuid: 'legacy', name: 'Browser wallet', icon: '', rdns: 'legacy' },
        provider: window.ethereum,
      });
    }
    return list;
  }

  const CSS = `
  .rcwp-back{position:fixed;inset:0;background:rgba(0,0,0,.6);backdrop-filter:blur(3px);z-index:2000;display:flex;align-items:center;justify-content:center;padding:16px}
  .rcwp{background:var(--surface,#12131a);border:1px solid var(--line,#2a2c38);border-radius:16px;width:min(380px,94vw);max-height:82vh;display:flex;flex-direction:column;padding:20px;box-shadow:0 24px 64px rgba(0,0,0,.5)}
  .rcwp h2{font-size:17px;text-align:center;margin:2px 0 14px;color:var(--text,#eee)}
  .rcwp-x{position:absolute;margin-left:calc(min(380px,94vw) - 40px);margin-top:-6px;background:none;border:0;color:var(--text-3,#888);font-size:18px;cursor:pointer;padding:6px}
  .rcwp-search{width:100%;margin-bottom:12px}
  .rcwp-list{overflow-y:auto;display:flex;flex-direction:column;gap:8px}
  .rcwp-item{display:flex;align-items:center;gap:12px;width:100%;text-align:left;background:var(--bg,#0a0b10);border:1px solid var(--line,#2a2c38);border-radius:12px;padding:12px 14px;cursor:pointer;color:var(--text,#eee);font-size:15px}
  .rcwp-item:hover,.rcwp-item:focus-visible{border-color:var(--gold,#c9a860);outline:none}
  .rcwp-item img{width:28px;height:28px;border-radius:6px}
  .rcwp-item .rcwp-fallback{width:28px;height:28px;border-radius:6px;background:var(--surface-2,#1a1c26);display:inline-flex;align-items:center;justify-content:center}
  .rcwp-empty{color:var(--text-2,#aaa);font-size:14px;line-height:1.6;text-align:center;padding:12px 4px}
  .rcwp-note{color:var(--text-3,#777);font-size:11.5px;text-align:center;margin-top:12px;line-height:1.5}
  `;

  function ensureStyles() {
    if (document.getElementById('rcwp-styles')) return;
    const s = document.createElement('style');
    s.id = 'rcwp-styles';
    s.textContent = CSS;
    document.head.appendChild(s);
  }

  function openModal(list) {
    return new Promise((resolve) => {
      ensureStyles();
      const back = document.createElement('div');
      back.className = 'rcwp-back';
      back.setAttribute('role', 'dialog');
      back.setAttribute('aria-modal', 'true');
      back.setAttribute('aria-label', 'Select your wallet');

      const card = document.createElement('div');
      card.className = 'rcwp';

      const x = document.createElement('button');
      x.className = 'rcwp-x';
      x.setAttribute('aria-label', 'Close');
      x.textContent = '✕';

      const h = document.createElement('h2');
      h.textContent = 'Select your wallet';

      const search = document.createElement('input');
      search.className = 'input rcwp-search';
      search.type = 'search';
      search.placeholder = `Search ${list.length} wallet${list.length === 1 ? '' : 's'}…`;
      search.setAttribute('aria-label', 'Search wallets');

      const ul = document.createElement('div');
      ul.className = 'rcwp-list';

      function render(filter) {
        ul.textContent = '';
        const q = String(filter || '').toLowerCase();
        const rows = list.filter(w => !q || w.info.name.toLowerCase().includes(q));
        if (!rows.length) {
          const e = document.createElement('div');
          e.className = 'rcwp-empty';
          e.textContent = 'No wallet matches that search.';
          ul.appendChild(e);
          return;
        }
        for (const w of rows) {
          const b = document.createElement('button');
          b.className = 'rcwp-item';
          b.type = 'button';
          if (w.info.icon && /^data:image\//.test(w.info.icon)) {
            const img = document.createElement('img');
            img.src = w.info.icon;
            img.alt = '';
            b.appendChild(img);
          } else {
            const f = document.createElement('span');
            f.className = 'rcwp-fallback';
            f.textContent = '👛';
            b.appendChild(f);
          }
          const nm = document.createElement('span');
          nm.textContent = w.info.name;
          b.appendChild(nm);
          b.onclick = () => done(w.provider);
          ul.appendChild(b);
        }
      }

      const note = document.createElement('p');
      note.className = 'rcwp-note';
      note.textContent = 'Read-only linking: your wallet signs one login message — never a transaction. '
        + 'RUNECLAW can see balances, not move them.';

      function done(result) {
        document.removeEventListener('keydown', onKey);
        back.remove();
        resolve(result);
      }
      function onKey(e) { if (e.key === 'Escape') done(null); }

      x.onclick = () => done(null);
      back.onclick = (e) => { if (e.target === back) done(null); };
      search.oninput = () => render(search.value);
      document.addEventListener('keydown', onKey);

      card.appendChild(x);
      card.appendChild(h);
      card.appendChild(search);
      card.appendChild(ul);
      card.appendChild(note);
      back.appendChild(card);
      document.body.appendChild(back);
      render('');
      search.focus();
    });
  }

  async function pick() {
    discover();
    // 6963 announcements are synchronous replies to the request event, but
    // give slow injectors one frame before deciding what's installed.
    await new Promise(r => setTimeout(r, 60));
    const list = candidates();
    if (!list.length) return null;                  // caller shows install/QR help
    if (list.length === 1) return list[0].provider; // fast path: no modal needed
    return openModal(list);
  }

  window.RCWalletPicker = { pick, _candidates: candidates };
})();
