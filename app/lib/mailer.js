'use strict';

// Provider-agnostic transactional email — dependency-free.
//
// Works with ANY provider that speaks SMTP (Gmail, SendGrid, Postmark, Mailgun,
// Amazon SES, a self-hosted Postfix …). No npm dependency: it drives SMTP over
// Node's built-in net/tls. When SMTP is not configured it is a safe no-op that
// logs what it *would* have sent — so password-reset / email-verification flows
// degrade gracefully on a fresh deploy instead of crashing.
//
// Configure via env:
//   SMTP_HOST      smtp.example.com            (required to actually send)
//   SMTP_PORT      587 (default) or 465
//   SMTP_SECURE    "true" → implicit TLS (port 465). Otherwise STARTTLS on 587.
//   SMTP_USER      login username              (optional; AUTH LOGIN if set)
//   SMTP_PASS      login password / API key    (optional)
//   MAIL_FROM      "RUNECLAW <no-reply@…>"     (required to actually send)
//   APP_BASE_URL   https://app.example.com     (used to build links in emails)
//
// Secrets are read from the environment only; nothing is logged except the
// recipient, subject, and — when unconfigured — the plaintext body preview.

const net = require('net');
const tls = require('tls');

function _cfg() {
  const host = process.env.SMTP_HOST || '';
  const from = process.env.MAIL_FROM || process.env.SMTP_FROM || '';
  const secure = String(process.env.SMTP_SECURE || '').toLowerCase() === 'true';
  const port = parseInt(process.env.SMTP_PORT || (secure ? '465' : '587'), 10);
  return {
    host,
    from,
    secure,
    port: Number.isFinite(port) ? port : 587,
    user: process.env.SMTP_USER || '',
    pass: process.env.SMTP_PASS || '',
    // Allow disabling cert verification only via an explicit opt-out (some
    // internal relays use self-signed certs). Defaults to verifying.
    rejectUnauthorized:
      String(process.env.SMTP_TLS_REJECT_UNAUTHORIZED || 'true').toLowerCase() !== 'false',
  };
}

function isConfigured() {
  const c = _cfg();
  return Boolean(c.host && c.from);
}

function baseUrl() {
  return (process.env.APP_BASE_URL || '').replace(/\/+$/, '');
}

// --- RFC 5322 message assembly (pure; exported for tests) ---

function _foldFrom(from) {
  // Accept "Name <addr>" or a bare address.
  return from;
}

function _addrOnly(from) {
  const m = /<([^>]+)>/.exec(from);
  return (m ? m[1] : from).trim();
}

// Encode a header value that may contain non-ASCII using RFC 2047 (UTF-8, B).
function _encodeHeader(value) {
  // eslint-disable-next-line no-control-regex
  if (/^[\x00-\x7F]*$/.test(value)) return value;
  return `=?UTF-8?B?${Buffer.from(value, 'utf8').toString('base64')}?=`;
}

function buildMessage({ from, to, subject, text, html, date }) {
  const boundary = 'rc_' + Buffer.from(String(subject || '') + '|' + String(to || ''))
    .toString('hex')
    .slice(0, 24);
  const headers = [
    `From: ${_foldFrom(from)}`,
    `To: ${to}`,
    `Subject: ${_encodeHeader(subject || '')}`,
    `Date: ${(date || new Date()).toUTCString()}`,
    'MIME-Version: 1.0',
  ];
  let body;
  if (html) {
    headers.push(`Content-Type: multipart/alternative; boundary="${boundary}"`);
    const textPart = [
      `--${boundary}`,
      'Content-Type: text/plain; charset=UTF-8',
      'Content-Transfer-Encoding: base64',
      '',
      Buffer.from(text || '', 'utf8').toString('base64'),
    ].join('\r\n');
    const htmlPart = [
      `--${boundary}`,
      'Content-Type: text/html; charset=UTF-8',
      'Content-Transfer-Encoding: base64',
      '',
      Buffer.from(html, 'utf8').toString('base64'),
    ].join('\r\n');
    body = `${textPart}\r\n${htmlPart}\r\n--${boundary}--\r\n`;
  } else {
    headers.push('Content-Type: text/plain; charset=UTF-8');
    headers.push('Content-Transfer-Encoding: base64');
    body = Buffer.from(text || '', 'utf8').toString('base64') + '\r\n';
  }
  // Dot-stuffing: any line beginning with '.' must be doubled per RFC 5321.
  const message = `${headers.join('\r\n')}\r\n\r\n${body}`;
  return message.replace(/\r\n\./g, '\r\n..');
}

// --- SMTP conversation ---

