// ===== Focus ON — головна логіка =====

(() => {
  "use strict";

  const tg = window.Telegram && window.Telegram.WebApp;

  // ---------- Тема (світла/темна) ----------
  function getStoredTheme() {
    try { return localStorage.getItem("focus-theme") || "dark"; } catch (e) { return "dark"; }
  }
  function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    const bg = theme === "light" ? "#f5f0fa" : "#07030f";
    const bottomBar = theme === "light" ? "#f5f0fa" : "#160c2a";
    if (tg) {
      if (tg.setHeaderColor) tg.setHeaderColor(bg);
      if (tg.setBackgroundColor) tg.setBackgroundColor(bg);
      if (tg.setBottomBarColor) tg.setBottomBarColor(bottomBar);
    }
    const ico = document.querySelector("#btn-theme-toggle .action-ico");
    if (ico) ico.textContent = theme === "light" ? "☀️" : "🌙";
  }
  function toggleTheme() {
    const cur = getStoredTheme();
    const next = cur === "dark" ? "light" : "dark";
    try { localStorage.setItem("focus-theme", next); } catch (e) {}
    applyTheme(next);
    haptic("light");
  }

  if (tg) {
    tg.ready();
    tg.expand();
  }
  // тема до ініціалізації решти
  applyTheme(getStoredTheme());

  // ---------- Стан ----------
  const MODES = {
    deep_work: { label: "Глибока робота", dur: 50 * 60, color: "#bf5af2" },
    focus: { label: "Фокус", dur: 25 * 60, color: "#5ac8fa" },
    short: { label: "Коротка сесія", dur: 15 * 60, color: "#ffd60a" },
    break: { label: "Перерва", dur: 5 * 60, color: "#64d2ff" },
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
    premiumPriceStars: 100,
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
      else if (type === "rigid") tg.HapticFeedback.impactOccurred("rigid");
      else if (type === "heavy") tg.HapticFeedback.impactOccurred("heavy");
      else if (type === "soft") tg.HapticFeedback.impactOccurred("soft");
      else if (type === "success") tg.HapticFeedback.notificationOccurred("success");
      else if (type === "error") tg.HapticFeedback.notificationOccurred("error");
    } catch (e) {}
  }

  // ---------- Звуко-тактильний фідбек (як нативний iOS picker) ----------
  // Web Audio синтезує короткий «тік» на кожній цифрі (без зовнішніх файлів).
  // AudioContext створюється ліниво і вимагає user-gesture (WebApp це має).
  let _audioCtx = null;
  function audioCtx() {
    if (_audioCtx) return _audioCtx;
    try {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (AC) _audioCtx = new AC();
    } catch (e) {}
    return _audioCtx;
  }
  // короткий «клік»: вузький імпульс ~8мс — звук прокрутки нативного пікера
  function playTick() {
    const ctx = audioCtx();
    if (!ctx) return;
    try {
      if (ctx.state === "suspended") ctx.resume();
      const t = ctx.currentTime;
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "square";
      osc.frequency.setValueAtTime(1800, t);     // високий, різкий тон
      osc.frequency.exponentialRampToValueAtTime(600, t + 0.008);
      gain.gain.setValueAtTime(0.0001, t);
      gain.gain.exponentialRampToValueAtTime(0.18, t + 0.001); // різка атака
      gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.03); // швидке затухання
      osc.connect(gain).connect(ctx.destination);
      osc.start(t);
      osc.stop(t + 0.04);
    } catch (e) {}
  }
  // комбінований фідбек: різкий haptic (rigid) + «тік» — як на iOS picker
  function tickFeedback() {
    haptic("rigid");
    playTick();
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
    // FAB прибрано — стан відображає phase-label та прогрес кільця
    // функція залишається як no-op, щоб не ламати виклики
  }

  function toggleTimer() {
    if (state.running) pause();
    else start();
  }

  // STOP: довге утримання на таймері — повне скидання лічильника
  function stop() {
    if (state.tickerId) { clearInterval(state.tickerId); state.tickerId = null; }
    state.running = false;
    state.remaining = state.totalSeconds;
    Music.stopAudio(true);
    setFabIcon(false);
    $("#phase-label").textContent = "Скинуто · тап щоб почати";
    renderTimer();
    haptic("med");
    toast("Таймер скинуто");
    // зберігаємо часткову сесію якщо щось відпрацювало
  }

  function start() {
    state.running = true;
    state.startedAt = new Date().toISOString();
    state.tickerId = setInterval(tick, 1000);
    setFabIcon(true);
    $("#phase-label").textContent = "У фокусі · тап для паузи";
    haptic("light");
    // якщо трек не обраний — обираємо перший доступний (закріплений → бажаний → демо)
    if (!state.selectedTrack) {
      state.selectedTrack = Music.pickDefaultTrack();
    }
    // запускаємо музику
    if (state.selectedTrack) {
      Music.playForTimer(state.selectedTrack);
    }
  }

  function pause() {
    state.running = false;
    clearInterval(state.tickerId);
    state.tickerId = null;
    setFabIcon(false);
    $("#phase-label").textContent = "Пауза · тап для продовження";
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
      state.isAdmin = !!data.is_admin;
      state.planExpiresAt = data.plan_expires_at || "";
      state.premiumPriceStars = data.premium_price_stars || 100;
      // версія з сервера
      const ver = data.version || "0.1.0-beta";
      const footer = document.getElementById("profile-footer");
      if (footer) footer.textContent = "Focus ON · " + ver;
      renderProfile(data);
      // адмін-дашборд
      if (data.is_admin) {
        $("#admin-dashboard").classList.remove("hidden");
        loadAdminStats();
        loadPromoCodes();
        // SSE real-time stream
        startAdminSSE();
      } else {
        $("#admin-dashboard").classList.add("hidden");
        stopAdminSSE();
      }
    } catch (e) {
      $("#profile-head").innerHTML = '<div class="hint-inline">Не вдалося завантажити профіль</div>';
    }
  }

  // ---------- Адмін: промокоди ----------
  async function loadPromoCodes() {
    const el = $("#promo-list");
    if (!el) return;
    try {
      const d = await API.listPromoCodes();
      const codes = d.codes || [];
      if (!codes.length) {
        el.innerHTML = '<div class="hint-inline">Поки немає промокодів</div>';
        return;
      }
      el.innerHTML = codes.map((c) =>
        '<div class="promo-row">' +
          '<span class="promo-code-val">' + escapeHtml(c.code) + '</span>' +
          '<span class="promo-info">' + c.days + 'д · ' + c.used_count + (c.max_uses > 0 ? '/' + c.max_uses : '') + '</span>' +
          '<button class="promo-del" data-code="' + escapeHtml(c.code) + '">✕</button>' +
        '</div>'
      ).join("");
      // кнопки видалення
      el.querySelectorAll(".promo-del").forEach((btn) => {
        btn.addEventListener("click", async () => {
          const code = btn.dataset.code;
          btn.disabled = true;
          try {
            await API.deletePromoCode(code);
            toast("Код видалено");
            loadPromoCodes();
          } catch (e) { toast("Не вдалось"); }
        });
      });
    } catch (e) {
      el.innerHTML = '<div class="hint-inline">Не вдалось завантажити</div>';
    }
  }

  // ---------- Адмін-статистика ----------
  // ---------- SSE real-time admin stream ----------
  let _adminSSE = null;
  let onlineNow = 0;  // кеш останнього онлайн-каунту з SSE-події

  function startAdminSSE() {
    stopAdminSSE();
    const initData = API.getInitData();
    if (!initData) return;
    // SSE через EventSource з auth у query (EventSource не підтримує заголовки)
    const url = "/api/admin/stats/stream?init_data=" + encodeURIComponent(initData);
    try {
      _adminSSE = new EventSource(url);
      // безіменні події (data:) = повна статистика
      _adminSSE.onmessage = (event) => {
        try {
          const d = JSON.parse(event.data);
          if (d && d.users) renderAdminStatsData(d);
        } catch (e) {}
      };
      // іменована подія "online" = легкий realtime-пуш кожні 5с
      _adminSSE.addEventListener("online", (event) => {
        try {
          const d = JSON.parse(event.data);
          renderOnlineUsers(d);
        } catch (e) {}
      });
      _adminSSE.onerror = () => {
        // SSE може падати при засинанні — перепідключення через 5с
        if (_adminSSE) { _adminSSE.close(); _adminSSE = null; }
        setTimeout(() => { if (state.isAdmin) startAdminSSE(); }, 5000);
      };
    } catch (e) {
      console.warn("SSE не підтримується:", e.message);
    }
  }

  // ---------- Онлайн-користувачі (realtime) ----------
  function renderOnlineUsers(data) {
    const el = $("#admin-online");
    onlineNow = data.count || 0;
    if (!el) return;
    const count = onlineNow;
    const users = data.users || [];
    const pulse = count > 0 ? '<span class="online-dot"></span>' : '<span class="online-dot idle"></span>';
    let html = '<div class="online-head">' + pulse +
      '<span class="online-count">' + count + '</span> онлайн' +
      '<span class="online-sub">зараз у застосунку</span></div>';
    if (users.length) {
      html += '<div class="online-list">';
      users.slice(0, 12).forEach((u) => {
        const name = u.name || ("ID:" + u.tg_id);
        const star = u.plan === "premium" ? " ⭐" : "";
        let link;
        if (u.username) {
          link = '<a href="https://t.me/' + escapeHtml(u.username) + '" class="user-link">' + escapeHtml(name) + '</a>';
        } else {
          link = '<a href="tg://user?id=' + u.tg_id + '" class="user-link">' + escapeHtml(name) + '</a>';
        }
        html += '<div class="online-user">' +
          '<span class="online-dot mini"></span>' + link + star + '</div>';
      });
      if (users.length > 12) html += '<div class="online-more">ще ' + (users.length - 12) + '</div>';
      html += '</div>';
    }
    el.innerHTML = html;
  }

  function stopAdminSSE() {
    if (_adminSSE) { _adminSSE.close(); _adminSSE = null; }
  }

  async function loadAdminStats() {
    const el = $("#admin-stats");
    if (!el) return;
    try {
      const d = await API.adminStats();
      renderAdminStatsData(d);
    } catch (e) {
      el.innerHTML = '<div class="hint-inline">Не вдалося завантажити статистику</div>';
    }
  }

  function renderAdminStatsData(d) {
    const el = $("#admin-stats");
    if (!el) return;
    const u = d.users || {};
    const s = d.sessions || {};
    const t = d.tracks || {};
    const p = d.payments || {};
    const catsMeta = d.categories || {};
    const modesMeta = d.modes || {};

      let html = '<div class="admin-cards">';
      const online = (d.online && d.online.count != null) ? d.online.count : onlineNow;
      html += adminCard("🟢", online, "онлайн");
      html += adminCard("👥", u.total, "користувачів");
      html += adminCard("⭐", u.premium, "преміум");
      html += adminCard("📊", s.total, "сесій");
      html += adminCard("🔥", u.dau || 0, "DAU");
      html += adminCard("📅", u.wau || 0, "WAU");
      html += '</div>';

      html += '<div class="net-row"><span class="net-k">Активних за місяць (MAU)</span><span class="net-v">' + (u.mau || 0) + '</span></div>';
      html += '<div class="net-row"><span class="net-k">Завершених сесій</span><span class="net-k">' + s.completed + '</span></div>';
      html += '<div class="net-row"><span class="net-k">Сесій сьогодні</span><span class="net-v">' + s.today + '</span></div>';
      html += '<div class="net-row"><span class="net-k">Загальний фокус</span><span class="net-v">' + human(u.total_focus_seconds) + '</span></div>';
      html += '<div class="net-row"><span class="net-k">Доход (UAH)</span><span class="net-v">' + p.revenue_uah + ' ₴</span></div>';
      html += '<div class="net-row"><span class="net-k">Платежів</span><span class="net-v">' + p.count + '</span></div>';
      html += '<div class="net-row"><span class="net-k">Баг-репортів</span><span class="net-v">' + d.bug_reports + '</span></div>';
      html += '<div class="net-row"><span class="net-k">Обраних (♥)</span><span class="net-v">' + t.favorites + '</span></div>';
      html += '<div class="net-row"><span class="net-k">Закріплених (📌)</span><span class="net-v">' + t.pinned + '</span></div>';

      // графік по днях
      if (d.by_day && d.by_day.length) {
        const maxC = Math.max(...d.by_day.map((x) => x.c), 1);
        html += '<h2 class="group-title">Активність (7 днів)</h2><div class="chart">';
        d.by_day.forEach((day) => {
          const pct = Math.round((day.c / maxC) * 100);
          const lbl = day.d.slice(5).replace("-", "/");
          html += '<div class="chart-col"><div class="chart-bar" style="height:' + Math.max(pct, 4) + '%"></div><span class="chart-lbl">' + lbl + '</span></div>';
        });
        html += '</div>';
      }

      // за категоріями
      if (d.by_category && d.by_category.length) {
        html += '<h2 class="group-title">За категоріями</h2><div class="list-group">';
        d.by_category.forEach((r) => {
          const meta = catsMeta[r.category] || { label: r.category, emoji: "" };
          html += '<div class="list-row"><span>' + meta.emoji + ' ' + escapeHtml(meta.label) + '</span><span class="r-time">' + r.c + ' сес · ' + human(r.s) + '</span></div>';
        });
        html += '</div>';
      }

      // топ користувачі з лінками на ТГ профіль
      if (d.top_users && d.top_users.length) {
        html += '<h2 class="group-title">Топ користувачів</h2><div class="list-group">';
        d.top_users.forEach((u, i) => {
          const name = u.first_name || u.username || ("ID:" + u.tg_id);
          const star = u.plan === "premium" ? " ⭐" : "";
          // лінк на профіль: якщо є @username → t.me/username, інакше tg://user?id=
          let profileLink;
          if (u.username) {
            profileLink = '<a href="https://t.me/' + escapeHtml(u.username) + '" class="user-link">' + escapeHtml(name) + '</a>';
          } else {
            profileLink = '<a href="tg://user?id=' + u.tg_id + '" class="user-link">' + escapeHtml(name) + '</a>';
          }
          html += '<div class="list-row"><span>' + (i + 1) + ". " + profileLink + star + '</span><span class="r-time">' + human(u.total_focus_seconds) + '</span></div>';
        });
        html += '</div>';
      }

      el.innerHTML = html;
  }

  function adminCard(emoji, value, label) {
    return '<div class="streak-card"><div class="stat-value">' + emoji + ' ' + value + '</div><div class="stat-label">' + label + '</div></div>';
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
      plan += '<button class="primary-btn" id="btn-upgrade">Розблокувати — ' + state.premiumPriceStars + '⭐/міс</button>';
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
    // рендер блоку кешу музики
    renderCacheBlock();

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

  // рендер блоку кешу музики в профілі
  async function renderCacheBlock() {
    const el = $("#profile-cache");
    if (!el) return;
    try {
      const count = await AudioCache.count();
      const bytes = await AudioCache.size();
      const sizeLabel = humanBytes(bytes);
      let nav = navigator.storage && navigator.storage.estimate ? await navigator.storage.estimate() : {};
      const quota = nav.quota || 0;
      const usage = nav.usage || bytes;
      const pct = quota ? Math.min(100, Math.round((usage / quota) * 100)) : 0;

      el.innerHTML =
        '<div class="net-row"><span class="net-k">Кешовані треки</span>' +
          '<span class="net-v">' + count + '</span></div>' +
        '<div class="net-row"><span class="net-k">Розмір кешу</span>' +
          '<span class="net-v">' + sizeLabel + '</span></div>' +
        (quota ? '<div class="net-row"><span class="net-k">Зайнято пам’яті</span>' +
          '<span class="net-v">' + pct + '%</span></div>' : '') +
        '<div class="cache-actions">' +
          (count > 0
            ? '<button class="cache-btn danger" id="btn-cache-clear">Очистити кеш</button>'
            : '<div class="hint-inline">Кеш порожній. Аудіотреки кешуватимуться автоматично при відтворенні — тоді музика зможе грати у фоні та офлайн.</div>') +
        '</div>';

      const clr = $("#btn-cache-clear");
      if (clr) clr.addEventListener("click", async () => {
        clr.disabled = true;
        clr.textContent = "Чищу…";
        await AudioCache.clear();
        toast("Кеш очищено");
        renderCacheBlock();
      });
    } catch (e) {
      el.innerHTML = '<div class="hint-inline">Кеш недоступний у цьому браузері</div>';
    }
  }

  function humanBytes(b) {
    if (!b) return "0 Б";
    if (b < 1024) return b + " Б";
    if (b < 1024 * 1024) return (b / 1024).toFixed(1) + " КБ";
    return (b / (1024 * 1024)).toFixed(1) + " МБ";
  }

  async function openSubscribe() {
    try {
      const res = await API.subscribe();
      if (!res.available) {
        toast(res.message || "Оплата тимчасово недоступна");
        return;
      }
      // відкриваємо нативний інвойс Telegram Stars
      if (tg && tg.openInvoice) {
        tg.openInvoice(res.invoice_link, (status) => {
          if (status === "paid") {
            toast("⭐ Преміум активовано!");
            haptic("success");
            loadProfile(); // оновити тариф одразу
          } else if (status === "cancelled") {
            toast("Оплату скасовано");
          } else if (status === "failed") {
            toast("Оплата не вдалась");
          }
          // pending — нічого не робимо
        });
      } else {
        toast("Оплата доступна лише в застосунку Telegram");
      }
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
      root.innerHTML = renderPaywall(state.premiumPriceStars);
      const b = root.querySelector("#paywall-btn");
      if (b) b.addEventListener("click", openSubscribe);
      return;
    }
    if (!state.me) {
      // ще не знаємо статус — пробуємо завантажити
      try { await loadProfile(); } catch (e) {}
    }

    if (!state.isPremium) {
      root.innerHTML = renderPaywall(state.premiumPriceStars);
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
        '<button class="primary-btn" id="paywall-btn">Розблокувати — ' + price + '⭐/міс</button>' +
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
      // не-адмін: ховаємо весь scope-row (треки додаються в "Мої треки" = scope=user)
      // адмін: показуємо "Для всіх" + "Демо"
      const scopeRows = document.querySelectorAll(".scope-row");
      scopeRows.forEach((row) => {
        if (admin) {
          row.classList.remove("hidden");
        } else {
          row.classList.add("hidden");
        }
      });
      $("#scope-admin-label").classList.toggle("hidden", !admin);
      $("#scope-demo-label").classList.toggle("hidden", !admin);
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

      // Закріплені → Бажане → Збірка (демо+адмін разом) → Мої треки
      const pinned = all.filter((t) => t.is_pinned);
      const favorites = all.filter((t) => t.is_favorite && !t.is_pinned);
      // Збірка = усі адмінські + демо треки (контент від адміна/системи)
      const collection = all.filter(
        (t) => (t._group === "demo" || t._group === "admin") && !t.is_pinned && !t.is_favorite
      );
      const user = all.filter((t) => t._group === "user" && !t.is_pinned && !t.is_favorite);

      const groups = [
        { key: "pinned", title: "📌 Закріплені", items: pinned },
        { key: "fav", title: "❤️ Бажане", items: favorites },
        { key: "collection", title: "🎵 Демо", items: collection },
        { key: "user", title: "🎤 Мої треки", items: user },
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
        root.innerHTML = '<div class="hint-inline">Поки порожньо. Натисни «＋», щоб додати трек 🎵</div>';
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
      const isDemo = t._group === "demo";
      // демо-треки може видаляти лише адмін
      const canDelete = !isDemo || state.isAdmin;
      const el = document.createElement("div");
      el.className = "track-item";
      el.dataset.id = t.id || "";
      el.dataset.group = t._group || "";
      el.dataset.key = t.track_key || "";

      // червона кнопка-фон під свайп — лише для треків, які можна видаляти
      if (canDelete) {
        const delBg = document.createElement("div");
        delBg.className = "track-delete-bg";
        delBg.innerHTML =
          '<svg viewBox="0 0 24 24" width="22" height="22"><path d="M6 7h12M9 7V5h6v2m-7 0v12a1 1 0 001 1h6a1 1 0 001-1V7" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>' +
          '<span class="del-label">Видалити</span>';
        el.appendChild(delBg);
      }

      const inner = document.createElement("div");
      inner.className = "track-item-inner";
      // НЕ затираємо cssText — позиціонування/z-index з CSS-класу!
      const catMeta = CATEGORIES[t.category] || CATEGORIES.other;
      const favCls = t.is_favorite ? " active" : "";
      const pinCls = t.is_pinned ? " active" : "";
      // CSS-клас арт-іконки за типом джерела
      const artCls = isYt ? " yt" : (t.kind === "soundcloud" ? " sc" : (t.kind === "spotify" ? " sp" : (t.kind === "apple_music" ? " am" : "")));
      const sourceLabel = isYt ? "YouTube" : (t.kind === "soundcloud" ? "SoundCloud" : (t.kind === "spotify" ? "Spotify" : (t.kind === "apple_music" ? "Apple Music" : "")));
      // мітка 🛠 для адміна на admin/demo треках (inline управління)
      const isAdminTrack = (t._group === "demo" || t._group === "admin") && state.isAdmin;
      const adminBadge = isAdminTrack ? '<span class="admin-track-badge" title="Керує адмін">🛠</span>' : "";
      // іконки play/pause перемикаються CSS-класом .playing + .paused
      inner.innerHTML =
        '<div class="track-art' + artCls + '" data-act="play">' +
          '<span class="ico-play"><svg viewBox="0 0 24 24" width="22" height="22"><path d="M8 5v14l11-7z" fill="currentColor"/></svg></span>' +
          '<span class="ico-pause"><svg viewBox="0 0 24 24" width="20" height="20"><path d="M6 5h4v14H6zM14 5h4v14h-4z" fill="currentColor"/></svg></span>' +
        '</div><div class="track-meta"><div class="track-title-row">' +
        '<span class="track-title">' + escapeHtml(t.title || "Без назви") + '</span>' +
        adminBadge +
        '<span class="track-cat-badge" title="' + escapeHtml(catMeta.label) + '">' + catMeta.emoji + '</span>' +
        '</div><div class="track-author">' +
        escapeHtml(t.author || sourceLabel) +
        "</div></div>" +
        '<div class="track-actions">' +
          '<button class="track-act fav' + favCls + '" data-act="fav" aria-label="Бажане">' +
            '<svg class="ico-heart" viewBox="0 0 24 24"><path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>' +
          '</button>' +
          '<button class="track-act pin' + pinCls + '" data-act="pin" aria-label="Закріпити">' +
            '<svg class="ico-pin" viewBox="0 0 24 24"><path d="M14 4l-1 6 3 3v2h-4v5l-1 .5-1-.5v-5H6v-2l3-3-1-6h6m0-2H8c-.55 0-1 .45-1 1l.86 5.15L5.3 11.7c-.37.37-.3.99.05 1.34L6 14v3h12v-3l.65-.96c.35-.35.42-.97.05-1.34l-2.56-2.55L17 5c0-.55-.45-1-1-1z" fill="currentColor"/></svg>' +
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

    // Обрати трек за замовчуванням для запуску таймера.
    // Пріоритет: закріплений → бажаний → демо (пріоритет аудіо для кешу/фону)
    pickDefaultTrack() {
      const all = [
        ...(this.data.demo || []),
        ...(this.data.tracks || []),
      ];
      if (!all.length) return null;
      // фільтр за категорією якщо вибрана не 'all'
      const cat = state.category;
      let pool = (cat && cat !== "all") ? all.filter((t) => (t.category || "other") === cat) : all;
      if (!pool.length) pool = all;
      // "embeddable" = iframe-based (YouTube/SoundCloud/Spotify) — без фонового відтворення
      const isDirect = (t) => t.kind === "audio";
      // пріоритет: pinned-аудіо → pinned → favorite-аудіо → favorite → аудіо → перший
      return (
        pool.find((t) => t.is_pinned && isDirect(t)) ||
        pool.find((t) => t.is_pinned) ||
        pool.find((t) => t.is_favorite && isDirect(t)) ||
        pool.find((t) => t.is_favorite) ||
        pool.find(isDirect) ||
        pool[0]
      );
    },

    // ---------- Жести для треку ----------
    bindTrackGestures(el, t) {
      // демо може видалити лише адмін; свої треки — власник; admin — адмін
      const canDelete = (t._group === "demo") ? state.isAdmin : true;
      let startX = 0, startY = 0;
      let currentX = 0;
      let dragging = false;
      let moved = false;
      let horizontal = null; // null | true | false
      let opened = false;     // чи відкрита кнопка видалення
      const SWIPE_OPEN = -88; // наскільки зсунути, щоб показати "видалити"

      function setX(x) {
        currentX = x;
        // зсуваємо лише inner, щоб червоний .track-delete-bg залишався на місці
        const inner = el.querySelector(".track-item-inner");
        if (inner) inner.style.transform = "translateX(" + x + "px)";
      }
      function closeSwipe() {
        opened = false;
        const inner = el.querySelector(".track-item-inner");
        if (inner) inner.style.transform = "";
      }

      el.addEventListener("touchstart", (e) => {
        if (el.querySelector(".track-title-input")) return; // не ламати редагування
        if (!canDelete) return; // демо не свайпаються
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
    async play(t) {
      // цей трек тепер активний
      state.currentTrack = t;
      state.selectedTrack = t; // для зв'язку з таймером
      state.isPlaying = true;
      state.playerPlaying = true;
      this._refreshPlayStates();

      // embedding-based джерела: YouTube, SoundCloud, Spotify, Apple Music
      const EMBEDDABLE = ["youtube", "soundcloud", "spotify", "apple_music"];
      if (EMBEDDABLE.includes(t.kind) && t.embed_url) {
        const c = $("#yt-container");
        c.classList.remove("hidden");
        let src = t.embed_url;
        if (t.kind === "youtube") {
          // enablejsapi=1 + origin — дозволяють керувати play/pause через postMessage
          src += "&enablejsapi=1&origin=" + encodeURIComponent(location.origin) + "&autoplay=1";
        }
        c.innerHTML = '<iframe width="1" height="1" src="' + src + '" frameborder="0" allow="autoplay; encrypted-media"></iframe>';
        this._setupMediaSession(t);
        if (t.kind === "spotify") {
          toast("Spotify: 30-сек прев’ю. Повний трек — з входом в акаунт.");
        } else if (t.kind === "apple_music") {
          toast("Apple Music: потрібна підписка для повного треку.");
        }
        haptic("light");
      } else {
        // пряме аудіо — кешуємо на пристрій, граємо з blob: URL
        this._playAudioCached(t);
      }
    },

    // Відтворення прямих аудіо з локальним кешем (для фонового відтворення)
    async _playAudioCached(t) {
      const audio = $("#audio-el");
      const cacheKey = t.track_key || ("db:" + t.id) || t.url;
      // покажемо стан завантаження
      const artEl = document.querySelector('.track-item[data-key="' + cacheKey + '"] .track-art');
      if (artEl) artEl.classList.add("loading");

      try {
        const resolved = await AudioCache.resolveUrl(cacheKey, t.url, (p) => {
          if (artEl) artEl.style.setProperty("--dl", Math.round(p * 100) + "%");
        });
        audio.src = resolved.url;
        audio.setAttribute("data-cached", resolved.cached ? "1" : "0");
        await audio.play();
        this._setupMediaSession(t);
      } catch (e) {
        console.warn("audio play:", e.message);
        toast("Не вдалося відтворити трек");
      } finally {
        if (artEl) {
          artEl.classList.remove("loading");
          artEl.style.removeProperty("--dl");
        }
      }
      haptic("light");
    },

    // Пауза активного треку
    pause() {
      const audio = $("#audio-el");
      if (audio.src) audio.pause();
      // embed-джерела: YouTube — postMessage; інші (SoundCloud/Spotify/Apple)
      // не мають уніфікованого pause — зберігаємо iframe стан для resume
      if (state.currentTrack) {
        if (state.currentTrack.kind === "youtube") {
          this._ytCommand("pauseVideo");
        } else {
          // SoundCloud/Spotify/Apple Music: прибираємо src щоб зупинити звук,
          // зберігаємо для resume (перестворимо iframe)
          const c = $("#yt-container");
          const iframe = c.querySelector("iframe");
          if (iframe) {
            state._pausedEmbedSrc = iframe.src;
            iframe.src = "about:blank";
          }
        }
      }
      state.isPlaying = false;
      state.playerPlaying = false;
      this._refreshPlayStates();
      this._updateMediaPlaybackState("paused");
    },

    // Відновлення активного треку
    resume() {
      const audio = $("#audio-el");
      if (audio.src) audio.play().catch(() => {});
      if (state.currentTrack) {
        if (state.currentTrack.kind === "youtube") {
          this._ytCommand("playVideo");
        } else if (state._pausedEmbedSrc) {
          // перестворюємо iframe з збереженого src
          const c = $("#yt-container");
          c.innerHTML = '<iframe width="1" height="1" src="' + state._pausedEmbedSrc + '" frameborder="0" allow="autoplay; encrypted-media"></iframe>';
          state._pausedEmbedSrc = null;
        }
      }
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

    // Усі треки плоским списком (для автоплей)
    allTracks() {
      return [
        ...(this.data.demo || []),
        ...(this.data.tracks || []),
      ];
    },

    // Автовідтворення наступного треку з тієї ж категорії
    playNextInCategory() {
      const all = this.allTracks();
      if (!all.length || !state.currentTrack) {
        this.stopAudio(true);
        return;
      }
      const cat = state.currentTrack.category || "other";
      // фільтр по тій же категорії
      let pool = all.filter((t) => (t.category || "other") === cat);
      if (!pool.length) pool = all;
      // знайти індекс поточного
      const idx = pool.findIndex((t) => t.track_key === state.currentTrack.track_key);
      if (idx === -1) {
        this.stopAudio(true);
        return;
      }
      // наступний (по колу)
      const next = pool[(idx + 1) % pool.length];
      if (next.track_key === state.currentTrack.track_key) {
        // лише один трек — граємо знову
        this.play(next);
      } else {
        this.play(next);
      }
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
      // скидаємо стан Media Session
      this._setPlaybackState("none");
    },

    // ---------- Media Session API: музика у фоні + Dynamic Island ----------
    _setupMediaSession(t) {
      if (!("mediaSession" in navigator)) return;
      const title = t.title || "Без назви";
      const artist = t.author || (
        t.kind === "youtube" ? "YouTube" :
        t.kind === "soundcloud" ? "SoundCloud" :
        t.kind === "spotify" ? "Spotify" :
        t.kind === "apple_music" ? "Apple Music" : "Focus ON"
      );
      try {
        navigator.mediaSession.metadata = new MediaMetadata({
          title: title,
          artist: artist,
          album: "Focus ON",
          artwork: [
            { src: "/static/icon-96.png", sizes: "96x96", type: "image/png" },
            { src: "/static/icon-192.png", sizes: "192x192", type: "image/png" },
            { src: "/static/icon-256.png", sizes: "256x256", type: "image/png" },
            { src: "/static/icon-512.png", sizes: "512x512", type: "image/png" },
          ],
        });
        navigator.mediaSession.setActionHandler("play", () => this.resume());
        navigator.mediaSession.setActionHandler("pause", () => this.pause());
        navigator.mediaSession.setActionHandler("stop", () => this.stopAudio(true));
        if ("seekforward" in navigator.mediaSession) {
          navigator.mediaSession.setActionHandler("seekbackward", null);
          navigator.mediaSession.setActionHandler("seekforward", null);
        }
      } catch (e) {}
      this._setPlaybackState("playing");
    },

    // КРИТИЧНО для Dynamic Island/Lock Screen: playbackState показує,
    // що медіа активне → iOS показує Now Playing віджет
    _setPlaybackState(state) {
      if (!("mediaSession" in navigator)) return;
      try {
        navigator.mediaSession.playbackState = state; // "playing" | "paused" | "none"
      } catch (e) {}
      // позиція для скрабінгу (live stream = 0/0)
      if ("setPositionState" in navigator.mediaSession) {
        try {
          navigator.mediaSession.setPositionState({
            duration: Number.MAX_SAFE_INTEGER,
            playbackRate: 1,
            position: 0,
          });
        } catch (e) {}
      }
    },

    _updateMediaPlaybackState(stateStr) {
      this._setPlaybackState(stateStr);
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
        // скидаємо фільтр категорій щоб новий трек точно був виден
        resetMusicCategory();
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

    // закриття модалів по тапу на фон + swipe-down по .sheet
    // time-modal ВИКЛЮЧЕНО зі swipe-down: колесо використовує вертикальні жести,
    // тож свайп вниз має прокручувати пікер, а не закривати модал
    $$(".modal").forEach((m) => {
      m.addEventListener("click", (e) => {
        if (e.target !== m) return;
        if (m.id === "time-modal") { closeTimePicker(); return; }
        m.classList.add("hidden");
      });
      if (m.id === "time-modal") return; // без swipe-down для пікера
      const sheet = m.querySelector(".sheet");
      if (sheet) setupSheetSwipeDown(m, sheet);
    });
  }

  // Нативний iOS-жест: swipe-down по всьому sheet (не лише handle) → закрити
  // КРИТИЧНО: блокуємо прокрутку щоб Telegram не згортав застосунок
  function setupSheetSwipeDown(modal, sheet) {
    let startY = 0, currentY = 0, dragging = false;

    // Обробляємо дотик по всьому sheet — але не по інтерактивних елементах
    sheet.addEventListener("touchstart", (e) => {
      // не ламати кліки по кнопках/інпутах
      if (e.target.closest("input, button, textarea, .seg, .seg-choice, .chip")) return;
      const t = e.touches[0];
      startY = t.clientY;
      dragging = true;
      sheet.style.transition = "none";
    }, { passive: true });

    sheet.addEventListener("touchmove", (e) => {
      if (!dragging) return;
      const t = e.touches[0];
      currentY = t.clientY - startY;
      if (currentY > 0) {
        // перехоплюємо жест, щоб Telegram не згорнув застосунок
        e.preventDefault();
        sheet.style.transform = "translateY(" + currentY + "px)";
        modal.style.background = "rgba(0,0,0," + Math.max(0.1, 0.5 - currentY / 600) + ")";
      }
    });

    sheet.addEventListener("touchend", () => {
      if (!dragging) return;
      dragging = false;
      sheet.style.transition = "";
      if (currentY > 100) {
        modal.classList.add("hidden");
      }
      sheet.style.transform = "";
      modal.style.background = "";
      currentY = 0;
    });
  }

  // ---------- Події ----------
  function bindEvents() {
    $$(".mode-btn").forEach((b) => b.addEventListener("click", () => selectMode(b.dataset.mode)));

    $$(".tab").forEach((t) => t.addEventListener("click", () => showScreen(t.dataset.screen)));

    // тап по кружечку таймера = play/pause; довге утримання = STOP (скинути)
    setupTimerInteraction();
    // явна кнопка «Свій час» → відкриває пікер
    const ctBtn = $("#btn-custom-time");
    if (ctBtn) ctBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      if (!state.running) openTimePicker();
    });

    // кнопка "повідомити про баг"
    $("#btn-report-bug").addEventListener("click", () => {
      $("#bug-modal").classList.remove("hidden");
      $("#bug-message").focus();
    });
    // тема — перемикач світла/темна
    $("#btn-theme-toggle").addEventListener("click", toggleTheme);
    // підтримка — з'єднання з адміністратором
    $("#btn-support").addEventListener("click", () => {
      const url = "https://t.me/shurakorobov";
      if (tg && tg.openTelegramLink) tg.openTelegramLink(url);
      else window.open(url, "_blank");
    });

    // промокод — активувати (юзер)
    $("#btn-redeem").addEventListener("click", async () => {
      const code = $("#promo-input").value.trim();
      if (!code) { toast("Введи промокод"); return; }
      const btn = $("#btn-redeem");
      btn.disabled = true; btn.textContent = "Активую…";
      try {
        const res = await API.redeem(code);
        if (res.ok) {
          toast("⭐ Преміум активовано на " + res.days + " днів!");
          haptic("success");
          $("#promo-input").value = "";
          await loadProfile();
        } else {
          toast(res.error || "Невірний код");
        }
      } catch (e) {
        toast(e.message);
      } finally {
        btn.disabled = false; btn.textContent = "Активувати";
      }
    });

    // промокод — створити (адмін)
    const cpBtn = $("#btn-create-promo");
    if (cpBtn) cpBtn.addEventListener("click", async () => {
      const code = $("#promo-code-input").value.trim();
      const days = parseInt($("#promo-days-input").value) || 30;
      const max_uses = parseInt($("#promo-max-input").value) || 0;
      if (!code) { toast("Введи код"); return; }
      cpBtn.disabled = true;
      try {
        const res = await API.createPromoCode(code, days, max_uses);
        if (res.ok) {
          toast("Код " + code.toUpperCase() + " створено");
          $("#promo-code-input").value = "";
          loadPromoCodes();
        } else {
          toast(res.error || "Не вдалось");
        }
      } catch (e) { toast(e.message); }
      finally { cpBtn.disabled = false; }
    });

    // чіпи категорій на таймері
    function onTimerCategory(k) {
      // якщо таймер працює — зупиняємо і скидаємо (збереженням часткової сесії)
      if (state.running) {
        finish(false).then(() => {
          state.category = k;
          buildChips($("#timer-category-chips"), k, onTimerCategory);
          toast("Категорію змінено, таймер скинуто");
        });
        return;
      }
      state.category = k;
      buildChips($("#timer-category-chips"), k, onTimerCategory);
      haptic("light");
    }
    buildChips($("#timer-category-chips"), state.category, onTimerCategory);

    // чіпи фільтру музики
    buildChips($("#music-category-chips"), state.musicCategory, onMusicCategory, true);
  }

  // module-level: скидання фільтру музики після додавання треку
  function onMusicCategory(k) {
    state.musicCategory = k;
    buildChips($("#music-category-chips"), k, onMusicCategory, true);
    Music.load();
  }
  function resetMusicCategory() {
    state.musicCategory = "all";
    buildChips($("#music-category-chips"), "all", onMusicCategory, true);
  }

  // ---------- Тайм-пікер (iOS wheel) ----------
  const WHEEL_ITEM_H = 44; // має збігатись з CSS .wheel-item height
  let _wheelState = { h: 0, m: 25 };

  // Створює одне «колесо» з інерційним перетягуванням, wheel-скролом,
  // тапом по елементу та клавіатурою. Повертає геттер/сеттер значення.
  function buildWheel(col) {
    const max = parseInt(col.dataset.max);
    const unit = col.dataset.unit;
    const scroll = col.querySelector(".wheel-scroll");
    const values = [];
    for (let i = 0; i <= max; i++) values.push(i);

    // заповнюємо слоти
    scroll.innerHTML = values.map((v) =>
      '<div class="wheel-item" data-val="' + v + '">' + String(v).padStart(2, "0") + '</div>'
    ).join("");
    const items = scroll.querySelectorAll(".wheel-item");

    let current = 0;
    let lastHapticSlot = 0; // останній слот, на якому лунав фідбек

    function hapticTick() {
      // різкий rigid-impact + синтетичний «тік» — як нативний iOS picker.
      // throttle: не частіше ніж кожні 45мс, щоб не «залипало» при швидкій прокрутці
      const now = performance.now();
      if (now - (hapticTick._last || 0) < 45) return;
      hapticTick._last = now;
      tickFeedback();
    }

    function snapTo(val, animate = true) {
      const prev = current;
      current = ((val % (max + 1)) + (max + 1)) % (max + 1);
      if (!animate) scroll.classList.add("dragging");
      scroll.style.transform = "translateY(" + (-current * WHEEL_ITEM_H) + "px)";
      items.forEach((it) => it.classList.remove("center"));
      if (items[current]) items[current].classList.add("center");
      if (!animate) requestAnimationFrame(() => scroll.classList.remove("dragging"));
      _wheelState[unit] = current;
      // вібрація при зміні слоту (окрім першого встановлення)
      if (prev !== current) { hapticTick(); lastHapticSlot = current; }
    }

    // --- перетягування (touch + mouse) ---
    let startY = 0, startVal = 0, dragging = false, lastY = 0, lastT = 0, velocity = 0, moveListener = null, upListener = null;

    function onDown(y) {
      dragging = true;
      startY = lastY = y;
      startVal = current;
      lastT = performance.now();
      velocity = 0;
      scroll.classList.add("dragging");
    }
    function onMove(y) {
      if (!dragging) return;
      const dy = y - startY;
      const now = performance.now();
      velocity = (y - lastY) / Math.max(1, now - lastT);
      lastY = y; lastT = now;
      const offset = startVal + (-dy / WHEEL_ITEM_H);
      scroll.style.transform = "translateY(" + (-offset * WHEEL_ITEM_H) + "px)";
      // підсвічуємо найближчий слот у реальному часі
      const near = ((Math.round(offset) % (max + 1)) + (max + 1)) % (max + 1);
      items.forEach((it) => it.classList.remove("center"));
      if (items[near]) items[near].classList.add("center");
      // вібрація при переході через кожен слот (як нативний picker)
      if (near !== lastHapticSlot) { hapticTick(); lastHapticSlot = near; }
    }
    function onUp() {
      if (!dragging) return;
      dragging = false;
      scroll.classList.remove("dragging");
      // поточна «сира» позиція з transform
      const tr = scroll.style.transform || "";
      const m = tr.match(/-?[\d.]+/);
      const raw = m ? (parseFloat(m[0]) / -WHEEL_ITEM_H) : current;
      // інерція: швидкість у px/мс × вікно → додаткові слоти
      const flick = Math.round((velocity * 80) / WHEEL_ITEM_H);
      snapTo(Math.round(raw) + flick, true);
    }

    // touch
    scroll.addEventListener("touchstart", (e) => onDown(e.touches[0].clientY), { passive: true });
    scroll.addEventListener("touchmove", (e) => onMove(e.touches[0].clientY), { passive: true });
    scroll.addEventListener("touchend", onUp);
    // mouse
    scroll.addEventListener("mousedown", (e) => { e.preventDefault(); onDown(e.clientY);
      moveListener = (ev) => onMove(ev.clientY);
      upListener = () => { onUp(); document.removeEventListener("mousemove", moveListener); document.removeEventListener("mouseup", upListener); };
      document.addEventListener("mousemove", moveListener);
      document.addEventListener("mouseup", upListener);
    });
    // коліщатко миші (десктоп)
    col.addEventListener("wheel", (e) => {
      e.preventDefault();
      snapTo(current + (e.deltaY > 0 ? 1 : -1), true);
    }, { passive: false });
    // тап по елементу
    items.forEach((it, idx) => {
      it.addEventListener("click", () => snapTo(idx, true));
    });
    // клавіатура
    col.tabIndex = 0;
    col.addEventListener("keydown", (e) => {
      if (e.key === "ArrowUp") { e.preventDefault(); snapTo(current - 1, true); }
      else if (e.key === "ArrowDown") { e.preventDefault(); snapTo(current + 1, true); }
    });

    return {
      get: () => current,
      set: (v) => snapTo(v, false),
      snap: (v) => snapTo(v, true),
    };
  }

  let _wheels = null;

  function openTimePicker() {
    const h = Math.floor(state.totalSeconds / 3600);
    const m = Math.floor((state.totalSeconds % 3600) / 60);
    // «розблоковуємо» AudioContext у рамках user-gesture (тап по «Свій час»),
    // щоб звуки тікання працювали під час прокрутки колеса
    const ctx = audioCtx();
    if (ctx && ctx.state === "suspended") { try { ctx.resume(); } catch (e) {} }
    if (!_wheels) {
      _wheels = {};
      $$(".wheel-col").forEach((col) => {
        _wheels[col.dataset.unit] = buildWheel(col);
      });
    }
    _wheels.h.set(h);
    _wheels.m.set(m);
    $("#time-modal").classList.remove("hidden");
    // блокуємо нативний pull-down Telegram (щоб свайп по колесу не згортав вікно)
    _setVerticalSwipes(false);
  }

  function setupTimePicker() {
    $("#btn-time-cancel").addEventListener("click", () => closeTimePicker());
    $("#btn-time-save").addEventListener("click", () => {
      const total = _wheels.h.get() * 3600 + _wheels.m.get() * 60;
      if (total >= 60) {
        setCustomTime(total);
        closeTimePicker();
      } else {
        toast("Мінімум 1 хвилина");
      }
    });

    // пресети
    $$(".preset-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const sec = parseInt(btn.dataset.sec);
        setCustomTime(sec);
        closeTimePicker();
      });
    });
  }

  function closeTimePicker() {
    $("#time-modal").classList.add("hidden");
    _setVerticalSwipes(true); // повертаємо нативні свайпи
  }

  // Вмикає/вимикає вертикальні свайпи Telegram WebApp.
  // Коли wheel-пікер відкритий — вимикаємо, щоб свайп по колесу не згортав вікно.
  function _setVerticalSwipes(enable) {
    if (!tg) return;
    try {
      if (enable) {
        if (tg.enableVerticalSwipes) tg.enableVerticalSwipes();
      } else {
        if (tg.disableVerticalSwipes) tg.disableVerticalSwipes();
      }
    } catch (e) {}
  }

  // ---------- Взаємодія з таймером: тап = play/pause, утримання = STOP ----------
  function setupTimerInteraction() {
    const wrap = $("#timer-wrap");
    let pressTimer = null;
    let longPressed = false;
    let touchStartX = 0, touchStartY = 0;
    let lastInput = 0; // тип останнього вводу: щоб уникнути подвійної обробки touch+mouse

    function startPressTimer() {
      clearTimeout(pressTimer);
      pressTimer = setTimeout(() => {
        longPressed = true;
        wrap.classList.add("stopping");
        stop();
        haptic("med");
      }, 700);
    }

    function endPress() {
      clearTimeout(pressTimer);
      wrap.classList.remove("stopping");
      if (!longPressed) {
        toggleTimer();
      }
      longPressed = false;
    }

    // TOUCH — основний інпут на мобільних
    wrap.addEventListener("touchstart", (e) => {
      longPressed = false;
      lastInput = "touch";
      const t = e.touches[0];
      touchStartX = t.clientX;
      touchStartY = t.clientY;
      startPressTimer();
    }, { passive: true });

    wrap.addEventListener("touchmove", (e) => {
      const t = e.touches[0];
      const dx = Math.abs(t.clientX - touchStartX);
      const dy = Math.abs(t.clientY - touchStartY);
      if (dx > 10 || dy > 10) {
        clearTimeout(pressTimer);
        wrap.classList.remove("stopping");
      }
    }, { passive: true });

    wrap.addEventListener("touchend", (e) => {
      // блокуємо синтетичний mouse-клік після touch
      endPress();
    });

    // MOUSE — лише десктоп (touch-пристрої ігнорують, бо lastInput=touch)
    wrap.addEventListener("mousedown", (e) => {
      // якщо це синтетичний клік від touch — ігноруємо
      if (lastInput === "touch") return;
      longPressed = false;
      startPressTimer();
    });
    wrap.addEventListener("mouseup", (e) => {
      if (lastInput === "touch") return;
      endPress();
    });
    wrap.addEventListener("mouseleave", () => { clearTimeout(pressTimer); });
  }

  // ---------- Аудіо-елемент: реакція на завершення/помилки ----------
  function setupAudioEvents() {
    const audio = $("#audio-el");
    // трек закінчився — граємо наступний з тієї ж категорії
    audio.addEventListener("ended", () => {
      Music.playNextInCategory();
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

  // ---------- Відновлення музики після розблокування/повернення у застосунок ----------
  function setupVisibilityHandler() {
    // коли застосунок згортається → WebKit призупиняє аудіо.
    // при поверненні (розблокуванні) — відновлюємо якщо мало грати.
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState !== "visible") return;
      //小幅 delay, щоб WebKit встиг «прокинутись»
      setTimeout(() => {
        // якщо таймер працював і трек був активний — поновлюємо
        if (state.currentTrack && state.running) {
          const audio = $("#audio-el");
          if (audio.src && audio.paused) {
            audio.play().catch(() => {
              // якщо не вдалося (політика автоплею) — спробуємо через користувацький жест
            });
          }
          Music._refreshPlayStates();
        }
      }, 300);
    });
    // окремо: focus повернувся до вікна
    window.addEventListener("focus", () => {
      if (state.currentTrack && state.running && state.isPlaying) {
        const audio = $("#audio-el");
        if (audio.src && audio.paused) {
          audio.play().catch(() => {});
        }
      }
    });
  }

  // ---------- Старт ----------
  async function init() {
    bindEvents();
    setupModals();
    setupTimePicker();
    setupAudioEvents();
    setupVisibilityHandler();
    setupCrashReporting();
    setupHeartbeat();
    selectMode("focus");
    setFabIcon(false);
    renderTimer();
    await loadProfile();
    // передзавантажуємо список треків, щоб pickDefaultTrack мав дані
    Music.load().catch(() => {});
  }

  // ---------- Heartbeat: позначає користувача онлайн ----------
  // частий heartbeat (8с) + миттєвий SSE-пуш адміну (2с) → real-time відчуття.
  let _hbTimer = null;
  function setupHeartbeat() {
    // перший пуш — якнайшвидше після ініціалізації
    setTimeout(() => { API.heartbeat().catch(() => {}); }, 800);
    // далі кожні 8с — поки вікно видиме
    _hbTimer = setInterval(() => {
      if (document.visibilityState === "visible") {
        API.heartbeat().catch(() => {});
      }
    }, 8000);
    // при поверненні у вікно — одразу
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") {
        API.heartbeat().catch(() => {});
      }
    });
  }

  // ---------- Автолог крашів та аномалій ----------
  function setupCrashReporting() {
    // буфер останніх дій для контексту
    const log = [];
    const MAX_LOG = 30;
    window.__focusLog = log;
    window.__pushLog = (msg) => {
      log.push("[" + new Date().toISOString().slice(11, 19) + "] " + msg);
      if (log.length > MAX_LOG) log.shift();
    };

    // необроблені помилки
    window.addEventListener("error", (e) => {
      const msg = e.message + (e.filename ? " @ " + e.filename.split("/").pop() + ":" + e.lineno : "");
      window.__pushLog("ERROR: " + msg);
      autoReportBug("Авто-звіт (краш): " + msg + "\n\nКонтекст:\n" + log.slice(-10).join("\n"));
    });
    // необроблені проміси
    window.addEventListener("unhandledrejection", (e) => {
      const reason = (e.reason && (e.reason.message || String(e.reason))) || "unknown";
      window.__pushLog("REJECT: " + reason);
      autoReportBug("Авто-звіт (непійманий проміс): " + reason + "\n\nКонтекст:\n" + log.slice(-10).join("\n"));
    });

    // обгортка console.error для аномалій
    const origErr = console.error;
    console.error = function (...args) {
      window.__pushLog("console.error: " + args.map(String).join(" ").slice(0, 200));
      origErr.apply(console, args);
    };
  }

  // дебаунс щоб не спамити адміна однаковими помилками
  let lastAutoReport = 0;
  let lastAutoReportMsg = "";
  function autoReportBug(message) {
    const now = Date.now();
    // не надсилати ту ж помилку частіше ніж раз на 30с
    if (message === lastAutoReportMsg && now - lastAutoReport < 30000) return;
    lastAutoReport = now;
    lastAutoReportMsg = message;
    try {
      const platform = platformInfo();
      API.reportBug(message, platform, state.screen || "?").catch(() => {});
    } catch (e) {}
  }

  document.addEventListener("DOMContentLoaded", init);
})();
