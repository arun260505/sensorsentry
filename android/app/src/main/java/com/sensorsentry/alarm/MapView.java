package com.sensorsentry.alarm;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
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
 * Full-featured map canvas for SensorSentry.
 *
 * Renders an OSM-inspired road network from /basemap, live GPS and witness
 * trails, vehicle label, sensor-info card, compass rose, scale bar, and
 * zoom/pan touch controls -- all on a single hardware-accelerated canvas
 * with zero external libraries.
 */
public class MapView extends View {

    // --- Dark console-style map palette ----------------------------------------
    private static final int C_MAP_BG       = 0xFF0D1E18;  // deep forest-green terrain
    private static final int C_ROAD_CASE_HI  = 0xFF3A2A18; // dark amber border (major road)
    private static final int C_ROAD_FILL_HI  = 0xFF7A5830; // amber fill (major road)
    private static final int C_ROAD_CASE_MID = 0xFF25201A;
    private static final int C_ROAD_FILL_MID = 0xFF3C3225;
    private static final int C_ROAD_FILL_LO  = 0xFF191D14;
    private static final int C_ROAD_CASE_LO  = 0xFF1E2218;

    // --- Trail colours (bright against dark background) -----------------------
    private static final int C_GPS          = 0xFFE8A020;  // bright amber/gold
    private static final int C_WITNESS      = 0xFF18A8D8;  // bright steel blue
    private static final int C_GAP          = 0xFFFF5040;  // bright red-orange
    private static final int C_HEAD_GPS     = 0xFFFFCC00;  // bright yellow dot
    private static final int C_HEAD_WIT     = 0xFF00D8FF;  // bright cyan dot

    // --- Overlay card colours --------------------------------------------------
    private static final int C_CARD_BG      = 0xEA0A1620;  // slightly darker card
    private static final int C_CARD_TEXT    = 0xFFCCDDE8;
    private static final int C_CARD_OK      = 0xFF5FBFAA;
    private static final int C_CARD_WARN    = 0xFFE88840;
    private static final int C_NORTH        = 0xFFE84040;

    // --- Paints ----------------------------------------------------------------
    private final Paint roadCase   = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint roadFill   = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint trailGps   = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint trailWit   = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint gapPaint   = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint dotPaint   = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint textPaint  = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint cardPaint  = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint bgPaint    = new Paint();

    // --- Road / basemap data (parsed once) ------------------------------------
    private int        roadCount  = 0;
    private double[][][] roadPts  = null;   // [road][point][0=lat 1=lon]
    private int[]      roadRanks  = null;
    private int        placeCount = 0;
    private double[][] placePts   = null;
    private String[]   placeNames = null;
    private boolean    hasMap     = false;

    // --- Live data -------------------------------------------------------------
    private double[] gnsLat = {}, gnsLon = {};
    private double[] witLat = {}, witLon = {};
    private float    gapM       = 0f;
    private float    headingDeg = Float.NaN;
    private float    gpsM       = 0f;
    private float    vertM      = 0f;
    private float    compassDeg = Float.NaN;
    private String   vehicleId  = "VEHICLE";
    private boolean  hasData    = false;

    // --- Zoom / pan ------------------------------------------------------------
    private float zoom = 1f, panX = 0f, panY = 0f;
    private float pinchD0 = -1f, zoom0 = 1f, lastTX, lastTY;
    private int   fingers = 0;

    // ===========================================================================
    public MapView(Context context) {
        super(context);
        setLayerType(LAYER_TYPE_HARDWARE, null);
        initPaints();
    }

    // --- Public API ------------------------------------------------------------

    public void setTrails(JSONArray gnss, JSONArray wit, float sep) {
        gnsLat = col(gnss, 0); gnsLon = col(gnss, 1);
        witLat = col(wit,  0); witLon = col(wit,  1);
        gapM   = sep;
        hasData = gnsLat.length > 0 || witLat.length > 0;
        invalidate();
    }