function _talk(socket, cfg) {
  // A tiny promise-based SMTP client bound to an established socket.
  let buffer = '';
  let waiter = null;

  socket.setEncoding('utf8');
  socket.on('data', (chunk) => {
    buffer += chunk;
    // A full reply ends with a line "NNN <text>" (space after the code).
    const lines = buffer.split('\r\n').filter((l) => l.length > 0);
    const last = lines[lines.length - 1];
    if (last && /^\d{3} /.test(last) && waiter) {
      const code = parseInt(last.slice(0, 3), 10);
      const text = buffer;
      buffer = '';
      const w = waiter;
      waiter = null;
      w.resolve({ code, text });
    }
  });

  function read() {
    return new Promise((resolve, reject) => {
      waiter = { resolve, reject };
    });
  }

  function send(line) {
    socket.write(line + '\r\n');
    return read();
  }

  return { read, send };
}

function _expect(reply, ...codes) {
  if (!codes.includes(reply.code)) {
    const err = new Error(`SMTP unexpected reply ${reply.code}: ${reply.text.trim()}`);
    err.code = reply.code;
    throw err;
  }
  return reply;
}

async function _deliver(cfg, envelope, message) {
  const connectTimeoutMs = 15000;

  function connectPlain() {
    return new Promise((resolve, reject) => {
      const s = net.createConnection({ host: cfg.host, port: cfg.port });
      s.setTimeout(connectTimeoutMs);
      s.once('timeout', () => { s.destroy(new Error('SMTP connect timeout')); });
      s.once('error', reject);
      s.once('connect', () => { s.removeListener('error', reject); resolve(s); });
    });
  }

  function connectTLS(existing) {
    return new Promise((resolve, reject) => {
      const opts = {
        host: cfg.host,
        servername: cfg.host,
        rejectUnauthorized: cfg.rejectUnauthorized,
      };
      if (existing) opts.socket = existing;
      const s = tls.connect(existing ? opts : { port: cfg.port, ...opts });
      s.setTimeout(connectTimeoutMs);
      s.once('timeout', () => { s.destroy(new Error('SMTP TLS timeout')); });
      s.once('error', reject);
      s.once('secureConnect', () => { s.removeListener('error', reject); resolve(s); });
    });
  }

  let socket = cfg.secure ? await connectTLS() : await connectPlain();
  let smtp = _talk(socket, cfg);
  try {
    _expect(await smtp.read(), 220);
    const ehloHost = 'runeclaw.local';
    let reply = _expect(await smtp.send(`EHLO ${ehloHost}`), 250);

    if (!cfg.secure && /STARTTLS/i.test(reply.text)) {
      _expect(await smtp.send('STARTTLS'), 220);
      socket = await connectTLS(socket);
      smtp = _talk(socket, cfg);
      reply = _expect(await smtp.send(`EHLO ${ehloHost}`), 250);
    }

    if (cfg.user) {
      _expect(await smtp.send('AUTH LOGIN'), 334);
      _expect(await smtp.send(Buffer.from(cfg.user, 'utf8').toString('base64')), 334);
      _expect(await smtp.send(Buffer.from(cfg.pass, 'utf8').toString('base64')), 235);
    }

    _expect(await smtp.send(`MAIL FROM:<${envelope.from}>`), 250);
    _expect(await smtp.send(`RCPT TO:<${envelope.to}>`), 250, 251);
    _expect(await smtp.send('DATA'), 354);
    // Send the message body followed by the terminating "<CRLF>.<CRLF>".
    socket.write(message);
    if (!message.endsWith('\r\n')) socket.write('\r\n');
    _expect(await smtp.send('.'), 250);
    try { await smtp.send('QUIT'); } catch { /* server may drop after QUIT */ }
  } finally {
    socket.end();
    socket.destroy();
  }
}

// Send an email. Resolves { skipped, messageId } — never rejects for a
// not-configured mailer (the caller treats email as best-effort). A real
// delivery failure DOES reject, so callers can log it.
async function sendMail({ to, subject, text, html }) {
  const cfg = _cfg();
  if (!cfg.host || !cfg.from) {
    const preview = String(text || '').replace(/\s+/g, ' ').slice(0, 200);
    console.log(`[mailer] (no-op, SMTP not configured) would send to=${to} subject="${subject}" :: ${preview}`);
    return { skipped: true, reason: 'not_configured' };
  }
  const envelope = { from: _addrOnly(cfg.from), to: String(to).trim() };
  const message = buildMessage({ from: cfg.from, to, subject, text, html });
  await _deliver(cfg, envelope, message);
  console.log(`[mailer] sent to=${to} subject="${subject}"`);
  return { skipped: false };
}

module.exports = {
  isConfigured,
  baseUrl,
  sendMail,
  // exported for tests
  buildMessage,
  _addrOnly,
  _encodeHeader,
};
