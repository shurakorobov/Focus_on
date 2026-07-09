// ===== Focus OS — локальний кеш аудіо (IndexedDB) =====
// Зберігає аудіофайли як Blob на пристрої, щоб:
//  1) музика грала з локального blob: URL — WebKit краще тримає таке у фоні
//  2) не завантажувати файл повторно при кожному play
//  3) працювало офлайн після першого завантаження

const AudioCache = (() => {
  const DB_NAME = "focus_os_audio";
  const STORE = "blobs";
  const VERSION = 1;

  let _db = null;

  function open() {
    return new Promise((resolve, reject) => {
      if (_db) return resolve(_db);
      const req = indexedDB.open(DB_NAME, VERSION);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains(STORE)) {
          db.createObjectStore(STORE, { keyPath: "key" });
        }
      };
      req.onsuccess = () => { _db = req.result; resolve(_db); };
      req.onerror = () => reject(req.error);
    });
  }

  async function _tx(mode) {
    const db = await open();
    return db.transaction(STORE, mode).objectStore(STORE);
  }

  async function has(key) {
    try {
      const store = await _tx("readonly");
      return await new Promise((resolve) => {
        const r = store.count(key);
        r.onsuccess = () => resolve(r.result > 0);
        r.onerror = () => resolve(false);
      });
    } catch (e) { return false; }
  }

  async function get(key) {
    try {
      const store = await _tx("readonly");
      return await new Promise((resolve) => {
        const r = store.get(key);
        r.onsuccess = () => resolve(r.result ? r.result.blob : null);
        r.onerror = () => resolve(null);
      });
    } catch (e) { return null; }
  }

  async function set(key, blob) {
    try {
      const store = await _tx("readwrite");
      return await new Promise((resolve) => {
        const r = store.put({ key, blob, size: blob.size, ts: Date.now() });
        r.onsuccess = () => resolve(true);
        r.onerror = () => resolve(false);
      });
    } catch (e) { return false; }
  }

  async function remove(key) {
    try {
      const store = await _tx("readwrite");
      return await new Promise((resolve) => {
        const r = store.delete(key);
        r.onsuccess = () => resolve(true);
        r.onerror = () => resolve(false);
      });
    } catch (e) { return false; }
  }

  async function clear() {
    try {
      const store = await _tx("readwrite");
      return await new Promise((resolve) => {
        const r = store.clear();
        r.onsuccess = () => resolve(true);
        r.onerror = () => resolve(false);
      });
    } catch (e) { return false; }
  }

  // Розмір усіх кешованих блобів (байт)
  async function size() {
    try {
      const store = await _tx("readonly");
      return await new Promise((resolve) => {
        const r = store.getAll();
        r.onsuccess = () => {
          const total = (r.result || []).reduce((a, x) => a + (x.size || 0), 0);
          resolve(total);
        };
        r.onerror = () => resolve(0);
      });
    } catch (e) { return 0; }
  }

  async function count() {
    try {
      const store = await _tx("readonly");
      return await new Promise((resolve) => {
        const r = store.count();
        r.onsuccess = () => resolve(r.result);
        r.onerror = () => resolve(0);
      });
    } catch (e) { return 0; }
  }

  // Завантажити URL у Blob і покласти в кеш. Повертає Blob або null.
  async function fetchAndStore(key, url, onProgress) {
    try {
      const res = await fetch(url);
      if (!res.ok) return null;
      // пробуємо streaming-прогрес, fallback на arrayBuffer
      if (res.body && typeof res.body.getReader === "function") {
        try {
          const total = parseInt(res.headers.get("content-length") || "0");
          const reader = res.body.getReader();
          const chunks = [];
          let received = 0;
          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            chunks.push(value);
            received += value.length;
            if (onProgress && total) onProgress(received / total);
          }
          const blob = new Blob(chunks);
          await set(key, blob);
          return blob;
        } catch (streamErr) {
          // fallback нижче
        }
      }
      // fallback: простий arrayBuffer
      const buf = await res.arrayBuffer();
      const blob = new Blob([buf]);
      await set(key, blob);
      if (onProgress) onProgress(1);
      return blob;
    } catch (e) {
      console.warn("AudioCache fetch error:", e);
      return null;
    }
  }

  // Отримати blob-URL: з кешу якщо є, інакше завантажити і покласти в кеш.
  // Повертає {url, cached} або null.
  async function resolveUrl(key, url, onProgress) {
    const cached = await get(key);
    if (cached) {
      return { url: URL.createObjectURL(cached), cached: true };
    }
    const blob = await fetchAndStore(key, url, onProgress);
    if (!blob) {
      // fallback — прямий URL без кешу
      return { url, cached: false };
    }
    return { url: URL.createObjectURL(blob), cached: true };
  }

  return { has, get, set, remove, clear, size, count, fetchAndStore, resolveUrl, open };
})();
