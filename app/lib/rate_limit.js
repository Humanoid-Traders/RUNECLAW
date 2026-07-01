/**
 * In-process sliding-window rate limiter (per key).
 *
 * Extracted from the proven per-IP limiter in app/routes/market.js (RC-AUD-028a)
 * so the money endpoints (credential submit, control changes, emergency stop) can
 * bound abuse per user. Single-process; front with a shared store (Redis) for a
 * multi-replica deployment.
 *
 *   router.post('/', rateLimit({ windowMs: 60000, max: 10, key: userKey }), handler)
 *
 * `key(req)` derives the bucket (defaults to client IP). Use userKey to bucket by
 * authenticated user (place AFTER the auth middleware).
 */

function ipKey(req) {
  return req.ip || (req.socket && req.socket.remoteAddress) || 'unknown';
}

function userKey(req) {
  return (req.user && req.user.user_id != null) ? `u:${req.user.user_id}` : ipKey(req);
}

function rateLimit({ windowMs = 60000, max = 20, key = ipKey, message = 'Too many requests, slow down.' } = {}) {
  const hits = new Map(); // key -> number[] (timestamps in window)

  const prune = () => {
    const cutoff = Date.now() - windowMs;
    for (const [k, arr] of hits) {
      const recent = arr.filter(ts => ts > cutoff);
      if (recent.length === 0) hits.delete(k);
      else hits.set(k, recent);
    }
    if (hits.size > 10000) {
      const keys = [...hits.keys()];
      for (let i = 0; i < keys.length - 5000; i++) hits.delete(keys[i]);
    }
  };
  const timer = setInterval(prune, windowMs);
  if (timer.unref) timer.unref();

  return function (req, res, next) {
    const k = key(req);
    const now = Date.now();
    const cutoff = now - windowMs;
    const arr = (hits.get(k) || []).filter(ts => ts > cutoff);
    if (arr.length >= max) {
      hits.set(k, arr);
      res.setHeader('Retry-After', Math.ceil(windowMs / 1000));
      return res.status(429).json({ error: message });
    }
    arr.push(now);
    hits.set(k, arr);
    next();
  };
}

module.exports = { rateLimit, ipKey, userKey };