    public void setBasemap(String json) {
        if (json == null || json.isEmpty()) return;
        try {
            JSONObject bm = new JSONObject(json);
            JSONArray  rs = bm.optJSONArray("roads");
            if (rs != null) {
                roadCount = rs.length();
                roadPts   = new double[roadCount][][];
                roadRanks = new int[roadCount];
                for (int i = 0; i < roadCount; i++) {
                    JSONObject r = rs.getJSONObject(i);
                    roadRanks[i] = r.optInt("rank", 1);
                    JSONArray pts = r.optJSONArray("points");
                    if (pts != null) {
                        roadPts[i] = new double[pts.length()][2];
                        for (int j = 0; j < pts.length(); j++) {
                            JSONArray p = pts.getJSONArray(j);
                            roadPts[i][j][0] = p.getDouble(0);
                            roadPts[i][j][1] = p.getDouble(1);
                        }
                    } else roadPts[i] = new double[0][2];
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
                    if (at != null) { placePts[i][0] = at.getDouble(0); placePts[i][1] = at.getDouble(1); }
                    placeNames[i] = p.optString("name", "");
                }
            }
            hasMap = true;
            invalidate();
        } catch (Exception ignored) {}
    }

    public void setSensorInfo(float gpsMetres, float vertMetres, float cmpDeg) {
        gpsM = gpsMetres; vertM = vertMetres; compassDeg = cmpDeg;
        invalidate();
    }

    public void setHeading(float deg)      { headingDeg = deg; invalidate(); }
    public void setVehicleId(String id)    { vehicleId = id;   invalidate(); }

    // --- Draw ------------------------------------------------------------------

    @Override
    protected void onDraw(Canvas canvas) {
        int w = getWidth(), h = getHeight();
        bgPaint.setColor(C_MAP_BG);
        canvas.drawRect(0, 0, w, h, bgPaint);

        BBox box = trailBox();
        if (!box.valid() && hasMap && roadCount > 0) box = roadBox();
        if (!box.valid()) { drawPlaceholder(canvas, w, h); drawOverlays(canvas, w, h, null); return; }

        float mg = dp(16);
        Proj proj = new Proj(box, w, h, mg, zoom, panX, panY);

        if (hasMap)  drawRoads(canvas, proj, w, h);
        drawTrail(canvas, proj, witLat, witLon, trailWit);
        drawTrail(canvas, proj, gnsLat, gnsLon, trailGps);
        drawHeads(canvas, proj);
        if (gapM > 0.5f) drawGap(canvas, proj);

        drawOverlays(canvas, w, h, proj);
    }

    private void drawRoads(Canvas canvas, Proj proj, int w, int h) {
        for (int pass = 0; pass < 2; pass++) {
            for (int i = 0; i < roadCount; i++) {
                if (roadPts[i] == null || roadPts[i].length < 2) continue;
                int rank = roadRanks[i];
                float cw, fw; int cc, fc;
                if      (rank >= 4) { cw = dp(8); fw = dp(5.5f); cc = C_ROAD_CASE_HI;  fc = C_ROAD_FILL_HI; }
                else if (rank >= 3) { cw = dp(6); fw = dp(4);    cc = C_ROAD_CASE_MID; fc = C_ROAD_FILL_MID; }
                else if (rank >= 2) { cw = dp(4); fw = dp(2.5f); cc = C_ROAD_CASE_LO;  fc = C_ROAD_FILL_MID; }
                else                { cw = dp(3); fw = dp(1.5f); cc = C_ROAD_CASE_LO;  fc = C_ROAD_FILL_LO; }
                Path path = roadPath(proj, roadPts[i]);
                if (pass == 0) { roadCase.setColor(cc); roadCase.setStrokeWidth(cw); canvas.drawPath(path, roadCase); }
                else           { roadFill.setColor(fc); roadFill.setStrokeWidth(fw); canvas.drawPath(path, roadFill); }
            }
        }
        // Place labels
        if (placePts != null) {
            textPaint.setColor(0xFFB8AA88); textPaint.setTextSize(dp(9));
            for (int i = 0; i < placeCount; i++) {
                float px = proj.x(placePts[i][1]), py = proj.y(placePts[i][0]);
                if (px > 0 && px < w && py > 0 && py < h)
                    canvas.drawText(placeNames[i], px - textPaint.measureText(placeNames[i])/2f, py, textPaint);
            }
        }
    }

