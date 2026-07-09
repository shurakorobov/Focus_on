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
  const TITLES = { timer: "Focus", stats: "Статистика", music: "Музика" };

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
  }

  // ---------- Таймер ----------
  function selectMode(mode) {
    if (state.running) {
      toast("Зупини таймер, щоб змінити режим");
      return;
    }
    state.mode = mode;
    state.totalSeconds = MODES[mode].dur;
    state.remaining = MODES[mode].dur;
    setModeColor(MODES[mode].color);
    $$(".mode-btn").forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
    haptic("light");
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
  }

  function pause() {
    state.running = false;
    clearInterval(state.tickerId);
    state.tickerId = null;
    setFabIcon(false);
    $("#phase-label").textContent = "На паузі";
  }

  async function finish(completed) {
    clearInterval(state.tickerId);
    state.tickerId = null;
    state.running = false;
    setFabIcon(false);

    const elapsed = state.totalSeconds - state.remaining;
    const payload = {
      mode: state.mode,
      planned: state.totalSeconds,
      actual: completed ? state.totalSeconds : elapsed,
      completed: completed,
      started_at: state.startedAt || new Date().toISOString(),
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
      const u = data.user || {};
      const name = u.first_name || u.username || "";
      // залишаємо заголовок "Focus" — Apple-style
    } catch (e) {}
  }

  // ---------- Статистика ----------
  let statsLoaded = false;
  async function loadStats() {
    let data;
    try {
      data = await API.stats();
    } catch (e) {
      $("#stats-by-mode").innerHTML = "";
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
      data.recent.forEach((row) => {
        const meta = modesMeta[row.mode] || { label: row.mode };
        const d = document.createElement("div");
        d.className = "list-row";
        const ic = row.completed ? "✓" : "○";
        d.innerHTML =
          '<span>' + ic + " " + escapeHtml(meta.label) +
          '</span><span class="r-time">' + human(row.actual) + "</span>";
        recent.appendChild(d);
      });
    }
  }

  // ---------- Музика ----------
  const Music = {
    data: { demo: [], tracks: [] },

    async load() {
      try {
        this.data = await API.tracks();
        state.isAdmin = !!this.data.is_admin;
        state.uploadEnabled = !!this.data.upload_enabled;
        this.render();
        this.applyPermissions();
      } catch (e) {
        $("#track-list").innerHTML = '<div class="hint-inline">Не вдалося завантажити треки</div>';
      }
    },

    applyPermissions() {
      const admin = state.isAdmin;
      $("#scope-admin-label").classList.toggle("hidden", !admin);
      $("#scope-admin-label2").classList.toggle("hidden", !admin);
      $("#tab-upload").classList.toggle("hidden", !state.uploadEnabled);
    },

    allTracks() {
      const demo = (this.data.demo || []).map((t) => ({ ...t, _group: "demo" }));
      const saved = this.data.tracks || [];
      return [
        ...demo,
        ...saved.filter((t) => t.scope === "admin").map((t) => ({ ...t, _group: "admin" })),
        ...saved.filter((t) => t.scope === "user").map((t) => ({ ...t, _group: "user" })),
      ];
    },

    render() {
      const root = $("#track-list");
      root.innerHTML = "";
      const groups = [
        { key: "demo", title: "Демо", items: (this.data.demo || []).map((t) => ({ ...t, _group: "demo" })) },
        {
          key: "admin", title: "Для всіх",
          items: (this.data.tracks || []).filter((t) => t.scope === "admin").map((t) => ({ ...t, _group: "admin" })),
        },
        {
          key: "user", title: "Мої треки",
          items: (this.data.tracks || []).filter((t) => t.scope === "user").map((t) => ({ ...t, _group: "user" })),
        },
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
        root.innerHTML = '<div class="hint-inline">Поки порожньо. Натисни «＋» на вкладці «Для всіх»… хоча, додаємо через кнопку нижче 🎵</div>';
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

      // червона кнопка-фон під свайп
      const delBg = document.createElement("div");
      delBg.className = "track-delete-bg";
      delBg.innerHTML = '<svg viewBox="0 0 24 24" width="22" height="22"><path d="M6 7h12M9 7V5h6v2m-7 0v12a1 1 0 001 1h6a1 1 0 001-1V7" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>';
      el.appendChild(delBg);

      const inner = document.createElement("div");
      inner.className = "track-item-inner";
      inner.style.cssText = "display:flex;align-items:center;gap:14px;width:100%;";
      inner.innerHTML =
        '<div class="track-art ' + (isYt ? "yt" : "") + '">' +
        (isYt
          ? '<svg viewBox="0 0 24 24" width="20" height="20"><path d="M10 8l6 4-6 4z" fill="currentColor"/></svg>'
          : '<svg viewBox="0 0 24 24" width="20" height="20"><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="1.6"/><circle cx="12" cy="12" r="1.5" fill="currentColor"/></svg>') +
        '</div><div class="track-meta"><div class="track-title">' +
        escapeHtml(t.title || "Без назви") +
        '</div><div class="track-author">' +
        escapeHtml(t.author || (isYt ? "YouTube" : "")) +
        "</div></div>";
      el.appendChild(inner);

      // зберігаємо посилання на назву для inline-редагування
      el._titleEl = inner.querySelector(".track-title");
      el._trackData = t;

      // ---- жести ----
      this.bindTrackGestures(el, t);
      return el;
    },

    // ---------- Жести для треку ----------
    bindTrackGestures(el, t) {
      let startX = 0, startY = 0;
      let currentX = 0;
      let dragging = false;
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
        horizontal = null;
        el.style.transition = "none";
      }, { passive: true });

      el.addEventListener("touchmove", (e) => {
        if (!dragging) return;
        const touch = e.touches[0];
        const dx = touch.clientX - startX;
        const dy = touch.clientY - startY;

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

      // тап: відтворення + лічильник потрійного тапу на назві
      let tapCount = 0;
      let tapTimer = null;
      el.addEventListener("click", (e) => {
        if (currentX < -10) { closeSwipe(); opened = false; return; }
        if (el.querySelector(".track-title-input")) return;

        // потрійний тап саме по назві
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
        // одинарний тап — відтворення (тільки якщо це не 2/3 тап по назві)
        if (!onTitle || tapCount === 0) {
          this.play(t);
        }
      });

      // клік по відкритій кнопці видалення
      delBg.addEventListener("click", (e) => {
        e.stopPropagation();
        this.deleteTrack(t, el);
      });
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

    play(t) {
      $$(".track-item").forEach((x) => x.classList.remove("playing"));
      const items = Array.from($$(".track-item"));
      const target = items.find((x) => x.querySelector(".track-title") && x.querySelector(".track-title").textContent === (t.title || "Без назви"));
      if (target) target.classList.add("playing");

      $("#player").classList.remove("hidden");
      $("#player-title").textContent = t.title || "Без назви";
      $("#player-author").textContent = t.author || "";
      state.playerPlaying = true;
      updatePlayerIcon();

      this.stopAudio(true);

      if (t.kind === "youtube" && t.embed_url) {
        const c = $("#yt-container");
        c.classList.remove("hidden");
        c.innerHTML = '<iframe width="1" height="1" src="' + t.embed_url + '&autoplay=1" frameborder="0" allow="autoplay; encrypted-media"></iframe>';
      } else {
        const audio = $("#audio-el");
        audio.src = t.url;
        audio.play().catch(() => {});
      }
      haptic("light");
    },

    stopAudio(silent) {
      const audio = $("#audio-el");
      audio.pause();
      audio.removeAttribute("src");
      const c = $("#yt-container");
      c.innerHTML = "";
      c.classList.add("hidden");
      if (!silent) {
        $$(".track-item").forEach((x) => x.classList.remove("playing"));
        $("#player").classList.add("hidden");
        state.playerPlaying = false;
      }
    },

    togglePlay() {
      const audio = $("#audio-el");
      if (state.playerPlaying) {
        audio.pause();
        state.playerPlaying = false;
      } else {
        audio.play().catch(() => {});
        state.playerPlaying = true;
      }
      updatePlayerIcon();
    },
  };

  function updatePlayerIcon() {
    const btn = $("#player-play");
    btn.innerHTML = state.playerPlaying
      ? '<svg viewBox="0 0 24 24" width="20" height="20"><path d="M6 5h4v14H6zM14 5h4v14h-4z"/></svg>'
      : '<svg viewBox="0 0 24 24" width="20" height="20"><path d="M8 5v14l11-7z"/></svg>';
  }

  // ---------- Модал додавання ----------
  const Modal = {
    open() {
      // чистимо поля
      $("#track-url").value = "";
      $("#track-title").value = "";
      $("#track-author").value = "";
      $("#file-name").textContent = "";
      $("#upload-status").classList.add("hidden");
      $("#add-modal").classList.remove("hidden");
    },
    close() {
      $("#add-modal").classList.add("hidden");
    },
  };

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
        const res = await API.addTrackUrl({ url, title, author, scope });
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

    $("#player-stop").addEventListener("click", () => Music.stopAudio(false));
    $("#player-play").addEventListener("click", () => Music.togglePlay());
  }

  // ---------- Старт ----------
  async function init() {
    bindEvents();
    setupModals();
    selectMode("focus");
    setFabIcon(false);
    renderTimer();
    await loadProfile();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
