package app.aibrain.brain;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.media.AudioManager;
import android.media.ToneGenerator;
import android.net.ConnectivityManager;
import android.net.NetworkCapabilities;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.speech.tts.TextToSpeech;
import android.speech.tts.UtteranceProgressListener;

import org.json.JSONObject;
import org.vosk.Model;
import org.vosk.Recognizer;
import org.vosk.android.RecognitionListener;
import org.vosk.android.SpeechService;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.Locale;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;

/**
 * Always listening for your activation word, like Siri. Speech is understood on the phone (Vosk, offline);
 * nothing leaves the phone until it hears the activation word, then only the words of your command go to
 * your Brain. With no internet, the phone does common things itself (OfflineBrain) and saves the rest.
 * A notification shows while it listens; tap "Stop" there to stop.
 */
public class VoiceService extends Service implements RecognitionListener {
    static final String CHANNEL = "brain-listening", ALERTS = "brain-alerts";
    static final String[][] MODELS = {
            {"en", "vosk-model-small-en-us-0.15", "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"},
            {"hi", "vosk-model-small-hi-0.22", "https://alphacephei.com/vosk/models/vosk-model-small-hi-0.22.zip"}};
    private static VoiceService self;

    private final Handler main = new Handler(Looper.getMainLooper());
    private Prefs prefs;
    private Model model;
    private SpeechService speech;
    private TextToSpeech tts;
    private BrainLink link;
    private boolean awaitingCommand, busy;
    private long commandUntil;
    private String state = "starting";

    public static void start(Context c) {
        Intent i = new Intent(c, VoiceService.class);
        if (Build.VERSION.SDK_INT >= 26) c.startForegroundService(i); else c.startService(i);
    }

    public static void stop(Context c) { c.stopService(new Intent(c, VoiceService.class)); }

    public static String state() { return self == null ? "off" : self.state; }

    @Override public IBinder onBind(Intent intent) { return null; }