    private Path roadPath(Proj proj, double[][] pts) {
        Path p = new Path();
        p.moveTo(proj.x(pts[0][1]), proj.y(pts[0][0]));
        for (int j = 1; j < pts.length; j++) p.lineTo(proj.x(pts[j][1]), proj.y(pts[j][0]));
        return p;
    }

    private void drawTrail(Canvas canvas, Proj proj, double[] lats, double[] lons, Paint paint) {
        if (lats.length < 2) return;
        Path p = new Path();
        p.moveTo(proj.x(lons[0]), proj.y(lats[0]));
        for (int i = 1; i < lats.length; i++) p.lineTo(proj.x(lons[i]), proj.y(lats[i]));
        canvas.drawPath(p, paint);
    }

    private void drawHeads(Canvas canvas, Proj proj) {
        if (witLat.length > 0) {
            dotPaint.setColor(C_HEAD_WIT);
            canvas.drawCircle(proj.x(witLon[witLon.length-1]), proj.y(witLat[witLat.length-1]), dp(8), dotPaint);
        }
        if (gnsLat.length > 0) {
            float hx = proj.x(gnsLon[gnsLon.length-1]), hy = proj.y(gnsLat[gnsLat.length-1]);
            dotPaint.setColor(C_HEAD_GPS);
            canvas.drawCircle(hx, hy, dp(6), dotPaint);
            // Vehicle label pill
            textPaint.setTextSize(dp(10)); textPaint.setTypeface(Typeface.DEFAULT_BOLD);
            float tw = textPaint.measureText(vehicleId);
            float lx = hx + dp(13), ly = hy + dp(4);
            RectF pill = new RectF(lx - dp(4), ly - dp(12), lx + tw + dp(4), ly + dp(3));
            cardPaint.setColor(C_CARD_BG); canvas.drawRoundRect(pill, dp(5), dp(5), cardPaint);
            textPaint.setColor(C_CARD_TEXT); canvas.drawText(vehicleId, lx, ly, textPaint);
            textPaint.setTypeface(Typeface.DEFAULT);
        }
    }

    private void drawGap(Canvas canvas, Proj proj) {
        if (gnsLat.length == 0 || witLat.length == 0) return;
        float gx = proj.x(gnsLon[gnsLon.length-1]), gy = proj.y(gnsLat[gnsLat.length-1]);
        float wx = proj.x(witLon[witLon.length-1]),  wy = proj.y(witLat[witLat.length-1]);
        canvas.drawLine(gx, gy, wx, wy, gapPaint);
        String lbl = String.format("%.1f m apart", gapM);
        textPaint.setColor(C_GAP); textPaint.setTextSize(dp(10));
        float tw = textPaint.measureText(lbl);
        float mx = (gx+wx)/2f, my = (gy+wy)/2f - dp(7);
        RectF pill = new RectF(mx-tw/2f-dp(4), my-dp(12), mx+tw/2f+dp(4), my+dp(3));
        cardPaint.setColor(0xEE1A0000); canvas.drawRoundRect(pill, dp(5), dp(5), cardPaint);
        canvas.drawText(lbl, mx-tw/2f, my, textPaint);
    }

    // --- Overlays (drawn on top of everything) ---------------------------------

