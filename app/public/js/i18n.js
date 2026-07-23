/**
 * RUNECLAW web i18n — a tiny, dependency-free client localizer.
 *
 * Text is marked up in HTML with data attributes and swapped at runtime:
 *   <h2 data-i18n="hero.tagline">Intelligent · Adaptive · Relentless</h2>
 *   <input data-i18n-attr="placeholder:auth.email_ph;aria-label:auth.email">
 *   <h1 data-i18n-html="hero.h1">An AI engine you can <span>talk to.</span></h1>
 * The English text stays in the markup as the source-of-truth fallback, so an
 * un-keyed or un-translated string simply renders in English — never blank.
 *
 * Language resolution: saved choice (localStorage `rc_lang`) → the browser's
 * `navigator.language` → English. Choosing a language also writes the logged-in
 * user's `prefs.lang` so the AI chat replies in it (see docs/i18n_chat_language).
 *
 * Dual-mode: the pure helpers (normalize/resolveLang/translate) are exported
 * under Node for unit tests; in the browser the module self-initializes and
 * exposes `window.RCI18N`.
 */
(function (root) {
  'use strict';

  // Languages the web UI offers. `dir: 'rtl'` flips the document for Arabic.
  var LANGS = [
    { code: 'en', name: 'English' },
    { code: 'es', name: 'Español' },
    { code: 'zh', name: '繁體中文' },
    { code: 'pt', name: 'Português' },
    { code: 'fr', name: 'Français' },
    { code: 'ar', name: 'العربية', dir: 'rtl' },
  ];
  var RTL = { ar: true };

  // key -> { en, es, zh, pt, fr, ar }. English is also the in-markup fallback.
  var STRINGS = {
    'nav.dashboard': { en: 'Dashboard', es: 'Panel', zh: '儀表板', pt: 'Painel', fr: 'Tableau de bord', ar: 'لوحة التحكم' },
    'nav.track': { en: 'Track record', es: 'Historial', zh: '業績記錄', pt: 'Histórico', fr: 'Historique', ar: 'السجل' },
    'nav.agents': { en: 'Marketplace', es: 'Mercado', zh: '市場', pt: 'Mercado', fr: 'Place de marché', ar: 'السوق' },
    'nav.strengthmap': { en: 'Strength Map', es: 'Mapa de fuerza', zh: '強度地圖', pt: 'Mapa de força', fr: 'Carte de force', ar: 'خريطة القوة' },
    'nav.proof': { en: 'Proof of PnL', es: 'Prueba de PnL', zh: 'PnL 證明', pt: 'Prova de PnL', fr: 'Preuve de PnL', ar: 'إثبات الأرباح' },
    'nav.flight': { en: 'Flight Recorder', es: 'Registro de vuelo', zh: '飛行記錄儀', pt: 'Registro de voo', fr: 'Boîte noire', ar: 'مسجل الرحلة' },
    'nav.stress': { en: 'Stress Lab', es: 'Lab de estrés', zh: '壓力測試', pt: 'Lab de estresse', fr: 'Labo de stress', ar: 'مختبر الضغط' },
    'nav.leaderboard': { en: 'Leaderboard', es: 'Clasificación', zh: '排行榜', pt: 'Classificação', fr: 'Classement', ar: 'المتصدرون' },
    'nav.letter': { en: 'Agent Letter', es: 'Carta del agente', zh: '代理週報', pt: 'Carta do agente', fr: 'Lettre de l’agent', ar: 'رسالة الوكيل' },
    'nav.docs': { en: 'Docs', es: 'Docs', zh: '文件', pt: 'Docs', fr: 'Docs', ar: 'الوثائق' },
    'nav.get_started': { en: 'Get started', es: 'Empezar', zh: '開始使用', pt: 'Começar', fr: 'Commencer', ar: 'ابدأ' },

    'hero.eyebrow': { en: 'Live · Bitget USDT-M futures', es: 'En vivo · Futuros USDT-M de Bitget', zh: '實盤 · Bitget USDT-M 合約', pt: 'Ao vivo · Futuros USDT-M da Bitget', fr: 'En direct · Futures USDT-M Bitget', ar: 'مباشر · عقود USDT-M على Bitget' },
    'hero.h1': {
      en: 'An AI trading engine<br>you can <span>talk to.</span>',
      es: 'Un motor de trading con IA<br>con el que puedes <span>hablar.</span>',
      zh: '一個你可以<span>對話</span>的<br>AI 交易引擎。',
      pt: 'Um motor de trading com IA<br>com quem você pode <span>falar.</span>',
      fr: 'Un moteur de trading IA<br>à qui vous pouvez <span>parler.</span>',
      ar: 'محرك تداول بالذكاء الاصطناعي<br><span>يمكنك محادثته.</span>',
    },
    'hero.tagline': { en: 'Intelligent · Adaptive · Relentless', es: 'Inteligente · Adaptable · Incansable', zh: '智能 · 自適應 · 不懈', pt: 'Inteligente · Adaptável · Incansável', fr: 'Intelligent · Adaptatif · Tenace', ar: 'ذكي · متكيّف · دؤوب' },
    'hero.body': {
      en: 'RUNECLAW scans 800+ symbols around the clock, explains every decision, and refuses anything its risk engine doesn’t like. Create an account and paper-trade in your browser within a minute — chat with the engine, place risk-checked trades, and go live only when you choose to.',
      es: 'RUNECLAW analiza más de 800 símbolos las 24 horas, explica cada decisión y rechaza todo lo que su motor de riesgo no apruebe. Crea una cuenta y opera en simulación desde tu navegador en un minuto: chatea con el motor, coloca operaciones con control de riesgo y pasa a real solo cuando tú lo decidas.',
      zh: 'RUNECLAW 全天候掃描 800+ 個標的，解釋每一個決策，並拒絕任何風險引擎不認可的操作。一分鐘內即可註冊並在瀏覽器中進行模擬交易——與引擎對話、下達經風險檢查的交易，只有在你選擇時才切換至實盤。',
      pt: 'A RUNECLAW analisa mais de 800 símbolos 24 horas por dia, explica cada decisão e recusa tudo o que o seu motor de risco não aprovar. Crie uma conta e faça paper trading no navegador em um minuto: converse com o motor, faça operações com controle de risco e vá para o modo real só quando você decidir.',
      fr: 'RUNECLAW analyse plus de 800 actifs en continu, explique chaque décision et refuse tout ce que son moteur de risque désapprouve. Créez un compte et tradez en simulation dans votre navigateur en une minute : discutez avec le moteur, passez des ordres contrôlés par le risque et passez en réel uniquement quand vous le décidez.',
      ar: 'يفحص RUNECLAW أكثر من 800 رمز على مدار الساعة، ويشرح كل قرار، ويرفض أي شيء لا يوافق عليه محرك المخاطر. أنشئ حسابًا وتداول تجريبيًا في متصفحك خلال دقيقة — تحدّث مع المحرك، ونفّذ صفقات مضبوطة المخاطر، وانتقل إلى التداول الحقيقي فقط عندما تختار ذلك.',
    },
    'hero.cta_create': { en: 'Create free account', es: 'Crear cuenta gratis', zh: '建立免費帳戶', pt: 'Criar conta grátis', fr: 'Créer un compte gratuit', ar: 'أنشئ حسابًا مجانيًا' },
    'hero.cta_dashboard': { en: 'View the dashboard', es: 'Ver el panel', zh: '查看儀表板', pt: 'Ver o painel', fr: 'Voir le tableau de bord', ar: 'عرض لوحة التحكم' },
    'hero.free_note': { en: 'Free · paper-trade in your browser instantly · no exchange keys needed.', es: 'Gratis · opera en papel al instante en tu navegador · sin claves de exchange.', zh: '免費 · 立即在瀏覽器中模擬交易 · 無需交易所金鑰。', pt: 'Grátis · opere em conta demo no navegador na hora · sem chaves de corretora.', fr: 'Gratuit · trading papier instantané dans votre navigateur · sans clés d’exchange.', ar: 'مجانًا · تداول تجريبي فوري في متصفحك · دون مفاتيح منصة.' },

    'sec.mkt_h': { en: 'The Strategy Agent Marketplace', es: 'El mercado de agentes de estrategia', zh: '策略代理市場', pt: 'O mercado de agentes de estratégia', fr: 'La place de marché des agents de stratégie', ar: 'سوق وكلاء الاستراتيجية' },
    'sec.mkt_p': { en: 'Browse the engine’s strategy agents — each one a real preset with a verified, reproducible backtest. Follow one, copy its picks on paper, or reproduce its numbers yourself in the Lab.', es: 'Explora los agentes de estrategia del motor: cada uno es un preajuste real con un backtest verificado y reproducible. Sigue uno, copia sus selecciones en papel o reproduce sus cifras tú mismo en el Lab.', zh: '瀏覽引擎的策略代理——每一個都是帶有可驗證、可重現回測的真實預設。關注其一、以紙上交易複製其選擇，或在實驗室中親自重現其數據。', pt: 'Explore os agentes de estratégia do motor — cada um é uma predefinição real com um backtest verificado e reproduzível. Siga um, copie as escolhas dele em papel ou reproduza os números você mesmo no Lab.', fr: 'Parcourez les agents de stratégie du moteur — chacun est un préréglage réel avec un backtest vérifié et reproductible. Suivez-en un, copiez ses choix sur papier, ou reproduisez ses chiffres vous-même dans le Lab.', ar: 'تصفّح وكلاء الاستراتيجية للمحرك — كل واحد إعداد حقيقي مع اختبار خلفي موثّق وقابل لإعادة الإنتاج. تابع أحدها، وانسخ اختياراته على الورق، أو أعِد إنتاج أرقامه بنفسك في المختبر.' },
    'sec.mkt_loading': { en: 'Loading the agent catalogue…', es: 'Cargando el catálogo de agentes…', zh: '正在載入代理目錄…', pt: 'Carregando o catálogo de agentes…', fr: 'Chargement du catalogue d’agents…', ar: 'جارٍ تحميل كتالوج الوكلاء…' },
    'sec.mkt_cta': { en: 'Browse the full marketplace →', es: 'Explorar todo el mercado →', zh: '瀏覽完整市場 →', pt: 'Explorar todo o mercado →', fr: 'Parcourir toute la place de marché →', ar: 'تصفّح السوق كاملاً →' },
    'sec.ask_h': { en: 'Talk to it, right here', es: 'Habla con él, aquí mismo', zh: '就在這裡與它對話', pt: 'Fale com ele, aqui mesmo', fr: 'Parlez-lui, ici même', ar: 'تحدّث معه، هنا مباشرة' },
    'sec.ask_p': { en: 'This is the live analyst — the same one behind the Telegram bot. Ask it anything; no account needed.', es: 'Este es el analista en vivo, el mismo que está detrás del bot de Telegram. Pregúntale lo que quieras; no necesitas cuenta.', zh: '這是實時分析師——與 Telegram 機器人背後的是同一個。隨便問；無需帳戶。', pt: 'Este é o analista ao vivo — o mesmo por trás do bot do Telegram. Pergunte o que quiser; sem precisar de conta.', fr: 'Voici l’analyste en direct — le même que derrière le bot Telegram. Posez-lui n’importe quelle question ; aucun compte requis.', ar: 'هذا هو المحلّل المباشر — نفسه الذي يشغّل بوت تيليجرام. اسأله أي شيء؛ دون حاجة إلى حساب.' },
    'sec.live_h': { en: 'Watch the agent think — live', es: 'Observa al agente pensar — en vivo', zh: '即時觀看智能體思考', pt: 'Veja o agente pensar — ao vivo', fr: 'Regardez l’agent réfléchir — en direct', ar: 'شاهد الوكيل يفكّر — مباشرة' },
    'sec.live_p': { en: 'The real engine’s mind-stream: scans, trade theses, executions and stop moves, streamed straight from the bot. No mockups.', es: 'El flujo mental del motor real: análisis, tesis de operación, ejecuciones y movimientos de stop, transmitidos directamente desde el bot. Sin maquetas.', zh: '真實引擎的思緒流：掃描、交易論點、成交與停損調整，直接由機器人串流。絕無虛構。', pt: 'O fluxo de pensamento do motor real: varreduras, teses de operação, execuções e ajustes de stop, transmitidos direto do bot. Sem simulações.', fr: 'Le flux de pensée du vrai moteur : analyses, thèses de trade, exécutions et déplacements de stop, diffusés directement depuis le bot. Aucune maquette.', ar: 'تدفّق تفكير المحرك الحقيقي: عمليات المسح وأطروحات الصفقات والتنفيذ وتحريك أوامر الإيقاف، تُبثّ مباشرة من البوت. بلا نماذج وهمية.' },

    'auth.tab_create': { en: 'Create account', es: 'Crear cuenta', zh: '建立帳戶', pt: 'Criar conta', fr: 'Créer un compte', ar: 'إنشاء حساب' },
    'auth.tab_login': { en: 'Log in', es: 'Iniciar sesión', zh: '登入', pt: 'Entrar', fr: 'Se connecter', ar: 'تسجيل الدخول' },
    'auth.confirm_pass': { en: 'Confirm password', es: 'Confirmar contraseña', zh: '確認密碼', pt: 'Confirmar senha', fr: 'Confirmer le mot de passe', ar: 'تأكيد كلمة المرور' },
    'auth.confirm_ph': { en: 'Repeat password', es: 'Repite la contraseña', zh: '重複輸入密碼', pt: 'Repita a senha', fr: 'Répétez le mot de passe', ar: 'أعد إدخال كلمة المرور' },
    'auth.paper_note': { en: 'Paper trading works immediately — no Telegram, no exchange keys.', es: 'El trading en simulación funciona de inmediato: sin Telegram, sin claves de exchange.', zh: '模擬交易即刻可用——無需 Telegram，無需交易所金鑰。', pt: 'O paper trading funciona imediatamente — sem Telegram, sem chaves de exchange.', fr: 'Le trading en simulation fonctionne immédiatement — sans Telegram, sans clés d’exchange.', ar: 'يعمل التداول التجريبي فورًا — دون تيليجرام ودون مفاتيح منصّة.' },
    'auth.email': { en: 'Email', es: 'Correo', zh: '電子郵件', pt: 'E-mail', fr: 'E-mail', ar: 'البريد الإلكتروني' },
    'auth.password': { en: 'Password', es: 'Contraseña', zh: '密碼', pt: 'Senha', fr: 'Mot de passe', ar: 'كلمة المرور' },
    'auth.email_ph': { en: 'you@example.com', es: 'tu@ejemplo.com', zh: 'you@example.com', pt: 'voce@exemplo.com', fr: 'vous@exemple.com', ar: 'you@example.com' },
    'auth.pass_min_ph': { en: 'Min 10 characters', es: 'Mínimo 10 caracteres', zh: '至少 10 個字元', pt: 'Mínimo 10 caracteres', fr: 'Minimum 10 caractères', ar: '10 أحرف على الأقل' },
    'auth.pass_ph': { en: 'Your password', es: 'Tu contraseña', zh: '你的密碼', pt: 'Sua senha', fr: 'Votre mot de passe', ar: 'كلمة المرور الخاصة بك' },
    'auth.create': { en: 'Create free account', es: 'Crear cuenta gratis', zh: '建立免費帳戶', pt: 'Criar conta grátis', fr: 'Créer un compte gratuit', ar: 'أنشئ حسابًا مجانيًا' },

    'foot.risk': {
      en: 'Trading futures involves substantial risk of loss and is not suitable for every investor. RUNECLAW is a trading tool, not financial advice. Use withdrawal-disabled API keys and never risk more than you can afford to lose.',
      es: 'Operar con futuros conlleva un riesgo sustancial de pérdida y no es adecuado para todos los inversores. RUNECLAW es una herramienta de trading, no asesoramiento financiero. Usa claves API sin permiso de retiro y nunca arriesgues más de lo que puedas permitirte perder.',
      zh: '交易期貨涉及重大虧損風險，並非適合所有投資者。RUNECLAW 是交易工具，而非財務建議。請使用停用提款權限的 API 金鑰，切勿冒超出承受能力的風險。',
      pt: 'Operar futuros envolve risco substancial de perda e não é adequado para todos os investidores. A RUNECLAW é uma ferramenta de trading, não aconselhamento financeiro. Use chaves de API sem permissão de saque e nunca arrisque mais do que pode perder.',
      fr: 'Le trading de futures comporte un risque de perte substantiel et ne convient pas à tous les investisseurs. RUNECLAW est un outil de trading, pas un conseil financier. Utilisez des clés API sans retrait et ne risquez jamais plus que ce que vous pouvez vous permettre de perdre.',
      ar: 'ينطوي تداول العقود الآجلة على مخاطر خسارة كبيرة وقد لا يناسب كل مستثمر. RUNECLAW أداة تداول وليست نصيحة مالية. استخدم مفاتيح API مع تعطيل السحب ولا تخاطر أبدًا بأكثر مما يمكنك تحمّل خسارته.',
    },

    'lang.label': { en: 'Language', es: 'Idioma', zh: '語言', pt: 'Idioma', fr: 'Langue', ar: 'اللغة' },

    // Dashboard shell chrome (static in dashboard.html).
    'dash.skip': { en: 'Skip to content', es: 'Saltar al contenido', zh: '跳至內容', pt: 'Pular para o conteúdo', fr: 'Aller au contenu', ar: 'تخطَّ إلى المحتوى' },
    'dash.account': { en: 'Account', es: 'Cuenta', zh: '帳戶', pt: 'Conta', fr: 'Compte', ar: 'الحساب' },
    'dash.chat_ph': { en: 'Ask anything — or "buy SOL 71 sl 70 tp 76"', es: 'Pregunta lo que sea — o "buy SOL 71 sl 70 tp 76"', zh: '隨便問——或輸入「buy SOL 71 sl 70 tp 76」', pt: 'Pergunte qualquer coisa — ou "buy SOL 71 sl 70 tp 76"', fr: 'Demandez n’importe quoi — ou "buy SOL 71 sl 70 tp 76"', ar: 'اسأل أي شيء — أو اكتب "buy SOL 71 sl 70 tp 76"' },
    'dash.confirm_trade': { en: 'Confirm trade', es: 'Confirmar operación', zh: '確認交易', pt: 'Confirmar operação', fr: 'Confirmer l’ordre', ar: 'تأكيد الصفقة' },
    'dash.confirm': { en: 'Confirm', es: 'Confirmar', zh: '確認', pt: 'Confirmar', fr: 'Confirmer', ar: 'تأكيد' },
    'dash.cancel_order': { en: 'Cancel order', es: 'Cancelar orden', zh: '取消訂單', pt: 'Cancelar ordem', fr: 'Annuler l’ordre', ar: 'إلغاء الأمر' },

    // Dashboard nav labels (left rail + bottom tabbar), built in dashboard.js.
    'nav.home': { en: 'Home', es: 'Inicio', zh: '首頁', pt: 'Início', fr: 'Accueil', ar: 'الرئيسية' },
    'nav.chat': { en: 'AI Chat', es: 'Chat IA', zh: 'AI 聊天', pt: 'Chat IA', fr: 'Chat IA', ar: 'محادثة الذكاء' },
    'nav.hub': { en: 'Agent Hub', es: 'Centro del agente', zh: '智能體中心', pt: 'Central do agente', fr: 'Hub de l’agent', ar: 'مركز الوكيل' },
    'nav.markets': { en: 'Markets', es: 'Mercados', zh: '市場', pt: 'Mercados', fr: 'Marchés', ar: 'الأسواق' },
    'nav.macro': { en: 'Macro', es: 'Macro', zh: '總經', pt: 'Macro', fr: 'Macro', ar: 'الاقتصاد الكلي' },
    'nav.guardian': { en: 'Guardian', es: 'Guardián', zh: '守護者', pt: 'Guardião', fr: 'Gardien', ar: 'الحارس' },
    'nav.signals': { en: 'Signals', es: 'Señales', zh: '信號', pt: 'Sinais', fr: 'Signaux', ar: 'الإشارات' },
    'nav.deepscan': { en: 'Deep Scan', es: 'Escaneo profundo', zh: '深度掃描', pt: 'Varredura profunda', fr: 'Analyse approfondie', ar: 'فحص عميق' },
    'nav.feed': { en: 'Live Feed', es: 'Feed en vivo', zh: '即時動態', pt: 'Feed ao vivo', fr: 'Flux en direct', ar: 'البث المباشر' },
    'nav.trade': { en: 'Trade', es: 'Operar', zh: '交易', pt: 'Operar', fr: 'Trader', ar: 'تداول' },
    'nav.portfolio': { en: 'Portfolio', es: 'Cartera', zh: '投資組合', pt: 'Carteira', fr: 'Portefeuille', ar: 'المحفظة' },
    'nav.leaderboard': { en: 'Leaders', es: 'Líderes', zh: '排行榜', pt: 'Líderes', fr: 'Classement', ar: 'المتصدرون' },
    'nav.lab': { en: 'Lab', es: 'Laboratorio', zh: '實驗室', pt: 'Laboratório', fr: 'Labo', ar: 'المختبر' },
    'nav.engine': { en: 'Engine', es: 'Motor', zh: '引擎', pt: 'Motor', fr: 'Moteur', ar: 'المحرك' },
    'nav.account': { en: 'Account', es: 'Cuenta', zh: '帳戶', pt: 'Conta', fr: 'Compte', ar: 'الحساب' },

    // Dashboard view headers (title + subtitle), emitted centrally by viewHead.
    'vh.home.title': { en: 'Home', es: 'Inicio', zh: '首頁', pt: 'Início', fr: 'Accueil', ar: 'الرئيسية' },
    'vh.home.sub': { en: 'Your account at a glance', es: 'Tu cuenta de un vistazo', zh: '一覽你的帳戶', pt: 'Sua conta num relance', fr: 'Votre compte en un coup d’œil', ar: 'حسابك في لمحة' },
    'vh.markets.title': { en: 'Markets', es: 'Mercados', zh: '市場', pt: 'Mercados', fr: 'Marchés', ar: 'الأسواق' },
    'vh.markets.sub': { en: 'Live exchange data', es: 'Datos del exchange en vivo', zh: '即時交易所數據', pt: 'Dados da exchange ao vivo', fr: 'Données d’échange en direct', ar: 'بيانات المنصّة المباشرة' },
    'vh.signals.title': { en: 'Signals', es: 'Señales', zh: '信號', pt: 'Sinais', fr: 'Signaux', ar: 'الإشارات' },
    'vh.signals.sub': { en: 'Every setup the engine generates — taken or not', es: 'Cada oportunidad que genera el motor, tomada o no', zh: '引擎產生的每個交易機會——無論是否採用', pt: 'Cada setup que o motor gera, executado ou não', fr: 'Chaque configuration générée par le moteur, prise ou non', ar: 'كل فرصة يولّدها المحرك، سواء نُفّذت أم لا' },
    'vh.deepscan.title': { en: 'Deep Scan', es: 'Escaneo profundo', zh: '深度掃描', pt: 'Varredura profunda', fr: 'Analyse approfondie', ar: 'فحص عميق' },
    'vh.deepscan.sub': { en: 'The engine’s per-symbol pattern read — chart & candlestick', es: 'La lectura de patrones del motor por símbolo: gráfico y velas', zh: '引擎對每個標的的形態解讀——圖表與 K 線', pt: 'A leitura de padrões do motor por símbolo — gráfico e candles', fr: 'La lecture des motifs par symbole du moteur — graphique et chandeliers', ar: 'قراءة المحرك للأنماط لكل رمز — الرسم والشموع' },
    'vh.trade.title': { en: 'Trade', es: 'Operar', zh: '交易', pt: 'Operar', fr: 'Trader', ar: 'تداول' },
    'vh.trade.sub': { en: 'Manual trading through the engine’s risk gate', es: 'Operativa manual a través del filtro de riesgo del motor', zh: '透過引擎風險閘進行手動交易', pt: 'Operação manual pelo filtro de risco do motor', fr: 'Trading manuel via le filtre de risque du moteur', ar: 'تداول يدوي عبر بوابة مخاطر المحرك' },
    'vh.portfolio.title': { en: 'Portfolio', es: 'Cartera', zh: '投資組合', pt: 'Carteira', fr: 'Portefeuille', ar: 'المحفظة' },
    'vh.portfolio.sub': { en: 'Your equity, history, and journal', es: 'Tu capital, historial y diario', zh: '你的權益、歷史與交易日誌', pt: 'Seu patrimônio, histórico e diário', fr: 'Vos fonds, votre historique et votre journal', ar: 'رأس مالك وسجلّك ومذكّرتك' },
    'vh.engine.title': { en: 'Engine', es: 'Motor', zh: '引擎', pt: 'Motor', fr: 'Moteur', ar: 'المحرك' },
    'vh.engine.sub': { en: 'The autonomous RUNECLAW engine, live', es: 'El motor autónomo de RUNECLAW, en vivo', zh: '自主運行的 RUNECLAW 引擎，實時', pt: 'O motor autônomo da RUNECLAW, ao vivo', fr: 'Le moteur autonome RUNECLAW, en direct', ar: 'محرك RUNECLAW المستقل، مباشرةً' },
    'vh.account.title': { en: 'Account', es: 'Cuenta', zh: '帳戶', pt: 'Conta', fr: 'Compte', ar: 'الحساب' },
    'vh.account.sub': { en: 'Profile, connections, and live-trading controls', es: 'Perfil, conexiones y controles de operativa real', zh: '個人資料、連接與實盤交易控制', pt: 'Perfil, conexões e controles de trading real', fr: 'Profil, connexions et contrôles de trading réel', ar: 'الملف الشخصي والاتصالات وضوابط التداول الحقيقي' },
    'vh.leaderboard.title': { en: 'Leaderboard', es: 'Clasificación', zh: '排行榜', pt: 'Classificação', fr: 'Classement', ar: 'لوحة المتصدرين' },
    'vh.leaderboard.sub': { en: 'Opt-in ranks by return % — anonymous handles, no dollar amounts', es: 'Rankings voluntarios por % de rendimiento: alias anónimos, sin importes', zh: '自願參與、按報酬率排名——匿名代號，不顯示金額', pt: 'Ranking opcional por % de retorno — apelidos anônimos, sem valores', fr: 'Classements volontaires par % de rendement — pseudos anonymes, sans montants', ar: 'ترتيب اختياري حسب نسبة العائد — أسماء مستعارة، دون مبالغ' },
    'vh.lab.title': { en: 'Strategy Lab', es: 'Laboratorio de estrategias', zh: '策略實驗室', pt: 'Laboratório de estratégias', fr: 'Labo de stratégies', ar: 'مختبر الاستراتيجيات' },
    'vh.lab.sub': { en: 'Run the engine’s honest backtester on frozen benchmark data', es: 'Ejecuta el backtester honesto del motor sobre datos de referencia congelados', zh: '在凍結的基準數據上運行引擎的誠實回測器', pt: 'Rode o backtester honesto do motor em dados de referência congelados', fr: 'Lancez le backtester honnête du moteur sur des données de référence figées', ar: 'شغّل أداة الاختبار الخلفي الأمينة للمحرك على بيانات مرجعية مجمّدة' },
    'vh.hub.title': { en: 'Agent Hub', es: 'Centro del agente', zh: '智能體中心', pt: 'Central do agente', fr: 'Hub de l’agent', ar: 'مركز الوكيل' },
    'vh.hub.sub': { en: 'Everything your agent does — status at a glance, one tap to act', es: 'Todo lo que hace tu agente: estado de un vistazo, una pulsación para actuar', zh: '你的智能體所做的一切——狀態一目了然，一鍵操作', pt: 'Tudo o que seu agente faz — status num relance, um toque para agir', fr: 'Tout ce que fait votre agent — statut en un coup d’œil, une touche pour agir', ar: 'كل ما يفعله وكيلك — الحالة في لمحة، نقرة واحدة للتنفيذ' },
    'vh.feed.title': { en: 'Live Feed', es: 'Feed en vivo', zh: '即時動態', pt: 'Feed ao vivo', fr: 'Flux en direct', ar: 'البث المباشر' },

    // Home first-run welcome (rendered synchronously, so apply() catches it).
    'home.welcome_title': { en: 'Meet your agent', es: 'Conoce a tu agente', zh: '認識你的智能體', pt: 'Conheça seu agente', fr: 'Rencontrez votre agent', ar: 'تعرّف على وكيلك' },
    'home.welcome_body': { en: 'Welcome to RUNECLAW. From here on, an autonomous trading agent works this dashboard with you — it scans the market around the clock, explains every read, and only ever trades through a strict risk gate. Three good first moves:', es: 'Bienvenido a RUNECLAW. A partir de ahora, un agente de trading autónomo trabaja en este panel contigo: analiza el mercado sin descanso, explica cada lectura y solo opera a través de un estricto filtro de riesgo. Tres buenos primeros pasos:', zh: '歡迎使用 RUNECLAW。從現在起，一個自主交易智能體將與你一起使用此儀表板——它全天候掃描市場、解釋每一次判讀，並且只透過嚴格的風險閘進行交易。三個不錯的起手式：', pt: 'Bem-vindo à RUNECLAW. Daqui em diante, um agente de trading autônomo trabalha neste painel com você — varre o mercado o tempo todo, explica cada leitura e só opera por um filtro de risco rígido. Três bons primeiros passos:', fr: 'Bienvenue sur RUNECLAW. Désormais, un agent de trading autonome utilise ce tableau de bord avec vous — il analyse le marché en continu, explique chaque lecture et ne trade qu’à travers un filtre de risque strict. Trois bons premiers gestes :', ar: 'مرحبًا بك في RUNECLAW. من الآن فصاعدًا، يعمل وكيل تداول مستقل على هذه اللوحة معك — يفحص السوق على مدار الساعة، ويشرح كل قراءة، ولا يتداول إلا عبر بوابة مخاطر صارمة. ثلاث خطوات أولى جيدة:' },
    'home.welcome_1': { en: '💬 1 · Say hello to your agent', es: '💬 1 · Saluda a tu agente', zh: '💬 1 · 向你的智能體打招呼', pt: '💬 1 · Diga olá ao seu agente', fr: '💬 1 · Dites bonjour à votre agent', ar: '💬 1 · ألقِ التحية على وكيلك' },
    'home.welcome_2': { en: '📡 2 · Watch it read the market', es: '📡 2 · Observa cómo lee el mercado', zh: '📡 2 · 觀看它解讀市場', pt: '📡 2 · Veja-o ler o mercado', fr: '📡 2 · Regardez-le lire le marché', ar: '📡 2 · شاهده يقرأ السوق' },
    'home.welcome_3': { en: '🎯 3 · Place a risk-gated paper trade', es: '🎯 3 · Coloca una operación en papel con filtro de riesgo', zh: '🎯 3 · 下一筆經風險閘的模擬交易', pt: '🎯 3 · Faça uma operação em papel com filtro de risco', fr: '🎯 3 · Passez un trade papier filtré par le risque', ar: '🎯 3 · نفّذ صفقة تجريبية عبر بوابة المخاطر' },
    'home.welcome_dismiss': { en: 'Got it — don’t show again', es: 'Entendido, no mostrar de nuevo', zh: '知道了——不再顯示', pt: 'Entendi — não mostrar de novo', fr: 'Compris — ne plus afficher', ar: 'حسنًا — لا تُظهرها مرّة أخرى' },
    'vh.feed.sub': { en: 'The agent’s mind-stream — every scan, thesis, trade and alert, as it happens', es: 'El flujo mental del agente: cada análisis, tesis, operación y alerta, en tiempo real', zh: '智能體的思緒流——每次掃描、論點、交易與警報，實時呈現', pt: 'O fluxo de pensamento do agente — cada varredura, tese, operação e alerta, em tempo real', fr: 'Le flux de pensée de l’agent — chaque analyse, thèse, trade et alerte, en direct', ar: 'تدفّق تفكير الوكيل — كل مسح وأطروحة وصفقة وتنبيه، لحظيًا' },
  };

  function normalize(code) {
    if (!code) return '';
    return String(code).trim().toLowerCase().replace(/_/g, '-').split('-')[0];
  }
  function codes() { return LANGS.map(function (l) { return l.code; }); }
  function supported(code) { return codes().indexOf(normalize(code)) >= 0; }

  function resolveLang(stored, nav) {
    if (supported(stored)) return normalize(stored);
    if (supported(nav)) return normalize(nav);
    return 'en';
  }

  function translate(key, lang) {
    var e = STRINGS[key];
    if (!e) return null;
    if (e[lang] != null) return e[lang];
    return e.en != null ? e.en : null;
  }

  // ── Browser-only from here ────────────────────────────────────────────────
  var current = 'en';

  function setAttrs(el, lang) {
    // data-i18n-attr="placeholder:key;aria-label:key2"
    var spec = el.getAttribute('data-i18n-attr');
    spec.split(';').forEach(function (pair) {
      var i = pair.indexOf(':');
      if (i < 0) return;
      var attr = pair.slice(0, i).trim();
      var v = translate(pair.slice(i + 1).trim(), lang);
      if (v != null) el.setAttribute(attr, v);
    });
  }

  function apply(scope, lang) {
    if (lang == null) lang = current;      // default to the active language
    var doc = scope || document;
    doc.querySelectorAll('[data-i18n]').forEach(function (el) {
      var v = translate(el.getAttribute('data-i18n'), lang);
      if (v != null) el.textContent = v;
    });
    doc.querySelectorAll('[data-i18n-html]').forEach(function (el) {
      var v = translate(el.getAttribute('data-i18n-html'), lang);
      if (v != null) el.innerHTML = v;
    });
    doc.querySelectorAll('[data-i18n-attr]').forEach(function (el) {
      setAttrs(el, lang);
    });
  }

  function persistServer(lang) {
    // Best-effort: store the logged-in user's prefs.lang so AI chat localizes.
    try {
      var token = localStorage.getItem('token');
      if (!token) return;
      fetch('/api/profile', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + token },
        body: JSON.stringify({ prefs: { lang: lang } }),
      }).catch(function () {});
    } catch (e) { /* ignore */ }
  }

  function setLang(lang, opts) {
    opts = opts || {};
    current = supported(lang) ? normalize(lang) : 'en';
    try { localStorage.setItem('rc_lang', current); } catch (e) { /* ignore */ }
    var el = document.documentElement;
    if (el) {
      el.setAttribute('lang', current);
      el.setAttribute('dir', RTL[current] ? 'rtl' : 'ltr');
    }
    apply(document, current);
    var sel = document.getElementById('rc-lang-select');
    if (sel && sel.value !== current) sel.value = current;
    if (opts.persistServer !== false) persistServer(current);
    try { root.dispatchEvent(new CustomEvent('rc-lang', { detail: current })); } catch (e) { /* ignore */ }
  }

  function buildSwitcher() {
    if (document.getElementById('rc-lang-select')) return;
    var host = document.querySelector('[data-i18n-switcher]')
      || document.querySelector('nav.topbar .nav-links');
    if (!host) return;
    var sel = document.createElement('select');
    sel.id = 'rc-lang-select';
    sel.className = 'rc-lang-select';
    sel.setAttribute('aria-label', translate('lang.label', current) || 'Language');
    LANGS.forEach(function (l) {
      var o = document.createElement('option');
      o.value = l.code; o.textContent = l.name;
      if (l.code === current) o.selected = true;
      sel.appendChild(o);
    });
    sel.addEventListener('change', function () { setLang(sel.value); });
    // Place before the primary CTA when we can, so it reads as a nav control.
    var cta = host.querySelector('.btn--primary');
    if (cta) host.insertBefore(sel, cta); else host.appendChild(sel);
  }

  function init() {
    var stored = null;
    try { stored = localStorage.getItem('rc_lang'); } catch (e) { /* ignore */ }
    var nav = (typeof navigator !== 'undefined') ? navigator.language : '';
    current = resolveLang(stored, nav);
    var el = document.documentElement;
    if (el) {
      el.setAttribute('lang', current);
      el.setAttribute('dir', RTL[current] ? 'rtl' : 'ltr');
    }
    buildSwitcher();
    if (current !== 'en') apply(document, current);
  }

  var api = {
    LANGS: LANGS, STRINGS: STRINGS,
    normalize: normalize, supported: supported, resolveLang: resolveLang,
    translate: translate, apply: apply, setLang: setLang,
    getLang: function () { return current; }, init: init,
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (typeof document !== 'undefined') {
    root.RCI18N = api;
    if (document.readyState !== 'loading') init();
    else document.addEventListener('DOMContentLoaded', init);
  }
})(typeof window !== 'undefined' ? window : globalThis);
