package app.aibrain.brain;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.GestureDescription;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.graphics.Path;
import android.graphics.Rect;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.util.DisplayMetrics;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.List;
import java.util.Locale;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/**
 * Phone control through Android's Accessibility service: read the screen, tap, swipe, type, Back/Home, open apps.
 * The owner switches it on in Settings → Accessibility → Brain, and can switch it off there any time.
 * It never types into password fields or card fields.
 */
public class ControlService extends AccessibilityService {
    public static volatile ControlService instance;

    @Override protected void onServiceConnected() { instance = this; BrainLink.statusChanged(this); }
    @Override public boolean onUnbind(Intent intent) { instance = null; BrainLink.statusChanged(this); return super.onUnbind(intent); }
    @Override public void onDestroy() { instance = null; super.onDestroy(); }
    @Override public void onAccessibilityEvent(AccessibilityEvent event) { }
    @Override public void onInterrupt() { }

    // ---------- reading the screen ----------

    public JSONObject screen() throws Exception {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        JSONObject out = new JSONObject();
        JSONArray els = new JSONArray();
        if (root != null) {
            out.put("app", appLabel(String.valueOf(root.getPackageName())));
            walk(root, els, 0);
        }
        out.put("elements", els);
        return out;
    }

    private void walk(AccessibilityNodeInfo n, JSONArray out, int depth) throws Exception {
        if (n == null || out.length() >= 120 || depth > 40) return;
        if (n.isVisibleToUser()) {
            String label = n.isPassword() ? "" : str(n.getText());
            if (label.isEmpty()) label = str(n.getContentDescription());
            if (label.isEmpty() && Build.VERSION.SDK_INT >= 26) label = str(n.getHintText());
            String rid = n.getViewIdResourceName() == null ? "" : n.getViewIdResourceName().replaceAll(".*/", "");
            boolean tap = n.isClickable() || n.isLongClickable();
            Rect r = new Rect();
            n.getBoundsInScreen(r);
            if ((!label.isEmpty() || (tap && !rid.isEmpty()) || n.isEditable()) && r.width() > 3 && r.height() > 3) {
                JSONObject e = new JSONObject();
                String kind = String.valueOf(n.getClassName()).replaceAll(".*\\.", "");
                e.put("label", (label.isEmpty() ? rid : label).length() > 80 ? (label.isEmpty() ? rid : label).substring(0, 80) : (label.isEmpty() ? rid : label));
                e.put("kind", n.isPassword() ? kind + " (password: the owner types this)" : kind);
                e.put("tap", tap);
                e.put("edit", n.isEditable());
                e.put("x", r.centerX());
                e.put("y", r.centerY());
                out.put(e);
            }
        }
        for (int i = 0; i < n.getChildCount(); i++) walk(n.getChild(i), out, depth + 1);
    }

    private static String str(CharSequence c) { return c == null ? "" : c.toString().trim().replace('\n', ' '); }

    private String appLabel(String pkg) {
        try {
            PackageManager pm = getPackageManager();
            return pm.getApplicationLabel(pm.getApplicationInfo(pkg, 0)).toString();
        } catch (Exception e) { return pkg; }
    }

    // ---------- touch ----------

    private void gesture(Path path, long ms) throws Exception {
        GestureDescription g = new GestureDescription.Builder()
                .addStroke(new GestureDescription.StrokeDescription(path, 0, Math.max(1, ms))).build();
        CountDownLatch done = new CountDownLatch(1);
        final boolean[] ok = {false};
        boolean sent = dispatchGesture(g, new GestureResultCallback() {
            @Override public void onCompleted(GestureDescription d) { ok[0] = true; done.countDown(); }
            @Override public void onCancelled(GestureDescription d) { done.countDown(); }
        }, null);
        if (!sent || !done.await(ms + 3000, TimeUnit.MILLISECONDS) || !ok[0]) throw new Exception("The phone didn't accept the touch.");
    }

    public void tap(float x, float y) throws Exception { Path p = new Path(); p.moveTo(x, y); gesture(p, 60); }
    public void longPress(float x, float y) throws Exception { Path p = new Path(); p.moveTo(x, y); gesture(p, 750); }

    public void swipe(float x1, float y1, float x2, float y2, long ms) throws Exception {
        Path p = new Path();
        p.moveTo(x1, y1);
        p.lineTo(x2, y2);
        gesture(p, ms);
    }

    public void scroll(String direction) throws Exception {
        DisplayMetrics m = getResources().getDisplayMetrics();
        float x = m.widthPixels / 2f, top = m.heightPixels * 0.3f, bottom = m.heightPixels * 0.72f;
        if ("up".equals(direction)) swipe(x, top, x, bottom, 350); else swipe(x, bottom, x, top, 350);
    }

    // ---------- typing and keys ----------

