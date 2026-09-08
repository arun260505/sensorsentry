package com.sensorsentry.alarm;

/**
 * Every colour the phone draws, in one place, in two sets.
 *
 * <p>The console learned this the hard way and wrote the rule down: never
 * hardcode a colour. Its map reads every shade out of the stylesheet so a theme
 * switch cannot leave the page dark around a map still painting roads on paper.
 * The phone had the same failure waiting for it — sixty-odd hex literals spread
 * across two files — so it gets the same discipline.
 *
 * <p>The values are the console's own tokens rather than a second palette
 * invented here. A judge holding the phone next to the laptop should see the
 * same map, not a cousin of it.
 *
 * <p>Not an enum and not a resource file: the whole interface is built in code,
 * and a static swap plus one repaint is the smallest thing that works.
 */
final class Theme {

    private Theme() {}

    static boolean dark = true;

    // --- the map ----------------------------------------------------------
    static int mapBg, roadCaseHi, roadFillHi, roadCaseMid, roadFillMid;
    static int roadCaseLo, roadFillLo, roadText, placeText;
    static int water, waterEdge, green, sand, built, campus, building, buildingEdge;

    // --- what the map is actually for -------------------------------------
    static int claimed, witness, gap, headClaimed, headWitness;

    // --- cards and overlays -----------------------------------------------
    static int cardBg, cardText, cardOk, cardWarn, north, ring;

    // --- the rest of the screen -------------------------------------------
    static int bg, bgAlert, ink, ink2, ink3, divider, label, hint;
    static int ok, warn, bad;

    static void apply(boolean useDark) {
        dark = useDark;
        if (useDark) {
            mapBg        = 0xFF10181F;
            roadCaseHi   = 0xFF1A242C; roadFillHi  = 0xFF4A5F70;
            roadCaseMid  = 0xFF1A242C; roadFillMid = 0xFF33434F;
            roadCaseLo   = 0xFF161E25; roadFillLo  = 0xFF232F39;
            roadText     = 0xFF8FA3B1; placeText   = 0xFFA6BAC7;

            // Water is darker than the land at night. A pale lake on a dark map
            // reads as a hole punched in it.
            water        = 0xFF16303F; waterEdge   = 0xFF1E4356;
            green        = 0xFF1A2A24; sand        = 0xFF2B2A22;
            built        = 0xFF171F26; campus      = 0xFF22222F;
            building     = 0xFF2A3742; buildingEdge = 0xFF3A4954;

            claimed      = 0xFFF0A52E; witness     = 0xFF35C7E8;
            gap          = 0xFFFF5C4D;
            headClaimed  = 0xFFFFC24D; headWitness = 0xFF6BDDF5;

            cardBg       = 0xE60D1720; cardText    = 0xFFCCDDE8;
            cardOk       = 0xFF5FBF8F; cardWarn    = 0xFFE88840;
            north        = 0xFFE84040; ring        = 0xFF3A5A7A;

            bg           = 0xFF0F1C25; bgAlert     = 0xFF8C1D13;
            ink          = 0xFFFFFFFF; ink2        = 0xFFA8BCC9; ink3 = 0xFF7F97A6;
            divider      = 0xFF1A3040; label       = 0xFF5FBFAA; hint = 0xFF5C7080;
            ok           = 0xFF5FBF8F; warn        = 0xFFE88840; bad  = 0xFFFF7060;
        } else {
            mapBg        = 0xFFF5F2EA;
            roadCaseHi   = 0xFFBFB298; roadFillHi  = 0xFFFFFFFF;
            roadCaseMid  = 0xFFC7BCA6; roadFillMid = 0xFFFFFFFF;
            roadCaseLo   = 0xFFD5CCB9; roadFillLo  = 0xFFF3EFE5;
            roadText     = 0xFF6F6857; placeText   = 0xFF574F40;

            water        = 0xFFB9D6E2; waterEdge   = 0xFF9DC2D2;
            green        = 0xFFDFE6CD; sand        = 0xFFEFE6CD;
            built        = 0xFFECE7DB; campus      = 0xFFE6E2EE;
            building     = 0xFFDED6C6; buildingEdge = 0xFFC9BFA9;

            claimed      = 0xFFC07818; witness     = 0xFF0E7C99;
            gap          = 0xFFB3352A;
            headClaimed  = 0xFF9A5E10; headWitness = 0xFF0A6076;

            cardBg       = 0xF2FFFFFF; cardText    = 0xFF23303A;
            cardOk       = 0xFF1E7D5A; cardWarn    = 0xFFB5651A;
            north        = 0xFFB3352A; ring        = 0xFFB0BCC6;

            bg           = 0xFFF7F5F0; bgAlert     = 0xFFB3352A;
            ink          = 0xFF16202A; ink2        = 0xFF3E4C58; ink3 = 0xFF6B7A86;
            divider      = 0xFFD8D2C6; label       = 0xFF1E7D5A; hint = 0xFF97A2AC;
            ok           = 0xFF1E7D5A; warn        = 0xFFB5651A; bad  = 0xFF9E2B20;
        }
    }

    /** Colour of the text that sits on top of an alert background. */
    static int onAlert() {
        return 0xFFFFFFFF;
    }

    static { apply(true); }
}
