package org.insibag.kadraj;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;
import android.webkit.MimeTypeMap;

import java.io.File;
import java.io.FileNotFoundException;
import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * KADRAJ'ın dosya sağlayıcısı (docs/steps/MOBILE_6.md, karar M20).
 *
 * Uygulamanın kendi dosyalarını ("Aç", "Paylaş") başka bir uygulamaya yalnız okunur açar. Sağlayıcı dışa kapalıdır
 * (exported=false): bir dosyaya yalnız o dosya için gönderilen niyetteki (intent) geçici izinle erişilir. Adres
 * biçimi content://&lt;paket&gt;.files/&lt;kök&gt;/&lt;yol&gt;; kökler "ext" (uygulamaya ayrılmış dış depolama, Belgeler
 * burada) ve "int" (iç klasör). Adı noktayla başlayan klasörler (.kadraj: ayarlar, kasa) hiçbir zaman açılmaz.
 *
 * Süreç başlarken Android önce sağlayıcıları kurar; Python ile konuşan komut köprüsü ({@link Bridge}) burada başlar.
 */
public class KadrajFiles extends ContentProvider {
    private static final String[] DEFAULT_COLUMNS = {OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE};
    private static final String[] ROOTS = {"ext", "int"};

    static String authority(Context ctx) {
        return ctx.getPackageName() + ".files";
    }

    @Override
    public boolean onCreate() {
        Context ctx = getContext();
        if (ctx != null) {
            Bridge.start(ctx);
        }
        return true;
    }

    private static File root(Context ctx, String name) {
        if ("ext".equals(name)) {
            return ctx.getExternalFilesDir(null);
        }
        if ("int".equals(name)) {
            return ctx.getFilesDir();
        }
        return null;
    }

    /** Göreli yolun parçalarından biri boş, "." / ".." ya da gizliyse (nokta ile başlıyorsa) true. */
    private static boolean hidden(List<String> parts) {
        if (parts.isEmpty()) {
            return true;
        }
        for (String part : parts) {
            if (part.isEmpty() || part.startsWith(".")) {
                return true;
            }
        }
        return false;
    }

    private static List<String> split(String rel) {
        List<String> parts = new ArrayList<>();
        for (String part : rel.split("/", -1)) {
            parts.add(part);
        }
        return parts;
    }

    /** Dosyanın paylaşım adresi; dosya köklerin dışındaysa ya da gizli bir klasördeyse null. */
    static Uri uriFor(Context ctx, File file) throws IOException {
        String path = file.getCanonicalPath();
        for (String name : ROOTS) {
            File base = root(ctx, name);
            if (base == null) {
                continue;
            }
            String rootPath = base.getCanonicalPath();
            if (!path.startsWith(rootPath + "/")) {
                continue;
            }
            List<String> parts = split(path.substring(rootPath.length() + 1));
            if (hidden(parts)) {
                return null;
            }
            Uri.Builder builder = new Uri.Builder().scheme("content").authority(authority(ctx)).appendPath(name);
            for (String part : parts) {
                builder.appendPath(part);
            }
            return builder.build();
        }
        return null;
    }

    private File fileFor(Uri uri) throws FileNotFoundException {
        Context ctx = getContext();
        List<String> segments = uri.getPathSegments();
        if (ctx == null || segments.size() < 2) {
            throw new FileNotFoundException("bad uri");
        }
        File base = root(ctx, segments.get(0));
        List<String> parts = new ArrayList<>(segments.subList(1, segments.size()));
        if (base == null || hidden(parts)) {
            throw new FileNotFoundException("not shared");
        }
        try {
            String rootPath = base.getCanonicalPath();
            File file = new File(base, String.join("/", parts)).getCanonicalFile();
            if (!file.getPath().startsWith(rootPath + "/") || !file.isFile()) {
                throw new FileNotFoundException("not shared");
            }
            return file;
        } catch (IOException e) {
            throw new FileNotFoundException("not shared");
        }
    }

    static String mimeOf(String name) {
        int dot = name.lastIndexOf('.');
        if (dot >= 0 && dot < name.length() - 1) {
            String ext = name.substring(dot + 1).toLowerCase(Locale.ROOT);
            String mime = MimeTypeMap.getSingleton().getMimeTypeFromExtension(ext);
            if (mime != null) {
                return mime;
            }
        }
        return "application/octet-stream";
    }

    @Override
    public String getType(Uri uri) {
        String last = uri.getLastPathSegment();
        return last == null ? null : mimeOf(last);
    }

    @Override
    public Cursor query(Uri uri, String[] projection, String selection, String[] selectionArgs, String sortOrder) {
        File file;
        try {
            file = fileFor(uri);
        } catch (FileNotFoundException e) {
            return null;
        }
        String[] asked = projection == null ? DEFAULT_COLUMNS : projection;
        List<String> columns = new ArrayList<>();
        List<Object> values = new ArrayList<>();
        for (String column : asked) {
            if (OpenableColumns.DISPLAY_NAME.equals(column)) {
                columns.add(column);
                values.add(file.getName());
            } else if (OpenableColumns.SIZE.equals(column)) {
                columns.add(column);
                values.add(file.length());
            }
        }
        MatrixCursor cursor = new MatrixCursor(columns.toArray(new String[0]), 1);
        cursor.addRow(values.toArray());
        return cursor;
    }

    @Override
    public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        if (!"r".equals(mode)) {
            throw new SecurityException("read only");
        }
        return ParcelFileDescriptor.open(fileFor(uri), ParcelFileDescriptor.MODE_READ_ONLY);
    }

    @Override
    public Uri insert(Uri uri, ContentValues values) {
        throw new UnsupportedOperationException("read only");
    }

    @Override
    public int update(Uri uri, ContentValues values, String selection, String[] selectionArgs) {
        throw new UnsupportedOperationException("read only");
    }

    @Override
    public int delete(Uri uri, String selection, String[] selectionArgs) {
        throw new UnsupportedOperationException("read only");
    }
}
