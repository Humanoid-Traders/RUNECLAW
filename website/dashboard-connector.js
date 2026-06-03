/**
 * RUNECLAW v2 — Dashboard API Connector
 * ======================================
 * Drop this <script> block into website/dashboard-pro.html
 * (or import as dashboard-connector.js) BEFORE your existing
 * dashboard script block.
 *
 * It overrides three functions the dashboard already calls:
 *   runFullScan()   → POST /scan
 *   renderPortfolio → GET  /portfolio  (auto-poll every 10s)
 *   confirmTrade()  → POST /confirm
 *   closePos()      → POST /portfolio/close/:symbol
 *
 * Set the API URL via:
 *   window.RUNECLAW_API = 'http://localhost:8000'   (local dev)
 *   window.RUNECLAW_API = 'https://your-vps.com'   (production)
 *
 * Or add <meta name="runeclaw-api" content="https://..."> to <head>.
 */

(function () {
  "use strict";

  // ── Config ─────────────────────────────────────────────────────────────────
  const API =
    window.RUNECLAW_API ||
    document.querySelector('meta[name="runeclaw-api"]')?.content ||
    "http://localhost:8000";

  const POLL_INTERVAL_MS  = 10_000;   // portfolio auto-refresh
  const SCAN_CACHE_TTL_MS = 120_000;  // 2 min — mirrors server-side TTL

  let _pollTimer = null;
  let _lastScanTs = 0;

  // ── Utility ────────────────────────────────────────────────────────────────
  async function api(method, path, body) {
    const opts = {
      method,
      headers: { "Content-Type": "application/json" },
    };
    if (body) opts.body = JSON.stringify(body);
    const res = await fetch(`${API}${path}`, opts);
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
  }

  function setStatus(msg, color = "var(--text2)") {
    const el = document.getElementById("scan-status");
    if (el) { el.textContent = msg; el.style.color = color; }
  }

  function showSpinner(on) {
    const sp = document.getElementById("scan-spinner");
    if (sp) sp.style.display = on ? "inline-block" : "none";
  }

  // ── /health ping on load ───────────────────────────────────────────────────
  (async () => {
    try {
      const h = await api("GET", "/health");
      console.log(`⚔️  RUNECLAW API online — ${h.symbols} symbols ready`);
      setStatus(`API connected · ${h.symbols} symbols · v${h.version}`, "var(--green)");
    } catch (e) {
      console.warn("RUNECLAW API unreachable — running in mock mode", e);
      setStatus("⚠️ API offline — mock mode active", "var(--yellow)");
    }
  })();

  // ── Override: runFullScan() ────────────────────────────────────────────────
  window.runFullScan = async function (forceRefresh = false) {
    // Honour client-side TTL even if server cache is fresh
    const age = Date.now() - _lastScanTs;
    if (!forceRefresh && age < SCAN_CACHE_TTL_MS && Object.keys(window.scanResults || {}).length) {
      setStatus("Using cached scan · " + Math.round(age / 1000) + "s old");
      return;
    }

    showSpinner(true);
    setStatus("Scanning 67 symbols across 4H / 1H / 5M …");

    try {
      const data = await api("POST", "/scan", {
        mode: "full",
        force: forceRefresh,
      });

      // Merge into the global scanResults object the dashboard reads
      window.scanResults = {};
      (data.results || []).forEach((r) => {
        window.scanResults[r.sym] = r;
      });

      _lastScanTs = Date.now();

      const longs  = data.results.filter((r) => r.dir === "long").length;
      const shorts = data.results.filter((r) => r.dir === "short").length;
      const neutral = data.results.length - longs - shorts;

      setStatus(
        `✅ ${data.cached ? "Cached" : "Live"} scan · ${longs} long · ${shorts} short · ${neutral} flat`,
        "var(--green)"
      );

      // Re-render the symbol grid + signals + pattern detections
      if (typeof window.renderSymGrid === "function")   window.renderSymGrid();
      if (typeof window.renderSignals === "function")   window.renderSignals();
      if (typeof window.renderPatternDetections === "function")
        window.renderPatternDetections();
    } catch (err) {
      setStatus(`⚠️ Scan error: ${err.message}`, "var(--red)");
      console.error("runFullScan error", err);
    } finally {
      showSpinner(false);
    }
  };

  // ── Override: runScan(mode) ────────────────────────────────────────────────
  window.runScan = async function (mode) {
    showSpinner(true);
    setStatus(`Running ${mode} scan …`);
    try {
      const data = await api("POST", "/scan", { mode, force: false });
      window.scanResults = {};
      (data.results || []).forEach((r) => { window.scanResults[r.sym] = r; });
      setStatus(`✅ ${mode} scan · ${data.results.length} symbols`, "var(--green)");
      if (typeof window.renderSymGrid === "function") window.renderSymGrid();
      if (typeof window.renderSignals === "function") window.renderSignals();
    } catch (err) {
      setStatus(`⚠️ ${err.message}`, "var(--red)");
    } finally {
      showSpinner(false);
    }
  };

  // ── Override: confirmTrade(sym, dir) ──────────────────────────────────────
  window.confirmTrade = async function (sym, dir) {
    const d = (window.scanResults || {})[sym];
    if (!d) { console.warn("No scan data for", sym); return; }

    const entry = d.price || 1;
    const sl    = dir === "long" ? entry * 0.985 : entry * 1.015;
    const tp    = dir === "long" ? entry * 1.04  : entry * 0.96;

    try {
      const res = await api("POST", "/confirm", {
        symbol:      sym,
        direction:   dir,
        entry,
        stop_loss:   parseFloat(sl.toFixed(6)),
        take_profit: parseFloat(tp.toFixed(6)),
        confidence:  d.conf || 0.65,
      });

      if (res.accepted) {
        console.log(`✅ Trade confirmed: ${sym} ${dir.toUpperCase()} @ ${entry}`);
        // Reflect immediately in local positions array
        if (!window.positions) window.positions = [];
        const existing = window.positions.find((p) => p.sym === sym);
        if (!existing) {
          window.positions.push({
            sym, dir, entry: entry.toFixed(6),
            sl: Math.abs((sl - entry) / entry * 100).toFixed(2),
            tp: Math.abs((tp - entry) / entry * 100).toFixed(2),
            conf: Math.round((d.conf || 0.65) * 100),
            status: "live",
            positionId: res.position_id,
          });
        }
      } else {
        alert(`❌ Risk gate rejected: ${res.reason}`);
      }

      await _refreshPortfolio();
    } catch (err) {
      console.error("confirmTrade error", err);
      alert(`Error: ${err.message}`);
    }
  };

  // ── Override: closePos(sym) ───────────────────────────────────────────────
  window.closePos = async function (sym) {
    try {
      await api("POST", `/portfolio/close/${sym}`);
      // Remove from local positions
      if (window.positions) {
        const idx = window.positions.findIndex((p) => p.sym === sym);
        if (idx >= 0) window.positions.splice(idx, 1);
      }
      await _refreshPortfolio();
    } catch (err) {
      console.error("closePos error", err);
    }
  };

  // ── Override: analyzeWithAI(sym) ──────────────────────────────────────────
  // Fetches deep analysis from /analyze and injects into AI chat
  window.analyzeWithAI = async function (sym) {
    if (typeof window.switchTab === "function") window.switchTab("ai");
    const input = document.getElementById("chat-input");
    if (input) {
      input.value = `Deep analyze ${sym} — pulling live data…`;
    }
    try {
      const res = await api("POST", "/analyze", { symbol: sym, timeframe: "4h" });
      const msg = [
        `📊 Live analysis for **${sym}** (4H):`,
        `RSI ${res.rsi ?? "—"} · ADX ${res.adx ?? "—"} · Vol ${res.vol_ratio ?? "—"}x`,
        `4H ${res.tf4h ?? "—"} / 1H ${res.tf1h ?? "—"} / 5M ${res.tf5m ?? "—"}`,
        `Chart: ${(res.chart_patterns || []).join(", ") || "none detected"}`,
        `Candle: ${(res.candle_patterns || []).join(", ") || "none detected"}`,
        `Structure: ${(res.structure || []).join(", ") || "none detected"}`,
        `Funding: ${res.funding_rate ?? 0} · OI Δ: ${res.oi_change ?? 0}%`,
        `\nScore: ${res.score ?? "—"} → ${(res.dir ?? "flat").toUpperCase()}`,
        `\nShould I trade this? What's the optimal entry / SL / TP given current structure?`,
      ].join("\n");
      if (input) input.value = msg;
      if (typeof window.sendChat === "function") window.sendChat();
    } catch (err) {
      if (input) input.value = `Analyze ${sym} — what do you see in the 4H chart structure?`;
      if (typeof window.sendChat === "function") window.sendChat();
    }
  };

  // ── Portfolio refresh ─────────────────────────────────────────────────────
  async function _refreshPortfolio() {
    try {
      const pf = await api("GET", "/portfolio");

      // Sync global state the dashboard reads
      window.equity   = pf.equity;
      window.dailyPnl = pf.daily_pnl;

      // Replace local positions array with server truth
      if (pf.open_positions) {
        window.positions = pf.open_positions.map((p) => ({
          sym:    p.symbol || p.sym,
          dir:    p.direction || p.dir,
          entry:  String(p.entry || 0),
          sl:     String(p.sl_pct || p.sl || 0),
          tp:     String(p.tp_pct || p.tp || 0),
          conf:   Math.round((p.confidence || 0.65) * 100),
          status: p.status || "live",
          pnl:    p.unrealised_pnl || 0,
        }));
      }

      // Trade history
      if (pf.trade_history) window.tradeHistory = pf.trade_history;

      // Update header stats
      if (typeof window.updateHeader === "function") window.updateHeader();
      if (typeof window.renderPortfolio === "function") window.renderPortfolio();
      if (typeof window.renderSignals === "function") window.renderSignals();
    } catch (err) {
      console.warn("Portfolio refresh failed:", err.message);
    }
  }

  // ── Risk status refresh ───────────────────────────────────────────────────
  async function _refreshRisk() {
    try {
      const rs = await api("GET", "/risk/status");
      const cbEl = document.getElementById("h-cb");
      if (cbEl) {
        if (rs.circuit_breaker) {
          cbEl.textContent = "TRIPPED"; cbEl.style.color = "var(--red)";
        } else if (Math.abs(rs.daily_loss) > rs.daily_loss_limit * 0.7) {
          cbEl.textContent = "WARNING"; cbEl.style.color = "var(--yellow)";
        } else {
          cbEl.textContent = "ARMED"; cbEl.style.color = "var(--green)";
        }
      }
      const regEl = document.getElementById("h-regime");
      if (regEl) {
        const score = rs.regime_score || 0;
        regEl.textContent = score > 1 ? "BULL" : score < -1 ? "BEAR" : "NEUTRAL";
      }
    } catch (_) {}
  }

  // ── Black swan status ─────────────────────────────────────────────────────
  async function _refreshBlackSwan() {
    try {
      const bs = await api("GET", "/blackswan");
      if (bs.anomaly && bs.severity >= 0.8) {
        setStatus(`🚨 BLACK SWAN DETECTED (severity ${bs.severity}) — trading halted`, "var(--red)");
      }
    } catch (_) {}
  }

  // ── Auto-poll ─────────────────────────────────────────────────────────────
  function startPolling() {
    if (_pollTimer) clearInterval(_pollTimer);
    _pollTimer = setInterval(async () => {
      await Promise.all([_refreshPortfolio(), _refreshRisk(), _refreshBlackSwan()]);
    }, POLL_INTERVAL_MS);
  }

  // Boot polling after DOM ready
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", startPolling);
  } else {
    startPolling();
  }

  console.log(`⚔️  RUNECLAW Dashboard Connector loaded · API: ${API}`);
})();
