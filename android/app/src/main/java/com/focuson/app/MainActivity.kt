package com.focuson.app

import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
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
import androidx.credentials.CredentialManager
import androidx.credentials.CustomCredential
import androidx.credentials.GetCredentialRequest
import com.google.android.libraries.identity.googleid.GetGoogleIdOption
import com.google.android.libraries.identity.googleid.GoogleIdTokenCredential
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
            LoginScreen(onLogin = { idToken ->
                scope.launch {
                    val token = exchangeGoogleLogin(idToken)
                    if (token != null) {
                        saveJwt(token)
                        jwt = token
                    }
                }
            })
        }
    }

    // ── Екран входу (Google Sign-In через Credential Manager) ─────
    @Composable
    private fun LoginScreen(onLogin: (idToken: String) -> Unit) {
        val ctx = LocalContext.current
        val scope = rememberCoroutineScope()
        var loading by remember { mutableStateOf(false) }
        var error by remember { mutableStateOf<String?>(null) }

        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(32.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
        ) {
            Text(
                "🎯 Focus ON",
                color = Color(0xFFbf5af2),
                fontSize = 36.sp,
                fontWeight = FontWeight.Bold,
            )
            Spacer(Modifier.height(8.dp))
            Text(
                "Таймер глибокої концентрації\nз музикою та звуками",
                color = Color(0xFFd2c8ff),
                fontSize = 15.sp,
                textAlign = TextAlign.Center,
                lineHeight = 22.sp,
            )
            Spacer(Modifier.height(48.dp))
            Button(
                onClick = {
                    if (loading) return@Button
                    loading = true
                    error = null
                    scope.launch {
                        val idToken = signInWithGoogle(ctx)
                        if (idToken != null) {
                            onLogin(idToken)
                        } else {
                            loading = false
                            error = "Не вдалося увійти. Перевірте підключення до інтернету."
                        }
                    }
                },
                enabled = !loading,
                modifier = Modifier
                    .fillMaxWidth()
                    .height(54.dp),
                shape = RoundedCornerShape(14.dp),
                colors = ButtonDefaults.buttonColors(containerColor = Color(0xFFbf5af2)),
            ) {
                if (loading) {
                    CircularProgressIndicator(
                        color = Color.White,
                        strokeWidth = 2.dp,
                        modifier = Modifier.size(24.dp)
                    )
                } else {
                    Text("Увійти через Google", fontSize = 17.sp, fontWeight = FontWeight.SemiBold)
                }
            }
            error?.let {
                Spacer(Modifier.height(16.dp))
                Text(it, color = Color(0xFFFF453A), fontSize = 13.sp, textAlign = TextAlign.Center)
            }
            Spacer(Modifier.height(24.dp))
            Text(
                "Вхід безпарольний — оберіть Google-акаунт,\nякий вже додано на цьому пристрої",
                color = Color(0xFF8a7fb5),
                fontSize = 12.sp,
                textAlign = TextAlign.Center,
                lineHeight = 18.sp,
            )
        }
    }

    /** Запускає Google Sign-In через Credential Manager (One Tap).
     *  Повертає idToken або null. */
    private suspend fun signInWithGoogle(ctx: Context): String? {
        return withContext(Dispatchers.Main) {
            try {
                val credentialManager = CredentialManager.create(ctx)
                val googleIdOption = GetGoogleIdOption.Builder()
                    .setFilterByAuthorizedAccounts(false)
                    .setServerClientId(AppConfig.GOOGLE_WEB_CLIENT_ID)
                    .build()
                val request = GetCredentialRequest.Builder()
                    .addCredentialOption(googleIdOption)
                    .build()
                val result = credentialManager.getCredential(
                    request = request,
                    context = ctx as ComponentActivity,
                )
                val cred = result.credential
                if (cred is CustomCredential && cred.type == GoogleIdTokenCredential.TYPE_GOOGLE_ID_TOKEN_CREDENTIAL) {
                    GoogleIdTokenCredential.createFrom(cred.data).idToken
                } else {
                    Log.w(TAG, "Невідомий тип credential: ${cred.type}")
                    null
                }
            } catch (e: Exception) {
                Log.e(TAG, "Google Sign-In помилка", e)
                null
            }
        }
    }

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
                    webChromeClient = WebChromeClient()
                    webViewClient = WebViewClient()

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
                                "(function(){try{var f=document.getElementById('f');" +
                                "if(f&&f.contentWindow)f.contentWindow.postMessage(" +
                                "{type:'focus_tick',remaining:$sec},'*');}catch(e){}})();", null
                            )
                        }
                    }
                    FocusBridge.finished = {
                        post {
                            evaluateJavascript(
                                "(function(){try{var f=document.getElementById('f');" +
                                "if(f&&f.contentWindow)f.contentWindow.postMessage(" +
                                "{type:'focus_finished'},'*');}catch(e){}})();", null
                            )
                        }
                    }
                    // Результат покупки premium → postMessage в iframe
                    BillingBridge.onPurchaseResult = { ok, token ->
                        post {
                            // JSON-кодуємо token безпечно (екрануєє спецсимволи/кавички)
                            val payload = JSONObject()
                                .put("type", if (ok) "purchase_success" else "purchase_failed")
                                .put("token", token)
                                .toString()
                            evaluateJavascript(
                                "(function(){try{var f=document.getElementById('f');" +
                                "if(f&&f.contentWindow)f.contentWindow.postMessage(" +
                                "$payload,'*');}catch(e){}})();", null
                            )
                        }
                    }
                    // Завантажуємо сторінку з інжекцією JWT через обгортку fetch
                    val html = """
                        <!DOCTYPE html><html><head><meta charset="utf-8">
                        <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
                        <style>
                          html,body{margin:0;padding:0;background:#07030f;height:100%;overflow:hidden;}
                          #f{position:fixed;inset:0;border:none;width:100%;height:100%;}
                        </style></head><body>
                        <iframe id="f" src="${AppConfig.BASE_URL}/"></iframe>
                        <script>
                          const JWT = "$jwt";
                          const f = document.getElementById('f');
                          f.onload = function() {
                            try {
                              const w = f.contentWindow;
                              w.__JWT = JWT;
                              w.eval([
                                "(function(){",
                                "  var of = window.fetch;",
                                "  window.fetch = function(u,o){",
                                "    o=o||{}; o.headers=o.headers||{};",
                                "    o.headers['Authorization']='Bearer '+window.__JWT;",
                                "    return of(u,o);",
                                "  };",
                                "  var ox = window.XMLHttpRequest.prototype.open;",
                                "  window.XMLHttpRequest.prototype.open = function(m,u){",
                                "    this._u=u; return ox.apply(this,arguments);",
                                "  };",
                                "  var os = window.XMLHttpRequest.prototype.send;",
                                "  window.XMLHttpRequest.prototype.send = function(){",
                                "    try{ this.setRequestHeader('Authorization','Bearer '+window.__JWT); }catch(e){}",
                                "    return os.apply(this,arguments);",
                                "  };",
                                "})();"
                              ].join('\n'));
                            } catch(e) { console.error('inject', e); }
                          };
                        </script>
                        </body></html>
                    """.trimIndent()
                    loadDataWithBaseURL(AppConfig.BASE_URL + "/", html, "text/html", "utf-8", null)
                }
            },
            modifier = Modifier.fillMaxSize(),
        )
    }

    // ── Мережа: обмін Google ID token на JWT ─────────────────────
    private suspend fun exchangeGoogleLogin(idToken: String): String? {
        return withContext(Dispatchers.IO) {
            try {
                val url = URL("${AppConfig.BASE_URL}/api/auth/google")
                val conn = url.openConnection() as HttpURLConnection
                conn.requestMethod = "POST"
                conn.setRequestProperty("Content-Type", "application/json")
                conn.doOutput = true
                val body = JSONObject().put("id_token", idToken).toString()
                conn.outputStream.use { it.write(body.toByteArray()) }

                if (conn.responseCode == 200) {
                    val resp = conn.inputStream.bufferedReader().readText()
                    JSONObject(resp).optString("token").takeIf { it.isNotEmpty() }
                } else {
                    Log.e(TAG, "google login failed: ${conn.responseCode}")
                    null
                }
            } catch (e: Exception) {
                Log.e(TAG, "google login error", e)
                null
            }
        }
    }

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
