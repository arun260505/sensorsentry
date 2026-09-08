package com.sensorsentry.alarm;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.DashPathEffect;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RectF;
import android.graphics.Typeface;
import android.view.MotionEvent;
import android.view.View;

import org.json.JSONArray;
import org.json.JSONObject;

/**
 * The console's map, on a phone.
 *
 * <p>Reads the same `/basemap` the browser reads and draws the same thing: real
 * OpenStreetMap roads, water, buildings and landuse, with the two tracks over
 * the top — where the vehicle really is, and where the GPS claims it is.
 *
 * <p>Three rules carried over from the console, each of which it learned by
 * getting them wrong first:
 *
 * <p><b>Colours come from {@link Theme}, never from a literal here.</b> A theme
 * switch has to reach every shade or the map goes on painting roads on paper
 * inside a dark screen.
 *
 * <p><b>Detail is decided in pixels, not in metres.</b> Buildings and labels
 * disappear when they are too small to carry information, which is what keeps
 * the whole-run view readable and the close view rich.
 *
 * <p><b>Nothing is allocated inside onDraw.</b> The console found 4,662 road
 * points being re-projected twice a frame and fixed it with a bounding box per
 * road; a phone is fifty times weaker and this map has 1,579 roads, so the same
 * fix matters more here. Paths and Paints are made once and reused, and any
 * road whose box is off screen is skipped whole rather than point by point.
 */
public class MapView extends View {

    /** Screen-space names the API actually uses. Not OSM tag names — those are
     *  what the first version matched, so `green`, `built` and `campus` were
     *  silently dropped and the VIT campus never appeared on the map at all. */
    private static final String K_WATER    = "water";
    private static final String K_GREEN    = "green";
    private static final String K_SAND     = "sand";
    private static final String K_BUILT    = "built";
    private static final String K_CAMPUS   = "campus";
    private static final String K_BUILDING = "building";

    /** Below this many pixels across, a shape is noise rather than information. */
    private static final float AREA_MIN_PX  = 3f;
    private static final float LABEL_MIN_PX = 60f;

    // --- paints, all made once -------------------------------------------
    private final Paint areaPaint  = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint areaEdge   = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint roadCase   = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint roadFill   = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint trailGps   = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint trailWit   = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint gapPaint   = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint dotPaint   = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint textPaint  = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint cardPaint  = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint strokePt   = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint bgPaint    = new Paint();

    /** One path, rewound between shapes. Allocating a Path per road per pass
     *  was ~3,000 objects a frame. */
    private final Path scratch = new Path();
    private final RectF pill   = new RectF();

    // --- basemap, parsed and bounded once ---------------------------------
    private int          roadCount = 0;
    private double[][][] roadPts;
    private int[]        roadRanks;
    private double[][]   roadBox;     // [road][minLat,minLon,maxLat,maxLon]

    private int          areaCount = 0;
    private double[][][] areaPts;
    private String[]     areaKinds;
    private String[]     areaNames;
    private double[][]   areaBox;

    private int        placeCount = 0;
    private double[][] placePts;
    private String[]   placeNames;

    private boolean hasMap = false;

    // --- live data ---------------------------------------------------------
    private double[] gnsLat = {}, gnsLon = {};
    private double[] witLat = {}, witLon = {};
    private float    gapM = 0f;
    private float    headingDeg = Float.NaN;
    private float    gpsM = 0f, vertM = 0f, compassDeg = Float.NaN;
    private String   vehicleId = "VEHICLE";

    // --- zoom / pan --------------------------------------------------------
    private float zoom = 1f, panX = 0f, panY = 0f;
    private float pinchD0 = -1f, zoom0 = 1f, lastTX, lastTY;
    private int   fingers = 0;
    private boolean following = true;

    public MapView(Context context) {
        super(context);
        initPaints();
    }

    // --- public API --------------------------------------------------------

    public void setTrails(JSONArray gnss, JSONArray wit, float sep) {
        gnsLat = col(gnss, 0); gnsLon = col(gnss, 1);
        witLat = col(wit,  0); witLon = col(wit,  1);
        gapM   = sep;
        invalidate();
    }

    public void setSensorInfo(float gpsMetres, float vertMetres, float cmpDeg) {
        gpsM = gpsMetres; vertM = vertMetres; compassDeg = cmpDeg;
    }

