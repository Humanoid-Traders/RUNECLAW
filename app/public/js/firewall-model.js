/**
 * RUNECLAW — Transaction Firewall model (Guardian).
 *
 * A pure, deterministic PRE-SIGN safety scan. Paste anything an agent is about
 * to act on — a message, a token's metadata, a URL, an address, a signing
 * request — and it flags the patterns behind prompt-injection and malicious
 * signing: AI-manipulation instructions, seed-phrase lures, drain/approval
 * language, hidden/bidi/homoglyph characters, risky URLs and address poisoning.
 *
 * Heuristic FLAGS, never a verdict: it explains WHY something looks dangerous so
 * a human (or a gated agent) decides. Fully local — no network, no data leaves
 * the page. §4: no account, no funds path; it reads text and warns.
 *
 * Dual export: browser (window.FirewallModel) + Node (require) for unit tests.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.FirewallModel = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const SEV_W = { high: 3, medium: 2, low: 1 };

  // Each rule: { kind, severity, title, why, re } — re captures the offending bit.
  const RULES = [
    { kind: 'injection', severity: 'high', title: 'Prompt-injection instruction',
      why: 'Text is trying to override an AI agent’s instructions — a classic way to hijack an autonomous signer.',
      re: /\b(ignore|disregard|forget|override)\b[^.\n]{0,40}\b(previous|prior|above|earlier|all|your)\b[^.\n]{0,25}\b(instructions?|prompts?|rules?|guardrails?)\b/i },
    { kind: 'injection', severity: 'high', title: 'Role / system-prompt hijack',
      why: 'Attempts to redefine the agent’s role or system prompt to bypass its safety.',
      re: /\b(you are now|new (system )?prompt|as an? (ai|assistant|agent|language model)|system:\s|<\|?(system|im_start)\|?>)\b/i },
    { kind: 'injection', severity: 'medium', title: 'Act-without-confirmation lure',
      why: 'Pressures the agent to act without asking the human — bypassing the approval gate.',
      re: /\b(do not|don'?t)\b[^.\n]{0,25}\b(tell|inform|warn|ask|confirm|mention)\b|\bwithout\b[^.\n]{0,20}\b(asking|confirmation|approval|permission)\b/i },
    { kind: 'secret', severity: 'high', title: 'Seed-phrase / private-key request',
      why: 'Anything asking for a seed phrase, mnemonic or private key is a wallet-drain attempt. RUNECLAW never needs it.',
      re: /\b(seed phrase|recovery phrase|mnemonic|private key|secret key|12[- ]?word|24[- ]?word)\b/i },
    { kind: 'drain', severity: 'high', title: 'Drain / sweep language',
      why: 'Instructions to move ALL funds to an address are the signature of a drainer.',
      re: /\b(send|transfer|move|withdraw|sweep|forward)\b[^.\n]{0,25}\b(all|entire|everything|full balance|your (funds|balance|wallet|assets))\b|\bdrain\b/i },
    { kind: 'approval', severity: 'high', title: 'Unlimited approval / setApprovalForAll',
      why: 'Unlimited allowances or setApprovalForAll let a contract move your tokens forever — the top NFT/token theft vector.',
      re: /\bsetApprovalForAll\b|\bincreaseAllowance\b|\b(unlimited|infinite|max(imum)?)\b[^.\n]{0,15}\b(approval|allowance)\b|\bapprove\b[^.\n]{0,15}\b(unlimited|infinite|max|all)\b|0x[fF]{60,64}\b/ },
    { kind: 'urgency', severity: 'low', title: 'Urgency / pressure',
      why: 'Time pressure is a social-engineering tell designed to stop you from checking.',
      re: /\b(act now|right now|immediately|urgent(ly)?|expires? (in|soon|today)|limited time|last chance|hurry|before it'?s too late|24 hours)\b/i },
    { kind: 'lure', severity: 'medium', title: 'Scam-lure keywords',
      why: 'Airdrop / claim / connect-wallet / validate lures front most wallet-drain flows.',
      re: /\b(claim your|free airdrop|connect wallet to (claim|verify)|validate your wallet|restore wallet|unlock (your )?wallet|migrate your (funds|assets)|double your|guaranteed (returns?|profit)|risk[- ]?free|100% safe)\b/i },
    { kind: 'impersonation', severity: 'medium', title: 'Authority / verification claim',
      why: '“Official / verified / support” claims in untrusted text are a trust-me tell, not proof.',
      re: /\b(official|verified|whitelisted|approved by|customer support|wallet support|security team)\b/i },
  ];

  const ZERO_WIDTH = /[​-‍﻿⁠᠎]/;
  const BIDI = /[‪-‮⁦-⁩]/;
  const CYRILLIC_GREEK = /[Ѐ-ӿͰ-Ͽ]/;
  const EVM = /0x[0-9a-fA-F]{40}\b/g;
  const URL_RE = /\bhttps?:\/\/[^\s"'<>]+/gi;
  const SHORTENERS = /(bit\.ly|tinyurl\.com|t\.co|cutt\.ly|is\.gd|goo\.gl|rb\.gy|shorturl|rebrand\.ly)/i;

  function snippet(s, m) {
    if (m == null) return '';
    const i = Math.max(0, m.index - 12);
    return String(s).slice(i, m.index + (m[0] ? m[0].length : 0) + 12).replace(/\s+/g, ' ').trim().slice(0, 80);
  }

  function scanText(input) {
    const text = String(input == null ? '' : input);
    const flags = [];
    const seen = new Set();
    const add = (f) => { const k = f.kind + '|' + f.title; if (seen.has(k)) return; seen.add(k); flags.push(f); };

    if (!text.trim()) return { level: 'empty', score: 0, flags: [], summary: 'Paste something to scan.' };

    for (const r of RULES) {
      r.re.lastIndex = 0;
      const m = r.re.exec(text);
      if (m) add({ kind: r.kind, severity: r.severity, title: r.title, why: r.why, match: snippet(text, m) });
    }

    // Hidden / obfuscation characters.
    if (ZERO_WIDTH.test(text)) add({ kind: 'hidden', severity: 'high', title: 'Zero-width / invisible characters',
      why: 'Invisible characters hide instructions or forge look-alike names that your eyes can’t see but a parser reads.', match: '' });
    if (BIDI.test(text)) add({ kind: 'hidden', severity: 'high', title: 'Right-to-left / bidi override',
      why: 'Bidirectional override characters can visually reorder text so an address or amount reads differently than it executes.', match: '' });
    if (CYRILLIC_GREEK.test(text) && /[a-zA-Z]/.test(text)) add({ kind: 'homoglyph', severity: 'medium', title: 'Mixed-script (homoglyph) characters',
      why: 'Cyrillic/Greek letters mixed with Latin fake trusted names (e.g. a fake “USDC”, “Uniswap”).', match: '' });

    // URLs.
    const urls = text.match(URL_RE) || [];
    for (const u of urls) {
      const low = u.toLowerCase();
      if (/^http:\/\//i.test(u)) add({ kind: 'url', severity: 'medium', title: 'Insecure http:// link',
        why: 'A wallet action over plain http can be intercepted. Trusted dapps use https.', match: u.slice(0, 80) });
      if (/xn--/i.test(low)) add({ kind: 'url', severity: 'high', title: 'Punycode (look-alike) domain',
        why: 'Punycode (xn--) domains impersonate real sites with look-alike Unicode letters.', match: u.slice(0, 80) });
      if (/:\/\/\d{1,3}(\.\d{1,3}){3}(:|\/|$)/.test(low)) add({ kind: 'url', severity: 'high', title: 'Raw IP-address link',
        why: 'A bare IP instead of a domain is a strong phishing / drainer tell.', match: u.slice(0, 80) });
      if (SHORTENERS.test(low)) add({ kind: 'url', severity: 'medium', title: 'URL shortener',
        why: 'Shorteners hide the real destination — never connect a wallet through one.', match: u.slice(0, 80) });
    }

    // Address poisoning: two distinct EVM addresses that share the same first-6
    // and last-4 (what most UIs show) are engineered to be mistaken for each other.
    const addrs = (text.match(EVM) || []).map((a) => a.toLowerCase());
    const uniq = Array.from(new Set(addrs));
    for (let i = 0; i < uniq.length; i++) {
      for (let j = i + 1; j < uniq.length; j++) {
        const a = uniq[i], b = uniq[j];
        if (a.slice(0, 8) === b.slice(0, 8) && a.slice(-4) === b.slice(-4)) {
          add({ kind: 'address', severity: 'high', title: 'Address-poisoning look-alike',
            why: 'Two different addresses share the same visible start and end — a copy-paste trap that redirects funds.', match: uniq[i].slice(0, 10) + '…' + uniq[i].slice(-4) });
        }
      }
    }

    const score = flags.reduce((a, f) => a + (SEV_W[f.severity] || 1), 0);
    const hasHigh = flags.some((f) => f.severity === 'high');
    const level = (hasHigh || score >= 5) ? 'danger' : score >= 2 ? 'caution' : 'clear';
    const summary = level === 'clear'
      ? (flags.length ? 'Minor notes only — nothing that looks like an attack.' : 'No known injection or drain patterns found. Still verify addresses and amounts yourself.')
      : level === 'caution'
        ? 'Some risky patterns — slow down and verify before signing.'
        : 'Dangerous patterns detected — do NOT sign or connect until you understand every flag.';
    return { level, score, flags, summary };
  }

  return { scanText, RULES };
}));
