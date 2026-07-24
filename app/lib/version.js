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
 *   4. the .git plumbing files directly — HEAD → refs → packed-refs — for a
 *      re-clone deploy that has a .git dir but NO git binary on PATH
 *   5. 'unknown' — honest when nothing is available, never a fake value
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

// Resolve the HEAD commit by reading .git plumbing directly — no git binary
// needed. Handles the normal `.git` dir, a `.git` *file* (worktree/submodule
// pointer: "gitdir: <path>"), a symbolic HEAD (ref: refs/heads/<branch>) via a
// loose ref file, and the packed-refs fallback. Returns a full 40-char SHA or
// null. Reads only ref plumbing — never object contents — so it stays cheap and
// carries no secrets (§F-15).
function readGitHead(root) {
  try {
    let gitDir = path.join(root || REPO_ROOT, '.git');
    const st = fs.statSync(gitDir);
    if (st.isFile()) {
      const m = fs.readFileSync(gitDir, 'utf8').match(/^gitdir:\s*(.+)\s*$/m);
      if (!m) return null;
      gitDir = path.resolve(root || REPO_ROOT, m[1].trim());
    }
    const head = fs.readFileSync(path.join(gitDir, 'HEAD'), 'utf8').trim();
    const ref = head.match(/^ref:\s*(.+)$/);
    if (!ref) {
      // Detached HEAD — the file already holds the raw SHA.
      return /^[0-9a-f]{40}$/i.test(head) ? head : null;
    }
    const refName = ref[1].trim();
    // Prefer a loose ref file.
    try {
      const loose = fs.readFileSync(path.join(gitDir, refName), 'utf8').trim();
      if (/^[0-9a-f]{40}$/i.test(loose)) return loose;
    } catch (e) { /* not loose — fall through to packed-refs */ }
    // Packed-refs fallback: lines are "<sha> <refname>".
    try {
      const packed = fs.readFileSync(path.join(gitDir, 'packed-refs'), 'utf8');
      for (const line of packed.split('\n')) {
        if (!line || line[0] === '#' || line[0] === '^') continue;
        const sp = line.indexOf(' ');
        if (sp > 0 && line.slice(sp + 1).trim() === refName) {
          const sha = line.slice(0, sp).trim();
          if (/^[0-9a-f]{40}$/i.test(sha)) return sha;
        }
      }
    } catch (e) { /* no packed-refs either — give up honestly */ }
    return null;
  } catch (e) {
    return null; // no .git at all — fall through to 'unknown'
  }
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

  // No git binary but a real .git checkout (the re-clone deploy): read the
  // ref plumbing directly rather than surrendering to 'unknown'.
  if (!sha) sha = readGitHead();

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

module.exports = { buildInfo, readGitHead };
