const http = require('http');
const https = require('https');

const REMOTE = 'deryrgeb.mule.page';

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
    delete headers['content-security-policy'];
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