    public void setHeading(float deg)   { headingDeg = deg; }
    public void setVehicleId(String id) { if (id != null && !id.isEmpty()) vehicleId = id; }

    public void setBasemap(String json) {
        if (json == null || json.isEmpty()) return;
        try {
            JSONObject bm = new JSONObject(json);

            JSONArray rs = bm.optJSONArray("roads");
            if (rs != null) {
                roadCount = rs.length();
                roadPts   = new double[roadCount][][];
                roadRanks = new int[roadCount];
                roadBox   = new double[roadCount][4];
                for (int i = 0; i < roadCount; i++) {
                    JSONObject r = rs.getJSONObject(i);
                    roadRanks[i] = r.optInt("rank", 1);
                    roadPts[i]   = points(r.optJSONArray("points"));
                    bound(roadPts[i], roadBox[i]);
                }
            }

            JSONArray as = bm.optJSONArray("areas");
            if (as != null) {
                areaCount = as.length();
                areaPts   = new double[areaCount][][];
                areaKinds = new String[areaCount];
                areaNames = new String[areaCount];
                areaBox   = new double[areaCount][4];
                for (int i = 0; i < areaCount; i++) {
                    JSONObject a = as.getJSONObject(i);
                    areaKinds[i] = a.optString("kind", "");
                    areaNames[i] = a.optString("name", "");
                    areaPts[i]   = points(a.optJSONArray("points"));
                    bound(areaPts[i], areaBox[i]);
                }
            }

            JSONArray ps = bm.optJSONArray("places");
            if (ps != null) {
                placeCount = ps.length();
                placePts   = new double[placeCount][2];
                placeNames = new String[placeCount];
                for (int i = 0; i < placeCount; i++) {
                    JSONObject p = ps.getJSONObject(i);
                    JSONArray at = p.optJSONArray("at");
                    if (at != null) {
                        placePts[i][0] = at.getDouble(0);
                        placePts[i][1] = at.getDouble(1);
                    }
                    placeNames[i] = p.optString("name", "");
                }
            }

            hasMap = roadCount > 0;
            invalidate();
        } catch (Exception ignored) {}
    }

    /** Re-read every colour. Called when the theme switches — without it the
     *  map keeps the shades it was built with and the screen changes around
     *  it, which is the exact failure the console documents. */
    public void refreshColours() {
        initPaints();
        invalidate();
    }

    public void resetView() {
        zoom = 1f; panX = 0f; panY = 0f; following = true; invalidate();
    }

    // --- drawing -----------------------------------------------------------

    @Override
    protected void onDraw(Canvas canvas) {
        int w = getWidth(), h = getHeight();
        bgPaint.setColor(Theme.mapBg);
        canvas.drawRect(0, 0, w, h, bgPaint);

        BBox box = trailBox();
        if (!box.valid() && hasMap) box = roadBoxAll();
        if (!box.valid()) {
            drawPlaceholder(canvas, w, h);
            return;
        }

        float margin = dp(14);
        // Follow is resolved *before* the projection is built, so nothing is
        // mutated while drawing. The old version nudged panX/panY inside
        // onDraw and rebuilt the projection afterwards.
        float fx = 0f, fy = 0f;
        if (following && gnsLat.length > 0) {
            Proj probe = new Proj(box, w, h, margin, zoom, panX, panY);
            fx = w / 2f - probe.x(gnsLon[gnsLon.length - 1]);
            fy = h / 2f - probe.y(gnsLat[gnsLat.length - 1]);
        }
        Proj proj = new Proj(box, w, h, margin, zoom, panX + fx, panY + fy);

        if (hasMap) {
            drawAreas(canvas, proj, w, h);
            drawRoads(canvas, proj, w, h);
            drawPlaces(canvas, proj, w, h);
        }
        drawTrail(canvas, proj, witLat, witLon, trailWit);
        drawTrail(canvas, proj, gnsLat, gnsLon, trailGps);
        if (gapM > 0.5f) drawGap(canvas, proj);
        drawHeads(canvas, proj);

        drawSensorCard(canvas, w);
        drawCompassRose(canvas, w);
        drawScaleBar(canvas, proj, h);
        drawButtons(canvas, w, h);
    }

