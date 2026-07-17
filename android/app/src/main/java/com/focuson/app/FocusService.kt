package com.focuson.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.media.AudioAttributes
import android.media.MediaPlayer
import android.os.Build
import android.os.CountDownTimer
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat

/**
 * Foreground service: тримає таймер фокусу + відтворення аудіо у фоні.
 *
 * Запускається через Intent з екстра: duration (сек), trackUrl (може бути порожнім).
 * Коли таймер доходить до 0 — service зупиняється, показує фінальне сповіщення.
 *
 * Це головна перевага нативного застосунку над Mini App:
 * WebKit паузить аудіо при згортанні, а foreground service — ні.
 */
class FocusService : Service() {

    companion object {
        const val TAG = "FocusService"
        const val CHANNEL_ID = "focus_timer"
        const val NOTIF_ID = 42
        const val ACTION_START = "START"
        const val ACTION_STOP = "STOP"
        const val EXTRA_DURATION = "duration"
        const val EXTRA_TRACK_URL = "track_url"
        const val EXTRA_TRACK_TITLE = "track_title"
    }

    private var timer: CountDownTimer? = null
    private var player: MediaPlayer? = null
    private var remainingMs: Long = 0
    private var totalSec: Int = 0

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                stopFocus()
                return START_NOT_STICKY
            }
            ACTION_START -> {
                val duration = intent.getIntExtra(EXTRA_DURATION, 0)
                val trackUrl = intent.getStringExtra(EXTRA_TRACK_URL) ?: ""
                val trackTitle = intent.getStringExtra(EXTRA_TRACK_TITLE) ?: "Focus ON"
                if (duration > 0) startFocus(duration, trackUrl, trackTitle)
            }
        }
        return START_STICKY
    }

    private fun startFocus(durationSec: Int, trackUrl: String, trackTitle: String) {
        totalSec = durationSec
        remainingMs = durationSec * 1000L

        // Стартуємо як foreground з початковим сповіщенням
        startForeground(NOTIF_ID, buildNotification(formatTime(durationSec), trackTitle))

        // Запускаємо аудіо якщо є URL
        if (trackUrl.isNotEmpty()) {
            startPlayback(trackUrl)
        }

        // Таймер з التحненням сповіщення кожну секунду
        timer?.cancel()
        timer = object : CountDownTimer(remainingMs, 1000) {
            override fun onTick(ms: Long) {
                remainingMs = ms
                val sec = (ms / 1000).toInt()
                val nm = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
                nm.notify(NOTIF_ID, buildNotification(formatTime(sec), trackTitle))
                // інформуємо WebView про прогрес
                FocusBridge.tick?.invoke(sec)
            }
            override fun onFinish() {
                FocusBridge.finished?.invoke()
                stopPlayback()
                showFinishedNotification(trackTitle)
                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf()
            }
        }.start()
    }

    private fun stopFocus() {
        timer?.cancel()
        timer = null
        stopPlayback()
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    // ── Відтворення аудіо ────────────────────────────────────────
    private fun startPlayback(url: String) {
        try {
            stopPlayback()
            player = MediaPlayer().apply {
                setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_MEDIA)
                        .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                        .build()
                )
                setDataSource(url)
                isLooping = true
                setOnPreparedListener { it.start() }
                setOnErrorListener { mp, what, extra ->
                    Log.e(TAG, "MediaPlayer error: $what/$extra")
                    true
                }
                prepareAsync()
            }
        } catch (e: Exception) {
            Log.e(TAG, "playback start error", e)
        }
    }

    private fun stopPlayback() {
        try {
            player?.let { if (it.isPlaying) it.stop(); it.release() }
        } catch (e: Exception) {}
        player = null
    }

    // ── Сповіщення ───────────────────────────────────────────────
    private fun buildNotification(timeText: String, trackTitle: String): Notification {
        val contentIntent = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val stopIntent = PendingIntent.getService(
            this, 1,
            Intent(this, FocusService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("🎯 $timeText — $trackTitle")
            .setContentText("Сесія фокусу триває")
            .setSmallIcon(android.R.drawable.ic_media_play)
            .setOngoing(true)
            .setContentIntent(contentIntent)
            .addAction(android.R.drawable.ic_media_pause, "Зупинити", stopIntent)
            .setOnlyAlertOnce(true)
            .build()
    }

    private fun showFinishedNotification(trackTitle: String) {
        val nm = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
        val notif = NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("✅ Сесію завершено!")
            .setContentText("$trackTitle — час вийшло")
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setAutoCancel(true)
            .build()
        nm.notify(NOTIF_ID + 1, notif)
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "Таймер фокусу",
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = "Показує час сесії та тримає музику у фоні"
            }
            val nm = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
            nm.createNotificationChannel(channel)
        }
    }

    private fun formatTime(sec: Int): String {
        val m = sec / 60
        val s = sec % 60
        return "%02d:%02d".format(m, s)
    }

    override fun onDestroy() {
        timer?.cancel()
        stopPlayback()
        super.onDestroy()
    }
}

/**
 * Міст між service та WebView.
 * MainActivity реєструє callback'и, JS викликає FocusBridge через JavascriptInterface.
 */
object FocusBridge {
    var tick: ((Int) -> Unit)? = null
    var finished: (() -> Unit)? = null
}