    private void drawOverlays(Canvas canvas, int w, int h, Proj proj) {
        drawSensorCard(canvas, w);
        drawCompassRose(canvas, w);
        if (proj != null) drawScaleBar(canvas, proj, w, h);
        drawZoomButtons(canvas, w, h);
    }

    private void drawSensorCard(Canvas canvas, int w) {
        int cx = dp(10), cy = dp(10);
        int cardW = dp(170), rowH = dp(19);
        int cardH = dp(14) + 4 * rowH + dp(6);
        RectF rect = new RectF(cx, cy, cx + cardW, cy + cardH);
        cardPaint.setColor(C_CARD_BG); canvas.drawRoundRect(rect, dp(7), dp(7), cardPaint);

        float tx = cx + dp(8), ty = cy + dp(17);
        textPaint.setTextSize(dp(10));

        // Header row
        textPaint.setTypeface(Typeface.DEFAULT_BOLD);
        textPaint.setColor(C_CARD_TEXT); canvas.drawText("Own sensors", tx, ty, textPaint);
        String truth = "the truth";
        textPaint.setColor(C_CARD_OK);
        canvas.drawText(truth, cx + cardW - dp(8) - textPaint.measureText(truth), ty, textPaint);
        textPaint.setTypeface(Typeface.DEFAULT);
        ty += rowH;

        // GPS row
        drawSwatch(canvas, tx, ty, C_HEAD_GPS, false);
        textPaint.setColor(C_CARD_TEXT); canvas.drawText("GPS", tx + dp(18), ty, textPaint);
        String gpsStr = gpsM > 0.1f ? String.format("%.0f m from our estimate", gpsM) : "on our estimate";
        textPaint.setColor(gpsM > 10 ? C_CARD_WARN : C_CARD_TEXT);
        float sw = textPaint.measureText(gpsStr);
        canvas.drawText(gpsStr, cx + cardW - dp(8) - sw, ty, textPaint);
        ty += rowH;

        // Compass row
        drawSwatch(canvas, tx, ty, 0xFF9080C8, false);
        textPaint.setColor(C_CARD_TEXT); canvas.drawText("Compass", tx + dp(18), ty, textPaint);
        String cmpStr = Float.isNaN(compassDeg) ? "---" : String.format("%.0f\u00b0 off", Math.abs(compassDeg));
        textPaint.setColor((!Float.isNaN(compassDeg) && Math.abs(compassDeg) > 20) ? C_CARD_WARN : C_CARD_TEXT);
        canvas.drawText(cmpStr, cx + cardW - dp(8) - textPaint.measureText(cmpStr), ty, textPaint);
        ty += rowH;

        // Height row
        dotPaint.setColor(0xFF80C8E0);
        textPaint.setColor(C_CARD_TEXT); canvas.drawText("Height", tx + dp(18), ty, textPaint);
        String hStr = vertM > 0.5f ? String.format("%.0f m off", vertM) : "ok";
        textPaint.setColor(vertM > 5 ? C_CARD_WARN : C_CARD_OK);
        canvas.drawText(hStr, cx + cardW - dp(8) - textPaint.measureText(hStr), ty, textPaint);
    }

    private void drawSwatch(Canvas canvas, float x, float y, int color, boolean dashed) {
        Paint sp = new Paint(Paint.ANTI_ALIAS_FLAG);
        sp.setColor(color); sp.setStyle(Paint.Style.STROKE); sp.setStrokeWidth(dp(2));
        if (dashed) sp.setPathEffect(new DashPathEffect(new float[]{dp(4), dp(3)}, 0));
        canvas.drawLine(x, y - dp(4), x + dp(14), y - dp(4), sp);
    }

