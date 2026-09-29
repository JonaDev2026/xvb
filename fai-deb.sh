#!/bin/sh
# Fa il pacchetto .deb di XVB da questa cartella: xvb.py, icone/ e
# icon.png cosi' come sono. Uso:  sh fai-deb.sh [versione]
set -e
VER="${1:-1.0}"
QUI="$(cd "$(dirname "$0")" && pwd)"
cd "$QUI"
for f in xvb.py icone icon.png; do
    [ -e "$f" ] || { echo "manca $f in $QUI"; exit 1; }
done
D="$(mktemp -d)"
mkdir -p "$D/DEBIAN" "$D/usr/lib/xvb" "$D/usr/bin" \
         "$D/usr/share/applications" "$D/usr/share/icons/hicolor/512x512/apps"
cp xvb.py "$D/usr/lib/xvb/"
cp -r icone "$D/usr/lib/xvb/"
cp icon.png "$D/usr/share/icons/hicolor/512x512/apps/xvb.png"

cat > "$D/usr/bin/xvb" <<'FINE'
#!/bin/sh
exec python3 /usr/lib/xvb/xvb.py "$@"
FINE
chmod 755 "$D/usr/bin/xvb"

cat > "$D/usr/share/applications/xvb.desktop" <<'FINE'
[Desktop Entry]
Version=1.0
Type=Application
Name=XVB
Comment=Extended Video Broadcast - IPTV player
Exec=xvb
Icon=xvb
Terminal=false
Categories=AudioVideo;Video;Player;TV;
StartupNotify=true
StartupWMClass=Xvb
FINE

cat > "$D/DEBIAN/control" <<FINE
Package: xvb
Version: $VER
Section: video
Priority: optional
Architecture: all
Depends: python3, python3-tk, python3-pil, python3-pil.imagetk, python3-mpv, libmpv2 | libmpv1
Maintainer: Jona <jonalinux.uk@gmail.com>
Description: XVB - Extended Video Broadcast
 IPTV player: m3u playlists, XMLTV guide, favorites, recording.
FINE

chmod -R go-w "$D"
chmod 755 "$D"
dpkg-deb --build --root-owner-group "$D" "$QUI/xvb_${VER}_all.deb"
rm -rf "$D"
echo "fatto: xvb_${VER}_all.deb"
echo "installa con:  sudo apt install ./xvb_${VER}_all.deb"
