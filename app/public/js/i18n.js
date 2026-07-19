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
