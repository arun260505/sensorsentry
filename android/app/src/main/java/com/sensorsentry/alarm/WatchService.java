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
    // Trail data for the map view
    public static final String EXTRA_TRAILS_GNSS    = "trails_gnss";
    public static final String EXTRA_TRAILS_WITNESS  = "trails_witness";
    public static final String EXTRA_GAP_M           = "gap_m";
    // Rich map and status panel extras
    public static final String EXTRA_BASEMAP         = "basemap";       // roads JSON, sent once
    public static final String EXTRA_HEADING_DEG     = "heading_deg";   // float, gyro heading
    public static final String EXTRA_VERT_M          = "vert_m";        // float, vertical residual
    public static final String EXTRA_COMPASS_DEG     = "compass_deg";   // float, compass offset °
    public static final String EXTRA_CAUSE_LABEL     = "cause_label";   // "attack" / "fault" / ""
    public static final String EXTRA_CAUSE_REASON    = "cause_reason";  // human-readable sentence
    public static final String EXTRA_VEHICLE_ID      = "vehicle_id";    // "DRONE-07" etc.
    public static final String EXTRA_SIGMA_M         = "sigma_m";       // float, position sigma (Our uncertainty)
    public static final String EXTRA_RATIO           = "ratio_x";       // float, worst pair ratio


    static final String CHANNEL_ALERT = "sensorsentry.alert.v3";
    static final String CHANNEL_WATCHING = "sensorsentry.watching";
    static final int NOTE_WATCHING = 1;
    static final int NOTE_ALERT = 2;

    private static final String TAG = "SensorSentry";

    /**
     * The alert tone, shipped inside the app.
     *
     * <p>It used to ask the phone for its default notification sound. On the
     * handset this was built for that setting is empty — so the channel was
     * created pointing at a URI that resolves to nothing, and the alarm was
     * completely silent while every other sign said it was working. Nothing in
     * the notification dump looked wrong; the sound simply did not exist.
     *
     * <p>Depending on a device setting for the one thing this app has to do
     * was the mistake. A tone in the APK sounds the same on any phone whatever
     * its owner has configured, which matters again when the demo is run from
     * somebody else's handset.
     */
    private Uri alertTone() {
        return Uri.parse("android.resource://" + getPackageName() + "/" + R.raw.alert);
    }
    private static final long POLL_MS = 1000L;
    private static final int TIMEOUT_MS = 3000;

    private volatile boolean running = false;
    private Thread worker;
    private String server = "";

    /** True while the console is already alerting, so one incident rings once
     *  rather than every second for as long as it lasts. */
    private boolean alerting = false;

    // Basemap — fetched once from /basemap, then broadcast on the next tick.
    private String  basemapJson  = null;
    private boolean basemapSent  = false;
    public static String cachedBasemapJson = null;
    public static boolean hasNewBasemap = false;

    // Per-broadcast state extracted from the snapshot.
    private float  lastHeadingDeg  = Float.NaN;
    private float  lastVertM       = 0f;
    private String lastCauseLabel  = "";
    private String lastCauseReason = "";
    private float  lastCompassDeg  = Float.NaN;
    private String lastVehicleId   = "VEHICLE";
    private float  lastSigmaM      = 0f;
    private float  lastRatio       = 0f;

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
                    publish("Cannot reach the console", server, false, false, "[]", "[]", 0f);
                } else {
                    if (basemapJson == null) fetchBasemap(); // once only
                    read(snapshot);
                }
            } catch (Exception e) {
                Log.w(TAG, "poll failed", e);
                publish("Cannot reach the console", server, false, false, "[]", "[]", 0f);
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

    /** Fetch road/place data once from /basemap and cache it. */
    private void fetchBasemap() {
        try {
            HttpURLConnection c =
                    (HttpURLConnection) new URL("http://" + server + "/basemap").openConnection();
            c.setConnectTimeout(4000);
            c.setReadTimeout(8000);
            c.setRequestMethod("GET");
            try {
                if (c.getResponseCode() == 200) {
                    StringBuilder sb = new StringBuilder();
                    try (BufferedReader r = new BufferedReader(
                            new InputStreamReader(c.getInputStream()))) {
                        String line;
                        while ((line = r.readLine()) != null) sb.append(line);
                    }
                    basemapJson = sb.toString();
                }
            } finally {
                c.disconnect();
            }
        } catch (Exception e) {
            Log.d(TAG, "basemap fetch skipped: " + e.getMessage());
        }
    }

    /** Turn one snapshot into the single sentence this app exists to show. */
    private void read(JSONObject snapshot) {
        // Extract trails from the snapshot's focus vehicle — same vehicle the
        // detail panels describe on the console. Passed to the MapView in every
        // broadcast so the map tracks the vehicle in real time, not just on alert.
        JSONObject trailsObj  = snapshot.optJSONObject("trails");
        org.json.JSONArray gnssArr    = trailsObj == null ? null : trailsObj.optJSONArray("gnss");
        org.json.JSONArray witnessArr = trailsObj == null ? null : trailsObj.optJSONArray("witness");
        String gnssTrailJson    = gnssArr    != null ? gnssArr.toString()    : "[]";
        String witnessTrailJson = witnessArr != null ? witnessArr.toString() : "[]";

        // Separation in metres: the residual's horizontal_m is the distance
        // between where GPS says it is and where the witness estimate places it.
        float gapM = 0f;
        JSONObject focusState = snapshot.optJSONObject("state");
        if (focusState != null) {
            JSONObject residual = focusState.optJSONObject("residual");
            if (residual != null) {
                gapM = (float) residual.optDouble("horizontal_m", 0.0);
            }
        }

        JSONObject vehicles = snapshot.optJSONObject("vehicles");
        if (vehicles == null || vehicles.length() == 0) {
            if (alerting) {
                getSystemService(NotificationManager.class).cancel(NOTE_ALERT);
            }
            alerting = false;
            publish("No vehicle running", "Start a run on the console", false, true,
                    gnssTrailJson, witnessTrailJson, gapM);
            return;
        }

        Iterator<String> keys = vehicles.keys();
        JSONObject state = vehicles.optJSONObject(keys.next()).optJSONObject("state");
        if (state == null) return;

        String vehicle = state.optString("vehicle_id", "vehicle");
        boolean alert = "ALERT".equals(state.optString("state"));

        // --- Extract rich map extras from the snapshot -----------------------
        lastVehicleId = vehicle;

        // gyro_heading_deg is a top-level field on the state JSON dict
        // (set from crossvalidator.gyro_heading_deg in pipeline.py).
        if (!state.isNull("gyro_heading_deg")) {
            double hd = state.optDouble("gyro_heading_deg", Double.NaN);
            lastHeadingDeg = Double.isNaN(hd) ? Float.NaN : (float) hd;
        }

        // vertical_m, sigma_m and ratio live in the residual sub-object.
        // We already have the residual from focusState above — re-read from
        // the per-vehicle state block to be safe (they are the same vehicle).
        JSONObject residualBlk = state.optJSONObject("residual");
        if (residualBlk != null) {
            double vt = residualBlk.optDouble("vertical_m", Double.NaN);
            lastVertM = Double.isNaN(vt) ? 0f : (float) vt;
            double sig = residualBlk.optDouble("sigma_m", Double.NaN);
            lastSigmaM = Double.isNaN(sig) ? 0f : (float) sig;
            double rat = residualBlk.optDouble("ratio", Double.NaN);
            lastRatio = Double.isNaN(rat) ? 0f : (float) rat;
        }

        // Compass heading offset: look for the heading_offset pair score
        // so we can show "N° off" in the sensor card, matching the target UI.
        org.json.JSONArray pairs = state.optJSONArray("pairs");
        if (pairs != null) {
            for (int pi = 0; pi < pairs.length(); pi++) {
                try {
                    JSONObject pair = pairs.getJSONObject(pi);
                    if (pair.optString("key", "").endsWith(":heading_offset")) {
                        double val = pair.optDouble("value", Double.NaN);
                        lastCompassDeg = Double.isNaN(val) ? Float.NaN : (float) val;
                        break;
                    }
                } catch (Exception ignored) {}
            }
        }

        if (!alert) {
            // Take the old alarm out of the shade when the vehicle recovers.
            // Left there, the previous incident's banner sits over the next
            // clean run — and on stage that reads as an alert that has not
            // cleared rather than one that has.
            if (alerting) {
                getSystemService(NotificationManager.class).cancel(NOTE_ALERT);
            }
            alerting = false;
            publish("All sensors agree", vehicle + " · nothing wrong", false, true,
                    gnssTrailJson, witnessTrailJson, gapM);
            return;
        }

        JSONObject blame = state.optJSONObject("blame");
        JSONObject cause = state.optJSONObject("cause");
        String guilty = blame == null ? null : blame.optString("guilty", null);
        String label = cause == null ? "" : cause.optString("label", "");
        String action = cause == null ? "" : cause.optString("action", "");

        lastCauseLabel  = label;
        lastCauseReason = action;

        String headline = headline(label, guilty);
        String detail = vehicle + (action.isEmpty() ? "" : " · " + action);
        publish(headline, detail, true, true, gnssTrailJson, witnessTrailJson, gapM);

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

    private void publish(String headline, String detail, boolean alert, boolean connected,
                         String gnssJson, String witnessJson, float gapM) {
        Intent update = new Intent(ACTION_UPDATE);
        update.setPackage(getPackageName());
        update.putExtra(EXTRA_HEADLINE,      headline);
        update.putExtra(EXTRA_DETAIL,        detail);
        update.putExtra(EXTRA_ALERT,         alert);
        update.putExtra(EXTRA_CONNECTED,     connected);
        update.putExtra(EXTRA_TRAILS_GNSS,   gnssJson);
        update.putExtra(EXTRA_TRAILS_WITNESS, witnessJson);
        update.putExtra(EXTRA_GAP_M,          gapM);
        // Rich map extras — sent on every tick so the UI always has fresh data.
        update.putExtra(EXTRA_VEHICLE_ID,    lastVehicleId);
        update.putExtra(EXTRA_HEADING_DEG,   lastHeadingDeg);
        update.putExtra(EXTRA_VERT_M,        lastVertM);
        update.putExtra(EXTRA_COMPASS_DEG,   lastCompassDeg);
        update.putExtra(EXTRA_CAUSE_LABEL,   lastCauseLabel);
        update.putExtra(EXTRA_CAUSE_REASON,  lastCauseReason);
        update.putExtra(EXTRA_SIGMA_M,       lastSigmaM);
        update.putExtra(EXTRA_RATIO,         lastRatio);
        // Basemap: pass via static field to avoid TransactionTooLargeException
        // since the JSON can be > 512KB, which drops the Intent.
        if (basemapJson != null && !basemapSent) {
            cachedBasemapJson = basemapJson;
            hasNewBasemap = true;
            basemapSent = true;
        }
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

        // Two separate choices, and they are not the same thing.
        //
        // WHAT it sounds like: the notification tone, not the alarm-clock one.
        // An alarm tone is designed to wake somebody from sleep and keeps
        // going; this is an alert about a lorry, and it should sound like one.
        //
        // HOW LOUD: the alarm stream regardless, because that is the one
        // channel a phone on silent still plays. A pocketed phone in a noisy
        // hall that politely says nothing has failed at its only job.
        try {
            Ringtone ringtone = RingtoneManager.getRingtone(getApplicationContext(), alertTone());
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
        alert.setSound(alertTone(), new AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_ALARM)
                .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                .build());
        manager.createNotificationChannel(alert);

        // The first version of this channel carried the alarm-clock tone, and
        // a channel's sound is fixed once Android has seen it — changing it in
        // code does nothing to an installed app. Hence the v2 id above and
        // this: drop the old one so it does not linger in the phone's
        // notification settings looking like a duplicate.
        manager.deleteNotificationChannel("sensorsentry.alert");
        manager.deleteNotificationChannel("sensorsentry.alert.v2");
    }
}