    @Override public void onCreate() {
        super.onCreate();
        self = this;
        prefs = new Prefs(this);
        channels(this);
        Notification n = note("Starting…");
        if (Build.VERSION.SDK_INT >= 30) startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE);
        else startForeground(1, n);
        tts = new TextToSpeech(this, s -> { if (s == TextToSpeech.SUCCESS) tts.setLanguage("hi".equals(prefs.lang()) ? new Locale("hi", "IN") : Locale.getDefault()); });
        link = BrainLink.get(this);
        link.start();
        new Thread(this::loadModel).start();
    }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && "stop".equals(intent.getAction())) {
            prefs.setListen(false);
            stopSelf();
            return START_NOT_STICKY;
        }
        return START_STICKY;
    }

    @Override public void onDestroy() {
        self = null;
        if (speech != null) { speech.stop(); speech.shutdown(); }
        if (tts != null) tts.shutdown();
        super.onDestroy();
    }

    // ---------- the offline speech model ----------

    static File modelDir(Context c, String lang) {
        for (String[] m : MODELS) if (m[0].equals(lang)) return new File(c.getFilesDir(), m[1]);
        return new File(c.getFilesDir(), MODELS[0][1]);
    }

    static void download(Context c, String lang) throws Exception {
        String url = MODELS[0][2];
        for (String[] m : MODELS) if (m[0].equals(lang)) url = m[2];
        HttpURLConnection con = (HttpURLConnection) new URL(url).openConnection();
        con.setConnectTimeout(20000);
        File root = c.getFilesDir();
        try (InputStream in = con.getInputStream(); ZipInputStream zip = new ZipInputStream(in)) {
            ZipEntry e;
            byte[] buf = new byte[1 << 16];
            while ((e = zip.getNextEntry()) != null) {
                File f = new File(root, e.getName());
                if (!f.getCanonicalPath().startsWith(root.getCanonicalPath() + File.separator)) throw new Exception("Unsafe download");
                if (e.isDirectory()) { f.mkdirs(); continue; }
                f.getParentFile().mkdirs();
                try (FileOutputStream out = new FileOutputStream(f)) {
                    int n;
                    while ((n = zip.read(buf)) > 0) out.write(buf, 0, n);
                }
            }
        }
    }

    private void loadModel() {
        try {
            File dir = modelDir(this, prefs.lang());
            if (!new File(dir, "am").exists()) {
                setState("Downloading the speech model (about 40 MB, once)…");
                download(this, prefs.lang());
            }
            model = new Model(dir.getAbsolutePath());
            main.post(this::listen);
        } catch (Exception e) {
            setState("Couldn't load speech: " + e.getMessage());
        }
    }

    private void listen() {
        try {
            if (speech != null) { speech.stop(); speech.shutdown(); }
            speech = new SpeechService(new Recognizer(model, 16000.0f), 16000.0f);
            speech.startListening(this);
            setState("Listening for \"" + (prefs.wakeWords().isEmpty() ? "hey brain" : prefs.wakeWords().get(0)) + "\"");
        } catch (Exception e) {
            setState("Microphone unavailable: " + e.getMessage());
        }
    }

    // ---------- hearing ----------

    @Override public void onPartialResult(String hypothesis) {
        if (busy || awaitingCommand) return;
        String heard = text(hypothesis, "partial");
        if (!heard.isEmpty() && Wake.split(heard, prefs.wakeWords(), prefs.aliases()) != null && !beeped) {
            beeped = true;
            beep();
            setState("Listening to you…");
        }
    }

    private boolean beeped;

    @Override public void onResult(String hypothesis) {
        if (busy) return;
        String heard = text(hypothesis, "text");
        if (awaitingCommand) {
            if (!heard.isEmpty()) { awaitingCommand = false; command(heard); }
            else if (System.currentTimeMillis() > commandUntil) { awaitingCommand = false; idle(); }
            return;
        }
        beeped = false;
        if (heard.isEmpty()) return;
        String after = Wake.split(heard, prefs.wakeWords(), prefs.aliases());
        if (after == null) { idle(); return; }
        if (!after.isEmpty()) command(after);
        else {                                       // "hey brain" … pause … "open WhatsApp"
            awaitingCommand = true;
            commandUntil = System.currentTimeMillis() + 8000;
            beep();
            setState("Listening to you…");
        }
    }

    @Override public void onFinalResult(String hypothesis) { onResult(hypothesis); }
    @Override public void onError(Exception e) { setState("Listening error: " + e.getMessage()); main.postDelayed(this::listen, 3000); }
    @Override public void onTimeout() { listen(); }

    private static String text(String json, String key) {
        try { return Wake.norm(new JSONObject(json).optString(key, "")); } catch (Exception e) { return ""; }
    }

    private void idle() {
        setState("Listening for \"" + (prefs.wakeWords().isEmpty() ? "hey brain" : prefs.wakeWords().get(0)) + "\"");
    }

    // ---------- doing ----------

    private void command(String said) {
        busy = true;
        if (speech != null) speech.setPause(true);         // don't hear our own answer
        setState("Working on: " + said);
        new Thread(() -> {
            String reply = online() && link != null ? link.voice(said) : null;
            if (reply == null) reply = OfflineBrain.handle(this, said);
            final String r = reply;
            main.post(() -> speak(r));
        }).start();
    }

    private void speak(String text) {
        if (text == null || text.trim().isEmpty() || tts == null) { resume(); return; }
        String clean = text.replaceAll("[*_#`>\\[\\]]", "");
        if (clean.length() > 600) clean = clean.substring(0, 600);
        tts.setOnUtteranceProgressListener(new UtteranceProgressListener() {
            @Override public void onStart(String id) { }
            @Override public void onDone(String id) { main.post(VoiceService.this::resume); }
            @Override public void onError(String id) { main.post(VoiceService.this::resume); }
        });
        setState("Answering…");
        // Hindi in Devanagari is read by the Hindi voice; an English voice would say it with an English accent.
        boolean hindi = clean.codePoints().anyMatch(cp -> cp >= 0x0900 && cp <= 0x097F) || "hi".equals(prefs.lang());
        tts.setLanguage(hindi ? new Locale("hi", "IN") : new Locale("en", "IN"));
        tts.speak(clean, TextToSpeech.QUEUE_FLUSH, null, "brain-reply");
    }

    private void resume() {
        busy = false;
        beeped = false;
        if (speech != null) speech.setPause(false);
        idle();
    }

    private boolean online() {
        ConnectivityManager cm = (ConnectivityManager) getSystemService(CONNECTIVITY_SERVICE);
        NetworkCapabilities nc = cm.getNetworkCapabilities(cm.getActiveNetwork());
        return nc != null && nc.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET);
    }

    private void beep() {
        try { new ToneGenerator(AudioManager.STREAM_NOTIFICATION, 70).startTone(ToneGenerator.TONE_PROP_BEEP, 150); } catch (Exception ignored) { }
    }

    /** The Brain asks the phone to say something. */
    static void say(Context c, String text) { if (self != null) self.main.post(() -> self.speak(text)); }

    // ---------- notifications ----------

    static void channels(Context c) {
        NotificationManager nm = c.getSystemService(NotificationManager.class);
        nm.createNotificationChannel(new NotificationChannel(CHANNEL, "Listening for the activation word", NotificationManager.IMPORTANCE_LOW));
        nm.createNotificationChannel(new NotificationChannel(ALERTS, "Brain messages", NotificationManager.IMPORTANCE_DEFAULT));
    }

    private Notification note(String text) {
        PendingIntent open = PendingIntent.getActivity(this, 0, new Intent(this, MainActivity.class), PendingIntent.FLAG_IMMUTABLE);
        PendingIntent stop = PendingIntent.getService(this, 1, new Intent(this, VoiceService.class).setAction("stop"), PendingIntent.FLAG_IMMUTABLE);
        return new Notification.Builder(this, CHANNEL).setContentTitle("Brain").setContentText(text)
                .setSmallIcon(android.R.drawable.ic_btn_speak_now).setOngoing(true).setContentIntent(open)
                .addAction(new Notification.Action.Builder(null, "Stop listening", stop).build()).build();
    }

    private void setState(String s) {
        state = s;
        main.post(() -> getSystemService(NotificationManager.class).notify(1, note(s)));
    }

    static void notify(Context c, String title, String text) {
        channels(c);
        Notification n = new Notification.Builder(c, ALERTS).setContentTitle(title).setContentText(text)
                .setStyle(new Notification.BigTextStyle().bigText(text)).setSmallIcon(android.R.drawable.ic_dialog_info).build();
        c.getSystemService(NotificationManager.class).notify((int) (System.currentTimeMillis() % 100000), n);
    }
}
