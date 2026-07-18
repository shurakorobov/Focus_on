// Скрипт робить 5 скріншотів Focus ON для Play Store (надійні переходи).
const { chromium } = require('C:/Users/Shura/AppData/Local/Temp/pw/node_modules/playwright');

const BASE = 'https://focus-on.onrender.com/';
const OUT = './screenshots/';

async function clickNav(page, label) {
  // таббар: шукаємо button що містить текст label
  const els = await page.$$('nav.tabbar button, .tabbar button, [class*="tabbar"] button');
  for (const el of els) {
    const t = (await el.innerText()).trim();
    if (t.includes(label)) { await el.click(); return true; }
  }
  // fallback: будь-яка кнопка з текстом
  try { await page.click(`button:has-text("${label}")`, { timeout: 3000 }); return true; } catch (e) {}
  return false;
}

(async () => {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
  });
  const page = await ctx.newPage();

  console.log('Відкриваю Focus ON...');
  await page.goto(BASE, { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForTimeout(2500);

  // Закриваємо онбординг
  try {
    await page.click('button:has-text("Пропустити")', { timeout: 2000 });
    await page.waitForTimeout(800);
  } catch (e) {}

  // 1. Таймер
  console.log('1/5 — Таймер');
  await page.screenshot({ path: OUT + '1-timer.png' });

  // 2. Музика
  console.log('2/5 — Музика');
  let ok = await clickNav(page, 'Музика');
  console.log('  click Музика:', ok);
  await page.waitForTimeout(2000);
  await page.screenshot({ path: OUT + '2-music.png' });

  // 3. Звуки (вкладка всередині Музики)
  console.log('3/5 — Звуки');
  try {
    // segmented control .media-seg → кнопка з текстом "🌊 Звуки" (з емодзі!)
    const segs = await page.$$('.media-seg button, .seg-mini');
    let clicked = false;
    for (const s of segs) {
      const t = (await s.innerText().catch(() => '')).trim();
      if (t.includes('Звуки')) { await s.click(); clicked = true; break; }
    }
    console.log('  click Звуки:', clicked);
    await page.waitForTimeout(1500);
  } catch (e) { console.log('  Звуки помилка:', e.message); }
  await page.screenshot({ path: OUT + '3-sounds.png' });

  // 4. Статистика
  console.log('4/5 — Статистика');
  ok = await clickNav(page, 'Статистика');
  console.log('  click Статистика:', ok);
  await page.waitForTimeout(2000);
  await page.screenshot({ path: OUT + '4-stats.png' });

  // 5. Профіль
  console.log('5/5 — Профіль');
  ok = await clickNav(page, 'Профіль');
  console.log('  click Профіль:', ok);
  await page.waitForTimeout(2000);
  await page.screenshot({ path: OUT + '5-profile.png' });

  await browser.close();
  console.log('✓ Готово!');
})().catch((e) => { console.error('Помилка:', e.message); process.exit(1); });
