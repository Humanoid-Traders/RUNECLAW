const crypto = require('crypto');
const path = require('path');

// Self-heal a wiped .env BEFORE any secret is read. The Python bot mirrors the
// web-pairing secrets (BOT_SYNC_SECRET / WEB_GATEWAY_SECRET / WEB_CREDS_KEY)
// into an encrypted vault under data/ that persists across redeploys. This
// separate process reads the SAME vault so the website comes back on its own
// after an env wipe instead of FATAL-exiting below. Fail-open: restores nothing
// (and never throws) if the vault or master key is absent. Only fills in
// secrets missing from the environment — a live value always wins.
try {
  const { restoreFromVault } = require('./lib/secrets_vault');
  const healed = restoreFromVault();
  if (healed.length) {
    console.warn(`SECRETS VAULT restored ${healed.length} web secret(s) missing from the `
      + `environment: ${healed.join(', ')} — running on vault-backed secrets. `
      + 'Restore your .env and ensure data/ persists across redeploys.');
  }
} catch (err) {
  console.warn('secrets vault restore skipped:', err && err.message);
}

// JWT_SECRET resolution, most-stable-first:
//   1. Explicit JWT_SECRET env (recommended; required for multi-replica).
//   2. Derived deterministically from BOT_SYNC_SECRET (already a required
//      >=32-char env). Same env -> same secret on EVERY restart/redeploy, even
//      on hosts with ephemeral disks. This fixes the "site keeps logging me
//      out" failure mode: the previous file-persisted secret (data/.jwt_secret)
//      was wiped on every restart of an ephemeral host, minting a new secret
//      and invalidating every session.
//   3. Ephemeral random (dev only, when BOT_SYNC_SECRET is also missing —
//      the fatal check below exits shortly after anyway).
if (!process.env.JWT_SECRET) {
  if (process.env.BOT_SYNC_SECRET && process.env.BOT_SYNC_SECRET.length >= 32) {
    process.env.JWT_SECRET = crypto
      .createHmac('sha256', process.env.BOT_SYNC_SECRET)
      .update('runeclaw-jwt-signing-v1')
      .digest('hex');
    console.log('JWT_SECRET derived from BOT_SYNC_SECRET — sessions survive restarts. '
      + 'Set an explicit JWT_SECRET for multi-replica deployments.');
  } else if (process.env.EPHEMERAL === 'true' || process.env.NODE_ENV !== 'production') {
    process.env.JWT_SECRET = crypto.randomBytes(48).toString('hex');
    console.log('WARNING: JWT_SECRET auto-generated (ephemeral) — sessions reset on restart.');
  }
  // If production without a derivable secret, auth.js enforces the fatal exit.
}
// RC-AUD-015: never ship a hardcoded sync secret — it grants write access to the
// /api/bot/sync endpoints (which overwrite trade/equity data). Require it to be
// provided via env in all modes; the bot and web app must share the same value.
// The previously committed default must be rotated — it is exposed in git history.
if (!process.env.BOT_SYNC_SECRET || process.env.BOT_SYNC_SECRET.length < 32) {
  console.error('FATAL: BOT_SYNC_SECRET must be set to a shared secret of >=32 chars (see .env.example).');
  process.exit(1);
}

const { auditConfig } = require('./lib/config_audit');
// Surface every silently-degrading config (SMTP, gateway/creds secrets,
// APP_BASE_URL, OAuth) once at boot — and, in production, refuse to start on a
// fatal (e.g. a set-but-malformed WEB_CREDS_KEY that would fail every exchange-
// key submission). Runs after the JWT/BOT_SYNC hard checks above.
auditConfig();

