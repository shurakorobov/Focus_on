package com.focuson.app

import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.util.Log
import android.webkit.JavascriptInterface
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

// ── Конфігурація ──────────────────────────────────────────────
// Для тесту на емуляторі: 10.0.2.2 = хост-машина (твій локальний сервер)
// Для релізу: замінити на Render URL, напр. https://focus-on.onrender.com
object AppConfig {
    const val BASE_URL = "https://focus-on.onrender.com"
    // SKU місячної підписки premium у Google Play Console
    const val PREMIUM_SKU = "focus_on_premium_month"
    // Google OAuth Web Client ID (з Google Cloud Console).
    // Це Web Client ID, не Android — бо верифікація ID-токена на бекенді перевіряє
    // audience проти цього ID. Отримати: console.cloud.google.com → APIs & Services → Credentials.
    const val GOOGLE_WEB_CLIENT_ID = "736192450457-h3nlrfbdp1a3fqksmh6u87g5eomuct4o.apps.googleusercontent.com"
}

class MainActivity : ComponentActivity() {

    companion object {
        private const val TAG = "FocusON"
        private const val PREFS = "focus_auth"
        private const val KEY_JWT = "jwt_token"
    }

    // BillingClient менеджер (створюється при FocusWebView init)
    private var billingManager: BillingManager? = null

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme {
                Surface(modifier = Modifier.fillMaxSize(), color = Color(0xFF07030f)) {
                    AppContent()
                }
            }
        }
    }

    override fun onStart() {
        super.onStart()
        // Підключаємо BillingClient при вході в foreground
        billingManager?.startConnection()
    }

    override fun onDestroy() {
        billingManager?.endConnection()
        super.onDestroy()
    }

    @Composable
    private fun AppContent() {
        var jwt by remember { mutableStateOf(getSavedJwt()) }
        val scope = rememberCoroutineScope()

        if (jwt != null) {
            FocusWebView(jwt = jwt!!, onLogout = {
                saveJwt(null)
                jwt = null
            })
        } else {
            LoginScreen(onLogin = { code, onResult ->
                scope.launch {
                    val token = exchangeCode(code)
                    if (token != null) {
                        saveJwt(token)
                        jwt = token
                    } else {
                        onResult(false)
                    }
                }
            })
        }
    }

    // ── Екран входу (bot-code: код з Telegram-бота) ──────────────
    @Composable
    private fun LoginScreen(onLogin: (code: String, onResult: (Boolean) -> Unit) -> Unit) {
        val ctx = LocalContext.current
        var code by remember { mutableStateOf("") }
        var loading by remember { mutableStateOf(false) }
        var error by remember { mutableStateOf<String?>(null) }

        Column(
            modifier = Modifier.fillMaxSize().padding(32.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
        ) {
            Text("🎯 Focus ON", color = Color(0xFFbf5af2), fontSize = 36.sp, fontWeight = FontWeight.Bold)
            Spacer(Modifier.height(8.dp))
            Text("Таймер глибокої концентрації\nз музикою та звуками",
                color = Color(0xFFd2c8ff), fontSize = 15.sp, textAlign = TextAlign.Center, lineHeight = 22.sp)
            Spacer(Modifier.height(40.dp))

            // Інструкція
            Text("1️⃣ Відкрийте бота", color = Color(0xFFd2c8ff), fontSize = 15.sp, fontWeight = FontWeight.Medium)
            Spacer(Modifier.height(8.dp))
            // Кнопка відкрити бота
            OutlinedButton(
                onClick = {
                    val intent = Intent(Intent.ACTION_VIEW, Uri.parse("https://t.me/focuson_on_bot?start=login"))
                    ctx.startActivity(intent)
                },
                modifier = Modifier.fillMaxWidth().height(48.dp),
                shape = RoundedCornerShape(14.dp),
            ) {
                Text("Отримати код @focuson_on_bot", fontSize = 14.sp, color = Color(0xFF5ac8fa))
            }

            Spacer(Modifier.height(24.dp))
            Text("2️⃣ Надішліть боту /login", color = Color(0xFFd2c8ff), fontSize = 15.sp, fontWeight = FontWeight.Medium)
            Spacer(Modifier.height(8.dp))
            Text("3️⃣ Введіть отриманий код:", color = Color(0xFFd2c8ff), fontSize = 15.sp, fontWeight = FontWeight.Medium)
            Spacer(Modifier.height(12.dp))

            // Поле вводу коду (6 цифр)
            OutlinedTextField(
                value = code,
                onValueChange = { v -> code = v.filter { it.isDigit() }.take(6) },
                label = { Text("6-значний код") },
                singleLine = true,
                keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(
                    keyboardType = androidx.compose.ui.text.input.KeyboardType.NumberPassword
                ),
                modifier = Modifier.fillMaxWidth(),
                textStyle = androidx.compose.ui.text.TextStyle(
                    fontSize = 24.sp,
                    fontWeight = FontWeight.Bold,
                    textAlign = TextAlign.Center,
                    color = Color.White,
                ),
            )

            Spacer(Modifier.height(20.dp))
            Button(
                onClick = {
                    if (loading || code.length != 6) return@Button
                    loading = true; error = null
                    onLogin(code) { ok ->
                        loading = false
                        if (!ok) {
                            error = "Невірний або прострочений код. Перевірте та спробуйте ще раз."
                            code = ""
                        }
                    }
                },
                enabled = !loading && code.length == 6,
                modifier = Modifier.fillMaxWidth().height(54.dp),
                shape = RoundedCornerShape(14.dp),
                colors = ButtonDefaults.buttonColors(containerColor = Color(0xFFbf5af2)),
            ) {
                if (loading) {
                    CircularProgressIndicator(color = Color.White, strokeWidth = 2.dp, modifier = Modifier.size(24.dp))
                } else {
                    Text("Увійти", fontSize = 17.sp, fontWeight = FontWeight.SemiBold)
                }
            }

            error?.let {
                Spacer(Modifier.height(16.dp))
                Text(it, color = Color(0xFFFF453A), fontSize = 13.sp, textAlign = TextAlign.Center)
            }
            Spacer(Modifier.height(32.dp))
            Text("Код діє 5 хвилин.\nПотрібен Telegram на будь-якому пристрої.",
                color = Color(0xFF8a7fb5), fontSize = 12.sp, textAlign = TextAlign.Center, lineHeight = 18.sp)
        }
    }

    /** Обмін bot-code на JWT через бекенд. */
    private suspend fun exchangeCode(code: String): String? {
        return withContext(Dispatchers.IO) {
            try {
                val url = URL("${AppConfig.BASE_URL}/api/auth/exchange-code")
                val conn = url.openConnection() as HttpURLConnection
                conn.requestMethod = "POST"
                conn.setRequestProperty("Content-Type", "application/json")
                conn.doOutput = true
                val body = JSONObject().put("code", code).toString()
                conn.outputStream.use { it.write(body.toByteArray()) }
                if (conn.responseCode == 200) {
                    val resp = conn.inputStream.bufferedReader().readText()
                    JSONObject(resp).optString("token").takeIf { it.isNotEmpty() }
                } else {
                    Log.e(TAG, "exchange-code failed: ${conn.responseCode}")
                    null
                }
            } catch (e: Exception) {
                Log.e(TAG, "exchange-code error", e)
                null
            }
        }
    }
    /** One Tap через Credential Manager з повтором при transient cancellation. */
    // ── WebView з інжекцією JWT + нативний міст ──────────────────
    @SuppressLint("SetJavaScriptEnabled")
    @Composable
    private fun FocusWebView(jwt: String, onLogout: () -> Unit) {
        AndroidView(
            factory = { ctx ->
                WebView(ctx).apply {
                    settings.javaScriptEnabled = true
                    settings.domStorageEnabled = true
                    settings.mediaPlaybackRequiresUserGesture = false
                    // Адаптивність: widest viewport без обмеження ширини
                    settings.useWideViewPort = true
                    settings.loadWithOverviewMode = true
                    webChromeClient = WebChromeClient()
                    webViewClient = object : WebViewClient() {
                        override fun onPageFinished(view: WebView?, url: String?) {
                            super.onPageFinished(view, url)
                            // Ін'єкція JWT: window.__JWT + перехоплення fetch/XHR
                            // після повного завантаження сторінки (надійніше ніж через iframe)
                            val js = """
                                (function(){
                                  window.__JWT = "$jwt";
                                  if (window._jwtInjected) return;
                                  window._jwtInjected = true;
                                  var of = window.fetch;
                                  window.fetch = function(u,o){
                                    o=o||{}; o.headers=o.headers||{};
                                    if(!o.headers['Authorization']) o.headers['Authorization']='Bearer '+window.__JWT;
                                    return of(u,o);
                                  };
                                  var ox = window.XMLHttpRequest.prototype.open;
                                  window.XMLHttpRequest.prototype.open = function(m,u){
                                    this._u=u; return ox.apply(this,arguments);
                                  };
                                  var os = window.XMLHttpRequest.prototype.send;
                                  window.XMLHttpRequest.prototype.send = function(){
                                    try{ this.setRequestHeader('Authorization','Bearer '+window.__JWT); }catch(e){}
                                    return os.apply(this,arguments);
                                  };
                                })();
                            """.trimIndent()
                            view?.evaluateJavascript(js, null)
                        }
                    }

                    // Нативний міст: JS викликає AndroidNative.startFocus(...) і .purchasePremium()
                    val billing = BillingManager(
                        context = this@MainActivity,
                        skuId = AppConfig.PREMIUM_SKU,
                        onResult = { ok, token -> BillingBridge.onPurchaseResult?.invoke(ok, token) }
                    )
                    billingManager = billing
                    addJavascriptInterface(NativeBridge(this@MainActivity, this, billing), "AndroidNative")

                    // Реєструємо callback'и від FocusService → оновлення UI через JS
                    FocusBridge.tick = { sec ->
                        post {
                            evaluateJavascript(
                                "(function(){try{window.postMessage({type:'focus_tick',remaining:$sec},'*');" +
                                "var d=document;if(d.defaultView)d.defaultView.postMessage({type:'focus_tick',remaining:$sec},'*');" +
                                "}catch(e){}})();", null
                            )
                        }
                    }
                    FocusBridge.finished = {
                        post {
                            evaluateJavascript(
                                "(function(){try{window.postMessage({type:'focus_finished'},'*');" +
                                "var d=document;if(d.defaultView)d.defaultView.postMessage({type:'focus_finished'},'*');" +
                                "}catch(e){}})();", null
                            )
                        }
                    }
                    // Результат покупки premium → postMessage прямо в document (без iframe)
                    BillingBridge.onPurchaseResult = { ok, token ->
                        post {
                            // JSON-кодуємо token безпечно
                            val payload = JSONObject()
                                .put("type", if (ok) "purchase_success" else "purchase_failed")
                                .put("token", token)
                                .toString()
                            evaluateJavascript(
                                "(function(){try{window.postMessage($payload,'*');" +
                                "var d=document;if(d.defaultView)d.defaultView.postMessage($payload,'*');" +
                                "}catch(e){}})();", null
                            )
                        }
                    }
                    // Завантажуємо застосунок напряму (без iframe).
                    // JWT ін'єкція відбувається в onPageFinished (вище).
                    loadUrl("${AppConfig.BASE_URL}/")
                }
            },
            modifier = Modifier.fillMaxSize(),
        )
    }

    // ── Мережа: обмін bot-code на JWT ─────────────────────────────

    // ── Сховище JWT ──────────────────────────────────────────────
    private fun getSavedJwt(): String? {
        return getSharedPreferences(PREFS, MODE_PRIVATE).getString(KEY_JWT, null)
    }
    private fun saveJwt(token: String?) {
        val prefs = getSharedPreferences(PREFS, MODE_PRIVATE).edit()
        if (token != null) prefs.putString(KEY_JWT, token) else prefs.remove(KEY_JWT)
        prefs.apply()
    }
}

