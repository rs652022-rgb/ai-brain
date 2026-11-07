package app.aibrain.brain;

import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.hardware.camera2.CameraManager;
import android.media.AudioManager;
import android.os.BatteryManager;
import android.provider.AlarmClock;
import android.provider.ContactsContract;

import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * What the phone can do by itself with no internet: open apps, call, prepare messages, alarms, timers,
 * the torch, volume, time, date and battery. Anything else is saved and done by the Brain once the phone
 * is back online.
 */
public final class OfflineBrain {
    private OfflineBrain() { }

    public static String handle(Context ctx, String said) {
        String t = Wake.norm(said);
        Matcher m;
        try {
            if ((m = Pattern.compile("^(?:open|launch|start|kholo|khol do)\\s+(.+?)(?:\\s+(?:app|kholo|kholdo))?$").matcher(t)).find()
                    || (m = Pattern.compile("^(.+?)\\s+(?:kholo|khol do|open karo)$").matcher(t)).find()) {
                return "Opening " + ControlService.openApp(ctx, m.group(1)) + ".";
            }
            if ((m = Pattern.compile("^(?:call|phone|dial)\\s+(.+)$").matcher(t)).find()
                    || (m = Pattern.compile("^(.+?)\\s+ko call (?:karo|kar do)$").matcher(t)).find()) {
                String number = number(ctx, m.group(1));
                if (number == null) return "I couldn't find " + m.group(1) + " in your contacts.";
                ControlService.call(ctx, number);
                return "Calling " + m.group(1) + ".";
            }
            if ((m = Pattern.compile("^(?:message|text|sms)\\s+(.+?)\\s+(?:saying|that|ki)\\s+(.+)$").matcher(t)).find()) {
                String number = number(ctx, m.group(1));
                if (number == null) return "I couldn't find " + m.group(1) + " in your contacts.";
                ControlService.sms(ctx, number, m.group(2));
                return "The message to " + m.group(1) + " is ready. Check it and tap send.";
            }
            if ((m = Pattern.compile("(?:alarm|wake me)(?: up)?(?: for| at)?\\s+(\\d{1,2})(?:[: ](\\d{2}))?\\s*(am|pm|a m|p m)?").matcher(t)).find()) {
                int h = Integer.parseInt(m.group(1)), min = m.group(2) == null ? 0 : Integer.parseInt(m.group(2));
                String ap = m.group(3) == null ? "" : m.group(3).replace(" ", "");
                if ("pm".equals(ap) && h < 12) h += 12;
                if ("am".equals(ap) && h == 12) h = 0;
                Intent i = new Intent(AlarmClock.ACTION_SET_ALARM).putExtra(AlarmClock.EXTRA_HOUR, h)
                        .putExtra(AlarmClock.EXTRA_MINUTES, min).putExtra(AlarmClock.EXTRA_SKIP_UI, true);
                ControlService.start(ctx, i);
                return String.format(Locale.ROOT, "Alarm set for %d:%02d.", h, min);
            }
            if ((m = Pattern.compile("timer (?:for )?(\\d+)\\s*(second|minute|hour)").matcher(t)).find()) {
                int n = Integer.parseInt(m.group(1));
                int secs = m.group(2).startsWith("hour") ? n * 3600 : m.group(2).startsWith("minute") ? n * 60 : n;
                ControlService.start(ctx, new Intent(AlarmClock.ACTION_SET_TIMER).putExtra(AlarmClock.EXTRA_LENGTH, secs)
                        .putExtra(AlarmClock.EXTRA_SKIP_UI, true));
                return "Timer set for " + n + " " + m.group(2) + (n == 1 ? "." : "s.");
            }
            if (t.matches(".*(torch|flashlight|flash light).*")) {
                boolean on = !t.matches(".*\\b(off|band|bund)\\b.*");
                CameraManager cm = (CameraManager) ctx.getSystemService(Context.CAMERA_SERVICE);
                cm.setTorchMode(cm.getCameraIdList()[0], on);
                return on ? "Torch on." : "Torch off.";
            }
            if (t.matches(".*\\bvolume\\b.*") || t.matches(".*\\b(mute|unmute)\\b.*")) {
                AudioManager am = (AudioManager) ctx.getSystemService(Context.AUDIO_SERVICE);
                int dir = t.contains("unmute") ? AudioManager.ADJUST_UNMUTE : t.contains("mute") ? AudioManager.ADJUST_MUTE
                        : t.matches(".*\\b(down|lower|kam)\\b.*") ? AudioManager.ADJUST_LOWER : AudioManager.ADJUST_RAISE;
                for (int i = 0; i < (dir == AudioManager.ADJUST_RAISE || dir == AudioManager.ADJUST_LOWER ? 3 : 1); i++)
                    am.adjustStreamVolume(AudioManager.STREAM_MUSIC, dir, AudioManager.FLAG_SHOW_UI);
                return "Done.";
            }
            if (t.matches(".*\\b(time|samay|kitne baje)\\b.*")) return "It's " + new SimpleDateFormat("h:mm a", Locale.ENGLISH).format(new Date()) + ".";
            if (t.matches(".*\\b(date|day|today|aaj)\\b.*")) return "Today is " + new SimpleDateFormat("EEEE, d MMMM", Locale.ENGLISH).format(new Date()) + ".";
            if (t.contains("battery")) {
                BatteryManager bm = (BatteryManager) ctx.getSystemService(Context.BATTERY_SERVICE);
                return "Battery is at " + bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY) + " percent.";
            }
            if ((t.equals("go home") || t.equals("home")) && ControlService.instance != null) { ControlService.instance.key("home"); return "Okay."; }
            if ((t.equals("go back") || t.equals("back")) && ControlService.instance != null) { ControlService.instance.key("back"); return "Okay."; }
        } catch (Exception e) {
            return "Sorry, " + (e.getMessage() == null ? "that didn't work." : e.getMessage());
        }
        new Prefs(ctx).enqueue(said);
        return "I'm offline right now, so I've saved that. I'll do it as soon as I'm back online.";
    }

    /** A phone number as said, or looked up in the phone's contacts by name. */
    static String number(Context ctx, String who) {
        String digits = who.replaceAll("[^0-9+]", "");
        if (digits.replace("+", "").length() >= 6) return digits;
        if (ctx.checkSelfPermission(android.Manifest.permission.READ_CONTACTS) != PackageManager.PERMISSION_GRANTED) return null;
        try (Cursor c = ctx.getContentResolver().query(ContactsContract.CommonDataKinds.Phone.CONTENT_URI,
                new String[]{ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME, ContactsContract.CommonDataKinds.Phone.NUMBER},
                ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME + " LIKE ?", new String[]{"%" + who + "%"}, null)) {
            if (c != null && c.moveToFirst()) return c.getString(1).replaceAll("[^0-9+]", "");
        }
        return null;
    }
}