    /** True when this shape has any chance of being on screen. */
    private boolean onScreen(double[] b, Proj p, int w, int h) {
        float x0 = p.x(b[1]), x1 = p.x(b[3]);
        float y0 = p.y(b[2]), y1 = p.y(b[0]);   // north is up, so lat inverts
        return x1 >= -20 && x0 <= w + 20 && y1 >= -20 && y0 <= h + 20;
    }

    private void drawAreas(Canvas canvas, Proj proj, int w, int h) {
        if (areaPts == null) return;
        // Washes, then water, then buildings — the order a paper map uses, so a
        // park cannot paint over the houses standing in it.
        for (int pass = 0; pass < 3; pass++) {
            for (int i = 0; i < areaCount; i++) {
                double[][] pts = areaPts[i];
                if (pts == null || pts.length < 3) continue;
                String k = areaKinds[i];

                boolean isWater = K_WATER.equals(k);
                boolean isBuild = K_BUILDING.equals(k);
                if (pass == 0 && (isWater || isBuild)) continue;
                if (pass == 1 && !isWater) continue;
                if (pass == 2 && !isBuild) continue;

                int fill;
                if (isWater)                 fill = Theme.water;
                else if (isBuild)            fill = Theme.building;
                else if (K_GREEN.equals(k))  fill = Theme.green;
                else if (K_SAND.equals(k))   fill = Theme.sand;
                else if (K_BUILT.equals(k))  fill = Theme.built;
                else if (K_CAMPUS.equals(k)) fill = Theme.campus;
                else continue;

                double[] b = areaBox[i];
                if (!onScreen(b, proj, w, h)) continue;
                float across = Math.max(proj.x(b[3]) - proj.x(b[1]),
                                        proj.y(b[0]) - proj.y(b[2]));
                if (across < AREA_MIN_PX) continue;

                trace(proj, pts, true);
                areaPaint.setColor(fill);
                canvas.drawPath(scratch, areaPaint);
                if (isWater || isBuild) {
                    areaEdge.setColor(isWater ? Theme.waterEdge : Theme.buildingEdge);
                    canvas.drawPath(scratch, areaEdge);
                }
            }
        }
        drawAreaNames(canvas, proj, w, h);
    }

    private void drawAreaNames(Canvas canvas, Proj proj, int w, int h) {
        if (areaNames == null) return;
        textPaint.setColor(Theme.placeText);
        textPaint.setTextSize(dp(9));
        for (int i = 0; i < areaCount; i++) {
            String name = areaNames[i];
            if (name == null || name.isEmpty()) continue;
            double[] b = areaBox[i];
            if (!onScreen(b, proj, w, h)) continue;
            float across = Math.max(proj.x(b[3]) - proj.x(b[1]),
                                    proj.y(b[0]) - proj.y(b[2]));
            if (across < LABEL_MIN_PX) continue;
            float cx = (proj.x(b[1]) + proj.x(b[3])) / 2f;
            float cy = (proj.y(b[0]) + proj.y(b[2])) / 2f;
            if (cx < 0 || cx > w || cy < 0 || cy > h) continue;
            String s = name.length() > 22 ? name.substring(0, 20) + "…" : name;
            canvas.drawText(s, cx - textPaint.measureText(s) / 2f, cy, textPaint);
        }
    }

    private void drawRoads(Canvas canvas, Proj proj, int w, int h) {
        // Minor roads are clutter when the whole run is on screen; they are the
        // street plan when it is not.
        int minRank = zoom < 1.4f ? 2 : 0;
        for (int pass = 0; pass < 2; pass++) {
            for (int i = 0; i < roadCount; i++) {
                double[][] pts = roadPts[i];
                if (pts == null || pts.length < 2) continue;
                int rank = roadRanks[i];
                if (rank < minRank) continue;
                if (!onScreen(roadBox[i], proj, w, h)) continue;

                float cw, fw; int cc, fc;
                if (rank >= 4)      { cw = dp(7);   fw = dp(4.5f); cc = Theme.roadCaseHi;  fc = Theme.roadFillHi; }
                else if (rank >= 3) { cw = dp(5.5f);fw = dp(3.5f); cc = Theme.roadCaseMid; fc = Theme.roadFillMid; }
                else if (rank >= 2) { cw = dp(4);   fw = dp(2.5f); cc = Theme.roadCaseLo;  fc = Theme.roadFillMid; }
                else                { cw = dp(2.5f);fw = dp(1.5f); cc = Theme.roadCaseLo;  fc = Theme.roadFillLo; }

                trace(proj, pts, false);
                if (pass == 0) { roadCase.setColor(cc); roadCase.setStrokeWidth(cw); canvas.drawPath(scratch, roadCase); }
                else           { roadFill.setColor(fc); roadFill.setStrokeWidth(fw); canvas.drawPath(scratch, roadFill); }
            }
        }
    }

