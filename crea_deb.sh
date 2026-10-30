#!/bin/sh
set -e

QUI="$(cd "$(dirname "$0")" && pwd)"
cd "$QUI"

VER="${1:-$(sed -n 's/^VERSIONE = "\(.*\)"/\1/p' xvb.py)}"
[ -n "$VER" ] || VER=1.0

# Controllo dei file necessari
for f in xvb.py icone icon.png fonts; do
    [ -e "$f" ] || {
        echo "ERRORE: manca $f in $QUI"
        exit 1
    }
done

# Controllo dei font Google Sans
for f in \
    fonts/static/GoogleSans-Regular.ttf \
    fonts/static/GoogleSans-Bold.ttf \
    
do
    [ -f "$f" ] || {
        echo "ERRORE: manca il font $f"
        exit 1
    }
done

D="$(mktemp -d)"
trap 'rm -rf "$D"' EXIT

mkdir -p "$D/DEBIAN" \
         "$D/usr/lib/xvb" \
         "$D/usr/bin" \
         "$D/usr/share/applications" \
         "$D/usr/share/icons/hicolor/512x512/apps" \
         "$D/usr/share/pixmaps" \
         "$D/usr/share/metainfo"

# Applicazione e risorse
cp xvb.py "$D/usr/lib/xvb/"
cp -a icone "$D/usr/lib/xvb/"
cp -a fonts "$D/usr/lib/xvb/"

# Icone
cp icon.png "$D/usr/share/icons/hicolor/512x512/apps/xvb.png"
cp icon.png "$D/usr/share/pixmaps/xvb.png"

# Collegamento Python
ln -s /usr/bin/python3 "$D/usr/lib/xvb/xvb"

# Avviatore
cat > "$D/usr/bin/xvb" <<'FINE'
#!/bin/sh
exec /usr/lib/xvb/xvb /usr/lib/xvb/xvb.py "$@"
FINE

chmod 755 "$D/usr/bin/xvb"

# Desktop File
cat > "$D/usr/share/applications/xvb.desktop" <<'FINE'
[Desktop Entry]
Version=1.0
Type=Application
Name=XVB Player
Comment=Media player for videos, music, discs and IPTV
Exec=xvb %F
Icon=xvb
Terminal=false
Categories=AudioVideo;Video;Player;TV;
Keywords=media;player;video;audio;iptv;mpv;
StartupNotify=true
StartupWMClass=Xvb
MimeType=video/mp4;video/x-matroska;video/webm;video/x-msvideo;video/quicktime;video/mpeg;video/mp2t;video/x-flv;video/x-ms-wmv;video/ogg;video/x-m4v;audio/mpeg;audio/flac;audio/x-flac;audio/ogg;audio/x-vorbis+ogg;audio/opus;audio/mp4;audio/x-m4a;audio/aac;audio/x-wav;audio/wav;audio/x-ms-wma;audio/x-ape;audio/x-mpegurl;audio/mpegurl;application/vnd.apple.mpegurl;
FINE

# AppStream Metainfo
cat > "$D/usr/share/metainfo/xvb.metainfo.xml" <<FINE
<?xml version="1.0" encoding="UTF-8"?>
<component type="desktop-application">
  <id>xvb.desktop</id>
  <metadata_license>CC0-1.0</metadata_license>
  <project_license>MIT</project_license>
  <name>XVB Player</name>
  <summary>Media player for videos, music, discs and IPTV</summary>
  <description>
    <p>Media player built on libmpv. It plays local videos and music, video DVDs, data discs and ISO images, and IPTV playlists (m3u) with an XMLTV guide and recording.</p>
  </description>
  <launchable type="desktop-id">xvb.desktop</launchable>
  <icon type="cached">xvb</icon>
  <categories>
    <category>AudioVideo</category>
    <category>Video</category>
    <category>Player</category>
  </categories>
  <url type="homepage">https://github.com/JonaDev2026/xvb</url>
  <url type="bugtracker">https://github.com/JonaDev2026/xvb/issues</url>
  <screenshots>
    <screenshot type="default">
      <caption>XVB Player Main Window</caption>
      <image type="source">https://raw.githubusercontent.com/JonaDev2026/xvb/main/preview.png</image>
    </screenshot>
  </screenshots>
</component>
FINE

# Triggers
cat > "$D/DEBIAN/triggers" <<'FINE'
activate-noawait update-desktop-database
activate-noawait gtk-update-icon-cache
FINE

# Dimensione installata
INSTALLED_SIZE=$(du -s --block-size=1k "$D" | cut -f1)

# Control
cat > "$D/DEBIAN/control" <<FINE
Package: xvb
Version: $VER
Section: video
Priority: optional
Architecture: all
Installed-Size: $INSTALLED_SIZE
Homepage: https://github.com/JonaDev2026/xvb
Depends: python3, librsvg2-bin, python3-tk, python3-pil, python3-pil.imagetk, python3-mpv, python3-mutagen, libmpv2 | libmpv1
Recommends: ffmpeg, tkdnd, python3-dbus, python3-gi
Maintainer: Jona <jonalinux.uk@gmail.com>
Description: Media player for videos, music, discs and IPTV
 Media player for videos, music, discs and IPTV (m3u playlists,
 XMLTV guide, recording), built on libmpv.
FINE

chmod -R go-w "$D"
chmod 755 "$D"

dpkg-deb --build --root-owner-group "$D" "$QUI/xvb_${VER}_all.deb"

echo "Fatto: xvb_${VER}_all.deb"

