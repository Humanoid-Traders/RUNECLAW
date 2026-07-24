'use strict';
/**
 * Build/version stamp — /api/version, the `build` block on the status payload,
 * and the "Running build" line on the /status page. This exists so a stale
 * deploy is a five-second check instead of route-probing the site. §F-15: the
 * stamp is public metadata only (short SHA, commit + boot time) — no secrets.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const os = require('node:os');

const { buildInfo, readGitHead } = require('../lib/version');

// Build a throwaway .git skeleton under a temp dir and return its root.
function fakeRepo(build) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'rc-git-'));
  const gitDir = path.join(root, '.git');
  fs.mkdirSync(gitDir, { recursive: true });
  build(root, gitDir);
  return root;
}

test('readGitHead() resolves a symbolic HEAD via a loose ref', () => {
  const sha = 'a'.repeat(40);
  const root = fakeRepo((_r, g) => {
    fs.writeFileSync(path.join(g, 'HEAD'), 'ref: refs/heads/main\n');
    fs.mkdirSync(path.join(g, 'refs', 'heads'), { recursive: true });
    fs.writeFileSync(path.join(g, 'refs', 'heads', 'main'), sha + '\n');
  });
  assert.equal(readGitHead(root), sha);
});

test('readGitHead() falls back to packed-refs when no loose ref exists', () => {
  const sha = 'b'.repeat(40);
  const root = fakeRepo((_r, g) => {
    fs.writeFileSync(path.join(g, 'HEAD'), 'ref: refs/heads/main\n');
    fs.writeFileSync(path.join(g, 'packed-refs'),
      '# pack-refs with: peeled fully-peeled sorted\n' +
      sha + ' refs/heads/main\n' +
      'c'.repeat(40) + ' refs/remotes/origin/main\n');
  });
  assert.equal(readGitHead(root), sha);
});

test('readGitHead() reads a detached HEAD directly', () => {
  const sha = 'd'.repeat(40);
  const root = fakeRepo((_r, g) => fs.writeFileSync(path.join(g, 'HEAD'), sha + '\n'));
  assert.equal(readGitHead(root), sha);
});

test('readGitHead() follows a .git *file* gitdir pointer', () => {
  const sha = 'e'.repeat(40);
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'rc-gitf-'));
  const real = path.join(root, 'realgit');
  fs.mkdirSync(path.join(real, 'refs', 'heads'), { recursive: true });
  fs.writeFileSync(path.join(real, 'HEAD'), 'ref: refs/heads/main\n');
  fs.writeFileSync(path.join(real, 'refs', 'heads', 'main'), sha + '\n');
  fs.writeFileSync(path.join(root, '.git'), 'gitdir: ' + real + '\n');
  assert.equal(readGitHead(root), sha);
});

test('readGitHead() returns null when there is no .git at all', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'rc-nogit-'));
  assert.equal(readGitHead(root), null);
});

test('buildInfo() reports a resolved sha, ISO boot time and numeric uptime', () => {
  const b = buildInfo();
  assert.equal(typeof b.sha, 'string');
  assert.ok(b.sha.length > 0, 'sha is never empty — "unknown" at worst, never blank');
  assert.ok(/^\d{4}-\d{2}-\d{2}T/.test(b.started_at), 'started_at is an ISO timestamp');
  assert.equal(typeof b.uptime_s, 'number');
  assert.ok(b.uptime_s >= 0);
  // committed_at is either an ISO string or null (honest when git is absent).
  assert.ok(b.committed_at === null || typeof b.committed_at === 'string');
});

test('the stamp carries no secrets (§F-15)', () => {
  const raw = JSON.stringify(buildInfo()).toLowerCase();
  for (const needle of ['secret', 'token', 'password', 'private', 'api_key', 'llm_api']) {
    assert.ok(!raw.includes(needle), `build stamp must not contain "${needle}"`);
  }
});

test('the status payload embeds the build block', async () => {
  const status = require('../lib/status');
  status.setProbes({
    getScan: async () => null,
    getReports: async () => null,
    pingGateway: async () => ({ state: 'not_configured' }),
    latestLetter: async () => null,
    dbMode: () => 'memory',
    uptimeS: () => 1,
  });
  const s = await status.buildStatus(Date.now());
  assert.ok(s.build, 'status carries a build block');
  assert.equal(typeof s.build.sha, 'string');
  assert.ok(s.build.sha.length > 0);
});

test('/api/version is mounted and returns the build stamp', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  assert.match(src, /app\.get\('\/api\/version'/);
  assert.match(src, /require\('\.\/lib\/version'\)\.buildInfo\(\)/);
});

test('the /status page renders the running build', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'status.html'), 'utf8');
  assert.match(html, /Running build/);
  assert.match(html, /d\.build/);           // reads the build block from the payload
  assert.match(html, /b\.sha/);             // shows the commit sha
});
