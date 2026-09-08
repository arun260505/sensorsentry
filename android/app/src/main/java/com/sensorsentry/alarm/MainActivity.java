package com.sensorsentry.alarm;

import android.Manifest;
import android.app.Activity;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Typeface;
import android.os.Build;
import android.os.Bundle;
import android.text.InputType;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONException;

/**
 * Main screen: status panel below a live map, matching the web console layout.
 *
 * <p>The map occupies the top portion; below it a scrollable panel shows the
 * same information hierarchy as the web console — status badge, headline,
 * description, the plain-English gap distance, and the numeric gap detail
 * (Our uncertainty / Ratio / Vertical) — so a judge looking at the phone
 * can read the same evidence structure they see on the laptop.
 */
public class MainActivity extends Activity {

    private static final String PREFS = "sensorsentry";
    private static final String KEY_SERVER = "server";
    private static final String DEFAULT_SERVER = "172.16.130.172:8080";

    // --- Core UI references ------------------------------------------------
    private TextView     headline;
    private TextView     detail;
    private TextView     connection;
    private EditText     serverField;
    private Button       toggle;
    private MapView      mapView;
    private boolean      watching = false;

    // --- Status-panel views ------------------------------------------------
    private TextView statusBadge;
    private TextView statusHeadline;
    private TextView statusDesc;
    private TextView gapDist;
    private TextView gapUncertainty;
    private TextView gapRatioView;
    private TextView gapVertical;

    // -----------------------------------------------------------------------

    private final BroadcastReceiver updates = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            // Hearing from the service at all means it is running. Reopening
            // the app starts a fresh Activity with watching=false, so the
            // address box and "Watch" button come back — the heartbeat is the
            // honest answer.
            if (!watching) markWatching();

            boolean alert     = intent.getBooleanExtra(WatchService.EXTRA_ALERT,     false);
            boolean connected = intent.getBooleanExtra(WatchService.EXTRA_CONNECTED, false);
            show(intent.getStringExtra(WatchService.EXTRA_HEADLINE),
                 intent.getStringExtra(WatchService.EXTRA_DETAIL), alert, connected);

            // --- Map trails ------------------------------------------------
            String gnssJson    = intent.getStringExtra(WatchService.EXTRA_TRAILS_GNSS);
            String witnessJson = intent.getStringExtra(WatchService.EXTRA_TRAILS_WITNESS);
            float  gapM        = intent.getFloatExtra(WatchService.EXTRA_GAP_M, 0f);
            if (gnssJson != null && witnessJson != null) {
                try {
                    mapView.setTrails(
                            new JSONArray(gnssJson), new JSONArray(witnessJson), gapM);
                } catch (JSONException ignored) {}
            }

            // --- Map overlays ----------------------------------------------
            if (WatchService.hasNewBasemap) {
                mapView.setBasemap(WatchService.cachedBasemapJson);
                WatchService.hasNewBasemap = false;
            }

            String vehicleId = intent.getStringExtra(WatchService.EXTRA_VEHICLE_ID);
            if (vehicleId != null && !vehicleId.isEmpty()) mapView.setVehicleId(vehicleId);

            float headingDeg = intent.getFloatExtra(WatchService.EXTRA_HEADING_DEG, Float.NaN);
            mapView.setHeading(headingDeg);

            float vertM      = intent.getFloatExtra(WatchService.EXTRA_VERT_M, 0f);
            float compassDeg = intent.getFloatExtra(WatchService.EXTRA_COMPASS_DEG, Float.NaN);
            mapView.setSensorInfo(gapM, vertM, compassDeg);

