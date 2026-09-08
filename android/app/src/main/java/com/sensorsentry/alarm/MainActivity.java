package com.sensorsentry.alarm;

import android.Manifest;
import android.app.Activity;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Paint;
import android.graphics.Typeface;
import android.graphics.pdf.PdfDocument;
import android.net.Uri;
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
import android.widget.Toast;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * The phone screen: a live map, the same verdict the console shows, and the
 * incident report afterwards.
 *
 * <p>It reports; it does not decide. Every word here is read off the console's
 * own snapshot — the phone runs no detection of its own, so there are never two
 * systems that could disagree about whether a lorry is being stolen.
 *
 * <p>Colours all come from {@link Theme}. Nothing on this screen chooses its own
 * shade, which is what makes the light/dark switch reach the whole thing
 * including the canvas.
 */
public class MainActivity extends Activity {

    private static final String PREFS      = "sensorsentry";
    private static final String KEY_SERVER = "server";
    private static final String KEY_DARK   = "dark";
    private static final String DEFAULT_SERVER = "192.168.29.66:8080";
    private static final int    REQ_SAVE_REPORT = 42;

    // --- views we recolour or rewrite -------------------------------------
    private FrameLayout  outerRoot;
    private LinearLayout innerContent, body, footer;
    private ScrollView   scroll;
    private MapView      mapView;

    private TextView brand, headline, detail, connection;
    private TextView statusBadge, statusHeadline, statusDesc;
    private TextView whyHeader, whyText, actionHeader, actionText;
    private TextView sensorRow, causeRow;
    private TextView gapDist, gapUncertainty, gapRatioView, gapVertical;
    private TextView reportHeader, reportBody, reportBy;
    private Button   themeButton, toggle, reportButton, saveButton;
    private EditText serverField;

    private final java.util.List<TextView> labels   = new java.util.ArrayList<>();
    private final java.util.List<TextView> sectionHeaders = new java.util.ArrayList<>();
    private final java.util.List<View>     dividers = new java.util.ArrayList<>();

    private boolean watching = false;
    private boolean alerting = false;
    private boolean mapFullscreen = false;
    private String  reportText = "";

    // -----------------------------------------------------------------------

    private final BroadcastReceiver updates = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            if (!watching) markWatching();

            boolean alert     = intent.getBooleanExtra(WatchService.EXTRA_ALERT, false);
            boolean connected = intent.getBooleanExtra(WatchService.EXTRA_CONNECTED, false);
            show(intent.getStringExtra(WatchService.EXTRA_HEADLINE),
                 intent.getStringExtra(WatchService.EXTRA_DETAIL), alert, connected);

            String gnssJson    = intent.getStringExtra(WatchService.EXTRA_TRAILS_GNSS);
            String witnessJson = intent.getStringExtra(WatchService.EXTRA_TRAILS_WITNESS);
            float  gapM        = intent.getFloatExtra(WatchService.EXTRA_GAP_M, 0f);
            if (gnssJson != null && witnessJson != null) {
                try {
                    mapView.setTrails(new JSONArray(gnssJson), new JSONArray(witnessJson), gapM);
                } catch (JSONException ignored) {}
            }

            if (WatchService.hasNewBasemap) {
                mapView.setBasemap(WatchService.cachedBasemapJson);
                WatchService.hasNewBasemap = false;
            }

            mapView.setVehicleId(intent.getStringExtra(WatchService.EXTRA_VEHICLE_ID));
            mapView.setHeading(intent.getFloatExtra(WatchService.EXTRA_HEADING_DEG, Float.NaN));

            float vertM      = intent.getFloatExtra(WatchService.EXTRA_VERT_M, 0f);
            float compassDeg = intent.getFloatExtra(WatchService.EXTRA_COMPASS_DEG, Float.NaN);
            mapView.setSensorInfo(gapM, vertM, compassDeg);