    private void drawPlaces(Canvas canvas, Proj proj, int w, int h) {
        if (placePts == null) return;
        textPaint.setColor(Theme.roadText);
        textPaint.setTextSize(dp(10));
        textPaint.setTypeface(Typeface.DEFAULT_BOLD);
        for (int i = 0; i < placeCount; i++) {
            float px = proj.x(placePts[i][1]), py = proj.y(placePts[i][0]);
            if (px < 0 || px > w || py < 0 || py > h) continue;
            dotPaint.setColor(Theme.roadText);
            canvas.drawCircle(px, py, dp(2.5f), dotPaint);
            String s = placeNames[i];
            canvas.drawText(s, px - textPaint.measureText(s) / 2f, py - dp(6), textPaint);
        }
        textPaint.setTypeface(Typeface.DEFAULT);
    }

    private void trace(Proj proj, double[][] pts, boolean close) {
        scratch.rewind();
        scratch.moveTo(proj.x(pts[0][1]), proj.y(pts[0][0]));
        for (int j = 1; j < pts.length; j++) {
            scratch.lineTo(proj.x(pts[j][1]), proj.y(pts[j][0]));
        }
        if (close) scratch.close();
    }

    private void drawTrail(Canvas canvas, Proj proj, double[] lat, double[] lon, Paint paint) {
        if (lat.length < 2) return;
        scratch.rewind();
        scratch.moveTo(proj.x(lon[0]), proj.y(lat[0]));
        for (int i = 1; i < lat.length; i++) scratch.lineTo(proj.x(lon[i]), proj.y(lat[i]));
        canvas.drawPath(scratch, paint);
    }

    private void drawHeads(Canvas canvas, Proj proj) {
        if (witLat.length > 0) {
            float x = proj.x(witLon[witLon.length - 1]), y = proj.y(witLat[witLat.length - 1]);
            dotPaint.setColor(Theme.headWitness);
            canvas.drawCircle(x, y, dp(7), dotPaint);
            strokePt.setColor(Theme.mapBg); strokePt.setStrokeWidth(dp(2));
            canvas.drawCircle(x, y, dp(7), strokePt);
        }
        if (gnsLat.length > 0) {
            float x = proj.x(gnsLon[gnsLon.length - 1]), y = proj.y(gnsLat[gnsLat.length - 1]);
            dotPaint.setColor(Theme.headClaimed);
            canvas.drawCircle(x, y, dp(6), dotPaint);
            strokePt.setColor(Theme.mapBg); strokePt.setStrokeWidth(dp(2));
            canvas.drawCircle(x, y, dp(6), strokePt);

            textPaint.setTextSize(dp(10));
            textPaint.setTypeface(Typeface.DEFAULT_BOLD);
            float tw = textPaint.measureText(vehicleId);
            float lx = x + dp(13), ly = y + dp(4);
            pill.set(lx - dp(5), ly - dp(12), lx + tw + dp(5), ly + dp(4));
            cardPaint.setColor(Theme.cardBg);
            canvas.drawRoundRect(pill, dp(5), dp(5), cardPaint);
            textPaint.setColor(Theme.cardText);
            canvas.drawText(vehicleId, lx, ly, textPaint);
            textPaint.setTypeface(Typeface.DEFAULT);
        }
    }

