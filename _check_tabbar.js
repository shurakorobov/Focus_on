// Перевіримо CSS стан таббару через веб-страницю напряму через Playwright
const pw = require('playwright');
(async () => {
  const b = await pw.chromium.launch({headless: true});
  // Емулюємо Android WebView точніше - висота 800px, ширина 412 (як Pixel на емуляторі)
  const p = await (await b.newContext({
    viewport: {width: 412, height: 915},
    isMobile: true,
    hasTouch: true,
    deviceScaleFactor: 2.625,
    userAgent: 'Mozilla/5.0 (Linux; Android 14; sdk_gphone64) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/120.0.0.0 Mobile Safari/537.36'
  })).newPage();
  
  await p.goto('https://focus-on.onrender.com/', {waitUntil:'networkidle', timeout: 60000});
  await p.waitForTimeout(3000);
  
  const info = await p.evaluate(() => {
    const t = document.querySelector('.tabbar');
    const h = document.querySelector('.large-title-bar');
    const s = document.querySelector('.screen.active');
    const body = document.body;
    function info(el, name) {
      if (!el) return name + ': NOT FOUND';
      const cs = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      return {
        name,
        position: cs.position,
        display: cs.display,
        height: cs.height,
        top: Math.round(r.top),
        bottom: Math.round(r.bottom),
        flexShrink: cs.flexShrink
      };
    }
    return {
      viewport: {w: window.innerWidth, h: window.innerHeight},
      body: info(body, 'body'),
      header: info(h, 'header'),
      screen: info(s, 'screen'),
      tabbar: info(t, 'tabbar'),
    };
  });
  console.log(JSON.stringify(info, null, 2));
  await b.close();
})();
