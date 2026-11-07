package app.aibrain.brain;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.media.AudioFormat;
import android.media.AudioRecord;
import android.media.MediaRecorder;
import android.net.Uri;
import android.os.Bundle;
import android.os.PowerManager;
import android.provider.Settings;
import android.text.InputType;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Switch;
import android.widget.TextView;

import org.json.JSONObject;
import org.vosk.Model;
import org.vosk.Recognizer;

import java.io.File;

/** Brain settings on the phone: address, token, activation word, phone control. */
public class SetupActivity extends Activity {
    private Prefs prefs;
    private EditText url, token;
    private Switch listen, hindi;
    private TextView status;

    @Override protected void onCreate(Bundle state) {
        super.onCreate(state);
        prefs = new Prefs(this);
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        int pad = (int) (18 * getResources().getDisplayMetrics().density);
        box.setPadding(pad, pad, pad, pad);

        text(box, "Your Brain", 22);
        text(box, "1. Your Brain's address: your server (https://…) or, for the Brain on your own computer, the Tailscale "
                + "address shown in Master → Use from my phone (http://100.x.x.x:8766 or https://…ts.net).", 14);
        url = field(box, "https://…", prefs.url(), InputType.TYPE_TEXT_VARIATION_URI);
        text(box, "2. An API token from your Brain (Master → API tokens). It lets this phone talk to your Brain.", 14);
        token = field(box, "brain_…", prefs.token(), InputType.TYPE_TEXT_VARIATION_PASSWORD);
        token.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);

        text(box, "3. Activation word: it listens all the time (offline, on this phone) and wakes up when you say it. "
                + "Change the word in your Brain: Master → Wake words.", 14);
        listen = new Switch(this);
        listen.setText("Always listen for my activation word");
        listen.setChecked(prefs.listen());
        box.addView(listen);
        hindi = new Switch(this);
        hindi.setText("Understand Hindi (instead of English)");
        hindi.setChecked("hi".equals(prefs.lang()));
        box.addView(hindi);

        button(box, "Save", v -> save());
        button(box, "Teach it my activation word (say it after tapping)", v -> learn());
        button(box, "4. Turn on phone control (Accessibility → Brain)", v ->
                startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)));
        button(box, "5. Keep listening when the screen is off (battery)", v -> {
            PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
            if (!pm.isIgnoringBatteryOptimizations(getPackageName())) {
                startActivity(new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:" + getPackageName())));
            } else status.setText("Already allowed.");
        });
        button(box, "Open my Brain", v -> { startActivity(new Intent(this, MainActivity.class)); finish(); });

        status = text(box, "", 14);
        text(box, "Privacy: speech is understood on this phone. Nothing is sent until it hears your activation word; "
                + "then only the words of your command go to your Brain. Phone control can be switched off in Accessibility any time. "
                + "Calls and messages wait for your OK, and it never types passwords, OTPs or card numbers.", 12);

        ScrollView sv = new ScrollView(this);
        sv.addView(box);
        setContentView(sv);
        requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO, Manifest.permission.POST_NOTIFICATIONS,
                Manifest.permission.READ_CONTACTS, Manifest.permission.CALL_PHONE}, 1);
        refresh();
    }

    private void save() {
        String u = url.getText().toString().trim();
        if (!u.isEmpty() && !u.startsWith("http")) u = "https://" + u;
        prefs.save(u, token.getText().toString(), listen.isChecked(), hindi.isChecked() ? "hi" : "en");
        BrainLink link = BrainLink.get(this);
        link.restart();
        new Thread(() -> {
            boolean ok = !prefs.token().isEmpty() && link.fetchConfig();
            runOnUiThread(() -> {
                if (prefs.listen()) { VoiceService.stop(this); VoiceService.start(this); } else VoiceService.stop(this);
                status.setText(ok ? "Saved. Connected to your Brain; activation word: \"" + prefs.wakeWords().get(0) + "\"."
                        : "Saved. Couldn't reach your Brain with this address and token yet.");
            });
        }).start();
    }

    /** Record the activation word once and remember how this phone's offline model hears it. */
    private void learn() {
        status.setText("Getting ready…");
        boolean wasOn = prefs.listen();
        VoiceService.stop(this);
        new Thread(() -> {
            String msg;
            try {
                File dir = VoiceService.modelDir(this, prefs.lang());
                if (!new File(dir, "am").exists()) {
                    runOnUiThread(() -> status.setText("Downloading the speech model (about 40 MB, once)…"));
                    VoiceService.download(this, prefs.lang());
                }
                Model model = new Model(dir.getAbsolutePath());
                Recognizer rec = new Recognizer(model, 16000.0f);
                int size = AudioRecord.getMinBufferSize(16000, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT);
                AudioRecord ar = new AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION, 16000, AudioFormat.CHANNEL_IN_MONO,
                        AudioFormat.ENCODING_PCM_16BIT, Math.max(size, 32000));
                runOnUiThread(() -> status.setText("Say your activation word now…"));
                ar.startRecording();
                byte[] buf = new byte[4000];
                long end = System.currentTimeMillis() + 3500;
                while (System.currentTimeMillis() < end) {
                    int n = ar.read(buf, 0, buf.length);
                    if (n > 0) rec.acceptWaveForm(buf, n);
                }
                ar.stop();
                ar.release();
                String heard = Wake.norm(new JSONObject(rec.getFinalResult()).optString("text"));
                rec.close();
                model.close();
                if (heard.isEmpty()) msg = "I didn't hear anything. Try again, a bit louder.";
                else if (Wake.split(heard, prefs.wakeWords(), prefs.aliases()) != null) msg = "I hear \"" + heard + "\": that already works.";
                else if (heard.split(" ").length < 2 && heard.length() < 6) msg = "I heard \"" + heard + "\", too short to use safely. Try a longer phrase.";
                else { prefs.addAlias(heard); msg = "Got it: I'll also wake up when I hear \"" + heard + "\"."; }
            } catch (SecurityException e) {
                msg = "Allow the microphone first.";
            } catch (Exception e) {
                msg = "Couldn't learn: " + e.getMessage();
            }
            final String m = msg;
            runOnUiThread(() -> {
                status.setText(m);
                if (wasOn) VoiceService.start(this);
            });
        }).start();
    }

    private void refresh() {
        String control = ControlService.instance != null ? "Phone control: on." : "Phone control: off (step 4).";
        status.setText(control + "  Listening: " + VoiceService.state() + ".");
    }

    @Override protected void onResume() { super.onResume(); if (status != null) refresh(); }

    private TextView text(LinearLayout box, String s, int sp) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTextSize(sp);
        t.setPadding(0, 12, 0, 6);
        box.addView(t);
        return t;
    }

    private EditText field(LinearLayout box, String hint, String value, int type) {
        EditText e = new EditText(this);
        e.setHint(hint);
        e.setText(value);
        e.setSingleLine(true);
        e.setInputType(InputType.TYPE_CLASS_TEXT | type);
        box.addView(e, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        return e;
    }

    private void button(LinearLayout box, String label, android.view.View.OnClickListener l) {
        Button b = new Button(this);
        b.setText(label);
        b.setAllCaps(false);
        b.setOnClickListener(l);
        box.addView(b, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
    }
}
