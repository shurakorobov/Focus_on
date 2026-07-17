# Play Console — налаштування місячної підписки Premium

 Покрокова інструкція для створення SKU `focus_on_premium_month`,
 який використовується в коді Android-клієнта (`BillingManager.kt`,
 `AppConfig.PREMIUM_SKU`) та бекенді (`config.GOOGLE_PLAY_PREMIUM_SKU`).

 ⚠️ **Product ID має збігатись символ-в-символ** з кодом, інакше
 `queryProductDetails` поверне порожній список і вікно оплати не відкриється.

 ---

 ## Передумови

 - [ ] Застосунок вже створено у Play Console (є package name `com.focuson.app`)
 - [ ] Хоча б один AAB завантажено у Internal Testing (треба для активації Products)
 - [ ] Акаунт має статус **Verified** (особа/організація підтверджена)

 Якщо AAB ще не завантажений — спершу виконай кроки з
 `PLAY_STORE_LISTING.md` (створення застосунку + upload AAB у Internal Testing),
 потім повертайся сюди.

 ---

 ## Крок 1. Відкрий розділ підписок

 1. Увійди в **[Play Console](https://play.google.com/console)**
 2. Обери свій застосунок **Focus ON**
 3. Ліве меню → **Earn** (або **Monetize**) → **Products** → **Subscriptions**

 > У новому інтерфейсі Play Console (2025+) шлях може бути:
 > **Monetize → Products → Subscriptions**

 ---

 ## Крок 2. Створи підписку

 1. Натисни **Create subscription** (або **+ Create product**)
 2. **Product ID:** `focus_on_premium_month`
    - ⚠️ ТІЛЬКИ малі літери, цифри, підкреслення
    - ⚠️ Після створення ЗМІНИТИ БУДЕ НЕМОЖЛИВО
    - ⚠️ Має **точно збігатись** із кодом у `BillingManager.kt`
 3. **Name (українська):** `Focus ON Premium — Місяць`
 4. **Name (англійська):** `Focus ON Premium — Month`
 5. Натисни **Create**

 ---

 ## Крок 3. Додай Base Plan (базовий план)

 Підписка без plan-у не з'явиться в Google Play Billing. Після створення
 продукту відкриється секція **Base plans & offers**.

 1. Натисни **Add base plan**
 2. Тип плану: **Auto-renewing** (автоподовження)
 3. **Plan ID:** `monthly` (або залиш дефолтний `base-plan-1`)
 4. **Renewal period:** 1 month (місяць)
 5. Натисни **Create**

 > Якщо є опція **Grace period** — встанови `3 days` (даемо юзеру 3 дні
 > після невдалого продовження, перш ніж забрати premium).
 > Опція **Resubscribe** — увімкни (дозволяє повернутись після скасування).

 ---

 ## Крок 4. Встанови ціну

 У створеному базовому плані:

 1. Натисни **Set price** (або **Add pricing**)
 2. Обери **price tier** або введи manually:
    - Рекомендовано: **$2.99 USD/міс** (≈ 125 грн)
    - Або **$4.99 USD/міс** (≈ 210 грн) — якщо цінуєш фічі високо
    - Або **$1.99 USD/міс** (≈ 85 грн) — для масової Adoption
 3. Google автоматично конвертує в локальні валюти (гривня, євро тощо)
    — можна відредагувати кожну окремо
 4. Збережи

 > Комісія Google: **15%** для підписок (не 30%).
 > При $2.99 отримаєш ~$2.54 за продаж.

 ---

 ## Крок 5. Активуй план

 1. Натисни трьокрапку меню біля плану
 2. Обери **Activate**
 3. Підтвердь

 Після активації план стає **Available** — доступний для покупки
 через BillingClient.

 ---

 ## Крок 6. Налаштуй License Testing (безкоштовні тести)

 Щоб тестувати оплату без реальних грошей:

 1. Play Console → **Setup** → **License Testing** (ліве меню, униз)
 2. Додай свій Google-акаунт (Gmail) до списку **License testers**
 3. Також додай акаунти товаришів, якщо будуть тестувати
 4. У тому ж розділі увімкни **Responds as UNPURCHASED** для тестів
    (або `PURCHASED` щоб перевірити активну підписку)

 > Тестові покупки НЕ знімають гроші, але проходять повний цикл:
 > BillingClient → launchBillingFlow → acknowledge → `/api/play/verify`.

 ---

 ## Крок 7. Вкажи обліковий запис для підписок (Privacy/Disclosure)

 Google вимагає URL з умовами підписки та політикою повернення:

 1. **Play Console** → **App content** (ліве меню)
 2. **Subscriptions** розділ → **Manage**
 3. Заповни:
    - **Privacy Policy URL:** `https://focus-on.onrender.com/privacy`
    - **Terms of Service URL:** (можна вказати той самий `/privacy`)
 4. Для кожного продукту-підписки додай:
    - Title: `Focus ON Premium`
    - Description: `Преміум-підписка з розширеною статистикою, пресетами та темами`
    - Price + renewal period: $2.99/міс
 5. Збережи

 Без цього Google **не пропустить** реліз із підпискою.

 ---

 ## Крок 8. Тест на реальному пристрої

 Після виконання кроків 1-5:

 1. На пристрої, де вхід у Google-акаунт зі списку License Testing
 2. Встанови debug-APK (`./gradlew :app:installDebug`)
    або відкрий застосунок через **Internal Testing**
 3. Увійди → Профіль → **«Підключити Premium»**
 4. Має відкритися **нативне вікно Google Play** (не браузер!)
    з ціною та кнопкою «Підписатись»
 5. Натисни **Підписатись** → відкриється тестовий засіб оплати
    (картка-пустушка з написом `Test card, always approves`)
 6. Після підтвердження:
    - `BillingManager.onResult(true, token)` →
    - `postMessage({type:'purchase_success', token})` →
    - `API.playVerify(token)` →
    - `db.set_user_plan(tg_id, "premium", 30)` →
    - toast `🎉 Преміум активовано!`
 7. Перевір `/api/me` — має повернути `is_premium: true`

 ---

 ## Перевірка діагностики (якщо щось не працює)

 ### Вікно Google Play не відкривається
 - Перевір, що **Product ID точно збігається**: `focus_on_premium_month`
 - Перевір, що base plan **Activated** (не в Draft)
 - Перевір, що поточний користувач Google-акаунта — **License tester**
 - Дивись `adb logcat | grep BillingManager`

 ### `error: product not found`
 - `queryProductDetails` повернув порожній список
 - Відбувається, коли Product ID не збігається або план не активований

 ### `error: billing not ready, retry`
 - BillingClient не встиг підключитися. Закрий/відкрий застосунок.
 - Може статися при першому запуску — дасто `onStart` → `startConnection`

 ### `error: no offer token`
 - Base plan не має offer. Створи base plan у Кроці 3.

 ### Покупка пройшла, але premium не активувався
 - Перевір `adb logcat` чи дійшов `purchase_success` до WebView
 - Перевір на бекенді лог `POST /api/play/verify` — має повернути `{"ok":true}`
 - Сповіщення адміну (телеграм-бот) має прийти з `Преміум активовано (Google Play)`

 ---

 ## Цінник-пам'ятка

 | Код (не міняти!)            | Де                           |
 |----------------------------|------------------------------|
 | `focus_on_premium_month`   | Play Console Product ID      |
 | `focus_on_premium_month`   | `AppConfig.PREMIUM_SKU` (Kotlin) |
 | `focus_on_premium_month`   | `config.GOOGLE_PLAY_PREMIUM_SKU` (Python) |
 | `focus_on_premium_month`   | `api.js playVerify()` (JS) default |
 | `focus_on_premium_month`   | `app.js openSubscribe()` (JS) |

 ---

 ## v2 — що додамо після публікації

 - **Повна серверна перевірка** через Google Play Developer API
   (`purchases.subscriptions.get`) — потрібен Service Account JSON
   + `google-api-python-client` у requirements.txt
 - **RTDN webhook** (Real-Time Developer Notifications) —
   отримує скасування/рефанди від Google → знімає premium автоматично
 - **Пробний період** (free trial 7 днів) через offer з `freeTrialDuration`
 - **Річна підписка** (інший SKU `focus_on_premium_year`) зі знижкою
