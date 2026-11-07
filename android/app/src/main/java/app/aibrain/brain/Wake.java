package app.aibrain.brain;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;

/** Finds the activation word in what was heard (same rules as the laptop listener). */
public final class Wake {
    private Wake() { }

    public static String norm(String text) {
        if (text == null) return "";
        String t = text.toLowerCase(Locale.ROOT).replace("[unk]", " ");
        t = t.replaceAll("[^\\p{L}\\p{N}\\s]", "");
        return t.replaceAll("\\s+", " ").trim();
    }

    /** Returns null if the activation word wasn't heard, else what was said after it (maybe ""). */
    public static String split(String heard, List<String> wakeWords, List<String> aliases) {
        List<String> words = new ArrayList<>(Arrays.asList(norm(heard).split(" ")));
        words.removeIf(String::isEmpty);
        for (String w : wakeWords) {
            String[] ws = norm(w).split(" ");
            if (ws.length == 0 || ws[0].isEmpty()) continue;
            for (int i = 0; i + ws.length <= words.size(); i++) {
                boolean match = true;
                for (int j = 0; j < ws.length; j++) if (!words.get(i + j).equals(ws[j])) { match = false; break; }
                if (match) return String.join(" ", words.subList(i + ws.length, words.size()));
            }
        }
        // learned spellings of unusual names vary a little: allow small differences near the start
        for (String a : aliases) {
            String an = norm(a);
            if (an.isEmpty()) continue;
            int n = an.split(" ").length;
            for (int size = Math.max(1, n - 1); size <= n + 1; size++) {
                for (int i = 0; i < 3 && i + size <= words.size(); i++) {
                    String chunk = String.join(" ", words.subList(i, i + size));
                    if (ratio(chunk, an) >= 0.75) return String.join(" ", words.subList(i + size, words.size()));
                }
            }
        }
        return null;
    }

    /** Similarity 0..1 (like Python's difflib ratio, based on edit distance). */
    static double ratio(String a, String b) {
        int[][] d = new int[a.length() + 1][b.length() + 1];
        for (int i = 0; i <= a.length(); i++) d[i][0] = i;
        for (int j = 0; j <= b.length(); j++) d[0][j] = j;
        for (int i = 1; i <= a.length(); i++)
            for (int j = 1; j <= b.length(); j++)
                d[i][j] = Math.min(Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1),
                        d[i - 1][j - 1] + (a.charAt(i - 1) == b.charAt(j - 1) ? 0 : 1));
        int max = Math.max(a.length(), b.length());
        return max == 0 ? 1.0 : 1.0 - (double) d[a.length()][b.length()] / max;
    }
}
