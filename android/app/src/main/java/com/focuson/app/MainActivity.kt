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
import java.net.URLEncoder

// ── Конфігурація ──────────────────────────────────────────────
// Для тесту на емуляторі: 10.0.2.2 = хост-машина (твій локальний сервер)
// Для релізу: замінити на Render URL, напр. https://focus-on.onrender.com
object AppConfig {
    const val BASE_URL = "https://focus-on.onrender.com"
    // Telegram bot username для Login Widget (без @)
    const val BOT_USERNAME = "focuson_on_bot"
}

class MainActivity : ComponentActivity() {

    companion object {
        private const val TAG = "FocusON"
        private const val PREFS = "focus_auth"
        private const val KEY_JWT = "jwt_token"
    }

    // Колбек від Telegram Login (через deep link / intent)
    private var onLoginResult: ((String) -> Unit)? = null

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
        // Обробка deep link / intent від браузера після логіну
        handleIntent(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleIntent(intent)
    }

    private fun handleIntent(intent: Intent?) {
        val data = intent?.data ?: return
        // Telegram Login повертає auth_data як query parameter
        data.getQueryParameter("auth_data")?.let { authData ->
            onLoginResult?.invoke(authData)
        }
    }

    @Composable
    private fun AppContent() {
        var jwt by remember { mutableStateOf(getSavedJwt()) }

        // Колбек з LoginScreen (через NativeBridge.onLogin з WebView)
        onLoginResult = { token ->
            saveJwt(token)
            jwt = token
        }

        if (jwt != null) {
            FocusWebView(jwt = jwt!!, onLogout = {
                saveJwt(null)
                jwt = null
            })
        } else {
            LoginScreen()
        }
    }

    // ── Екран входу ──────────────────────────────────────────────
    // WebView зі сторінкою /android-login. Telegram Login Widget відпрацює
    // всередині WebView і передасть JWT через AndroidNative.onLogin(token).
    @SuppressLint("SetJavaScriptEnabled")
    @Composable
    private fun LoginScreen() {
        AndroidView(
            factory = { ctx ->
                WebView(ctx).apply {
                    settings.javaScriptEnabled = true
                    settings.domStorageEnabled = true
                    webViewClient = WebViewClient()
                    webChromeClient = WebChromeClient()
                    // Міст: сторінка /android-login викличе AndroidNative.onLogin(token)
                    addJavascriptInterface(object {
                        @JavascriptInterface
                        fun onLogin(token: String) {
                            runOnUiThread { onLoginResult?.invoke(token) }
                        }
                    }, "AndroidNative")
                    loadUrl("${AppConfig.BASE_URL}/android-login")
                }
            },
            modifier = Modifier.fillMaxSize(),
        )
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

                    // Нативний міст: JS викликає AndroidNative.startFocus(...)
                    addJavascriptInterface(NativeBridge(this@MainActivity, this), "AndroidNative")

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

    // ── Мережа: обмін Telegram payload на JWT ────────────────────
    private suspend fun exchangeTelegramLogin(authData: String): String? {
        return withContext(Dispatchers.IO) {
            try {
                val url = URL("${AppConfig.BASE_URL}/api/auth/telegram-login")
                val conn = url.openConnection() as HttpURLConnection
                conn.requestMethod = "POST"
                conn.setRequestProperty("Content-Type", "application/json")
                conn.doOutput = true
                val body = JSONObject().put("auth_data", authData).toString()
                conn.outputStream.use { it.write(body.toByteArray()) }

                if (conn.responseCode == 200) {
                    val resp = conn.inputStream.bufferedReader().readText()
                    JSONObject(resp).optString("token").takeIf { it.isNotEmpty() }
                } else {
                    Log.e(TAG, "login failed: ${conn.responseCode}")
                    null
                }
            } catch (e: Exception) {
                Log.e(TAG, "login error", e)
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

    private fun getBotId(): String {
        return "8922920802"
    }
}

/**
 * JavascriptInterface: JS викликає AndroidNative.startFocus(duration, url, title).
 * ВАЖЛИВО: @JavascriptInterface потрібен для безпеки (Android 4.2+).
 */
class NativeBridge(private val activity: ComponentActivity, private val webView: WebView) {

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

    @JavascriptInterface
    fun ping(): String = "native-bridge-ok"
}
