package app.aibrain.brain;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Build;

/**
 * After a restart: Android 14+ doesn't let apps start the microphone by themselves at boot, so we show a
 * notification; one tap starts listening again. Older Android starts listening directly.
 */
public class BootReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context c, Intent intent) {
        Prefs p = new Prefs(c);
        if (!p.listen() || p.url().isEmpty()) return;
        if (Build.VERSION.SDK_INT >= 34) {
            VoiceService.notify(c, "Brain isn't listening yet", "Open the Brain app once to start listening for your activation word.");
        } else {
            try { VoiceService.start(c); } catch (Exception e) {
                VoiceService.notify(c, "Brain isn't listening yet", "Open the Brain app once to start listening.");
            }
        }
    }
}
