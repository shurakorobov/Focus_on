// ===== Focus OS — головна логіка (Apple-style) =====

(() => {
  "use strict";

  const tg = window.Telegram && window.Telegram.WebApp;
  if (tg) {
    tg.ready();
    tg.expand();
    if (tg.setHeaderColor) tg.setHeaderColor("#000000");
    if (tg.setBackgroundColor) tg.setBackgroundColor("#000000");
  }

  // ---------- Стан ----------
  const MODES = {
    deep_work: { label: "Глибока робота", dur: 50 * 60, color: "#bf5af2" },
    focus: { label: "Фокус", dur: 25 * 60, color: "#30d158" },
    short: { label: "Коротка сесія", dur: 15 * 60, color: "#ff9f0a" },
    break: { label: "Перерва", dur: 5 * 60, color: "#ff453a" },
  };
  const CATEGORIES = {
    deep_work: { label: "DeepWork", emoji: "🧠", color: "#bf5af2" },
    creative: { label: "Креатив", emoji: "🎨", color: "#ff9f0a" },
    learning: { label: "Навчання", emoji: "📚", color: "#0a84ff" },
    reading: { label: "Читання", emoji: "📖", color: "#64d2ff" },
    training: { label: "Тренування", emoji: "💪", color: "#30d158" },
    other: { label: "Інше", emoji: "✨", color: "#8e8e93" },
  };
  const TITLES = { timer: "Focus", stats: "Статистика", music: "Музика", profile: "Профіль" };

  const state = {
    screen: "timer",
    mode: "focus",
    totalSeconds: MODES.focus.dur,
    remaining: MODES.focus.dur,
    running: false,
    startedAt: null,
    tickerId: null,
    isAdmin: false,
    uploadEnabled: false,
    playerPlaying: false,
    currentTrack: null,    // активний трек, що грає/на паузі
    isPlaying: false,      // чи активний трек зараз грає
    selectedTrack: null,   // трек, обраний для фокусу
    customMode: false,     // чи встановлено свій час вручну
    // нові поля
    category: "other",         // поточна категорія на таймері
    musicCategory: "all",      // фільтр категорій на екрані Музика
    addCategory: "other",      // категорія в модалці додавання (URL)
    uploadCategory: "other",   // категорія в модалці додавання (файл)
    isPremium: false,
    planExpiresAt: "",
    premiumPriceUah: 100,
    premiumRange: "month",     // діапазон на преміум-статистиці
    me: null,                  // кеш /api/me
  };

  // ---------- DOM ----------
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => document.querySelectorAll(s);

  // ---------- Утиліти ----------
  function fmt(sec) {
    sec = Math.max(0, Math.round(sec));
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
  }
  function human(sec) {
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    if (h > 0) return h + " год " + m + " хв";
    if (m > 0) return m + " хв";
    return sec + " с";
  }
  function escapeHtml(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function haptic(type) {
    if (!tg || !tg.HapticFeedback) return;
    try {
      if (type === "light") tg.HapticFeedback.impactOccurred("light");
      else if (type === "med") tg.HapticFeedback.impactOccurred("medium");
      else if (type === "success") tg.HapticFeedback.notificationOccurred("success");
      else if (type === "error") tg.HapticFeedback.notificationOccurred("error");
    } catch (e) {}
  }
  function platformInfo() {
    const ua = navigator.userAgent || "";
    const platform = (tg && tg.platform) || "unknown";
    const v = (tg && tg.version) || "?";
    return `${platform} · TG ${v} · ${ua.slice(0, 60)}`;
  }
  function fmtDate(iso) {
    if (!iso) return "";
    try {
      const d = new Date(iso);
      return d.toLocaleDateString("uk-UA", { day: "numeric", month: "long", year: "numeric" });
    } catch (e) { return iso; }
  }

  // ---------- Тост ----------
  let toastTimer = null;
  function toast(msg) {
    const el = $("#toast");
    el.textContent = msg;
    el.classList.remove("hidden");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.add("hidden"), 2200);
  }

  function setModeColor(color) {
    document.documentElement.style.setProperty("--mode-color", color);
  }

  // ---------- Кільце прогресу ----------
  const RING_LEN = 2 * Math.PI * 126; // r=126
  $("#ring-fg").style.strokeDasharray = RING_LEN;

  function renderTimer() {
    $("#time-display").textContent = fmt(state.remaining);
    const progress = state.remaining / state.totalSeconds;
    $("#ring-fg").style.strokeDashoffset = RING_LEN * (1 - progress);
  }

  // ---------- Чіпи категорій ----------
  function buildChips(container, current, onSelect, includeAll) {
    container.innerHTML = "";
    if (includeAll) {
      const c = document.createElement("button");
      c.className = "chip" + (current === "all" ? " active" : "");
      c.textContent = "Усі";
      c.addEventListener("click", () => onSelect("all"));
      container.appendChild(c);
    }
    Object.keys(CATEGORIES).forEach((key) => {
      const meta = CATEGORIES[key];
      const c = document.createElement("button");
      c.className = "chip" + (current === key ? " active" : "");
      c.textContent = meta.emoji + " " + meta.label;
      if (current === key) c.style.borderColor = meta.color;
      c.addEventListener("click", () => onSelect(key));
      container.appendChild(c);
    });
  }

  // ---------- Навігація ----------
  function showScreen(name) {
    state.screen = name;
    $$(".screen").forEach((s) => s.classList.remove("active"));
    $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.screen === name));
    $("#screen-" + name).classList.add("active");
    $("#page-title").textContent = TITLES[name] || "";
    // FAB лише на екрані таймера
    $(".fab").classList.toggle("hidden", name !== "timer");
    if (name === "stats") loadStats();
    if (name === "music") Music.load();
    if (name === "profile") loadProfile();
  }

  // ---------- Таймер ----------
  function selectMode(mode) {
    if (state.running) {
      toast("Зупини таймер, щоб змінити режим");
      return;
    }
    state.mode = mode;
    state.customMode = false;
    state.totalSeconds = MODES[mode].dur;
    state.remaining = MODES[mode].dur;
    setModeColor(MODES[mode].color);
    $$(".mode-btn").forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
    if (!state.running) $("#phase-label").textContent = "Тапни по часу, щоб змінити";
    haptic("light");
    renderTimer();
  }

  function setCustomTime(seconds) {
    if (state.running) return;
    if (seconds < 5) seconds = 5;
    state.customMode = true;
    state.mode = "focus"; // базовий режим для статистики
    state.totalSeconds = seconds;
    state.remaining = seconds;
    setModeColor(MODES["focus"].color);
    $$(".mode-btn").forEach((b) => b.classList.remove("active"));
    $("#phase-label").textContent = "Натисни, щоб почати";
    renderTimer();
  }

  function tick() {
    state.remaining -= 1;
    if (state.remaining <= 0) {
      state.remaining = 0;
      renderTimer();
      finish(true);
      return;
    }
    renderTimer();
  }

  const ICON_PLAY = '<path d="M8 5v14l11-7z"/>';
  const ICON_PAUSE = '<path d="M6 5h4v14H6zM14 5h4v14h-4z"/>';

  function setFabIcon(playing) {
    $("#fab-icon").innerHTML = playing ? ICON_PAUSE : ICON_PLAY;
  }

  function toggleTimer() {
    if (state.running) pause();
    else start();
  }

  function start() {
    state.running = true;
    state.startedAt = new Date().toISOString();
    state.tickerId = setInterval(tick, 1000);
    setFabIcon(true);
    $("#phase-label").textContent = "У фокусі…";
    haptic("light");
    // запускаємо музику, якщо обраний трек
    if (state.selectedTrack) {
      Music.playForTimer(state.selectedTrack);
    }
  }

  function pause() {
    state.running = false;
    clearInterval(state.tickerId);
    state.tickerId = null;
    setFabIcon(false);
    $("#phase-label").textContent = "На паузі";
    // ставимо музику на паузу
    Music.pauseAudio();
  }

  async function finish(completed) {
    clearInterval(state.tickerId);
    state.tickerId = null;
    state.running = false;
    setFabIcon(false);

    // зупиняємо музику
    Music.stopAudio(true);

    const elapsed = state.totalSeconds - state.remaining;
    const payload = {
      mode: state.mode,
      planned: state.totalSeconds,
      actual: completed ? state.totalSeconds : elapsed,
      completed: completed,
      started_at: state.startedAt || new Date().toISOString(),
      category: state.category,
    };

    if (completed) {
      $("#phase-label").textContent = "Готово! 🎉";
      toast("Сесію збережено");
      haptic("success");
      if (navigator.vibrate) navigator.vibrate([180, 90, 180]);
    } else {
      $("#phase-label").textContent = "Натисни, щоб почати";
    }

    state.remaining = state.totalSeconds;
    renderTimer();

    try {
      await API.finishSession(payload);
    } catch (e) {
      console.warn("Сесія не збережена:", e.message);
    }
  }

  // ---------- Профіль ----------
  async function loadProfile() {
    try {
      const data = await API.me();
      state.me = data;
      state.isPremium = !!data.is_premium;
      state.planExpiresAt = data.plan_expires_at || "";
      state.premiumPriceUah = data.premium_price_uah || 100;
      renderProfile(data);
    } catch (e) {
      $("#profile-head").innerHTML = '<div class="hint-inline">Не вдалося завантажити профіль</div>';
    }
  }

  function renderProfile(data) {
    const u = data.user || {};
    const p = data.profile || {};
    const name = [u.first_name, u.last_name].filter(Boolean).join(" ") || u.username || "Користувач";
    const uname = u.username ? "@" + u.username : "—";

    // шапка
    let head = '<div class="profile-head">';
    if (u.photo_url) {
      head += '<img class="profile-avatar" src="' + escapeHtml(u.photo_url) + '" alt="" />';
    } else {
      const initial = (u.first_name || "?").charAt(0).toUpperCase();
      head += '<div class="profile-avatar placeholder">' + escapeHtml(initial) + '</div>';
    }
    head += '<div class="profile-info"><div class="profile-name">' + escapeHtml(name) + '</div>';
    head += '<div class="profile-sub">' + escapeHtml(uname) + '</div></div></div>';
    $("#profile-head").innerHTML = head;

    // тариф
    let plan;
    if (data.is_premium) {
      const exp = data.plan_expires_at ? "до " + fmtDate(data.plan_expires_at) : "без обмежень";
      plan = '<div class="plan-badge premium">⭐ Premium</div><div class="plan-exp">Діє ' + exp + '</div>';
    } else {
      plan = '<div class="plan-badge free">Безкоштовний</div>';
      plan += '<div class="plan-exp">Преміум відкриває деталізовану статистику, графіки та безліміт треків</div>';
      plan += '<button class="primary-btn" id="btn-upgrade">Розблокувати — ' + state.premiumPriceUah + '₴/міс</button>';
    }
    $("#profile-plan").innerHTML = plan;

    // мережева інформація
    const net = data.network || {};
    const conn = (navigator.connection || navigator.mozConnection || navigator.webkitConnection) || {};
    const netRows = [
      ["IP", net.ip || "—"],
      ["Країна", net.country || "—"],
      ["Місто", net.city || "—"],
      ["Провайдер", net.isp || "—"],
      ["Тип зв'язку", conn.effectiveType ? conn.effectiveType.toUpperCase() : "—"],
    ];
    let netHtml = '<div class="net-grid">' +
      netRows.map(([k, v]) =>
        '<div class="net-row"><span class="net-k">' + escapeHtml(k) + '</span>' +
        '<span class="net-v">' + escapeHtml(String(v)) + '</span></div>'
      ).join("") +
      '<div class="net-row"><span class="net-k">Швидкість</span>' +
        '<span class="net-v" id="net-speed">вимірюю…</span></div>' +
      '</div>';
    $("#profile-network").innerHTML = netHtml;
    // асинхронно міряємо швидкість
    measureNetworkSpeed();

    // рекомендації
    const recs = data.recommendations || [];
    if (recs.length) {
      $("#profile-recommendations").innerHTML = recs.map((r) =>
        '<div class="rec-item">💡 ' + escapeHtml(r) + '</div>'
      ).join("");
    } else {
      $("#profile-recommendations").innerHTML = '<div class="hint-inline">Поки немає рекомендацій</div>';
    }

    // кнопка підписки
    const upBtn = $("#btn-upgrade");
    if (upBtn) upBtn.addEventListener("click", openSubscribe);
  }

  // вимірювання швидкості завантаження
  async function measureNetworkSpeed() {
    const el = $("#net-speed");
    if (!el) return;
    try {
      const res = await API.measureSpeed();
      if (el) {
        const label = res.mbps >= 1 ? res.mbps + " Мбіт/с" : Math.round(res.mbps * 1000) + " Кбіт/с";
        el.textContent = "↓ " + label;
      }
    } catch (e) {
      if (el) el.textContent = "недоступно";
    }
  }

  async function openSubscribe() {
    try {
      const res = await API.subscribe();
      if (!res.available) {
        toast(res.message || "Оплата тимчасово недоступна");
        return;
      }
      // відкриваємо сторінку оплати LiqPay
      if (tg && tg.openLink) {
        tg.openLink(res.checkout_url, { try_instant_view: true });
      } else {
        window.open(res.checkout_url, "_blank");
      }
      toast("Після оплати тариф оновиться автоматично");
    } catch (e) {
      toast("Не вдалося: " + e.message);
    }
  }

  // ---------- Статистика ----------
  let statsLoaded = false;
  async function loadStats() {
    let data;
    try {
      data = await API.stats();
    } catch (e) {
      $("#stats-by-mode").innerHTML = "";
      $("#stats-premium").innerHTML = "";
      return;
    }
    $("#stat-today").textContent = human(data.today_seconds);
    $("#stat-total").textContent = human(data.total_seconds);
    $("#stat-sessions").textContent = data.total_sessions;

    const modesMeta = data.modes || {};
    const byMode = $("#stats-by-mode");
    byMode.innerHTML = "";
    if (!data.by_mode.length) {
      byMode.innerHTML = '<div class="hint-inline">Ще немає завершених сесій</div>';
    } else {
      data.by_mode.forEach((row) => {
        const meta = modesMeta[row.mode] || { label: row.mode, color: "#0a84ff" };
        const d = document.createElement("div");
        d.className = "list-row";
        d.innerHTML =
          '<span><span class="dot" style="background:' + meta.color + '"></span>' +
          escapeHtml(meta.label) + "</span><span><b>" + row.c +
          "</b> · " + human(row.s) + "</span>";
        byMode.appendChild(d);
      });
    }

    const recent = $("#stats-recent");
    recent.innerHTML = "";
    if (!data.recent.length) {
      recent.innerHTML = '<div class="hint-inline">Історія порожня</div>';
    } else {
      const catsMeta = CATEGORIES;
      data.recent.forEach((row) => {
        const meta = modesMeta[row.mode] || { label: row.mode };
        const cat = catsMeta[row.category] || catsMeta.other;
        const d = document.createElement("div");
        d.className = "list-row";
        const ic = row.completed ? "✓" : "○";
        d.innerHTML =
          '<span>' + ic + " " + escapeHtml(meta.label) +
          (cat ? ' <span class="cat-tag">' + cat.emoji + "</span>" : "") +
          '</span><span class="r-time">' + human(row.actual) + "</span>";
        recent.appendChild(d);
      });
    }

    // преміум-блок
    renderPremiumBlock();
  }

  async function renderPremiumBlock() {
    const root = $("#stats-premium");
    root.innerHTML = "";

    if (!state.isPremium && state.me) {
      root.innerHTML = renderPaywall(state.premiumPriceUah);
      const b = root.querySelector("#paywall-btn");
      if (b) b.addEventListener("click", openSubscribe);
      return;
    }
    if (!state.me) {
      // ще не знаємо статус — пробуємо завантажити
      try { await loadProfile(); } catch (e) {}
    }

    if (!state.isPremium) {
      root.innerHTML = renderPaywall(state.premiumPriceUah);
      const b = root.querySelector("#paywall-btn");
      if (b) b.addEventListener("click", openSubscribe);
      return;
    }

    // премиум активний — показуємо деталі
    const wrap = document.createElement("div");
    wrap.className = "premium-unlocked";
    wrap.innerHTML =
      '<h2 class="group-title">Детальна статистика ⭐</h2>' +
      '<div class="premium-toolbar">' +
        '<div class="seg-group" id="range-segs">' +
          ["week", "month", "year", "all"].map((r) => {
            const labels = { week: "Тиждень", month: "Місяць", year: "Рік", all: "Усе" };
            return '<button class="seg-mini' + (state.premiumRange === r ? " active" : "") + '" data-range="' + r + '">' + labels[r] + '</button>';
          }).join("") +
        '</div>' +
      '</div>' +
      '<div id="premium-content"></div>';
    root.appendChild(wrap);

    wrap.querySelectorAll(".seg-mini").forEach((b) => {
      b.addEventListener("click", () => {
        state.premiumRange = b.dataset.range;
        wrap.querySelectorAll(".seg-mini").forEach((x) => x.classList.toggle("active", x === b));
        loadPremiumContent();
      });
    });

    await loadPremiumContent();
  }

  function renderPaywall(price) {
    const features = [
      "📊 Статистика за категоріями діяльності",
      "📈 Графіки прогресу за тиждень/місяць/рік",
      "🔥 Серії (streaks) та щоденні цілі",
      "🗂 Повна історія сесій (без обмеження)",
      "🎵 Безлімітні завантаження треків",
    ];
    return (
      '<div class="paywall">' +
        '<div class="paywall-lock">🔒</div>' +
        '<h3>Premium</h3>' +
        '<p class="paywall-sub">Детальна статистика та розширені можливості</p>' +
        '<ul class="paywall-list">' +
          features.map((f) => '<li>' + f + '</li>').join("") +
        '</ul>' +
        '<button class="primary-btn" id="paywall-btn">Розблокувати — ' + price + '₴/міс</button>' +
      '</div>'
    );
  }

  async function loadPremiumContent() {
    const el = $("#premium-content");
    if (!el) return;
    el.innerHTML = '<div class="hint-inline">Завантаження…</div>';
    try {
      const d = await API.statsPremium(state.premiumRange);
      let html = '';

      // серії
      html += '<div class="streak-cards">';
      html += '<div class="streak-card"><div class="stat-value">🔥 ' + d.current_streak + '</div><div class="stat-label">поточна серія</div></div>';
      html += '<div class="streak-card"><div class="stat-value">🏆 ' + d.best_streak + '</div><div class="stat-label">рекорд</div></div>';
      html += '<div class="streak-card"><div class="stat-value">' + human(d.total_seconds) + '</div><div class="stat-label">за період</div></div>';
      html += '</div>';

      // графік по днях
      if (d.by_day && d.by_day.length) {
        const maxSec = Math.max(...d.by_day.map((x) => x.s), 1);
        html += '<h2 class="group-title">Активність по днях</h2><div class="chart">';
        // показуємо останні ~14 барів (або всі якщо менше)
        const days = d.by_day.slice(-14);
        days.forEach((day) => {
          const pct = Math.round((day.s / maxSec) * 100);
          const dateLbl = day.d.slice(5).replace("-", "/");
          html += '<div class="chart-col"><div class="chart-bar" style="height:' + Math.max(pct, 3) + '%"></div><span class="chart-lbl">' + dateLbl + '</span></div>';
        });
        html += '</div>';
      }

      // за категоріями
      if (d.by_category && d.by_category.length) {
        const total = d.by_category.reduce((a, x) => a + x.s, 0) || 1;
        html += '<h2 class="group-title">За категоріями</h2><div class="list-group">';
        d.by_category.forEach((row) => {
          const meta = (d.categories && d.categories[row.category]) || { label: row.category, emoji: "", color: "#8e8e93" };
          const pct = Math.round((row.s / total) * 100);
          html += '<div class="list-row col">' +
            '<span class="cat-row"><span class="cat-emoji">' + meta.emoji + '</span>' +
            '<span class="cat-info"><span class="cat-name">' + escapeHtml(meta.label) + '</span>' +
            '<span class="cat-bar"><span class="cat-fill" style="width:' + pct + '%;background:' + meta.color + '"></span></span></span></span>' +
            '<span class="r-time">' + human(row.s) + ' · ' + pct + '%</span>' +
          '</div>';
        });
        html += '</div>';
      }

      el.innerHTML = html || '<div class="hint-inline">Немає даних за цей період</div>';
    } catch (e) {
      if (e.status === 403) {
        state.isPremium = false;
        renderPremiumBlock();
      } else {
        el.innerHTML = '<div class="hint-inline">Не вдалося завантажити: ' + escapeHtml(e.message) + '</div>';
      }
    }
  }

  // ---------- Музика ----------
  const Music = {
    data: { demo: [], tracks: [] },

    async load() {
      try {
        this.data = await API.tracks(state.musicCategory);
        state.isAdmin = !!this.data.is_admin;
        state.uploadEnabled = !!this.data.upload_enabled;
        state.isPremium = !!this.data.is_premium;
        this.render();
        this.applyPermissions();
      } catch (e) {
        console.error("Music.load помилка:", e);
        const msg = e.status === 401
          ? "Потрібна авторизація — відкрийте через бота"
          : "Не вдалося завантажити треки";
        $("#track-list").innerHTML = '<div class="hint-inline">' + msg + (e.message ? " (" + e.message + ")" : "") + "</div>";
      }
    },

    applyPermissions() {
      const admin = state.isAdmin;
      $("#scope-admin-label").classList.toggle("hidden", !admin);
      $("#scope-admin-label2").classList.toggle("hidden", !admin);
      $("#tab-upload").classList.toggle("hidden", !state.uploadEnabled);
    },

    render() {
      const root = $("#track-list");
      root.innerHTML = "";

      const all = [
        ...(this.data.demo || []).map((t) => ({ ...t, _group: "demo" })),
        ...(this.data.tracks || []).map((t) => ({ ...t, _group: t.scope })),
      ];

      // Закріплені → Бажане → за джерелом
      const pinned = all.filter((t) => t.is_pinned);
      const favorites = all.filter((t) => t.is_favorite && !t.is_pinned);
      const demo = all.filter((t) => t._group === "demo" && !t.is_pinned && !t.is_favorite);
      const admin = all.filter((t) => t._group === "admin" && !t.is_pinned && !t.is_favorite);
      const user = all.filter((t) => t._group === "user" && !t.is_pinned && !t.is_favorite);

      const groups = [
        { key: "pinned", title: "📌 Закріплені", items: pinned },
        { key: "fav", title: "❤️ Бажане", items: favorites },
        { key: "demo", title: "Демо", items: demo },
        { key: "admin", title: "Для всіх", items: admin },
        { key: "user", title: "Мої треки", items: user },
      ];

      let any = false;
      groups.forEach((g) => {
        if (!g.items.length) return;
        any = true;
        const head = document.createElement("div");
        head.className = "group-head";
        head.textContent = g.title;
        root.appendChild(head);

        const list = document.createElement("div");
        list.className = "list-group";
        g.items.forEach((t) => list.appendChild(this.row(t)));
        root.appendChild(list);
      });

      if (!any) {
        root.innerHTML = '<div class="hint-inline">Поки порочньо. Натисни «＋», щоб додати трек 🎵</div>';
      }

      // плаваюча кнопка «додати»
      if (!$("#add-track-fab")) {
        const fab = document.createElement("button");
        fab.id = "add-track-fab";
        fab.className = "add-fab";
        fab.innerHTML = '<svg viewBox="0 0 24 24" width="26" height="26"><path d="M11 5h2v6h6v2h-6v6h-2v-6H5v-2h6z" fill="currentColor"/></svg>';
        fab.addEventListener("click", () => Modal.open());
        $("#screen-music").appendChild(fab);
      }
    },

    row(t) {
      const isYt = t.kind === "youtube";
      const el = document.createElement("div");
      el.className = "track-item";
      el.dataset.id = t.id || "";
      el.dataset.group = t._group || "";
      el.dataset.key = t.track_key || "";

      // червона кнопка-фон під свайп (z-index:0, під inner)
      const delBg = document.createElement("div");
      delBg.className = "track-delete-bg";
      delBg.innerHTML =
        '<svg viewBox="0 0 24 24" width="22" height="22"><path d="M6 7h12M9 7V5h6v2m-7 0v12a1 1 0 001 1h6a1 1 0 001-1V7" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>' +
        '<span class="del-label">Видалити</span>';
      el.appendChild(delBg);

      const inner = document.createElement("div");
      inner.className = "track-item-inner";
      // НЕ затираємо cssText — позиціонування/z-index з CSS-класу!
      const catMeta = CATEGORIES[t.category] || CATEGORIES.other;
      const favCls = t.is_favorite ? " active" : "";
      const pinCls = t.is_pinned ? " active" : "";
      // іконки play/pause перемикаються CSS-класом .playing + .paused
      inner.innerHTML =
        '<div class="track-art ' + (isYt ? "yt" : "") + '" data-act="play">' +
          '<span class="ico-play"><svg viewBox="0 0 24 24" width="22" height="22"><path d="M8 5v14l11-7z" fill="currentColor"/></svg></span>' +
          '<span class="ico-pause"><svg viewBox="0 0 24 24" width="20" height="20"><path d="M6 5h4v14H6zM14 5h4v14h-4z" fill="currentColor"/></svg></span>' +
        '</div><div class="track-meta"><div class="track-title-row">' +
        '<span class="track-title">' + escapeHtml(t.title || "Без назви") + '</span>' +
        '<span class="track-cat-badge" title="' + escapeHtml(catMeta.label) + '">' + catMeta.emoji + '</span>' +
        '</div><div class="track-author">' +
        escapeHtml(t.author || (isYt ? "YouTube" : "")) +
        "</div></div>" +
        '<div class="track-actions">' +
          '<button class="track-act fav' + favCls + '" data-act="fav" aria-label="Бажане">' +
            '<svg class="ico-heart" viewBox="0 0 24 24" width="22" height="22"><path d="M12 21s-7.5-4.6-10-9.3C.6 8.4 2 5 5.2 5c2 0 3.3 1.1 4.1 2.3C10.5 5.9 12 5 13.9 5c3.2 0 4.6 3.4 3 6.7C19.5 16.4 12 21 12 21z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>' +
          '</button>' +
          '<button class="track-act pin' + pinCls + '" data-act="pin" aria-label="Закріпити">' +
            '<svg class="ico-pin" viewBox="0 0 24 24" width="20" height="20"><path d="M9 4h6l-1 5 3 3v2h-4v5l-1 1-1-1v-5H6v-2l3-3-1-5z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/></svg>' +
          '</button>' +
        '</div>';
      el.appendChild(inner);

      // зберігаємо посилання на назву для inline-редагування
      el._titleEl = inner.querySelector(".track-title");
      el._trackData = t;

      // оновити візуал під поточний стан відтворення
      this._applyPlayState(el, t);

      // ---- жести ----
      this.bindTrackGestures(el, t);
      return el;
    },

    // Візуально відобразити стан відтворення на елементі треку
    _applyPlayState(el, t) {
      const art = el.querySelector(".track-art");
      if (!art) return;
      const isActive = state.currentTrack && state.currentTrack.track_key === t.track_key;
      art.classList.toggle("playing", isActive);
      // пауза показує play-іконку навіть у активного треку
      art.classList.toggle("paused", isActive && !state.isPlaying);
    },

    // Оновити всі треки після зміни стану відтворення
    _refreshPlayStates() {
      $$(".track-item").forEach((el) => {
        const t = el._trackData;
        if (t) this._applyPlayState(el, t);
      });
    },

    // ---------- Жести для треку ----------
    bindTrackGestures(el, t) {
      let startX = 0, startY = 0;
      let currentX = 0;
      let dragging = false;
      let moved = false;
      let horizontal = null; // null | true | false
      let opened = false;     // чи відкрита кнопка видалення
      const SWIPE_OPEN = -88; // наскільки зсунути, щоб показати "видалити"

      function setX(x) {
        currentX = x;
        el.style.transform = "translateX(" + x + "px)";
      }
      function closeSwipe() {
        opened = false;
        el.style.transform = "";
      }

      el.addEventListener("touchstart", (e) => {
        if (el.querySelector(".track-title-input")) return; // не ламати редагування
        const touch = e.touches[0];
        startX = touch.clientX;
        startY = touch.clientY;
        dragging = true;
        moved = false;
        horizontal = null;
        el.style.transition = "none";
      }, { passive: true });

      el.addEventListener("touchmove", (e) => {
        if (!dragging) return;
        const touch = e.touches[0];
        const dx = touch.clientX - startX;
        const dy = touch.clientY - startY;

        if (Math.abs(dx) > 6 || Math.abs(dy) > 6) moved = true;
        if (horizontal === null) {
          if (Math.abs(dx) > 8 || Math.abs(dy) > 8) {
            horizontal = Math.abs(dx) > Math.abs(dy);
          }
        }
        if (horizontal === false) return; // вертикальний скрол — не чіпаємо

        let x = dx;
        if (opened) x = SWIPE_OPEN + dx; // якщо вже відкрито — від поточного
        // обмеження: вліво до SWIPE_OPEN, вправо до 0
        if (x > 0) x = x * 0.3; // пружинка вправо
        if (x < SWIPE_OPEN) x = SWIPE_OPEN + (x - SWIPE_OPEN) * 0.3;
        setX(x);
      }, { passive: true });

      el.addEventListener("touchend", (e) => {
        if (!dragging) return;
        dragging = false;
        el.style.transition = "";
        if (horizontal === false) {
          currentX = 0;
          return;
        }
        const x = currentX;
        // якщо зсунуто достатньо — відкрити/закрити
        if (x < SWIPE_OPEN / 2) {
          setX(SWIPE_OPEN);
          opened = true;
        } else {
          closeSwipe();
          opened = false;
        }
      });

      // тап: по арт-іконці — play/pause; по назві — потрійний тап = rename
      let tapCount = 0;
      let tapTimer = null;

      // окремі надійні обробники для кнопок дій (не покладаємось на bubbling)
      el.querySelectorAll(".track-act").forEach((btn) => {
        btn.addEventListener("click", (e) => {
          e.preventDefault();
          e.stopPropagation();
          if (moved) return; // ігнорувати клік після свайпу
          this.handleAction(btn.dataset.act, t, el);
        });
      });

      el.addEventListener("click", (e) => {
        if (moved) return; // клік після свайпу — ігноруємо
        // кнопки бажаного/закріпити вже оброблені окремо
        if (e.target.closest(".track-act")) return;

        if (currentX < -10) { closeSwipe(); opened = false; return; }
        if (el.querySelector(".track-title-input")) return;

        // тап по арт-іконці → play / pause
        const onArt = e.target.closest(".track-art");
        if (onArt) {
          this.togglePlay(t);
          return;
        }

        // потрійний тап по назві → перейменування
        const onTitle = e.target.closest(".track-title");
        if (onTitle && t.id) {
          tapCount++;
          clearTimeout(tapTimer);
          if (tapCount >= 3) {
            tapCount = 0;
            this.startInlineRename(el, t);
            return;
          }
          tapTimer = setTimeout(() => { tapCount = 0; }, 400);
        }
        // одинарний тап по строці (не арт, не 2/3 тап по назві) → прев'ю
        if (!onTitle || tapCount === 0) {
          this.togglePlay(t);
        }
      });

      // клік по відкритій кнопці видалення
      const delBtn = el.querySelector(".track-delete-bg");
      if (delBtn) {
        delBtn.addEventListener("click", (e) => {
          e.stopPropagation();
          this.deleteTrack(t, el);
        });
      }
    },

    // ---------- Бажане / Закріпити ----------
    async handleAction(act, t, el) {
      const key = t.track_key || ("db:" + t.id);
      try {
        if (act === "fav") {
          const res = await API.toggleFavorite(key);
          t.is_favorite = res.is_favorite;
          const btn = el.querySelector('.track-act.fav');
          if (btn) btn.classList.toggle("active", res.is_favorite);
          haptic("light");
          toast(res.is_favorite ? "♡ Додано в бажане" : "Прибрано з бажаного");
          // перегрупувати, щоб трек потрапив у групу «Бажане» / покинув її
          this.load();
        } else if (act === "pin") {
          const res = await API.togglePin(key);
          t.is_pinned = res.is_pinned;
          const btn = el.querySelector('.track-act.pin');
          if (btn) btn.classList.toggle("active", res.is_pinned);
          haptic("light");
          toast(res.is_pinned ? "📌 Закріплено" : "Відкріплено");
          this.load(); // перегрупувати
        }
      } catch (e) {
        toast("Не вдалося: " + e.message);
      }
    },

    // ---------- Inline-редагування назви ----------
    startInlineRename(el, t) {
      const oldTitle = el._titleEl.textContent;
      const input = document.createElement("input");
      input.className = "track-title-input";
      input.value = oldTitle === "Без назви" ? "" : oldTitle;
      input.maxLength = 100;
      el._titleEl.replaceWith(input);
      input.focus();
      input.select();

      let done = false;
      const finish = async (save) => {
        if (done) return;
        done = true;
        const val = input.value.trim();
        const newTitleEl = document.createElement("div");
        newTitleEl.className = "track-title";
        newTitleEl.textContent = val || "Без назви";
        input.replaceWith(newTitleEl);
        el._titleEl = newTitleEl;
        if (save && val && val !== oldTitle) {
          try {
            await API.renameTrack(t.id, val);
            toast("Назву оновлено");
            t.title = val;
          } catch (e) {
            toast("Не вдалося зберегти");
          }
        }
      };
      input.addEventListener("blur", () => finish(true));
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") { e.preventDefault(); input.blur(); }
        else if (e.key === "Escape") { finish(false); }
      });
    },

    // ---------- Видалення (після свайпа) ----------
    async deleteTrack(t, el) {
      try {
        await API.deleteTrack(t.id);
        // анімація зникнення
        el.style.transition = "transform 0.25s, opacity 0.25s, height 0.25s, padding 0.25s";
        el.style.transform = "translateX(-100%)";
        el.style.opacity = "0";
        setTimeout(() => { el.remove(); }, 250);
        toast("Трек видалено");
      } catch (e) {
        toast("Не вдалося видалити");
        el.style.transform = "";
      }
    },

    // ---------- Відтворення: єдиний стан currentTrack + isPlaying ----------

    // Головний перемикач по тапу: новий трек → play; активний → pause/resume
    togglePlay(t) {
      const isActive = state.currentTrack && state.currentTrack.track_key === t.track_key;
      if (isActive) {
        if (state.isPlaying) this.pause();
        else this.resume();
      } else {
        this.play(t);
      }
    },

    // Запуск нового треку
    play(t) {
      // цей трек тепер активний
      state.currentTrack = t;
      state.selectedTrack = t; // для зв'язку з таймером
      state.isPlaying = true;
      state.playerPlaying = true;

      if (t.kind === "youtube" && t.embed_url) {
        const c = $("#yt-container");
        c.classList.remove("hidden");
        // enablejsapi=1 + origin — дозволяють керувати play/pause через postMessage
        const ytSrc = t.embed_url + "&enablejsapi=1&origin=" + encodeURIComponent(location.origin);
        c.innerHTML = '<iframe width="1" height="1" src="' + ytSrc + '&autoplay=1" frameborder="0" allow="autoplay; encrypted-media"></iframe>';
      } else {
        const audio = $("#audio-el");
        audio.src = t.url;
        audio.play().catch((e) => console.warn("audio play:", e.message));
      }
      this._setupMediaSession(t);
      this._refreshPlayStates();
      haptic("light");
    },

    // Пауза активного треку
    pause() {
      const audio = $("#audio-el");
      if (audio.src) audio.pause();
      // YouTube iframe: шлємо postMessage для паузи
      this._ytCommand("pauseVideo");
      state.isPlaying = false;
      state.playerPlaying = false;
      this._refreshPlayStates();
      this._updateMediaPlaybackState("paused");
    },

    // Відновлення активного треку
    resume() {
      const audio = $("#audio-el");
      if (audio.src) audio.play().catch(() => {});
      // YouTube iframe: шлємо postMessage для відтворення
      this._ytCommand("playVideo");
      state.isPlaying = true;
      state.playerPlaying = true;
      this._refreshPlayStates();
      this._updateMediaPlaybackState("playing");
    },

    // Команда в YouTube iframe через postMessage API
    _ytCommand(cmd) {
      const iframe = $("#yt-container iframe");
      if (!iframe || !iframe.contentWindow) return;
      iframe.contentWindow.postMessage(
        JSON.stringify({ event: "command", func: cmd, args: [] }),
        "*"
      );
    },

    // Запуск треку при старті таймера
    playForTimer(t) {
      this.play(t);
    },

    pauseAudio() {
      this.pause();
    },

    // Повна зупинка (таймер закінчився)
    stopAudio(silent) {
      const audio = $("#audio-el");
      audio.pause();
      const c = $("#yt-container");
      c.innerHTML = "";
      c.classList.add("hidden");
      state.currentTrack = null;
      state.selectedTrack = null;
      state.isPlaying = false;
      state.playerPlaying = false;
      this._refreshPlayStates();
    },

    // ---------- Media Session API: музика у фоні ----------
    _setupMediaSession(t) {
      if (!("mediaSession" in navigator)) return;
      const title = t.title || "Без назви";
      const artist = t.author || (t.kind === "youtube" ? "YouTube" : "Focus OS");
      try {
        navigator.mediaSession.metadata = new MediaMetadata({
          title: title,
          artist: artist,
          album: "Focus OS",
          artwork: [
            { src: "/static/icon-192.png", sizes: "192x192", type: "image/png" },
          ],
        });
        navigator.mediaSession.setActionHandler("play", () => this.resume());
        navigator.mediaSession.setActionHandler("pause", () => this.pause());
        navigator.mediaSession.setActionHandler("stop", () => this.stopAudio(true));
      } catch (e) {}
      this._updateMediaPlaybackState("playing");
    },

    _updateMediaPlaybackState(stateStr) {
      if (!("mediaSession" in navigator) || !("setPositionState" in navigator.mediaSession)) return;
      try {
        navigator.mediaSession.setPositionState({
          duration: 0, // потік невідомої довжини
          playbackRate: 1,
          position: 0,
        });
      } catch (e) {}
    },
  };

  // ---------- Модал додавання ----------
  const Modal = {
    open() {
      // чистимо поля
      $("#track-url").value = "";
      $("#track-title").value = "";
      $("#track-author").value = "";
      $("#file-name").textContent = "";
      $("#upload-status").classList.add("hidden");
      state.addCategory = state.category; // підставляємо поточну категорію
      state.uploadCategory = state.category;
      renderAddChips();
      $("#add-modal").classList.remove("hidden");
    },
    close() {
      $("#add-modal").classList.add("hidden");
    },
  };

  function onAddCategory(k) {
    state.addCategory = k;
    buildChips($("#add-category-chips"), k, onAddCategory);
  }
  function onUploadCategory(k) {
    state.uploadCategory = k;
    buildChips($("#upload-category-chips"), k, onUploadCategory);
  }
  function renderAddChips() {
    buildChips($("#add-category-chips"), state.addCategory, onAddCategory);
    buildChips($("#upload-category-chips"), state.uploadCategory, onUploadCategory);
  }

  function setupModals() {
    // таби
    $$(".seg").forEach((seg) => {
      seg.addEventListener("click", () => {
        const name = seg.dataset.tab;
        $$(".seg").forEach((s) => s.classList.toggle("active", s === seg));
        $$(".pane").forEach((p) => p.classList.toggle("active", p.id === "pane-" + name));
      });
    });

    $("#btn-close-modal").addEventListener("click", Modal.close);

    // файл
    $("#track-file").addEventListener("change", (e) => {
      const f = e.target.files[0];
      $("#file-name").textContent = f ? f.name : "";
    });

    // зберегти URL
    $("#btn-save-url").addEventListener("click", async () => {
      const url = $("#track-url").value.trim();
      if (!url) { toast("Введи посилання"); return; }
      const title = $("#track-title").value.trim();
      const author = $("#track-author").value.trim();
      const scope = document.querySelector('input[name="scope"]:checked').value;
      const btn = $("#btn-save-url");
      btn.disabled = true;
      btn.textContent = "Додаю…";
      try {
        const res = await API.addTrackUrl({ url, title, author, scope, category: state.addCategory });
        Modal.close();
        toast("Додано: " + (res.title || "трек"));
        await Music.load();
      } catch (e) {
        toast("Помилка: " + e.message);
      } finally {
        btn.disabled = false;
        btn.textContent = "Додати";
      }
    });

    // завантажити
    $("#btn-upload").addEventListener("click", async () => {
      const fi = $("#track-file");
      if (!fi.files.length) { toast("Обери файл"); return; }
      const title = $("#upload-title").value.trim();
      const author = $("#upload-author").value.trim();
      const scope = document.querySelector('input[name="scope2"]:checked').value;
      const fd = new FormData();
      fd.append("file", fi.files[0]);
      fd.append("title", title);
      fd.append("author", author);
      fd.append("scope", scope);
      fd.append("category", state.uploadCategory);

      const status = $("#upload-status");
      const btn = $("#btn-upload");
      status.className = "upload-status";
      status.classList.remove("hidden");
      status.textContent = "Завантаження…";
      btn.disabled = true;
      try {
        await API.uploadTrack(fd);
        status.className = "upload-status ok";
        status.textContent = "✓ Готово!";
        await Music.load();
        setTimeout(() => { Modal.close(); status.classList.add("hidden"); }, 700);
      } catch (e) {
        status.className = "upload-status err";
        status.textContent = "✕ " + e.message;
      } finally {
        btn.disabled = false;
      }
    });

    // баг-репорт
    $("#btn-bug-cancel").addEventListener("click", () => $("#bug-modal").classList.add("hidden"));
    $("#btn-bug-send").addEventListener("click", async () => {
      const msg = $("#bug-message").value.trim();
      if (!msg) { toast("Опиши проблему"); return; }
      const btn = $("#btn-bug-send");
      btn.disabled = true;
      btn.textContent = "Надсилаю…";
      try {
        await API.reportBug(msg, platformInfo(), state.screen);
        $("#bug-modal").classList.add("hidden");
        $("#bug-message").value = "";
        toast("Дякуємо! Звіт надіслано");
        haptic("success");
      } catch (e) {
        toast("Не вдалося: " + e.message);
      } finally {
        btn.disabled = false;
        btn.textContent = "Надіслати";
      }
    });

    // закриття модалів по тапу на фон
    $$(".modal").forEach((m) => {
      m.addEventListener("click", (e) => {
        if (e.target === m) m.classList.add("hidden");
      });
    });
  }

  // ---------- Події ----------
  function bindEvents() {
    $$(".mode-btn").forEach((b) => b.addEventListener("click", () => selectMode(b.dataset.mode)));
    $("#start-btn").addEventListener("click", toggleTimer);

    $$(".tab").forEach((t) => t.addEventListener("click", () => showScreen(t.dataset.screen)));

    // тап по таймеру — відкрити редактор часу (якщо таймер не запущений)
    $("#time-display").addEventListener("click", () => {
      if (!state.running) openTimePicker();
    });
    $("#timer-wrap").addEventListener("click", (e) => {
      // клік по кільцю/фону таймера теж відкриває пікер
      if (!state.running && !e.target.closest(".modes") && !e.target.closest(".category-block") && !e.target.closest(".custom-time-btn")) {
        openTimePicker();
      }
    });
    // явна кнопка «Свій час»
    const ctBtn = $("#btn-custom-time");
    if (ctBtn) ctBtn.addEventListener("click", () => {
      if (!state.running) openTimePicker();
    });

    // кнопка "повідомити про баг"
    $("#btn-report-bug").addEventListener("click", () => {
      $("#bug-modal").classList.remove("hidden");
      $("#bug-message").focus();
    });
    // підтримка — посилання на адміна
    $("#btn-support").addEventListener("click", () => {
      const url = "https://t.me/focuson_on_bot";
      if (tg && tg.openTelegramLink) tg.openTelegramLink(url);
      else window.open(url, "_blank");
    });

    // чіпи категорій на таймері
    function onTimerCategory(k) {
      if (state.running) { toast("Зупини таймер, щоб змінити категорію"); return; }
      state.category = k;
      buildChips($("#timer-category-chips"), k, onTimerCategory);
      haptic("light");
    }
    buildChips($("#timer-category-chips"), state.category, onTimerCategory);

    // чіпи фільтру музики
    function onMusicCategory(k) {
      state.musicCategory = k;
      buildChips($("#music-category-chips"), k, onMusicCategory, true);
      Music.load();
    }
    buildChips($("#music-category-chips"), state.musicCategory, onMusicCategory, true);
  }

  // ---------- Тайм-пікер ----------
  function openTimePicker() {
    const h = Math.floor(state.totalSeconds / 3600);
    const m = Math.floor((state.totalSeconds % 3600) / 60);
    const s = state.totalSeconds % 60;
    $("#pick-h").value = h;
    $("#pick-m").value = m;
    $("#pick-s").value = s;
    $("#time-modal").classList.remove("hidden");
  }

  function setupTimePicker() {
    $("#btn-time-cancel").addEventListener("click", () => $("#time-modal").classList.add("hidden"));
    $("#btn-time-save").addEventListener("click", () => {
      const h = parseInt($("#pick-h").value) || 0;
      const m = parseInt($("#pick-m").value) || 0;
      const s = parseInt($("#pick-s").value) || 0;
      const total = h * 3600 + m * 60 + s;
      if (total >= 5) {
        setCustomTime(total);
        $("#time-modal").classList.add("hidden");
      } else {
        toast("Мінімум 5 секунд");
      }
    });

    // стрілки +/- год/хв/сек
    $$(".picker-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const dir = btn.dataset.dir;
        const delta = parseInt(btn.dataset.delta);
        const input = $("#pick-" + dir);
        let val = parseInt(input.value) || 0;
        val += delta;
        if (dir === "m" || dir === "s") {
          if (val < 0) val = 59;
          if (val > 59) val = 0;
        } else {
          if (val < 0) val = 23;
          if (val > 23) val = 0;
        }
        input.value = val;
      });
    });

    // пресети
    $$(".preset-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const sec = parseInt(btn.dataset.sec);
        setCustomTime(sec);
        $("#time-modal").classList.add("hidden");
      });
    });
  }

  // ---------- Аудіо-елемент: реакція на завершення/помилки ----------
  function setupAudioEvents() {
    const audio = $("#audio-el");
    // трек закінчився — скинути активний стан
    audio.addEventListener("ended", () => {
      Music.stopAudio(true);
    });
    audio.addEventListener("error", () => {
      if (state.currentTrack) {
        state.isPlaying = false;
        state.playerPlaying = false;
        Music._refreshPlayStates();
      }
    });
    // синхронізуємо стан паузи/програвання з реальним аудіо
    audio.addEventListener("play", () => {
      state.isPlaying = true;
      state.playerPlaying = true;
      Music._refreshPlayStates();
    });
    audio.addEventListener("pause", () => {
      // pause може виникати і від нас — оновлюємо лише якщо це не повна зупинка
      if (state.currentTrack) {
        state.isPlaying = false;
        state.playerPlaying = false;
        Music._refreshPlayStates();
      }
    });
  }

  // ---------- Старт ----------
  async function init() {
    bindEvents();
    setupModals();
    setupTimePicker();
    setupAudioEvents();
    selectMode("focus");
    setFabIcon(false);
    renderTimer();
    await loadProfile();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