/**
 * JavascriptInterface: JS викликає AndroidNative.startFocus(duration, url, title)
 * або .purchasePremium() для підписки через Google Play Billing.
 * ВАЖЛИВО: @JavascriptInterface потрібен для безпеки (Android 4.2+).
 */
class NativeBridge(
    private val activity: ComponentActivity,
    private val webView: WebView,
    private val billing: BillingManager,
) {

    @JavascriptInterface
    fun startFocus(durationSec: Int, trackUrl: String, trackTitle: String) {
        Log.i("NativeBridge", "startFocus dur=$durationSec url=${trackUrl.take(40)}")
        val intent = Intent(activity, FocusService::class.java).apply {
            action = FocusService.ACTION_START
            putExtra(FocusService.EXTRA_DURATION, durationSec)
            putExtra(FocusService.EXTRA_TRACK_URL, trackUrl)
            putExtra(FocusService.EXTRA_TRACK_TITLE, trackTitle)
        }
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.O) {
            activity.startForegroundService(intent)
        } else {
            activity.startService(intent)
        }
    }

    @JavascriptInterface
    fun stopFocus() {
        Log.i("NativeBridge", "stopFocus")
        val intent = Intent(activity, FocusService::class.java).apply {
            action = FocusService.ACTION_STOP
        }
        activity.startService(intent)
    }

    /** Запускає нативний UI Google Play для купівлі місячної підписки premium. */
    @JavascriptInterface
    fun purchasePremium() {
        Log.i("NativeBridge", "purchasePremium")
        activity.runOnUiThread { billing.purchasePremium(activity) }
    }

    /** JS-флаг: чи доступна нативна оплата (Google Play Billing). */
    @JavascriptInterface
    fun hasNativeBilling(): Boolean = true

    @JavascriptInterface
    fun ping(): String = "native-bridge-ok"
}

/**
 * Синглтон-міст для результатів Google Play Billing → WebView.
 * BillingManager викликає onPurchaseResult(ok, token),
 * MainActivity реєструє лямбду, що кидає postMessage в iframe.
 * (Mirror патерну FocusBridge у FocusService.kt.)
 */
object BillingBridge {
    var onPurchaseResult: ((ok: Boolean, token: String) -> Unit)? = null
}
