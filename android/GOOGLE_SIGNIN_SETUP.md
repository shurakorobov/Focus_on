# Google Sign-In — налаштування в Google Cloud Console

 Без цього кроку вхід через Google не запрацює — Android-клієнт не зможе
 отримати ID-токен, а бекенд не зможе його верифікувати.

 ⏱ Орієнтовний час: 15–20 хвилин.

 ───────────────────────────────────────────────────────────────
  КРОК 1. ВІДКРИЙ GOOGLE CLOUD CONSOLE
 ───────────────────────────────────────────────────────────────

 1.1. Відкрий https://console.cloud.google.com
 1.2. Увійди своїм Google-акаунтом (тим, що буде адміном Focus ON)
 1.3. Якщо проєктів ще немає — Google запропонує створити.
     Натисни на випадаючий список проєктів (вгорі, біля логотипа) → **New Project**
 1.4. Project name: `Focus ON`
 1.5. Натисни **Create**
 1.6. Дочекайся створення (5-10 сек), обери цей проєкт у списку


 ───────────────────────────────────────────────────────────────
  КРОК 2. OAuth CONSENT SCREEN (екран згоди)
 ───────────────────────────────────────────────────────────────

 Це сторінка, яку бачить юзер при вході через Google («Focus ON хоче отримати:
 ім'я, email»). Поки застосунок не перевірено Google — буде режим Testing.

 2.1. Ліве меню → **APIs & Services** → **OAuth consent screen**

 2.2. User Type: **External** (для загального використання)
      Натисни **Create**

 2.3. App information:
      ┌──────────────────────────────────────────────────────────┐
      │ App name:        Focus ON                                │
      │ User support email: [твій Gmail]                         │
      │ App logo:        (опційно — завантаж playstore-icon-512) │
      │ Application home page: https://focus-on.onrender.com     │
      │                                                          │
      │ Application type:  (•) Web, (•) Android                  │
      └──────────────────────────────────────────────────────────┘

 2.4. App domain (опційно, але рекомендую):
      - Privacy policy URL: https://focus-on.onrender.com/privacy
      - Terms of service URL: (пропусти)

 2.5. Authorized domains:
      - Додай: onrender.com

 2.6. Developer contact information:
      - [твій Gmail]

 2.7. Натисни **Save and Continue**

 2.8. Scopes — які дані запитуємо:
      - Натисни **Add or Remove Scopes**
      - Додай: `userinfo.email` та `userinfo.profile` (вони під "..auth/userinfo.email")
      - Натисни **Update**

 2.9. Test users:
      - Додай свій Gmail (і Gmail-и тестувальників)
      - Поки consent screen у Testing — лише ці акаунти зможуть увійти

 2.10. Натисни **Save and Continue** → переглянь summary → **Back to Dashboard**


 ───────────────────────────────────────────────────────────────
  КРОК 3. СТВОРЕННЯ WEB CLIENT ID (для бекенду)
 ───────────────────────────────────────────────────────────────

 ⭐ Цей Client ID потрібен бекенду для верифікації ID-токенів.

 3.1. Ліве меню → **APIs & Services** → **Credentials**

 3.2. Натисни **+ Create Credentials** → **OAuth client ID**

 3.3. Application type: **Web application**

 3.4. Name: `Focus ON — Web (backend verify)`

 3.5. Authorized JavaScript origins:
      ┌──────────────────────────────────────────────────────────┐
      │ https://focus-on.onrender.com                           │
      │ http://localhost:8000   (для локального тесту)          │
      └──────────────────────────────────────────────────────────┘

 3.6. Authorized redirect URIs: (залиш порожнім — не потрібен для ID-токенів)

 3.7. Натисни **Create**

 3.8. ⭐ З'явиться вікно з Client ID та Client Secret.
      Скопіюй **Client ID** (виглядає як
      `123456789-abcdefg.apps.googleusercontent.com`).

      Це значення треба вставити у ДВУХ місцях:
      - Бекенд (Render env): GOOGLE_OAUTH_CLIENT_ID
      - Android-код: AppConfig.GOOGLE_WEB_CLIENT_ID


 ───────────────────────────────────────────────────────────────
  КРОК 4. СТВОРЕННЯ ANDROID CLIENT ID (для застосунку)
 ───────────────────────────────────────────────────────────────

 ⭐ Android Client ID потрібен, щоб Google Play міг ідентифікувати застосунок.
      Саме він отримує ID-токен на пристрої.

 4.1. Ще раз **+ Create Credentials** → **OAuth client ID**

 4.2. Application type: **Android**

 4.3. Name: `Focus ON — Android`

 4.4. Package name: `com.focuson.app`

 4.5. SHA-1 certificate fingerprint — ТРЕБА ОТРИМАТИ З КЕYSTORE.

      Відкрий Git Bash у папці проєкту і виконай:

      ════ Для DEBUG-збірки ════
      keytool -list -v -keystore \
        "$ANDROID_HOME/.android/debug.keystore" \
        -alias androiddebugkey -storepass android -keypass android \
        | grep SHA1

      Або з Android Studio JBR (якщо keytool не в PATH):
      "S:/Development/AndroidStudio/jbr/bin/keytool.exe" -list -v \
        -keystore "$USERPROFILE/.android/debug.keystore" \
        -alias androiddebugkey -storepass android -keypass android 2>&1 | grep SHA1

      Скопіюй рядок, що починається з SHA1:
      (виглядає як `AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD`)

      ════ Для RELEASE-збірки (для Play Market) ════
      "S:/Development/AndroidStudio/jbr/bin/keytool.exe" -list -v \
        -keystore focuson-release.jks -storepass focuson2026 2>&1 | grep SHA1

      ⭐ Рекомендую додати ОБИДВА SHA-1 (debug + release) до цього OAuth-клієнта,
        щоб тестити і на debug, і на release.

 4.6. Натисни **Create** → скопіюй Client ID (для Android).
      (Він не вставляється в код напряму — Google зв'язує його по package+SHA-1)


 ───────────────────────────────────────────────────────────────
  КРОК 5. ВСТАВКА CLIENT ID В КОД
 ───────────────────────────────────────────────────────────────

 5.1. В Android-коді: відкрий файл
      C:\Users\Shura\ZCodeProject\Vibecode\android\app\src\main\java\com\focuson\app\MainActivity.kt
      Знайди рядок:
      ┌──────────────────────────────────────────────────────────────────────┐
      │ const val GOOGLE_WEB_CLIENT_ID = "PASTE_YOUR_WEB_CLIENT_ID..."       │
      └──────────────────────────────────────────────────────────────────────┘
      Заміни на Web Client ID з Кроку 3.8 (напр. 123456789-abc...googleusercontent.com)

 5.2. На бекенді (Render):
      - Відкрий https://dashboard.render.com
      - Обери свій сервіс focus-on
      - Environment → Add Environment Variable
      - Key:   GOOGLE_OAUTH_CLIENT_ID
      - Value: [той самий Web Client ID]
      - Save

 5.3. (Опційно) ADMIN_GOOGLE_EMAILS — твій Gmail для адмін-доступу:
      - Key:   ADMIN_GOOGLE_EMAILS
      - Value: shura.korobov@gmail.com  (твій Gmail)
      - Save

      ⭐ Без цього ти не побачиш адмін-дашборд статистики.
      Можна кілька через кому: a@gmail.com, b@gmail.com


 ───────────────────────────────────────────────────────────────
  КРОК 6. ПЕРЕВІРКА
 ───────────────────────────────────────────────────────────────

 Після виконання кроків 1–5:

 6.1. Перебілди Android-застосунок:
      cd /c/Users/Shura/ZCodeProject/Vibecode/android
      ./gradlew :app:assembleDebug

 6.2. Deploy на Render (якщо ще не зробив push):
      git add -A && git commit -m "Google Sign-In"
      git push origin main
      Render автоматично redeploy (чекай 1-2 хв)

 6.3. На телефоні (з Google-акаунтом):
      - Встанови застосунок
      - Натисни «Увійти через Google»
      - Системне вікно вибору акаунта
      - Обери свій Gmail
      - Застосунок одразу переходить на головний екран (без пароля!)

 6.4. Перевір, що твій email у ADMIN_GOOGLE_EMAILS:
      - У застосунку відкрий Профіль
      - Має побачити адмін-розділ зі статистикою онлайн


 ───────────────────────────────────────────────────────────────
  ЧАСТІ ПИТАННЯ / ПРОБЛЕМИ
 ───────────────────────────────────────────────────────────────

 П: "Помилка 16 (DEVELOPER_ERROR)" при вході
 В: Перевір що package name = com.focuson.app (точно) і SHA-1
    доданий до Android OAuth-клієнта (Крок 4.5).

 П: "Goog­le Sign-In неможливо — GOOGLE_WEB_CLIENT_ID placeholder"
 В: Ти не вставив Web Client ID у MainActivity.kt (Крок 5.1).

 П: Бекенд повертає "invalid google token"
 В: Або GOOGLE_OAUTH_CLIENT_ID не заданий у Render env,
    або не збігається з Client ID, що в AppDelegate.
    Обидва значення мають бути однакові (Web Client ID).

 П: Адмін-дашборд не видно після входу через Google
 В: Твій email не в ADMIN_GOOGLE_EMAILS (Крок 5.3).

 П: Можна пропустити SHA-1 для Android-клієнта?
 В: Ні — без нього Google не зв'яже застосунок з OAuth-клієнтом.

 П: "Testing" режим — це назавжди?
 В: Ні. Якщо публікуєш застосунок широко (100+ юзерів) — треба
    Submit for Verification у consent screen. Для початку достатньо Testing.


 ───────────────────────────────────────────────────────────────
  КУДИ ВСТАВЛЯТИ ОТРИМАНІ Client ID
 ───────────────────────────────────────────────────────────────

   Client ID тип          Куди вставити
  ──────────────────────┬──────────────────────────────────────
   Web Client ID         │ Render: GOOGLE_OAUTH_CLIENT_ID (env)
                         │ MainActivity.kt: AppConfig.GOOGLE_WEB_CLIENT_ID
   Android Client ID     │ Нікуди в код — Google зв'язує по package+SHA-1
