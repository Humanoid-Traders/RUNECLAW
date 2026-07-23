'use strict';
/**
 * Build/version stamp — so anyone (the operator, a test, the /status page, or
 * a monitoring probe) can see EXACTLY which commit the running server is
 * serving. A stale deploy then becomes a five-second check instead of
 * route-probing the site. Resolved ONCE at process start and memoised.
 *
 * §F-15: carries NO secrets and reads no credentials — only the short git SHA,
 * the commit time and the process boot time. All of that is public metadata.
 *
 * Resolution order (first hit wins), chosen so the stamp survives every deploy
 * shape we use:
 *   1. BUILD_SHA / SOURCE_COMMIT env (set by a container build ARG)
 *   2. build-info.json written at build time (a .git-less image still stamps)
 *   3. `git` in the checkout (the re-clone deploy path has a real .git)
 *   4. 'unknown' — honest when nothing is available, never a fake value
 */

const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const REPO_ROOT = path.join(__dirname, '..', '..');

function gitOut(args) {
  try {
    const out = execFileSync('git', args, {
      cwd: REPO_ROOT,
      timeout: 1500,
      stdio: ['ignore', 'pipe', 'ignore'],
    });
    return String(out).trim() || null;
  } catch (e) {
    return null; // no git binary / not a checkout — fall through honestly
  }
}

function readBuildFile() {
  // A build step may drop build-info.json next to the app or at the repo root.
  for (const p of [path.join(__dirname, '..', 'build-info.json'),
                   path.join(REPO_ROOT, 'build-info.json')]) {
    try {
      const bi = JSON.parse(fs.readFileSync(p, 'utf8'));
      if (bi && (bi.sha || bi.committed_at)) return bi;
    } catch (e) { /* absent or unreadable — try the next location */ }
  }
  return null;
}

function compute() {
  let sha = (process.env.BUILD_SHA || process.env.SOURCE_COMMIT || '').trim() || null;
  let committedAt = (process.env.BUILD_TIME || '').trim() || null;

  const bi = readBuildFile();
  if (bi) {
    if (bi.sha) sha = String(bi.sha);
    if (bi.committed_at) committedAt = String(bi.committed_at);
  }

  if (!sha) sha = gitOut(['rev-parse', '--short', 'HEAD']);
  if (!committedAt) committedAt = gitOut(['show', '-s', '--format=%cI', 'HEAD']);

  return {
    sha: (sha ? String(sha).slice(0, 12) : 'unknown'),
    committed_at: committedAt || null,
    started_at: new Date().toISOString(),
  };
}

const BUILD = compute();

/** Immutable build facts plus the live uptime at call time. */
function buildInfo() {
  return {
    sha: BUILD.sha,
    committed_at: BUILD.committed_at,
    started_at: BUILD.started_at,
    uptime_s: Math.floor(process.uptime()),
  };
}

module.exports = { buildInfo };