    private void drawGap(Canvas canvas, Proj proj) {
        if (gnsLat.length == 0 || witLat.length == 0) return;
        float gx = proj.x(gnsLon[gnsLon.length - 1]), gy = proj.y(gnsLat[gnsLat.length - 1]);
        float wx = proj.x(witLon[witLon.length - 1]), wy = proj.y(witLat[witLat.length - 1]);
        gapPaint.setColor(Theme.gap);
        canvas.drawLine(gx, gy, wx, wy, gapPaint);

        String lbl = String.format("%.0f m apart", gapM);
        textPaint.setTextSize(dp(11));
        textPaint.setTypeface(Typeface.DEFAULT_BOLD);
        float tw = textPaint.measureText(lbl);
        float mx = (gx + wx) / 2f, my = (gy + wy) / 2f - dp(8);
        pill.set(mx - tw / 2f - dp(6), my - dp(13), mx + tw / 2f + dp(6), my + dp(4));
        cardPaint.setColor(Theme.cardBg);
        canvas.drawRoundRect(pill, dp(6), dp(6), cardPaint);
        textPaint.setColor(Theme.gap);
        canvas.drawText(lbl, mx - tw / 2f, my, textPaint);
        textPaint.setTypeface(Typeface.DEFAULT);
    }

    // --- overlays ----------------------------------------------------------

    private void drawSensorCard(Canvas canvas, int w) {
        float cx = dp(10), cy = dp(10), cardW = dp(178), rowH = dp(19);
        pill.set(cx, cy, cx + cardW, cy + dp(14) + 4 * rowH);
        cardPaint.setColor(Theme.cardBg);
        canvas.drawRoundRect(pill, dp(8), dp(8), cardPaint);

        float tx = cx + dp(9), ty = cy + dp(18), right = cx + cardW - dp(9);
        textPaint.setTextSize(dp(10));

        textPaint.setTypeface(Typeface.DEFAULT_BOLD);
        textPaint.setColor(Theme.cardText);
        canvas.drawText("Own sensors", tx, ty, textPaint);
        textPaint.setColor(Theme.cardOk);
        canvas.drawText("the truth", right - textPaint.measureText("the truth"), ty, textPaint);
        textPaint.setTypeface(Typeface.DEFAULT);
        ty += rowH;

        row(canvas, tx, ty, right, Theme.headClaimed, "GPS",
            gpsM > 0.1f ? String.format("%.0f m from us", gpsM) : "on our estimate",
            gpsM > 10f);
        ty += rowH;

        row(canvas, tx, ty, right, Theme.witness, "Compass",
            Float.isNaN(compassDeg) ? "—" : String.format("%.0f° off", Math.abs(compassDeg)),
            !Float.isNaN(compassDeg) && Math.abs(compassDeg) > 20f);
        ty += rowH;

        row(canvas, tx, ty, right, Theme.cardOk, "Height",
            Math.abs(vertM) > 0.5f ? String.format("%.0f m off", Math.abs(vertM)) : "ok",
            Math.abs(vertM) > 5f);
    }

    private void row(Canvas canvas, float tx, float ty, float right,
                     int swatch, String label, String value, boolean warn) {
        strokePt.setColor(swatch);
        strokePt.setStrokeWidth(dp(2.5f));
        canvas.drawLine(tx, ty - dp(4), tx + dp(13), ty - dp(4), strokePt);
        textPaint.setColor(Theme.cardText);
        canvas.drawText(label, tx + dp(19), ty, textPaint);
        textPaint.setColor(warn ? Theme.cardWarn : Theme.cardText);
        canvas.drawText(value, right - textPaint.measureText(value), ty, textPaint);
    }

    private void drawCompassRose(Canvas canvas, int w) {
        float r = dp(30), cx = w - dp(12) - r, cy = dp(12) + r;
        cardPaint.setColor(Theme.cardBg);
        canvas.drawCircle(cx, cy, r, cardPaint);

        strokePt.setColor(Theme.ring);
        for (int d = 0; d < 360; d += 45) {
            double rad = Math.toRadians(d);
            float inner = r - dp(d % 90 == 0 ? 7 : 4);
            strokePt.setStrokeWidth(dp(d % 90 == 0 ? 2 : 1));
            canvas.drawLine((float) (cx + inner * Math.sin(rad)), (float) (cy - inner * Math.cos(rad)),
                            (float) (cx + r * Math.sin(rad)),     (float) (cy - r * Math.cos(rad)), strokePt);
        }

        textPaint.setColor(Theme.north);
        textPaint.setTextSize(dp(11));
        textPaint.setTypeface(Typeface.DEFAULT_BOLD);
        canvas.drawText("N", cx - textPaint.measureText("N") / 2f, cy - r + dp(15), textPaint);
        textPaint.setTypeface(Typeface.DEFAULT);

        if (!Float.isNaN(headingDeg)) {
            double rad = Math.toRadians(headingDeg);
            strokePt.setColor(Theme.witness);
            strokePt.setStrokeWidth(dp(2.5f));
            canvas.drawLine(cx, cy,
                    (float) (cx + (r - dp(8)) * Math.sin(rad)),
                    (float) (cy - (r - dp(8)) * Math.cos(rad)), strokePt);
        }
    }