            updateGapPanel(gapM,
                    intent.getFloatExtra(WatchService.EXTRA_SIGMA_M, 0f),
                    intent.getFloatExtra(WatchService.EXTRA_RATIO, 0f), vertM);

            // Why, and what to do. Two different sentences, and the owner of
            // the lorry needs both: one to believe the verdict, one to act on
            // it. The screen was showing neither.
            showWhy(intent.getStringExtra(WatchService.EXTRA_CAUSE_LABEL),
                    intent.getStringExtra(WatchService.EXTRA_GUILTY),
                    intent.getStringExtra(WatchService.EXTRA_CAUSE_REASON),
                    intent.getStringExtra(WatchService.EXTRA_CAUSE_ACTION));
        }
    };

    // --- lifecycle ---------------------------------------------------------

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        SharedPreferences prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        // Light by default. A phone is read in daylight far more often than a
        // projector screen is, and the demo hall is bright with the blinds up
        // until somebody decides otherwise. The switch is one tap away.
        Theme.apply(prefs.getBoolean(KEY_DARK, false));

        setContentView(buildView());
        serverField.setText(prefs.getString(KEY_SERVER, DEFAULT_SERVER));
        applyTheme();

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

    // --- the screen --------------------------------------------------------

    private View buildView() {
        outerRoot = new FrameLayout(this);

        innerContent = new LinearLayout(this);
        innerContent.setOrientation(LinearLayout.VERTICAL);
        outerRoot.addView(innerContent, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT));

        scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        innerContent.addView(scroll, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f));

        body = new LinearLayout(this);
        body.setOrientation(LinearLayout.VERTICAL);
        body.setPadding(dp(20), dp(30), dp(20), dp(12));
        scroll.addView(body);

        // --- header row: brand, and the theme switch ------------------------
        LinearLayout head = new LinearLayout(this);
        head.setOrientation(LinearLayout.HORIZONTAL);
        head.setGravity(Gravity.CENTER_VERTICAL);
        body.addView(head);

        brand = new TextView(this);
        brand.setText("SENSORSENTRY");
        brand.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f);
        brand.setLetterSpacing(0.18f);
        head.addView(brand, new LinearLayout.LayoutParams(
                0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));

        themeButton = new Button(this);
        themeButton.setAllCaps(false);
        themeButton.setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f);
        themeButton.setOnClickListener(v -> flipTheme());
        head.addView(themeButton, new LinearLayout.LayoutParams(dp(58), dp(40)));

        headline = new TextView(this);
        headline.setText("Not watching");
        headline.setTextSize(TypedValue.COMPLEX_UNIT_SP, 32f);
        headline.setLineSpacing(0f, 1.05f);
        headline.setPadding(0, dp(20), 0, 0);
        body.addView(headline);

        detail = new TextView(this);
        detail.setText("Enter the console address and press Watch.");
        detail.setTextSize(TypedValue.COMPLEX_UNIT_SP, 16f);
        detail.setPadding(0, dp(12), 0, dp(10));
        body.addView(detail);

        // --- the map --------------------------------------------------------
        mapView = new MapView(this);
        LinearLayout.LayoutParams mapLp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, dp(290));
        mapLp.topMargin = dp(8);
        body.addView(mapView, mapLp);
        mapView.setOnMapClickListener(this::toggleFullscreenMap);

        TextView tapHint = new TextView(this);
        tapHint.setText("tap the map for full screen · pinch to zoom");
        tapHint.setTextSize(TypedValue.COMPLEX_UNIT_SP, 11f);
        tapHint.setPadding(0, dp(6), 0, 0);
        labels.add(tapHint);
        body.addView(tapHint);

        // --- verdict --------------------------------------------------------
        dividers.add(divider(body, dp(14), 0));

        statusBadge = new TextView(this);
        statusBadge.setText("WAITING");
        statusBadge.setTextSize(TypedValue.COMPLEX_UNIT_SP, 11f);
        statusBadge.setLetterSpacing(0.15f);
        statusBadge.setTypeface(Typeface.DEFAULT_BOLD);
        statusBadge.setPadding(0, dp(14), 0, dp(2));
        body.addView(statusBadge);

        statusHeadline = new TextView(this);
        statusHeadline.setText("Not watching");
        statusHeadline.setTextSize(TypedValue.COMPLEX_UNIT_SP, 23f);
        statusHeadline.setTypeface(Typeface.DEFAULT_BOLD);
        body.addView(statusHeadline);

        statusDesc = new TextView(this);
        statusDesc.setText("Connect to see live sensor data.");
        statusDesc.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14f);
        statusDesc.setPadding(0, dp(6), 0, dp(4));
        body.addView(statusDesc);

        // --- the verdict, in fields -------------------------------------------
        sectionHeader(body, "THE VERDICT");
        sensorRow = gapRow(body, "Sensor at fault");
        causeRow  = gapRow(body, "Cause");

        // --- why we say that -------------------------------------------------
        whyHeader = sectionHeader(body, "WHY WE SAY THAT");
        whyText = new TextView(this);
        whyText.setText("Waiting for a verdict.");
        whyText.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14f);
        whyText.setLineSpacing(0f, 1.2f);
        whyText.setPadding(0, dp(2), 0, dp(8));
        body.addView(whyText);

        // --- and what to do about it -------------------------------------------
        actionHeader = sectionHeader(body, "WHAT TO DO");
        actionText = new TextView(this);
        actionText.setText("Nothing to do.");
        actionText.setTextSize(TypedValue.COMPLEX_UNIT_SP, 15f);
        actionText.setLineSpacing(0f, 1.25f);
        actionText.setTypeface(Typeface.DEFAULT_BOLD);
        actionText.setPadding(0, dp(2), 0, dp(8));
        body.addView(actionText);

        dividers.add(divider(body, dp(6), 0));

        gapDist = new TextView(this);
        gapDist.setText("— metres between where GPS says it is and where it worked out it is");
        gapDist.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f);
        gapDist.setPadding(0, dp(10), 0, dp(10));
        body.addView(gapDist);

        dividers.add(divider(body, 0, 0));

        sectionHeader(body, "THE GAP, IN DETAIL");
        gapUncertainty = gapRow(body, "Our uncertainty");
        gapRatioView   = gapRow(body, "Ratio");
        gapVertical    = gapRow(body, "Vertical");

        dividers.add(divider(body, dp(10), 0));

        // --- the written report ---------------------------------------------
        reportHeader = sectionHeader(body, "INCIDENT REPORT");

        reportBy = new TextView(this);
        reportBy.setText("Fetch the write-up of the last incident.");
        reportBy.setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f);
        reportBy.setPadding(0, dp(2), 0, dp(8));
        labels.add(reportBy);
        body.addView(reportBy);

        LinearLayout reportRow = new LinearLayout(this);
        reportRow.setOrientation(LinearLayout.HORIZONTAL);
        body.addView(reportRow);

        reportButton = new Button(this);
        reportButton.setText("Get report");
        reportButton.setAllCaps(false);
        reportButton.setOnClickListener(v -> fetchReport());
        LinearLayout.LayoutParams rl = new LinearLayout.LayoutParams(
                0, dp(46), 1f);
        rl.rightMargin = dp(6);
        reportRow.addView(reportButton, rl);

        saveButton = new Button(this);
        saveButton.setText("Save as PDF");
        saveButton.setAllCaps(false);
        saveButton.setEnabled(false);
        saveButton.setOnClickListener(v -> saveReport());
        reportRow.addView(saveButton, new LinearLayout.LayoutParams(0, dp(46), 1f));

        reportBody = new TextView(this);
        reportBody.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f);
        reportBody.setLineSpacing(0f, 1.25f);
        reportBody.setTypeface(Typeface.MONOSPACE);
        reportBody.setPadding(0, dp(12), 0, dp(24));
        reportBody.setVisibility(View.GONE);
        body.addView(reportBody);

        // --- footer ----------------------------------------------------------
        footer = new LinearLayout(this);
        footer.setOrientation(LinearLayout.VERTICAL);
        footer.setPadding(dp(20), dp(8), dp(20), dp(18));
        innerContent.addView(footer, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT));

        connection = new TextView(this);
        connection.setText("not connected");
        connection.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f);
        connection.setPadding(0, 0, 0, dp(8));
        footer.addView(connection);

        serverField = new EditText(this);
        serverField.setHint("laptop address, e.g. 192.168.1.5:8080");
        serverField.setInputType(InputType.TYPE_TEXT_VARIATION_URI);
        serverField.setSingleLine(true);
        footer.addView(serverField);

        // Watch is a big target because you press it once, at the start.
        //
        // Stop is not the same button wearing a different word. Once the phone
        // is watching, the only thing a full-width control at the bottom of the
        // screen can do is switch the alarm off by accident, in a pocket, with
        // nothing on screen afterwards to say it happened. So it shrinks to a
        // small one on the left and the space goes to what is being watched.
        LinearLayout controls = new LinearLayout(this);
        controls.setOrientation(LinearLayout.HORIZONTAL);
        LinearLayout.LayoutParams rowLp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT);
        rowLp.topMargin = dp(12);
        footer.addView(controls, rowLp);

        toggle = new Button(this);
        toggle.setText("Watch");
        toggle.setAllCaps(false);
        toggle.setTextSize(TypedValue.COMPLEX_UNIT_SP, 18f);
        toggle.setOnClickListener(v -> onToggle());
        controls.addView(toggle, new LinearLayout.LayoutParams(
                0, dp(54), 1f));

        return outerRoot;
    }

    // --- theme --------------------------------------------------------------

    private void flipTheme() {
        Theme.apply(!Theme.dark);
        getSharedPreferences(PREFS, MODE_PRIVATE)
                .edit().putBoolean(KEY_DARK, Theme.dark).apply();
        applyTheme();
    }

    /** Repaint every view from the palette. The map is told separately: it
     *  caches its paints, so without this it keeps the shades it was built
     *  with while the screen changes around it. */
    private void applyTheme() {
        themeButton.setText(Theme.dark ? "☀" : "☾");

        outerRoot.setBackgroundColor(alerting ? Theme.bgAlert : Theme.bg);
        brand.setTextColor(Theme.ink3);
        headline.setTextColor(alerting ? Theme.onAlert() : Theme.ink);
        detail.setTextColor(alerting ? Theme.onAlert() : Theme.ink2);
        statusDesc.setTextColor(Theme.ink2);
        whyText.setTextColor(Theme.ink2);
        gapDist.setTextColor(Theme.ink2);
        connection.setTextColor(Theme.ink3);
        reportBody.setTextColor(Theme.ink2);
        serverField.setTextColor(Theme.ink);
        serverField.setHintTextColor(Theme.hint);

        paintPanel(alerting);
        mapView.refreshColours();
    }

    // --- report -------------------------------------------------------------

    private void fetchReport() {
        final String server = serverField.getText().toString().trim().isEmpty()
                ? getSharedPreferences(PREFS, MODE_PRIVATE).getString(KEY_SERVER, DEFAULT_SERVER)
                : serverField.getText().toString().trim();
        reportButton.setEnabled(false);
        reportBy.setText("Fetching…");

        new Thread(() -> {
            String title = null, bodyText = null, by = null, note = null;
            boolean enabled = true;
            try {
                HttpURLConnection c = (HttpURLConnection)
                        new URL("http://" + server + "/report").openConnection();
                c.setConnectTimeout(4000);
                c.setReadTimeout(8000);
                try {
                    StringBuilder sb = new StringBuilder();
                    try (BufferedReader r = new BufferedReader(
                            new InputStreamReader(c.getInputStream()))) {
                        String line;
                        while ((line = r.readLine()) != null) sb.append(line).append('\n');
                    }
                    JSONObject o = new JSONObject(sb.toString());
                    enabled = o.optBoolean("enabled", true);
                    note = o.optString("note", "");
                    JSONObject rep = o.optJSONObject("report");
                    if (rep != null) {
                        title    = rep.optString("title", "");
                        bodyText = rep.optString("body", "");
                        by       = rep.optString("generated_by", "");
                    }
                } finally {
                    c.disconnect();
                }
            } catch (Exception e) {
                note = "could not reach " + server;
            }

            final String fTitle = title, fBody = bodyText, fBy = by, fNote = note;
            final boolean fEnabled = enabled;
            runOnUiThread(() -> showReport(fEnabled, fTitle, fBody, fBy, fNote));
        }, "report-fetch").start();
    }

    private void showReport(boolean enabled, String title, String bodyText,
                            String by, String note) {
        reportButton.setEnabled(true);
        if (!enabled) {
            reportBy.setText("The written report is switched off on the console.");
            reportBody.setVisibility(View.GONE);
            saveButton.setEnabled(false);
            return;
        }
        if (bodyText == null || bodyText.isEmpty()) {
            reportBy.setText(note != null && !note.isEmpty() ? note : "No incident recorded yet.");
            reportBody.setVisibility(View.GONE);
            saveButton.setEnabled(false);
            return;
        }
        reportText = title + "\n\n" + bodyText + "\n\nwritten by: " + by + "\n";
        reportBody.setText(title + "\n\n" + bodyText);
        reportBody.setVisibility(View.VISIBLE);
        // Say honestly what wrote it. A template passing for a model is the
        // second-worst outcome available; a blank panel is the worst.
        reportBy.setText("written by: " + by);
        saveButton.setEnabled(true);
    }

    /**
     * Save the report as a PDF, through the system file picker.
     *
     * <p>PDF rather than text, because a plain .txt is saved successfully and
     * then cannot be opened: most phones ship no text viewer at all, so the
     * file lands in Downloads and tapping it does nothing. A report nobody can
     * read is not a report. Android renders PDFs itself and every phone can
     * open one, and it is also the format an insurer or an investigator would
     * expect to be handed.
     *
     * <p>The picker means no storage permission is needed and the user chooses
     * where it goes, so it is a real file they can find again.
     */
    private void saveReport() {
        if (reportText.isEmpty()) return;
        Intent intent = new Intent(Intent.ACTION_CREATE_DOCUMENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.setType("application/pdf");
        intent.putExtra(Intent.EXTRA_TITLE, "sensorsentry-incident.pdf");
        try {
            startActivityForResult(intent, REQ_SAVE_REPORT);
        } catch (Exception e) {
            Toast.makeText(this, "No file picker on this phone", Toast.LENGTH_SHORT).show();
        }
    }

    @Override
    protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (request != REQ_SAVE_REPORT || result != RESULT_OK || data == null) return;
        Uri uri = data.getData();
        if (uri == null) return;
        try (OutputStream out = getContentResolver().openOutputStream(uri)) {
            if (out == null) return;
            writePdf(out);
            Toast.makeText(this, "Report saved as PDF", Toast.LENGTH_SHORT).show();
        } catch (Exception e) {
            Toast.makeText(this, "Could not save: " + e.getMessage(),
                    Toast.LENGTH_LONG).show();
        }
    }

    /** A4 at 72 points to the inch, which is what PdfDocument works in. */
    private static final int PAGE_W = 595, PAGE_H = 842, MARGIN = 48;

    private void writePdf(OutputStream out) throws Exception {
        PdfDocument doc = new PdfDocument();
        Paint text = new Paint(Paint.ANTI_ALIAS_FLAG);
        text.setColor(0xFF16202A);
        text.setTextSize(10.5f);

        Paint head = new Paint(Paint.ANTI_ALIAS_FLAG);
        head.setColor(0xFF16202A);
        head.setTextSize(15f);
        head.setTypeface(Typeface.create(Typeface.DEFAULT, Typeface.BOLD));

        java.util.List<String> lines = new java.util.ArrayList<>();
        for (String para : reportText.split("\n", -1)) {
            if (para.trim().isEmpty()) { lines.add(""); continue; }
            lines.addAll(wrap(para, text, PAGE_W - 2 * MARGIN));
        }

        int page = 0, i = 0;
        while (i < lines.size() || page == 0) {
            page++;
            PdfDocument.Page p = doc.startPage(
                    new PdfDocument.PageInfo.Builder(PAGE_W, PAGE_H, page).create());
            android.graphics.Canvas c = p.getCanvas();
            float y = MARGIN;

            if (page == 1) {
                c.drawText("SensorSentry — incident report", MARGIN, y + 12, head);
                y += 34;
            }
            while (i < lines.size() && y < PAGE_H - MARGIN) {
                c.drawText(lines.get(i), MARGIN, y, text);
                y += 15;
                i++;
            }
            doc.finishPage(p);
            if (i >= lines.size()) break;
        }
        doc.writeTo(out);
        doc.close();
    }

    private static java.util.List<String> wrap(String s, Paint paint, float width) {
        java.util.List<String> out = new java.util.ArrayList<>();
        String rest = s;
        while (!rest.isEmpty()) {
            int n = paint.breakText(rest, true, width, null);
            if (n <= 0) { out.add(rest); break; }
            if (n < rest.length()) {
                int space = rest.lastIndexOf(' ', n);
                if (space > 0) n = space;
            }
            out.add(rest.substring(0, n).trim());
            rest = rest.substring(n).trim();
        }
        return out;
    }

    // --- layout helpers -----------------------------------------------------

    private View divider(LinearLayout parent, int topMargin, int bottomMargin) {
        View v = new View(this);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 1);
        lp.topMargin = topMargin;
        lp.bottomMargin = bottomMargin;
        parent.addView(v, lp);
        return v;
    }

    private TextView sectionHeader(LinearLayout parent, String text) {
        TextView tv = new TextView(this);
        tv.setText(text);
        tv.setTextSize(TypedValue.COMPLEX_UNIT_SP, 11f);
        tv.setLetterSpacing(0.12f);
        tv.setTypeface(Typeface.DEFAULT_BOLD);
        tv.setPadding(0, dp(14), 0, dp(4));
        tv.setTextColor(Theme.label);
        sectionHeaders.add(tv);
        parent.addView(tv);
        return tv;
    }

    private TextView gapRow(LinearLayout parent, String label) {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setPadding(0, dp(5), 0, dp(5));
        parent.addView(row, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT));

        TextView lv = new TextView(this);
        lv.setText(label);
        lv.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14f);
        labels.add(lv);
        row.addView(lv, new LinearLayout.LayoutParams(
                0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f));

        TextView rv = new TextView(this);
        rv.setText("—");
        rv.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14f);
        row.addView(rv);
        return rv;
    }

    // --- state --------------------------------------------------------------

    private void markWatching() {
        watching = true;
        toggle.setText("Stop watching");
        toggle.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f);
        serverField.setVisibility(View.GONE);
        // Shrink it once it is armed: from here on the screen belongs to the
        // vehicle, not to the button that turns it off.
        android.view.ViewGroup.LayoutParams lp = toggle.getLayoutParams();
        if (lp instanceof LinearLayout.LayoutParams) {
            LinearLayout.LayoutParams ll = (LinearLayout.LayoutParams) lp;
            ll.weight = 0f;
            ll.width  = dp(140);
            ll.height = dp(42);
            toggle.setLayoutParams(ll);
        }
    }

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

    private void show(String head, String note, boolean alert, boolean connected) {
        if (head != null) headline.setText(head);
        if (note != null) detail.setText(note);

        alerting = alert;
        outerRoot.setBackgroundColor(alert ? Theme.bgAlert : Theme.bg);
        headline.setTextColor(alert ? Theme.onAlert() : Theme.ink);
        detail.setTextColor(alert ? Theme.onAlert() : Theme.ink2);

        connection.setText(connected ? "connected" : "not connected");
        connection.setTextColor(alert ? 0xCCFFFFFF : (connected ? Theme.ok : Theme.bad));

        if (alert) {
            // The whole screen goes red so a glance across a room answers the
            // question before anything is read — but then every word below has
            // to be readable *on red*. The light theme's inks are dark by
            // definition, so the panel came out nearly invisible: the right
            // answer, printed in a colour nobody can see.
            statusBadge.setText("ALERT");
            statusBadge.setTextColor(0xFFFFD9D4);
            statusHeadline.setText(head != null ? head : "ALERT");
            statusHeadline.setTextColor(Theme.onAlert());
            statusDesc.setText("A sensor cross-check has failed. Follow the action shown.");
        } else if (connected && "No vehicle running".equals(head)) {
            statusBadge.setText("WAITING");
            statusBadge.setTextColor(Theme.ink3);
            statusHeadline.setText("No run");
            statusHeadline.setTextColor(Theme.ink2);
            statusDesc.setText("Start a scenario from the control screen.");
        } else if (connected) {
            statusBadge.setText("ALL QUIET");
            statusBadge.setTextColor(Theme.ok);
            statusHeadline.setText("Everything agrees");
            statusHeadline.setTextColor(Theme.ok);
            statusDesc.setText("Every cross-check agrees. All sensors tell the same story.");
        } else {
            statusBadge.setText("WAITING");
            statusBadge.setTextColor(Theme.ink3);
            statusHeadline.setText(head != null ? head : "Not watching");
            statusHeadline.setTextColor(Theme.ink2);
            statusDesc.setText("Connect to a console to see live data.");
        }
        statusDesc.setTextColor(alert ? 0xE6FFFFFF : Theme.ink2);
        paintPanel(alert);
    }

    // On an alert the whole screen is red, and the palette's own inks are
    // chosen to sit on the page background rather than on that. Every value
    // written after the repaint has to ask which world it is in, or it comes
    // out dark red on red — the right answer, printed invisibly.
    private int muted()   { return alerting ? 0xB3FFFFFF : Theme.ink3; }
    private int normal()  { return alerting ? 0xE6FFFFFF : Theme.ink2; }
    private int shout()   { return alerting ? 0xFFFFFFFF : Theme.bad; }
    private int caution() { return alerting ? 0xFFFFD9A8 : Theme.warn; }

    /** Every remaining word on the screen, in a colour that survives the
     *  background it is sitting on. */
    private void paintPanel(boolean alert) {
        int body    = alert ? 0xE6FFFFFF : Theme.ink2;
        int muted   = alert ? 0xB3FFFFFF : Theme.ink3;
        int heading = alert ? 0xFFFFD9D4 : Theme.label;

        whyText.setTextColor(body);
        actionText.setTextColor(alert ? Theme.onAlert() : Theme.ink);
        gapDist.setTextColor(body);
        reportBody.setTextColor(body);
        for (TextView t : labels) t.setTextColor(muted);
        if (whyHeader != null)    whyHeader.setTextColor(heading);
        if (actionHeader != null) actionHeader.setTextColor(heading);
        if (reportHeader != null) reportHeader.setTextColor(heading);
        for (TextView h : sectionHeaders) h.setTextColor(heading);
        for (View d : dividers) d.setBackgroundColor(alert ? 0x40FFFFFF : Theme.divider);
    }

    private static final java.util.Map<String, String> SENSOR_NAMES = new java.util.HashMap<>();
    static {
        SENSOR_NAMES.put("gnss", "GPS receiver");
        SENSOR_NAMES.put("imu",  "motion sensor");
        SENSOR_NAMES.put("mag",  "compass");
        SENSOR_NAMES.put("baro", "altitude sensor");
        SENSOR_NAMES.put("odom", "wheel sensor");
    }

    private void showWhy(String cause, String guilty, String reason, String action) {
        // Which sensor, in words the owner of the lorry uses. "gnss/attack"
        // means something to us and nothing to them.
        if (guilty == null || guilty.isEmpty()) {
            sensorRow.setText("—");
            sensorRow.setTextColor(muted());
        } else if ("cannot_isolate".equals(guilty)) {
            // The console refusing to name a sensor is the most careful thing
            // it does. A phone that turned that into a confident accusation
            // would undo it.
            sensorRow.setText("not identified");
            sensorRow.setTextColor(caution());
        } else {
            String name = SENSOR_NAMES.containsKey(guilty) ? SENSOR_NAMES.get(guilty) : guilty;
            sensorRow.setText(name);
            sensorRow.setTextColor(shout());
        }

        causeRow.setText(cause == null || cause.isEmpty() ? "—" : cause);
        causeRow.setTextColor(cause == null || cause.isEmpty() || "unclassified".equals(cause)
                ? muted() : shout());

        if (reason == null || reason.isEmpty()) {
            whyText.setText(watching ? "Nothing to explain — every check agrees."
                                     : "Waiting for a verdict.");
            whyText.setTextColor(muted());
        } else {
            whyText.setText(reason);
            whyText.setTextColor(normal());
        }

        if (action == null || action.isEmpty()) {
            actionText.setText("Nothing to do.");
            actionText.setTextColor(muted());
        } else {
            actionText.setText(action);
            actionText.setTextColor(alerting ? 0xFFFFFFFF : Theme.ink);
        }
    }

    private void toggleFullscreenMap() {
        mapFullscreen = !mapFullscreen;
        if (mapFullscreen) {
            body.removeView(mapView);
            outerRoot.addView(mapView, new FrameLayout.LayoutParams(
                    FrameLayout.LayoutParams.MATCH_PARENT,
                    FrameLayout.LayoutParams.MATCH_PARENT));
            innerContent.setVisibility(View.GONE);
        } else {
            outerRoot.removeView(mapView);
            LinearLayout.LayoutParams mapLp = new LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT, dp(290));
            mapLp.topMargin = dp(8);
            body.addView(mapView, 3, mapLp);
            innerContent.setVisibility(View.VISIBLE);
        }
    }

    private void updateGapPanel(float gapM, float sigmaM, float ratio, float vertM) {
        if (gapDist == null) return;

        if (gapM > 0.1f) {
            gapDist.setText(String.format(
                    "%.0f metres between where GPS says it is and where it worked out it is", gapM));
            gapDist.setTextColor(gapM > 10f ? caution() : normal());
        } else {
            gapDist.setText("— metres between where GPS says it is and where it worked out it is");
            gapDist.setTextColor(muted());
        }

        gapUncertainty.setText(sigmaM > 0.1f ? String.format("± %.1f m", sigmaM) : "—");
        gapUncertainty.setTextColor(sigmaM > 30f ? caution() : normal());

        gapRatioView.setText(ratio > 0.01f ? String.format("%.2f ×", ratio) : "—");
        gapRatioView.setTextColor(ratio > 2.5f ? caution() : normal());

        gapVertical.setText(vertM != 0f ? String.format("%+.1f m", vertM) : "—");
        gapVertical.setTextColor(Math.abs(vertM) > 5f ? caution() : normal());
    }

    private int dp(int v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }
}