    private void drawCompassRose(Canvas canvas, int w) {
        int r = dp(32), cx = w - dp(12) - r, cy = dp(12) + r;
        cardPaint.setColor(C_CARD_BG); canvas.drawCircle(cx, cy, r, cardPaint);

        Paint ring = new Paint(Paint.ANTI_ALIAS_FLAG);
        ring.setColor(0xFF3A5A7A); ring.setStyle(Paint.Style.STROKE); ring.setStrokeWidth(dp(1.5f));
        canvas.drawCircle(cx, cy, r, ring);

        // Cardinal ticks
        for (int d = 0; d < 360; d += 45) {
            double rad = Math.toRadians(d); float inner = r - dp(d % 90 == 0 ? 7 : 4);
            float x1 = (float)(cx + inner * Math.sin(rad)), y1 = (float)(cy - inner * Math.cos(rad));
            float x2 = (float)(cx + r    * Math.sin(rad)), y2 = (float)(cy - r    * Math.cos(rad));
            ring.setStrokeWidth(dp(d % 90 == 0 ? 2 : 1));
            canvas.drawLine(x1, y1, x2, y2, ring);
        }

        // N label
        textPaint.setColor(C_NORTH); textPaint.setTextSize(dp(12)); textPaint.setTypeface(Typeface.DEFAULT_BOLD);
        float nw = textPaint.measureText("N");
        canvas.drawText("N", cx - nw/2f, cy - r + dp(16), textPaint);
        textPaint.setTypeface(Typeface.DEFAULT);

        // Heading needle
        if (!Float.isNaN(headingDeg)) {
            double rad = Math.toRadians(headingDeg);
            float nx = (float)(cx + (r-dp(8)) * Math.sin(rad)), ny = (float)(cy - (r-dp(8)) * Math.cos(rad));
            Paint needle = new Paint(Paint.ANTI_ALIAS_FLAG);
            needle.setColor(0xFFFFFFFF); needle.setStrokeWidth(dp(2));
            canvas.drawLine(cx, cy, nx, ny, needle);
            // Tail (opposite direction)
            float tx2 = (float)(cx - (r/3f) * Math.sin(rad)), ty2 = (float)(cy + (r/3f) * Math.cos(rad));
            needle.setColor(0x88FFFFFF);
            canvas.drawLine(cx, cy, tx2, ty2, needle);
        }
    }

    private void drawScaleBar(Canvas canvas, Proj proj, int w, int h) {
        double latRef = witLat.length > 0 ? witLat[witLat.length-1] :
                        gnsLat.length > 0 ? gnsLat[gnsLat.length-1] : 13.0;
        double mPerDeg = 111320.0 * Math.cos(Math.toRadians(latRef));
        double mPerPx  = (1.0 / proj.scaleX) * mPerDeg;
        double barTarget = dp(60) * mPerPx;
        double[] nice = {1,2,5,10,20,50,100,200,500,1000,2000,5000};
        double barM = nice[0];
        for (double v : nice) { if (v <= barTarget * 1.4) barM = v; }
        float barPx = (float)(barM / mPerPx);

        int bx = dp(10), by = h - dp(12);
        Paint sp = new Paint(Paint.ANTI_ALIAS_FLAG);
        sp.setColor(0xFF303030); sp.setStrokeWidth(dp(1.5f));
        canvas.drawLine(bx, by, bx + barPx, by, sp);
        canvas.drawLine(bx, by - dp(5), bx, by + dp(2), sp);
        canvas.drawLine(bx + barPx, by - dp(5), bx + barPx, by + dp(2), sp);
        textPaint.setColor(0xFF303030); textPaint.setTextSize(dp(9));
        String lbl = barM >= 1000 ? String.format("%.0f km", barM/1000) : String.format("%.0f m", barM);
        canvas.drawText(lbl, bx + barPx/2f - textPaint.measureText(lbl)/2f, by - dp(7), textPaint);
    }