    private void drawScaleBar(Canvas canvas, Proj proj, int h) {
        double lat = witLat.length > 0 ? witLat[witLat.length - 1]
                   : gnsLat.length > 0 ? gnsLat[gnsLat.length - 1] : 12.83;
        double mPerPx = (1.0 / proj.scaleX) * 111320.0 * Math.cos(Math.toRadians(lat));
        double target = dp(70) * mPerPx;
        double[] nice = {1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000};
        double barM = nice[0];
        for (double v : nice) if (v <= target * 1.4) barM = v;
        float barPx = (float) (barM / mPerPx);

        float bx = dp(12), by = h - dp(14);
        // Drawn in the map's own text colour. The first version used a fixed
        // dark grey, which on a dark map is invisible.
        strokePt.setColor(Theme.roadText);
        strokePt.setStrokeWidth(dp(2));
        canvas.drawLine(bx, by, bx + barPx, by, strokePt);
        canvas.drawLine(bx, by - dp(5), bx, by + dp(2), strokePt);
        canvas.drawLine(bx + barPx, by - dp(5), bx + barPx, by + dp(2), strokePt);

        String lbl = barM >= 1000 ? String.format("%.0f km", barM / 1000) : String.format("%.0f m", barM);
        textPaint.setColor(Theme.roadText);
        textPaint.setTextSize(dp(10));
        canvas.drawText(lbl, bx + barPx / 2f - textPaint.measureText(lbl) / 2f, by - dp(7), textPaint);
    }

    // Button geometry lives in one place so the drawing and the hit test
    // cannot drift apart — they already had, by five pixels.
    private float btnX(int w)   { return w - dp(8) - dp(78); }
    private float btnW()        { return dp(78); }
    private float btnSize()     { return dp(38); }
    private float btnTop(int h) { return h / 2f - btnSize() * 2f; }
    private float btnY(int h, int i) { return btnTop(h) + i * (btnSize() + dp(8)); }

    private void drawButtons(Canvas canvas, int w, int h) {
        String[] labels = {"+", "−", "Whole run", "Follow"};
        for (int i = 0; i < 4; i++) {
            float x = btnX(w), y = btnY(h, i);
            boolean lit = i == 3 && following;
            pill.set(x, y, x + btnW(), y + btnSize());
            cardPaint.setColor(lit ? Theme.witness : Theme.cardBg);
            canvas.drawRoundRect(pill, dp(8), dp(8), cardPaint);
            textPaint.setColor(lit ? Theme.mapBg : Theme.cardText);
            textPaint.setTextSize(i < 2 ? dp(20) : dp(12));
            textPaint.setTypeface(Typeface.DEFAULT_BOLD);
            canvas.drawText(labels[i],
                    x + btnW() / 2f - textPaint.measureText(labels[i]) / 2f,
                    y + btnSize() / 2f + dp(i < 2 ? 7 : 4), textPaint);
        }
        textPaint.setTypeface(Typeface.DEFAULT);
    }

    private void drawPlaceholder(Canvas canvas, int w, int h) {
        textPaint.setColor(Theme.ink3);
        textPaint.setTextSize(dp(13));
        String msg = hasMap ? "Waiting for the vehicle…" : "Connect to see the live map";
        canvas.drawText(msg, (w - textPaint.measureText(msg)) / 2f, h / 2f, textPaint);
    }

    // --- touch -------------------------------------------------------------

    public interface OnMapClickListener { void onMapClick(); }
    private OnMapClickListener clickListener;
    public void setOnMapClickListener(OnMapClickListener l) { clickListener = l; }

