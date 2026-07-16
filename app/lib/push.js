/**
 * Web Push sender (VAPID).
 *
 * Enabled only when the operator sets VAPID_PUBLIC_KEY + VAPID_PRIVATE_KEY
 * (generate once: node -e "console.log(require('web-push').generateVAPIDKeys())").
 * Without keys everything here is a clean no-op — the UI shows push as
 * unavailable and nothing breaks.
 *
 * Delivery is strictly best-effort and fire-and-forget: a push failure must
 * never affect the caller (bot sync ingest). Subscriptions that the push
 * service reports as gone (404/410) are pruned automatically.
 */

const { pool } = require('../db');

let webpush = null;
try { webpush = require('web-push'); } catch (e) { /* optional dep */ }

const PUBLIC_KEY = process.env.VAPID_PUBLIC_KEY || '';
const PRIVATE_KEY = process.env.VAPID_PRIVATE_KEY || '';
const SUBJECT = process.env.VAPID_SUBJECT || 'mailto:ops@runeclaw.local';

let configured = false;
if (webpush && PUBLIC_KEY && PRIVATE_KEY) {
  try {
    webpush.setVapidDetails(SUBJECT, PUBLIC_KEY, PRIVATE_KEY);
    configured = true;
  } catch (e) {
    console.error('Web push disabled (bad VAPID keys):', e.message);
  }
}

// Injectable transport so tests can capture sends without hitting a real
// push service. Defaults to the real web-push sender.
let sender = (subscription, payload) =>
  webpush.sendNotification(subscription, payload, { TTL: 3600 });
function setSender(fn) { sender = fn; }

function isConfigured() { return configured; }
function publicKey() { return configured ? PUBLIC_KEY : ''; }

async function prune(endpoint) {
  try {
    await pool.execute('DELETE FROM push_subscriptions WHERE endpoint = ?', [endpoint]);
  } catch (e) { /* best-effort */ }
}

/**
 * Send { title, body, url? } to every push subscription of every user in
 * userIds (or ALL subscribed users when userIds is null). Returns sends
 * attempted. Never throws.
 */
async function notifySubscribers(payload, userIds = null) {
  if (!configured) return 0;
  let rows = [];
  try {
    if (Array.isArray(userIds)) {
      if (!userIds.length) return 0;
      const all = [];
      for (const uid of userIds.slice(0, 500)) {
        const [r] = await pool.execute(
          'SELECT endpoint, keys_json FROM push_subscriptions WHERE user_id = ?', [uid]);
        all.push(...r);
      }
      rows = all;
    } else {
      const [r] = await pool.execute(
        'SELECT endpoint, keys_json FROM push_subscriptions ORDER BY id DESC LIMIT 2000');
      rows = r;
    }
  } catch (e) {
    return 0;
  }
  const body = JSON.stringify(payload || {});
  let sent = 0;
  await Promise.all(rows.map(async (row) => {
    let keys = {};
    try { keys = JSON.parse(row.keys_json || '{}'); } catch (e) { /* prune below */ }
    try {
      await sender({ endpoint: row.endpoint, keys }, body);
      sent++;
    } catch (err) {
      const code = err && (err.statusCode || err.status);
      if (code === 404 || code === 410) await prune(row.endpoint);
    }
  }));
  return sent;
}

module.exports = { isConfigured, publicKey, notifySubscribers, setSender };
