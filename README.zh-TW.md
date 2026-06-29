```
 ____  _   _ _   _ _____ ____ _        ___        __
|  _ \| | | | \ | | ____/ ___| |      / \ \      / /
| |_) | | | |  \| |  _|| |   | |     / _ \ \ /\ / /
|  _ <| |_| | |\  | |__| |___| |___ / ___ \ V  V /
|_| \_\\___/|_| \_|_____\____|_____/_/   \_\_/\_/
```

<h3 align="center">AI 交易指揮核心 | 以紀律為本。</h3>
<h4 align="center">由 Humanoid Traders 打造 | 為 Bitget AI Base Camp 而生</h4>
<h5 align="center">為 Bitget AI Base Camp 打造 · Hackathon S1 — 策略與風險類別</h5>

<p align="center">
  <a href="https://humanoid-traders-1.gitbook.io/humanoid-traders-ai"><img src="https://img.shields.io/badge/Full_Documentation-%E2%86%92_GitBook-blue?style=for-the-badge&logo=gitbook&logoColor=white" alt="Full Documentation → GitBook"></a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11+-blue?logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/license-AGPL--3.0-blue" alt="License AGPL-3.0">
  <a href="https://github.com/Humanoid-Traders/RUNECLAW/actions/workflows/ci.yml"><img src="https://github.com/Humanoid-Traders/RUNECLAW/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI"></a>
  <img src="https://img.shields.io/badge/tests-862%20test%20functions%20defined-brightgreen" alt="862 Test Functions Defined">
  <img src="https://img.shields.io/badge/security%20tests-29%20passing-blueviolet" alt="29 Security Tests">
  <img src="https://img.shields.io/badge/red%20team-28%20scenarios%20%7C%20framework%20included-critical" alt="Red Team 28 Scenarios | Framework Included">
  <img src="https://img.shields.io/badge/risk%20checks-21%20(16%20strict%20%2B%205%20advisory)-red" alt="21 Risk Checks">
  <img src="https://img.shields.io/badge/mode-live%20trading-green" alt="Live Trading">
  <img src="https://img.shields.io/badge/exchange-Bitget-blue" alt="Bitget">
  <img src="https://img.shields.io/badge/bot-LIVE%20%40HTRUNECLAW__bot-26a5e4?logo=telegram" alt="Live Telegram Bot">
  <img src="https://img.shields.io/badge/hackathon-AI%20Base%20Camp%20S1-purple" alt="AI Base Camp Hackathon S1">
</p>

<p align="center">
  <a href="https://github.com/Humanoid-Traders/RUNECLAW">GitHub</a> &middot;
  <a href="https://y9z5438h.mule.page/">網站</a> &middot;
  <a href="https://humanoid-traders-1.gitbook.io/humanoid-traders-ai">文件</a> &middot;
  <a href="https://t.me/HTRUNECLAW_bot">實機機器人</a> &middot;
  <a href="https://t.me/+VRNgsmkR5pszZTdk">Telegram</a> &middot;
  <a href="https://x.com/BaurPatric70363">X / Twitter</a>
</p>

<p align="center">
  <b>立即實機體驗 &rarr; <a href="https://t.me/HTRUNECLAW_bot">@HTRUNECLAW_bot</a></b>
</p>

<p align="center">
  <a href="./README.md">English</a> &middot; <b>繁體中文</b>
</p>

---

> **免責聲明：** RUNECLAW 是為 Bitget AI Base Camp · Hackathon S1 打造的教育性原型。
> 它**尚未達到正式生產水準**，在未經大量額外防護、獨立安全稽核、壓力測試與法規審查之前，
> **絕不可**使用真實資金操作。
> 回測結果使用合成資料，無法預測未來表現。過往績效
> 並不代表未來結果。本專案不構成財務建議。

---

## 什麼是 RUNECLAW？

**RUNECLAW** 是由 **Humanoid Traders** 為 Bitget AI Base Camp · Hackathon S1 打造的 AI 交易指揮系統。它將多時間框架分析、匯流評分、市況偵測、訂單流微結構與風險優先邏輯整合為一套講求紀律的框架 —— 全部都可透過 Telegram 機器人介面操控。

本系統**預設以模擬優先模式運作**。每一個交易構想都必須通過 21 項交易前風險檢查（16 道嚴格的失敗即關閉閘門 + 1 道失敗即放行的流動性防護 + 4 項在資料不可用時略過的顧問性檢查）、一道對抗式自我批判閘門，並在執行前取得明確的人工確認。

> **Shield 風險引擎以 MCP 伺服器形式提供 —— 任何 GetClaw agent 都可呼叫它。** 詳見 `bot/mcp/server.py`。

**核心理念：** 機器人提出建議。人類做出決定。風險引擎負責執行。

### AI 學習系統 (NEW)
RUNECLAW 包含一套完整的**自我改進 AI 學習系統**，內含 8 個整合模組：
- **經驗記憶 (Experience Memory)** —— 每一個交易決策都連同完整市場脈絡一併記錄
- **反思引擎 (Reflection Engine)** —— 交易後分析產生經驗教訓與改進提案
- **策略評估器 (Strategy Evaluator)** —— 以 S/A/B/C/D 級排名進行風險調整後評分
- **型態學習器 (Pattern Learner)** —— 跨市況偵測反覆出現的市場型態
- **總經學習器 (Macro Learner)** —— 追蹤加密貨幣對 FOMC/CPI/NFP/PCE 事件的反應
- **模型比較器 (Model Comparer)** —— 並列追蹤規則式與 LLM 的準確度
- **提示最佳化器 (Prompt Optimizer)** —— 為提示做版本追蹤並進行績效評分
- **回饋收集器 (Feedback Collector)** —— 將人工回饋整合進學習迴圈

所有提案都會通過一道**安全政策**，含禁止動作清單、提高風險的關鍵字偵測，以及變更分類。型態永遠**不得**覆寫風險引擎（透過 Pydantic validator 強制執行）。

### LLM Token 最佳化器 (NEW)
一套 4 層最佳化流程，可將 LLM API 成本最多降低 70%：
- **語意快取 (Semantic Cache)** —— 以市場市況、RSI 區間、MACD 方向為鍵的 TTL 分桶回應快取
- **分層流程 (Tiered Pipeline)** —— 第 1 層（免費規則）處理明確訊號、第 2 層（迷你模型）處理中等情形、第 3 層（完整模型）處理高潛力情形
- **智慧批次 (Smart Batching)** —— 單次 LLM 呼叫最多合併 5 個標的
- **自適應頻率 (Adaptive Frequency)** —— 在平靜／低 ADX 的市場中完全略過 LLM

