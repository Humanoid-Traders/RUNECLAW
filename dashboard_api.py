"""
RUNECLAW — Combined Website + Dashboard API Server.
Serves the landing page from website/ at root, dashboard from
dashboard_static/ at /dashboard, and the snapshot API on /api/*.
"""
import hmac
import json
import os
import mimetypes
from http.server import HTTPServer, ThreadingHTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "data", "dashboard_snapshot.json")
FEED_FILE = os.path.join(BASE_DIR, "data", "dashboard_feed.json")
WEBSITE_DIR = os.path.join(BASE_DIR, "website")
DASHBOARD_DIR = os.path.join(BASE_DIR, "dashboard_static")
API_KEY = os.environ.get("DASHBOARD_API_KEY", "")
CORS_ORIGIN = os.environ.get("DASHBOARD_CORS_ORIGIN", "")
ENVIRONMENT = os.environ.get("ENVIRONMENT", "production")
# Additional allowed origins (always permitted alongside CORS_ORIGIN)
_EXTRA_ORIGINS = {"https://3v9vq3w2.mule.page"}
if ENVIRONMENT == "development":
    _EXTRA_ORIGINS.add("http://localhost:9090")

def load_json(path, fallback):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return fallback

def save_json(path, data):
    try:
        with open(path, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


class Handler(BaseHTTPRequestHandler):
    def _cors_headers(self):
        req_origin = self.headers.get("Origin", "")
        allowed = CORS_ORIGIN if CORS_ORIGIN else None
        if req_origin and (req_origin == allowed or req_origin in _EXTRA_ORIGINS):
            self.send_header("Access-Control-Allow-Origin", req_origin)
        elif allowed:
            self.send_header("Access-Control-Allow-Origin", allowed)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Key")

    def _json_response(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def _serve_static(self, base_dir, rel_path):
        """Serve a static file from base_dir with path traversal protection."""
        filepath = os.path.join(base_dir, rel_path.lstrip("/"))
        filepath = os.path.realpath(filepath)
        if not filepath.startswith(os.path.realpath(base_dir)):
            self.send_response(403)
            self.end_headers()
            return
        if os.path.isfile(filepath):
            mime, _ = mimetypes.guess_type(filepath)
            with open(filepath, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", mime or "application/octet-stream")
            self.send_header("Content-Length", len(content))
            # Cache images/videos for 1 hour, HTML for no-cache
            if mime and (mime.startswith("image/") or mime.startswith("video/")):
                self.send_header("Cache-Control", "public, max-age=3600")
            else:
                self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(content)
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")

    def do_GET(self):
        path = self.path.split("?")[0]

        # API routes
        if path == "/api/snapshot":
            self._json_response(load_json(DATA_FILE, {"traders":[], "total_traders":0}))
            return
        if path == "/api/feed":
            self._json_response(load_json(FEED_FILE, []))
            return
        if path == "/api/health":
            snap = load_json(DATA_FILE, {})
            self._json_response({"status":"ok","last_update":snap.get("received_at",""),"traders":snap.get("total_traders",0)})
            return

        # Dashboard routes — /dashboard or /dashboard/*
        if path == "/dashboard" or path == "/dashboard/":
            self._serve_static(DASHBOARD_DIR, "index.html")
            return
        if path.startswith("/dashboard/"):
            rel = path[len("/dashboard/"):]
            self._serve_static(DASHBOARD_DIR, rel)
            return

        # Website landing page — everything else
        if path == "/" or path == "":
            path = "/index.html"
        self._serve_static(WEBSITE_DIR, path)

    def do_POST(self):
        if self.path == "/api/snapshot":
            if not API_KEY:
                self._json_response({"error": "DASHBOARD_API_KEY not configured"}, 403)
                return
            key = self.headers.get("X-API-Key", "")
            if not key or not hmac.compare_digest(key, API_KEY):
                self._json_response({"error":"bad key"}, 403)
                return
            length = int(self.headers.get("Content-Length", 0))
            if length > 10_000_000:  # 10MB max
                self._json_response({"error": "payload too large"}, 413)
                return
            try:
                body = json.loads(self.rfile.read(length))
            except (json.JSONDecodeError, ValueError):
                self._json_response({"error": "invalid JSON"}, 400)
                return
            body["received_at"] = datetime.now(timezone.utc).isoformat()
            save_json(DATA_FILE, body)

            # Feed — add new entry from snapshot, then truncate
            feed = load_json(FEED_FILE, [])
            feed_entry = {
                "timestamp": body.get("received_at", datetime.now(timezone.utc).isoformat()),
                "traders": len(body.get("traders", [])),
                "total_traders": body.get("total_traders", 0),
            }
            feed.insert(0, feed_entry)
            save_json(FEED_FILE, feed[:100])

            self._json_response({"ok":True,"traders":len(body.get("traders",[]))})
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        pass

    def do_HEAD(self):
        """Handle HEAD requests — send headers only, no body."""
        path = self.path.split("?")[0]

        # API routes
        if path in ("/api/snapshot", "/api/feed", "/api/health"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self._cors_headers()
            self.end_headers()
            return

        # Dashboard routes
        if path == "/dashboard" or path == "/dashboard/":
            filepath = os.path.join(DASHBOARD_DIR, "index.html")
        elif path.startswith("/dashboard/"):
            rel = path[len("/dashboard/"):]
            filepath = os.path.join(DASHBOARD_DIR, rel.lstrip("/"))
        else:
            # Website landing page
            if path == "/" or path == "":
                path = "/index.html"
            filepath = os.path.join(WEBSITE_DIR, path.lstrip("/"))

        filepath = os.path.realpath(filepath)
        base_check = os.path.realpath(WEBSITE_DIR) if not filepath.startswith(os.path.realpath(DASHBOARD_DIR)) else os.path.realpath(DASHBOARD_DIR)
        if not filepath.startswith(base_check):
            self.send_response(403)
            self.end_headers()
            return
        if os.path.isfile(filepath):
            mime, _ = mimetypes.guess_type(filepath)
            size = os.path.getsize(filepath)
            self.send_response(200)
            self.send_header("Content-Type", mime or "application/octet-stream")
            self.send_header("Content-Length", size)
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()


if __name__ == "__main__":
    if not API_KEY:
        print("ERROR: DASHBOARD_API_KEY environment variable is not set. Refusing to start.")
        print("Set it with: export DASHBOARD_API_KEY='your-secret-key'")
        raise SystemExit(1)
    port = int(os.environ.get("DASHBOARD_PORT", 9090))
    os.makedirs(os.path.join(BASE_DIR, "data"), exist_ok=True)
    os.makedirs(WEBSITE_DIR, exist_ok=True)
    os.makedirs(DASHBOARD_DIR, exist_ok=True)
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"RUNECLAW on :{port}  |  Landing: /  |  Dashboard: /dashboard")
    server.serve_forever()