    private float downX, downY;

    @Override
    public boolean onTouchEvent(MotionEvent e) {
        int n = e.getPointerCount();
        switch (e.getActionMasked()) {
            case MotionEvent.ACTION_DOWN:
                downX = lastTX = e.getX(); downY = lastTY = e.getY();
                fingers = 1;
                return true;
            case MotionEvent.ACTION_POINTER_DOWN:
                fingers = n;
                if (n == 2) { pinchD0 = pinchDist(e); zoom0 = zoom; }
                return true;
            case MotionEvent.ACTION_MOVE:
                if (fingers == 1 && n == 1) {
                    float dx = e.getX() - lastTX, dy = e.getY() - lastTY;
                    if (Math.abs(dx) > 2 || Math.abs(dy) > 2) following = false;
                    panX += dx; panY += dy;
                    lastTX = e.getX(); lastTY = e.getY();
                    invalidate();
                } else if (fingers >= 2 && n >= 2 && pinchD0 > 0) {
                    zoom = clamp(zoom0 * pinchDist(e) / pinchD0);
                    invalidate();
                }
                return true;
            case MotionEvent.ACTION_UP:
                boolean tap = Math.abs(e.getX() - downX) < dp(10)
                           && Math.abs(e.getY() - downY) < dp(10);
                fingers = 0;
                if (tap && !hitButton(e.getX(), e.getY()) && clickListener != null) {
                    clickListener.onMapClick();
                }
                return true;
            case MotionEvent.ACTION_POINTER_UP:
                fingers = Math.max(0, fingers - 1);
                return true;
        }
        return super.onTouchEvent(e);
    }

    private boolean hitButton(float tx, float ty) {
        int w = getWidth(), h = getHeight();
        if (tx < btnX(w) || tx > btnX(w) + btnW()) return false;
        for (int i = 0; i < 4; i++) {
            float y = btnY(h, i);
            if (ty < y || ty > y + btnSize()) continue;
            switch (i) {
                case 0: zoom = clamp(zoom * 1.5f); break;
                case 1: zoom = clamp(zoom / 1.5f); break;
                case 2: zoom = 1f; panX = 0; panY = 0; following = false; break;
                case 3: following = !following; panX = 0; panY = 0; break;
            }
            invalidate();
            return true;
        }
        return false;
    }

    private static float clamp(float z) { return Math.max(0.3f, Math.min(12f, z)); }

    private float pinchDist(MotionEvent e) {
        float dx = e.getX(0) - e.getX(1), dy = e.getY(0) - e.getY(1);
        return (float) Math.sqrt(dx * dx + dy * dy);
    }

    // --- helpers -----------------------------------------------------------

    private static double[][] points(JSONArray arr) {
        if (arr == null) return new double[0][2];
        double[][] out = new double[arr.length()][2];
        for (int i = 0; i < arr.length(); i++) {
            try {
                JSONArray p = arr.getJSONArray(i);
                out[i][0] = p.getDouble(0);
                out[i][1] = p.getDouble(1);
            } catch (Exception ignored) {}
        }
        return out;
    }

    private static void bound(double[][] pts, double[] box) {
        box[0] = box[1] = Double.MAX_VALUE;
        box[2] = box[3] = -Double.MAX_VALUE;
        for (double[] p : pts) {
            if (p[0] < box[0]) box[0] = p[0];
            if (p[1] < box[1]) box[1] = p[1];
            if (p[0] > box[2]) box[2] = p[0];
            if (p[1] > box[3]) box[3] = p[1];
        }
    }

    private double[] col(JSONArray arr, int c) {
        if (arr == null) return new double[0];
        double[] out = new double[arr.length()];
        int n = 0;
        for (int i = 0; i < arr.length(); i++) {
            try { out[n++] = arr.getJSONArray(i).getDouble(c); } catch (Exception ignored) {}
        }
        double[] r = new double[n];
        System.arraycopy(out, 0, r, 0, n);
        return r;
    }

    private float dp(float v) { return v * getResources().getDisplayMetrics().density; }

