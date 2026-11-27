package org.insibag.kadraj;

import android.app.Activity;
import android.app.Application;
import android.content.ActivityNotFoundException;
import android.content.ClipData;
import android.content.Context;
import android.content.Intent;
import android.media.MediaScannerConnection;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.FileObserver;
import android.os.Handler;
import android.os.Looper;
import android.os.Process;
import android.util.Log;
import android.view.WindowManager;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.lang.ref.WeakReference;
import java.nio.charset.StandardCharsets;
import java.util.regex.Pattern;

/**
 * Python ile Java arasındaki komut köprüsü (docs/steps/MOBILE_6.md, karar M20). PySide6'nın Android yapısında
 * JNI bağı olmadığı için iki taraf dosyayla konuşur.
 *
 * Python uygulamanın iç klasörüne files/.kadraj/bridge/&lt;kimlik&gt;.req (JSON) yazar. Bu sınıf klasörü FileObserver
 * ile izler, işi yapar ve sonucu &lt;kimlik&gt;.res ({"ok": true} ya da {"ok": false, "error": "..."}) olarak yazar.
 * Klasör yalnız bu uygulamaya açıktır; başka bir uygulama komut gönderemez. "ready" dosyası köprünün bu süreçte
 * çalıştığını söyler (protokol sürümü ve süreç kimliği).
 *
 * İşler: "open" (dosyayı uygun uygulamayla aç), "share" (paylaşım menüsü), "screen" (ekran açık kalsın),
 * "scan" (yeni dosyaları galeriye tanıt). Python tarafı: kadraj/bridge.py.
 */
final class Bridge {
    static final int PROTOCOL = 1;
    private static final String TAG = "KadrajBridge";
    private static final Pattern ID = Pattern.compile("[A-Za-z0-9_-]{1,64}");
    private static Bridge instance;

    private final Context app;
    private final File dir;
    private final Handler main = new Handler(Looper.getMainLooper());
    private WeakReference<Activity> current = new WeakReference<>(null);
    private boolean keepScreenOn = false;
    private Watcher watcher;                 // güçlü başvuru: FileObserver çöpe giderse izleme durur

    static synchronized void start(Context ctx) {
        if (instance != null) {
            return;
        }
        Context app = ctx.getApplicationContext() != null ? ctx.getApplicationContext() : ctx;
        try {
            Bridge bridge = new Bridge(app);
            bridge.begin();
            instance = bridge;
        } catch (Throwable t) {
            Log.e(TAG, "köprü başlatılamadı", t);
        }
    }

    private Bridge(Context app) {
        this.app = app;
        this.dir = new File(new File(app.getFilesDir(), ".kadraj"), "bridge");
    }

    private void begin() throws IOException, JSONException {
        if (!dir.isDirectory() && !dir.mkdirs()) {
            throw new IOException("klasör yok: " + dir);
        }
        File[] old = dir.listFiles();
        if (old != null) {
            for (File f : old) {
                f.delete();                  // önceki oturumdan kalan istek ve sonuçlar
            }
        }
        if (app instanceof Application) {
            ((Application) app).registerActivityLifecycleCallbacks(new Tracker());
        }
        watcher = Build.VERSION.SDK_INT >= 29 ? new Watcher(dir) : new Watcher(dir.getPath());
        watcher.startWatching();
        JSONObject ready = new JSONObject();
        ready.put("protocol", PROTOCOL);
        ready.put("pid", Process.myPid());
        write("ready", ready);
    }

    // -- istekler -------------------------------------------------------------------------------------------------
    private void onFile(String name) {
        if (name == null || !name.endsWith(".req")) {
            return;
        }
        String id = name.substring(0, name.length() - 4);
        File req = new File(dir, name);
        if (!ID.matcher(id).matches() || !req.isFile()) {
            return;                          // aynı dosya için ikinci olay (CLOSE_WRITE + MOVED_TO) ya da geçersiz ad
        }
        JSONObject cmd;
        try {
            cmd = new JSONObject(read(req));
        } catch (IOException | JSONException e) {
            req.delete();
            answer(id, "bad_request");
            return;
        }
        req.delete();
        try {
            handle(id, cmd);
        } catch (Throwable t) {
            Log.e(TAG, "istek başarısız: " + cmd.optString("action"), t);
            answer(id, "failed");
        }
    }