### 多供應商 LLM 支援 (NEW)
RUNECLAW 透過 `LLM_BASE_URL` 支援任何相容於 OpenAI 的 LLM 供應商：
- **Google Gemini 2.5 Flash** —— 預設供應商，使用免費方案 API 金鑰即可零成本推理
- **Alibaba Qwen** —— 透過 DashScope 使用 `qwen-max`、`qwen-plus`、`qwen-turbo`（Hackathon S1 合作夥伴）
- **Groq** —— `llama-3.3-70b-versatile`，推理最快（免費方案）
- **OpenRouter** —— `qwen/qwen3.6-35b-a3b`，每百萬 token $0.15（最便宜的前沿模型）
- **Together AI / Fireworks** —— 開源 Qwen 模型，推理快速
- **本機 (vLLM/Ollama)** —— 自架以達成零 API 成本

> **零成本設定：** 設定 `LLM_PROVIDER=gemini` 與 `LLM_MODEL=gemini-2.5-flash`，並使用來自 [Google AI Studio](https://aistudio.google.com/apikey) 的免費 API 金鑰。無需信用卡。

### 多時間框架掃描模式 (NEW)
三個專屬掃描指令，輸出達儀表板等級的豐富內容：
- **`/scalp`** —— 5 分鐘 K 線、依成交量取前 3 名、緊密 SL/TP，適合快速交易
- **`/intraday`** —— 15 分鐘 K 線、依漲跌幅取前 5 名變動者
- **`/swing`** —— 4 小時 K 線、寬鬆 SL/TP、順勢佈局

每次掃描產生 4 個區塊：**帳戶狀態 (Account Status)**（權益、部位、斷路器）、**即時報價 (Live Tickers)**（價格、24 小時變動、成交量表格）、**市況評估 (Regime Assessment)**（逐一標的的敘述，含 RSI、VWAP、EMA20、支撐／壓力位）、以及**掃描裁決 (Scan Verdict)**（可操作的交易構想，含進場/SL/TP/R:R 與信心條）。

### 多資產宇宙 — 加密貨幣 + 傳統金融永續合約 (NEW)
除了加密貨幣，RUNECLAW 也掃描並交易 **Bitget 上的非加密 USDT-M 永續合約** —— 相同的風險引擎、AI 分析與人工確認流程適用於每一個類別。以 `/mode <universe>`（或 `.env` 中的 `ASSET_UNIVERSE`）即可瞬間切換焦點，無需重啟：

| `/mode` | 宇宙 | 標的 |
|---------|----------|-------------|
| `all_markets` | 所有市場 | 加密貨幣 + 以下所有傳統金融永續合約 |
| `solana` | Solana 生態系 | 15 個 SOL 生態系代幣（見下文） |
| `metals` | 貴金屬 + 工業金屬 | 黃金 (XAU)、白銀 (XAG)、鉑 (XPT)、鈀 (XPD)、銅、PAX Gold |
| `commodities` | 能源 | WTI 原油 (CL)、布蘭特 (BZ)、天然氣 |
| `stocks` | 美股永續合約 | TSLA, AAPL, MSFT, GOOGL, AMZN, META, NVDA, AMD, COIN, MSTR, HOOD, PLTR, ARM, MRVL, INTC |
| `etfs` | 行業／區域 ETF | XLK, DFEN, KWEB, SGOV, EWH, INDA |
| `pre_ipo` | Pre-IPO 代幣 | OpenAI、Anthropic |
| `tradfi` | 所有非加密類 | 金屬 + 大宗商品 + 股票 + ETF + pre-IPO |
| `hybrid` | 混合 | 加密貨幣主流幣 + 精選傳統金融 |

**傳統金融感知風險：** 金屬與能源 24/7 交易；美股／ETF 永續合約遵守**具日光節約時間 (DST) 感知的美國市場時段**（引擎會區分常規與延長交易時段）。股票部位採用更緊密的 SL/TP 乘數與最大相關股票上限（不會一次堆疊 5 檔科技股），而金屬帶有行業標籤（貴金屬／工業）以供具相關性感知的集中度限制使用。

### Solana 生態系模式 (NEW)
在 `.env` 中設定 `ASSET_UNIVERSE=solana` 或使用 `/mode solana`，即可優先處理 15 個 Solana 生態系代幣。全部都在 Bitget 上以完整 USDT 配對支援交易。

**代幣：** SOL, JUP, JTO, BONK, WIF, PYTH, RAY, ORCA, RENDER, HNT, MOBILE, W, JITO, TENSOR, DRIFT

**Solana 專屬風險調整：**
- **迷因幣波動防護**：BONK 與 WIF 採用更緊密的 4% ATR 門檻（相對於預設 6%），以防止在極端波動飆升期間進場
- **生態系相關性群組**：非迷因類 Solana 代幣（JUP, JTO, PYTH, RAY 等）被歸類為 `SOLANA_ECO` —— 風險引擎會限制跨相關資產的集中下注
- **實機模式切換**：`/mode solana` 與 `/mode all` 可在不重啟下切換掃描器焦點

### 自然語言介面 (NEW)
用平易的中文（或英文）與 RUNECLAW 對話，不必背誦指令：
- **意圖路由**：「比特幣怎麼樣？」會派送到 `/analyze BTC`，「有什麼在動？」會觸發 `/scan`
- **標的擷取**：能理解代號（`$ETH`）、名稱（`Solana`）與配對（`BTC/USDT`）
- **LLM 後備**：當規則式型態無法比對時，選用的 LLM 分類會路由到正確的技能
- **AI 對話**：未比對到的訊息會由 LLM 給出貼合脈絡的回應（絕不會憑空捏造交易）

### 雙語介面 — English / 繁體中文 (NEW)
整個 Telegram 介面皆**以英文與繁體中文（繁體中文）在地化**：
- **每位使用者的語言偏好** —— 每位使用者以 `/lang` 選擇自己的語言；該選擇會逐一使用者儲存，因此訊息、選單、警示與風險說明都會以所選語言呈現。
- **完整涵蓋** —— 指令、確認、風險檢查裁決、交易通知與錯誤訊息全部翻譯（見 `bot/utils/i18n.py`，`SUPPORTED_LANGS = {en, zh}`）。
- **安全後備** —— 任何未翻譯的鍵都會回退為英文，而非失敗。

### 主動式警示監控 (NEW)
背景協程會主動推送未經請求的警示，無需等待指令：
- **斷路器**跳脫與解除
- 已掃描資產上的**成交量飆升**
- **黑天鵝**異常偵測
- **引擎狀態變更**（停止、冷卻）
- 等待確認的**新交易訊號**

以 `/watch on|off` 為每個對話切換。唯讀 —— 此監控絕不會建立交易或修改風險。

### 紅隊壓力測試器 (NEW)
一個對抗式引擎，以橫跨 10 個類別的 28 種情境攻擊風險引擎：
閃崩、流動性枯竭、相關性拋售、植入過時資料、信心操控、
R:R 操弄、規避斷路器、零值／負值、方向反轉以及最大部位灌爆。
內含紅隊測試框架 —— 執行 28 種對抗式情境以驗證風險閘門行為。包含 ATR=0 的壞資料測試。

### 對抗式自我批判閘門 (NEW)
交易前的看空情境分析，會在每一筆已確認的交易執行前運行：
- 7 項啟發式檢查：過度自信（>90%）、邊際 R:R（<1.5x）、方向擁擠（3+ 同方向）、同資產加碼、投組熱度（4+ 持倉）、總經逆風、過緊停損（距進場 <1%）
- 在 3+ 項疑慮時給出 **HALT** 裁決，並附完整說明阻止執行
- **WARN** 裁決會記錄疑慮但允許交易繼續進行
- 失敗即放行設計：批判錯誤絕不阻擋交易（與失敗即關閉的風險引擎不同）

### 投組風險值 (Value at Risk) (NEW)
參數式 VaR，作為第 #21 項風險檢查：
- 使用歷史的逐筆交易報酬波動度，採 95% 信賴區間
- 拒絕會將投組 VaR 推升至超過權益 15% 的交易（可透過 `MAX_PORTFOLIO_VAR_PCT` 設定）
- 當已平倉交易少於 5 筆（歷史不足）時，會優雅地略過

### 加密簽章存證 (NEW)
採用 Ed25519 數位簽章以達成稽核鏈的不可否認性：
- 在一批稽核項雜湊上計算 Merkle 根
- 以 Ed25519 私鑰簽署（首次執行時產生，儲存於 `data/attestation_key.bin`）
- 可將任一批次對照公鑰驗證，以證明該批項目由此機器人實例所建立
- 優雅後備：若缺少 `cryptography` 套件，SHA-256 雜湊鏈仍可運作

### 黑天鵝偵測器 (NEW)
一種先於斷路器啟動的統計異常偵測。監控 5 種異常類型：
相關性瓦解、成交量崩塌、價格加速（閃崩）、波動度爆發（ATR 飆升），
以及買賣價差擴大。會在斷路器的 5% 每日虧損門檻觸發**之前**先行預防性停止。

### 情緒代理引擎（基於價格/成交量）(NEW)
以價格衍生的情緒代理作為第 11 位匯流投票者。結合：
- **恐懼與貪婪指數**（0-100），來自價格動能（40%）、成交量趨勢（30%）、波動度（30%）
- **逆向邏輯**：極度恐懼 -> 看多票 [+0.3, +0.6]，極度貪婪 -> 看空票 [-0.6, -0.3]
- **資金費率逆向**：極端正向資金費率加上看空偏移，極端負向則加上看多偏移

### 多代理蜂群協定 (NEW)
透過實驗性的程序內 pub/sub 架構達成可組合的代理協作。五個專責代理：
Scanner（感知市場）、Analyst（產生論點）、Risk（為每筆交易把關）、Executor（管理部位）、
Sentinel（監測黑天鵝）。透過 SwarmBus pub/sub 通訊，當嚴重度 >= 0.8 時 Sentinel 會向所有代理廣播 HALT。
已可作為獨立的 Agent Hub 代理部署至正式環境。

### 多用戶實盤交易 (NEW)
RUNECLAW 支援**多位使用者各自在自己的 Bitget 帳戶上實盤交易**，彼此互相隔離。預設為關閉 —— 設定 `PER_USER_LIVE_ENABLED=true` 以啟用。每一層都受到把關，且在啟用前操作者路徑與位元組完全相同：
- **自有帳戶執行** —— 每位使用者透過 `/connect` 連結自己的金鑰（靜態時以 Fernet 加密）；其已確認的交易在**其自己的**帳戶上執行，絕非操作者的帳戶。未連結金鑰的使用者會被拒絕，絕不會被默默路由。
- **逐一使用者的風險隔離** —— 每位使用者擁有自己的斷路器、虧損連續、每日虧損與回撤狀態；一位使用者的停止絕不會中止其他任何人。
- **依自有權益的部位規模** —— 部位依使用者自身餘額而非操作者的餘額來決定大小。
- **逐一使用者的保證金上限** —— 管理員以 `/setcap` 限制某使用者的每筆交易保證金（僅可收緊，絕不可高於全域實盤上限）。
- **全域緊急停止開關** —— 緊急停止／`/closeall` 會將**每一個**帳戶（操作者 + 所有逐一使用者）平倉，並同時停止所有風險引擎；`/reset` 可恢復。
- **逐一使用者的可觀測性** —— `/accounts` 顯示每個帳戶的即時權益、未平倉部位、曝險、斷路器狀態、調控節流以及已設定的上限。
- **專屬存取允許清單** —— 透過 `LIVE_TRADER_TELEGRAM_IDS` 為一般實盤使用者開通（授予實盤交易權限，但**不**含操作者／管理員特權），再以 `/approve` + `/grant_live` 完成。

> 實盤啟用、強化順序，以及首位實盤使用者的飛行前檢查清單，記錄於 `docs/MULTI_USER_LIVE_SETUP.md`、`docs/LIVE_HARDENING_RUNBOOK.md` 與 `docs/FIRST_LIVE_USER_PREFLIGHT.md`。

### 實現績效調控器 (NEW)
在交易前檢查之上的閉迴路後盾（受 `LIVE_PERFORMANCE_GOVERNOR_ENABLED` 把關）。它為每個帳戶近期**已平倉**交易的實現勝率與淨損益評分，並在結果惡化時自動**縮減**部位規模 —— 或在某帳戶既經常虧損又淨值為負時**暫停**交易。僅可收緊；於 `/accounts` 中呈現。

---

## 架構

```
 Telegram Bot        API Bridge (8000)      Bitget Exchange
      |                    |                      |
      v                    v                      v
 +-----------+    +---------------+   +-----------+
 |  Skill    |--->|  RuneClaw     |-->|  Market   |
 |  Registry |    |  Engine       |   |  Scanner  |
 +-----------+    +-------+-------+   +-----------+
                          |                 |
                   +------+------+    OHLCV / Tickers
                   |             |
              +----v----+  +----v-----+
              |   AI    |  |  Risk    |
              | Analyzer|  |  Engine  |
              +---------+  +----+-----+
                   |            |
              Trade Idea   Risk Check
                   |            |
                   v            v
              +----+------------+----+
              | Human Confirmation   |
              | (Telegram Keyboard)  |
              +----------+-----------+
                         |
                    +----v----+
                    |Portfolio|
                    | Tracker |
                    +---------+
```

**執行期服務：**
- **Telegram Bot**（內部 8080 連接埠）—— 指令介面、人在迴圈中的確認
- **API Bridge**（8000 連接埠）—— 公開引擎端點的 FastAPI REST API（`/health`、`/scan`、`/portfolio`、`/risk/status`、`/confirm`）
- **Redis**（內部 6379 連接埠，未對主機公開）—— LLM 快取、速率限制、工作階段狀態
- **Dashboard**（經由 API Bridge 提供）—— War Room、即時訊號、投組檢視

**流程：** SCAN --> ANALYZE --> RISK GATE --> HUMAN CONFIRM --> EXECUTE（紙上或實盤；實盤受把關且需人工確認，僅管理員可自動執行）

### 執行期服務

| 服務 | 連接埠 | 說明 |
|---------|------|-------------|
| **bot** | 8080（內部） | Telegram 機器人 + 儀表板伺服器。於 8080 進行 Socket 健康檢查。 |
| **api_bridge** | 8000 | 供外部整合（War Room、即時訊號、MCP）使用的 FastAPI REST API。於 `/health` 健康檢查。 |
| **redis** | 6379（內部） | 工作階段狀態、LLM 快取、速率限制。AOF 持久化。密碼保護，未對主機公開。 |
| **nginx** | 80/443 | TLS 反向代理（選用）。提供靜態網站，將 `/api/*` 代理至 api_bridge。 |

> **注意：** 即使在 `--mode telegram` 下，bot 服務也會在 8080 連接埠執行 HTTP 伺服器供儀表板使用。健康檢查仰賴此項。Redis 僅在 Docker 網路內部。

---

## 功能特色

### 市場情報
- 即時掃描 67 個 Bitget USDT 配對（API bridge）／324+ 配對（Telegram 機器人）
- **多資產：** 加密貨幣外加非加密 USDT-M 永續合約 —— 金屬（黃金／白銀／鉑／鈀／銅）、能源（WTI/Brent/NatGas）、美股永續合約（TSLA, NVDA, AAPL, …）、ETF 與 pre-IPO 代幣 —— 可透過 `/mode` 切換（見「多資產宇宙」）
- 成交量飆升偵測（2 倍滾動平均）
- 具可設定門檻的動能評分
- 前 N 名變動者排名，附結構化訊號輸出

### AI 分析引擎
- 技術指標：RSI-14、MACD (12/26/9)、布林通道 (20/2)、ATR-14、ADX-14、VWAP、SMA-50 趨勢對齊、能量潮 (OBV)、滾動 VWAP（20 根與 50 根）
- K 線型態偵測：14 種型態（偵測並貢獻於型態評分），包含十字星、錘子、射擊之星、吞沒、母子、頂／底鑷子、晨星／夜星、紅三兵、黑三鴉
- 費波那契回撤位：在 50 根回看區間偵測波段高／低點，標準位階（23.6%、38.2%、50%、61.8%、78.6%）並附區間分類
- 30+ 投票者匯流評分模型（自原本的 6 位擴展而來；部分投票者源自重疊的資料來源）：動能（RSI、MACD、Stochastic）、趨勢（ADX、EMA 緞帶、VWAP）、成交量（飆升、OBV、成交量分布/POC）、結構（布林 %B、Donchian、Keltner 擠壓、費波那契）、型態（圖表型態、Elliott、Wyckoff、諧波、流動性掃蕩）、訂單流（CVD、掛單失衡、巨鯨成交、資金費率），外加多時間框架、聰明錢與情緒投票者，以及混合後的 LLM 信心。可選用的家族上限去相關與習得的逐一投票者權重。
- 由 LLM 驅動的方向性論點產生（預設 Gemini 2.5 Flash，相容 GPT-4o / Anthropic / Groq）
- 未設定 LLM 金鑰時採規則式後備
- 結構化的 `TradeIdea` 輸出，含進場、SL、TP、信心、推理

### 聰明錢引擎 (NEW)
- **清算瀑布偵測** —— 資金費率極值 + OI 變動 + CVD 背離，預示擁擠交易的清算風險
- **資金費率擠壓** —— 具滾動動能追蹤的逆向佈局偵測器
- **巨鯨流向追蹤** —— 滾動買／賣歷史，含隱形吸籌偵測與一致性放大器
- **綜合評分** —— 加權混合（機構 35%、逆向 20%、巨鯨 25%、瀑布 20%）正規化至 [-1, 1]
- 具界限記憶的執行緒安全滾動狀態

### 多時間框架分析 (NEW)
- 使用 EMA20 與 EMA50，於 1H/4H/1D 進行 **HTF 趨勢對齊**
- **市場結構偵測** —— 波段高／低點、HH/HL（看多）、LH/LL（看空）
- **結構突破 (BOS)** —— 價格超越上一個波段點
- **特徵轉變 (CHoCH)** —— 結構性反轉偵測
- 對齊評分，附衝突時間框架懲罰
- 當 HTF 資料不可用時優雅後備

### 自適應策略模式 (NEW)
- 依市況 + 脈絡選擇的 **5 種策略模式**：
  - TREND_CONTINUATION：寬鬆 TP（R:R 2.0），需 HTF 對齊
  - BREAKOUT：高信心條（0.65），需 BOS + 成交量
  - MEAN_REVERSION：緊密 SL/TP，RSI/BB 極值，CVD 背離
  - LIQUIDITY_SWEEP：最高信心條（0.68），需瀑布 + 巨鯨確認
  - CONSERVATIVE：預設／不確定，標準參數
- 逐模式的 SL/TP 乘數、最低信心與匯流加成
- 模式選擇會被稽核並予以說明

### 可解釋性引擎 (NEW)
- **結構化推理鏈** —— 從資料收集到風險評估的逐步邏輯
- **因子歸因** —— 逐一指標的貢獻百分比，附最強看多／看空因子
- **合規評分** —— 可解釋性、資料充分性、風險文件、稽核軌跡
- **自然語言敘述** —— 供 Telegram 使用的一行摘要，供稽核使用的多段詳述
- 設計上支援 MiCA 式的決策可稽核性

### 風險引擎（失敗即關閉）
- **21 項交易前檢查** —— 16 道嚴格失敗即關閉（一項失敗 = 拒絕）、1 道失敗即放行（#17 LIQUIDITY：無掛單簿資料 = 通過）、4 項顧問／略過（#18 MACRO、#19 MTF、#20 PCA、#21 VaR：資料不可用時略過）。權威清單見 `config/risk_manifest.yaml`。
- 斷路器在每日虧損或回撤突破時停止交易
- 固定分數部位規模：風險預算（權益的 2%）除以停損距離，上限為名目的 20%
- 最大未平倉部位限制
- 風險／報酬比下限（1.2x）
- 信心門檻閘門（≥60%）
- 逐一標的曝險限制（每資產最多 20%）
- 相關性群組集中度防護（每群組最多 2 個部位，例如 ALT_L1、SOLANA_ECO）
- 連續虧損連勝偵測 + 冷卻
- 過時資料防護（拒絕逾 5 分鐘的構想）
- 波動度防護（基於 ATR）
- 確認時重新檢查（市場可能已變動）

### 紙上交易
- 完整投組追蹤，含損益、勝率與回撤
- 自動停損與停利監控
- 供事後檢討用的交易歷史帳本
- 預設 $10,000 紙上餘額（可設定）

### Telegram 機器人介面
- 每項操作皆有斜線指令
- 供交易確認／拒絕用的內嵌鍵盤
- 逐一使用者的速率限制（20 次/分鐘）
- 即時狀態與風險儀表板

### 稽核軌跡
- 結構化 JSON 記錄（JSONL 格式）
- 三個頻道：`trade.jsonl`、`risk.jsonl`、`system.jsonl`
- 每一個決策、確認與拒絕都被記錄
- 供 Hackathon 後分析的機器可讀格式

### 回測

RUNECLAW 提供兩種回測模式：

| 模式 | 腳本 | 資料來源 | LLM | 用途 |
|------|--------|-------------|-----|---------|
| **合成** | `backtest_audit.py` | GBM+GARCH 隨機漫步 | 關閉 | 在雜訊上對風險閘門做健全性檢查 |
| **真實資料** | `backtest_realdata.py` | Bitget 歷史 OHLCV | 可設定 | 策略績效驗證 |

```bash
# 合成回測（驗證風險引擎行為）
python backtest_audit.py

# 含買進並持有基準的真實資料回測
python backtest_realdata.py --symbols default

# 啟用 LLM 分析的真實資料回測
python backtest_realdata.py --symbols all --llm
```

**方法論透明度：**
- 合成回測使用隨機漫步資料，**無法**驗證產生超額報酬 (alpha) 的模組（聰明錢、訂單流、情緒、清算瀑布）。它們用以證明在各種雜訊市況下風險閘門與規則式後備運作正確。
- 真實資料回測使用實際 Bitget OHLCV，並建模手續費（0.10%）與滑價（0.05%），並含買進並持有基準以供比較。
- 帶 `--llm` 旗標的結果反映完整 AI 分析流程；不帶時則僅運行規則式後備。

---

## 快速開始

```bash
# 1. 複製儲存庫
git clone https://github.com/Humanoid-Traders/RUNECLAW.git
cd RUNECLAW

# 2. 建立虛擬環境
python -m venv .venv
source .venv/bin/activate

# 3. 安裝相依套件
pip install -r bot/requirements.txt

# 4. 設定環境
cp .env.example .env
# 在 .env 中填入你的 API 金鑰

# 5. 以 CLI 模式執行（不需 Telegram token）
python -m bot.main --mode cli

# 6. 以 Telegram 機器人執行
python -m bot.main --mode telegram

# 7. 單次市場掃描
python -m bot.main --mode scan
```

---

## Telegram 指令

| 指令 | 說明 |
|---------|-------------|
| `/start` | 含 War Room 導覽的主選單 |
| `/status` | 引擎狀態、健康分數、資金、風險量表 |
| `/scan` | 掃描市場找出前段變動者與成交量飆升 |
| `/scalp` | 豐富版剝頭皮掃描（5m K 線、依成交量取前 3） |
| `/intraday` | 豐富版日內掃描（15m K 線、前 5 名變動者） |
| `/swing` | 豐富版波段掃描（4h K 線、基於趨勢） |
| `/analyze BTC` | 對特定資產執行 AI 分析 |
| `/run` | 策略預設（逢低狙擊、動能、剝頭皮） |
| `/portfolio` | 檢視紙上投組，附損益瀑布 |
| `/trade` | 檢視並確認／拒絕待處理交易 |
| `/journal` | 交易歷史，附勝／負拆解 |
| `/risk` | 含視覺量表的風險儀表板 |
| `/rejected` | 近期遭風險拒絕的交易，附失敗原因 |
| `/whynot [SYM]` | 說明某交易為何遭拒絕 |
| `/dashboard` | 指揮中心（狀態/風險/部位分頁） |
| `/backtest` | 以合成資料執行回測（僅規則式，LLM 關閉） |
| `/walkforward` | 前推驗證（過度擬合偵測） |
| `/macro` | 總經事件行事曆（FOMC、CPI、NFP） |
| `/learn` | AI 學習系統儀表板（8 模組） |
| `/patterns` | 檢視已偵測的市場型態 |
| `/proposals` | 檢視待處理的改進提案 |
| `/optimize` | LLM token 最佳化統計 |
| `/costs` | 代理經濟學（LLM + 基礎設施拆解） |
| `/watch on\|off` | 切換主動式警示 |
| `/halt` | 緊急停止開關（於所有帳戶跳脫斷路器、取消全部） |
| `/closeall` | 管理員：將每一個帳戶（操作者 + 逐一使用者）的未平倉部位平倉 |
| `/pause` / `/resume` | 暫停／恢復交易 |
| `/mode <universe>` | 切換資產宇宙 — `solana`、`metals`、`commodities`、`stocks`、`etfs`、`pre_ipo`、`tradfi`、`hybrid`、`all_markets`（不需重啟） |
| `/setllm` | 在執行期切換 LLM 供應商（BYOK） |
| `/llmstatus` | 目前 LLM 供應商與模型資訊 |
| `/lang` | 切換介面語言 — English / 繁體中文（逐一使用者） |
| `/paper on\|off` | 為你的交易切換紙上與實盤執行 |
| `/help` | 列出所有可用指令 |

### 掃描、分析與策略指令

| 指令 | 說明 |
|---------|-------------|
| `/deepscan` / `/fullscan` | 多時間框架深度掃描／全宇宙掃描 |
| `/stockscan` | 掃描美股永續合約宇宙 |
| `/forcescan` | 強制立即執行一次掃描週期 |
| `/momentum` `/dip` `/squeeze` `/sweep` `/zones` | 策略預設捷徑（`/run <preset>` 的別名） |
| `/buy <SYM>` / `/sell <SYM>` | 暫存一個手動做多／做空交易構想（仍受風險把關 + 確認） |
| `/strategy` | 啟用中的策略 + 市況路由 |
| `/session` | 目前交易時段的部位規模脈絡 |
| `/performance` | 績效摘要（勝率、損益、R） |
| `/daily_report` | 日終交易報告（交易、勝/負、最佳/最差、損益） |
| `/equitycurve` | 權益曲線檢視 |
| `/holdtime` | 持有時間分布分析 |
| `/attribution` | 逐因子損益歸因 |
| `/crossasset` | 跨資產相關性檢視 |
| `/montecarlo` | 蒙地卡羅穩健性模擬 |
| `/signals` / `/latest_signal` | 訊號統計／最近一筆訊號 |
| `/orders` / `/open_positions` | 未成交訂單／未平倉部位 |
| `/autoconfirm` | 切換管理員自動確認（0.85 閘門） |
| `/playbook` | GetAgent playbook 控制 |
| `/llmtiers` / `/llmreset` | LLM 分層路由設定／重設 |
| `/set_tier` `/revoke` | 管理員：設定使用者分層／撤銷存取 |
| `/channel` `/broadcast` | 管理員：行銷頻道轉發器 |

### 實盤與多用戶指令

| 指令 | 對象 | 說明 |
|---------|-----|-------------|
| `/connect <key> <secret> <pass>` | 使用者 | 連結你自己的 Bitget 帳戶（僅限私訊；經驗證、靜態加密） |
| `/disconnect` | 使用者 | 移除你已連結的 Bitget 金鑰 |
| `/exchange` | 使用者 | 查看你的連結帳戶狀態 |
| `/livebalance` | 使用者 | 你的即時 Bitget 餘額 |
| `/livepositions` | 使用者 | 你的未平倉實盤部位，附 SL/TP |
| `/liveclose <id>` | 使用者 | 平掉你其中一個實盤部位 |
| `/golive CONFIRM` | 管理員 | 啟用實盤交易（當未以環境變數啟用時） |
| `/approve <id> [role]` | 管理員 | 核准待處理使用者（trader/viewer/admin） |
| `/grant_live <id>` / `/revoke_live <id>` | 管理員 | 授予／撤銷使用者的實盤交易權限 |
| `/setcap <id> <usd\|off>` | 管理員 | 限制使用者的每筆交易保證金（僅可收緊） |
| `/accounts` | 管理員 | 逐帳戶實盤風險：權益、曝險、斷路器、調控器、上限 |
| `/users` | 管理員 | 已註冊使用者名冊（角色、分層、模式） |
| `/health` | 管理員 | 引擎生命徵象（WS、餘額、tick 健康度） |
| `/slippage` | 管理員 | 執行品質／滑價漂移 |
| `/calibration` | 管理員 | 信心校準學習器就緒度 |

> 多用戶實盤交易預設為關閉（`PER_USER_LIVE_ENABLED`）。開通說明見 `docs/MULTI_USER_LIVE_SETUP.md`。

交易確認使用 Telegram 內嵌鍵盤 —— 直接在對話中點按 **Confirm** 或 **Reject**。

---

## 專案結構

```
runeclaw/
|-- bot/
|   |-- main.py                 # Entry point (telegram / cli / scan / backtest)
|   |-- config.py               # All settings from env, fail-closed defaults
|   |-- core/
|   |   |-- engine.py           # Central orchestrator (9-state FSM)
|   |   |-- market_scanner.py   # Bitget market scanner, volume spike detection
|   |   |-- analyzer.py         # AI + technical analysis, 30+ voter confluence
|   |   |-- order_flow.py       # Exchange microstructure: CVD, book imbalance, whales
|   |   |-- smart_money.py      # Liquidation cascade, funding squeeze, whale tracking
|   |   |-- multi_timeframe.py  # HTF alignment, market structure, BOS/CHoCH
|   |   |-- strategy_modes.py   # 5 adaptive strategy modes with per-mode configs
|   |   |-- red_team.py         # 28-scenario adversarial stress tester
|   |   |-- black_swan.py       # Statistical anomaly detection (5 anomaly types)
|   |   |-- sentiment.py        # Sentiment proxy engine (price/volume-based, 11th confluence voter)
|   |   |-- swarm.py            # Multi-agent swarm protocol (experimental, in-process pub/sub)
|   |   |-- explainability.py   # Reasoning chains, factor attribution, compliance
|   |   |-- ta_utils.py         # Shared TA utilities (EMA, ADX, Regime)
|   |   |-- metrics.py          # Sharpe/Sortino/Calmar from per-trade returns
|   |   |-- llm_cache.py        # Semantic LLM response cache with TTL
|   |   |-- token_optimizer.py  # Tiered pipeline, smart batching, adaptive frequency
|   |-- risk/
|   |   |-- risk_engine.py      # 21-check risk gate, circuit breaker
|   |   |-- portfolio.py        # Paper trading ledger, PnL tracking, mark-to-market
|   |-- learning/
|   |   |-- orchestrator.py     # 10-step learning workflow coordinator
|   |   |-- experience.py       # Decision memory and trade history
|   |   |-- reflection.py       # Post-trade reflection and lesson extraction
|   |   |-- strategy_eval.py    # Risk-adjusted strategy scoring (S/A/B/C/D tiers)
|   |   |-- patterns.py         # Recurring pattern detection
|   |   |-- macro_learner.py    # Macro event reaction tracking
|   |   |-- model_compare.py    # Rule-based vs LLM accuracy comparison
|   |   |-- prompt_opt.py       # Prompt version tracking and optimization
|   |   |-- feedback.py         # Human feedback collection
|   |   |-- safety_policy.py    # Immutable safety rules, blocked actions
|   |   |-- store.py            # JSON-based learning data persistence
|   |   |-- models.py           # Pydantic models for all learning records
|   |-- macro/
|   |   |-- calendar.py         # 2026 FOMC/CPI/NFP/PCE event calendar
|   |   |-- models.py           # Macro event and risk state models
|   |-- skills/
|   |   |-- skill_registry.py   # Modular skill system, built-in skills
|   |   |-- telegram_handler.py # Telegram bot commands, inline keyboards
|   |-- backtest/
|   |   |-- engine.py           # Backtest engine with intrabar SL/TP + walk-forward
|   |   |-- data_loader.py      # Synthetic data (GBM + GARCH), CSV, Bitget fetch
|   |   |-- models.py           # Backtest data models
|   |-- utils/
|   |   |-- models.py           # Pydantic schemas (TradeIdea, RiskCheck, etc.)
|   |   |-- trailing.py         # Shared trailing-stop logic
|   |   |-- logger.py           # Structured JSON audit logging
|   |-- prompts/
|   |   |-- system_prompt.md    # Agent persona and capabilities
|   |   |-- skill_definitions.yaml
|   |-- requirements.txt
|-- tests/
|   |-- test_core.py            # 383 core engine tests
|   |-- test_quant_skill.py     # 95 quant skill tests
|   |-- test_learning.py        # 77 learning system tests
|   |-- test_intent_and_monitor.py  # 47 intent routing + monitor tests
|   |-- test_learning_cannot_override_risk.py  # 45 safety policy tests
|   |-- test_ux_upgrades.py     # 39 UX upgrade tests
|   |-- test_token_optimizer.py # 36 token optimizer tests
|   |-- test_risk_upgrades.py   # 31 risk upgrade tests
|   |-- test_quant_upgrades.py  # 31 quant upgrade tests
|   |-- test_intelligence_upgrades.py  # 30 intelligence tests
|   |-- test_security.py        # 29 security tests
|   |-- test_macro.py           # 27 macro calendar tests
|   |-- test_var_critique_attestation.py  # 25 VaR/critique/attestation tests
|   |-- test_execution_upgrades.py  # 25 execution upgrade tests
|   |-- test_logic_bugs.py      # 24 logic regression tests
|   |-- test_exchange_and_compliance.py  # 20 exchange/compliance tests
|   |-- test_manifest_and_whynot.py  # 10 manifest tests
|   |-- test_live_executor.py   # 7 live executor tests
|   |-- test_telegram_commands.py  # Telegram command tests
|   |-- selftest_upgrade.py     # Self-test upgrade harness
|   |-- (862 total test functions)
|-- docs/
|   |-- gitbook/                # Full GitBook documentation
|   |-- SUBMISSION.md           # Hackathon submission document
|-- demo/
|   |-- sample_output.json      # Example trade idea
|   |-- sample_risk_check.json  # Example risk check
|   |-- sample_portfolio.json   # Example portfolio state
|-- website/
|   |-- index.html              # Landing page
|   |-- dashboard-pro.html      # 3-tab command center dashboard
|-- .github/
|   |-- workflows/
|       |-- ci.yml                 # CI/CD: planned (not yet active)
|-- .env.example
|-- pyproject.toml
|-- Dockerfile
|-- backtest_audit.py              # Synthetic data sanity check
|-- run_deep_backtest.py           # 500-run robustness sweep
|-- run_realdata_backtest.py       # Real-data backtest with benchmarks
|-- LICENSE
|-- README.md
```

---

## 安全與風險

RUNECLAW 以**失敗即關閉**的理念設計：

- **預設模擬。** 實盤交易需要兩個明確的環境旗標。
- **每筆交易通過 21 項檢查。** 16 道嚴格失敗即關閉、1 道失敗即放行（流動性）、4 項顧問／略過。細節見 `config/risk_manifest.yaml`。
- **斷路器。** 在每日虧損（5%）或最大回撤（10%）時自動停止。
- **人在迴圈中。** 未經明確確認，任何交易都不會執行。
- **確認時重新檢查。** 因市場狀況會改變，故在確認時重新評估風險。
- **完整稽核軌跡。** 每一個決策都以結構化 JSON 記錄以供審查。
- **無靜默失敗。** 未處理的錯誤會中止流程，絕不繼續進行。

> **本系統是為 Hackathon 展示與紙上交易而打造。
> 它不構成財務建議，在未經大量額外防護、測試與法規審查之前，
> 不應使用真實資金操作。**

---

## 技術棧

| 層 | 技術 |
|-------|-----------|
| 語言 | Python 3.11+ |
| 交易所 | Bitget，透過 [ccxt](https://github.com/ccxt/ccxt) |
| AI / LLM | 預設 Gemini 2.5 Flash（GPT-4o、Anthropic、Groq 可設定） |
| 技術分析 | NumPy + 自訂指標 |
| 資料模型 | Pydantic v2（嚴格驗證） |
| 機器人介面 | python-telegram-bot 20.x |
| 記錄 | 結構化 JSON (JSONL) |
| 設定 | python-dotenv + dataclass 預設值 |

---

## 安全性

- **絕不提交 `.env` 檔案。** `.env` 檔案含 API 金鑰與密鑰。它已列於 `.gitignore` 中。
- **定期輪換 API 金鑰。** 若你懷疑某金鑰已外洩，請立即在 Bitget 與 OpenAI 儀表板上撤銷。
- 在市場資料操作上**使用唯讀 API 金鑰**。僅在你明確打算實盤時才啟用交易權限（本原型不建議）。
- **Telegram 機器人 token** 授予對機器人的完整控制權。請保密。將 `TELEGRAM_CHAT_ID` 限制於你自己的 chat ID。
- **LLM API 成本：** 每次 `/analyze` 呼叫都會消耗 LLM token。預設為 Gemini 2.5 Flash（有免費方案）。GPT-4o 每次分析約耗費 $0.01-0.03。設定 `LLM_API_KEY=`（留空）以改用免費的規則式後備。
- **程式碼中不含密鑰。** 所有憑證皆從環境變數載入並附安全預設值。在完整歷史上執行 `gitleaks` 或 `trufflehog` 以驗證。

### 安全強化（稽核 v3.0）

| 修正 | 類別 | 說明 |
|-----|----------|-------------|
| C1 | 嚴重 | 以執行緒安全的 `RuntimeState` 包裝器取代對 frozen CONFIG 的 `object.__setattr__` |
| C3 | 嚴重 | 新增日誌遮蔽層 —— API 金鑰、密鑰、token 會自所有日誌輸出與 traceback 中剔除 |
| C5 | 嚴重 | 當設定 `MCP_AUTH_TOKEN` 時，MCP 伺服器需要 bearer token 驗證 |
| W1 | 警告 | CostTracker 現在於 UTC 邊界每日重設；另以 `snapshot_lifetime()` 提供累計統計 |
| W5 | 警告 | 快取鍵使用完整 64 字元 SHA-256 hex（原先截斷為 16） |
| W6 | 警告 | 前推回測在每個折疊後清理暫存目錄 |
| Input | 強化 | `/approve` 驗證數字 Telegram ID；`/analyze` 拒絕非英數字母標的 |
| Encapsulation | 強化 | 風險引擎使用 `portfolio.get_position_value()` 公開 API，而非私有的 `_last_prices` |
| AGPL | 合規 | `/start` 與 `/help` 含原始碼儲存庫連結與財務免責聲明 |
| Corruption | 強化 | 投組在狀態檔損毀時記錄 CRITICAL 警示，而非靜默後備 |

`tests/test_security.py` 中有 **29 項專屬安全測試**，涵蓋：日誌遮蔽、MCP 驗證、執行期狀態、快取鍵、成本重設、投組損毀、輸入驗證與注入防範。注意：安全稽核為 AI 輔助且為內部進行；尚未執行任何獨立第三方稽核。

---

## 實盤交易紀錄

RUNECLAW 正在 **Bitget 期貨上實盤交易**，採微型部位。所有交易皆透過 Telegram 機器人介面並經人工確認執行。

**交易期間：** 2026 年 6 月 17-19 日  
**交易所：** Bitget USDT-M 期貨  
**部位規模：** 每筆 $10-20（微型測試模式）  
**槓桿：** 5x  
**已平倉交易總數：** 38  
**勝率：** 55.3%（21 勝 / 17 負）  
**已實現損益總計：** +$46.30  

### `logs/` 中的檔案

| 檔案 | 說明 |
|------|-------------|
| `live_trading_log.csv` | 完整交易日誌，含時間戳、配對、方向、進場/出場價、規模、損益 |
| `closed_trades.json` | 來自機器人狀態檔的原始已平倉交易紀錄 |
| `audit_chain.jsonl` | 不可變稽核鏈 —— 每一個交易決策皆連同脈絡記錄 |

### 訂單執行功能

- **POST_ONLY 限價單** —— 保證僅以 maker 成交，若會穿越掛單簿則交易所拒絕
- **限價單價格驗證** —— 僅在限價會即時成交時才有條件地重新計算
- **價格漂移取消** —— 當市場偏離 >2% 時自動取消過時的待成交限價單
- **4 小時時間到期** —— 未成交限價單在可設定逾時後取消
- **交易所回報的損益** —— 使用 Bitget 實際的 `profit` 欄位，而非估算
- **交易去重** —— 防止對帳 + 手動平倉路徑造成的重複計算

---

## 限制與成熟度

這是一個 **Hackathon 原型**（成熟度：早期階段）。已知限制：

- **單人開發者專案** —— 除自動稽核外，同儕審查有限
- **實盤交易進行中** —— RUNECLAW 正在 Bitget 期貨上以微型部位實盤交易（每筆 $10-20、5x 槓桿）。完整交易紀錄見 `logs/`。
- **回測方法論注意事項** —— 回測使用合成 GBM+GARCH 價格資料並設 `use_llm=False`。這驗證了在隨機漫步上的風險閘門行為與部位規模，但**並未**驗證產生超額報酬的模組（聰明錢、訂單流、情緒融合、清算瀑布），這些需要真實的市場微結構資料。回測結果應解讀為**引擎健全性檢查**，而非獲利能力的證據。需要真實資料、啟用 LLM、樣本外的驗證才能評估策略績效。
- **API 延遲與滑價** —— 真實交易所狀況與模擬不同
- **已進行安全稽核** —— AI 輔助深度稽核 (v3.0)，所有 5 個嚴重問題均已修正，並新增 29 項安全測試。尚未執行任何獨立第三方稽核。
- **LLM 相依** —— AI 分析品質取決於模型可用性與成本
- **無保證正常運行時間** —— 無監控、警示或容錯移轉基礎設施
- **可擴展性：** 目前為單一實例 —— 蜂群使用實驗性的程序內 pub/sub（並非正式的 MCP 部署）
- **相關性防護** —— 目前實作為逐群組計數上限（每相關性群組最多 2 個部位），而非完整的成對相關矩陣。`MAX_CORRELATION` 設定旋鈕保留供未來實作。
- **匯流投票者** —— 擴展後的 30+ 投票者模型含許多源自相同價量序列的指標（RSI、MACD、OBV、VWAP、布林通道），它們在統計上並不獨立。素樸加總可能重複計算動能訊號。加權評分 —— 外加可選的家族上限去相關處理（`CONFLUENCE_FAMILY_CAP_ENABLED`）與習得的逐一投票者權重 —— 可緩解但無法消除此問題。

### 回測方法論

三個回測工具，各有不同用途：

| 腳本 | 資料來源 | LLM | 用途 |
|--------|-----------|-----|---------|
| `backtest_audit.py` | 合成 (GBM+GARCH) | 關閉 | 引擎健全性檢查 —— 在雜訊上的風險閘門行為 |
| `run_realdata_backtest.py` | **真實 Binance OHLCV** | 可設定 | 含買進並持有基準的策略驗證 |
| `run_deep_backtest.py` | 合成 (GBM+GARCH) | 關閉 | 500 次穩健性掃描（5 市況 x 20 標的 x 5 種子） |

合成回測**僅驗證風險引擎與規則式後備** —— 它們不運用 AI 或市場微結構模組。真實資料回測使用前推樣本外驗證（70/30 切分），是評估策略優勢的適當工具。

```bash
# 真實資料回測（不需 API 金鑰）：
python run_realdata_backtest.py

# 啟用 LLM：
python run_realdata_backtest.py --llm --output results.json
```

---

## 團隊

| 角色 | 姓名 |
|------|------|
| 首席開發者 | *P.Baur* |
| AI / 策略 | *Claude + MuleRun + RUNECLAW* |
| 風險 / 後端 | *職缺開放* |

---

## RUNECLAW 對比一般交易機器人

| 能力 | RUNECLAW | 一般 Hackathon 機器人 |
|------------|:--------:|:---------------------:|
| 交易前風險檢查 | **21 項檢查（16 嚴格 + 5 顧問）** | 0-3 項基本檢查 |
| 失敗即關閉設計 | **是** —— 任何失敗 = 拒絕 | 失敗即放行（錯誤略過檢查） |
| 斷路器 | 在每日虧損／回撤時**自動停止** | 無或僅手動 |
| 人工確認 | 透過 Telegram 鍵盤**強制要求** | 自動執行或無閘門 |
| 市況偵測 | **ADX-14 市況過濾**阻擋逆勢 | 不予考慮 |
| 匯流評分 | **30+ 投票者模型**（含可選的家族上限去相關） | 1-2 個指標 |
| 稽核軌跡 | **完整 JSONL** —— 每一決策皆記錄 | 極少或無 |
| 模擬優先 | **預設模式** —— 實盤需 2 個明確旗標 | 常為預設實盤 |
| 部位規模 | **固定分數**，含曝險上限 | 固定手數或餘額% |
| 確認時重新檢查 | **是** —— 市場可能已變動 | 無重新驗證 |
| 回測引擎 | **內建**，含手續費 + 滑價建模 | 外部或無 |
| 即時市場連線 | 在真實 Bitget 資料上**掃描 324+ 配對**（唯讀市場資料） | 僅模擬資料 |

> 安全與透明是一等的設計目標，而非事後補強。

---

## 與我們一同 Fork 並奪勝

RUNECLAW 開放協作。若你正為 Bitget AI Base Camp 開發並需要風險引擎、掃描器或分析流程 —— fork 它、擴展它，並提交你自己的參賽作品。

**如何貢獻：**

1. **Fork** 本儲存庫
2. 在其上**建構**你的策略模組、UI 或整合
3. **提交**至 Hackathon，並標明 RUNECLAW 作為你的風險／分析層
4. **開一個 PR** 回饋改進 —— 我們會合併出色的貢獻

### 擴充藍圖

| 擴充 | 說明 | 難度 |
|-----------|-------------|------------|
| **多交易所連接器** | 新增 OKX、Bybit、Binance 轉接器 —— 相同風險引擎，更多市場 | 中等 |
| **網頁儀表板** | 瀏覽器中的即時圖表、投組追蹤器、風險熱圖 | 中等 |
| **新分析策略** | 自訂指標組合、機器學習型態偵測、掛單簿失衡 | 易-難 |
| **更多語言** | 英文 + 繁體中文（繁體中文）今日已透過 `/lang` 出貨；可在同一 i18n 層上加入 ES/RU/AR 等 | 易 |
| **鏈上資料來源** | 整合巨鯨錢包追蹤、DEX 流向、來自鏈上來源的資金費率 | 中等 |
| **情緒來源** | Twitter/X 情緒、恐懼與貪婪指數、新聞 NLP 評分 | 中等 |
| **投組最佳化** | Kelly 準則部位規模、相關性感知配置、Markowitz 前緣 | 難 |
| **警示系統** | 市況變更、異常偵測、斷路器事件的推播通知 | 易 |
| **回測 UI** | 含權益曲線、交易標記、回撤圖表的視覺化回測結果 | 中等 |
| **多代理協調** | 擴展蜂群協定 —— 針對不同市況的專責代理 | 難 |

我們相信最好的 Hackathon 專案都建立於穩固的基礎之上。RUNECLAW 提供風險引擎與市場情報 —— 你帶來超額報酬 (alpha)。

```bash
# 60 秒內開始
git clone https://github.com/Humanoid-Traders/RUNECLAW.git
cd RUNECLAW && cp .env.example .env
pip install -r bot/requirements.txt
python -m bot.main --mode scan
```

> **想要共同提交？** 開一個標題為「Co-submission: [你的專案名稱]」的 issue，我們會協調。

---

## 授權

**AGPL-3.0** —— GNU Affero General Public License v3.0。詳見 [LICENSE](./LICENSE) 與 [NOTICE](./NOTICE)。

你可以自由檢視、研究、fork 並修改本程式碼。若你散布它，或將修改版本作為網路服務（SaaS、API、網頁應用）運行，你必須以相同授權釋出你的原始碼。商業授權洽詢：透過 [Telegram 社群](https://t.me/+VRNgsmkR5pszZTdk)聯絡 Humanoid Traders。

---

<p align="center"><b>RUNECLAW</b> —— 紀律重於預測。透明重於炒作。</p>
<p align="center"><i>為 Bitget AI Base Camp · Hackathon S1 打造</i></p>