    private void drawZoomButtons(Canvas canvas, int w, int h) {
        int bx = w - dp(46), bs = dp(36), gap = dp(8), by = h / 2 - bs - gap/2;
        cardPaint.setColor(C_CARD_BG);
        canvas.drawRoundRect(new RectF(bx, by,            bx+bs, by+bs),      dp(7), dp(7), cardPaint);
        canvas.drawRoundRect(new RectF(bx, by+bs+gap,     bx+bs, by+2*bs+gap),dp(7), dp(7), cardPaint);
        textPaint.setColor(C_CARD_TEXT); textPaint.setTextSize(dp(22));
        canvas.drawText("+", bx + bs/2f - textPaint.measureText("+")/2f, by       + bs/2f + dp(8), textPaint);
        canvas.drawText("-", bx + bs/2f - textPaint.measureText("-")/2f, by+bs+gap+ bs/2f + dp(8), textPaint);
    }

    private void drawPlaceholder(Canvas canvas, int w, int h) {
        textPaint.setColor(0xFF8AAFCC); textPaint.setTextSize(dp(13));
        String msg = hasMap ? "Waiting for GPS data..." : "Connect to see live map";
        canvas.drawText(msg, (w - textPaint.measureText(msg))/2f, h/2f, textPaint);
    }

    // --- Touch -----------------------------------------------------------------

    @Override
    public boolean onTouchEvent(MotionEvent e) {
        int n = e.getPointerCount();
        switch (e.getActionMasked()) {
            case MotionEvent.ACTION_DOWN:
                lastTX = e.getX(); lastTY = e.getY(); fingers = 1; return true;
            case MotionEvent.ACTION_POINTER_DOWN:
                fingers = n;
                if (n == 2) { pinchD0 = pinchDist(e); zoom0 = zoom; } return true;
            case MotionEvent.ACTION_MOVE:
                if (fingers == 1 && n == 1) {
                    panX += e.getX() - lastTX; panY += e.getY() - lastTY;
                    lastTX = e.getX(); lastTY = e.getY(); invalidate();
                } else if (fingers >= 2 && n >= 2 && pinchD0 > 0) {
                    zoom = Math.max(0.3f, Math.min(10f, zoom0 * pinchDist(e) / pinchD0)); invalidate();
                } return true;
            case MotionEvent.ACTION_UP:
                int w = getWidth(), h = getHeight();
                int bx = w-dp(46), bs = dp(36), gap = dp(8), by = h/2-bs-gap/2;
                float tx = e.getX(), ty = e.getY();
                if (tx >= bx && tx <= bx+bs) {
                    if (ty >= by        && ty <= by+bs)          { zoom = Math.min(10f, zoom*1.5f); invalidate(); return true; }
                    if (ty >= by+bs+gap && ty <= by+2*bs+gap)    { zoom = Math.max(0.3f,zoom/1.5f); invalidate(); return true; }
                }
                fingers = 0; return true;
            case MotionEvent.ACTION_POINTER_UP:
                fingers = Math.max(0, fingers-1); return true;
        }
        return super.onTouchEvent(e);
    }

    private float pinchDist(MotionEvent e) {
        float dx = e.getX(0)-e.getX(1), dy = e.getY(0)-e.getY(1);
        return (float) Math.sqrt(dx*dx+dy*dy);
    }

    // --- Helpers ---------------------------------------------------------------

    private double[] col(JSONArray arr, int c) {
        if (arr == null) return new double[0];
        double[] out = new double[arr.length()]; int n = 0;
        for (int i = 0; i < arr.length(); i++) {
            try { out[n++] = arr.getJSONArray(i).getDouble(c); } catch (Exception ignored) {}
        }
        double[] r = new double[n]; System.arraycopy(out,0,r,0,n); return r;
    }

    private int dp(int v)   { return Math.round(v * getResources().getDisplayMetrics().density); }
    private int dp(float v) { return Math.round(v * getResources().getDisplayMetrics().density); }

    private BBox trailBox() {
        BBox b = new BBox();
        if (gnsLat.length > 0) b.extend(gnsLat, gnsLon);
        if (witLat.length > 0) b.extend(witLat, witLon);
        if (b.valid()) {
            if (b.maxLat==b.minLat){b.maxLat+=2e-4;b.minLat-=2e-4;}
            if (b.maxLon==b.minLon){b.maxLon+=2e-4;b.minLon-=2e-4;}
        }
        return b;
    }