    private void handle(String id, JSONObject cmd) throws IOException, JSONException {
        String action = cmd.optString("action");
        switch (action) {
            case "open":
            case "share": {
                File file = new File(cmd.optString("path"));
                if (!file.isFile()) {
                    answer(id, "not_found");
                    return;
                }
                Uri uri = KadrajFiles.uriFor(app, file);
                if (uri == null) {
                    answer(id, "not_shared");
                    return;
                }
                String mime = cmd.optString("mime");
                if (mime.isEmpty()) {
                    mime = KadrajFiles.mimeOf(file.getName());
                }
                Intent intent;
                if ("open".equals(action)) {
                    intent = new Intent(Intent.ACTION_VIEW);
                    intent.setDataAndType(uri, mime);
                } else {
                    Intent send = new Intent(Intent.ACTION_SEND);
                    send.setType(mime);
                    send.putExtra(Intent.EXTRA_STREAM, uri);
                    send.setClipData(ClipData.newRawUri(file.getName(), uri));
                    send.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
                    String title = cmd.optString("title");
                    intent = Intent.createChooser(send, title.isEmpty() ? null : title);
                }
                intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
                launch(id, intent);
                return;
            }
            case "screen":
                keepScreenOn = cmd.optBoolean("on", false);
                applyScreen(current.get());
                answer(id, null);
                return;
            case "scan": {
                JSONArray list = cmd.optJSONArray("paths");
                int n = list == null ? 0 : list.length();
                String[] paths = new String[n];
                for (int i = 0; i < n; i++) {
                    paths[i] = list.optString(i);
                }
                if (n > 0) {
                    MediaScannerConnection.scanFile(app, paths, null, null);
                }
                answer(id, null);
                return;
            }
            default:
                answer(id, "unknown_action");
        }
    }

    private void launch(String id, Intent intent) {
        Activity activity = current.get();
        try {
            if (activity != null && !activity.isFinishing()) {
                activity.startActivity(intent);
            } else {
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                app.startActivity(intent);
            }
            answer(id, null);
        } catch (ActivityNotFoundException e) {
            answer(id, "no_app");
        } catch (SecurityException e) {
            Log.e(TAG, "izin yok", e);
            answer(id, "denied");
        }
    }

    private void applyScreen(Activity activity) {
        if (activity == null) {
            return;
        }
        if (keepScreenOn) {
            activity.getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        } else {
            activity.getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        }
    }

    // -- dosyalar -------------------------------------------------------------------------------------------------
    private void answer(String id, String error) {
        try {
            JSONObject res = new JSONObject();
            res.put("ok", error == null);
            if (error != null) {
                res.put("error", error);
            }
            write(id + ".res", res);
        } catch (IOException | JSONException e) {
            Log.e(TAG, "sonuç yazılamadı", e);
        }
    }

    /** Önce geçici dosyaya yazar, sonra adını değiştirir: Python yarım dosya okumaz. */
    private void write(String name, JSONObject data) throws IOException {
        File tmp = new File(dir, name + ".tmp");
        try (OutputStream out = new FileOutputStream(tmp)) {
            out.write(data.toString().getBytes(StandardCharsets.UTF_8));
        }
        if (!tmp.renameTo(new File(dir, name))) {
            tmp.delete();
            throw new IOException("ad değiştirilemedi: " + name);
        }
    }

    private static String read(File file) throws IOException {
        ByteArrayOutputStream buf = new ByteArrayOutputStream();
        try (InputStream in = new FileInputStream(file)) {
            byte[] chunk = new byte[8192];
            int n;
            while ((n = in.read(chunk)) > 0) {
                buf.write(chunk, 0, n);
                if (buf.size() > 1 << 20) {
                    throw new IOException("istek çok büyük");
                }
            }
        }
        return new String(buf.toByteArray(), StandardCharsets.UTF_8);
    }

    // -- izleyiciler ----------------------------------------------------------------------------------------------
    private final class Watcher extends FileObserver {
        private static final int MASK = FileObserver.CLOSE_WRITE | FileObserver.MOVED_TO;

        Watcher(File folder) {
            super(folder, MASK);
        }

        @SuppressWarnings("deprecation")
        Watcher(String folder) {
            super(folder, MASK);
        }

        @Override
        public void onEvent(int event, final String name) {
            main.post(new Runnable() {       // işler arayüz iş parçacığında (Android'in; Qt'ninki ayrı)
                @Override
                public void run() {
                    onFile(name);
                }
            });
        }
    }

    private final class Tracker implements Application.ActivityLifecycleCallbacks {
        @Override
        public void onActivityCreated(Activity activity, Bundle state) {
        }

        @Override
        public void onActivityStarted(Activity activity) {
        }

        @Override
        public void onActivityResumed(Activity activity) {
            current = new WeakReference<>(activity);
            applyScreen(activity);
        }

        @Override
        public void onActivityPaused(Activity activity) {
        }

        @Override
        public void onActivityStopped(Activity activity) {
        }

        @Override
        public void onActivitySaveInstanceState(Activity activity, Bundle state) {
        }

        @Override
        public void onActivityDestroyed(Activity activity) {
            if (current.get() == activity) {
                current = new WeakReference<>(null);
            }
        }
    }
}
