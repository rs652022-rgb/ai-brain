package app.aibrain.brain;

import android.content.Context;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;

import org.json.JSONObject;

import java.util.List;
import java.util.concurrent.TimeUnit;

import okhttp3.MediaType;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.RequestBody;
import okhttp3.Response;
import okhttp3.WebSocket;
import okhttp3.WebSocketListener;

/**
 * The live link between this phone and your Brain: the Brain can control the phone through it (when
 * Phone control is on), and spoken commands go to the Brain through it. Reconnects by itself.
 */
public class BrainLink {
    private static BrainLink current;
    private final Context ctx;
    private final Prefs prefs;
    private final OkHttpClient http = new OkHttpClient.Builder().readTimeout(0, TimeUnit.MILLISECONDS)
            .pingInterval(25, TimeUnit.SECONDS).build();
    private final OkHttpClient api = new OkHttpClient.Builder().callTimeout(150, TimeUnit.SECONDS).build();
    private final Handler main = new Handler(Looper.getMainLooper());
    private WebSocket ws;
    private volatile boolean open, stopped;
    private long delay = 2000;

    private BrainLink(Context ctx) {
        this.ctx = ctx.getApplicationContext();
        this.prefs = new Prefs(ctx);
    }

    /** One link per app, shared by the app screen and the listening service. */
    public static synchronized BrainLink get(Context ctx) {
        if (current == null) current = new BrainLink(ctx);
        return current;
    }

    public boolean connected() { return open; }

    private volatile boolean connecting;

    public void start() {
        stopped = false;
        if (open || connecting) return;
        connect();
    }

    /** Settings changed: connect again with the new address or token. */
    public void restart() {
        if (ws != null) ws.cancel();
        open = false;
        connecting = false;
        stopped = false;
        connect();
    }

    public void stop() {
        stopped = true;
        if (ws != null) ws.close(1000, "bye");
    }

    private void connect() {
        if (stopped || prefs.url().isEmpty() || prefs.token().isEmpty()) return;
        connecting = true;
        String url = prefs.url().replaceFirst("^http", "ws") + "/api/phone/ws";
        ws = http.newWebSocket(new Request.Builder().url(url).build(), new WebSocketListener() {
            @Override public void onOpen(WebSocket s, Response r) {
                try {
                    JSONObject hello = new JSONObject().put("type", "hello").put("token", prefs.token())
                            .put("name", Build.MANUFACTURER + " " + Build.MODEL)
                            .put("accessibility", ControlService.instance != null).put("listening", prefs.listen());
                    s.send(hello.toString());
                } catch (Exception ignored) { }
            }

            @Override public void onMessage(WebSocket s, String text) {
                try {
                    JSONObject m = new JSONObject(text);
                    String type = m.optString("type");
                    if ("welcome".equals(type)) { open = true; connecting = false; delay = 2000; flushQueue(); return; }
                    if ("error".equals(type)) { open = false; return; }
                    if (m.has("id")) new Thread(() -> s.send(handle(m).toString())).start();
                } catch (Exception ignored) { }
            }

            @Override public void onClosed(WebSocket s, int code, String reason) { retry(); }
            @Override public void onFailure(WebSocket s, Throwable t, Response r) { retry(); }
        });
    }

    private void retry() {
        open = false;
        connecting = false;
        if (stopped) return;
        main.postDelayed(this::connect, delay);
        delay = Math.min(60000, delay * 2);
    }

    /** Tell the Brain when phone control is switched on or off. */
    public static void statusChanged(Context c) {
        BrainLink l = current;
        if (l != null && l.ws != null && l.open) {
            try {
                l.ws.send(new JSONObject().put("type", "status").put("accessibility", ControlService.instance != null)
                        .put("listening", l.prefs.listen()).toString());
            } catch (Exception ignored) { }
        }
    }

    // ---------- the Brain asks the phone to do something ----------

