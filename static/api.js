// ===== Focus OS — API-клієнт =====
// Усі запити до бекенду проходять через цей модуль.
// Авторизація — через initData Telegram WebApp, що додається до кожного запиту.

const API = (() => {
  let initData = "";

  function getInitData() {
    if (initData) return initData;
    if (window.Telegram && window.Telegram.WebApp) {
      initData = window.Telegram.WebApp.initData || "";
    }
    return initData;
  }

  async function request(method, path, { json, body, params } = {}) {
    const headers = {};
    const opts = { method, headers };

    // Авторизація завжди через заголовок (надійніше за query-параметр —
    // initData довгий і може обрізатись проксі)
    const id = getInitData();
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
    stats: () => request("GET", "/api/stats"),
    finishSession: (payload) =>
      request("POST", "/api/session/finish", { json: payload }),

    // музика
    tracks: () => request("GET", "/api/tracks"),
    addTrackUrl: (payload) =>
      request("POST", "/api/tracks/url", { json: payload }),
    uploadTrack: (formData) =>
      request("POST", "/api/tracks/upload", { body: formData }),
    deleteTrack: (id) => request("DELETE", "/api/tracks/" + id),
    renameTrack: (id, title) =>
      request("PATCH", "/api/tracks/" + id, { json: { title } }),

    // для діагностики
    getInitData,
  };
})();