    public void type(String text, boolean enter) throws Exception {
        AccessibilityNodeInfo root = getRootInActiveWindow();
        if (root == null) throw new Exception("Nothing is on the screen to type into.");
        AccessibilityNodeInfo f = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
        if (f == null || !f.isEditable()) f = firstEditable(root);
        if (f == null) throw new Exception("Tap a text box first.");
        if (f.isPassword()) throw new Exception("That's a password field: the owner types passwords themselves.");
        String hint = (str(f.getViewIdResourceName()) + " " + (Build.VERSION.SDK_INT >= 26 ? str(f.getHintText()) : "")).toLowerCase(Locale.ROOT);
        if (hint.matches(".*(card|cvv|cvc|otp|pin).*")) throw new Exception("I don't type into card, OTP or PIN fields.");
        Bundle args = new Bundle();
        args.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text);
        if (!f.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)) throw new Exception("Couldn't type there.");
        if (enter) imeEnter(f);
    }

    private AccessibilityNodeInfo firstEditable(AccessibilityNodeInfo n) {
        if (n == null) return null;
        if (n.isEditable() && n.isVisibleToUser()) return n;
        for (int i = 0; i < n.getChildCount(); i++) {
            AccessibilityNodeInfo r = firstEditable(n.getChild(i));
            if (r != null) return r;
        }
        return null;
    }

    private void imeEnter(AccessibilityNodeInfo f) {
        if (Build.VERSION.SDK_INT >= 30) f.performAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER.getId());
    }

    public void key(String name) throws Exception {
        int action;
        switch (name) {
            case "back": action = GLOBAL_ACTION_BACK; break;
            case "home": action = GLOBAL_ACTION_HOME; break;
            case "recents": action = GLOBAL_ACTION_RECENTS; break;
            case "notifications": action = GLOBAL_ACTION_NOTIFICATIONS; break;
            case "quick_settings": action = GLOBAL_ACTION_QUICK_SETTINGS; break;
            case "lock": action = Build.VERSION.SDK_INT >= 28 ? GLOBAL_ACTION_LOCK_SCREEN : -1; break;
            case "screenshot": action = Build.VERSION.SDK_INT >= 28 ? GLOBAL_ACTION_TAKE_SCREENSHOT : -1; break;
            case "enter": {
                AccessibilityNodeInfo root = getRootInActiveWindow();
                AccessibilityNodeInfo f = root == null ? null : root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
                if (f == null) throw new Exception("No text box is focused.");
                imeEnter(f);
                return;
            }
            default: throw new Exception("Unknown key " + name);
        }
        if (action < 0 || !performGlobalAction(action)) throw new Exception("This phone can't do " + name + ".");
    }

    // ---------- apps, links, calls and messages ----------

    public String openApp(String name) throws Exception { return openApp(this, name); }

    public static String openApp(android.content.Context ctx, String name) throws Exception {
        PackageManager pm = ctx.getPackageManager();
        Intent main = new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER);
        List<ResolveInfo> apps = pm.queryIntentActivities(main, 0);
        String want = name.toLowerCase(Locale.ROOT).trim();
        ResolveInfo best = null;
        for (ResolveInfo ri : apps) {
            String label = ri.loadLabel(pm).toString().toLowerCase(Locale.ROOT);
            String pkg = ri.activityInfo.packageName.toLowerCase(Locale.ROOT);
            if (label.equals(want) || pkg.equals(want)) { best = ri; break; }
            if (best == null && (label.contains(want) || want.contains(label) || pkg.contains(want.replace(" ", "")))) best = ri;
        }
        if (best == null) throw new Exception("I couldn't find an app called " + name + ".");
        Intent i = pm.getLaunchIntentForPackage(best.activityInfo.packageName);
        if (i == null) throw new Exception("That app can't be opened directly.");
        start(ctx, i);
        return best.loadLabel(pm).toString();
    }

    public static void start(android.content.Context ctx, Intent i) {
        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        // An accessibility service may open apps from the background; prefer it when it's on
        android.content.Context c = instance != null ? instance : ctx;
        c.startActivity(i);
    }

    public static void openUrl(android.content.Context ctx, String url) { start(ctx, new Intent(Intent.ACTION_VIEW, Uri.parse(url))); }

    public static void call(android.content.Context ctx, String number) {
        boolean can = ctx.checkSelfPermission(android.Manifest.permission.CALL_PHONE) == PackageManager.PERMISSION_GRANTED;
        start(ctx, new Intent(can ? Intent.ACTION_CALL : Intent.ACTION_DIAL, Uri.parse("tel:" + number)));
    }

    public static void sms(android.content.Context ctx, String number, String text) {
        Intent i = new Intent(Intent.ACTION_SENDTO, Uri.parse("smsto:" + number));
        i.putExtra("sms_body", text);
        start(ctx, i);
    }

    public static void whatsapp(android.content.Context ctx, String number, String text) {
        openUrl(ctx, "https://wa.me/" + number.replace("+", "") + "?text=" + Uri.encode(text));
    }
}
