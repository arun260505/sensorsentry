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
import android.os.Build;
import android.os.Bundle;
import android.text.InputType;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;

/**
 * One screen: what is happening to the vehicle, in one line.
 *
 * <p>Deliberately not a map, a chart or a sensor list. The console has all of
 * that on a laptop where there is room for it. What a fleet manager needs from
 * a phone in a pocket is the answer to one question — is somebody stealing my
 * lorry — and anything else on the screen is in the way of it.
 *
 * <p>Built in code rather than XML because the whole interface is four views,
 * and a layout file plus a resource lookup for each of them is more machinery
 * than the thing being built.
 */
public class MainActivity extends Activity {

    private static final String PREFS = "sensorsentry";
    private static final String KEY_SERVER = "server";
    private static final String DEFAULT_SERVER = "172.16.130.172:8080";

    private LinearLayout root;
    private TextView headline;
    private TextView detail;
    private TextView connection;
    private EditText serverField;
    private Button toggle;
    private boolean watching = false;

    private final BroadcastReceiver updates = new BroadcastReceiver() {
        @Override
        public void onReceive(Context context, Intent intent) {
            boolean alert = intent.getBooleanExtra(WatchService.EXTRA_ALERT, false);
            boolean connected = intent.getBooleanExtra(WatchService.EXTRA_CONNECTED, false);
            show(intent.getStringExtra(WatchService.EXTRA_HEADLINE),
                 intent.getStringExtra(WatchService.EXTRA_DETAIL), alert, connected);
        }
    };

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        setContentView(buildView());

        SharedPreferences prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        serverField.setText(prefs.getString(KEY_SERVER, DEFAULT_SERVER));

        // Android 13 and later will silently drop every notification until
        // this is granted, which would leave the app looking like it works
        // and doing the one thing it exists for not at all.
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
        try {
            unregisterReceiver(updates);
        } catch (IllegalArgumentException ignored) {
            // Not registered; nothing to undo.
        }
    }

    // --- the screen --------------------------------------------------------

    private View buildView() {
        int pad = dp(20);
        root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(pad, dp(36), pad, pad);
        root.setBackgroundColor(Color.parseColor("#0f1c25"));

        TextView brand = new TextView(this);
        brand.setText("SENSORSENTRY");
        brand.setTextColor(Color.parseColor("#7f97a6"));
        brand.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f);
        brand.setLetterSpacing(0.18f);
        root.addView(brand);

        headline = new TextView(this);
        headline.setText("Not watching");
        headline.setTextColor(Color.WHITE);
        headline.setTextSize(TypedValue.COMPLEX_UNIT_SP, 34f);
        headline.setLineSpacing(0f, 1.05f);
        headline.setPadding(0, dp(28), 0, 0);
        root.addView(headline);

        detail = new TextView(this);
        detail.setText("Enter the console address and press Watch.");
        detail.setTextColor(Color.parseColor("#a8bcc9"));
        detail.setTextSize(TypedValue.COMPLEX_UNIT_SP, 16f);
        detail.setPadding(0, dp(14), 0, 0);
        root.addView(detail);

        View spacer = new View(this);
        root.addView(spacer, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f));

        connection = new TextView(this);
        connection.setTextColor(Color.parseColor("#7f97a6"));
        connection.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f);
        connection.setText("not connected");
        connection.setPadding(0, 0, 0, dp(10));
        root.addView(connection);

        serverField = new EditText(this);
        serverField.setHint("laptop address, e.g. 192.168.1.5:8080");
        serverField.setInputType(InputType.TYPE_TEXT_VARIATION_URI);
        serverField.setTextColor(Color.WHITE);
        serverField.setHintTextColor(Color.parseColor("#5c7080"));
        serverField.setSingleLine(true);
        root.addView(serverField);

        toggle = new Button(this);
        toggle.setText("Watch");
        toggle.setAllCaps(false);
        toggle.setTextSize(TypedValue.COMPLEX_UNIT_SP, 18f);
        toggle.setGravity(Gravity.CENTER);
        toggle.setOnClickListener(v -> onToggle());
        LinearLayout.LayoutParams button = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, dp(56));
        button.topMargin = dp(14);
        root.addView(toggle, button);

        return root;
    }

    private void onToggle() {
        Intent service = new Intent(this, WatchService.class);
        if (watching) {
            stopService(service);
            watching = false;
            toggle.setText("Watch");
            serverField.setVisibility(View.VISIBLE);
            show("Not watching", "Press Watch to reconnect.", false, false);
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
        watching = true;
        toggle.setText("Stop");
        // The address is setup, not information. Once it is watching, the
        // screen should carry the one thing being watched for and nothing
        // else — an IP address on a screen held up to a room is noise.
        serverField.setVisibility(View.GONE);
        show("Connecting…", "", false, false);
    }

    /**
     * Paint the screen for what is happening.
     *
     * <p>An alert turns the whole background red. A phone glanced at from
     * across a room should answer the question before it is read.
     */
    private void show(String head, String note, boolean alert, boolean connected) {
        if (head != null) headline.setText(head);
        if (note != null) detail.setText(note);
        // Paint the layout we built, not getRootView() — that returns the
        // window decor, which the theme paints over, so the screen stayed
        // navy through an alert it was supposed to turn red for.
        root.setBackgroundColor(alert
                ? Color.parseColor("#8c1d13") : Color.parseColor("#0f1c25"));
        connection.setText(connected ? "connected" : "not connected");
        connection.setTextColor(connected
                ? Color.parseColor("#5fbf8f") : Color.parseColor("#c9736a"));
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