const express = require('express');
const { migrate } = require('./db');
const { router: authRouter } = require('./auth');
const tradesRouter = require('./routes/trades');
const syncRouter = require('./routes/sync');
const marketRouter = require('./routes/market');
const insightRouter = require('./routes/insight');
const signalsRouter = require('./routes/signals');
const credentialsRouter = require('./routes/credentials');
const controlsRouter = require('./routes/controls');
const chatRouter = require('./routes/chat');
const publicChatRouter = require('./routes/public_chat');
const webtradeRouter = require('./routes/webtrade');
const portfolioRouter = require('./routes/portfolio');
const leaderboardRouter = require('./routes/leaderboard');
const trackRouter = require('./routes/track');
const labRouter = require('./routes/lab');
const feedRouter = require('./routes/feed');
const reportsRouter = require('./routes/reports');
const profileRouter = require('./routes/profile');
const { router: streamRouter } = require('./routes/stream');

const app = express();

// Behind the deployment's reverse proxy, req.ip is the proxy's address unless
// Express is told to trust the X-Forwarded-For hop. Without this, every
// per-IP rate limiter (public market data, /mcp, login attempts) collapses
// into ONE shared bucket for all visitors — a single client can exhaust the
// global budget, and abuse can't be attributed to a source address.
app.set('trust proxy', 1);

// Security headers — BEFORE the static handler so every response (including
// static-served HTML) carries them. The CSP allows inline script/style (the
// pages use both) but pins script sources to this origin plus the Telegram
// login widget — an injected <script src> from anywhere else won't execute,
// which is the second line of defense behind the app's HTML escaping.
const CSP = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline' https://telegram.org",
  "style-src 'self' 'unsafe-inline'",
  // blob: is needed for the WebGL agent viewer (GLTF decodes embedded textures
  // to same-origin blob: URLs); it's ephemeral and same-origin, not a network
  // egress. worker-src blob: covers optional GLTF Draco/KTX2 decoders.
  "img-src 'self' data: blob:",
  "font-src 'self'",
  "connect-src 'self' blob:",
  "worker-src 'self' blob:",
  "frame-src https://oauth.telegram.org",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join('; ');
app.use((req, res, next) => {
  res.setHeader('X-Content-Type-Options', 'nosniff');
  res.setHeader('X-Frame-Options', 'DENY');
  res.setHeader('Referrer-Policy', 'strict-origin-when-cross-origin');
  res.setHeader('Content-Security-Policy', CSP);
  // Meaningful only over HTTPS; browsers ignore it on plain HTTP (local dev).
  res.setHeader('Strict-Transport-Security', 'max-age=15552000');
  next();
});

// WEB-VISION: the chat route accepts optional image attachments (chart /
// position screenshots, base64), which exceed the 1mb default. Give ONLY
// /api/chat a larger parser — it runs first for that path, so the global 1mb
// parser below skips the already-parsed body; every other route stays at 1mb.
app.use('/api/chat', express.json({ limit: '7mb' }));
app.use(express.json({ limit: '1mb' })); // Cap payload size
// Cache policy: HTML must never be cached (deploys ship new markup that
// references version-tagged assets, e.g. /styles.css?v=2) — a cached HTML +
// stale-CSS mix renders the dashboard completely unstyled. Assets get a
// moderate cache; the ?v= query busts them on every deploy that changes them.
app.use(express.static(path.join(__dirname, 'public'), {
  maxAge: '1h',
  setHeaders: (res, filePath) => {
    if (filePath.endsWith('.html')) res.setHeader('Cache-Control', 'no-cache');
    else if (/\.(css|js|woff2)$/.test(filePath)) res.setHeader('Cache-Control', 'public, max-age=86400');
  },
}));

