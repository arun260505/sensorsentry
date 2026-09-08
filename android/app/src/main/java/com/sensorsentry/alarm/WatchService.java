package com.sensorsentry.alarm;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.media.AudioAttributes;
import android.media.Ringtone;
import android.media.RingtoneManager;
import android.net.Uri;
import android.os.Build;
import android.os.IBinder;
import android.os.VibrationEffect;
import android.os.Vibrator;
import android.util.Log;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.Iterator;

/**
 * Watches one SensorSentry console and raises the alarm.
 *
 * <p>The fleet manager is not sitting in front of the operator screen. They
 * are somewhere else entirely, and the first they should know about a spoofed
 * lorry is their phone going off in their pocket. That is the whole app.
 *
 * <p><b>Polling, not the event stream.</b> The console publishes server-sent
 * events and reading them would be tidier, but it would also mean owning
 * reconnect logic on a phone that moves between cells and sleeps its radio. A
 * one-second poll is self-healing by construction: every request either works
 * or is simply retried a second later, and a second of latency is nothing
 * against a detection that takes ten.
 *
 * <p><b>It reports, it does not decide.</b> Everything shown here is read off
 * the console's own snapshot. The phone runs no detection of its own — if it
 * did, there would be two systems that could disagree about whether a lorry
 * was being stolen.
 */
public class WatchService extends Service {

    public static final String ACTION_UPDATE = "com.sensorsentry.alarm.UPDATE";
    public static final String EXTRA_HEADLINE = "headline";
    public static final String EXTRA_DETAIL = "detail";
    public static final String EXTRA_ALERT = "alert";
    public static final String EXTRA_CONNECTED = "connected";

    static final String CHANNEL_ALERT = "sensorsentry.alert";
    static final String CHANNEL_WATCHING = "sensorsentry.watching";
    static final int NOTE_WATCHING = 1;
    static final int NOTE_ALERT = 2;

    private static final String TAG = "SensorSentry";
    private static final long POLL_MS = 1000L;
    private static final int TIMEOUT_MS = 3000;

    private volatile boolean running = false;
    private Thread worker;
    private String server = "";

    /** True while the console is already alerting, so one incident rings once
     *  rather than every second for as long as it lasts. */
    private boolean alerting = false;

    @Override
    public void onCreate() {
        super.onCreate();
        createChannels();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && intent.getStringExtra("server") != null) {
            server = intent.getStringExtra("server");
        }
        startForeground(NOTE_WATCHING, watchingNotification("Connecting…"));