            // --- Status-panel gap detail -----------------------------------
            float sigmaM = intent.getFloatExtra(WatchService.EXTRA_SIGMA_M, 0f);
            float ratio  = intent.getFloatExtra(WatchService.EXTRA_RATIO,   0f);
            updateGapPanel(gapM, sigmaM, ratio, vertM);
        }
    };

    // --- Lifecycle ---------------------------------------------------------

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        setContentView(buildView());

        SharedPreferences prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        serverField.setText(prefs.getString(KEY_SERVER, DEFAULT_SERVER));

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
                && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
                   != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 1);
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        IntentFilter filter = new IntentFilter(WatchService.ACTION_UPDATE);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            registerReceiver(updates, filter, Context.RECEIVER_NOT_EXPORTED);
        } else {
            registerReceiver(updates, filter);
        }
    }

    @Override
    protected void onPause() {
        super.onPause();
        try { unregisterReceiver(updates); } catch (IllegalArgumentException ignored) {}
    }

    private void markWatching() {
        watching = true;
        toggle.setText("Stop");
        serverField.setVisibility(View.GONE);
    }

    // --- Screen layout -----------------------------------------------------

    private FrameLayout outerRoot;
    private LinearLayout innerContent;
    private boolean isMapFullscreen = false;
    private LinearLayout body;

    private View buildView() {
        outerRoot = new FrameLayout(this);
        outerRoot.setBackgroundColor(0xFF0F1C25);

        innerContent = new LinearLayout(this);
        innerContent.setOrientation(LinearLayout.VERTICAL);
        outerRoot.addView(innerContent, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT));

        // ── Scrollable body (everything above the footer button) ─────────────
        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        innerContent.addView(scroll, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f));

        body = new LinearLayout(this);
        body.setOrientation(LinearLayout.VERTICAL);
        body.setPadding(dp(20), dp(36), dp(20), dp(12));
        scroll.addView(body);

        // ── Header ───────────────────────────────────────────────────────────
        TextView brand = new TextView(this);
        brand.setText("SENSORSENTRY");
        brand.setTextColor(0xFF7F97A6);
        brand.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f);
        brand.setLetterSpacing(0.18f);
        body.addView(brand);

        headline = new TextView(this);
        headline.setText("Not watching");
        headline.setTextColor(Color.WHITE);
        headline.setTextSize(TypedValue.COMPLEX_UNIT_SP, 34f);
        headline.setLineSpacing(0f, 1.05f);
        headline.setPadding(0, dp(28), 0, 0);
        body.addView(headline);

        detail = new TextView(this);
        detail.setText("Enter the console address and press Watch.");
        detail.setTextColor(0xFFA8BCC9);
        detail.setTextSize(TypedValue.COMPLEX_UNIT_SP, 16f);
        detail.setPadding(0, dp(14), 0, dp(10));
        body.addView(detail);

        // ── Map card ─────────────────────────────────────────────────────────
        mapView = new MapView(this);
        LinearLayout.LayoutParams mapLp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, dp(270));
        mapLp.topMargin    = dp(8);
        mapLp.bottomMargin = dp(0);
        body.addView(mapView, mapLp);

        // ── Status panel ─────────────────────────────────────────────────────
        divider(body, dp(12), dp(0));

        statusBadge = new TextView(this);
        statusBadge.setText("WAITING");
        statusBadge.setTextColor(0xFF7F97A6);
        statusBadge.setTextSize(TypedValue.COMPLEX_UNIT_SP, 11f);
        statusBadge.setLetterSpacing(0.15f);
        statusBadge.setTypeface(Typeface.DEFAULT_BOLD);
        statusBadge.setPadding(0, dp(14), 0, dp(2));
        body.addView(statusBadge);

        statusHeadline = new TextView(this);
        statusHeadline.setText("Not watching");
        statusHeadline.setTextColor(0xFFA8BCC9);
        statusHeadline.setTextSize(TypedValue.COMPLEX_UNIT_SP, 24f);
        statusHeadline.setTypeface(Typeface.DEFAULT_BOLD);
        statusHeadline.setPadding(0, dp(2), 0, 0);
        body.addView(statusHeadline);

        statusDesc = new TextView(this);
        statusDesc.setText("Connect to see live sensor data.");
        statusDesc.setTextColor(0xFF7F97A6);
        statusDesc.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14f);
        statusDesc.setPadding(0, dp(6), 0, dp(8));
        body.addView(statusDesc);

        divider(body, dp(4), dp(4));

        // Gap plain-English distance line
        gapDist = new TextView(this);
        gapDist.setText("— metres between where GPS says it is and where it worked out it is");
        gapDist.setTextColor(0xFF7F97A6);
        gapDist.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f);
        gapDist.setPadding(0, dp(10), 0, dp(10));
        body.addView(gapDist);

        divider(body, dp(0), dp(0));

        // ── THE GAP, IN DETAIL ───────────────────────────────────────────────
        sectionHeader(body, "THE GAP, IN DETAIL");

        gapUncertainty = gapRow(body, "Our uncertainty");
        gapRatioView   = gapRow(body, "Ratio");
        gapVertical    = gapRow(body, "Vertical");

        divider(body, dp(8), dp(0));

        // ── SENSORS ──────────────────────────────────────────────────────────
        sectionHeader(body, "SENSORS");

        TextView sensorsNote = new TextView(this);
        sensorsNote.setText("See the sensor card on the map for live health.");
        sensorsNote.setTextColor(0xFF3A5060);
        sensorsNote.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f);
        sensorsNote.setPadding(0, dp(4), 0, dp(20));
        body.addView(sensorsNote);

        // ── Footer (always visible at the bottom, outside the scroll) ────────
        LinearLayout footer = new LinearLayout(this);
        footer.setOrientation(LinearLayout.VERTICAL);
        footer.setPadding(dp(20), dp(8), dp(20), dp(20));
        innerContent.addView(footer, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT));

        connection = new TextView(this);
        connection.setTextColor(0xFF7F97A6);
        connection.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f);
        connection.setText("not connected");
        connection.setPadding(0, 0, 0, dp(8));
        footer.addView(connection);

        serverField = new EditText(this);
        serverField.setHint("laptop address, e.g. 192.168.1.5:8080");
        serverField.setInputType(InputType.TYPE_TEXT_VARIATION_URI);
        serverField.setTextColor(Color.WHITE);
        serverField.setHintTextColor(0xFF5C7080);
        serverField.setSingleLine(true);
        footer.addView(serverField);

        toggle = new Button(this);
        toggle.setText("Watch");
        toggle.setAllCaps(false);
        toggle.setTextSize(TypedValue.COMPLEX_UNIT_SP, 18f);
        toggle.setGravity(Gravity.CENTER);
        toggle.setOnClickListener(v -> onToggle());
        LinearLayout.LayoutParams btnLp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, dp(56));
        btnLp.topMargin = dp(14);
        footer.addView(toggle, btnLp);

        mapView.setOnMapClickListener(this::toggleFullscreenMap);

        return outerRoot;
    }

    // --- Layout helpers ----------------------------------------------------

    private void divider(LinearLayout parent, int topMargin, int bottomMargin) {
        View v = new View(this);
        v.setBackgroundColor(0xFF1A3040);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 1);
        lp.topMargin    = topMargin;
        lp.bottomMargin = bottomMargin;
        parent.addView(v, lp);
    }

    private void sectionHeader(LinearLayout parent, String text) {
        TextView tv = new TextView(this);
        tv.setText(text);
        tv.setTextColor(0xFF3A6073);
        tv.setTextSize(TypedValue.COMPLEX_UNIT_SP, 11f);
        tv.setLetterSpacing(0.12f);
        tv.setTypeface(Typeface.DEFAULT_BOLD);
        tv.setPadding(0, dp(12), 0, dp(4));
        parent.addView(tv);
    }

    /**
     * Label + right-aligned value row. Returns the value TextView so callers
     * can update it from the broadcast.
     */
    private TextView gapRow(LinearLayout parent, String label) {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setPadding(0, dp(5), 0, dp(5));
        parent.addView(row, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT));

        TextView lv = new TextView(this);
        lv.setText(label);
        lv.setTextColor(0xFF5FBFAA);
        lv.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14f);
        row.addView(lv, new LinearLayout.LayoutParams(
                0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));

        TextView rv = new TextView(this);
        rv.setText("—");
        rv.setTextColor(0xFFA8BCC9);
        rv.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14f);
        row.addView(rv);

        return rv;
    }

    // --- State changes -----------------------------------------------------

    private void onToggle() {
        Intent service = new Intent(this, WatchService.class);
        if (watching) {
            stopService(service);
            watching = false;
            toggle.setText("Watch");
            serverField.setVisibility(View.VISIBLE);
            show("Not watching", "Press Watch to reconnect.", false, false);
            updateGapPanel(0, 0, 0, 0);
            return;
        }
        String server = serverField.getText().toString().trim();
        if (server.isEmpty()) {
            detail.setText("Put the laptop's address in the box first.");
            return;
        }
        getSharedPreferences(PREFS, MODE_PRIVATE)
                .edit().putString(KEY_SERVER, server).apply();
        service.putExtra("server", server);
        startForegroundService(service);
        markWatching();
        show("Connecting…", "", false, false);
    }

    /**
     * Paint the screen and status-panel text for the current system state.
     *
     * <p>An alert turns the whole background red so a glance across a room
     * answers the question before anything is read.
     */
    private void show(String head, String note, boolean alert, boolean connected) {
        if (head != null) headline.setText(head);
        if (note != null) detail.setText(note);

        outerRoot.setBackgroundColor(alert ? 0xFF8C1D13 : 0xFF0F1C25);

        connection.setText(connected ? "connected" : "not connected");
        connection.setTextColor(connected ? 0xFF5FBF8F : 0xFFC9736A);

        // Status badge + headline + description in the panel below the map.
        if (alert) {
            statusBadge.setText("ALERT");
            statusBadge.setTextColor(0xFFCC4030);
            statusHeadline.setText(head != null ? head : "ALERT");
            statusHeadline.setTextColor(0xFFFF7060);
            statusDesc.setText(
                    "A sensor cross-check has failed. Follow the action procedure.");
            statusDesc.setTextColor(0xFFA8BCC9);
        } else if (connected && "No vehicle running".equals(head)) {
            statusBadge.setText("WAITING");
            statusBadge.setTextColor(0xFF7F97A6);
            statusHeadline.setText("No run");
            statusHeadline.setTextColor(0xFFA8BCC9);
            statusDesc.setText("Start a scenario from the control screen.");
            statusDesc.setTextColor(0xFF7F97A6);
        } else if (connected) {
            statusBadge.setText("ALL QUIET");
            statusBadge.setTextColor(0xFF5FBF8F);
            statusHeadline.setText("Everything agrees");
            statusHeadline.setTextColor(0xFF5FBF8F);
            statusDesc.setText(
                    "All cross-checks agree. Every sensor is telling the same story.");
            statusDesc.setTextColor(0xFFA8BCC9);
        } else {
            statusBadge.setText("WAITING");
            statusBadge.setTextColor(0xFF7F97A6);
            statusHeadline.setText(head != null ? head : "Not watching");
            statusHeadline.setTextColor(0xFFA8BCC9);
            statusDesc.setText("Connect to a console to see live data.");
            statusDesc.setTextColor(0xFF7F97A6);
        }
    }

    private void toggleFullscreenMap() {
        isMapFullscreen = !isMapFullscreen;
        if (isMapFullscreen) {
            body.removeView(mapView);
            outerRoot.addView(mapView, new FrameLayout.LayoutParams(
                    FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT));
            innerContent.setVisibility(View.GONE);
        } else {
            outerRoot.removeView(mapView);
            LinearLayout.LayoutParams mapLp = new LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT, dp(270));
            mapLp.topMargin = dp(8);
            mapLp.bottomMargin = dp(0);
            body.addView(mapView, 3, mapLp);
            innerContent.setVisibility(View.VISIBLE);
        }
    }

    /** Refresh the gap-detail rows beneath the map every broadcast tick. */
    private void updateGapPanel(float gapM, float sigmaM, float ratio, float vertM) {
        if (gapDist == null) return;

        if (gapM > 0.1f) {
            gapDist.setText(String.format(
                    "%.0f metres between where GPS says it is and where it worked out it is",
                    gapM));
            gapDist.setTextColor(gapM > 10f ? 0xFFE88840 : 0xFFA8BCC9);
        } else {
            gapDist.setText(
                    "— metres between where GPS says it is and where it worked out it is");
            gapDist.setTextColor(0xFF7F97A6);
        }

        if (sigmaM > 0.1f) {
            gapUncertainty.setText(String.format("± %.1f m", sigmaM));
            gapUncertainty.setTextColor(sigmaM > 30f ? 0xFFE88840 : 0xFFA8BCC9);
        } else {
            gapUncertainty.setText("—");
            gapUncertainty.setTextColor(0xFFA8BCC9);
        }

        if (ratio > 0.01f) {
            gapRatioView.setText(String.format("%.2f ×", ratio));
            gapRatioView.setTextColor(ratio > 2.5f ? 0xFFE88840 : 0xFFA8BCC9);
        } else {
            gapRatioView.setText("—");
            gapRatioView.setTextColor(0xFFA8BCC9);
        }

        gapVertical.setText(vertM != 0f ? String.format("%+.1f m", vertM) : "—");
        gapVertical.setTextColor(Math.abs(vertM) > 5f ? 0xFFE88840 : 0xFFA8BCC9);
    }

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }
}