// API routes
app.use('/api/auth', authRouter);
app.use('/api/trades', tradesRouter);
app.use('/api/bot/sync', syncRouter);
app.use('/api/market', marketRouter);
app.use('/api/insight', insightRouter);
app.use('/api/patterns', require('./routes/patterns'));
app.use('/api/macro', require('./routes/macro'));
// Readiness composes several read-only signals; mount the specific path BEFORE
// the /api/guardian prefix so it wins the match.
app.use('/api/guardian/readiness', require('./routes/guardian_readiness'));
app.use('/api/guardian/review', require('./routes/guardian_review'));
app.use('/api/guardian', require('./routes/guardian'));
app.use('/api/signals', signalsRouter);
app.use('/api/credentials', credentialsRouter);
app.use('/api/controls', controlsRouter);
app.use('/api/chat', chatRouter);
app.use('/api/llm', require('./routes/llm'));
app.use('/api/staking', require('./routes/staking'));
app.use('/api/public/chat', publicChatRouter);
app.use('/api/contract', require('./routes/contract'));
app.use('/api/trade', webtradeRouter);
app.use('/api/portfolio', portfolioRouter);
app.use('/api/leaderboard', leaderboardRouter);
app.use('/api/public/proofofpnl', require('./routes/public_proofofpnl'));
app.use('/api/public/flight', require('./routes/public_flight'));
app.use('/api/public/user-strategies', require('./routes/public_user_strategies'));
app.use('/api/public/leaderboard', require('./routes/public_leaderboard'));
app.use('/api/public/strategies', require('./routes/public_strategies'));
app.use('/api/public/letter', require('./routes/public_letter'));
app.use('/api/public/agent', require('./routes/public_agent'));
app.use('/api/public/invite', require('./routes/public_invite'));
app.use('/api/public', trackRouter);
app.use('/api/lab', labRouter);
app.use('/api/feed', feedRouter);
app.use('/api/reports', reportsRouter);
app.use('/api/profile', profileRouter);
app.use('/api/push', require('./routes/push'));
app.use('/api/copy', require('./routes/copy'));
app.use('/api/alerts', require('./routes/alerts'));
app.use('/api/strategies', require('./routes/user_strategies'));
app.use('/api/replay', require('./routes/replay'));
app.use('/api/letter', require('./routes/letter'));
app.use('/api/wallet', require('./routes/wallet'));
app.use('/api/defi', require('./routes/defi'));
app.use('/api/networth', require('./routes/networth'));
app.use('/api/holdings', require('./routes/holdings'));
app.use('/api/idleyield', require('./routes/idleyield'));
app.use('/api/crossyield', require('./routes/cross_yield'));
app.use('/api/authority', require('./routes/authority'));
app.use('/api/sentry', require('./routes/sentry'));
app.use('/api/positions', require('./routes/positions'));
app.use('/api/arena', require('./routes/arena'));
app.use('/api/since', require('./routes/since'));
app.use('/api/watchlist', require('./routes/watchlist'));
app.use('/api/news', require('./routes/news'));
app.use('/api/proofofpnl', require('./routes/proofofpnl'));
app.use('/api/share', require('./routes/share'));
app.use('/api/airdrops', require('./routes/airdrops'));
app.use('/api/exposure', require('./routes/exposure'));
app.use('/api/research', require('./routes/research'));
app.use('/api/ingest', require('./routes/ingest'));
app.use('/mcp', require('./routes/mcp'));
// ERC-8257 tool surface (well-known manifest + invoke endpoint + operator
// registration plan) — mounted at root because /.well-known/ is absolute.
app.use(require('./routes/tool8257'));
app.use('/api/public/status', require('./routes/public_status'));
// GET /api/version — which commit is serving this process. Public build
// metadata only (short SHA, commit + boot time); no secrets. Lets anyone
// confirm a deploy actually landed instead of guessing.
app.get('/api/version', (req, res) => {
  res.setHeader('Cache-Control', 'no-cache');
  res.json(require('./lib/version').buildInfo());
});
app.use('/api/nft', require('./routes/nft'));
app.use('/api/spot', require('./routes/spot'));
app.use('/api/tax', require('./routes/tax'));
app.use('/api/reputation', require('./routes/reputation'));
app.use('/api/counterparty', require('./routes/counterparty'));
app.use('/api/web3', require('./routes/web3_execute'));   // admin-only preview (mounted first: specific POST /execute)
app.use('/api/web3', require('./routes/web3'));
app.use('/api/dapps', require('./routes/dapps'));

