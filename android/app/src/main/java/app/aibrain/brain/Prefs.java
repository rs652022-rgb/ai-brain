package app.aibrain.brain;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONArray;

import java.util.ArrayList;
import java.util.List;

/** The app's settings, kept only on this phone. */
public final class Prefs {
    private final SharedPreferences p;

    public Prefs(Context c) {
        p = c.getSharedPreferences("brain", Context.MODE_PRIVATE);
    }

    public String url() {
        String u = p.getString("url", BuildConfig.DEFAULT_URL);
        return u == null ? "" : u.replaceAll("/+$", "");
    }

    public String token() { return p.getString("token", ""); }
    public boolean listen() { return p.getBoolean("listen", false); }
    public String lang() { return p.getString("lang", "en"); }

    public List<String> wakeWords() { return list("wake", "hey brain"); }
    public List<String> aliases() { return list("aliases", ""); }

    public void save(String url, String token, boolean listen, String lang) {
        p.edit().putString("url", url.trim().replaceAll("/+$", "")).putString("token", token.trim())
                .putBoolean("listen", listen).putString("lang", lang).apply();
    }

    public void setListen(boolean on) { p.edit().putBoolean("listen", on).apply(); }

    public void setWakeWords(List<String> words) { putList("wake", words); }

    public void addAlias(String a) {
        List<String> l = aliases();
        if (!l.contains(a)) l.add(a);
        while (l.size() > 6) l.remove(0);
        putList("aliases", l);
    }

    public void clearAliases() { putList("aliases", new ArrayList<>()); }

    // Commands said while offline, done by the Brain when the phone is back online
    public List<String> queue() { return list("queue", ""); }
    public void enqueue(String text) { List<String> q = queue(); q.add(text); putList("queue", q); }
    public void clearQueue() { putList("queue", new ArrayList<>()); }

    private List<String> list(String key, String def) {
        List<String> out = new ArrayList<>();
        try {
            JSONArray a = new JSONArray(p.getString(key, def.isEmpty() ? "[]" : "[\"" + def + "\"]"));
            for (int i = 0; i < a.length(); i++) out.add(a.getString(i));
        } catch (Exception ignored) { }
        return out;
    }

    private void putList(String key, List<String> l) {
        p.edit().putString(key, new JSONArray(l).toString()).apply();
    }
}
