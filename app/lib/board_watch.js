/**
 * Follow-this-agent: verifiable-leaderboard milestone watch (community C4).
 *
 * Periodically reads the PUBLIC ranked board (the same re-verified rows the
 * /leaderboard page serves) and pushes a single digest notification when
 * ranks actually move — an agent climbing, entering, or leaving the board.
 * Payloads carry handles and ranks only: the board is size-agnostic by
 * construction, so a milestone push can never leak a dollar figure.
 *
 * Delivery goes through push.notifyTopic('board', …): OPT-IN per user via
 * profile prefs (push_board), so the new category reaches nobody who didn't
 * ask for it. The first sweep after boot only records a baseline — restarts
 * never replay old moves as fresh news. Everything is best-effort; a gateway
 * or push hiccup skips the sweep, never throws.
 */

const { getGateway, isConfigured } = require('./gateway');

let prev = null;                 // handle -> rank (null until first sweep)

/** Reset the baseline (tests). */
function resetBoardWatch() { prev = null; }

/** Compose the digest line for a diff, or null when nothing moved. */
function diffDigest(prevMap, rows) {
  const now = new Map(rows.map(r => [String(r.handle), Number(r.rank)]));
  const bits = [];
  for (const [handle, rank] of now) {
    const old = prevMap.get(handle);
    if (old === undefined) bits.push(`${handle} entered at #${rank}`);
    else if (rank < old) bits.push(`${handle} climbed #${old}→#${rank}`);
    else if (rank > old) bits.push(`${handle} slipped #${old}→#${rank}`);
  }
  for (const handle of prevMap.keys()) {
    if (!now.has(handle)) bits.push(`${handle} left the board`);
  }
  return bits.length ? bits.slice(0, 6).join(' · ') : null;
}

/**
 * One sweep: fetch the board, diff against the last sweep, push a digest to
 * board-topic subscribers when something moved. Injectable fetch/notify for
 * tests. Returns true when a push was attempted.
 */
async function sweepBoard(fetchBoard, notify) {
  try {
    let rows;
    if (fetchBoard) {
      rows = await fetchBoard();
    } else {
      if (!isConfigured()) return false;
      const r = await getGateway('/public/leaderboard', 15000);
      if (!r || r.status !== 200) return false;
      rows = (r.data && r.data.rows) || [];
    }
    if (!Array.isArray(rows)) return false;
    const now = new Map(rows.map(x => [String(x.handle), Number(x.rank)]));
    if (prev === null) { prev = now; return false; }   // baseline only
    const digest = diffDigest(prev, rows);
    prev = now;
    if (!digest) return false;
    let send = notify;
    if (!send) {
      const { notifyTopic } = require('./push');
      send = (p) => notifyTopic('board', p);
    }
    await send({
      title: '🏆 Verifiable board moved',
      body: digest,
      url: '/leaderboard',
    });
    return true;
  } catch (e) {
    return false;
  }
}

let timer = null;
function startBoardWatch(intervalMs = 3_600_000) {
  if (timer) return;
  sweepBoard().catch(() => {});
  timer = setInterval(() => { sweepBoard().catch(() => {}); }, intervalMs);
  if (timer.unref) timer.unref();
}

module.exports = { sweepBoard, startBoardWatch, resetBoardWatch, diffDigest };