    private BBox trailBox() {
        BBox b = new BBox();
        if (gnsLat.length > 0) b.extend(gnsLat, gnsLon);
        if (witLat.length > 0) b.extend(witLat, witLon);
        if (b.valid()) {
            if (b.maxLat - b.minLat < 4e-4) { b.maxLat += 2e-4; b.minLat -= 2e-4; }
            if (b.maxLon - b.minLon < 4e-4) { b.maxLon += 2e-4; b.minLon -= 2e-4; }
        }
        return b;
    }

    private BBox roadBoxAll() {
        BBox b = new BBox();
        if (roadBox == null) return b;
        for (int i = 0; i < roadCount; i++) {
            double[] r = roadBox[i];
            if (r[0] < b.minLat) b.minLat = r[0];
            if (r[1] < b.minLon) b.minLon = r[1];
            if (r[2] > b.maxLat) b.maxLat = r[2];
            if (r[3] > b.maxLon) b.maxLon = r[3];
        }
        return b;
    }

    private static class BBox {
        double minLat = Double.MAX_VALUE, maxLat = -Double.MAX_VALUE;
        double minLon = Double.MAX_VALUE, maxLon = -Double.MAX_VALUE;
        void extend(double[] la, double[] lo) {
            for (int i = 0; i < la.length; i++) {
                if (la[i] < minLat) minLat = la[i];
                if (la[i] > maxLat) maxLat = la[i];
                if (lo[i] < minLon) minLon = lo[i];
                if (lo[i] > maxLon) maxLon = lo[i];
            }
        }
        boolean valid() { return minLat != Double.MAX_VALUE; }
    }

    private static class Proj {
        final double latMin, lonMin, latRange;
        final float scaleX, scaleY, ox, oy;
        Proj(BBox b, int w, int h, float mg, float zoom, float panX, float panY) {
            latMin = b.minLat; lonMin = b.minLon;
            latRange = b.maxLat - b.minLat;
            double lonRange = b.maxLon - b.minLon;
            double cos = Math.cos(Math.toRadians((b.minLat + b.maxLat) / 2.0));
            float cw = w - 2 * mg, ch = h - 2 * mg;
            float base = Math.min((float) (cw / lonRange * cos), (float) (ch / latRange)) * zoom;
            scaleX = (float) (base / cos);
            scaleY = base;
            ox = mg + (cw - (float) (lonRange * scaleX)) / 2f + panX;
            oy = mg + (ch - (float) (latRange * scaleY)) / 2f + panY;
        }
        float x(double lon) { return ox + (float) ((lon - lonMin) * scaleX); }
        float y(double lat) { return oy + (float) (latRange * scaleY - (lat - latMin) * scaleY); }
    }

    private void initPaints() {
        areaPaint.setStyle(Paint.Style.FILL);
        areaEdge.setStyle(Paint.Style.STROKE);
        areaEdge.setStrokeWidth(dp(1));

        roadCase.setStyle(Paint.Style.STROKE);
        roadCase.setStrokeCap(Paint.Cap.ROUND);
        roadCase.setStrokeJoin(Paint.Join.ROUND);
        roadFill.setStyle(Paint.Style.STROKE);
        roadFill.setStrokeCap(Paint.Cap.ROUND);
        roadFill.setStrokeJoin(Paint.Join.ROUND);

        trailGps.setColor(Theme.claimed);
        trailGps.setStyle(Paint.Style.STROKE);
        trailGps.setStrokeWidth(dp(3.5f));
        trailGps.setStrokeCap(Paint.Cap.ROUND);
        trailGps.setStrokeJoin(Paint.Join.ROUND);

        trailWit.setColor(Theme.witness);
        trailWit.setStyle(Paint.Style.STROKE);
        trailWit.setStrokeWidth(dp(3.5f));
        trailWit.setStrokeCap(Paint.Cap.ROUND);
        trailWit.setStrokeJoin(Paint.Join.ROUND);

        gapPaint.setStyle(Paint.Style.STROKE);
        gapPaint.setStrokeWidth(dp(2));
        gapPaint.setPathEffect(new DashPathEffect(new float[]{dp(9), dp(6)}, 0));

        dotPaint.setStyle(Paint.Style.FILL);
        cardPaint.setStyle(Paint.Style.FILL);
        strokePt.setStyle(Paint.Style.STROKE);
        strokePt.setStrokeCap(Paint.Cap.ROUND);
    }
}