    private JSONObject handle(JSONObject m) {
        JSONObject reply = new JSONObject();
        try {
            reply.put("id", m.getString("id"));
            JSONObject result = run(m);
            reply.put("ok", true).put("result", result);
        } catch (Exception e) {
            try { reply.put("ok", false).put("error", e.getMessage() == null ? e.toString() : e.getMessage()); } catch (Exception ignored) { }
        }
        return reply;
    }

    private JSONObject run(JSONObject m) throws Exception {
        String op = m.getString("op");
        ControlService c = ControlService.instance;
        JSONObject r = new JSONObject();
        switch (op) {
            case "status": return r.put("accessibility", c != null);
            case "speak": VoiceService.say(ctx, m.optString("text")); return r;
            case "open_app": return r.put("label", ControlService.openApp(ctx, m.getString("name")));
            case "open_url": ControlService.openUrl(ctx, m.getString("url")); return r;
            case "call": ControlService.call(ctx, m.getString("number")); return r;
            case "sms": ControlService.sms(ctx, m.getString("number"), m.optString("text")); return r;
            case "whatsapp": ControlService.whatsapp(ctx, m.getString("number"), m.optString("text")); return r;
            default: break;
        }
        if (c == null) throw new Exception("Phone control is off. On the phone: Settings → Accessibility → Brain → On.");
        switch (op) {
            case "screen": return c.screen();
            case "tap": c.tap((float) m.getDouble("x"), (float) m.getDouble("y")); return r;
            case "long_press": c.longPress((float) m.getDouble("x"), (float) m.getDouble("y")); return r;
            case "swipe": c.swipe((float) m.getDouble("x1"), (float) m.getDouble("y1"), (float) m.getDouble("x2"),
                    (float) m.getDouble("y2"), m.optLong("ms", 350)); return r;
            case "scroll": c.scroll(m.optString("direction", "down")); return r;
            case "type": c.type(m.getString("text"), m.optBoolean("enter")); return r;
            case "key": c.key(m.getString("name")); return r;
            default: throw new Exception("Unknown action " + op);
        }
    }

    // ---------- spoken commands go to the Brain ----------

    /** One full Brain turn. Returns the reply to speak, or null if the Brain can't be reached. */
    public String voice(String text) {
        if (prefs.url().isEmpty()) return null;
        try {
            JSONObject body = new JSONObject().put("text", text).put("device", "phone");
            Request.Builder rb = new Request.Builder().url(prefs.url() + "/api/voice/command")
                    .post(RequestBody.create(body.toString(), MediaType.get("application/json")));
            if (!prefs.token().isEmpty()) rb.header("Authorization", "Bearer " + prefs.token());
            try (Response r = api.newCall(rb.build()).execute()) {
                if (r.code() == 401 || r.code() == 403) return "Your Brain didn't accept this phone. Check the API token in Brain settings.";
                if (!r.isSuccessful() || r.body() == null) return null;
                return new JSONObject(r.body().string()).optString("reply", "");
            }
        } catch (Exception e) {
            return null;
        }
    }

    /** Activation words and learned spellings from the Brain (Master → Wake words). */
    public boolean fetchConfig() {
        try {
            Request rq = new Request.Builder().url(prefs.url() + "/api/phone/config")
                    .header("Authorization", "Bearer " + prefs.token()).build();
            try (Response r = api.newCall(rq).execute()) {
                if (!r.isSuccessful() || r.body() == null) return false;
                JSONObject j = new JSONObject(r.body().string());
                java.util.ArrayList<String> words = new java.util.ArrayList<>();
                for (int i = 0; i < j.getJSONArray("wake_words").length(); i++) words.add(j.getJSONArray("wake_words").getString(i));
                if (!words.isEmpty()) prefs.setWakeWords(words);
                return true;
            }
        } catch (Exception e) {
            return false;
        }
    }

    /** Things said while offline: the Brain does them now. */
    private void flushQueue() {
        List<String> q = prefs.queue();
        if (q.isEmpty()) return;
        prefs.clearQueue();
        new Thread(() -> {
            for (String t : q) {
                String reply = voice(t);
                if (reply == null) { prefs.enqueue(t); continue; }
                VoiceService.notify(ctx, "Done while you were offline: " + t, reply);
            }
        }).start();
    }
}