// Single-host dev foot-gun: Express and the bot's gateway both default to
// port 8080. Warn loudly if they would collide.
if (!process.env.BOT_GATEWAY_URL && String(process.env.PORT || 8080) === '8080') {
  console.warn('WARNING: BOT_GATEWAY_URL is unset and PORT is 8080 — the bot gateway '
    + 'default (http://localhost:8080) points at THIS server. Set BOT_GATEWAY_URL '
    + 'to the bot process (aiohttp dashboard) address.');
}
app.use('/api/stream', streamRouter);

// SPA fallback - serve index.html for non-API routes (never cached: see above)
app.get('/', (req, res) => { res.setHeader('Cache-Control', 'no-cache'); res.sendFile(path.join(__dirname, 'public', 'index.html')); });
app.get('/dashboard', (req, res) => { res.setHeader('Cache-Control', 'no-cache'); res.sendFile(path.join(__dirname, 'public', 'dashboard.html')); });
// Account-management landing pages reached from email links.
app.get('/track', (req, res) => { res.setHeader('Cache-Control', 'no-cache'); res.sendFile(path.join(__dirname, 'public', 'track.html')); });
app.get('/proof', (req, res) => { res.setHeader('Cache-Control', 'no-cache'); res.sendFile(path.join(__dirname, 'public', 'proof.html')); });
app.get('/flight', (req, res) => { res.setHeader('Cache-Control', 'no-cache'); res.sendFile(path.join(__dirname, 'public', 'flight.html')); });
app.get('/stress', (req, res) => { res.setHeader('Cache-Control', 'no-cache'); res.sendFile(path.join(__dirname, 'public', 'stress.html')); });
app.get('/sentinel', (req, res) => { res.setHeader('Cache-Control', 'no-cache'); res.sendFile(path.join(__dirname, 'public', 'sentinel.html')); });
app.get('/guardian', (req, res) => { res.setHeader('Cache-Control', 'no-cache'); res.sendFile(path.join(__dirname, 'public', 'guardian.html')); });
app.get('/firewall', (req, res) => { res.setHeader('Cache-Control', 'no-cache'); res.sendFile(path.join(__dirname, 'public', 'firewall.html')); });
app.get('/escape', (req, res) => { res.setHeader('Cache-Control', 'no-cache'); res.sendFile(path.join(__dirname, 'public', 'escape.html')); });
app.get('/intent', (req, res) => { res.setHeader('Cache-Control', 'no-cache'); res.sendFile(path.join(__dirname, 'public', 'intent.html')); });
app.get('/leaderboard', (req, res) => { res.setHeader('Cache-Control', 'no-cache'); res.sendFile(path.join(__dirname, 'public', 'leaderboard.html')); });
// /arena — SSR unfurl: when a season exists, shared links carry its live
// status ("RUNECLAW Arena · Genesis is LIVE"). §4: name + status + window
// only; best-effort with the static pitch as fallback.
app.get('/arena', async (req, res) => {
  res.setHeader('Cache-Control', 'no-cache');
  let title = 'RUNECLAW — Paper Trading Arena';
  let og = 'Trade live markets with a virtual 10,000 vUSDT stake — no API keys, no risk, real prices. Climb the leaderboard on percent return.';
  try {
    const { pool } = require('./db');
    const seasons = require('./lib/arena_seasons');
    const [rows] = await pool.execute('SELECT id, name, starts_at, ends_at FROM arena_seasons');
    const season = rows[0];
    if (season) {
      const st = seasons.seasonStatus(season, new Date());
      const when = st === 'live' ? `is LIVE until ${new Date(season.ends_at).toISOString().slice(0, 10)}`
        : st === 'upcoming' ? `starts ${new Date(season.starts_at).toISOString().slice(0, 10)}`
        : 'has ended — see the Hall of Champions';
      title = `RUNECLAW Arena · ${season.name} ${st === 'live' ? 'is LIVE' : st}`;
      og = `${season.name} ${when}. Same virtual stake for everyone, ranked on percent return under anonymous handles. ${og}`;
    }
  } catch (e) { /* static pitch remains */ }
  const clean = (t) => t.replace(/[&<>"']/g, '');
  const html = require('fs').readFileSync(path.join(__dirname, 'public', 'arena.html'), 'utf8')
    .replace(/__ARENATITLE__/g, clean(title))
    .replace(/__ARENAOG__/g, clean(og));
  res.type('html').send(html);
});
// Public Arena trader card — SSR unfurl: the handle AND the trader's live
// stats are injected into the title/og tags, so a pasted link unfurls as
// "ace — +12.4% on the RUNECLAW Paper Arena · 🔥🎯 5 badges". §4: percent,
// counts and badges only (the same public card the API serves — one source
// of truth via fetchTraderCard). Handle regex-validated then HTML-escaped;
// stat lookups are best-effort with the static text as fallback.
app.get('/trader/:handle', async (req, res) => {
  res.setHeader('Cache-Control', 'no-cache');
  const raw = String(req.params.handle || '').trim();
  const safe = (/^[A-Za-z0-9_]{3,20}$/.test(raw) ? raw : 'Arena trader').replace(/[&<>"']/g, '');
  let ogline = 'Percent return, win rate and achievements on the RUNECLAW Paper Arena — same virtual stake for everyone, ranked on skill alone.';
  let title = `${safe} — RUNECLAW Arena trader`;
  try {
    const card = await require('./lib/arena_trader').fetchTraderCard(raw);
    if (card) {
      const sign = card.return_pct > 0 ? '+' : '';
      title = `${safe} — ${sign}${card.return_pct}% on the RUNECLAW Arena`;
      const icons = (card.badges || []).map((b) => b.icon).join('');
      ogline = `${sign}${card.return_pct}% all-time · ${card.closed_trades} closed trades`
        + (card.win_rate_pct != null ? ` · ${card.win_rate_pct}% win rate` : '')
        + (icons ? ` · ${icons} ${card.badges.length} achievement${card.badges.length === 1 ? '' : 's'}` : '')
        + ' — same virtual stake for everyone, ranked on skill alone. Think you can beat them?';
    }
  } catch (e) { /* stats are decoration on the unfurl — the page still works */ }
  const html = require('fs').readFileSync(path.join(__dirname, 'public', 'trader.html'), 'utf8')
    .replace(/__TITLE__/g, title.replace(/[&<>"']/g, ''))
    .replace(/__OGLINE__/g, ogline.replace(/[&<>"']/g, ''))
    .replace(/__HANDLE__/g, safe);
  res.type('html').send(html);
});
// Digital Asset Links — lets the Android TWA app (Bubblewrap wrapper) open
// this site fullscreen without browser chrome. Served ONLY when the operator
// has configured the app identity; an unconfigured host answers 404 honestly
// rather than shipping an empty/false statement. §F-15: the cert fingerprint
// is public by design (it's printed on every APK).
app.get('/.well-known/assetlinks.json', (req, res) => {
  // The operator's published app identity — PUBLIC by design (the fingerprint
  // is printed on every APK), so shipping it as the default is §F-15-safe.
  // Env still overrides for forks / re-keyed builds; blanking both disables.
  const DEFAULT_ANDROID_PACKAGE = 'com.humanoidtraders.runeclaw';
  const DEFAULT_ANDROID_CERT_SHA256 = '60:49:32:1C:B0:A8:E2:20:61:87:C7:B6:0B:51:19:E9:56:B5:C7:3E:4D:B8:86:C8:64:77:9B:62:E9:67:0F:50';
  const pkg = (process.env.ANDROID_PACKAGE ?? DEFAULT_ANDROID_PACKAGE).trim();
  const sha = (process.env.ANDROID_CERT_SHA256 ?? DEFAULT_ANDROID_CERT_SHA256).trim();
  if (!pkg || !sha) return res.status(404).json({ error: 'not_configured' });
  res.setHeader('Cache-Control', 'public, max-age=3600');
  res.json([{
    relation: ['delegate_permission/common.handle_all_urls'],
    target: { namespace: 'android_app', package_name: pkg,
      sha256_cert_fingerprints: [sha] },
  }]);
});
app.get('/letter/:week?', (req, res) => { res.setHeader('Cache-Control', 'no-cache'); res.sendFile(path.join(__dirname, 'public', 'letter.html')); });
app.get('/wallet-link', (req, res) => { res.setHeader('Cache-Control', 'no-cache'); res.sendFile(path.join(__dirname, 'public', 'wallet-link.html')); });
app.get('/reset', (req, res) => { res.setHeader('Cache-Control', 'no-cache'); res.sendFile(path.join(__dirname, 'public', 'reset.html')); });
app.get('/verify', (req, res) => { res.setHeader('Cache-Control', 'no-cache'); res.sendFile(path.join(__dirname, 'public', 'verify.html')); });
app.get('/agent', (req, res) => { res.setHeader('Cache-Control', 'no-cache'); res.sendFile(path.join(__dirname, 'public', 'agent.html')); });
app.get('/agent/:address', (req, res) => { res.setHeader('Cache-Control', 'no-cache'); res.sendFile(path.join(__dirname, 'public', 'agent-card.html')); });
// Public, shareable Strategy-Agent directory + per-agent profile (marketplace
// slugs, e.g. /agents and /agents/dip-sniper). Bare /agents must precede the
// parametised route so it isn't captured as a slug.
app.get('/agents', (req, res) => { res.setHeader('Cache-Control', 'no-cache'); res.sendFile(path.join(__dirname, 'public', 'agents.html')); });
// NB: /agents/:slug is registered further down (after originFrom) so it can
// server-render per-agent <head> metadata via lib/agent_seo.
app.get('/developers', (req, res) => { res.setHeader('Cache-Control', 'no-cache'); res.sendFile(path.join(__dirname, 'public', 'developers.html')); });
app.get('/status', (req, res) => { res.setHeader('Cache-Control', 'no-cache'); res.sendFile(path.join(__dirname, 'public', 'status.html')); });
// Public 3D Strength Map — the whole USDT-perp universe scored from public
// Bitget market data (percent/ratio + public market prices; no user P&L).
app.get('/strengthmap', (req, res) => { res.setHeader('Cache-Control', 'no-cache'); res.sendFile(path.join(__dirname, 'public', 'strengthmap.html')); });

// SEO discoverability for the public marketplace: robots.txt + a dynamic
// sitemap.xml that enumerates the static public pages plus one URL per catalogue
// agent (best-effort from the gateway — the static pages ship even if it's down).
// Both are generated (no physical files), so these routes win over express.static.
const { buildSitemap, buildRobots } = require('./lib/sitemap');
const originFrom = (req) => {
  const base = (process.env.APP_BASE_URL || process.env.WEBSITE_URL || '').trim();
  if (base) return base.replace(/\/+$/, '');
  const proto = String(req.headers['x-forwarded-proto'] || req.protocol || 'https').split(',')[0].trim();
  const host = req.headers['x-forwarded-host'] || req.get('host') || '';
  return host ? `${proto}://${host}` : '';
};
app.get('/robots.txt', (req, res) => {
  res.setHeader('Content-Type', 'text/plain; charset=utf-8');
  res.setHeader('Cache-Control', 'public, max-age=3600');
  res.send(buildRobots(originFrom(req)));
});
let _sitemapCache = null; // { at, origin, xml }
app.get('/sitemap.xml', async (req, res) => {
  const origin = originFrom(req);
  const now = Date.now();
  const SITEMAP_TTL = 5 * 60 * 1000;
  res.setHeader('Content-Type', 'application/xml; charset=utf-8');
  res.setHeader('Cache-Control', 'public, max-age=3600');
  if (_sitemapCache && _sitemapCache.origin === origin && (now - _sitemapCache.at) < SITEMAP_TTL) {
    return res.send(_sitemapCache.xml);
  }
  let agents = [];
  try {
    const gw = require('./lib/gateway');
    if (gw.isConfigured()) {
      const r = await gw.getGateway('/public/strategies', 10000);
      if (r && r.status >= 200 && r.status < 300 && r.data && Array.isArray(r.data.agents)) {
        agents = r.data.agents;
      }
    }
  } catch (_) { /* best-effort: the static pages still ship */ }
  const xml = buildSitemap(origin, agents);
  _sitemapCache = { at: now, origin, xml };
  res.send(xml);
});

// Public per-agent page with server-rendered <head> metadata so each strategy
// agent unfurls on social + ranks in search with its own title/description/
// JSON-LD. The body is still client-rendered; we only fill the AGENT_SEO marker.
const agentSeo = require('./lib/agent_seo');
const fs = require('fs');
let _strategyHtml = null;
function strategyHtml() {
  if (_strategyHtml == null) {
    _strategyHtml = fs.readFileSync(path.join(__dirname, 'public', 'strategy.html'), 'utf8');
  }
  return _strategyHtml;
}
let _agentCat = null; // { at, agents }
async function agentBySlug(slug) {
  const now = Date.now();
  if (!_agentCat || (now - _agentCat.at) > 5 * 60 * 1000) {
    let agents = [];
    try {
      const gw = require('./lib/gateway');
      if (gw.isConfigured()) {
        const r = await gw.getGateway('/public/strategies', 8000);
        if (r && r.status >= 200 && r.status < 300 && r.data && Array.isArray(r.data.agents)) {
          agents = r.data.agents;
        }
      }
    } catch (_) { /* best-effort: generic card still ships */ }
    _agentCat = { at: now, agents };
  }
  const hit = _agentCat.agents.find((a) => String(a && a.id).toLowerCase() === slug) || null;
  if (hit) return hit;
  // Fall back to a published community strategy so member-authored links unfurl
  // with their own title/description too. DB read (not cached) — cheap.
  try {
    const community = await require('./lib/user_strategies').getPublicBySlug(slug);
    if (community) return community;
  } catch (_) { /* generic card still ships */ }
  return null;
}
app.get('/agents/:slug', async (req, res) => {
  res.setHeader('Cache-Control', 'no-cache');
  const slug = String(req.params.slug || '').toLowerCase();
  let agent = null;
  try {
    if (agentSeo.SLUG_RE.test(slug)) agent = await agentBySlug(slug);
  } catch (_) { /* fall back to the generic card */ }
  res.type('html').send(agentSeo.injectAgentMeta(strategyHtml(), agent, originFrom(req), slug));
});

// Error handler
app.use((err, req, res, next) => {
  console.error('Unhandled error:', err.message);
  res.status(500).json({ error: 'Internal server error' });
});

(async () => {
  try {
    await migrate();
    console.log('Database migrated successfully');
  } catch (err) {
    console.error('Migration failed:', err.message);
    process.exit(1);
  }

  const PORT = process.env.PORT || 8080;
  app.listen(PORT, '0.0.0.0', () => {
    console.log(`RUNECLAW app running on port ${PORT}`);
  });

  // Custom "tell me when…" alert tripwires: evaluate active alerts against
  // public tickers once a minute (skips instantly when none are armed).
  require('./lib/alerts').startAlertEngine();

  // Weekly agent letter: hourly sweep lazily writes the letter for the last
  // completed ISO week and announces it once with a web push.
  require('./lib/letter').startLetterSweep();

  // Follow-the-agent: hourly watch on the public verifiable board; rank moves
  // push a digest to users who opted into the 'board' topic.
  require('./lib/board_watch').startBoardWatch();

  // Follow-an-agent (Marketplace Phase 3b): watch followed agents' live gate
  // matches; a NEW pick pushes to that agent's followers who opted into
  // push_copy. Baseline-on-boot, best-effort, paper-only.
  require('./lib/copy_watch').startCopyWatch();

  // Arena liquidation watch: one push when a paper position drifts within 3%
  // of its liq price (hysteresis re-arm at 6% — never spams a hovering market).
  require('./lib/arena_watch').startArenaWatch();

  // Season ceremonies: the starting gun (upcoming→live) and the final
  // whistle (→ended, crowning the champion) announce themselves — durable
  // flags mean a restart never replays a ceremony.
  require('./lib/season_watch').startSeasonWatch();
  require('./lib/pattern_watch').startPatternWatch();
})();
