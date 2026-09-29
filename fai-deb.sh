#!/bin/sh
# Fa il pacchetto .deb di XVB da questa cartella: xvb.py, icone/ e
# icon.png cosi' come sono. Uso:  sh fai-deb.sh [versione]
# Senza versione usa VERSIONE scritta in xvb.py.
set -e
QUI="$(cd "$(dirname "$0")" && pwd)"
cd "$QUI"
# la versione: quella passata, se no quella scritta in xvb.py (VERSIONE)
VER="${1:-$(sed -n 's/^VERSIONE = "\(.*\)"/\1/p' xvb.py)}"
[ -n "$VER" ] || VER=1.0
for f in xvb.py icone icon.png; do
    [ -e "$f" ] || { echo "manca $f in $QUI"; exit 1; }
done
D="$(mktemp -d)"
mkdir -p "$D/DEBIAN" "$D/usr/lib/xvb" "$D/usr/bin" \
         "$D/usr/share/applications" "$D/usr/share/icons/hicolor/512x512/apps"
cp xvb.py "$D/usr/lib/xvb/"
cp -r icone "$D/usr/lib/xvb/"
cp icon.png "$D/usr/share/icons/hicolor/512x512/apps/xvb.png"

# l'interprete passa da un link chiamato xvb: cosi' il processo si chiama
# xvb (non python3) e il monitor di sistema gli da' l'icona giusta
ln -s /usr/bin/python3 "$D/usr/lib/xvb/xvb"
cat > "$D/usr/bin/xvb" <<'FINE'
#!/bin/sh
exec /usr/lib/xvb/xvb /usr/lib/xvb/xvb.py "$@"
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