    private BBox roadBox() {
        BBox b = new BBox();
        if (roadPts == null) return b;
        for (int i = 0; i < roadCount; i++) {
            if (roadPts[i] == null) continue;
            for (double[] pt : roadPts[i]) {
                if (pt[0]<b.minLat) b.minLat=pt[0]; if (pt[0]>b.maxLat) b.maxLat=pt[0];
                if (pt[1]<b.minLon) b.minLon=pt[1]; if (pt[1]>b.maxLon) b.maxLon=pt[1];
            }
        }
        return b;
    }

    private static class BBox {
        double minLat=Double.MAX_VALUE, maxLat=-Double.MAX_VALUE;
        double minLon=Double.MAX_VALUE, maxLon=-Double.MAX_VALUE;
        void extend(double[] la, double[] lo) {
            for (int i=0;i<la.length;i++){
                if(la[i]<minLat)minLat=la[i]; if(la[i]>maxLat)maxLat=la[i];
                if(lo[i]<minLon)minLon=lo[i]; if(lo[i]>maxLon)maxLon=lo[i];
            }
        }
        boolean valid(){ return minLat!=Double.MAX_VALUE; }
    }

    private static class Proj {
        final double latMin, lonMin, latRange, lonRange;
        final float scaleX, scaleY, ox, oy;
        Proj(BBox b, int w, int h, float mg, float zoom, float panX, float panY) {
            latMin=b.minLat; lonMin=b.minLon;
            latRange=b.maxLat-b.minLat; lonRange=b.maxLon-b.minLon;
            double cos=Math.cos(Math.toRadians((b.minLat+b.maxLat)/2.0));
            float cw=w-2*mg, ch=h-2*mg;
            float sxc=(float)(cw/lonRange*cos), sy=(float)(ch/latRange);
            float base=Math.min(sxc,sy)*zoom;
            scaleX=(float)(base/cos); scaleY=base;
            float uw=(float)(lonRange*scaleX), uh=(float)(latRange*scaleY);
            ox=mg+(cw-uw)/2f+panX; oy=mg+(ch-uh)/2f+panY;
        }
        float x(double lon){ return ox+(float)((lon-lonMin)*scaleX); }
        float y(double lat){ return oy+(float)((latRange*scaleY)-(lat-latMin)*scaleY); }
    }

    private void initPaints() {
        roadCase.setStyle(Paint.Style.STROKE); roadCase.setStrokeCap(Paint.Cap.ROUND); roadCase.setStrokeJoin(Paint.Join.ROUND);
        roadFill.setStyle(Paint.Style.STROKE); roadFill.setStrokeCap(Paint.Cap.ROUND); roadFill.setStrokeJoin(Paint.Join.ROUND);

        trailGps.setColor(C_GPS); trailGps.setStyle(Paint.Style.STROKE);
        trailGps.setStrokeWidth(dp(3.5f)); trailGps.setStrokeCap(Paint.Cap.ROUND); trailGps.setStrokeJoin(Paint.Join.ROUND);

        trailWit.setColor(C_WITNESS); trailWit.setStyle(Paint.Style.STROKE);
        trailWit.setStrokeWidth(dp(3.5f)); trailWit.setStrokeCap(Paint.Cap.ROUND); trailWit.setStrokeJoin(Paint.Join.ROUND);

        gapPaint.setColor(C_GAP); gapPaint.setStyle(Paint.Style.STROKE);
        gapPaint.setStrokeWidth(dp(2f)); gapPaint.setPathEffect(new DashPathEffect(new float[]{dp(10),dp(7)},0));

        dotPaint.setStyle(Paint.Style.FILL);
        cardPaint.setStyle(Paint.Style.FILL);
    }
}