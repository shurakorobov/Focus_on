// ===== Focus ON — API-клієнт =====
// Усі запити до бекенду проходять через цей модуль.
// Авторизація (у порядку пріоритету):
//   1. JWT з localStorage (після bot-code входу в браузері)
//   2. initData з Telegram WebApp SDK (Mini App)
//   3. ?tgWebAppInitData= (Telegram на iPad / зовнішній браузер)
//   4. #tgWebAppInitData= (Telegram iOS у деяких версіях)

const API = (() => {
  let cachedToken = "";
  const JWT_KEY = "focus_jwt";

  function getAuthToken() {
    if (cachedToken) return cachedToken;
    // 1) JWT з localStorage — після bot-code входу в браузері
    try {
      const jwt = localStorage.getItem(JWT_KEY);
      if (jwt && jwt.split(".").length === 3) {
        cachedToken = jwt;
        return cachedToken;
      }
    } catch (e) {}
    // 2) JS-об'єкт від Telegram WebApp SDK (Mini App — основний канал)
    if (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.initData) {
      cachedToken = window.Telegram.WebApp.initData;
      return cachedToken;
    }
    // 3) URL-параметр ?tgWebAppInitData=... (Telegram на деяких платформах)
    try {
      const qs = new URLSearchParams(window.location.search || "");
      const fromUrl = qs.get("tgWebAppInitData");
      if (fromUrl) { cachedToken = fromUrl; return cachedToken; }
    } catch (e) {}
    // 4) Хеш-фрагмент #tgWebAppInitData=... (Telegram iOS у деяких версіях)
    try {
      const h = new URLSearchParams((window.location.hash || "").replace(/^#/, ""));
      const fromHash = h.get("tgWebAppInitData");
      if (fromHash) { cachedToken = fromHash; return cachedToken; }
    } catch (e) {}
    return "";
  }

  /** Зберегти JWT після успішного bot-code входу (викликає app.js). */
  function setJwt(token) {
    cachedToken = token || "";
    try {
      if (token) localStorage.setItem(JWT_KEY, token);
      else localStorage.removeItem(JWT_KEY);
    } catch (e) {}
  }

  /** Прибрати збережений JWT (коли бекенд відхилив його 401). */
  function clearJwt() {
    cachedToken = "";
    try { localStorage.removeItem(JWT_KEY); } catch (e) {}
  }

  async function request(method, path, { json, body, params } = {}) {
    const headers = {};
    const opts = { method, headers };

    // Авторизація завжди через заголовок (надійніше за query-параметр —
    // initData довгий і може обрізатись проксі)
    const id = getAuthToken();
    if (id) headers["Authorization"] = "Bearer " + id;

    if (json !== undefined) {
      headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(json);
    } else if (body) {
      opts.body = body;
    }

    let url = path;
    if (params) {
      const usp = new URLSearchParams(params);
      url = path + (path.includes("?") ? "&" : "?") + usp.toString();
    }

    const res = await fetch(url, opts);
    let data = null;
    const ct = res.headers.get("content-type") || "";
    if (ct.includes("application/json")) {
      data = await res.json();
    } else {
      data = { ok: false, status: res.status };
    }
    if (!res.ok) {
      // 401 — збережений токен більше невалідний, скидаємо
      if (res.status === 401) clearJwt();
      const msg = (data && data.detail) || ("HTTP " + res.status);
      const err = new Error(msg);
      err.status = res.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  return {
    health: () => request("GET", "/api/health"),
    me: () => request("GET", "/api/me"),
    modes: () => request("GET", "/api/modes"),
    categories: () => request("GET", "/api/categories"),
    stats: () => request("GET", "/api/stats"),
    statsToday: () => request("GET", "/api/stats/today"),
    setDailyGoal: (seconds) =>
      request("PUT", "/api/daily-goal", { json: { seconds } }),
    statsPremium: (range = "month", category = "") =>
      request("GET", "/api/stats/premium", {
        params: { range, ...(category ? { category } : {}) },
      }),
    finishSession: (payload) =>
      request("POST", "/api/session/finish", { json: payload }),

    // маркетингова атрибуція + власна продуктова аналітика
    claimAttribution: (start_param) =>
      request("POST", "/api/attribution/claim", { json: { start_param } }),
    trackEvent: (event_name, params = {}) =>
      request("POST", "/api/events", { json: { event_name, params } }),

    // музика
    tracks: (category) =>
      request("GET", "/api/tracks", {
        params: category && category !== "all" ? { category } : {},
      }),
    addTrackUrl: (payload) =>
      request("POST", "/api/tracks/url", { json: payload }),
    uploadTrack: (formData) =>
      request("POST", "/api/tracks/upload", { body: formData }),
    deleteTrack: (id) => request("DELETE", "/api/tracks/" + id),
    renameTrack: (id, title) =>
      request("PATCH", "/api/tracks/" + id, { json: { title } }),
    toggleFavorite: (track_key) =>
      request("POST", "/api/tracks/favorite", { json: { track_key } }),
    togglePin: (track_key) =>
      request("POST", "/api/tracks/pin", { json: { track_key } }),

    // підписка
    subscribe: () => request("POST", "/api/subscribe"),
    // Google Play Billing: верифікація purchase_token → активація premium (Android)
    playVerify: (purchaseToken, productId = "focus_on_premium_month") =>
      request("POST", "/api/play/verify", {
        json: { purchase_token: purchaseToken, product_id: productId },
      }),
    grantPremium: (tg_id, days) =>
      request("POST", "/api/admin/grant-premium", { json: { tg_id, days } }),
    adminStats: () => request("GET", "/api/admin/stats"),
    // heartbeat: позначити себе онлайн (fire-and-forget)
    heartbeat: () => request("POST", "/api/heartbeat"),

    // вимірювання швидкості мережі (завантаження ~256КБ з сервера)
    measureSpeed: async () => {
      const start = performance.now();
      const res = await fetch("/api/network/payload?_=" + Date.now(), { cache: "no-store" });
      const buf = await res.arrayBuffer();
      const elapsed = (performance.now() - start) / 1000; // сек
      const bytes = buf.byteLength;
      const mbps = (bytes * 8) / 1e6 / elapsed; // мегабіт/с
      return { mbps: Math.round(mbps * 100) / 100, bytes, elapsed: Math.round(elapsed * 100) / 100 };
    },

    // баг-репорт
    reportBug: (message, platform = "", screen = "") =>
      request("POST", "/api/bug-report", { json: { message, platform, screen } }),

    // музика: статистика прослуховувань + фонове відтворення
    recordPlay: (track_key, title = "", source = "webview", duration = 0) =>
      request("POST", "/api/play", { json: { track_key, title, source, duration } }),
    playInBackground: (track_key, title = "", duration = 0) =>
      request("POST", "/api/play-in-background", { json: { track_key, title, duration } }),

    // промокоди
    redeem: (code) =>
      request("POST", "/api/redeem", { json: { code } }),
    listPromoCodes: () => request("GET", "/api/admin/promo-codes"),
    createPromoCode: (code, days, max_uses) =>
      request("POST", "/api/admin/promo-codes", { json: { code, days, max_uses } }),
    deletePromoCode: (code) =>
      request("DELETE", "/api/admin/promo-codes/" + encodeURIComponent(code)),

    // для діагностики та входу
    getAuthToken,
    setJwt,
    clearJwt,
    // зворотна сумісність (старі назви)
    getInitData: getAuthToken,
  };
})();