        if (!running) {
            running = true;
            worker = new Thread(this::loop, "sensorsentry-poll");
            worker.start();
        }
        // Restart if Android kills us; the point of the app is to be running.
        return START_STICKY;
    }

    @Override
    public void onDestroy() {
        running = false;
        if (worker != null) worker.interrupt();
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    // --- the loop ----------------------------------------------------------

    private void loop() {
        while (running) {
            try {
                JSONObject snapshot = fetch();
                if (snapshot == null) {
                    publish("Cannot reach the console", server, false, false);
                } else {
                    read(snapshot);
                }
            } catch (Exception e) {
                Log.w(TAG, "poll failed", e);
                publish("Cannot reach the console", server, false, false);
            }
            try {
                Thread.sleep(POLL_MS);
            } catch (InterruptedException e) {
                return;
            }
        }
    }

    private JSONObject fetch() throws Exception {
        HttpURLConnection connection =
                (HttpURLConnection) new URL("http://" + server + "/snapshot").openConnection();
        connection.setConnectTimeout(TIMEOUT_MS);
        connection.setReadTimeout(TIMEOUT_MS);
        connection.setRequestMethod("GET");
        try {
            if (connection.getResponseCode() != 200) return null;
            StringBuilder body = new StringBuilder();
            try (BufferedReader reader = new BufferedReader(
                    new InputStreamReader(connection.getInputStream()))) {
                String line;
                while ((line = reader.readLine()) != null) body.append(line);
            }
            return new JSONObject(body.toString());
        } finally {
            connection.disconnect();
        }
    }

    /** Turn one snapshot into the single sentence this app exists to show. */
    private void read(JSONObject snapshot) {
        JSONObject vehicles = snapshot.optJSONObject("vehicles");
        if (vehicles == null || vehicles.length() == 0) {
            alerting = false;
            publish("No vehicle running", "Start a run on the console", false, true);
            return;
        }

        Iterator<String> keys = vehicles.keys();
        JSONObject state = vehicles.optJSONObject(keys.next()).optJSONObject("state");
        if (state == null) return;

        String vehicle = state.optString("vehicle_id", "vehicle");
        boolean alert = "ALERT".equals(state.optString("state"));

        if (!alert) {
            alerting = false;
            publish("All sensors agree", vehicle + " · nothing wrong", false, true);
            return;
        }

        JSONObject blame = state.optJSONObject("blame");
        JSONObject cause = state.optJSONObject("cause");
        String guilty = blame == null ? null : blame.optString("guilty", null);
        String label = cause == null ? "" : cause.optString("label", "");
        String action = cause == null ? "" : cause.optString("action", "");

        String headline = headline(label, guilty);
        String detail = vehicle + (action.isEmpty() ? "" : " · " + action);
        publish(headline, detail, true, true);

        // Ring once per incident, not once per second.
        if (!alerting) {
            alerting = true;
            raise(headline, detail);
        }
    }

    /**
     * The one line the manager reads.
     *
     * <p>Named after the thing that happened rather than after the field it
     * came out of. "gnss/attack" means something to us and nothing to the
     * person whose lorry it is.
     *
     * <p>An unnamed sensor is reported as unnamed. The console refuses to
     * guess which sensor is lying when the evidence will not support it, and
     * a phone that turned that into a confident accusation would be undoing
     * the most careful thing the system does.
     */
    private String headline(String cause, String guilty) {
        boolean named = guilty != null && !guilty.isEmpty()
                && !"cannot_isolate".equals(guilty) && !"null".equals(guilty);
        if (!named) return "SOMETHING IS WRONG";
        switch (cause) {
            case "attack":
                return "gnss".equals(guilty)
                        ? "GPS SPOOFING DETECTED" : "TAMPERED · " + friendly(guilty);
            case "fault":
                return friendly(guilty).toUpperCase() + " HAS FAILED";
            case "interference":
                return "INTERFERENCE ON THE " + friendly(guilty).toUpperCase();
            default:
                return "SOMETHING IS WRONG";
        }
    }

    private String friendly(String sensor) {
        switch (sensor) {
            case "gnss": return "GPS";
            case "mag":  return "compass";
            case "baro": return "altimeter";
            case "odom": return "wheels";
            case "imu":  return "motion sensor";
            default:     return sensor;
        }
    }

    // --- telling somebody --------------------------------------------------

    private void publish(String headline, String detail, boolean alert, boolean connected) {
        Intent update = new Intent(ACTION_UPDATE);
        update.setPackage(getPackageName());
        update.putExtra(EXTRA_HEADLINE, headline);
        update.putExtra(EXTRA_DETAIL, detail);
        update.putExtra(EXTRA_ALERT, alert);
        update.putExtra(EXTRA_CONNECTED, connected);
        sendBroadcast(update);

        NotificationManager manager = getSystemService(NotificationManager.class);
        manager.notify(NOTE_WATCHING, watchingNotification(
                connected ? headline : "Cannot reach the console"));
    }

    /** The alarm: a heads-up notification, the alarm tone, and a buzz. */
    private void raise(String headline, String detail) {
        NotificationManager manager = getSystemService(NotificationManager.class);

        Intent open = new Intent(this, MainActivity.class);
        open.setFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        PendingIntent tap = PendingIntent.getActivity(
                this, 0, open, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);

        Notification note = new Notification.Builder(this, CHANNEL_ALERT)
                .setSmallIcon(R.drawable.ic_alert)
                .setContentTitle(headline)
                .setContentText(detail)
                .setStyle(new Notification.BigTextStyle().bigText(detail))
                .setCategory(Notification.CATEGORY_ALARM)
                .setPriority(Notification.PRIORITY_MAX)
                .setAutoCancel(true)
                .setContentIntent(tap)
                .build();
        manager.notify(NOTE_ALERT, note);

        // The channel already carries a sound, but a pocketed phone in a noisy
        // hall needs the alarm stream rather than the notification stream.
        try {
            Uri tone = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM);
            if (tone == null) tone = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION);
            Ringtone ringtone = RingtoneManager.getRingtone(getApplicationContext(), tone);
            if (ringtone != null) {
                ringtone.setAudioAttributes(new AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_ALARM)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                        .build());
                ringtone.play();
            }
        } catch (Exception e) {
            Log.w(TAG, "no alarm tone", e);
        }

        Vibrator vibrator = getSystemService(Vibrator.class);
        if (vibrator != null && vibrator.hasVibrator()) {
            vibrator.vibrate(VibrationEffect.createWaveform(
                    new long[]{0, 400, 200, 400, 200, 700}, -1));
        }
    }

    private Notification watchingNotification(String text) {
        Intent open = new Intent(this, MainActivity.class);
        PendingIntent tap = PendingIntent.getActivity(
                this, 0, open, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        return new Notification.Builder(this, CHANNEL_WATCHING)
                .setSmallIcon(R.drawable.ic_alert)
                .setContentTitle("SensorSentry")
                .setContentText(text)
                .setOngoing(true)
                .setContentIntent(tap)
                .build();
    }

    private void createChannels() {
        NotificationManager manager = getSystemService(NotificationManager.class);

        // Two channels, because they are two different kinds of interruption.
        // One is a quiet line in the shade saying the app is alive; the other
        // is meant to make somebody reach into their pocket.
        NotificationChannel watching = new NotificationChannel(
                CHANNEL_WATCHING, "Watching", NotificationManager.IMPORTANCE_LOW);
        watching.setDescription("Shown while the app is connected to a console.");
        watching.setShowBadge(false);
        manager.createNotificationChannel(watching);

        NotificationChannel alert = new NotificationChannel(
                CHANNEL_ALERT, "Sensor alerts", NotificationManager.IMPORTANCE_HIGH);
        alert.setDescription("A vehicle's sensors have stopped agreeing.");
        alert.enableVibration(true);
        alert.setVibrationPattern(new long[]{0, 400, 200, 400});
        Uri tone = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM);
        if (tone != null) {
            alert.setSound(tone, new AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ALARM)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                    .build());
        }
        manager.createNotificationChannel(alert);

        if (Build.VERSION.SDK_INT >= 0) {
            manager.deleteNotificationChannel("sensorsentry.old");
        }
    }
}
