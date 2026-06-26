const http = require('http');
const https = require('https');

// RC-AUD-028(b): this is a legacy/optional blind proxy to a HARDCODED upstream
// host that forwards all client headers. It is NOT wired into the Express app
// (server.js) and is not intended for production. Refuse to run unless the
// operator explicitly opts in via ENABLE_LEGACY_PROXY=true.
if (process.env.ENABLE_LEGACY_PROXY !== 'true') {
  console.error(
    'proxy.js is a legacy/optional component and is disabled by default. ' +
    'Set ENABLE_LEGACY_PROXY=true to run it. Not for production use.'
  );
  process.exit(1);
}

const REMOTE = 'y9z5438h.mule.page';

const server = http.createServer((req, res) => {
  const options = {
    hostname: REMOTE,
    port: 443,
    path: req.url,
    method: req.method,
    headers: { ...req.headers, host: REMOTE },
  };

  const proxy = https.request(options, (proxyRes) => {
    // Remove content-encoding to avoid double-decompression issues
    const headers = { ...proxyRes.headers };
    delete headers['content-encoding'];
    // Add restrictive CSP if upstream doesn't provide one.
    // RC-AUD-028(b): 'unsafe-inline' removed from script-src (it negated XSS
    // protection); inline styles are left allowed for the static dashboard.
    if (!headers['content-security-policy']) {
      headers['content-security-policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; connect-src 'self' https://api.bitget.com; img-src 'self' data:; frame-ancestors 'none'";
    }
    res.writeHead(proxyRes.statusCode, headers);
    proxyRes.pipe(res);
  });

  proxy.on('error', (err) => {
    res.writeHead(502);
    res.end('Proxy error: ' + err.message);
  });

  req.pipe(proxy);
});

server.listen(3000, '0.0.0.0', () => {
  console.log('Proxy to ' + REMOTE + ' running on port 3000');
});
