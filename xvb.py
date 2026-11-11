import gzip
import hashlib
import json
import math
import os
import random
import re
import socket
import subprocess
import sys
import threading
import time

import logging
from logging.handlers import RotatingFileHandler


_XVB_LOGGER = logging.getLogger("xvb")


class _XVBFiltroSicurezza(logging.Filter):
    """Oscura credenziali e token nei messaggi del log."""

    _parametri = re.compile(
        r"(?i)(password|passwd|username|user|token|"
        r"api[_-]?key|authorization|secret)=([^&\\s]+)"
    )
    _autenticazione = re.compile(
        r"(?i)(https?://)[^/@\\s:]+:[^/@\\s]+@"
    )

    def filter(self, record):
        try:
            testo = record.getMessage()
            testo = self._parametri.sub(r"\\1=[REDACTED]", testo)
            testo = self._autenticazione.sub(r"\\1[REDACTED]@", testo)
            record.msg = testo
            record.args = ()
        except Exception:
            pass
        return True


def _xvb_eccezione(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return

    _XVB_LOGGER.critical(
        "Eccezione non gestita",
        exc_info=(exc_type, exc_value, exc_traceback)
    )


def _xvb_eccezione_thread(args):
    _XVB_LOGGER.error(
        "Eccezione non gestita nel thread %s",
        getattr(args.thread, "name", "sconosciuto"),
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback)
    )


def _xvb_eccezione_tk(exc_type, exc_value, exc_traceback):
    _XVB_LOGGER.error(
        "Errore in un callback Tkinter",
        exc_info=(exc_type, exc_value, exc_traceback)
    )


def configura_logging():
    """Configura il registro degli errori di XVB."""

    if _XVB_LOGGER.handlers:
        return

    _XVB_LOGGER.setLevel(logging.INFO)
    _XVB_LOGGER.propagate = False

    filtro = _XVBFiltroSicurezza()

    percorsi = (
        os.path.join(CASA, ".local", "state", "xvb"),
        os.path.join(CASA, ".cache", "xvb"),
    )

    for cartella in percorsi:
        try:
            os.makedirs(cartella, exist_ok=True)
            percorso = os.path.join(cartella, "xvb.log")

            gestore = RotatingFileHandler(
                percorso,
                maxBytes=2 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8"
            )
            gestore.setFormatter(logging.Formatter(
                "%(asctime)s | %(levelname)s | %(threadName)s | %(message)s"
            ))
            gestore.addFilter(filtro)
            _XVB_LOGGER.addHandler(gestore)

            _XVB_LOGGER.info("XVB %s avviato", VERSIONE)

            sys.excepthook = _xvb_eccezione

            if hasattr(threading, "excepthook"):
                threading.excepthook = _xvb_eccezione_thread

            return

        except OSError:
            continue

    gestore = logging.StreamHandler(sys.stderr)
    gestore.addFilter(filtro)
    _XVB_LOGGER.addHandler(gestore)
    _XVB_LOGGER.error("Impossibile creare il file di log su disco")




_FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
_FONT_STATIC_DIR = os.path.join(_FONT_DIR, "static")
_FONTCONFIG_FILE = os.path.join(_FONT_DIR, "xvb-fonts.conf")
if os.path.isdir(_FONT_STATIC_DIR):
    try:
        import xml.sax.saxutils as _xml_escape
        _fc_xml = (
            '<?xml version="1.0"?>\n'
            '<!DOCTYPE fontconfig SYSTEM "fonts.dtd">\n'
            '<fontconfig>\n'
            '  <include>/etc/fonts/fonts.conf</include>\n'
            '  <dir>' + _xml_escape.escape(_FONT_STATIC_DIR) + '</dir>\n'
            '</fontconfig>\n'
        )
        with open(_FONTCONFIG_FILE, "w", encoding="utf-8") as _fc_file:
            _fc_file.write(_fc_xml)
        os.environ["FONTCONFIG_FILE"] = _FONTCONFIG_FILE
        subprocess.run(["fc-cache", _FONT_STATIC_DIR],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=10, check=False)
    except Exception:
        pass

import tkinter as tk
from tkinter import filedialog, ttk
import tkinter.font as tkfont
import xml.etree.ElementTree as ET
from urllib.parse import quote, urljoin, urlparse, urlencode
import io
import zipfile
from urllib.request import Request, urlopen
import tkinter.font as tkfont

import mpv


def opzioni_mpv_xwayland():
    if os.environ.get("WAYLAND_DISPLAY") and os.environ.get("DISPLAY"):
        return {"gpu_context": "x11egl"}
    return {}

try:
    import mutagen
    HA_TAG = True
except ImportError:
    HA_TAG = False
try:
    from PIL import Image, ImageTk
    HA_PIL = True
except ImportError:
    HA_PIL = False
try:
    import dbus
    import dbus.service
    import dbus.mainloop.glib
    from gi.repository import GLib
    HA_MPRIS = True
except Exception:
    HA_MPRIS = False

MPRIS_RADICE = "org.mpris.MediaPlayer2"
MPRIS_PLAYER = "org.mpris.MediaPlayer2.Player"
MPRIS_PROP = "org.freedesktop.DBus.Properties"


OV_SFONDO, OV_BARRA, OV_PIP, OV_MENU, OV_EPG = 59, 60, 61, 62, 64
OV_HUD = 63


def premoltiplica(im):
    """mpv vuole le sovrapposizioni con l'alfa gia' moltiplicato nei colori
    (python-mpv dice di farlo ma non lo fa): senza, le parti semitrasparenti
    escono coi colori pieni e la sfumatura diventa una fascia."""
    from PIL import ImageChops
    r, g, b, a = im.convert("RGBA").split()
    return Image.merge("RGBA", (ImageChops.multiply(r, a), ImageChops.multiply(g, a),
                                ImageChops.multiply(b, a), a))


def avvia_mpris(app):
    """XVB sul bus di sessione come lettore MPRIS: i tasti multimediali
    della tastiera e il pannello audio di Cinnamon (titolo, copertina,
    comandi). Il bus gira in un thread suo col ciclo di GLib; i comandi
    tornano a Tk con root.after. None se dbus manca o il nome e' preso."""
    if not HA_MPRIS:
        return None
    try:
        dbus.mainloop.glib.threads_init()
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        bus = dbus.SessionBus()
        nome = dbus.service.BusName(MPRIS_RADICE + ".xvb", bus, do_not_queue=True)
    except Exception:
        return None

    class Mpris(dbus.service.Object):
        def __init__(self):
            super().__init__(nome, "/org/mpris/MediaPlayer2")
            self.stato = {}

        def tk(self, f, *a):
            app.root.after(0, lambda: f(*a))


        @dbus.service.method(MPRIS_RADICE)
        def Raise(self):
            self.tk(app.mpris_alza)

        @dbus.service.method(MPRIS_RADICE)
        def Quit(self):
            self.tk(app.chiudi)


        @dbus.service.method(MPRIS_PLAYER)
        def Next(self):
            self.tk(app.salta, +1)

        @dbus.service.method(MPRIS_PLAYER)
        def Previous(self):
            self.tk(app.salta, -1)

        @dbus.service.method(MPRIS_PLAYER)
        def PlayPause(self):
            self.tk(app.mpris_comando, "playpause")

        @dbus.service.method(MPRIS_PLAYER)
        def Play(self):
            self.tk(app.mpris_comando, "play")

        @dbus.service.method(MPRIS_PLAYER)
        def Pause(self):
            self.tk(app.mpris_comando, "pause")

        @dbus.service.method(MPRIS_PLAYER)
        def Stop(self):
            self.tk(app.ferma)

        @dbus.service.method(MPRIS_PLAYER, in_signature="x")
        def Seek(self, us):
            self.tk(app.avanza, int(us) / 1e6)

        @dbus.service.method(MPRIS_PLAYER, in_signature="ox")
        def SetPosition(self, _traccia, us):
            self.tk(app.mpris_posizione, int(us) / 1e6)

        @dbus.service.method(MPRIS_PLAYER, in_signature="s")
        def OpenUri(self, _uri):
            pass

        @dbus.service.signal(MPRIS_PLAYER, signature="x")
        def Seeked(self, us):
            pass


        def tutte(self, iface):
            if iface == MPRIS_RADICE:
                return dbus.Dictionary({
                    "CanQuit": True, "CanRaise": True, "HasTrackList": False,
                    "Identity": "XVB Player", "DesktopEntry": "xvb",
                    "SupportedUriSchemes": dbus.Array(["file", "http", "https"], signature="s"),
                    "SupportedMimeTypes": dbus.Array([], signature="s"),
                }, signature="sv")
            if iface != MPRIS_PLAYER:
                return dbus.Dictionary({}, signature="sv")
            st = self.stato
            meta = {"mpris:trackid": dbus.ObjectPath("/org/xvb/track/%d" % st.get("giro", 0))}
            if st.get("titolo"):
                meta["xesam:title"] = st["titolo"]
            if st.get("artista"):
                meta["xesam:artist"] = dbus.Array([st["artista"]], signature="s")
            if st.get("album"):
                meta["xesam:album"] = st["album"]
            if st.get("arte"):
                meta["mpris:artUrl"] = "file://" + quote(st["arte"])
            if st.get("durata"):
                meta["mpris:length"] = dbus.Int64(int(st["durata"] * 1e6))
            try:
                pos = int((app.mpv.time_pos or 0) * 1e6)
            except Exception:
                pos = 0
            return dbus.Dictionary({
                "PlaybackStatus": st.get("stato", "Stopped"),
                "LoopStatus": st.get("giro_ripeti", "None"),
                "Rate": 1.0, "MinimumRate": 1.0, "MaximumRate": 1.0,
                "Shuffle": bool(st.get("shuffle")),
                "Metadata": dbus.Dictionary(meta, signature="sv"),
                "Volume": float(st.get("volume", 1.0)),
                "Position": dbus.Int64(pos),
                "CanGoNext": True, "CanGoPrevious": True,
                "CanPlay": True, "CanPause": True,
                "CanSeek": bool(st.get("durata")), "CanControl": True,
            }, signature="sv")

        @dbus.service.method(MPRIS_PROP, in_signature="ss", out_signature="v")
        def Get(self, iface, prop):
            return self.tutte(iface)[prop]

        @dbus.service.method(MPRIS_PROP, in_signature="s", out_signature="a{sv}")
        def GetAll(self, iface):
            return self.tutte(iface)

        @dbus.service.method(MPRIS_PROP, in_signature="ssv")
        def Set(self, iface, prop, v):
            if prop == "Volume":
                self.tk(app.volume.set, int(max(0.0, min(1.5, float(v))) * 100))
            elif prop == "Shuffle":
                self.tk(app.mpris_shuffle, bool(v))
            elif prop == "LoopStatus":
                self.tk(app.metti_ripeti,
                        {"Track": "uno", "Playlist": "tutti"}.get(str(v), ""))

        @dbus.service.signal(MPRIS_PROP, signature="sa{sv}as")
        def PropertiesChanged(self, iface, cambiati, tolti):
            pass

        def aggiorna(self, stato):
            """Da Tk: se qualcosa e' cambiato, lo si dice al desktop."""
            if stato == self.stato:
                return
            self.stato = stato

            def manda():
                try:
                    tutto = self.tutte(MPRIS_PLAYER)
                    del tutto["Position"]
                    self.PropertiesChanged(MPRIS_PLAYER, tutto, dbus.Array([], signature="s"))
                except Exception:
                    pass
                return False
            GLib.idle_add(manda)

    try:
        m = Mpris()
    except Exception:
        return None
    threading.Thread(target=GLib.MainLoop().run, daemon=True).start()
    return m

VERSIONE = "4.5.4"
AUTORE = "Jonathan Sanfilippo"
ANNO = "2026"
REPO = "JonaDev2026/xvb"
DONA = "https://www.paypal.com/paypalme/Jonathanuk"
UA = "Mozilla/5.0 (X11; Linux x86_64)"
IN_AFFANNO = 3.0
TRANQUILLO = 15.0
QUANTO_TRANQUILLO = 20.0
DOPO_UNA_DISCESA = 120.0
IN_CALO = -0.30
QUANTO_IN_CALO = 4

QUI = os.path.dirname(os.path.abspath(__file__))
CASA = os.path.expanduser("~")
CONFIG = os.path.join(CASA, ".config", "xvb", "xvb.json")
CONFIG_VECCHIO = os.path.join(CASA, ".config", "tv", "tv.json")


CARTELLE_LISTE = (os.path.join(QUI, "playlists"),
                  os.path.join(CASA, ".local", "share", "xvb", "playlists"))
if not os.access(QUI, os.W_OK):


    CARTELLE_LISTE = CARTELLE_LISTE[::-1]

CARTELLE_VECCHIE = tuple(c for c in (os.path.join(QUI, "lists"),
                                     os.path.join(CASA, ".local", "share", "xvb", "lists"))
                         if os.path.isdir(c))
PUNTO = 24


from traduzioni import LINGUE, TESTI, lingua_sistema

for _l, _voci in (
        ("it", ("Taglia", "Copia", "Incolla", "Seleziona tutto", "Buffer", "Minimo", "Basso", "Standard", "Alto")),
        ("es", ("Cortar", "Copiar", "Pegar", "Seleccionar todo", "B\u00fafer", "M\u00ednimo", "Bajo", "Est\u00e1ndar", "Alto")),
        ("fr", ("Couper", "Copier", "Coller", "Tout s\u00e9lectionner", "Tampon", "Minimal", "Bas", "Standard", "\u00c9lev\u00e9")),
        ("de", ("Ausschneiden", "Kopieren", "Einf\u00fcgen", "Alles ausw\u00e4hlen", "Puffer", "Minimal", "Niedrig", "Standard", "Hoch")),
        ("pt", ("Recortar", "Copiar", "Colar", "Selecionar tudo", "Buffer", "M\u00ednimo", "Baixo", "Padr\u00e3o", "Alto")),
        ("ru", ("\u0412\u044b\u0440\u0435\u0437\u0430\u0442\u044c", "\u041a\u043e\u043f\u0438\u0440\u043e\u0432\u0430\u0442\u044c", "\u0412\u0441\u0442\u0430\u0432\u0438\u0442\u044c", "\u0412\u044b\u0434\u0435\u043b\u0438\u0442\u044c \u0432\u0441\u0451", "\u0411\u0443\u0444\u0435\u0440", "\u041c\u0438\u043d\u0438\u043c\u0430\u043b\u044c\u043d\u044b\u0439", "\u041d\u0438\u0437\u043a\u0438\u0439", "\u0421\u0442\u0430\u043d\u0434\u0430\u0440\u0442\u043d\u044b\u0439", "\u0412\u044b\u0441\u043e\u043a\u0438\u0439"))):
    for _k, _t in zip(("Cut", "Copy", "Paste", "Select all", "Buffer", "Minimal", "Low", "Standard", "High"), _voci):
        TESTI[_l].setdefault(_k, _t)

LINGUA = "en"


def _(testo):
    """Il testo nella lingua scelta; se manca, resta l'inglese."""
    return TESTI.get(LINGUA, {}).get(testo, testo)


VELOCITA = (1, 1.25, 1.5, 2, 0.5, 0.75)
PROPORZIONI = (("Auto", "-1"), ("16:9", "16:9"), ("4:3", "4:3"), ("21:9", "21:9"))


LARGA_BARRA = 250
RIDOTTE = 18

PREFERITI = "favorite"


PREFERITI_ACCESI = True
PREF_IPTV = "iptv"
PREF_MUSICA = "music"
PREF_VIDEO = "video"
AUDIO = ("mp3", "flac", "ogg", "opus", "m4a", "aac", "wav", "wma", "ape", "mka")


def tipo_preferiti(url):
    """In quale lista dei preferiti va una cosa: iptv, music o video."""


    if not (os.path.isabs(url) and e_media(url)):
        return PREF_IPTV
    return PREF_MUSICA if os.path.splitext(url)[1][1:].lower() in AUDIO else PREF_VIDEO
ROSSO = "#ff453a"
PALLINI = ("#ff5257", "#ff9f0a", "#ffd60a", "#30d158", "#0a84ff",
           "#bf5af2", "#ff375f", "#64d2ff")


SCALA_FILE = ("#ff5257", "#30d158", "#ff7f11", "#42a0ff",
              "#ffd60a", "#bf5af2", "#ff6fae", "#64d2ff",
              "#c19272", "#66e3b0", "#d4af37", "#9190f9",
              "#e23179", "#b4e04a", "#ff9670", "#c0c7d0")

IN_RIPRODUZIONE = "#42414d"
FONDO = "#1c1b22"
PANNELLO = "#1c1b22"
BARRA = PANNELLO
STATO = "#121217"
TASTO = "#2c2c2c"
SCELTO = "#42414d"
ZEBRA = "#2b2a33"
TESTO = "#e0e0e0"
GRIGIO = "#FFD4A8"
ACCENTO = "#bb86fc"
IN_ONDA = "#42414d"
IN_ONDA2 = "#2b2338" #colore bg della notifica nuova versione
TESTO_ONDA = "#e9ddff"
CERCA = "#42414d"
CIMA = "#1c1b22"
MENU = CIMA


def leggi_config():
    """Quello che ci siamo segnati l'ultima volta. Se c'e' ancora quello
    di quando l'app si chiamava TV, si prende quello."""
    for f in (CONFIG, CONFIG_VECCHIO):
        try:
            with open(f, encoding="utf-8") as h:
                return json.load(h)
        except Exception:
            continue
    return {}


def scrivi_config(d):
    try:
        os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
        with open(CONFIG, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=1)
    except Exception:
        pass


NOMI_URL = {}


def nome_di(dove):
    """Il nome da mostrare: quello del file, senza cartella ne' estensione;
    per una lista da url, il nome che le si e' dato."""
    if dove in NOMI_URL:
        return NOMI_URL[dove]
    return os.path.splitext(os.path.basename(dove))[0] or dove


def e_una_lista(f):
    """Se quel file e' una lista m3u, a guardarci dentro: non conta
    l'estensione, conta che ci siano le righe #EXTM3U o #EXTINF."""
    try:
        with open(f, "rb") as h:
            testa = h.read(4096).decode("utf-8", "ignore")
    except Exception:
        return False
    return "#EXTM3U" in testa or "#EXTINF" in testa


LISTE_NASCOSTE = set()


def liste_in(cartella):
    """Le liste dentro a una cartella, qualunque nome abbiano, con o senza
    estensione: basta che dentro siano m3u."""
    fuori = []
    try:
        for n in sorted(os.listdir(cartella)):
            f = os.path.join(cartella, n)
            if (not n.startswith(".") and os.path.isfile(f) and e_una_lista(f)
                    and os.path.abspath(f) not in LISTE_NASCOSTE):
                fuori.append(f)
    except Exception:
        pass
    return fuori


def liste_in_cartella():
    """Le liste sciolte nelle cartelle playlists/."""
    fuori = []
    for cartella in CARTELLE_LISTE + CARTELLE_VECCHIE:
        fuori += liste_in(cartella)
    return fuori


def categorie():
    """Le sottocartelle di playlists/ (tranne img/): ognuna e' una categoria
    con dentro le sue liste. Torna [(nome, [liste])], solo quelle piene."""
    fuori = []
    for cartella in CARTELLE_LISTE + CARTELLE_VECCHIE:
        try:
            nomi = sorted(os.listdir(cartella))
        except Exception:
            continue
        for n in nomi:
            f = os.path.join(cartella, n)
            if n.startswith(".") or n == "img" or not os.path.isdir(f):
                continue
            dentro = liste_in(f)
            if dentro:
                fuori.append((n, dentro))
    return fuori


# Il buffer di mpv: (chiave, nome, MiB avanti, MiB indietro, secondi avanti,
# secondi da riempire prima di ripartire). "standard" e' quello di sempre.
BUFFER = (("minimal", "Minimal", 48, 16, 10, 5),
          ("low", "Low", 128, 32, 20, 10),
          ("standard", "Standard", 512, 128, 30, 15),
          ("high", "High", 1024, 256, 60, 15))

CATEGORIE_URL = (("podcast", "Podcast"), ("musica", "Music"), ("radio", "Radio"),
                 ("cam", "IP cam"), ("film", "Film"), ("video", "Video"))

CACHE_LISTE = os.path.join(CASA, ".cache", "xvb", "liste")
LISTA_VECCHIA = 6 * 3600
_CACHE_PARSED_LOCK = threading.RLock()
_CACHE_PARSED_MEMORIA = {}


def copia_lista_url(url, aggiorna=True):
    """La copia in cache di una lista da url: si riscarica se manca o e'
    vecchia (o sempre, con aggiorna forzato); se la rete non va e c'e'
    una copia, si usa quella."""
    os.makedirs(CACHE_LISTE, exist_ok=True)
    f = os.path.join(CACHE_LISTE, hashlib.md5(url.encode()).hexdigest() + ".m3u")
    fresca = os.path.isfile(f) and time.time() - os.path.getmtime(f) < LISTA_VECCHIA
    if fresca and aggiorna != "forza":
        return f
    try:
        r = Request(url, headers={"User-Agent": UA})
        dati = urlopen(r, timeout=30).read()
        if b"#EXTINF" not in dati[:65536]:
            raise ValueError("not an m3u playlist")
        with open(f, "wb") as o:
            o.write(dati)
    except Exception:
        if not os.path.isfile(f):
            raise
    return f


def _percorso_lista(f, solo_cache=False):
    if re.match(r"https?://", f, re.I):
        percorso = os.path.join(CACHE_LISTE, hashlib.md5(f.encode()).hexdigest() + ".m3u")
        if not os.path.isfile(percorso):
            if solo_cache:
                return None
            percorso = copia_lista_url(f)
        return percorso
    return f if os.path.isfile(f) else None


def _cache_lista_memoria_valida(f):
    percorso = _percorso_lista(f, solo_cache=True)
    if not percorso:
        return None
    try:
        st = os.stat(percorso)
        with _CACHE_PARSED_LOCK:
            voce = _CACHE_PARSED_MEMORIA.get(f)
            if (voce
                    and voce[0] == os.path.abspath(percorso)
                    and voce[1] == st.st_mtime_ns
                    and voce[2] == st.st_size):
                return voce[3]
    except OSError:
        pass
    return None


def _cache_lista_valida(f):
    dati = _cache_lista_memoria_valida(f)
    if dati is not None:
        return dati

    percorso = _percorso_lista(f, solo_cache=True)
    if not percorso:
        return None
    chiave = hashlib.sha256(f.encode("utf-8", "replace")).hexdigest()
    archivio = os.path.join(CACHE_LISTE, "parsed", chiave + ".json")
    try:
        st = os.stat(percorso)
        with open(archivio, "r", encoding="utf-8") as o:
            dati = json.load(o)
        if (dati.get("versione") == 1
                and dati.get("percorso") == os.path.abspath(percorso)
                and dati.get("mtime_ns") == st.st_mtime_ns
                and dati.get("dimensione") == st.st_size):
            with _CACHE_PARSED_LOCK:
                _CACHE_PARSED_MEMORIA[f] = (
                    os.path.abspath(percorso),
                    st.st_mtime_ns,
                    st.st_size,
                    dati
                )
            return dati
    except (OSError, ValueError, TypeError):
        return None
    return None


def _dati_lista(f, solo_cache=False):
    percorso = _percorso_lista(f, solo_cache=solo_cache)
    if not percorso:
        return {"canali": [], "categorie": {}, "loghi": {}, "chiavi": {}}
    os.makedirs(os.path.join(CACHE_LISTE, "parsed"), exist_ok=True)
    chiave = hashlib.sha256(f.encode("utf-8", "replace")).hexdigest()
    archivio = os.path.join(CACHE_LISTE, "parsed", chiave + ".json")
    with _CACHE_PARSED_LOCK:
        dati = _cache_lista_valida(f)
        if dati is not None:
            return dati
        st = os.stat(percorso)
        canali, categorie, loghi, chiavi = [], {}, {}, {}
        nome = ide = logo = lic = None
        categoria = ""
        with open(percorso, encoding="utf-8", errors="ignore") as sorgente:
            for riga in sorgente:
                riga = riga.strip()
                if riga.startswith("#EXTINF"):
                    m = re.match(r'#EXTINF:[^,"]*(?:"[^"]*"[^,]*)*,(.*)$', riga)
                    nome = (m.group(1) if m else riga.split(",", 1)[-1]).strip() or _("unnamed")
                    m = re.search(r'tvg-id="([^"]*)"', riga)
                    ide = m.group(1).strip() if m else None
                    m = re.search(r'tvg-logo="([^"]+)"', riga)
                    logo = m.group(1).strip() if m else None
                    m = re.search(r'license-type="([^"]*)"', riga)
                    d = re.search(r'license-details="([^"]*)"', riga)
                    lic = d.group(1).strip() if m and d and m.group(1).strip().lower() == "clearkey" else None
                    m = re.search(r'group-title="([^"]*)"', riga, re.I)
                    categoria = m.group(1).strip() if m else ""
                elif riga and not riga.startswith("#"):
                    canali.append([nome or riga, riga, ide or None])
                    categorie[riga] = categoria
                    if logo:
                        loghi[riga] = logo
                    if lic:
                        chiavi[riga] = lic
                    nome = ide = logo = lic = None
                    categoria = ""
        dati = {"versione": 1, "percorso": os.path.abspath(percorso),
                "mtime_ns": st.st_mtime_ns, "dimensione": st.st_size,
                "canali": canali, "categorie": categorie,
                "loghi": loghi, "chiavi": chiavi}
        temporaneo = archivio + ".tmp"
        with open(temporaneo, "w", encoding="utf-8") as o:
            json.dump(dati, o, ensure_ascii=False, separators=(",", ":"))
        os.replace(temporaneo, archivio)
        _CACHE_PARSED_MEMORIA[f] = (
            os.path.abspath(percorso),
            st.st_mtime_ns,
            st.st_size,
            dati
        )
        return dati


def leggi_lista(f, solo_cache=False):
    dati = _dati_lista(f, solo_cache=solo_cache)
    LOGHI.update(dati.get("loghi", {}))
    CHIAVI.update(dati.get("chiavi", {}))
    return [tuple(c) for c in dati.get("canali", [])]


def _cat_vuota(d):
    return "MyPlaylist" if os.path.basename(os.path.dirname(d)) == PREFERITI else "Altri"


def nome_vista(d):
    if os.path.basename(os.path.dirname(d)) == PREFERITI:
        n = os.path.splitext(os.path.basename(d))[0]
        return {PREF_IPTV: "IPTV", PREF_MUSICA: _("Music"), PREF_VIDEO: _("Video")}.get(n, n)
    return nome_di(d)


def categorie_m3u(f, solo_cache=True):
    dati = _dati_lista(f, solo_cache=solo_cache)
    return {url: (categoria or _cat_vuota(f)) for url, categoria in dati.get("categorie", {}).items()}


LOGHI = {}
CHIAVI = {}


def chiavi_clearkey(det):
    """Le coppie (kid, chiave) di license-details: 'KID:CHIAVE', solo la
    chiave, anche tra virgolette, oppure il JSON {"KID":"CHIAVE",...}; anche
    se codificato come url. Le chiavi a zeri sono segnaposto e non contano."""
    from urllib.parse import unquote
    t = unquote((det or "").strip()).strip().strip('"').strip()
    coppie = []
    if t.startswith("{"):
        try:
            coppie = [(str(k).strip().lower(), str(v).strip().lower())
                      for k, v in json.loads(t).items()]
        except (ValueError, AttributeError):
            coppie = []
    else:
        m = re.fullmatch(r"(?:([0-9a-fA-F]{32}):)?([0-9a-fA-F]{32})", t)
        if m:
            coppie = [((m.group(1) or "").lower(), m.group(2).lower())]
    return [(k, v) for k, v in coppie
            if re.fullmatch(r"[0-9a-f]{32}", v) and int(v, 16) != 0]


def chiave_clearkey(det):
    """La prima chiave vera (32 cifre esadecimali) di license-details; ffmpeg
    ne accetta una sola per flusso. None se non si legge."""
    c = chiavi_clearkey(det)
    return c[0][1] if c else None
def manifest_pulito(testo, url):
    """Il manifest DASH senza le tracce dei sottotitoli (ffmpeg le riapre
    all'infinito e il canale non parte) e coi percorsi resi assoluti."""
    testo = re.sub(r'\s*<AdaptationSet\b[^>]*mimeType="(?:text/|application/ttml)[^"]*"[^>]*>.*?</AdaptationSet>',
                   "", testo, flags=re.S)
    if "<BaseURL" not in testo:
        from xml.sax.saxutils import escape
        base = escape(url.rsplit("/", 1)[0] + "/")
        testo = testo.replace("<Period", "<BaseURL>%s</BaseURL>\n<Period" % base, 1)
    return testo


_SERVER_MPD = None


def server_mpd():
    """Un piccolo server solo su 127.0.0.1: a ogni richiesta riscarica il
    manifest del canale (e' un live, mpv lo richiede ogni pochi secondi) e
    lo da' ripulito. Serve solo i canali che hanno una ClearKey."""
    global _SERVER_MPD
    if _SERVER_MPD is not None:
        return _SERVER_MPD
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlsplit

    class Gestore(BaseHTTPRequestHandler):
        def do_GET(self):
            try:
                url = parse_qs(urlsplit(self.path).query)["u"][0]
                if url not in CHIAVI:
                    raise ValueError("canale senza chiave")
                testo = urlopen(Request(url, headers={"User-Agent": UA}),
                                timeout=10).read().decode("utf-8", "ignore")
                dati = manifest_pulito(testo, url).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/dash+xml")
                self.send_header("Content-Length", str(len(dati)))
                self.end_headers()
                self.wfile.write(dati)
            except Exception:
                self.send_error(502)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Gestore)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _SERVER_MPD = srv
    return srv


CACHE_LOGHI = os.path.join(CASA, ".cache", "xvb", "loghi")

COPERTINE_URL = os.path.join(CASA, ".config", "xvb", "copertine")
IMMAGINI = ("jpg", "jpeg", "png", "webp", "bmp", "svg")


def copertina_url(u):
    return os.path.join(COPERTINE_URL, hashlib.md5(u.encode("utf-8")).hexdigest() + ".jpg")
FILE_COLORI = os.path.join(CASA, ".cache", "xvb", "colori.json")
FILE_TAG = os.path.join(CASA, ".cache", "xvb", "tag.json")
RIGA_ELENCO = 64
LOGO_L, LOGO_A = 72, 56
COLONNA = LOGO_L
RAGGIO = 10  # raggio degli angoli delle card nell'elenco
CACHE_ANTEPRIME = os.path.join(CASA, ".cache", "xvb", "anteprime")
CACHE_VINILI = os.path.join(CASA, ".cache", "xvb", "vinili")
CACHE_ARTISTI = os.path.join(CASA, ".cache", "xvb", "artisti")
CACHE_ARTISTI_INDEX = os.path.join(CACHE_ARTISTI, "indice.json")


def copertina_dentro(f):
    """La copertina incorporata in un brano (mp3, flac, m4a, ogg...), come
    bytes; None se non c'e'."""
    if not HA_TAG:
        return None
    try:
        t = mutagen.File(f)
        if t is None:
            return None
        if getattr(t, "pictures", None):
            return t.pictures[0].data
        if t.tags:
            for k in t.tags.keys():
                if str(k).startswith("APIC"):
                    return t.tags[k].data
            if "covr" in t.tags:
                return bytes(t.tags["covr"][0])
            if "metadata_block_picture" in t.tags:
                import base64
                from mutagen.flac import Picture
                return Picture(base64.b64decode(t.tags["metadata_block_picture"][0])).data
    except Exception:
        pass
    return None


TMDB = "https://api.themoviedb.org/3"

TMDB_CHIAVE = ("eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiIxMzIxMjRhMzU0YjRmYzE4NGVmYjgwODk5NzZmMWVmMiIsIm5iZiI6"
               "MTc5MDc3MTY0OS4zODYsInN1YiI6IjZhYmQwMWMxOTkyNzRmMGQxOTFlN2JjYiIsInNjb3BlcyI6WyJhcGlf"
               "cmVhZCJdLCJ2ZXJzaW9uIjoxfQ.AYShiHv1HdbN0Wj1l0co9D6CIFcOy5NAM5WkMZyQQ0M")
TMDB_IMG = "https://image.tmdb.org/t/p/w342"
FILE_PROVATI = os.path.join(CASA, ".cache", "xvb", "info_provate.json")
FILE_INFO = os.path.join(CASA, ".cache", "xvb", "info.json")
LINGUE_TMDB = {"en": "en-US", "it": "it-IT", "es": "es-ES", "fr": "fr-FR",
               "de": "de-DE", "pt": "pt-BR", "ru": "ru-RU"}


def leggi_json(f):
    try:
        with open(f, encoding="utf-8") as h:
            return json.load(h)
    except Exception:
        return {}


def scrivi_json(f, d):
    try:
        os.makedirs(os.path.dirname(f), exist_ok=True)
        with open(f, "w", encoding="utf-8") as h:
            json.dump(d, h)
    except Exception:
        pass
UA_XVB = "XVB/%s ( https://github.com/JonaDev2026/xvb )"


def titolo_anno(f):
    """Titolo e anno di un film dal nome del file (o della cartella, se
    il file ha un nome da rip): 'Neon Harbour (2023)', 'Neon.Harbour.2023.1080p'."""
    for testo in (os.path.splitext(os.path.basename(f))[0],
                  os.path.basename(os.path.dirname(f))):
        m = re.match(r"^(.*?)[\s._\-\(\[]+((?:19|20)\d{2})(?:\D|$)", testo)
        if m and m.group(1).strip():
            return re.sub(r"[._]+", " ", m.group(1)).strip(), m.group(2)
    t = re.sub(r"[._]+", " ", os.path.splitext(os.path.basename(f))[0]).strip()
    return t, None


def json_da(url, intestazioni=None):
    r = Request(url, headers=dict({"User-Agent": UA_XVB % VERSIONE,
                                   "Accept": "application/json"}, **(intestazioni or {})))
    return json.loads(urlopen(r, timeout=15).read().decode("utf-8"))


def scarica_in(url, dove):
    r = Request(url, headers={"User-Agent": UA_XVB % VERSIONE})
    dati = urlopen(r, timeout=20).read()
    if len(dati) < 500:
        raise ValueError("empty image")
    with open(dove, "wb") as o:
        o.write(dati)


def _tmdb(chiave, strada, q):
    from urllib.parse import urlencode
    if len(chiave) > 40:
        h = {"Authorization": "Bearer " + chiave}
    else:
        h, q = None, dict(q, api_key=chiave)
    return json_da("%s/%s?%s" % (TMDB, strada, urlencode(q)), h)


def generi_tmdb(chiave, lingua, cache):
    """I nomi dei generi di TMDB nella lingua dell'app (id -> nome), in
    cache: una chiamata sola per lingua."""
    k = "_generi_" + lingua
    if k not in cache:
        nomi = {}
        for tipo in ("movie", "tv"):
            for g in _tmdb(chiave, "genre/%s/list" % tipo, {"language": lingua}).get("genres") or []:
                nomi[str(g["id"])] = g["name"]
        cache[k] = nomi
    return cache[k]


def info_tmdb(chiave, titolo, anno, lingua):
    """Titolo, anno, generi (id), voto e poster di un film (o di una
    serie) su TMDB, o None se non lo trova. La chiave puo' essere quella
    corta (v3) o il token lungo (v4)."""
    for tipo, campo, nome, data in (("movie", "year", "title", "release_date"),
                                    ("tv", "first_air_date_year", "name", "first_air_date")):
        q = {"query": titolo, "include_adult": "false", "language": lingua}
        if anno:
            q[campo] = anno
        ris = _tmdb(chiave, "search/" + tipo, q).get("results") or []
        if ris:
            r = next((x for x in ris if x.get("poster_path")), ris[0])
            return {"titolo": r.get(nome) or "", "anno": (r.get(data) or "")[:4],
                    "generi": [str(g) for g in r.get("genre_ids") or []],
                    "voto": r.get("vote_average") or 0, "voti": r.get("vote_count") or 0,
                    "poster": TMDB_IMG + r["poster_path"] if r.get("poster_path") else ""}
    return None


def _anno_via(t):
    """'Terremoto (1993)', '[1993] Terremoto', '1993 - Terremoto' ->
    ('Terremoto', '1993')."""
    anno = ""
    m = re.search(r"[\(\[]\s*((?:19|20)\d\d)\s*[\)\]]", t)
    if m:
        anno, t = m.group(1), (t[:m.start()] + t[m.end():])
    else:
        m = re.match(r"\s*((?:19|20)\d\d)\s*[-.\u2013]\s*(.+)$", t) or \
            re.match(r"(.+?)\s*[-\u2013]\s*((?:19|20)\d\d)\s*$", t)
        if m:
            a, b = m.groups()
            anno, t = (a, b) if a.isdigit() else (b, a)
    return re.sub(r"\s+", " ", t.replace("_", " ")).strip(" -\u2013."), anno


def dal_percorso(f, radici=()):
    """Quando un brano non ha tag: titolo, artista, album e anno ricavati
    da cartelle e nome del file. Capisce:
      .../Artista/Album/01 - Titolo.mp3   (anche 'Album (1993)', '1993 - Album')
      .../Artista - Album/01. Titolo.mp3
      .../qualcosa/Artista - Titolo.mp3
    Le cartelle aggiunte (radici) non contano come artista o album."""
    cartella, nome = os.path.split(f)
    radice = os.path.splitext(nome)[0].replace("_", " ")
    radici = [os.path.normpath(r) for r in radici]
    titolo = re.sub(r"^\s*\d{1,3}\s*[-.\s]\s*", "", radice).strip()
    artista = album = anno = ""
    if " - " in titolo:
        a, t = titolo.split(" - ", 1)
        artista, titolo = a.strip(), t.strip()
    padre = os.path.basename(cartella)
    nonno_path = os.path.dirname(cartella)
    nonno = os.path.basename(nonno_path)
    if os.path.normpath(cartella) not in radici and padre:
        if " - " in padre and not re.match(r"\s*(19|20)\d\d\s*-", padre):
            a, b = padre.split(" - ", 1)
            artista = artista or a.strip()
            album, anno = _anno_via(b)
        else:
            album, anno = _anno_via(padre)
            if nonno and os.path.normpath(nonno_path) not in radici:
                if not artista:
                    artista = nonno.replace("_", " ").strip()
            elif not anno and not artista:


                artista, album = album, ""
    if artista and album.lower() == artista.lower():
        album = ""
    return titolo or radice, artista, album, anno


def copertina_musicbrainz(artista, album):
    """L'url della copertina di un album (MusicBrainz + Cover Art
    Archive) e il suo anno: (url, anno), o (None, "")."""
    from urllib.parse import quote
    q = 'releasegroup:"%s" AND artist:"%s"' % (album.replace('"', ""), artista.replace('"', ""))
    ris = json_da("https://musicbrainz.org/ws/2/release-group/?fmt=json&limit=3&query=" + quote(q))
    for g in ris.get("release-groups") or []:
        if g.get("id"):
            return ("https://coverartarchive.org/release-group/%s/front-500" % g["id"],
                    (g.get("first-release-date") or "")[:4])
    return None, ""


def copertina_da_brano(artista, titolo):
    """Senza album: si cerca il brano (artista + titolo) su MusicBrainz e si
    prende l'album ufficiale piu' vecchio in cui e' uscito, scartando
    raccolte e live. ([url copertine, dalla migliore], anno, nome album):
    piu' d'una perche' non tutti gli album hanno la copertina caricata."""
    from urllib.parse import quote
    q = 'recording:"%s" AND artist:"%s"' % (titolo.replace('"', ""), artista.replace('"', ""))
    ris = json_da("https://musicbrainz.org/ws/2/recording/?fmt=json&limit=15&query=" + quote(q))
    scelte = []
    for r in ris.get("recordings") or []:
        for rel in r.get("releases") or []:
            g = rel.get("release-group") or {}
            if not g.get("id"):
                continue
            buono = (rel.get("status", "Official") == "Official"
                     and g.get("primary-type") in ("Album", "EP", "Single")
                     and not g.get("secondary-types"))
            data = rel.get("date") or "9999"

            scelte.append((not buono, g.get("primary-type") != "Album", data, g["id"],
                           g.get("title") or rel.get("title") or ""))
    if not scelte:
        return [], "", ""
    scelte.sort()
    _n, _a, data, _g, nome = scelte[0]
    urls = []
    for c in scelte:
        u = "https://coverartarchive.org/release-group/%s/front-500" % c[3]
        if u not in urls:
            urls.append(u)
    return urls[:3], (data[:4] if data[:4].isdigit() and data != "9999" else ""), nome


def poster_film(f):
    """L'immagine di un film accanto al file (stesso nome, poster, folder,
    cover), originale e non ridotta; None se non c'e'."""
    cartella, base = os.path.split(f)
    radice = os.path.splitext(base)[0]
    nomi = ([radice + e for e in (".jpg", ".jpeg", ".png", ".JPG", ".JPEG", ".PNG")] +
            [n + e for n in ("poster", "folder", "cover") for e in (".jpg", ".jpeg", ".png")])
    return next((os.path.join(cartella, n) for n in nomi
                 if os.path.isfile(os.path.join(cartella, n))), None)


def anteprima_di(f):
    """L'immagine per un file nell'elenco, salvata in cache: per la musica
    la copertina (dentro al file, o cover/folder.jpg nella cartella); per
    i video un'immagine con lo stesso nome o poster/folder.jpg, se no un
    fotogramma preso con ffmpeg. Torna il percorso del file in cache, o
    None."""
    cartella, base = os.path.split(f)
    radice = os.path.splitext(base)[0]
    audio = os.path.splitext(f)[1][1:].lower() in AUDIO


    nomi = ([radice + e for e in (".jpg", ".jpeg", ".png", ".JPG", ".JPEG", ".PNG")] +
            [n + e for n in ("poster", "folder", "cover") for e in (".jpg", ".jpeg", ".png")]
            if not audio else
            [n + e for n in ("cover", "folder") for e in (".jpg", ".jpeg", ".png")] +
            [radice + e for e in (".jpg", ".jpeg", ".png")])
    immagine = next((os.path.join(cartella, n) for n in nomi
                     if os.path.isfile(os.path.join(cartella, n))), None)
    sorgente = immagine or f
    try:
        m = os.path.getmtime(sorgente)
    except OSError:
        return None
    os.makedirs(CACHE_ANTEPRIME, exist_ok=True)
    fuori = os.path.join(CACHE_ANTEPRIME,
                         hashlib.md5(("%s|%s" % (sorgente, m)).encode()).hexdigest() + ".jpg")
    if os.path.isfile(fuori):
        return fuori if os.path.getsize(fuori) else None
    dati = None
    if immagine:
        try:
            with open(immagine, "rb") as h:
                dati = h.read()
        except Exception:
            dati = None
    elif audio:
        dati = copertina_dentro(f)
    try:
        if dati is not None and HA_PIL:
            import io
            im = Image.open(io.BytesIO(dati)).convert("RGB")
            im.thumbnail((LOGO_L * 4, LOGO_A * 4))
            im.save(fuori, "JPEG", quality=85)
            return fuori
        if not audio:

            for ss in ("30", "1"):
                r = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-ss", ss,
                                    "-i", f, "-frames:v", "1", "-vf", "scale=%d:-2" % (LOGO_L * 4),
                                    fuori], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   timeout=20)
                if r.returncode == 0 and os.path.isfile(fuori) and os.path.getsize(fuori):
                    return fuori
    except Exception:
        pass
    try:
        open(fuori, "wb").close()
    except Exception:
        pass
    return None
CARATTERE = None
STILE_CARATTERE = "normal"
NOME_PX = 17
SOTTO_PX = 15

def font_desktop_sistema():
    """Restituisce famiglia, stile e dimensione del font desktop Linux."""
    import shlex
    comandi = (
        ["gsettings", "get", "org.gnome.desktop.interface", "font-name"],
        ["gsettings", "get", "org.cinnamon.desktop.interface", "font-name"],
        ["xfconf-query", "-c", "xsettings", "-p", "/Gtk/FontName"],
    )
    for cmd in comandi:
        try:
            valore = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL,
                                             timeout=2).strip()
            parti = shlex.split(valore)
            if not parti:
                continue


            if parti and re.fullmatch(r"\d+(?:\.\d+)?", parti[-1]):
                parti.pop()
            stili = []
            while parti and parti[-1].lower() in ("italic", "oblique", "bold", "regular", "book", "light", "medium"):
                stili.insert(0, parti.pop().lower())
            if parti:
                famiglia = " ".join(parti).strip()
                stile = "italic" if any(x in ("italic", "oblique") for x in stili) else "normal"
                peso = "bold" if "bold" in stili else "normal"
                return famiglia, stile, peso
        except Exception:
            continue

    try:
        from pathlib import Path
        import configparser
        cfg = configparser.ConfigParser()
        cfg.read(str(Path.home() / ".config" / "kdeglobals"), encoding="utf-8")
        parti = cfg.get("General", "font", fallback="").split(",")
        if parti and parti[0].strip():
            stile_txt = parti[1].lower() if len(parti) > 1 else ""
            return parti[0].strip(), ("italic" if "italic" in stile_txt or "oblique" in stile_txt else "normal"), ("bold" if "bold" in stile_txt else "normal")
    except Exception:
        pass
    return None, "normal", "normal"

def imposta_font_globale(root):
    """Imposta Google Sans incorporato nell'app su tutti i font Tk."""
    global CARATTERE, STILE_CARATTERE
    CARATTERE = "Google Sans"
    STILE_CARATTERE = "normal"
    for nome in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont",
                 "TkCaptionFont", "TkSmallCaptionFont", "TkIconFont", "TkTooltipFont"):
        try:
            tkfont.nametofont(nome, root=root).configure(
                family=CARATTERE, slant="roman", weight="normal")
        except tk.TclError:
            pass

def percorso_google_sans(peso="Regular", corsivo=False):
    """Percorso del file Google Sans statico incluso in fonts/static."""
    suffisso = "Italic" if corsivo else ""
    nome = "GoogleSans-" + peso + suffisso + ".ttf"
    percorso = os.path.join(_FONT_STATIC_DIR, nome)
    if os.path.isfile(percorso):
        return percorso

    nome = "GoogleSans_17pt-" + peso + suffisso + ".ttf"
    percorso = os.path.join(_FONT_STATIC_DIR, nome)
    return percorso if os.path.isfile(percorso) else None

def leggi_tag():
    try:
        with open(FILE_TAG, encoding="utf-8") as h:
            return json.load(h)
    except Exception:
        return {}


def scrivi_tag(d):
    try:
        os.makedirs(os.path.dirname(FILE_TAG), exist_ok=True)
        with open(FILE_TAG, "w", encoding="utf-8") as h:
            json.dump(d, h)
    except Exception:
        pass


def tag_di(f, cache):
    """(titolo, artista, album, anno) dai tag del file (mutagen), vuoti se
    non ci sono. In cache per percorso e data di modifica, cosi' una
    cartella grossa si legge una volta sola."""
    vuoto = ("", "", "", "")
    if not HA_TAG:
        return vuoto
    try:
        m = os.path.getmtime(f)
    except OSError:
        return vuoto
    v = cache.get(f)
    if v and v[0] == m and len(v) == 5:
        return tuple(v[1:])
    titolo, artista, album, anno = vuoto
    try:
        t = mutagen.File(f, easy=True)
        if t and t.tags:
            prendi = lambda k: str((t.tags.get(k) or [""])[0]).strip()
            titolo, artista, album = prendi("title"), prendi("artist"), prendi("album")
            anno = prendi("date")[:4] if prendi("date")[:4].isdigit() else ""
    except Exception:
        pass
    cache[f] = [m, titolo, artista, album, anno]
    return titolo, artista, album, anno


def svg_in_png(dati):
    """Un logo SVG diventa PNG: con rsvg-convert, o con cairosvg se manca.
    Se non si puo' i dati restano com'erano."""
    if b"<svg" not in dati[:4096].lower():
        return dati
    try:
        r = subprocess.run(["rsvg-convert", "-w", "256"], input=dati,
                           capture_output=True, timeout=15)
        if r.returncode == 0 and r.stdout:
            return r.stdout
    except Exception:
        pass
    try:
        import cairosvg
        return cairosvg.svg2png(bytestring=dati, output_width=256)
    except Exception:
        return dati


def schiarisci(colore, minimo=0.80):
    """Lo stesso colore piu' chiaro, per i testi sullo scuro: stessa tinta,
    luce almeno `minimo`. Se non e' un #rrggbb lo lascia com'e'."""
    import colorsys
    try:
        r, g, b = (int(colore[i:i + 2], 16) / 255.0 for i in (1, 3, 5))
    except (ValueError, TypeError, IndexError):
        return colore
    h, l, sa = colorsys.rgb_to_hls(r, g, b)
    r, g, b = colorsys.hls_to_rgb(h, max(l, minimo), sa)
    return "#%02x%02x%02x" % (int(round(r * 255)), int(round(g * 255)), int(round(b * 255)))


def colore_dal_logo(url_logo):
    """Il colore dominante del logo di un canale, da usare per il pallino:
    si scarica il logo (in cache), si buttano i pixel trasparenti, bianchi,
    neri e grigi, si prende la tinta piu' presente e la si porta a una
    luminosita' che si veda sullo scuro. None se non si riesce."""
    if not HA_PIL:
        return None
    import colorsys
    os.makedirs(CACHE_LOGHI, exist_ok=True)
    f = os.path.join(CACHE_LOGHI, hashlib.md5(url_logo.encode()).hexdigest())
    if not os.path.isfile(f) or os.path.getsize(f) == 0:
        r = Request(url_logo, headers={"User-Agent": UA})
        dati = svg_in_png(urlopen(r, timeout=10).read())
        with open(f, "wb") as o:
            o.write(dati)
    try:
        im = Image.open(f).convert("RGBA")
    except Exception:
        os.remove(f)
        raise
    im.thumbnail((48, 48))
    secchi = {}
    for r, g, b, a in im.getdata():
        if a < 128:
            continue
        h, l, sa = colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)
        if sa < 0.35 or l < 0.12 or l > 0.92:
            continue
        k = int(h * 12) % 12
        tot = secchi.setdefault(k, [0, 0.0, 0.0, 0.0])
        tot[0] += 1
        tot[1] += h
        tot[2] += l
        tot[3] += sa
    if not secchi:
        return None
    n, h, l, sa = max(secchi.values(), key=lambda t: t[0])
    h, l, sa = h / n, l / n, sa / n


    l = min(0.72, max(0.55, l))
    sa = max(0.75, sa)
    if 0.55 <= h <= 0.75:
        l = max(l, 0.63)
    r, g, b = colorsys.hls_to_rgb(h, l, sa)
    return "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))


COLORI_VERSIONE = 4


def leggi_colori():
    try:
        with open(FILE_COLORI, encoding="utf-8") as h:
            d = json.load(h)
        if d.get("_v") != COLORI_VERSIONE:
            return {}
        d.pop("_v", None)
        return d
    except Exception:
        return {}


def scrivi_colori(d):
    try:
        os.makedirs(os.path.dirname(FILE_COLORI), exist_ok=True)
        with open(FILE_COLORI, "w", encoding="utf-8") as h:
            json.dump(dict(d, _v=COLORI_VERSIONE), h)
    except Exception:
        pass


def immagine_lista(dove):
    """L'immagine accanto al nome: per le playlist IPTV usa icone/m3u.png;
    per il resto (guide e registrazioni) usa icone/default.png.
    L'icona principale IPTV (tv.png) viene gestita separatamente.
    Torna il percorso, o None."""
    nomi = (["m3u"] if dove else []) + ["default"]
    for n in nomi:
        for est in (".png", ".jpg", ".jpeg", ".gif"):
            f = os.path.join(QUI, "icone", n + est)
            if os.path.isfile(f):
                return f
    return None


def carica_logo(f, larga, alta, dentro=None, margine=0, quadrata=False):
    """Un'immagine come immagine Tk, dentro un riquadro fisso, centrata.
    Con `dentro` (px) l'immagine e' piu' piccola del riquadro, che resta
    quello (cosi' resta allineata alle altre). None se non si legge."""
    try:
        if HA_PIL:
            im = Image.open(f).convert("RGBA")
            if quadrata and im.height > im.width:


                lato = im.width
                y0 = int((im.height - lato) * 0.3)
                im = im.crop((0, y0, lato, y0 + lato)).resize((alta, alta), Image.LANCZOS)
                box = Image.new("RGBA", (larga, alta), (0, 0, 0, 0))
                box.paste(im, ((larga - alta) // 2, 0))
                return ImageTk.PhotoImage(box)

            k = min((dentro or larga - 2 * margine) / float(max(1, im.width)),
                    (dentro or alta - 2 * margine) / float(max(1, im.height)))
            im = im.resize((max(1, int(im.width * k)),
                            max(1, int(im.height * k))), Image.LANCZOS)
            box = Image.new("RGBA", (larga, alta), (0, 0, 0, 0))
            box.paste(im, ((larga - im.width) // 2, (alta - im.height) // 2))
            return ImageTk.PhotoImage(box)
        im = tk.PhotoImage(file=f)
        k = max(1, int(round(max(im.height() / float(alta),
                                 im.width() / float(larga)))))
        return im.subsample(k, k) if k > 1 else im
    except Exception:
        return None


_MASCHERE_DISCO = {}
_ANELLI_DISCO = {}


def disco_base(f, lato):
    """La copertina di un brano, quadrata (ritagliata al centro), pronta per
    farne un vinile."""
    im = Image.open(f).convert("RGBA")
    l = min(im.size)
    x0, y0 = (im.width - l) // 2, (im.height - l) // 2
    return im.crop((x0, y0, x0 + l, y0 + l)).resize((lato, lato), Image.LANCZOS)


def disco_frame(base, larga, alta, angolo=0.0, colore=None):
    """Il vinile: copertina tonda col foro al centro e, intorno al foro, la
    striscia del colore della voce; girata in senso orario dell'angolo dato,
    in un riquadro fisso larga x alta."""
    from PIL import ImageDraw
    K = base.width
    if K not in _MASCHERE_DISCO:
        m = Image.new("L", (K, K), 0)
        d = ImageDraw.Draw(m)
        d.ellipse([0, 0, K - 1, K - 1], fill=255)
        r = K * 0.075
        d.ellipse([K / 2.0 - r, K / 2.0 - r, K / 2.0 + r, K / 2.0 + r], fill=0)
        _MASCHERE_DISCO[K] = m
    im = base.rotate(-angolo, resample=Image.BICUBIC) if angolo else base.copy()
    im.putalpha(_MASCHERE_DISCO[K])
    if colore and (K, colore) not in _ANELLI_DISCO:
        try:
            ov = Image.new("RGBA", (K, K), (0, 0, 0, 0))
            d = ImageDraw.Draw(ov)
            R, r = K * 0.19, K * 0.075
            d.ellipse([K / 2.0 - R, K / 2.0 - R, K / 2.0 + R, K / 2.0 + R], fill=colore)
            d.ellipse([K / 2.0 - r, K / 2.0 - r, K / 2.0 + r, K / 2.0 + r], fill=(0, 0, 0, 0))
        except Exception:
            ov = None
        _ANELLI_DISCO[(K, colore)] = ov
    if colore and _ANELLI_DISCO.get((K, colore)) is not None:
        im.alpha_composite(_ANELLI_DISCO[(K, colore)])
    im = im.resize((alta, alta), Image.LANCZOS)
    box = Image.new("RGBA", (larga, alta), (0, 0, 0, 0))
    box.alpha_composite(im, ((larga - alta) // 2, 0))
    return box


def disco_vinile(f, larga, alta, colore=None):
    """Carica il vinile dalla cache su disco o lo genera una sola volta."""
    try:
        stat = os.stat(f)
        chiave = "%s|%s|%s|%s|%s|%s" % (
            os.path.abspath(f), stat.st_mtime_ns, stat.st_size,
            colore or "", larga, alta
        )
        nome = hashlib.md5(chiave.encode("utf-8")).hexdigest() + ".png"
        percorso = os.path.join(CACHE_VINILI, nome)

        if not os.path.isfile(percorso) or os.path.getsize(percorso) == 0:
            os.makedirs(CACHE_VINILI, exist_ok=True)
            immagine = disco_frame(
                disco_base(f, alta * 3), larga, alta, 0.0, colore
            )
            immagine.save(percorso, "PNG")

        return ImageTk.PhotoImage(Image.open(percorso).convert("RGBA"))
    except Exception:
        return None


def pallino(colore, lato=PUNTO):
    """Un pallino colorato, stile etichette del Mac, in un riquadro
    quadrato trasparente."""
    r = lato * 0.22
    cx = cy = lato / 2.0
    if HA_PIL:
        K = 4
        im = Image.new("RGBA", (lato * K, lato * K), (0, 0, 0, 0))
        from PIL import ImageDraw
        ImageDraw.Draw(im).ellipse(
            [(cx - r) * K, (cy - r) * K, (cx + r) * K, (cy + r) * K],
            fill=colore)
        return ImageTk.PhotoImage(im.resize((lato, lato), Image.LANCZOS))
    im = tk.PhotoImage(width=lato, height=lato)
    for y in range(lato):
        dy = y + 0.5 - cy
        mezza = (r * r - dy * dy)
        if mezza <= 0:
            continue
        mezza = mezza ** 0.5
        x0, x1 = int(round(cx - mezza)), int(round(cx + mezza))
        if x1 > x0:
            im.put(colore, to=(x0, y, x1, y + 1))
    return im


def qualita(alta, banda):
    """Come chiamare una qualita' e di che colore scriverla: dall'altezza
    in pixel se c'e', se no dalla banda."""
    if alta >= 2160:
        return "2160p", "#bf5af2"
    if alta >= 1080:
        return "1080p", "#30d158"
    if alta >= 720:
        return "720p", "#0a84ff"
    if alta >= 480:
        return "%dp" % alta, "#ff9f0a"
    if alta > 0:
        return "%dp" % alta, "#ff5257"
    return "%.0f kbit/s" % (banda / 1000.0), GRIGIO


def nel_riquadro(im, lato, dentro):
    """Un'immagine PIL ridotta a `dentro` px e messa al centro di un
    riquadro trasparente da `lato` (senza dentro: grande quanto lui)."""
    d = dentro or lato
    im = im.resize((d, d), Image.LANCZOS)
    if d == lato:
        return ImageTk.PhotoImage(im)
    box = Image.new("RGBA", (lato, lato), (0, 0, 0, 0))
    box.paste(im, ((lato - d) // 2, (lato - d) // 2))
    return ImageTk.PhotoImage(box)


def cartellina(colore, aperta=False, lato=PUNTO, dentro=None):
    """L'icona della cartella per le categorie: solo contorno, del colore
    che le tocca, chiusa o aperta (con la falda davanti inclinata)."""
    if not HA_PIL:
        return None
    from PIL import ImageDraw
    K = 4
    im = Image.new("RGBA", (lato * K, lato * K), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    u = lato * K / 24.0
    sp = int(1.7 * u)

    d.line([(3 * u, 19 * u), (3 * u, 5 * u), (10 * u, 5 * u), (12 * u, 7.5 * u),
            (21 * u, 7.5 * u), (21 * u, 19 * u), (3 * u, 19 * u)],
           fill=colore, width=sp, joint="curve")
    if aperta:

        d.line([(3 * u, 19 * u), (6 * u, 11.5 * u), (23 * u, 11.5 * u),
                (20 * u, 19 * u)], fill=colore, width=sp, joint="curve")
    return nel_riquadro(im, lato, dentro)


def segnalibro(colore, lato=PUNTO, dentro=None):
    """L'icona delle playlist dei preferiti (bookmark.svg), solo contorno,
    del colore che le tocca."""
    if not HA_PIL:
        return None
    from PIL import ImageDraw
    K = 4
    im = Image.new("RGBA", (lato * K, lato * K), (0, 0, 0, 0))
    s = lato * K * 0.76 / 514.0

    def P(x, y):
        return ((x - 320) * s + lato * K / 2.0, (y - 318) * s + lato * K / 2.0)

    def curva(a, b, c, e):
        fuori = []
        for i in range(1, 11):
            t = i / 10.0
            m = 1 - t
            fuori.append(P(m ** 3 * a[0] + 3 * m * m * t * b[0] + 3 * m * t * t * c[0] + t ** 3 * e[0],
                           m ** 3 * a[1] + 3 * m * m * t * b[1] + 3 * m * t * t * c[1] + t ** 3 * e[1]))
        return fuori
    pti = [P(192, 64)]
    pti += curva((192, 64), (156.7, 64), (128, 92.7), (128, 128))
    pti += [P(128, 544)]
    pti += curva((128, 544), (128, 555.5), (134.2, 566.2), (144.2, 571.8))
    pti += curva((144.2, 571.8), (154.2, 577.4), (166.5, 577.3), (176.4, 571.4))
    pti += [P(320, 485.3), P(463.5, 571.4)]
    pti += curva((463.5, 571.4), (473.4, 577.3), (485.7, 577.5), (495.7, 571.8))
    pti += curva((495.7, 571.8), (505.7, 566.1), (512, 555.5), (512, 544))
    pti += [P(512, 128)]
    pti += curva((512, 128), (512, 92.7), (483.3, 64), (448, 64))
    ImageDraw.Draw(im).line(pti + pti[:2], fill=colore, width=int(1.7 * lato * K / 24.0),
                            joint="curve")
    return nel_riquadro(im, lato, dentro)


def globo(colore, lato=PUNTO, dentro=None):
    """L'icona del globo per le liste da url: solo contorno, come le
    cartelle, del colore che le tocca."""
    if not HA_PIL:
        return None
    from PIL import ImageDraw
    K = 4
    im = Image.new("RGBA", (lato * K, lato * K), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    u = lato * K / 24.0
    sp = int(1.7 * u)
    d.ellipse((3 * u, 3 * u, 21 * u, 21 * u), outline=colore, width=sp)
    d.ellipse((8 * u, 3 * u, 16 * u, 21 * u), outline=colore, width=sp)
    d.line([(3 * u, 12 * u), (21 * u, 12 * u)], fill=colore, width=sp)
    d.line([(5 * u, 7.5 * u), (19 * u, 7.5 * u)], fill=colore, width=sp)
    d.line([(5 * u, 16.5 * u), (19 * u, 16.5 * u)], fill=colore, width=sp)
    return nel_riquadro(im, lato, dentro)


def disco(colore, lato=PUNTO, alta=None, dentro=None):
    """L'icona del disco (CD, DVD, ISO montata): icone/disco.png se c'e',
    se no disegnata, solo contorno, come globi e cartelle."""
    if not HA_PIL:
        return None
    f = os.path.join(QUI, "icone", "disco.png")
    if os.path.isfile(f):
        im = carica_logo(f, lato, alta or lato, dentro)
        if im is not None:
            return im
    from PIL import ImageDraw
    K = 4
    im = Image.new("RGBA", (lato * K, lato * K), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    u = lato * K / 24.0
    sp = int(1.7 * u)
    d.ellipse((3 * u, 3 * u, 21 * u, 21 * u), outline=colore, width=sp)
    d.ellipse((9.5 * u, 9.5 * u, 14.5 * u, 14.5 * u), outline=colore, width=sp)
    d.arc((6 * u, 6 * u, 18 * u, 18 * u), 200, 250, fill=colore, width=int(1.2 * u))
    return nel_riquadro(im, lato, dentro)


def vinile(colore, lato=PUNTO, dentro=None):
    """L'icona di un album: un vinile nero col foro e la fascia centrale del
    colore dato."""
    if not HA_PIL:
        return None
    from PIL import ImageDraw
    K = 4
    im = Image.new("RGBA", (lato * K, lato * K), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    u = lato * K / 24.0
    c = 12 * u

    def cerchio(r, **kw):
        d.ellipse((c - r * u, c - r * u, c + r * u, c + r * u), **kw)
    cerchio(11.4, fill=(24, 24, 28, 255), outline="#6a6a74", width=max(1, int(1.2 * u)))
    cerchio(9.2, outline=(72, 72, 80, 255), width=max(1, int(0.5 * u)))
    cerchio(7.2, outline=(72, 72, 80, 255), width=max(1, int(0.5 * u)))
    cerchio(5.2, fill=colore)
    cerchio(1.9, fill=(0, 0, 0, 0))
    return nel_riquadro(im, lato, dentro)


def dischi():
    """I dischi montati: CD e DVD nel lettore, e le immagini ISO montate
    (sono tutti iso9660 o udf). [(punto di montaggio, nome)]."""
    fuori = []
    try:
        with open("/proc/mounts", encoding="utf-8", errors="ignore") as h:
            for riga in h:
                p = riga.split()
                if len(p) >= 3 and p[2] in ("iso9660", "udf"):
                    mp = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), p[1])
                    if os.path.isdir(mp):
                        fuori.append((mp, os.path.basename(mp.rstrip(os.sep)) or mp))
    except Exception:
        pass
    return fuori


def dvd_cifrato(mp):
    """Se un DVD video e' cifrato (CSS). Non decifra niente: guarda solo i
    pacchetti MPEG dei file VOB del titolo piu' grande, dove i due bit
    "PES_scrambling_control" dicono se quel pezzo e' cifrato. Nel dubbio
    (file che non si leggono, nessun VOB) risponde True."""
    try:
        cartella = next(os.path.join(mp, n) for n in ("VIDEO_TS", "video_ts")
                        if os.path.isdir(os.path.join(mp, n)))
        vob = [os.path.join(cartella, n) for n in os.listdir(cartella)
               if n.upper().endswith(".VOB") and not n.upper().startswith("VIDEO_TS")]
        if not vob:
            return True
        f = max(vob, key=os.path.getsize)
        settori = os.path.getsize(f) // 2048
        visti = 0
        with open(f, "rb") as h:
            for i in range(200):
                h.seek((settori * i // 200) * 2048)
                s = h.read(2048)
                if len(s) < 2048 or s[:4] != b"\x00\x00\x01\xba" or (s[4] & 0xC0) != 0x40:
                    continue
                p = 14 + (s[13] & 7)
                if s[p:p + 3] != b"\x00\x00\x01" or not (s[p + 3] == 0xBD or 0xC0 <= s[p + 3] <= 0xEF):
                    continue
                visti += 1
                if (s[p + 6] >> 4) & 3:
                    return True
        return visti == 0
    except Exception:
        return True


def cd_audio():
    """I CD audio nei lettori: [(dispositivo, numero di tracce)]. Un CD
    audio non si monta come una cartella: lo si riconosce dai dati che
    udev tiene per il lettore (ID_CDROM_MEDIA_TRACK_COUNT_AUDIO)."""
    fuori = []
    try:
        lettori = sorted(n for n in os.listdir("/sys/block") if n.startswith("sr"))
    except OSError:
        return fuori
    for n in lettori:
        try:
            with open("/sys/block/%s/dev" % n) as h:
                mm = h.read().strip()
            with open("/run/udev/data/b" + mm, encoding="utf-8", errors="ignore") as h:
                dati = h.read()
        except OSError:
            continue
        m = re.search(r"^E:ID_CDROM_MEDIA_TRACK_COUNT_AUDIO=(\d+)", dati, re.M)
        if m and int(m.group(1)) > 0:
            fuori.append(("/dev/" + n, int(m.group(1))))
    return fuori


def e_cd(url):
    return url.startswith("cdda://")


def titolo_da_etichetta(etichetta):
    """Il titolo da cercare dall'etichetta di un DVD: 'BACK_FUTURE_3' ->
    'Back Future 3'; via DISC 1, DVD, PAL, WS e simili."""
    t = re.sub(r"[_.]+", " ", etichetta)
    t = re.sub(r"\b(DISC|DISK|CD|D)\s*\d\b|\b(DVD|DVD9|DVD5|PAL|NTSC|WS|FS|R\d|UK|US|EU|SE)\b",
               " ", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip().title() or etichetta


def e_dvd(mp):
    """Un DVD video: ha la cartella VIDEO_TS."""
    return any(os.path.isdir(os.path.join(mp, n)) for n in ("VIDEO_TS", "video_ts"))


def colore_di(nome):
    """Il colore del pallino di un canale: sempre lo stesso per lo stesso
    nome, cosi' non cambia a ogni apertura."""
    n = int(hashlib.md5(nome.encode("utf-8")).hexdigest()[:8], 16)
    return PALLINI[n % len(PALLINI)]


CALDI = ("#ff9f0a", "#ffd60a", "#ffb340")
FREDDI = ("#30d158", "#0a84ff", "#bf5af2", "#64d2ff")
COLORI_CARTELLE = tuple(CALDI[i // 2 % len(CALDI)] if i % 2 == 0 else
                        FREDDI[i // 2 % len(FREDDI)]
                        for i in range(2 * len(CALDI) * len(FREDDI)))


def colore_cartella(nome, posto):
    """Il colore di una cartella: rosso solo per i preferiti; le altre,
    nell'ordine in cui stanno, un caldo e un freddo alternati."""
    if nome == PREFERITI:
        return ROSSO
    return COLORI_CARTELLE[posto % len(COLORI_CARTELLE)]


def cartella_liste_in_uso():
    """La cartella delle liste che si usa davvero: la prima che ha gia'
    qualcosa dentro; se sono tutte vuote, la prima."""
    for c in CARTELLE_LISTE:
        try:
            if any(not n.startswith(".") for n in os.listdir(c)):
                return c
        except Exception:
            continue
    return CARTELLE_LISTE[0]


def file_preferiti(tipo=PREF_IPTV):
    """La lista dei preferiti: playlists/favorite/iptv.m3u per i canali,
    music.m3u per la musica, video.m3u per i video; quella che c'e' gia'
    (in qualunque cartella delle liste), se no nella cartella in uso. Il
    vecchio favorite/favorite.m3u vale come iptv.m3u."""
    nome = tipo
    for c in CARTELLE_LISTE:
        f = os.path.join(c, PREFERITI, nome + ".m3u")
        if os.path.isfile(f):
            return f
        vecchio = os.path.join(c, PREFERITI, PREFERITI + ".m3u")
        if tipo == PREF_IPTV and os.path.isfile(vecchio):
            try:
                os.rename(vecchio, f)
                return f
            except Exception:
                return vecchio
    return os.path.join(cartella_liste_in_uso(), PREFERITI, nome + ".m3u")


def leggi_preferiti_cat(tipo=PREF_IPTV):
    """Come leggi_preferiti, ma con la playlist: (nome, url, ide, playlist)."""
    f = file_preferiti(tipo)
    if not os.path.isfile(f):
        return []
    try:
        canali = leggi_lista(f)
        cat = categorie_m3u(f)
    except Exception:
        return []
    return [(n, u, i, cat.get(u, "MyPlaylist")) for n, u, i in canali]


def leggi_preferiti(tipo=PREF_IPTV):
    f = file_preferiti(tipo)
    if not os.path.isfile(f):
        return []
    try:
        return leggi_lista(f)
    except Exception:
        return []


def scrivi_preferiti(canali, tipo=PREF_IPTV):
    f = file_preferiti(tipo)
    if not canali:

        try:
            os.remove(f)
        except Exception:
            pass
        try:
            os.rmdir(os.path.dirname(f))
        except Exception:
            pass
        return
    os.makedirs(os.path.dirname(f), exist_ok=True)
    with open(f, "w", encoding="utf-8") as o:
        o.write("#EXTM3U\n")
        for voce in canali:
            nome, url, ide = voce[0], voce[1], voce[2]
            cat = voce[3] if len(voce) > 3 else ""
            lic = (' license-type="clearkey" license-details="%s"' % CHIAVI[url]
                   if url in CHIAVI else "")
            gt = (' group-title="%s"' % cat.replace('"', "'")) if cat else ""
            o.write("#EXTINF:-1%s,%s\n%s\n" % (
                (' tvg-id="%s"' % ide if ide else "") + lic + gt, nome, url))


CACHE_EPG = os.path.join(CASA, ".cache", "xvb", "epg")
CACHE_VARIANTE = os.path.join(CASA, ".cache", "xvb")
CARTELLA_DEMO = os.path.join(CASA, ".local", "share", "xvb", "demo")
DEMO_M3U = os.path.join(CARTELLA_DEMO, "demo.m3u")
DEMO_XML = os.path.join(CARTELLA_DEMO, "demo.xml")
CACHE_DEMO_LOCK = os.path.join(CACHE_VARIANTE, "demo.lock")
URL_DEMO_M3U = "https://raw.githubusercontent.com/JonaDev2026/xvb/main/demo/demo.m3u"
URL_DEMO_XML = "https://raw.githubusercontent.com/JonaDev2026/xvb/main/demo/demo.xml"
MEDIA = ("mkv", "mp4", "avi", "mov", "webm", "ts", "m2ts", "mpg", "mpeg", "wmv", "flv",
         "ogv", "m4v", "mp3", "flac", "ogg", "opus", "m4a", "aac", "wav", "wma", "ape", "mka")


def e_media(f):
    return os.path.splitext(f)[1][1:].lower() in MEDIA


def nome_url(u):
    """Il nome da mostrare per un indirizzo: l'ultimo pezzo del percorso,
    se no il sito."""
    return (os.path.basename(urlparse(u).path.rstrip("/")) or urlparse(u).netloc or u)


def titolo_url(u):
    """Il titolo di un indirizzo, per la lista: il nome del file senza
    estensione, con gli spazi al posto di _ . -"""
    pezzi = [x for x in urlparse(u).path.split("/") if x]
    n = pezzi[-1] if pezzi else ""
    m = re.match(r"(.*)\.([A-Za-z0-9]+)$", n)
    if m and (m.group(2).lower() in MEDIA or m.group(2).lower() in ("m3u8", "m3u", "mpd", "ism")):
        n = m.group(1)
    if not n and len(pezzi) > 1:
        n = re.sub(r"\.[A-Za-z0-9]+$", "", pezzi[-2])
    return re.sub(r"[_.]+", " ", n).strip() or urlparse(u).netloc or u


def tipo_url(u):
    """Cos'e' un indirizzo e da dove viene: 'Video · MP4 · sito',
    'Audio · MP3 · sito', 'Stream HLS · sito', 'Stream · sito'."""
    p, sito = urlparse(u).path, urlparse(u).netloc
    m = re.search(r"\.([A-Za-z0-9]+)$", p)
    est = m.group(1).lower() if m else ""
    if est in AUDIO:
        cosa = "Audio \u00b7 " + est.upper()
    elif est in MEDIA:
        cosa = "Video \u00b7 " + est.upper()
    elif est in ("m3u8", "m3u"):
        cosa = "Stream HLS"
    elif est == "mpd":
        cosa = "Stream DASH"
    else:
        cosa = "Stream"
    return cosa + (" \u00b7 " + sito if sito else "")


def e_media_url(u):
    """Un file video o audio in rete (http...mp4, mp3...): si suona come un
    file, non come un canale IPTV."""
    return bool(re.match(r"https?://", u or "", re.I)) and e_media(urlparse(u).path)


def media_in(cartella):
    """I file audio/video dentro a una cartella e a tutte le sue
    sottocartelle (quelle nascoste no), in ordine di percorso cosi' gli
    album restano insieme: (nome senza estensione, percorso, None) come i
    canali."""
    fuori = []
    try:
        for dove, sotto, nomi in os.walk(cartella):
            sotto[:] = sorted((d for d in sotto if not d.startswith(".")), key=str.lower)
            for n in sorted(nomi, key=str.lower):
                if not n.startswith(".") and e_media(n):
                    fuori.append((os.path.splitext(n)[0], os.path.join(dove, n), None))
    except Exception:
        pass
    return fuori


def ore_min_sec(secondi):
    """1:02:03 o 02:03, come il cronometro del REC."""
    secondi = int(max(0, secondi))
    ore, resto = divmod(secondi, 3600)
    return ("%d:%02d:%02d" % (ore, resto // 60, resto % 60) if ore
            else "%02d:%02d" % (resto // 60, resto % 60))


REGISTRAZIONI = os.path.join(CASA, "Videos", "xvb")

PORTA = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or os.path.join(CASA, ".cache", "xvb"),
                     "xvb.sock")


_DURATE = {}


def durata_di(f):
    """La durata di un file in secondi, con ffprobe (viene con ffmpeg);
    None se non si riesce. In memoria per percorso e data di modifica."""
    try:
        k = (f, os.path.getmtime(f))
    except OSError:
        return None
    if k not in _DURATE:
        try:
            r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                "-of", "default=nw=1:nk=1", f],
                               capture_output=True, text=True, timeout=15)
            _DURATE[k] = float(r.stdout.strip())
        except Exception:
            _DURATE[k] = None
    return _DURATE[k]
EPG_VECCHIA = 6 * 3600


def ora_xmltv(t):
    """'20260929120000 +0200' -> secondi dal 1970. None se non si legge."""
    m = re.match(r"(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})?\s*([+-]\d{4})?", t or "")
    if not m:
        return None
    y, mo, d, h, mi = (int(x) for x in m.groups()[:5])
    se = int(m.group(6) or 0)
    import calendar
    utc = calendar.timegm((y, mo, d, h, mi, se, 0, 0, 0))
    z = m.group(7)
    if z:
        scarto = (int(z[1:3]) * 60 + int(z[3:5])) * 60
        utc -= scarto if z[0] == "+" else -scarto
    return utc


def prendi_epg(dove):
    """Il file della guida: da un percorso locale com'e', da un url lo
    scarica in cache (e lo riusa per sei ore). Torna il percorso."""
    if os.path.isfile(dove):
        return dove
    os.makedirs(CACHE_EPG, exist_ok=True)
    f = os.path.join(CACHE_EPG, hashlib.md5(dove.encode()).hexdigest())
    if os.path.isfile(f) and time.time() - os.path.getmtime(f) < EPG_VECCHIA:
        return f
    try:
        r = Request(dove, headers={"User-Agent": UA})
        dati = urlopen(r, timeout=60).read()
    except Exception:
        if os.path.isfile(f):
            return f                # non si scarica: meglio la copia vecchia che niente
        raise
    with open(f + ".part", "wb") as o:
        o.write(dati)
    os.replace(f + ".part", f)
    return f


def unisci_programmi(base, altri):
    """base e altri: liste ordinate (inizio, fine, titolo). Si tiene tutta la
    base e di altri si aggiungono solo i programmi che non si sovrappongono."""
    import bisect
    if not altri:
        return base
    if not base:
        return list(altri)
    res = list(base)
    inizi = [b_[0] for b_ in res]
    for s_, e_, t_ in altri:
        i = bisect.bisect_left(inizi, e_)
        if i > 0 and res[i - 1][1] > s_:
            continue
        res.insert(i, (s_, e_, t_))
        inizi.insert(i, s_)
    return res


def unisci_guide(guide):
    """guide: [(nomi, programmi)] in ordine di preferenza. Lo stesso canale
    in guide diverse (stesso id, o stesso nome) e' un canale solo: decide la
    prima guida che ha i suoi programmi, le altre riempiono i buchi e il
    futuro. Torna (nomi, programmi)."""
    padre = {}

    def radice(x):
        while padre.setdefault(x, x) != x:
            padre[x] = padre[padre[x]]
            x = padre[x]
        return x

    per_nome = {}
    for nomi, _p in guide:
        for nome, ide in nomi.items():
            if ide:
                per_nome.setdefault(nome, []).append(ide)
    for ids in per_nome.values():
        for ide in ids[1:]:
            padre[radice(ide)] = radice(ids[0])
    for _n, prog in guide:
        for ide in prog:
            if ide:
                radice(ide)
    gruppi = {}
    for ide in list(padre):
        gruppi.setdefault(radice(ide), []).append(ide)
    programmi = {}
    for _n, prog in guide:
        if "" in prog:
            programmi.setdefault("", prog[""])
    for ids in gruppi.values():
        lista = []
        for _n, prog in guide:
            for ide in ids:
                if ide in prog:
                    lista = unisci_programmi(lista, prog[ide])
        if lista:
            for ide in ids:
                programmi[ide] = lista
    nomi_tutti = {}
    for nomi, _p in guide:
        for nome, ide in nomi.items():
            nomi_tutti.setdefault(nome, ide)
    return nomi_tutti, programmi


def piu_nuova(a, b):
    """Se la versione a e' piu' nuova della b: si confrontano numero per
    numero ('1.10' batte '1.9')."""
    def pezzi(v):
        return [int(x) if x.isdigit() else 0 for x in re.split(r"[.\-]", v)]
    return pezzi(a) > pezzi(b)


def registrazioni():
    """Le registrazioni su disco, raggruppate per canale e giorno:
    [((canale, giorno), [(ora, titolo, percorso), ...])], dal giorno piu'
    recente. Il canale e' la sottocartella (i file sciolti vanno sotto
    'xvb'), giorno e ora si leggono dal nome del file, se no dalla data
    del file."""
    gruppi = {}
    try:
        voci = os.listdir(REGISTRAZIONI)
    except Exception:
        return []
    for n in voci:
        p = os.path.join(REGISTRAZIONI, n)
        if os.path.isdir(p):
            canale, file_ = n, [os.path.join(p, f) for f in os.listdir(p)]
        else:
            canale, file_ = "xvb", [p]
        for f in file_:
            if not f.lower().endswith((".mkv", ".mka", ".mp4", ".ts")):
                continue
            base = os.path.splitext(os.path.basename(f))[0]
            m = re.search(r"(\d{4})-(\d{2})-(\d{2}) (\d{2})-(\d{2})$", base)
            if m:
                giorno = "%s-%s-%s" % m.group(1, 2, 3)
                ora = "%s:%s" % m.group(4, 5)
                titolo = base[:m.start()].rstrip(" -") or base
            else:
                t = time.localtime(os.path.getmtime(f))
                giorno, ora, titolo = time.strftime("%Y-%m-%d", t), time.strftime("%H:%M", t), base
            gruppi.setdefault((canale, giorno), []).append((ora, titolo, f))
    fuori = sorted(gruppi.items(), key=lambda kv: (kv[0][1], kv[0][0]), reverse=True)
    return [(k, sorted(v)) for k, v in fuori]


def nome_dentro(f):
    """Il nome del file scritto dentro all'archivio gzip (chi comprime ce
    lo lascia: guida.xml.gz -> 'guida.xml'). None se non c'e' o non e' gz."""
    try:
        with open(f, "rb") as h:
            testa = h.read(4096)
        if testa[:2] != b"\x1f\x8b":
            return None
        flag, p = testa[3], 10
        if flag & 4:
            p += 2 + int.from_bytes(testa[p:p + 2], "little")
        if flag & 8:
            fine = testa.index(b"\x00", p)
            nome = os.path.basename(testa[p:fine].decode("latin-1").strip())
            return nome or None
    except Exception:
        pass
    return None


def nome_epg(dove):
    """Come chiamare la guida quando non si sa ancora il nome del file
    dentro all'archivio: l'ultima parola dell'url o del percorso, .xml."""
    if re.match(r"https?://", dove, re.I):
        u = urlparse(dove)
        dominio = u.netloc.lower()
        if dominio.startswith("www."):
            dominio = dominio[4:]


        nome = os.path.basename(u.path.rstrip("/"))
        if not nome:
            return dominio
    else:
        nome = os.path.basename(dove.rstrip("/")) or dove
    nome = re.sub(r"\.gz$", "", nome, flags=re.I)
    return nome if nome.lower().endswith(".xml") else nome + ".xml"


def nome_piatto(nome):
    """Un nome di canale ridotto all'osso per confrontarlo con la guida:
    minuscolo, senza spazi, punteggiatura e senza HD/FHD/4K in coda."""
    n = re.sub(r"[^a-z0-9]+", "", (nome or "").lower())
    return re.sub(r"(fhd|uhd|hd|4k|sd)$", "", n)


POSTER_H = 192
CACHE_POSTER = os.path.join(CASA, ".cache", "xvb", "poster")
EPG_ICONE = {}
EPG_ICONE_NOME = {}
EPG_DESC = {}
EPG_DESC_NOME = {}


def icona_programma_epg(ide, nome, programma):
    """Trova la locandina anche nelle altre guide, se l'ID XMLTV cambia.

    Prima rispetta l'ID della guida principale; se li' manca l'immagine,
    cerca per nome del canale, inizio e titolo del programma.
    """
    if not programma:
        return None
    inizio, _fine, titolo = programma
    src = EPG_ICONE.get((ide or "", inizio))
    if src:
        return src
    return EPG_ICONE_NOME.get((nome_piatto(nome), inizio, nome_piatto(titolo)))


def descrizione_programma_epg(ide, nome, programma):
    """La descrizione del programma (stessa ricerca della locandina)."""
    if not programma:
        return ""
    inizio, _fine, titolo = programma
    return (EPG_DESC.get((ide or "", inizio))
            or EPG_DESC_NOME.get((nome_piatto(nome), inizio, nome_piatto(titolo))) or "")


def leggi_epg(f):
    """La guida XMLTV, anche compressa (gz): torna (nomi, programmi).
    nomi: nome mostrato (minuscolo) -> id del canale.
    programmi: id -> [(inizio, fine, titolo)] ordinati, solo quelli da
    un'ora fa in poi, per non tenere in memoria giorni interi."""
    with open(f, "rb") as h:
        testa = h.read(2)
    apri = gzip.open if testa == b"\x1f\x8b" else open
    nomi, programmi, nomi_per_id = {}, {}, {}
    da = time.time() - 3600
    with apri(f, "rb") as h:
        for _ev, el in ET.iterparse(h):
            if el.tag == "channel":
                ide = el.get("id") or ""
                for n in el.findall("display-name"):
                    if n.text and n.text.strip():
                        chiave = nome_piatto(n.text)
                        nomi.setdefault(chiave, ide)
                        nomi_per_id.setdefault(ide, set()).add(chiave)
                el.clear()
            elif el.tag == "programme":
                inizio, fine = ora_xmltv(el.get("start")), ora_xmltv(el.get("stop"))
                if inizio is not None and fine is not None and fine >= da:
                    t = el.find("title")
                    titolo = (t.text or "").strip() if t is not None else ""
                    programmi.setdefault(el.get("channel") or "", []).append(
                        (inizio, fine, titolo))
                    dd = el.find("desc")
                    if dd is not None and dd.text and dd.text.strip() and inizio <= time.time() + 86400:
                        ide_d = el.get("channel") or ""
                        testo_d = " ".join(dd.text.split())[:300]
                        EPG_DESC.setdefault((ide_d, inizio), testo_d)
                        titolo_d = nome_piatto(titolo)
                        for nome_d in nomi_per_id.get(ide_d, ()):
                            EPG_DESC_NOME.setdefault((nome_d, inizio, titolo_d), testo_d)
                    ic = el.find("icon")
                    if ic is not None and ic.get("src"):
                        src = ic.get("src")
                        ide = el.get("channel") or ""

                        EPG_ICONE.setdefault((ide, inizio), src)
                        titolo_key = nome_piatto(titolo)
                        for nome_key in nomi_per_id.get(ide, ()):
                            EPG_ICONE_NOME.setdefault((nome_key, inizio, titolo_key), src)
                el.clear()
    for lista in programmi.values():
        lista.sort()
    return nomi, programmi


def varianti(url):
    """Le qualita' dichiarate nel master, dalla piu' leggera in su.

    Torna (banda, indirizzo video, indirizzo audio o None, altezza in
    pixel o 0, master). L'audio puo' stare per conto suo: in tante liste
    la variante e' solo video e la traccia audio e' dichiarata a parte,
    in un gruppo. Per questo `master` e' il testo di una master playlist
    fatta apposta, con dentro solo quella variante e il suo gruppo audio
    (indirizzi assoluti): la si da' a mpv al posto della variante nuda,
    cosi' e' ffmpeg a leggere video e audio insieme e restano a tempo.
    Se il canale non e' un master torna una lista vuota: non c'e' niente
    da scegliere e si suona l'indirizzo com'e'."""
    if "jmp2.uk" in url:
        return []
    try:
        r = Request(url, headers={"User-Agent": UA})


        risposta = urlopen(r, timeout=10)
        testo = risposta.read(262144).decode("utf-8", "ignore")
        base_url = risposta.geturl()
    except Exception:
        return []
    if not testo.lstrip().startswith("#EXTM3U") or "#EXT-X-STREAM-INF" not in testo:
        return []
    gruppi, media, testa = {}, {}, ["#EXTM3U"]
    for riga in testo.splitlines():
        riga = riga.strip()
        if riga.startswith("#EXT-X-MEDIA") and "TYPE=AUDIO" in riga:
            g = re.search(r'GROUP-ID="([^"]*)"', riga)
            u = re.search(r'URI="([^"]*)"', riga)
            if g and u:
                assoluto = urljoin(base_url, u.group(1))
                gruppi.setdefault(g.group(1), assoluto)
                media.setdefault(g.group(1), []).append(
                    riga.replace('URI="%s"' % u.group(1), 'URI="%s"' % assoluto))
        elif riga.startswith(("#EXT-X-VERSION", "#EXT-X-INDEPENDENT-SEGMENTS")):
            testa.append(riga)
    fuori, banda, gruppo, video, alta, inf = [], 0, None, True, 0, ""
    for riga in testo.splitlines():
        riga = riga.strip()
        if riga.startswith("#EXT-X-STREAM-INF"):
            inf = riga
            m = re.search(r"BANDWIDTH=(\d+)", riga)
            banda = int(m.group(1)) if m else 0
            m = re.search(r"RESOLUTION=(\d+)x(\d+)", riga)
            alta = int(m.group(2)) if m else 0
            g = re.search(r'AUDIO="([^"]*)"', riga)
            gruppo = g.group(1) if g else None


            c = re.search(r'CODECS="([^"]*)"', riga)
            codec = c.group(1).lower() if c else ""
            video = True
            if codec and "RESOLUTION=" not in riga:

                video = any(k in codec for k in ("avc", "hvc", "hev", "av01",
                                                 "vp0", "dvh", "mp4v"))
        elif riga and not riga.startswith("#"):
            if video:
                indirizzo = urljoin(base_url, riga)
                master = "\n".join(testa + media.get(gruppo, []) +
                                   [inf, indirizzo]) + "\n"
                fuori.append((banda, indirizzo, gruppi.get(gruppo), alta, master))
            banda, gruppo, video, alta, inf = 0, None, True, 0, ""
    fuori.sort()
    return fuori


class Ricerca(tk.Canvas):
    """La casella di ricerca, stile Material: una pillola senza bordi,
    la lente a sinistra, la scritta grigia quando e' vuota. La pillola e'
    disegnata su una tela e la casella vera ci sta sopra."""

    def __init__(self, dove, cambia, alta=36, vuota="Search"):
        tk.Canvas.__init__(self, dove, height=alta, bg=PANNELLO,
                           highlightthickness=0, bd=0)
        self.alta, self.cambia, self.vuota = alta, cambia, vuota
        self.testo = tk.StringVar()
        self.casella = tk.Entry(self, textvariable=self.testo, bg=CERCA,
                                fg=TESTO, insertbackground=ACCENTO,
                                relief="flat", bd=0, highlightthickness=0,
                                font=(CARATTERE, 10, STILE_CARATTERE))
        self.casella.bind("<KeyRelease>", lambda e: self.cambia())
        self.casella.bind("<FocusIn>", self.dentro)
        self.casella.bind("<FocusOut>", self.fuori)
        self.casella.bind("<Escape>", lambda e: (self.testo.set(""),
                                                self.cambia(), self.fuori()))
        self.bind("<Configure>", self.disegna)
        self.fuori()

    def disegna(self, ev=None):
        self.delete("pillola")
        w, h, r = self.winfo_width(), self.alta, self.alta // 2

        for x in (0, w - 2 * r):
            self.create_oval(x, 0, x + 2 * r, h, fill=CERCA, outline="",
                             tags="pillola")
        self.create_rectangle(r, 0, w - r, h, fill=CERCA, outline="",
                              tags="pillola")

        cx, cy = r + 2, h / 2.0
        self.create_oval(cx - 6, cy - 7, cx + 4, cy + 3, outline=GRIGIO,
                         width=2, tags="pillola")
        self.create_line(cx + 3, cy + 2, cx + 8, cy + 7, fill=GRIGIO,
                         width=2, capstyle="round", tags="pillola")
        self.tag_lower("pillola")
        self.casella.place(x=r + 16, y=h // 2 - 10, width=w - r * 2 - 22,
                           height=20)

    def dentro(self, ev=None):
        if self.testo.get() == self.vuota:
            self.testo.set("")
            self.casella.config(fg=TESTO)

    def fuori(self, ev=None):
        if not self.testo.get():
            self.testo.set(self.vuota)
            self.casella.config(fg=GRIGIO)

    def get(self):
        v = self.testo.get()
        return "" if v == self.vuota else v


class Cursore(tk.Canvas):
    """Un cursore disegnato come quello di YouTube: traccia sottile grigia,
    la parte piena bianca, senza pomello. Si trascina, si clicca, e
    risponde alla rotella."""

    def __init__(self, dove, cambia, larga=170, alta=26):
        tk.Canvas.__init__(self, dove, width=larga, height=alta, bg=BARRA,
                           highlightthickness=0, bd=0, cursor="hand2")
        self.larga, self.alta, self.cambia = larga, alta, cambia
        self.valore = 85
        self.bind("<Button-1>", self.tocca)
        self.bind("<B1-Motion>", self.tocca)
        self.bind("<Button-4>", lambda e: self.set(self.valore + 5))
        self.bind("<Button-5>", lambda e: self.set(self.valore - 5))
        self.bind("<MouseWheel>", lambda e: self.set(
            self.valore + (5 if e.delta > 0 else -5)))
        self.disegna()

    def tocca(self, ev):
        k = (ev.x - 10) / float(self.larga - 20)
        self.set(int(round(max(0.0, min(1.0, k)) * 100)))

    def set(self, v):
        v = int(max(0, min(100, v)))
        if v != self.valore:
            self.valore = v
            self.cambia(v)
        self.disegna()

    def get(self):
        return self.valore

    def sfondo(self, tinta):
        """La sfumatura della barra passa anche dietro al cursore: tinta e'
        la funzione riga -> colore della barra (None = fondo piatto)."""
        self.tinta = tinta
        self.disegna()

    def disegna(self):
        self.delete("all")
        tinta = getattr(self, "tinta", None)
        if tinta and self.winfo_ismapped():
            y0 = self.winfo_y()
            for r in range(self.alta):
                self.create_line(0, r, self.larga, r, fill=tinta(y0 + r))
        y, x0, x1 = self.alta // 2, 10, self.larga - 10
        x = x0 + (x1 - x0) * self.valore / 100.0

        self.create_line(x0, y, x1, y, width=4, fill="#4a4a4a", capstyle="round")
        if x > x0:
            self.create_line(x0, y, x, y, width=4, fill="#ffffff", capstyle="round")


def colore_sfondo_guida(hex_colore, bg_hex="#121212", alfa=0.40):
    """Calcola il colore del blocco in onda con un effetto trasparenza al 40%
    miscelato con lo sfondo scuro, garantendo massima leggibilità al testo bianco."""
    try:
        h_c = hex_colore.lstrip("#")
        r1, g1, b1 = int(h_c[0:2], 16), int(h_c[2:4], 16), int(h_c[4:6], 16)
        bg_c = bg_hex.lstrip("#")
        r2, g2, b2 = int(bg_c[0:2], 16), int(bg_c[2:4], 16), int(bg_c[4:6], 16)
        rf = int(r1 * alfa + r2 * (1.0 - alfa))
        gf = int(g1 * alfa + g2 * (1.0 - alfa))
        bf = int(b1 * alfa + b2 * (1.0 - alfa))
        return "#%02x%02x%02x" % (rf, gf, bf)
    except Exception:
        return "#252030"


class Guida(tk.Toplevel):
    """La griglia dei programmi, stile guida TV: una riga per canale della
    lista aperta, il tempo che scorre a destra, i programmi come blocchi;
    la riga lilla e' adesso. La riga delle ore e la colonna dei nomi
    restano ferme mentre si scorre. Clic sul nome o su un blocco per
    vedere il canale. Rotella per le righe, Shift+rotella per il tempo."""

    ORA = 300
    RIGA = 64
    NOMI = 250
    TESTA = 28

    def __init__(self, app):
        tk.Toplevel.__init__(self, app.root, bg=FONDO)
        self.app = app
        self.title(_("TV guide"))
        self.geometry("1100x620")
        self.minsize(700, 300)
        if app.icona_finestra is not None:
            self.iconphoto(False, app.icona_finestra)


        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(1, weight=1)
        tk.Frame(self, bg=PANNELLO, width=self.NOMI, height=self.TESTA).grid(row=0, column=0, sticky="nsew")
        self.testa = tk.Canvas(self, bg=PANNELLO, height=self.TESTA, highlightthickness=0, bd=0)
        self.testa.grid(row=0, column=1, sticky="nsew")
        self.nomi = tk.Canvas(self, bg=FONDO, width=self.NOMI, highlightthickness=0, bd=0)
        self.nomi.grid(row=1, column=0, sticky="nsew")
        self.tela = tk.Canvas(self, bg=FONDO, highlightthickness=0, bd=0, xscrollincrement=40, yscrollincrement=32)
        self.tela.grid(row=1, column=1, sticky="nsew")
        self.sbx = tk.Scrollbar(self, orient="horizontal", command=self.xv, bg=TASTO,
                                troughcolor=FONDO, activebackground=GRIGIO, bd=0,
                                highlightthickness=0, relief="flat", elementborderwidth=0)
        self.sbx.grid(row=2, column=1, sticky="ew")
        self.sby = tk.Scrollbar(self, orient="vertical", command=self.yv, bg=TASTO,
                                troughcolor=FONDO, activebackground=GRIGIO, bd=0,
                                highlightthickness=0, relief="flat", elementborderwidth=0)
        self.sby.grid(row=1, column=2, sticky="ns")
        self.tela.config(xscrollcommand=self.sbx.set, yscrollcommand=self.sby.set)
        self.da = int(time.time() // 1800 * 1800) - 1800
        self.fino = self.da + 6 * 3600
        self.righe = []
        self.raccogli()
        self.tela.bind("<Configure>", lambda e: self.disegna())
        for c in (self.tela, self.nomi, self.testa):
            c.bind("<Button-4>", lambda e: self.scorri_y(-1))
            c.bind("<Button-5>", lambda e: self.scorri_y(+1))
            c.bind("<MouseWheel>", lambda e: self.scorri_y(-1 if e.delta > 0 else +1))
            try:
                c.bind("<Button-6>", lambda e: self.scorri_x(-1))
                c.bind("<Button-7>", lambda e: self.scorri_x(+1))
            except tk.TclError:
                pass
            c.bind("<Shift-Button-4>", lambda e: self.scorri_x(-1))
            c.bind("<Shift-Button-5>", lambda e: self.scorri_x(+1))
            c.bind("<Shift-MouseWheel>", lambda e: self.scorri_x(-1 if e.delta > 0 else +1))
        self.tela.bind("<Button-1>", lambda e: self.clic(self.tela, e))
        self.nomi.bind("<Button-1>", lambda e: self.clic(self.nomi, e))
        self.bind("<Escape>", lambda e: self.chiudi())
        self.dopo = self.after(30000, self.ridisegna)
        self.protocol("WM_DELETE_WINDOW", self.chiudi)

    def raccogli(self):
        """I canali della lista aperta che stanno nella guida, coi loro
        programmi nella finestra di tempo."""
        app = self.app
        self.righe = []
        for nome, url, ide in app.canali:
            k = ide if ide and ide in app.epg else app.epg_nomi.get(nome_piatto(nome))
            if not k:
                continue
            prog = [(a, b, t) for a, b, t in app.epg.get(k, [])
                    if b > self.da and a < self.fino]
            if prog:
                self.righe.append((nome, url, ide, prog))

    def x_di(self, t):
        return (t - self.da) / 3600.0 * self.ORA

    def disegna(self):
        c, n, h = self.tela, self.nomi, self.testa
        for tela in (c, n, h):
            tela.delete("all")
        largo = int(self.x_di(self.fino)) + 20
        alto = max(1, len(self.righe)) * self.RIGA + 20
        c.config(scrollregion=(0, 0, largo, alto))
        n.config(scrollregion=(0, 0, self.NOMI, alto))
        h.config(scrollregion=(0, 0, largo, self.TESTA))
        if not self.righe:
            c.create_text(20, 20, anchor="w", fill=GRIGIO,
                          text=_("no guide data for this playlist"))
            return
        adesso = time.time()

        t = self.da
        while t <= self.fino:
            x = self.x_di(t)
            h.create_line(x, self.TESTA - 6, x, self.TESTA, fill="#3a3a3a")
            h.create_text(x + 6, self.TESTA // 2, anchor="w", fill=GRIGIO,
                          text=time.strftime("%H:%M", time.localtime(t)))
            c.create_line(x, 0, x, alto, fill="#2a2a2a")
            t += 1800

        for i, (nome, url, ide, prog) in enumerate(self.righe):
            y0 = i * self.RIGA
            y1 = y0 + self.RIGA
            fondo = PANNELLO if i % 2 else FONDO
            c.create_rectangle(0, y0, largo, y1, fill=fondo, outline="")
            colore = self.app.colore_canale(nome, url)
            for a, b, titolo in prog:
                xa, xb = max(self.x_di(a), 0), self.x_di(b)
                in_onda = a <= adesso < b

                c.create_rectangle(xa + 1, y0 + 6, xb - 1, y1 - 6,
                                   fill=colore_sfondo_guida(colore, fondo, 0.40) if in_onda else TASTO, outline="",
                                   tags=("prog", url))
                if xb - xa > 30:
                    testo = titolo
                    while testo and len(testo) * 7 > xb - xa - 14:
                        testo = testo[:-1]
                    if testo != titolo and len(testo) > 2:
                        testo = testo[:-1] + "\u2026"
                    c.create_text(xa + 8, (y0 + y1) // 2, anchor="w", text=testo,
                                  fill="#ffffff" if in_onda else TESTO,
                                  tags=("prog", url))

            n.create_rectangle(0, y0, self.NOMI, y1, fill=fondo, outline="", tags=("nome", url))
            logo = self.app.logo_di(url)
            if logo is not None:
                n.create_image(8 + LOGO_L // 2, (y0 + y1) // 2, image=logo, tags=("nome", url))
            else:
                n.create_image(8 + LOGO_L // 2, (y0 + y1) // 2,
                               image=self.app.pallino_di(nome, url), tags=("nome", url))
            n.create_text(LOGO_L + 18, (y0 + y1) // 2, anchor="w", text=nome[:18],
                          fill=TESTO, tags=("nome", url))

        x = self.x_di(adesso)
        c.create_line(x, 0, x, alto, fill=ACCENTO, width=2)
        h.create_polygon(x - 6, self.TESTA - 8, x + 6, self.TESTA - 8, x, self.TESTA, fill=ACCENTO)

        h.xview_moveto(c.xview()[0])
        n.yview_moveto(c.yview()[0])

    def yv(self, *a):
        self.tela.yview(*a)
        self.nomi.yview_moveto(self.tela.yview()[0])

    def xv(self, *a):
        self.tela.xview(*a)
        self.testa.xview_moveto(self.tela.xview()[0])

    def ridisegna(self):
        self.disegna()
        self.dopo = self.after(30000, self.ridisegna)

    def scorri_y(self, verso):
        self.tela.yview_scroll(verso * 2, "units")
        self.nomi.yview_moveto(self.tela.yview()[0])

    def scorri_x(self, verso):
        self.tela.xview_scroll(verso * 3, "units")
        self.testa.xview_moveto(self.tela.xview()[0])

    def clic(self, tela, ev):
        """Clic su un nome o su un blocco: si apre quel canale."""
        x, y = tela.canvasx(ev.x), tela.canvasy(ev.y)
        for item in tela.find_overlapping(x, y, x, y):
            tags = tela.gettags(item)
            if len(tags) >= 2 and tags[0] in ("nome", "prog"):
                url = tags[1]
                for nome, u, ide in self.app.canali:
                    if u == url:
                        self.app.apri(nome, u, ide)
                        self.after(400, self.disegna)
                        return

    def chiudi(self):
        try:
            self.after_cancel(self.dopo)
        except Exception:
            pass
        self.destroy()


SCORRE = object()


def _azione_informativa_audio():
    pass


_AZIONE_INFORMATIVA_AUDIO = _azione_informativa_audio


class Tendina(object):
    """Il menu a tendina disegnato noi, nello stile delle finestrelle:
    scuro, bordo sottile, voci con aria, spunta lilla su quella attiva,
    separatori fra i gruppi. Si chiude scegliendo, cliccando fuori o con
    Esc. Una sola aperta alla volta."""

    ALTA = 30

    def __init__(self, root):
        self.root, self.top, self.al_chiudere, self.chi = root, None, None, None
        self.altrove = None

    def aperta(self):
        return self.top is not None

    def apri(self, x, y, voci, sopra=False, destra=False, al_chiudere=None, chi=None,
             altrove=None):
        """voci: (testo, cosa) - cosa None = voce spenta; None = separatore.
        Un testo che comincia con '*  ' e' spuntato, con '   ' no.
        sopra: la tendina cresce verso l'alto da y; destra: il bordo
        destro sta a x."""
        self.chiudi()
        self.al_chiudere, self.chi, self.altrove = al_chiudere, chi, altrove
        top = tk.Toplevel(self.root, bg=SCELTO)
        top.overrideredirect(True)
        top.geometry("+-9000+-9000")
        dentro = tk.Frame(top, bg=PANNELLO)
        dentro.pack(padx=1, pady=1)


        piatte = []
        for v in voci:
            if v and v[0] is SCORRE:
                piatte += v[1]
            else:
                piatte.append(v)
        larga = max([len(v[0]) for v in piatte if v] + [18]) * 8 + 60


        try:
            f = tkfont.nametofont("TkDefaultFont")
            larga = max([larga] + [f.measure(v[0][3:] if v[0][:3] in ("*  ", "   ") else v[0]) + 70
                                   for v in piatte if v])
        except tk.TclError:
            pass

        def fai_riga(dove, voce):
            testo, cosa = voce
            spunta = testo.startswith("*  ")
            if testo[:3] in ("*  ", "   "):
                testo = testo[3:]
            riga = tk.Frame(dove, bg=PANNELLO, height=self.ALTA, width=larga)
            riga.pack(fill="x")
            riga.pack_propagate(False)
            informativa = cosa is _AZIONE_INFORMATIVA_AUDIO
            colore = TESTO if cosa or informativa else GRIGIO
            segno = tk.Label(riga, text="●" if spunta else "", bg=PANNELLO,
                             fg=ACCENTO, width=2, anchor="center")
            segno.pack(side="left", padx=(8, 0))
            et = tk.Label(riga, text=testo, bg=PANNELLO, fg=colore, anchor="w")
            et.pack(side="left", fill="both", expand=True, padx=(4, 16))
            if cosa and not informativa:
                for w in (riga, segno, et):
                    w.bind("<Enter>", lambda e, r=riga, a=segno, b=et: self.accendi(r, a, b, True))
                    w.bind("<Leave>", lambda e, r=riga, a=segno, b=et: self.accendi(r, a, b, False))
                    w.bind("<Button-1>", lambda e, c=cosa: self.scegli(c))
            return (riga, segno, et), spunta

        def fai_gruppo(lista, righe):
            """Le voci in una finestrella alta 'righe' voci che scorre con
            la rotella; una barretta a destra dice dove si e'. Si apre gia'
            sulla voce spuntata."""


            altre = sum(self.ALTA if v else 9 for v in voci if not (v and v[0] is SCORRE))
            righe = max(3, min(righe, (top.winfo_screenheight() - 40 - altre) // self.ALTA))
            tutta = len(lista) * self.ALTA
            alta = min(len(lista), righe) * self.ALTA
            vista = tk.Frame(dentro, bg=PANNELLO, width=larga, height=alta)
            vista.pack(fill="x")
            vista.pack_propagate(False)
            lista_f = tk.Frame(vista, bg=PANNELLO)
            lista_f.place(x=0, y=0, relwidth=1)
            pezzi, spuntata = [vista, lista_f], None
            for i, v in enumerate(lista):
                ws, sp = fai_riga(lista_f, v)
                pezzi += ws
                if sp and spuntata is None:
                    spuntata = i
            if tutta <= alta:
                return
            barra = tk.Frame(vista, bg=GRIGIO, width=3)
            stato = {"su": 0}

            def vai(y):
                y = max(0, min(tutta - alta, y))
                stato["su"] = y
                lista_f.place_configure(y=-y)
                lunga = max(12, alta * alta // tutta)
                barra.place(relx=1.0, x=-2, y=(alta - lunga) * y // (tutta - alta),
                            height=lunga, anchor="ne")
                barra.lift()
            for p in pezzi:
                p.bind("<Button-4>", lambda e: vai(stato["su"] - 3 * self.ALTA))
                p.bind("<Button-5>", lambda e: vai(stato["su"] + 3 * self.ALTA))
                p.bind("<MouseWheel>", lambda e: vai(stato["su"] + (-3 if e.delta > 0 else 3) * self.ALTA))
            vai((spuntata - righe // 2) * self.ALTA if spuntata else 0)

        for voce in voci:
            if voce is None:
                tk.Frame(dentro, bg=SCELTO, height=1).pack(fill="x", padx=10, pady=4)
            elif voce[0] is SCORRE:
                fai_gruppo(voce[1], voce[2])
            else:
                fai_riga(dentro, voce)
        top.update_idletasks()
        w, h = top.winfo_reqwidth(), top.winfo_reqheight()
        if sopra:
            y -= h
        if destra:
            x -= w

        x = max(0, min(x, top.winfo_screenwidth() - w))
        y = max(0, min(y, top.winfo_screenheight() - h))
        top.geometry("+%d+%d" % (x, y))
        top.bind("<Button-1>", self.fuori)
        top.bind("<Escape>", lambda e: self.chiudi())
        self.top = top


        self.root.after(30, lambda: self.prendi(top))


        self.fuori_da = None
        self.aveva_fuoco = False


        self.finestra_era = self.root.winfo_geometry()
        self.root.after(150, lambda: self.sorveglia(top))

    def sorveglia(self, top):
        if self.top is not top or not top.winfo_exists():
            return
        try:


            if self.root.winfo_geometry() != getattr(self, "finestra_era", None):
                self.chiudi()
                return
            fuoco = self.root.focus_get()
            if fuoco is not None:
                self.aveva_fuoco = True
            elif getattr(self, "aveva_fuoco", False):
                self.chiudi()
                return
            x, y = top.winfo_pointerxy()
        except (tk.TclError, KeyError):
            self.chiudi()
            return

        def sopra(w):
            try:
                return (w is not None and w.winfo_exists()
                        and w.winfo_rootx() <= x < w.winfo_rootx() + w.winfo_width()
                        and w.winfo_rooty() <= y < w.winfo_rooty() + w.winfo_height())
            except tk.TclError:
                return False
        if sopra(top) or sopra(self.chi):
            self.fuori_da = None
        elif self.altrove and self.altrove(x, y, subito=True):
            return
        elif self.fuori_da is None:
            self.fuori_da = time.time()
        elif time.time() - self.fuori_da > 2.0:
            self.chiudi()
            return
        self.root.after(120, lambda: self.sorveglia(top))

    def prendi(self, top):
        if self.top is not top or not top.winfo_exists():
            return
        try:
            top.focus_set()
            top.grab_set()
        except tk.TclError:
            pass

    def accendi(self, riga, a, b, si):
        c = SCELTO if si else PANNELLO
        for w in (riga, a, b):
            w.config(bg=c)
        b.config(fg="#ffffff" if si else TESTO)

    def scegli(self, cosa):
        self.chiudi()
        self.root.after(40, cosa)

    def fuori(self, ev):
        """Un clic fuori dalla tendina la chiude (col grab arriva a lei)."""
        t = self.top
        if t is None:
            return
        if not (0 <= ev.x < t.winfo_width() and 0 <= ev.y < t.winfo_height()):
            altrove = self.altrove
            self.chiudi()
            if altrove:


                self.root.after(60, lambda: altrove(ev.x_root, ev.y_root))

    def chiudi(self):
        if self.top is None:
            return
        top, self.top = self.top, None
        try:
            top.grab_release()
        except tk.TclError:
            pass


        self.root.after_idle(lambda: top.winfo_exists() and top.destroy())
        f, self.al_chiudere = self.al_chiudere, None

        self.chiusa = (self.chi, time.time())
        self.chi = None
        if f:
            f()


class Elenco(tk.Text):
    """L'elenco di sinistra, fatto con un Text: ogni voce e' due righe,
    il pallino col nome e sotto il sottotitolo (programma in onda o
    artista) del colore del pallino. Espone quel poco dell'interfaccia
    della Treeview che il resto dell'app usa: insert, item, delete,
    exists, get_children, selection, selection_set, see..."""

    def __init__(self, dove, **kw):
        tk.Text.__init__(self, dove, bg=PANNELLO, fg=TESTO, bd=0, highlightthickness=0,
                         cursor="arrow", wrap="none", spacing1=0, spacing3=0,
                         insertwidth=0, padx=0, takefocus=1,
                         font=(CARATTERE, -NOME_PX), **kw)

        self.rientro = tk.PhotoImage(width=10, height=1)
        self.rientro_zero = tk.PhotoImage(width=8, height=1)
        self.stacco = tk.PhotoImage(width=1, height=12)


        self.colonna = tk.Canvas(self, bg=PANNELLO, highlightthickness=0, bd=0,
                                 width=COLONNA, cursor="arrow")
        self.colonna_dopo = None
        self.prog_di = None
        self.barre = []
        self.poster_h = 0
        self.poster_onda_h = 0
        self.poster_di = None
        self.desc_di = None
        self.fisici = set()
        self.larg_desc = 0
        self.font_desc = None
        self.poster_lbl = []
        self.singolo = None
        self.config(yscrollcommand=lambda *a: self.ridisegna_colonna())
        self.bind("<Configure>", lambda e: self.ridisegna_colonna(), add="+")
        self.bind("<Configure>", self._larghezza, add="+")
        self.colonna.bind("<Button-1>", lambda e: self.clic_y(e.y, attiva=True))
        self.colonna.bind("<Button-4>", lambda e: self.yview_scroll(-3, "units"))
        self.colonna.bind("<Button-5>", lambda e: self.yview_scroll(3, "units"))
        self.colonna.bind("<MouseWheel>", lambda e: self.yview_scroll(-3 if e.delta > 0 else 3, "units"))
        self.tag_configure("stacco", font=(CARATTERE, -1), spacing1=0, spacing3=0)
        self.corti = {}

        self.bindtags((str(self), str(self.winfo_toplevel()), "all"))
        self.voci = []
        self.posto = {}
        self.dati = {}
        self.scelta = None
        self.tag_configure("scelto", background=SCELTO, foreground="#ffffff")

        self.tag_configure("zebra", background=ZEBRA)
        self.tag_lower("zebra")


        self.nome_sopra = 10
        self.tag_configure("nome", foreground="#ffffff", spacing1=self.nome_sopra, spacing3=4,
                           font=(CARATTERE, -NOME_PX, "bold"))
        self.tag_configure("sotto", spacing1=0, spacing3=14, font=(CARATTERE, -SOTTO_PX))
        self.tag_configure("descrizione", foreground=GRIGIO, spacing1=0, spacing2=0, spacing3=14,
                           wrap="word", lmargin1=COLONNA, lmargin2=COLONNA, rmargin=8,
                           font=(CARATTERE, -SOTTO_PX))
        self.tag_configure("onda")
        self.tag_raise("onda")
        self.tag_raise("scelto")
        self.tag_configure("sopra", background=SCELTO, foreground="#ffffff")
        self.tag_raise("sopra")
        self.sopra = None
        self.ultimo = None
        self._guardia_id = None
        self.al_attivita = None
        self.bind("<Motion>", lambda e: self.passa_y(e.y))
        self.bind("<Leave>", self.esci_sopra)
        self.colonna.bind("<Motion>", lambda e: self.passa_y(e.y))
        self.colonna.bind("<Leave>", self.esci_sopra)
        self.bind("<Button-1>", self.clic)
        self.bind("<Button-4>", lambda e: self.yview_scroll(-3, "units"))
        self.bind("<Button-5>", lambda e: self.yview_scroll(3, "units"))
        self.bind("<MouseWheel>", lambda e: self.yview_scroll(-3 if e.delta > 0 else 3, "units"))
        self.bind("<Up>", lambda e: self.sposta(-1))
        self.bind("<Down>", lambda e: self.sposta(+1))
        self.tinte = set()
        self.segno = "logo"
        self.rientro_dot = tk.PhotoImage(width=PUNTO + 6, height=1)


    def column(self, *a, **k):
        pass

    def get_children(self, *a):
        return tuple(self.voci)

    def exists(self, iid):
        return iid in self.dati

    def insert(self, parent, index, iid=None, text="", image=None, tags=(), colore=None,
               logo=None):
        if iid is None:
            iid = str(len(self.voci))
        self.dati[iid] = dict(text=text, image=image, tags=tuple(tags), colore=colore,
                              logo=logo, img=None)
        self.posto[iid] = len(self.voci)
        self.voci.append(iid)
        self.disegna(iid)
        return iid

    def delete(self, *iids):
        if not iids:
            return
        if set(iids) >= set(self.voci):
            tk.Text.delete(self, "1.0", "end")
            self.voci, self.dati, self.scelta, self.posto = [], {}, None, {}
            self.sopra = None
            self.fisici.clear()
            self.ridisegna_colonna()
        else:
            for iid in iids:
                if iid in self.dati:
                    r = self.riga(iid)
                    tk.Text.delete(self, "%d.0" % r, "%d.0" % (r + self._n(iid)))
                    self.fisici.discard(iid)
                    self.voci.remove(iid)
                    del self.dati[iid]
                    if self.scelta == iid:
                        self.scelta = None
            self.posto = {v: k for k, v in enumerate(self.voci)}

    def item(self, iid, option=None, **kw):
        d = self.dati[iid]
        if option:
            return d.get(option)
        if not kw:
            return dict(d)
        for k, v in kw.items():
            d[k] = tuple(v) if k == "tags" else v
        self.disegna(iid)
        return None

    def selection(self):
        return (self.scelta,) if self.scelta in self.dati else ()

    def selection_set(self, iid, vedi=False):
        """La voce scelta. Il grigio della selezione si vede solo quando ci
        si muove con la tastiera (vedi=True): col mouse no, se no fra
        grigio, argento e colori e' un casino."""
        if self.scelta in self.dati:
            self.tag_remove("scelto", *self.spanne(self.scelta))
        self.scelta = iid if iid in self.dati else None
        if self.scelta:
            self.ultimo = self.scelta
        if self.scelta and vedi:
            self.tag_add("scelto", *self.spanne(self.scelta))
            self.tag_raise("scelto")
        self.ridisegna_colonna()
        self.event_generate("<<TreeviewSelect>>")

    def selection_remove(self, iid):
        if self.scelta == iid:
            self.selection_set(None)

    def see(self, iid):
        if iid in self.dati:
            tk.Text.see(self, "%d.0" % (self.riga(iid) + 1))
            tk.Text.see(self, "%d.0" % self.riga(iid))


    def _n(self, iid):
        """Righe di testo della voce: 3, 4 se ha la riga della descrizione."""
        return 4 if iid in self.fisici else 3

    def riga(self, iid):
        p = self.posto[iid]
        return 3 * p + 1 + sum(1 for k in self.fisici if self.posto.get(k, p) < p)

    def _dove(self, r):
        """Dalla riga di testo: (indice della voce, True se e' la riga descrizione)."""
        r0, spost = r - 1, 0
        for p in sorted(self.posto[k] for k in self.fisici if k in self.posto):
            inizio = 3 * p + spost
            if r0 < inizio:
                break
            if r0 < inizio + 4:
                return p, r0 == inizio + 2
            spost += 1
        return (r0 - spost) // 3, False

    def _visibili(self, su, giu):
        return range(max(0, self._dove(su)[0]), min(len(self.voci), self._dove(giu)[0] + 1))

    def _due_righe(self, testo):
        """La descrizione accorciata in modo che stia in due righe."""
        larg = (self.winfo_width() if self.winfo_width() > 50 else 380) - COLONNA - 28
        if self.font_desc is None:
            self.font_desc = tkfont.Font(root=self, font=(CARATTERE, -SOTTO_PX))
        m = self.font_desc.measure
        righe, cur, taglio = [], "", False
        for w in testo.split():
            prova = (cur + " " + w) if cur else w
            if m(prova) <= larg or not cur:
                cur = prova
            elif len(righe) < 3:
                righe.append(cur)
                cur = w
            else:
                taglio = True
                break
        righe.append(cur)
        if taglio:
            ult = righe[-1]
            while ult and m(ult + "\u2026") > larg:
                ult = ult.rsplit(" ", 1)[0] if " " in ult else ult[:-1]
            righe[-1] = ult + "\u2026"
        return " ".join(righe)

    def aggiorna_desc(self):
        """La riga descrizione della voce in onda, se e' cambiata."""
        if self.desc_di is None or self.segno != "logo":
            return
        for iid, d in list(self.dati.items()):
            if "onda" in d["tags"] and (self.desc_di(iid) or "") != d.get("desc", ""):
                self.disegna(iid)

    def _larghezza(self, e):
        if self.fisici and abs(e.width - self.larg_desc) > 6:
            self.after_idle(lambda: [self.disegna(i) for i in list(self.fisici) if i in self.dati])
        self.larg_desc = e.width

    def spanne(self, iid):
        r = self.riga(iid)
        return ("%d.0" % r, "%d.0" % (r + 2))

    def corto(self, img):
        """Il pallino senza la trasparenza sopra e sotto (alto 14 px invece
        di 24): cosi' la riga del nome non e' piu' alta del testo e il
        sottotitolo ci sta attaccato."""
        k = str(img)
        if k not in self.corti:
            try:
                c = tk.PhotoImage(width=PUNTO, height=14)
                c.tk.call(c, "copy", k, "-from", 0, 5, PUNTO, 19)
                self.corti[k] = c
            except tk.TclError:
                self.corti[k] = img
        return self.corti[k]

    def disegna(self, iid):
        d = self.dati[iid]
        r = self.riga(iid)
        testo = d["text"] or ""
        nome, _sep, sotto = testo.partition("\n")
        if d["img"] is not None:

            tk.Text.delete(self, "%d.0" % r, "%d.0" % (r + self._n(iid)))
            self.fisici.discard(iid)


        colore = d["colore"] or GRIGIO
        tinta = "c" + colore
        if tinta not in self.tinte:
            self.tinte.add(tinta)


            if self.segno == "bordo":
                self.tag_configure(tinta, foreground=colore, lmargin1=3, lmargin2=3,
                                   lmargincolor=colore)
            elif self.segno == "logo":
                self.tag_configure(tinta, foreground=colore, lmargin1=COLONNA,
                                   lmargin2=COLONNA, lmargincolor=PANNELLO)
            else:
                self.tag_configure(tinta, foreground=colore, lmargin1=0, lmargin2=0)
            self.tag_lower(tinta, "zebra")
        zebra = ("zebra",)
        extra = (tuple(d["tags"]) + (("scelto",) if self.scelta == iid else ())
                 + (("sopra",) if self.sopra == iid else ()))


        desc = ""
        if self.desc_di is not None and self.segno == "logo" and "onda" in d["tags"]:
            desc = self.desc_di(iid) or ""
        d["desc"] = desc
        tk.Text.insert(self, "%d.0" % r, "\n", ("stacco",))
        if desc:
            tk.Text.insert(self, "%d.0" % r, self._due_righe(desc) + "\n", ("descrizione", "zebra", "onda"))
        if self.segno == "logo":

            nome, sotto = nome.lstrip(" "), sotto.lstrip(" ")
            tk.Text.insert(self, "%d.0" % r, (sotto or " ") + "\n", (tinta, "sotto") + zebra + extra)
            tk.Text.insert(self, "%d.0" % r, nome + "\n", (tinta, "nome") + zebra + extra)
        else:
            tk.Text.insert(self, "%d.0" % r, " " + (sotto or " ") + "\n", (tinta, "sotto") + zebra + extra)
            tk.Text.insert(self, "%d.0" % r, " " + nome + "\n", (tinta, "nome") + zebra + extra)


        if self.segno == "dot" and d.get("image") is not None:
            self.image_create("%d.0" % r, image=self.corto(d["image"]), align="bottom", padx=4)
            sotto_img = self.rientro_dot
        else:
            rientro = self.rientro_zero if self.segno == "logo" else self.rientro
            self.image_create("%d.0" % r, image=rientro)
            sotto_img = rientro
        self.tag_add(tinta, "%d.0" % r); self.tag_add("nome", "%d.0" % r)
        if self.poster_onda_h and not self.poster_h and "onda" in d["tags"]:
            self.tag_add("pronda", "%d.0" % r)
        self.image_create("%d.0" % (r + 1), image=sotto_img)
        self.tag_add(tinta, "%d.0" % (r + 1)); self.tag_add("sotto", "%d.0" % (r + 1))
        rs = r + 3 if desc else r + 2
        self.image_create("%d.0" % rs, image=self.stacco)
        self.tag_add("stacco", "%d.0" % rs)
        if desc:
            self.fisici.add(iid)
        if self.sopra == iid:
            self.tag_add("sopra", *self.spanne_sopra(iid))
        for t in zebra + extra:
            self.tag_add(t, "%d.0" % r); self.tag_add(t, "%d.0" % (r + 1))
        d["img"] = True
        self.ridisegna_colonna()

    def ridisegna_colonna(self):
        if self.colonna_dopo is None:
            self.colonna_dopo = self.after_idle(self._colonna)

    def _colonna(self):
        self._colonna_loghi()
        self._barre()
        self._poster()

    def metti_poster(self, h):
        """Lo spazio sopra al nome di ogni voce per il poster (0 = nessuno)."""
        if h == self.poster_h:
            return
        self.poster_h = h
        self.tag_configure("nome", spacing1=self.nome_sopra + (h + 3 if h else 0))
        self.ridisegna_colonna()

    def metti_poster_onda(self, h):
        """Lo spazio sopra al nome della sola riga in onda (0 = nessuno)."""
        if h == self.poster_onda_h:
            return
        self.poster_onda_h = h
        self.tag_configure("pronda", spacing1=self.nome_sopra + (h + 3 if h else 0))
        self.tag_raise("pronda")
        for iid, d in list(self.dati.items()):
            if "onda" in d["tags"]:
                self.disegna(iid)
        self.ridisegna_colonna()

    def _nuovo_poster(self):
        e = tk.Label(self, bd=0, highlightthickness=0, bg=PANNELLO, fg=GRIGIO,
                     cursor="arrow", font=(CARATTERE, -SOTTO_PX), padx=6)
        e.image = None
        e.bind("<Button-1>", lambda ev, w=e: self.clic_y(w.winfo_y() + w.winfo_height() + 6))
        e.bind("<Motion>", lambda ev, w=e: self.passa_y(w.winfo_y() + w.winfo_height() + 6))
        e.bind("<Double-Button-1>", lambda ev: self.doppio() if self.doppio else None)
        e.bind("<Button-4>", lambda ev: self.yview_scroll(-3, "units"))
        e.bind("<Button-5>", lambda ev: self.yview_scroll(3, "units"))
        e.bind("<MouseWheel>", lambda ev: self.yview_scroll(-3 if ev.delta > 0 else 3, "units"))
        return e

    def _poster(self):
        """Sopra al nome di ogni voce che si vede, l'immagine del programma
        in onda (o, se manca, il suo titolo), nello spazio lasciato apposta."""
        usati, h = 0, self.poster_h
        if (h or self.poster_onda_h) and self.poster_di is not None and self.voci:
            try:
                su = int(self.index("@0,0").split(".")[0])
                giu = int(self.index("@0,%d" % self.winfo_height()).split(".")[0])
            except (tk.TclError, ValueError):
                su = giu = 0
            larg = self.winfo_width()
            for i in self._visibili(su, giu):
                hh = h or (self.poster_onda_h if "onda" in self.dati[self.voci[i]]["tags"] else 0)
                if not hh:
                    continue
                img, titolo = self.poster_di(self.voci[i], larg - 8, hh)
                if img is None and not titolo:
                    continue
                riga = self.dlineinfo("%d.0" % self.riga(self.voci[i]))
                if riga is None:
                    continue
                if usati >= len(self.poster_lbl):
                    self.poster_lbl.append(self._nuovo_poster())
                e = self.poster_lbl[usati]
                usati += 1
                _n = self.tag_names("%d.0" % self.riga(self.voci[i]))
                e.config(bg=(self.tag_cget("scelto", "background") if ("scelto" in _n or "sopra" in _n)
                             else self.tag_cget("onda", "background") if "onda" in _n else ZEBRA) or ZEBRA)
                if img is not None:
                    e.config(image=img, text="")
                else:
                    e.config(image="", text=titolo)
                e.image = img
                e.place(x=4, y=riga[1] + 3, width=larg - 8, height=hh)
                tk.Misc.lift(e)
        for e in self.poster_lbl[usati:]:
            e.place_forget()

    def _nuova_barra(self):
        pista = tk.Frame(self, bg="#444444", height=2, bd=0, highlightthickness=0, cursor="arrow")
        pieno = tk.Frame(pista, bd=0, highlightthickness=0, bg=GRIGIO)
        for w in (pista, pieno):
            w.bind("<Button-1>", lambda e, b=pista: self.clic_y(b.winfo_y() - 1))
            w.bind("<Button-4>", lambda e: self.yview_scroll(-3, "units"))
            w.bind("<Button-5>", lambda e: self.yview_scroll(3, "units"))
            w.bind("<MouseWheel>", lambda e: self.yview_scroll(-3 if e.delta > 0 else 3, "units"))
        return pista, pieno

    def _barre(self):
        """In fondo a ogni voce coi tempi del programma in onda: una riga
        sottile, del colore della voce, lunga quanto ne e' passato."""
        usate = 0
        if self.prog_di is not None and self.voci:
            try:
                su = int(self.index("@0,0").split(".")[0])
                giu = int(self.index("@0,%d" % self.winfo_height()).split(".")[0])
            except (tk.TclError, ValueError):
                su = giu = 0
            adesso, larg = time.time(), self.winfo_width()
            for i in self._visibili(su, giu):
                iid = self.voci[i]
                parte = self.prog_di(iid)
                if parte is None:
                    continue
                riga = self.dlineinfo("%d.0" % (self.riga(iid) + 2))
                if riga is None:
                    continue
                if usate >= len(self.barre):
                    self.barre.append(self._nuova_barra())
                pista, pieno = self.barre[usate]
                usate += 1
                pieno.config(bg=self.dati[iid].get("colore") or GRIGIO)
                x0 = (COLONNA if self.segno == "logo" else 0) + 8
                pista.place(x=x0, y=riga[1] - 12, width=larg - x0, height=3)
                pieno.place(x=0, y=0, relwidth=parte, relheight=1)
        for pista, _pieno in self.barre[usate:]:
            pista.place_forget()

    def _colonna_loghi(self):
        """I loghi delle voci che si vedono, ognuno centrato sull'altezza
        della sua voce (nome + sottotitolo); dove non c'e' logo, il pallino."""
        self.colonna_dopo = None
        c = self.colonna
        dx = getattr(self, "colonna_dx", None)
        if dx is None:
            dx = self.colonna_dx = tk.Canvas(self, bg=PANNELLO, highlightthickness=0, bd=0,
                                             width=RAGGIO, cursor="arrow")
            dx.bind("<Button-1>", lambda e: self.clic_y(e.y, attiva=True))
            dx.bind("<Motion>", lambda e: self.passa_y(e.y))
            dx.bind("<Leave>", self.esci_sopra)
            dx.bind("<Button-4>", lambda e: self.yview_scroll(-3, "units"))
            dx.bind("<Button-5>", lambda e: self.yview_scroll(3, "units"))
            dx.bind("<MouseWheel>", lambda e: self.yview_scroll(-3 if e.delta > 0 else 3, "units"))
        if self.segno != "logo":
            c.place_forget()
            dx.place_forget()
            return
        c.place(x=0, y=0, width=COLONNA, relheight=1)
        c.delete("all")
        dx.delete("all")
        dx.place(relx=1.0, x=0, y=0, anchor="ne", width=RAGGIO, relheight=1)
        if not self.voci:
            return
        try:
            su = int(self.index("@0,0").split(".")[0])
            giu = int(self.index("@0,%d" % self.winfo_height()).split(".")[0])
        except (tk.TclError, ValueError):
            return
        for i in self._visibili(su, giu):
            iid = self.voci[i]
            r = self.riga(iid)
            if iid in self.fisici:
                d1 = self.dlineinfo("%d.0" % (r + 2))
                d2 = self.dlineinfo("%d.0 lineend" % (r + 2))
                if d1 or d2:
                    c.create_rectangle(0, d1[1] - 1 if d1 else 0, COLONNA,
                                       (d2[1] + d2[3] - 1) if d2 else self.winfo_height(),
                                       fill=(self.tag_cget("sopra", "background") if iid == self.sopra
                                             else self.tag_cget("onda", "background")) or ZEBRA, outline="")
            a, b = self.dlineinfo("%d.0" % r), self.dlineinfo("%d.0" % (r + 1))
            if a is None and b is None:
                continue

            hr = self.poster_h or (self.poster_onda_h if "onda" in self.dati[iid]["tags"] else 0)


            y_logo = (a[1] + (hr + 3 if hr else 0) if a else b[1] - 20) - 1


            y0 = (a[1] if a else b[1] - 20) - 1
            y1 = ((b[1] + b[3]) if b else a[1] + a[3]) - 1
            d = self.dati[iid]
            img = d.get("logo") or (self.corto(d["image"]) if d.get("image") is not None else None)

            nomi = self.tag_names("%d.0" % r)
            fondo = ZEBRA
            if "scelto" in nomi or "sopra" in nomi:
                fondo = self.tag_cget("scelto", "background") or ZEBRA
            elif "onda" in nomi:
                fondo = self.tag_cget("onda", "background") or ZEBRA
            c.create_rectangle(0, y0, COLONNA, y1, fill=fondo, outline="")
            if iid in self.fisici:
                _d1 = self.dlineinfo("%d.0 lineend" % (r + 2))
                if _d1:
                    y1 = _d1[1] + _d1[3] - 1
            dx.create_rectangle(0, y0, RAGGIO, y1, fill=fondo, outline="")
            for _cv, _sx in ((c, True), (dx, False)):
                for _yy, _su in ((y0, True), (y1, False)):
                    _px, _cx = (0, RAGGIO) if _sx else (RAGGIO, 0)
                    _cy = _yy + RAGGIO if _su else _yy - RAGGIO
                    _hx = -1 if _sx else 1
                    _hy = -1 if _su else 1
                    _pt = [(_px, _yy)]
                    for _k in range(9):
                        _t = 1.5707963 * _k / 8
                        _pt.append((_cx + RAGGIO * math.sin(_t) * _hx,
                                    _cy + RAGGIO * math.cos(_t) * _hy))
                    _cv.create_polygon(_pt, fill=PANNELLO, outline="")
            if img is not None:
                c.create_image(COLONNA // 2, (y_logo + y1) // 2, image=img,
                               tags=("lg_" + iid,))

    def clic_y(self, y, attiva=False):
        """Clic singolo sulla colonna dei loghi."""
        if self.al_attivita:
            self.al_attivita()
        self.focus_set()
        try:
            r = int(self.index("@%d,%d" % (COLONNA + 5, y)).split(".")[0])
        except (tk.TclError, ValueError):
            return "break"
        i, sul = self._dove(r)
        if sul:
            return "break"
        if 0 <= i < len(self.voci):
            self.selection_set(self.voci[i])
            if attiva and self.singolo:
                self.singolo()
        return "break"

    def clic(self, ev):
        if self.al_attivita:
            self.al_attivita()
        self.focus_set()
        try:
            r = int(self.index("@%d,%d" % (ev.x, ev.y)).split(".")[0])
        except (tk.TclError, ValueError):
            return "break"
        i, sul = self._dove(r)
        if sul:
            return "break"
        if 0 <= i < len(self.voci):
            self.selection_set(self.voci[i])
            if self.singolo:
                self.singolo()
        return "break"

    def accendi_sopra(self, iid):
        if iid == self.sopra:
            return
        if self.sopra in self.dati:
            self.tag_remove("sopra", *self.spanne_sopra(self.sopra))
        self.sopra = iid
        if iid in self.dati:
            self.tag_add("sopra", *self.spanne_sopra(iid))
            self.tag_raise("sopra")
            if self._guardia_id is None:
                self._guardia_id = self.after(120, self._guardia)
        self.ridisegna_colonna()

    def _guardia(self):
        # Finche' e' acceso, controlla dov'e' il puntatore: se e' uscito si spegne.
        self._guardia_id = None
        if self.sopra is None:
            return
        try:
            x, y = self.winfo_pointerxy()
            w = self.winfo_containing(x, y)
        except (tk.TclError, KeyError):
            w = None
        if w is not None and (w is self or str(w).startswith(str(self) + ".")):
            self._guardia_id = self.after(120, self._guardia)
        else:
            self.accendi_sopra(None)

    def spanne_sopra(self, iid):
        r = self.riga(iid)
        return ("%d.0" % r, "%d.0" % (r + (3 if iid in self.fisici else 2)))

    def passa_y(self, y):
        try:
            r = int(self.index("@%d,%d" % (COLONNA + 5, y)).split(".")[0])
        except (tk.TclError, ValueError):
            return
        i = self._dove(r)[0]
        self.accendi_sopra(self.voci[i] if 0 <= i < len(self.voci) else None)

    def esci_sopra(self, e):
        w = self.winfo_containing(e.x_root, e.y_root)
        if w is not None and str(w).startswith(str(self)):
            return
        self.accendi_sopra(None)

    def sposta(self, dove):
        if self.al_attivita:
            self.al_attivita()
        if not self.voci:
            return "break"
        base = self.scelta if self.scelta in self.dati else self.ultimo
        i = self.voci.index(base) if base in self.dati else -1
        i = max(0, min(len(self.voci) - 1, i + dove))
        self.selection_set(self.voci[i], vedi=True)
        self.see(self.voci[i])
        return "break"


class Finestrella(tk.Toplevel):
    """Una finestrella nostra, scura come i pannelli, centrata sulla
    finestra dell'app, al posto di quelle grigie di Tk. Invio = OK,
    Esc = annulla. Con `chiedi` ha una casella di testo e torna quello
    che si e' scritto (None se si annulla); senza, e' solo da leggere."""

    def __init__(self, root, titolo, corpo, chiedi=None, valore="",
                 ok=None, annulla=None, larga=420, domanda=False,
                 maschera=False):
        tk.Toplevel.__init__(self, root, bg=PANNELLO)
        self.withdraw()
        self.title(titolo)
        self.transient(root)
        self.resizable(False, False)
        self.risposta = None
        dentro = tk.Frame(self, bg=PANNELLO)
        dentro.pack(padx=22, pady=(18, 16))
        corpo(dentro)
        if chiedi is not None:
            tk.Label(dentro, text=chiedi, bg=PANNELLO, fg=GRIGIO, anchor="w"
                     ).pack(fill="x", pady=(10, 4))
            self.casella = tk.Entry(dentro, bg=CERCA, fg=TESTO,
                                    insertbackground=ACCENTO,
                                    show="*" if maschera else "",
                                    relief="flat", bd=0, highlightthickness=1,
                                    highlightbackground=SCELTO, highlightcolor=ACCENTO,
                                    font=(CARATTERE, 10, STILE_CARATTERE), width=max(30, larga // 9))
            self.casella.pack(fill="x", ipady=6)
            self.casella.insert(0, valore)
            self.casella.select_range(0, "end")
        bottoni = tk.Frame(dentro, bg=PANNELLO)
        bottoni.pack(fill="x", pady=(16, 0))

        def bottone(testo, cosa, primario=False):
            b = tk.Button(bottoni, text=testo, command=cosa, relief="flat", bd=0,
                          highlightthickness=0, cursor="hand2", padx=16, pady=5,
                          bg=IN_ONDA if primario else CERCA,
                          fg=TESTO_ONDA if primario else TESTO,
                          activebackground=SCELTO, activeforeground="#ffffff")
            b.pack(side="right", padx=(8, 0))
            return b
        bottone(ok or _("OK"), self.va_bene, primario=True)
        if chiedi is not None or domanda:
            bottone(annulla or _("Cancel"), self.lascia)
        self.bind("<Return>", lambda e: self.va_bene())
        self.bind("<Escape>", lambda e: self.lascia())
        self.protocol("WM_DELETE_WINDOW", self.lascia)

        self.update_idletasks()
        x = root.winfo_rootx() + (root.winfo_width() - self.winfo_reqwidth()) // 2
        y = root.winfo_rooty() + (root.winfo_height() - self.winfo_reqheight()) // 2
        self.geometry("+%d+%d" % (max(0, x), max(0, y)))
        self.deiconify()
        if chiedi is not None:
            self.casella.focus_set()
        else:
            self.focus_set()
        self.grab_set()

    def va_bene(self):
        self.risposta = self.casella.get() if hasattr(self, "casella") else True
        self.chiudi_dopo()

    def lascia(self):
        self.risposta = None
        self.chiudi_dopo()

    def chiudi_dopo(self):
        """Si chiude a evento finito, non dentro al clic sul bottone: col
        grab addosso, distruggerla nel suo stesso evento fa saltare X
        (BadWindow, X_QueryTree)."""
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.after_idle(lambda: self.winfo_exists() and self.destroy())


SOTTOTITOLI = ("srt", "ass", "ssa", "sub", "vtt", "idx", "sup")


def sfoglia(root, titolo, da, file_=True, tipi=None):
    """Sceglie un file (file_) o una cartella, con una finestra nostra al
    posto di quella grigia di Tk: il percorso in cima, su, l'elenco con
    le cartelle prima e poi i file multimediali. Doppio clic su una
    cartella per entrarci, su un file per prenderlo. Torna il percorso o
    None."""
    dove = [da if os.path.isdir(da) else CASA]
    voci = []
    scelto = [None]

    def corpo(dentro):
        tk.Label(dentro, text=_("Choose a file") if file_ else _("Choose a folder"),
                 bg=PANNELLO, fg=TESTO, anchor="w",
                 font=(CARATTERE, 11, "bold", STILE_CARATTERE)).pack(fill="x")
        testa = tk.Frame(dentro, bg=PANNELLO)
        testa.pack(fill="x", pady=(10, 4))
        su = tk.Button(testa, text="\u2191", relief="flat", bd=0, highlightthickness=0,
                       cursor="hand2", padx=10, pady=3, bg=CERCA, fg=TESTO,
                       activebackground=SCELTO, activeforeground="#ffffff")
        su.pack(side="left", padx=(0, 6))
        percorso = tk.Entry(testa, bg=CERCA, fg=TESTO, insertbackground=ACCENTO,
                            relief="flat", bd=0, highlightthickness=1,
                            highlightbackground=SCELTO, highlightcolor=ACCENTO,
                            font=(CARATTERE, 10, STILE_CARATTERE))
        percorso.pack(side="left", fill="x", expand=True, ipady=5)


        riquadro = tk.Frame(dentro, bg=CERCA, width=520, height=PUNTO * 12 + 8)
        riquadro.pack(fill="both", expand=True, pady=(0, 2))
        riquadro.pack_propagate(False)
        elenco = ttk.Treeview(riquadro, show="tree", style="Canali.Treeview",
                              selectmode="browse")
        elenco.pack(fill="both", expand=True, padx=4, pady=4)
        icone[0] = cartellina(GRIGIO, False) or tk.PhotoImage(width=PUNTO, height=PUNTO)
        icone[1] = tk.PhotoImage(width=PUNTO, height=PUNTO)
        cache_anteprime = []

        def riempi():
            del voci[:]
            sel[0] = None
            elenco.delete(*elenco.get_children())
            percorso.delete(0, "end")
            percorso.insert(0, dove[0])
            try:
                nomi = sorted(os.listdir(dove[0]), key=str.lower)
            except Exception:
                nomi = []
            nomi = [n for n in nomi if not n.startswith(".")]
            for n in nomi:
                p = os.path.join(dove[0], n)
                if os.path.isdir(p):
                    elenco.insert("", "end", iid=str(len(voci)), text=" " + n, image=icone[0])
                    voci.append((p, True))
            if file_:
                for n in nomi:
                    p = os.path.join(dove[0], n)
                    if os.path.isfile(p) and (
                            os.path.splitext(n)[1][1:].lower() in tipi if tipi else e_media(n)):


                        icona_corrente = icone[1]
                        estensione = os.path.splitext(n)[1][1:].lower()


                        if 'Image' in globals() and 'ImageTk' in globals() and estensione in ("jpg", "jpeg", "png", "webp", "bmp", "svg"):
                            try:
                                if estensione == "svg":
                                    import subprocess
                                    import tempfile


                                    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                                        tmp_name = tmp.name


                                    subprocess.run(
                                        ["rsvg-convert", "-w", str(PUNTO), "-h", str(PUNTO), p, "-o", tmp_name],
                                        check=True,
                                        stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL
                                    )

                                    img = Image.open(tmp_name)
                                    img.load()
                                    os.unlink(tmp_name)
                                else:
                                    img = Image.open(p)
                                    img.thumbnail((PUNTO, PUNTO))

                                foto_min = ImageTk.PhotoImage(img)
                                cache_anteprime.append(foto_min)
                                icona_corrente = foto_min
                            except Exception:
                                pass

                        elenco.insert("", "end", iid=str(len(voci)), text=" " + n, image=icona_corrente)
                        voci.append((p, False))

        def vai(p):
            if os.path.isdir(p):
                dove[0] = os.path.abspath(p)
                riempi()

        def entra(ev=None):
            s = elenco.selection()
            if not s:
                return "break"
            p, cartella = voci[int(s[0])]
            if cartella:
                vai(p)
            else:
                scelto[0] = p
                finestra[0].va_bene()
            return "break"

        su.config(command=lambda: vai(os.path.dirname(dove[0])))
        percorso.bind("<Return>", lambda e: (vai(os.path.expanduser(percorso.get())), "break")[1])
        elenco.bind("<Double-Button-1>", entra)
        elenco.bind("<Return>", entra)
        elenco.bind("<BackSpace>", lambda e: vai(os.path.dirname(dove[0])))


        elenco.bind("<<TreeviewSelect>>", lambda e: sel.__setitem__(
            0, voci[int(elenco.selection()[0])] if elenco.selection() else None))
        riempi()


        root.after(50, lambda: elenco.winfo_exists() and elenco.focus_set())

    sel, icone = [None], {}
    finestra = [None]
    finestra[0] = Finestrella(root, titolo, corpo, ok=_("Open"), annulla=_("Cancel"),
                              domanda=True)
    root.wait_window(finestra[0])
    if not finestra[0].risposta:
        return None
    if scelto[0]:
        return scelto[0]
    if sel[0]:
        p, cartella = sel[0]
        if file_ and not cartella:
            return p
        if not file_ and cartella:
            return p
    return None if file_ else dove[0]


def installa_demo(cfg):
    if os.path.isfile(CACHE_DEMO_LOCK):
        return False

    os.makedirs(CARTELLA_DEMO, exist_ok=True)
    os.makedirs(CACHE_VARIANTE, exist_ok=True)

    temporanei = []

    try:
        for url, destinazione in (
            (URL_DEMO_M3U, DEMO_M3U),
            (URL_DEMO_XML, DEMO_XML)
        ):
            richiesta = Request(url, headers={"User-Agent": UA})
            dati = urlopen(richiesta, timeout=20).read()

            if destinazione == DEMO_M3U:
                if b"#EXTM3U" not in dati[:4096]:
                    raise ValueError("Playlist demo non valida")
                if b"bigbuckbunny.demo" not in dati:
                    raise ValueError("ID playlist demo non valido")
            else:
                radice = ET.fromstring(dati)
                ids = {c.get("id") for c in radice.findall("channel")}
                if not any(
                    p.get("channel") in ids
                    for p in radice.findall("programme")
                ):
                    raise ValueError("Associazione XMLTV non valida")

            temporaneo = destinazione + ".part"
            temporanei.append(temporaneo)

            with open(temporaneo, "wb") as f:
                f.write(dati)

            os.replace(temporaneo, destinazione)

        liste = cfg.get("liste")
        if not isinstance(liste, list):
            liste = []
            cfg["liste"] = liste

        if DEMO_M3U not in liste:
            liste.append(DEMO_M3U)

        epg = cfg.get("epg")
        if not isinstance(epg, list):
            epg = [epg] if isinstance(epg, str) and epg else []
            cfg["epg"] = epg

        if DEMO_XML not in epg:
            epg.append(DEMO_XML)

        scrivi_config(cfg)

        verificata = leggi_config()
        if DEMO_M3U not in verificata.get("liste", []):
            raise OSError("Configurazione playlist non salvata")
        if DEMO_XML not in verificata.get("epg", []):
            raise OSError("Configurazione guida non salvata")

        temporaneo_lock = CACHE_DEMO_LOCK + ".part"
        with open(temporaneo_lock, "w", encoding="utf-8") as f:
            f.write("XVB DEMO INSTALLED")

        os.replace(temporaneo_lock, CACHE_DEMO_LOCK)
        return True

    except Exception as errore:
        for temporaneo in temporanei:
            try:
                os.remove(temporaneo)
            except OSError:
                pass

        sys.stderr.write("XVB demo: %s\\n" % errore)
        return False


class TV(object):
    def __init__(self, lista=None, da_aprire=None, porta=None):
        self.cfg = leggi_config()
        LISTE_NASCOSTE.update(os.path.abspath(x) for x in self.cfg.get("liste_nascoste", []))
        global LINGUA


        if self.cfg.get("lingua") in dict(LINGUE):
            LINGUA = self.cfg["lingua"]
        else:
            LINGUA = lingua_sistema()

        if self.cfg.get("url_aperti"):
            for u in self.cfg["url_aperti"]:
                self.cfg.get("nomi_url", {}).pop(u, None)
            self.cfg["url_aperti"] = []

        e = self.cfg.get("epg")
        self.cfg["epg"] = [e] if isinstance(e, str) and e else (e if isinstance(e, list) else [])


        for c in CARTELLE_LISTE:
            try:
                os.makedirs(c, exist_ok=True)
            except Exception:
                pass
        installa_demo(self.cfg)
        self.canali, self.visti, self.liste = [], [], []
        self.varianti, self.quale = [], -1
        self.manuale = True
        self.storia, self.in_calo = [], 0
        self.nome, self.nome_in_onda, self.precedente = "", "", None
        self.epg_nomi, self.epg, self.ide_in_onda = {}, {}, None
        self.nascosti = {}
        self.riga_mostrata = ("", "", "")
        self.da_riallineare, self.suonato_da = False, 0.0
        self.registrando, self.fine_rec = "", 0.0
        self.inizio_rec = 0.0
        self.nuova, self.pagina_nuova = "", ""
        self.velocita = 1
        self.loop_ab_a = None
        self.loop_ab_b = None
        self.loop_ab_file = None
        self.loop_ab_in_corso = False
        self.timer_spegnimento = None
        self.file_in_onda = None
        self.iid_reg, self.reg, self.reg_in_uso = {}, [], None
        self.iid_media, self.media_in_uso = {}, None
        self.ordine_vero = []
        self.posto_file = {}
        self.sfondo_su = False
        self.tag_file = {}
        # Cache persistente file -> artista: evita di rileggere i tag ad ogni brano.
        self._xvb_artisti_file = leggi_json(CACHE_ARTISTI_INDEX)
        if not isinstance(self._xvb_artisti_file, dict):
            self._xvb_artisti_file = {}
        self._xvb_artisti_lock = threading.RLock()
        self._xvb_artisti_dirty = 0
        self._xvb_artisti_mem = {}
        self._xvb_render_cache = {}
        self.info = leggi_json(FILE_INFO)
        self.loghi_img = {}
        self._disco = {"url": None, "ang": 0.0, "base": None, "img": None}
        self.anteprime = {}
        self.riprendi_da = 0.0
        self.percorso_mpv = None
        self.dischi_visti = []
        self.tag_cache = leggi_tag()
        self.icona_finestra, self.icona_file = None, ""
        self.piano = None
        self.ultima_discesa = 0.0
        self.tranquillo_da = time.time()
        self.affanni = []

        self.root = tk.Tk(className="xvb")
        self.root.report_callback_exception = _xvb_eccezione_tk
        imposta_font_globale(self.root)
        self.root.title("XVB v" + VERSIONE)
        self.root.after(500, self._giradisco)


        for p in (os.path.join(QUI, "icon.png"),
                  "/usr/share/icons/hicolor/512x512/apps/xvb.png"):
            if os.path.isfile(p):
                try:
                    self.icona_finestra = tk.PhotoImage(file=p)
                    self.icona_file = p
                    self.root.iconphoto(True, self.icona_finestra)
                except tk.TclError:
                    continue
                break


        self.vuoto = tk.PhotoImage(width=PUNTO, height=PUNTO)
        self.pallini = {c: pallino(c) for c in PALLINI}
        self.colori = leggi_colori()
        self.coda_colori = []
        self.in_coda = set()
        threading.Thread(target=self.lavora_colori, daemon=True).start()
        self.cartelle = {}
        self.iid_di = {}
        self.cartella_di = {}
        self.root.geometry("1280x720")
        self.root.configure(bg=FONDO)
        f = os.path.join(QUI, "icon.png")
        if os.path.isfile(f):
            try:
                self.icona = tk.PhotoImage(file=f)
                self.root.iconphoto(True, self.icona)
            except Exception:
                pass


        self.icone, self.icone_pil = {}, {}
        self.vestiti = {}
        for n in ("play", "pausa", "repeat", "repeat_one", "playlist", "favorite_on", "favorite_off",
                  "rec", "rec_stop",
                  "volume", "volume_high",
                  "volume_low", "volume_off", "muto", "pieno", "prima", "dopo",
                  "aperto", "chiuso", "setting", "epg", "shuffle", "stop", "pip", "timer",
                  "cuffie", "cuffie_bluetooth"):
            p = os.path.join(QUI, "icone", n + ".png")
            if os.path.isfile(p):
                if HA_PIL:
                    try:
                        self.icone_pil[n] = Image.open(p).convert("RGBA")
                    except Exception:
                        pass
                try:
                    self.icone[n] = tk.PhotoImage(file=p)
                except Exception:

                    try:
                        self.icone[n] = ImageTk.PhotoImage(
                            Image.open(p).convert("RGBA"))
                    except Exception:
                        pass

        if HA_PIL and "aperto" in self.icone_pil:
            self.icone_pil["aperto_su"] = self.icone_pil["aperto"].rotate(180, resample=Image.BICUBIC)
            self.icone["aperto_su"] = ImageTk.PhotoImage(self.icone_pil["aperto_su"])

        if HA_PIL:
            dimensioni_icone = {
                "play": (34, 34),
                "pausa": (34, 34),
                "stop": (24, 24),
                "prima": (34, 34),
                "dopo": (34, 34),
            }
            for n, dimensione in dimensioni_icone.items():
                if n in self.icone_pil:
                    im = self.icone_pil[n]
                    im = im.resize(dimensione, Image.LANCZOS)
                    self.icone_pil[n] = im
                    self.icone[n] = ImageTk.PhotoImage(im)


        if "epg" not in self.icone:
            self.icona_testo("epg", "EPG")
        for v in VELOCITA:
            self.icona_testo("x%g" % v, "x%g" % v)
        self.icona_testo("-10", "-10")
        self.icona_testo("+10", "+10")

        self.icone_mancanti = [n for n in (
            "play", "pausa", "repeat", "repeat_one", "playlist", "favorite_on", "favorite_off",
            "rec", "rec_stop", "volume",
            "volume_high", "volume_low",
            "volume_off", "muto", "pieno", "prima", "dopo", "aperto", "chiuso",
            "setting", "stop") if n not in self.icone]
        if self.icone_mancanti:
            sys.stderr.write("missing icons in %s: %s\n" % (
                os.path.join(QUI, "icone"), ", ".join(self.icone_mancanti)))


        self.cima = tk.Frame(self.root, bg=BARRA, height=0)
        self.stile_menu = dict(bg=MENU, fg=TESTO, activebackground=IN_ONDA,
                               activeforeground="#ffffff", disabledforeground=GRIGIO,
                               selectcolor=ACCENTO, bd=1, relief="solid",
                               activeborderwidth=0, font=(CARATTERE, 10, STILE_CARATTERE))
        self.barra_menu = tk.Menu(self.root, tearoff=0, bg=CIMA, fg="#ffffff", activebackground=MENU,
                                  activeforeground="#ffffff", bd=0, relief="flat",
                                  activeborderwidth=0, font=(CARATTERE, 10, STILE_CARATTERE))
        self.root.config(menu=self.barra_menu)
        self.menu_cima = {}
        self.tendina = Tendina(self.root)
        self.root.bind_class("Entry", "<Button-3>", self.menu_testo)


        self.menu("Media", lambda: [
            (_("Open file..."), self.apri_file),
            (_("Open folder..."), self.apri_cartella_media),
            (_("Open URL..."), self.apri_url),
            None,
            (_("Download posters and covers"), self.scarica_copertine)] + ([
            None,
            (_("Recent"), None)] + [
            ("   " + corto(os.path.basename(f), 48), lambda f=f: self.apri_da_fuori([f]))
            for f in self.recenti()] + [
            (_("Clear recent"), self.pulisci_recenti)] if self.recenti() else []))
        self.menu("Playback", lambda: [
            (_("Play / Pause"), self.pausa),
            (_("Stop"), self.ferma),
            None,
            (_("Sleep timer"), None),
            (_("15 minutes"), lambda: self.imposta_timer_spegnimento(15)),
            (_("30 minutes"), lambda: self.imposta_timer_spegnimento(30)),
            (_("45 minutes"), lambda: self.imposta_timer_spegnimento(45)),
            (_("60 minutes"), lambda: self.imposta_timer_spegnimento(60)),
            (_("90 minutes"), lambda: self.imposta_timer_spegnimento(90)),
            (_("Cancel sleep timer"), self.annulla_timer_spegnimento),
            None,
            (_("Back 10 s"), lambda: self.avanza(-10)),
            (_("Forward 10 s"), lambda: self.avanza(+10)),
            None,
            (_("Set A (loop start)"), self.loop_ab_imposta_a),
            (_("Set B (loop end)"), self.loop_ab_imposta_b),
            (_("Clear A-B loop"), self.loop_ab_azzera),
            None,
            (_("Previous"), lambda: self.salta(-1)),
            (_("Next"), lambda: self.salta(+1)),
            None,
            (("*  " if self.cfg.get("shuffle") else "   ") + _("Shuffle"), self.shuffle),
            None] + [
            (("*  " if self.cfg.get("ripeti", "") == k else "   ") + _(n),
             lambda k=k: self.metti_ripeti(k))
            for k, n in (("", "No repeat"), ("uno", "Repeat one"), ("tutti", "Repeat all"))] + [
            None] + self.voci_capitoli() + [
            (("*  " if v == self.velocita else "   ") + _("Speed x%g") % v,
             lambda v=v: self.metti_velocita(v)) for v in sorted(VELOCITA)])
        self.menu("Video", lambda: [
            (("*  " if self.cfg.get("proporzioni", "-1") == val and not self.cfg.get("riempi")
              else "   ") + _("Aspect %s") % nome, lambda val=val: self.metti_proporzioni(val))
            for nome, val in PROPORZIONI] + [
            (("*  " if self.cfg.get("riempi") else "   ") + _("Fill screen (crop)"), self.riempi),
            None,
            (("*  " if self.cfg.get("deinterlaccia") else "   ") + _("Deinterlace"), self.deinterlaccia),
            (("*  " if self.cfg.get("hwdec", True) else "   ") + _("Hardware decoding"), self.decodifica_hw),
            None,
            (_("Brightness +"), lambda: self.regola("brightness", +10)),
            (_("Brightness -"), lambda: self.regola("brightness", -10)),
            (_("Contrast +"), lambda: self.regola("contrast", +10)),
            (_("Contrast -"), lambda: self.regola("contrast", -10)),
            (_("Saturation +"), lambda: self.regola("saturation", +10)),
            (_("Saturation -"), lambda: self.regola("saturation", -10)),
            (_("Reset picture"), self.azzera_immagine),
            None,
            (_("Screenshot"), self.istantanea)])
        self.menu("Audio", lambda: [
            (("*  " if t["selected"] else "   ") + _("Track: %s") % corto(self.nome_traccia(t), 30),
             lambda i=t["id"]: self.metti_traccia("aid", i))
            for t in self.tracce("audio")] + ([None] if self.tracce("audio") else []) + [
            (("*  " if not any(t["selected"] for t in self.tracce("sub")) else "   ") + _("Subtitles off"),
             lambda: self.metti_traccia("sid", "no"))] + [
            (("*  " if t["selected"] else "   ") + _("Subtitles: %s") % self.nome_traccia(t),
             lambda i=t["id"]: self.metti_traccia("sid", i))
            for t in self.tracce("sub")] + [
            (_("Load subtitles..."), self.carica_sottotitoli if self.file_in_onda else None),
            (_("Search subtitles online..."), self.cerca_sottotitoli_online if self.file_in_onda else None),
            (_("Subtitle delay +100 ms"), lambda: self.ritardo_sottotitoli(+0.1)),
            (_("Subtitle delay -100 ms"), lambda: self.ritardo_sottotitoli(-0.1)),
            (_("Subtitles larger"), lambda: self.scala_sottotitoli(+0.1)),
            (_("Subtitles smaller"), lambda: self.scala_sottotitoli(-0.1)),
            (_("Reset subtitles"), self.azzera_sottotitoli)] + [None] + [
            (("*  " if self.cfg.get("boost") else "   ") + _("Volume boost +50%"), self.boost),
            (("*  " if self.cfg.get("normalizza") else "   ") + _("Normalize loudness"), self.normalizza),
            (_("Audio output device..."), self.scegli_uscita_audio),
            (_("Audio equalizer..."), self.equalizzatore_audio),
            None,
            (_("Delay +100 ms"), lambda: self.ritardo_audio(+0.1)),
            (_("Delay -100 ms"), lambda: self.ritardo_audio(-0.1)),
            (_("Reset delay"), self.azzera_ritardo)])


        self.menu("IPTV", lambda: [
            (_("Playlists"), None),
            (_("Add playlist URL"), self.chiedi_lista_url),
            (_("Open playlists folder"), self.apri_cartella_liste),
            (_("Reload playlists"), self.ricarica_liste),
            None,
            (_("TV guide"), None),
            (_("Programme guide"), self.apri_guida),
            (_("Add guide URL"), self.chiedi_epg),
            (_("Reload guide"), self.ricarica_epg if self.cfg.get("epg") else None),
            None,
            (_("Recording"), None),
            (_("Stop recording") if self.registrando else _("Record now"), self.registra),
            (_("Schedule..."), self.pianifica),
            (_("Cancel schedule"), self.annulla_piano if self.piano else None),
            (_("Open recordings folder"), self.apri_registrazioni)])
        self.menu("View", lambda: [
            (_("Fullscreen"), self.schermo_intero),
            (_("Picture in picture"), self.pip_si_no),
            (_("Split screen") + "  F2", self.split_si_no),
            (("*  " if self.cfg.get("in_cima") else "   ") + _("Always on top"), self.sempre_in_cima),
            None,
            (_("Show list") if self.nascosti.get(self.sinistra) else _("Hide list"),
             lambda: self.nascondi(self.sinistra)),
            (_("Show sidebar") if self.nascosti.get(self.destra) else _("Hide sidebar"),
             lambda: self.nascondi(self.destra)),

            None,
            (_("Language"), None)] + [
            (("*  " if codice == LINGUA else "   ") + nome,
             lambda c=codice: self.cambia_lingua(c)) for codice, nome in LINGUE] + [
            None,
            (_("Buffer"), None)] + [
            (("*  " if k == self.buffer_attuale()[0] else "   ") + "%s  (%s)" % (
                _(n), "%d MiB" % mx if mx < 1024 else "%d GiB" % (mx // 1024)),
             lambda k=k: self.metti_buffer(k)) for k, n, mx, _b, _a, _w in BUFFER])
        self.menu("Help", lambda: [
            (_("About XVB..."), self.informazioni),
            None,
            (_("Check for updates"), self.controlla_versione),
            (_("Download %s") % self.nuova if self.nuova else _("Up to date"),
             self.apri_release if self.nuova else None),
            None,
            (_("Donate"), self.dona)])

        self.sinistra = tk.Frame(self.root, bg=PANNELLO,
                                 width=300)
        self.sinistra.pack(side="left", fill="y")
        self.sinistra.pack_propagate(False)
        self.cerca = Ricerca(self.sinistra, self.filtra, vuota=_("Search"))
        self.cerca.pack(fill="x", padx=10, pady=10)
        st = ttk.Style(self.root)
        st.theme_use("clam")
        st.configure("Canali.Treeview", background=PANNELLO,
                     fieldbackground=PANNELLO, foreground=TESTO,
                     rowheight=RIGA_ELENCO, borderwidth=0, relief="flat")

        st.layout("Canali.Treeview",
                  [("Canali.Treeview.treearea", {"sticky": "nswe"})])


        def mappa(che):
            return [e for e in st.map("Treeview", query_opt=che)
                    if e[:2] != ("!disabled", "!selected")]
        st.map("Canali.Treeview",
               background=[("selected", SCELTO)] + mappa("background"),
               foreground=[("selected", "#ffffff")] + mappa("foreground"))
        self.elenco = Elenco(self.sinistra)
        self.cfg["segno"] = "logo"
        self.elenco.segno = "logo"
        scrivi_config(self.cfg)
        self.elenco.prog_di = self.progresso_di
        self.elenco.poster_di = self.poster_di
        self.elenco.desc_di = self.descrizione_di
        self.elenco.singolo = self.parti
        self.elenco.al_attivita = self.attivita
        self._ultima_att = time.monotonic()
        self._t_inatt = None
        self._att_onda = None
        self._vista_onda = None
        for _ev in ("<Motion>", "<ButtonPress>", "<ButtonRelease>", "<MouseWheel>",
                    "<Button-4>", "<Button-5>", "<KeyPress>"):
            self.root.bind_all(_ev, self.attivita, add="+")
        self.poster_tk, self.poster_pil = {}, {}
        self.elenco.tag_configure("onda", background=IN_ONDA,
                                  foreground=TESTO_ONDA)
        self.elenco.bind("<<TreeviewSelect>>", lambda e: self.non_sul_verde(
            self.elenco, "onda"))

        self.elenco.column("#0", width=200, stretch=True)
        self.elenco.pack(fill="both", expand=True, padx=2, pady=(0, 8))
        self.elenco.bind("<Button-3>", self.menu_elenco)
        self.elenco.colonna.bind("<Button-3>", self.menu_elenco)
        self.elenco.bind("<Return>", lambda e: self.parti())


        self.destra = tk.Frame(self.root, bg=PANNELLO,
                               width=LARGA_BARRA)

        self.maniglia_sx = self.maniglia(self.sinistra, "larga_sx", +1)
        self.maniglia_dx = self.maniglia(self.destra, "larga_dx", -1)
        self.maniglia_sx.pack(side="left", fill="y")
        self.destra.pack(side="right", fill="y")
        self.destra.pack_propagate(False)
        self.maniglia_dx.pack(side="right", fill="y")
        self.et_liste = tk.Label(self.destra, text=_("Playlists"), bg=PANNELLO,
                                 fg=GRIGIO, anchor="w")
        self.et_liste.pack(fill="x", padx=10, pady=(10, 4))

        stile_liste = "Canali.Treeview"
        if "aperto" in self.icone and "chiuso" in self.icone:
            try:

                self.frecce = {}
                for n in ("aperto", "chiuso"):
                    self.frecce[n] = self.icone[n]
                    if HA_PIL:
                        try:
                            im = Image.open(os.path.join(QUI, "icone", n + ".png"))
                            im = im.convert("RGBA").resize((16, 16), Image.LANCZOS)
                            a = im.getchannel("A").point(lambda p: int(p * 0.75))
                            im.putalpha(a)
                            self.frecce[n] = ImageTk.PhotoImage(im)
                        except Exception:
                            pass
                self.vuoto_freccia = tk.PhotoImage(width=1, height=1)
                st.element_create("Liste.Treeitem.indicator", "image",
                                  self.frecce["chiuso"],
                                  ("user2", self.vuoto_freccia),
                                  ("user1", self.frecce["aperto"]),
                                  sticky="w", padding=(2, 0, 4, 0))
                st.layout("Liste.Treeview",
                          [("Liste.Treeview.treearea", {"sticky": "nswe"})])
                st.layout("Liste.Treeview.Item", [
                    ("Treeitem.padding", {"sticky": "nswe", "children": [
                        ("Liste.Treeitem.indicator", {"side": "left", "sticky": ""}),
                        ("Treeitem.image", {"side": "left", "sticky": ""}),
                        ("Treeitem.text", {"sticky": "nswe"})]})])
                st.configure("Liste.Treeview", background=PANNELLO,
                             fieldbackground=PANNELLO, foreground=TESTO,
                             rowheight=PUNTO + 8, borderwidth=0, relief="flat",
                             indent=14)
                st.map("Liste.Treeview",
                       background=[("selected", SCELTO)] + mappa("background"),
                       foreground=[("selected", "#ffffff")] + mappa("foreground"))
                stile_liste = "Liste.Treeview"
            except tk.TclError:
                pass
        self.el_liste = ttk.Treeview(self.destra, show="tree",
                                     style=stile_liste,
                                     selectmode="browse", columns=("x",))
        self.el_liste.column("#0", width=170, stretch=True)

        self.el_liste.column("x", width=26, stretch=False, anchor="center")
        self.el_liste.pack(fill="both", expand=True, padx=2, pady=(0, 8))
        self.el_liste.bind("<Button-1>", self.clic_liste)
        self.el_liste.bind("<Button-3>", self.menu_liste)


        self.el_liste.bind(
            "<Button-1>", self.gestisci_click_sidebar, add="+")
        self.el_liste.bind("<KeyPress>", lambda e: setattr(self, "col_mouse", False), add="+")


        self.el_liste.bind("<Return>", lambda e: self.scegli_lista())
        self.el_liste.bind("<<TreeviewSelect>>", lambda e: (
            getattr(self, "col_mouse", False) and self.scegli_lista(),
            self.non_sul_verde(self.el_liste, "usata"),
            getattr(self, "col_mouse", False) and self.el_liste.selection() and
            self.root.after_idle(lambda: self.el_liste.selection_remove(*self.el_liste.selection()))))


        self.el_liste.tag_configure("sotto", background=ZEBRA)
        self.el_liste.tag_configure("sopra", background=SCELTO, foreground="#ffffff")
        self._sopra_liste = None
        self.el_liste.bind("<Motion>", self.passa_liste, add="+")
        self.el_liste.bind("<Leave>", lambda e: self.spegni_liste(), add="+")
        self.el_liste.tag_configure("usata", background=IN_ONDA,
                                    foreground=TESTO_ONDA)


        self.img_liste = {}
        self.globi = {}
        self.el_liste.bind("<<TreeviewOpen>>",
                           lambda e: self.apri_chiudi_cartella(True))
        self.el_liste.bind("<<TreeviewClose>>",
                           lambda e: self.apri_chiudi_cartella(False))


        self.riga_stato = tk.Frame(self.root, bg=STATO, height=28)
        DIMENSIONE_TESTO_STATO = 12
        self.riga_stato.pack(side="bottom", fill="x")
        self.riga_stato.pack_propagate(False)
        self.et_orologio = tk.Label(
            self.riga_stato,
            text=time.strftime("%a %d %b  %H:%M"),
            bg=STATO,
            fg=TESTO
        )
        self.et_orologio.pack(side="right", padx=(0, 10), fill="y")


        self.icona_errore = None
        f_err = os.path.join(QUI, "icone", "error.png")
        if os.path.isfile(f_err):
            try:
                if HA_PIL:
                    self.icona_errore = ImageTk.PhotoImage(
                        Image.open(f_err).convert("RGBA").resize((16, 16), Image.LANCZOS))
                else:
                    self.icona_errore = tk.PhotoImage(file=f_err)
            except Exception:
                self.icona_errore = None
        self.livello = tk.Canvas(self.riga_stato, width=40, height=14,
                                 bg=STATO, highlightthickness=0, bd=0)
        self.livello.pack(side="left", padx=(8, 0))
        self.livello_barre = [0.0] * 10
        self.root.after(1000, self.anima_livello)
        self.ic_categoria = tk.Label(self.riga_stato, text="", anchor="w",
                                     bg=STATO)
        self.ic_categoria.pack(side="left", padx=(8, 0), fill="y")
        self.et_categoria = tk.Label(self.riga_stato, text="", anchor="w",
                                     bg=STATO, fg=TESTO)
        self.et_categoria.pack(side="left", fill="y")
        self.stato = tk.Label(self.riga_stato, text=_("ready"), anchor="w",
                              bg=STATO, fg=TESTO)
        self.stato.pack(side="left", padx=(8, 0), fill="y")

        self.et_canale = self.stato
        self.et_titolo = tk.Label(self.riga_stato, text="", bg=STATO, fg=TESTO)
        self.et_titolo.pack(side="left", fill="y")
        self.et_ora = tk.Label(self.riga_stato, text="", bg=STATO, fg=TESTO)
        self.et_ora.pack(side="left", fill="y")

        self.et_seguente = tk.Label(self.riga_stato, text="", bg=STATO, fg=TESTO)
        self.et_seguente.pack(side="left", fill="y")
        self.et_titolo_seguente = tk.Label(self.riga_stato, text="", bg=STATO, fg=TESTO)
        self.et_titolo_seguente.pack(side="left", fill="y")

        self.et_formato = tk.Label(self.riga_stato, text="", bg=STATO, fg=TESTO, width=4)
        self.et_formato.pack(side="left", fill="y")
        self.et_inizio = tk.Label(self.riga_stato, text="", bg=STATO, fg=TESTO)
        self.et_ora_seguente = tk.Label(self.riga_stato, text="", bg=STATO, fg=TESTO)
        self.fr_cat = tk.Label(self.riga_stato, text="", bg=STATO)
        self.ic_cat = tk.Label(self.riga_stato, text="", anchor="w", bg=STATO)
        self.et_cat = tk.Label(self.riga_stato, text="", anchor="w",
                               bg=STATO, fg=TESTO)
        self.sep_eq = tk.Label(self.riga_stato, text="    ", bg=STATO, fg=TESTO)
        self.ic_tipo = tk.Canvas(self.riga_stato, width=16, height=14,
                                 bg=STATO, highlightthickness=0, bd=0)
        for w in (self.et_formato, self.livello, self.sep_eq, self.ic_categoria,
                  self.ic_tipo, self.et_categoria, self.stato, self.et_inizio,
                  self.et_titolo, self.et_ora, self.et_seguente,
                  self.et_ora_seguente, self.et_titolo_seguente):
            w.pack_forget()
        self.et_formato.pack(side="left", padx=(8, 0), fill="y")
        self.livello.pack(side="left")
        self.sep_eq.pack(side="left", fill="y")
        self.ic_categoria.pack(side="left", padx=(0, 6))
        self.et_categoria.pack(side="left", fill="y")
        self.fr_cat.pack(side="left", padx=(2, 0))
        self.ic_cat.pack(side="left", padx=(0, 6))
        self.et_cat.pack(side="left", fill="y")
        for w in (self.stato, self.et_inizio, self.et_titolo,
                  self.et_ora, self.et_seguente, self.et_ora_seguente,
                  self.et_titolo_seguente):
            w.pack(side="left", fill="y")
        self.root.after(1200, self.aggiorna_formato)

        self.et_recdot = tk.Label(self.riga_stato, bg=STATO, bd=0)
        self.et_rec = tk.Label(self.riga_stato, text="", bg=STATO, fg="#ff453a")


        font_stato = tkfont.Font(root=self.root, font="TkDefaultFont")
        font_stato.configure(size=DIMENSIONE_TESTO_STATO)

        for widget in self.riga_stato.winfo_children():
            if isinstance(widget, tk.Label):
                widget.configure(font=font_stato)

        self.dot_rec = []

        if HA_PIL:
            r0, g0, b0 = (int(STATO[k:k + 2], 16) for k in (1, 3, 5))
            for i in range(12):
                t = 0.5 + 0.5 * math.cos(2 * math.pi * i / 12)
                c = "#%02x%02x%02x" % (int(r0 + (0xff - r0) * t), int(g0 + (0x45 - g0) * t),
                                       int(b0 + (0x3a - b0) * t))
                self.dot_rec.append(pallino(c, lato=16))
        self.fase_rec = 0
        self.barra = tk.Frame(self.root, bg=BARRA, height=96)


        self.barra.pack_propagate(False)


        self.avviso = tk.Frame(self.root, bg=IN_ONDA2, height=42)
        self.avviso.pack_propagate(False)
        self.et_xvb = tk.Label(self.avviso, text="xvb", bg=IN_ONDA2, fg=ACCENTO,
                               font=(CARATTERE, 13, "bold", STILE_CARATTERE))
        self.et_xvb.pack(side="left", padx=(12, 10), fill="y")
        self.et_avviso = tk.Label(self.avviso, text="", bg=IN_ONDA2, fg=TESTO_ONDA,
                                  anchor="w")
        self.et_avviso.pack(side="left", fill="both", expand=True)
        for w in (self.avviso, self.et_xvb, self.et_avviso):
            w.bind("<Button-1>", lambda e: self.chiudi_avviso())
        self.timer_avviso, self.avviso_chiave = None, (None, 0)


        self.fondo_barra = tk.Canvas(self.barra, bg=BARRA, highlightthickness=0, bd=0)
        self.fondo_barra.place(x=0, y=0, relwidth=1, relheight=1)
        self.colore_barra = None
        self.fondo_barra.bind("<Configure>", lambda e: self.disegna_fondo_barra())


        self.linea = tk.Canvas(self.barra, height=10, bg=BARRA,
                               highlightthickness=0, bd=0)

        self.linea.place(x=0, y=30, relwidth=1, height=10)
        self.pieno_linea = self.linea.create_rectangle(0, 0, 0, 2,
                                                       fill="#ffffff", outline="")
        self.linea.bind("<Configure>", lambda e: self.disegna_fondo_linea())


        self.alta_linea = 2
        self.linea.bind("<Enter>", lambda e: self.linea.config(
            cursor="hand2" if self.file_in_onda else ""))
        self.linea.bind("<Button-1>", self.vai_a)
        self.linea.bind("<B1-Motion>", self.vai_a)
        self.barra.bind("<Button-1>", lambda e: 30 <= e.y < 40 and self.vai_a(e))
        self.root.after(500, self.aggiorna_linea)
        self.root.after(1000, self.controlla_registrazione)
        self.root.after(60000, self.aggiorna_sottotitoli)
        self.root.after(8000, self.controlla_copertine)
        self.root.after(5000, self.guarda_dischi)
        self.mpris, self.arte_mpris = None, {}
        self.root.after(1500, self.avvia_mpris)
        self.root.after(500, self.accetta_trascinati)


        self.b_sidebar = self.tasto("playlist", _("Playlists"), self.sidebar, 8)
        gruppo = [self.tasto("prima", "<", lambda: self.salta(-1), 3, padx=(24, 0)),
                  self.tasto("-10", "-10", lambda: self.avanza(-10), 4, padx=0)]

        self.b_pausa = self.tasto("play", _("Play"), self.pausa, 8, padx=5)
        if self.b_pausa.icona:
            self.b_pausa.config(height=36)
            self.b_pausa.pack_configure(pady=3)
        gruppo += [self.b_pausa,
                   self.tasto("stop", _("Stop"), self.ferma, 4, padx=5),
                   self.tasto("+10", "+10", lambda: self.avanza(+10), 4, padx=0),
                   self.tasto("dopo", ">", lambda: self.salta(+1), 3, padx=0)]

        for b in gruppo:
            if b.icona:
                b.config(width=self.icone[b.icona].width())
        self.b_rec = self.tasto("rec", _("Record"), self.registra, padx=(24, 2))

        self.b_ripeti = self.tasto("repeat", _("No repeat"), self.gira_ripeti, 6)
        self.b_shuffle = self.tasto("shuffle", _("Shuffle"), self.shuffle, 7)


        self.b_pref = self.tasto("favorite_off", _("Favorite"), self.preferito,
                                 lato="right")
        self.b_pieno = self.tasto("pieno", _("Fullscreen"), self.schermo_intero, 14,
                                  lato="right")
        self.b_pref.pack_forget()
        self.tasto("pip", "PiP", self.pip_si_no, 4, lato="right")

        self.b_velocita = self.tasto("x1", "x1", self.gira_velocita, 4, lato="right",
                                     padx=(6, 2))
        self.b_velocita.pack_forget()

        self.b_qualita = self.tasto("setting", _("quality"), self.apri_menu_qualita,
                                    lato="right")
        self.b_qualita.pack_forget()

        self.b_sidebar.pack_forget()
        self.b_sidebar.pack(side="right", padx=2, pady=6, before=self.b_pieno)
        self.volume = Cursore(self.barra, self.alza_volume, larga=85)
        self.volume.pack(side="right", padx=0)
        self.b_audio_menu = self.bottone(self.barra, "", self.apri_menu_uscita_audio)
        self.b_audio_menu.config(image=self.icone.get("aperto"), width=34, height=30,
                                 bg=BARRA, activebackground=BARRA)
        self.b_audio_menu.icona = "aperto"
        self.b_audio_menu.sopra = False
        self.b_audio_menu.bind("<Enter>", lambda e: self.hover_tk(self.b_audio_menu, True), add="+")
        self.b_audio_menu.bind("<Leave>", lambda e: self.hover_tk(self.b_audio_menu, False), add="+")
        self.b_audio_menu.pack(side="right", padx=0, pady=6, before=self.volume)
        self.b_muto = self.tasto("volume", _("Mute"), self.muto, lato="right")


        self.volume_largo, self.volume_dopo, self.volume_animazione = 0, None, None
        self.volume.config(width=0)
        for w in (self.b_muto, self.volume, self.b_audio_menu):
            w.bind("<Enter>", lambda e: self.apri_volume(), add="+")
            w.bind("<Leave>", lambda e: self.chiudi_volume_tra_poco(), add="+")

        self.giri_ingranaggio, self.passo_ingranaggio = [], 0
        self.verso_ingranaggio = 0
        p = os.path.join(QUI, "icone", "setting.png")
        if HA_PIL and os.path.isfile(p):
            try:
                im = Image.open(p).convert("RGBA")
                self.giri_ingranaggio = [
                    ImageTk.PhotoImage(im.rotate(-a, resample=Image.BICUBIC))
                    for a in range(0, 181, 15)]
            except Exception:
                self.giri_ingranaggio = []

        self.video = tk.Frame(self.root, bg="black")
        self.video.pack(side="right", fill="both", expand=True)
        self.split, self.schermo, self.mpv2, self.stato_schermi = False, 0, None, [{}, {}]
        self.cornici = [tk.Frame(self.video, bg="black", bd=0) for _n in range(2)]
        self.cornici[0].place(x=0, y=0, relwidth=1, relheight=1)
        self.vid1 = tk.Frame(self.cornici[0], bg="black", bd=0)
        self.vid1.place(x=0, y=0, relwidth=1, relheight=1)
        self.vid2 = tk.Frame(self.cornici[1], bg="black", bd=0)

        self.disponi(False)
        self.video.bind("<Button-1>", lambda e: self.root.focus_set())
        self.root.update_idletasks()

        self.mpv = mpv.MPV(
            **opzioni_mpv_xwayland(),
            wid=str(self.vid1.winfo_id()),
            cache="yes",
            demuxer_max_bytes="%dMiB" % self.buffer_attuale()[2],
            demuxer_max_back_bytes="%dMiB" % self.buffer_attuale()[3],
            demuxer_readahead_secs=self.buffer_attuale()[4],
            cache_pause_wait=self.buffer_attuale()[5],
            network_timeout=60,
            ytdl=False,
            hwdec="auto-safe",
            force_window="yes",
            volume_max=150,
            screenshot_directory=os.path.join(CASA, "Pictures", "xvb"),
            screenshot_template="xvb-%tY-%tm-%td_%tH-%tM-%tS",
            user_agent=UA,
            cursor_autohide=3000,
            stream_lavf_o=("reconnect=1,reconnect_streamed=1,"
                           "reconnect_on_network_error=1,"
                           "reconnect_delay_max=10"),

        )


        protocolli = "file,http,https,tcp,tls,crypto,data,httpproxy,udp,rtp,rtmp,rtmps,srt"


        try:
            @self.mpv.on_key_press("MBTN_LEFT_DBL")
            def _doppio():
                self.root.after(0, self.doppio_clic)


            try:
                self.mpv["window-dragging"] = "no"
            except Exception:
                pass


            @self.mpv.key_binding("MBTN_LEFT")
            def _clic(stato, *_a):
                if stato.startswith("d"):
                    self.ov_premuto = True
                    self.root.after(0, self.clic_schermo, 0)
                elif stato.startswith("u"):
                    self.ov_premuto = False
        except Exception:
            pass
        self.mpv1 = self.mpv
        for valore in ("embedded-first", "attachment"):
            try:
                self.mpv["audio-display"] = valore
                break
            except Exception:
                continue
        try:
            self.mpv["demuxer-lavf-o"] = "protocol_whitelist=%%%d%%%s" % (
                len(protocolli), protocolli)
        except Exception:
            pass
        self.volume.set(int(self.cfg.get("volume", 85)))
        self.icona_volume()
        self.applica_video()
        self.applica_uscita_audio(silenzioso=True)
        self.tipo_uscita_corrente = self.rileva_tipo_uscita_audio()
        self.icona_volume()
        self.root.after(1000, self.controlla_uscita_audio)
        threading.Thread(target=self.ascolta_eventi_uscita_audio, daemon=True).start()
        if self.cfg.get("in_cima"):
            self.root.attributes("-topmost", True)
        if self.icone_mancanti:
            self.scrivi(_("missing icons: %s") % ", ".join(self.icone_mancanti))


        self.angolo, self.carico = 0, False
        self.giri_carico = []
        p = os.path.join(QUI, "icone", "loading.png")
        if HA_PIL and os.path.isfile(p):
            try:
                im = Image.open(p).convert("RGBA")
                self.giri_carico = [
                    ImageTk.PhotoImage(im.rotate(-a, resample=Image.BICUBIC))
                    for a in range(0, 360, 20)]
            except Exception:
                self.giri_carico = []
        elif os.path.isfile(p):
            try:
                self.giri_carico = [tk.PhotoImage(file=p)]
            except Exception:
                pass
        self.vuoto_carico = tk.PhotoImage(width=1, height=1)
        self.et_carico = tk.Label(self.riga_stato, image=self.vuoto_carico,
                                  bg=STATO, bd=0)
        self.et_carico.pack(side="left", padx=(10, 0), before=self.stato)

        self.sfondo = tk.Label(self.video, bg="black", bd=0)
        self.sfondo.bind("<Double-Button-1>", lambda e: self.doppio_clic())

        self.pip = False
        self.b_pip_esci = tk.Label(self.video, bg="#000000", fg="#ffffff", cursor="hand2",
                                   bd=0, padx=4, pady=4)
        if "pip" in self.icone:
            self.b_pip_esci.config(image=self.icone["pip"])
        else:
            self.b_pip_esci.config(text="\u2922", font=(CARATTERE, 14, STILE_CARATTERE))
        self.b_pip_esci.bind("<Button-1>", lambda e: self.esci_pip())


        self.sotto = tk.Frame(self.video, bg="black", width=1, height=1)
        self.sotto.place(x=0, y=0)
        self.sfondo_file = os.path.join(QUI, "icone", "screen.png")
        self.sfondo_misura, self.sfondo_img, self.sfondo_su = None, None, False


        self.video.bind("<Configure>", lambda e: self.rifai_sfondo_poi())
        self.root.bind("<Map>", lambda e: e.widget is self.root and not getattr(
            self, "_finestra_vista", None) and setattr(self, "_finestra_vista", time.time()),
            add="+")
        self.mostra_sfondo(True, dissolvi=True, attesa=0)
        self.mpv1 = self.mpv
        self.mpv1.observe_property("paused-for-cache",
                                   lambda n, v: self.mpv is self.mpv1 and self._buffer(n, v))
        self.mpv1.observe_property("time-pos",
                                   lambda n, v: self.mpv is self.mpv1 and self._va(n, v))

        self.pieno, self.barra_visibile, self.timer_barra = False, True, None
        self.sorveglio, self.dove_era = False, (0, 0)
        self.mai_partito = True
        self.root.after(100, self.posa_barra)
        try:
            self.mpv.overlay_ids.update((OV_SFONDO, OV_BARRA, OV_PIP, OV_MENU, OV_EPG, OV_HUD))
        except Exception:
            pass
        self.attesa_da, self.salti = 0.0, 0

        try:
            @self.mpv.event_callback("end-file")
            def fine(ev, app=self):
                if app.mpv is app.mpv1:
                    app.finito_male(ev)
        except Exception:
            pass


        def tasto(nome, cosa):
            def f(ev):
                if isinstance(self.root.focus_get(),
                              (tk.Entry, tk.Listbox, ttk.Treeview, tk.Text)):
                    return
                cosa()
            self.root.bind(nome, f)

        tasto("<space>", self.pausa)
        tasto("<F2>", self.split_si_no)
        tasto("<m>", self.muto)
        tasto("<BackSpace>", self.switch)


        tasto("<plus>", lambda: self.ritardo_audio(+0.1))
        tasto("<KP_Add>", lambda: self.ritardo_audio(+0.1))
        tasto("<minus>", lambda: self.ritardo_audio(-0.1))
        tasto("<KP_Subtract>", lambda: self.ritardo_audio(-0.1))
        tasto("<Up>", lambda: self.volume.set(self.volume.get() + 5))
        tasto("<Down>", lambda: self.volume.set(self.volume.get() - 5))
        tasto("<Left>", lambda: self.salta(-1))
        tasto("<Right>", lambda: self.salta(+1))
        tasto("<Shift-Left>", lambda: self.avanza(-10))
        tasto("<Shift-Right>", lambda: self.avanza(+10))

        self.elenco.bind("<Left>", lambda e: self.salta(-1))
        self.elenco.bind("<Right>", lambda e: self.salta(+1))
        self.elenco.bind("<Shift-Left>", lambda e: self.avanza(-10))
        self.elenco.bind("<Shift-Right>", lambda e: self.avanza(+10))
        self.root.bind("<Prior>", lambda e: self.salta(-1))
        self.root.bind("<Next>", lambda e: self.salta(+1))
        self.root.bind("<F11>", self.schermo_intero)
        self.root.bind("<Escape>", lambda e: self.esci_pip() if self.pip else self.schermo_intero(None, False))
        self.root.protocol("WM_DELETE_WINDOW", self.chiudi)

        if lista and os.path.isfile(lista):

            lista = os.path.abspath(lista)
            if lista not in self.tutte_le_liste():
                self.cfg.setdefault("liste", []).append(lista)
            self.rifai_liste(scegli=lista)
        else:


            self.cfg["lista"] = None
            self.rifai_albero()
            self.canali = []
            self.filtra()
            self.scrivi(_("ready"))
        if da_aprire:
            self.root.after(300, lambda: self.apri_da_fuori(da_aprire))


        self.porta = porta
        if porta is not None:
            threading.Thread(target=self.ascolta, daemon=True).start()
        threading.Thread(target=self.guarda, daemon=True).start()
        if self.cfg.get("epg"):
            self.carica_guide()
        self.root.after(30 * 60 * 1000, self.giro_guide)
        self.root.after(60 * 1000, self.giro_liste)

        self.root.after(8000, lambda: self.controlla_versione(zitto=True))


    def bottone(self, dove, testo, cosa):
        return tk.Button(dove, text=testo, command=cosa, bg=TASTO, fg=TESTO,
                         relief="flat", bd=0, highlightthickness=0,
                         activebackground=SCELTO, activeforeground="#ffffff",
                         cursor="hand2")

    def icona_testo(self, chiave, testo):
        """Una scritta corta disegnata come icona (30x24), cosi' sta sulla
        sfumatura senza fondo come le png. Senza PIL non si fa."""
        if not HA_PIL:
            return
        try:
            from PIL import ImageDraw, ImageFont
            try:
                percorso_font = percorso_google_sans("Bold")
                if not percorso_font:
                    raise FileNotFoundError("Manca fonts/static/GoogleSans-Bold.ttf")
                font = ImageFont.truetype(percorso_font, 15 if chiave in ("-10", "+10") else 11)
            except Exception:
                font = ImageFont.load_default()
            larghezza, altezza = (48, 34) if chiave in ("-10", "+10") else (34, 24)
            im = Image.new("RGBA", (larghezza, altezza), (0, 0, 0, 0))
            d = ImageDraw.Draw(im)
            x0, y0, x1, y1 = d.textbbox((0, 0), testo, font=font)
            d.text(((larghezza - (x1 - x0)) // 2 - x0, (altezza - (y1 - y0)) // 2 - y0), testo,
                   fill="#e8e8ef", font=font)
            self.icone_pil[chiave] = im
            self.icone[chiave] = ImageTk.PhotoImage(im)
        except Exception:
            pass

    def tasto(self, icona, testo, cosa, largo=6, lato="left", padx=2):
        """Un bottone della barra; padx=(6, 2) apre un gruppo nuovo, con
        piu' aria a sinistra."""
        b = self.bottone(self.barra, testo, cosa)
        b.icona = None
        if icona in self.icone:


            dimensioni = (48, 40) if icona in ("-10", "+10") else (34, 30)
            b.config(image=self.icone[icona], width=dimensioni[0], height=dimensioni[1], bg=BARRA,
                     activebackground=BARRA)
            b.icona = icona
        else:
            b.config(width=largo)
        b.pack(side=lato, padx=padx, pady=6)
        b.sopra = False
        b.bind("<Enter>", lambda e, b=b: self.hover_tk(b, True), add="+")
        b.bind("<Leave>", lambda e, b=b: self.hover_tk(b, False), add="+")
        return b

    def hover_tk(self, b, si):
        b.sopra = si
        if getattr(b, "icona", None):
            self.vesti(b)


    def tutte_le_liste(self):
        """Quelle nella cartella playlists/ e nelle sue categorie, che si
        caricano da sole, piu' i file aggiunti a mano. Gli avanzi nel file
        di configurazione - liste sparite, o che stanno gia' in playlists/ -
        si tolgono da soli."""
        fuori = liste_in_cartella()
        for nome, dentro in categorie():
            # Esclude la cartella dei preferiti dal conteggio e dalle liste IPTV/M3U
            if nome.lower() != PREFERITI.lower():
                fuori += dentro
        buone = []
        for d in self.cfg.get("liste", []):
            if (not d.startswith("http") and os.path.isfile(d)
                    and d not in fuori
                    and os.path.dirname(os.path.abspath(d))
                    not in CARTELLE_LISTE + CARTELLE_VECCHIE):
                buone.append(d)
        if buone != self.cfg.get("liste", []):
            self.cfg["liste"] = buone
            scrivi_config(self.cfg)
        NOMI_URL.clear()
        NOMI_URL.update(self.cfg.get("nomi_liste", {}))
        NOMI_URL.update(self.cfg.get("liste_url", {}))
        return fuori + buone + list(self.cfg.get("liste_url", {}))

    def rifai_liste(self, scegli=None):
        """Rifa' la barra delle liste e ne apre una: quella chiesta, se no
        quella ricordata (cercata DOPO aver fatto l'elenco, se no non si
        trova), se no la prima."""
        self.rifai_albero()
        if not self.liste:
            self.canali = []
            self.filtra()
            self.scrivi(_("no playlist: put one in playlists/"))
            return
        if not scegli:
            scegli = self.lista_dell_ultimo_canale()
        i = self.trova_lista(scegli)
        if self.liste[i] in self.iid_di:
            self.mostra_riga(self.iid_di[self.liste[i]])
        self.carica(self.liste[i])

    def mostra_riga(self, iid):
        """Scorre la barra fino a quella riga senza aprire le cartelle
        chiuse (see() le aprirebbe): se e' dentro una chiusa, si vede la
        cartella."""
        vista, p = iid, self.el_liste.parent(iid)
        while p:
            if not self.el_liste.item(p, "open"):
                vista = p
            p = self.el_liste.parent(p)
        self.el_liste.see(vista)

    def spegni_liste(self):
        v = self._sopra_liste
        self._sopra_liste = None
        if not v or not self.el_liste.exists(v[0]):
            return
        cur = tuple(self.el_liste.item(v[0], "tags") or ())
        if "sopra" in cur:
            resto = tuple(t for t in cur if t != "sopra")
            self.el_liste.item(v[0], tags=resto or v[1])

    def _guardia_liste(self):
        self._gl_id = None
        if not self._sopra_liste:
            return
        try:
            x, y = self.root.winfo_pointerxy()
            w = self.root.winfo_containing(x, y)
        except (tk.TclError, KeyError):
            w = None
        if w is self.el_liste:
            self._gl_id = self.root.after(120, self._guardia_liste)
        else:
            self.spegni_liste()

    def passa_liste(self, e):
        iid = self.el_liste.identify_row(e.y)
        if self._sopra_liste and self._sopra_liste[0] == iid:
            return
        self.spegni_liste()
        if iid and self.el_liste.exists(iid):
            self._sopra_liste = (iid, tuple(self.el_liste.item(iid, "tags") or ()))
            self.el_liste.item(iid, tags=("sopra",))
            if getattr(self, "_gl_id", None) is None:
                self._gl_id = self.root.after(120, self._guardia_liste)

    def tag_riga(self, iid, usata):
        """I tag di una riga della barra: "usata" se e' quella in uso, e
        "sotto" se sta dentro a una cartella (il fondo grigio chiaro)."""
        if usata:
            self.el_liste.item(iid, tags=("usata",))
        else:
            self.el_liste.item(iid, tags=("sotto",) if self.el_liste.parent(iid) else ())

    def segna_sotto(self, padre=""):
        """Le righe dentro alle cartelle (non quelle in cima) col fondo
        grigio chiaro; i tag che hanno gia' (es. usata) restano."""
        for i in self.el_liste.get_children(padre):
            if padre:
                t = tuple(self.el_liste.item(i, "tags") or ())
                if "sotto" not in t and "usata" not in t:
                    self.el_liste.item(i, tags=("sotto",) + t)
            self.segna_sotto(i)

    def rifai_albero(self):
        """Solo la barra delle liste, senza aprirne nessuna."""
        self.liste = self.tutte_le_liste()
        if not hasattr(self, "_cache_liste_tentate"):
            self._cache_liste_tentate = set()
            self._cache_liste_in_corso = False
        if (not self._cache_liste_in_corso
                and any(d not in self._cache_liste_tentate for d in self.liste)):
            self._cache_liste_in_corso = True
            def prepara_cache_liste():
                try:
                    while True:
                        lavoro = [d for d in tuple(self.liste)
                                  if d not in self._cache_liste_tentate]
                        if not lavoro:
                            break
                        for sorgente_lista in lavoro:
                            self._cache_liste_tentate.add(sorgente_lista)
                            try:
                                leggi_lista(sorgente_lista)
                            except Exception:
                                pass
                        try:
                            self.root.after(0, self.rifai_albero)
                        except Exception:
                            pass
                finally:
                    self._cache_liste_in_corso = False
            threading.Thread(target=prepara_cache_liste, daemon=True).start()
        self.el_liste.delete(*self.el_liste.get_children())
        self.iid_di = {}
        self.iid_epg = {}
        self.iid_categoria_m3u = {}
        self._mappa_categorie_m3u = {}
        self._colore_categoria_m3u = {}
        adesso = self.cfg.get("lista")

        posto = [0]

        def riga(padre, d):
            f = immagine_lista(d)
            if os.path.basename(os.path.dirname(d)) == PREFERITI:
                ico = {PREF_MUSICA: "music", PREF_VIDEO: "player"}.get(
                    os.path.splitext(os.path.basename(d))[0])
                for est in (".png", ".jpg", ".jpeg", ".gif"):
                    g = ico and os.path.join(QUI, "icone", ico + est)
                    if g and os.path.isfile(g):
                        f = g
                        break
            if f and f not in self.img_liste:
                im = carica_logo(f, PUNTO, PUNTO, RIDOTTE)
                if im is not None:
                    self.img_liste[f] = im
            img = self.img_liste.get(f, self.vuoto)
            m3u = bool(f) and os.path.splitext(os.path.basename(f))[0] == "m3u"
            if re.match(r"https?://", d, re.I) and not m3u:


                colore = COLORI_CARTELLE[posto[0] % len(COLORI_CARTELLE)]
                posto[0] += 1
                if colore not in self.globi:
                    self.globi[colore] = globo(colore, dentro=RIDOTTE) or self.vuoto
                img = self.globi[colore]
            chiave_playlist = "m3u-playlist:" + d
            iid = self.el_liste.insert(
                padre, "end", text=" " + nome_vista(d),
                image=img,
                values=("" if os.path.basename(os.path.dirname(d)) == PREFERITI else "\u2715",),
                open=aperta_di(chiave_playlist, False),
                tags=("usata",) if d == adesso else ())
            self.iid_di[d] = iid
            self.chiave_di[iid] = chiave_playlist
            return iid

        self.cartella_di = {}


        self.chiave_di = {}
        aperte = self.cfg.get("aperte", {})

        def aperta_di(chiave, di_norma):
            return bool(aperte.get(chiave, di_norma))


        aperta = aperta_di("iptv", self.cfg.get("iptv_aperta", True))
        f_tv = os.path.join(QUI, "icone", "tv.png")
        if f_tv not in self.img_liste:
            self.img_liste[f_tv] = carica_logo(f_tv, PUNTO, PUNTO, RIDOTTE) \
                if os.path.isfile(f_tv) else None
        iptv = self.el_liste.insert("", "end", text=" IPTV",
                                    image=self.img_liste.get(f_tv) or self.vuoto,
                                    open=aperta)
        self.chiave_di[iptv] = "iptv"
        self.iid_iptv = iptv


        epg_aperta = aperta_di("epg", bool(self.cfg.get("epg")))
        epg_colore = colore_cartella("EPG", posto[0])
        posto[0] += 1
        cartella_epg = self.el_liste.insert(
            iptv, "end", text=" EPG (%d)" % len(self.cfg.get("epg", [])),
            image=self.icona_cartella(epg_colore, epg_aperta), open=epg_aperta)
        self.cartella_di[cartella_epg] = epg_colore
        self.chiave_di[cartella_epg] = "epg"
        if self.cfg.get("epg"):
            f = immagine_lista("")
            if f and f not in self.img_liste:
                im = carica_logo(f, PUNTO, PUNTO, RIDOTTE)
                if im is not None:
                    self.img_liste[f] = im
            for d in self.cfg["epg"]:
                iid = self.el_liste.insert(cartella_epg, "end", image=self.img_liste.get(f, self.vuoto),
                                           text=" " + self.nome_guida(d), values=("\u2715",))
                self.iid_epg[iid] = d


        m3u_aperta = aperta_di("m3u", any(d == self.cfg.get("lista") for d in self.liste))
        m3u_colore = colore_cartella("M3U", posto[0])
        posto[0] += 1
        cartella_m3u = self.el_liste.insert(
            iptv, "end", text=" M3U (%d)" % len(self.liste),
            image=self.icona_cartella(m3u_colore, m3u_aperta), open=m3u_aperta)
        self.cartella_di[cartella_m3u] = m3u_colore
        self.chiave_di[cartella_m3u] = "m3u"

        indice_colore_categoria = [0]

        def aggiungi_categorie_playlist(d, iid_playlist):
            dati_cache = _cache_lista_valida(d)
            mappa = ({url: (categoria or _cat_vuota(d))
                      for url, categoria in dati_cache.get("categorie", {}).items()}
                     if dati_cache else {})
            self._mappa_categorie_m3u[d] = mappa
            nomi = sorted(set(mappa.values()), key=str.casefold)
            for nome_categoria in nomi:
                extra_x = ({"values": ("\u2715",)}
                           if os.path.basename(os.path.dirname(d)) == PREFERITI else {})
                colore = PALLINI[indice_colore_categoria[0] % len(PALLINI)]
                indice_colore_categoria[0] += 1
                self._colore_categoria_m3u[(d, nome_categoria)] = colore
                aperta_categoria = aperta_di(
                    "m3u-cat:" + d + ":" + nome_categoria, False)
                iid_categoria = self.el_liste.insert(
                    iid_playlist, "end",
                    text=" " + nome_categoria,
                    image=(self.icona_segnalibro(colore) if extra_x
                           else self.icona_cartella(colore, aperta_categoria)),
                    open=aperta_categoria, **extra_x,
                    tags=("usata",) if getattr(self, "_categoria_m3u_attiva", None) == (d, nome_categoria) else ("sotto",))
                if not extra_x:
                    self.cartella_di[iid_categoria] = colore
                self.chiave_di[iid_categoria] = (
                    "m3u-cat:" + d + ":" + nome_categoria)
                self.iid_categoria_m3u[iid_categoria] = (d, nome_categoria)


        self.iid_reg = {}
        self.cartella_reg = None
        self.reg = registrazioni()
        if self.reg:
            aperta = aperta_di("registrazioni", self.reg_in_uso is not None)


            f_vhs = os.path.join(QUI, "icone", "vhs.png")
            if f_vhs not in self.img_liste:
                self.img_liste[f_vhs] = carica_logo(f_vhs, PUNTO, PUNTO, RIDOTTE) \
                    if os.path.isfile(f_vhs) else None
            vhs = self.img_liste[f_vhs]
            padre = self.el_liste.insert("", "end", text=" " + _("Recordings"),
                                         image=vhs or self.icona_cartella(GRIGIO, aperta),
                                         open=aperta)
            if not vhs:
                self.cartella_di[padre] = GRIGIO
            self.chiave_di[padre] = "registrazioni"
            self.cartella_reg = padre
            def icona_reg(nome):
                f = os.path.join(QUI, "icone", nome + ".png")
                if f not in self.img_liste:
                    self.img_liste[f] = carica_logo(f, PUNTO, PUNTO, RIDOTTE) \
                        if os.path.isfile(f) else None
                return self.img_liste[f]
            f_img = immagine_lista("")
            riserva = self.img_liste.get(f_img, self.vuoto)
            img_video = icona_reg("player") or riserva
            img_audio = icona_reg("music") or img_video
            for (canale, giorno), voci_reg in self.reg:
                solo_audio = all(f.lower().endswith(".mka") for _o, _t, f in voci_reg)
                try:
                    bello = time.strftime("%d/%m/%Y", time.strptime(giorno, "%Y-%m-%d"))
                except ValueError:
                    bello = giorno
                iid = self.el_liste.insert(padre, "end", text=" %s \u00b7 %s" % (canale, bello),
                                           image=img_audio if solo_audio else img_video,
                                           values=("\u2715",),
                                           tags=("usata",) if (canale, giorno) == self.reg_in_uso else ())
                self.iid_reg[iid] = (canale, giorno)


        self.iid_media = {}

        def icona(nome, riserva, dentro=None):
            f = os.path.join(QUI, "icone", nome + ".png")
            if f not in self.img_liste:
                im = carica_logo(f, PUNTO, PUNTO, dentro) if os.path.isfile(f) else None
                self.img_liste[f] = im
            return self.img_liste[f] or riserva
        img_pl = icona("player", self.vuoto, dentro=RIDOTTE)


        img_mu = icona("music", img_pl, dentro=RIDOTTE)
        if "url" not in self.globi:
            self.globi["url"] = globo("#64d2ff", dentro=RIDOTTE) or self.vuoto
        img_ra = icona("radio", self.globi["url"], dentro=RIDOTTE)
        img_ti = icona("timer", self.globi["url"], dentro=RIDOTTE)

        def img_di(lista):
            """player o music: quella dei file che sono di piu'."""
            audio = sum(1 for _n, f, _i in lista if os.path.splitext(f)[1][1:].lower() in AUDIO)
            return img_mu if lista and audio * 2 > len(lista) else img_pl


        if "disco" not in self.img_liste:
            self.img_liste["disco"] = disco(TESTO, dentro=RIDOTTE) or self.vuoto
        montati, cd = dischi(), cd_audio()
        if montati or cd:
            aperta = aperta_di("dischi", True)
            lettore = self.el_liste.insert("", 0, text=" %s (%d)" % (_("Discs"), len(montati) + len(cd)),
                                           image=self.img_liste["disco"], open=aperta)
            self.chiave_di[lettore] = "dischi"
            for dev, tracce in cd:

                chiave = ("cd", dev)
                iid = self.el_liste.insert(lettore, "end", text=" %s (%d)" % (_("Audio CD"), tracce),
                                           image=img_mu,
                                           tags=("usata",) if chiave == self.media_in_uso else ())
                self.iid_media[iid] = chiave
            for mp, nome in montati:


                chiave = ("disco", mp)
                try:
                    img = img_pl if e_dvd(mp) else img_di(media_in(mp))
                except Exception:
                    img = self.img_liste["disco"]
                iid = self.el_liste.insert(lettore, "end", text=" " + nome, image=img,
                                           tags=("usata",) if chiave == self.media_in_uso else ())
                self.iid_media[iid] = chiave
        aperta = aperta_di("locale", True)
        locale = self.el_liste.insert("", "end", text=" Local",
                                      image=self.icona_cartella(TESTO_ONDA, aperta), open=aperta)
        self.cartella_di[locale] = TESTO_ONDA
        self.chiave_di[locale] = "locale"
        for c in self.cfg.get("cartelle", []):


            nome = os.path.basename(c.rstrip(os.sep)) or c
            chiave = ("cartella", c)
            sotto = []
            try:
                for d in sorted(os.listdir(c), key=str.lower):
                    p = os.path.join(c, d)
                    if not d.startswith(".") and os.path.isdir(p):
                        dentro = media_in(p)
                        if dentro:
                            sotto.append((d, p, dentro))
            except Exception:
                pass
            tutti = media_in(c)
            in_uso = self.media_in_uso and self.media_in_uso[0] == "cartella" and (
                self.media_in_uso[1] == c or self.media_in_uso[1].startswith(c + os.sep))
            iid = self.el_liste.insert(locale, "end", text=" %s (%d)" % (nome, len(tutti)),
                                       image=img_di(tutti), values=("\u2715",),
                                       open=aperta_di("media:" + c, bool(in_uso)),
                                       tags=("usata",) if chiave == self.media_in_uso else ())
            self.iid_media[iid] = chiave
            self.chiave_di[iid] = "media:" + c
            for d, p, dentro in sotto:
                k = ("cartella", p)


                if img_di(tutti) == img_mu:
                    kv = ("vinile", colore_di(d))
                    if kv not in self.img_liste:
                        self.img_liste[kv] = (vinile(colore_di(d))
                                              or self.img_liste["disco"])
                    img_sotto = self.img_liste[kv]
                else:
                    img_sotto = img_di(dentro)
                figlio = self.el_liste.insert(iid, "end", text=" %s (%d)" % (d, len(dentro)),
                                              image=img_sotto,
                                              tags=("usata",) if k == self.media_in_uso else ())
                self.iid_media[figlio] = k

        self.iid_file, self.iid_url = {}, {}
        if self.cfg.get("importati"):
            chiave = ("importati", None)
            iid = self.el_liste.insert(locale, "end", text=" %s (%d)" % (
                _("Imported media"), len(self.cfg["importati"])), image=img_pl,
                values=("\u2715",), open=aperta_di("importati", False),
                tags=("usata",) if chiave == self.media_in_uso else ())
            self.iid_media[iid] = chiave
            self.chiave_di[iid] = "importati"
            for f in self.cfg["importati"]:
                figlio = self.el_liste.insert(
                    iid, "end", values=("\u2715",),
                    image=img_mu if os.path.splitext(f)[1][1:].lower() in AUDIO else img_pl,
                    text=" " + os.path.splitext(os.path.basename(f))[0])
                self.iid_file[figlio] = f
        if not self.el_liste.get_children(locale):
            self.el_liste.delete(locale)


        if "azzurro" not in self.globi:
            self.globi["azzurro"] = globo(TESTO_ONDA, dentro=RIDOTTE) or self.vuoto
        aperta = aperta_di("url_tutti", True)
        rete = self.el_liste.insert("", "end", text=" URL", image=self.globi["azzurro"], open=aperta)
        self.chiave_di[rete] = "url_tutti"
        img_pod = icona("mic", img_ra, dentro=RIDOTTE)
        img_cam = icona("cam", img_pl, dentro=RIDOTTE)
        img_vid = icona("video", img_pl, dentro=RIDOTTE)
        for cat, etichetta, img in (("podcast", "Podcast", img_pod), ("musica", "Music", img_mu),
                                    ("radio", "Radio", img_ra), ("cam", "IP cam", img_cam),
                                    ("film", "Film", img_pl), ("video", "Video", img_vid),
                                    (None, "Temporary", img_ti)):
            lista = self.url_di(cat)
            if not lista:
                continue
            chiave = ("url", cat)
            iid = self.el_liste.insert(rete, "end", text=" %s (%d)" % (_(etichetta), len(lista)),
                                       image=img, values=("\u2715",),
                                       open=aperta_di("url:%s" % cat, False),
                                       tags=("usata",) if chiave == self.media_in_uso else ())
            self.iid_media[iid] = chiave
            self.chiave_di[iid] = "url:%s" % cat
            for u in lista:
                figlio = self.el_liste.insert(iid, "end", image=img, values=("\u2715",),
                                              text=" " + self.nome_di_url(u))
                self.iid_url[figlio] = (cat, u)
        if not self.el_liste.get_children(rete):
            self.el_liste.delete(rete)

        gia = set()
        for nome, dentro in categorie():
            gia.update(dentro)
        for d in self.liste:
            if d not in gia:
                iid_playlist = riga(cartella_m3u, d)
                aggiungi_categorie_playlist(d, iid_playlist)
        for nome, dentro in categorie():
            if nome == PREFERITI and not PREFERITI_ACCESI:
                continue
            colore = colore_cartella(nome, posto[0])
            if nome != PREFERITI:
                posto[0] += 1
            aperta = aperta_di("cat:" + nome, any(d == adesso for d in dentro))

            padre = self.el_liste.insert("" if nome == PREFERITI else cartella_m3u, "end",
                                         text=" " + (_("Favorites") if nome == PREFERITI else nome),
                                         image=self.icona_cartella(colore, aperta),
                                         open=aperta)
            self.cartella_di[padre] = colore
            self.chiave_di[padre] = "cat:" + nome
            for d in dentro:
                iid_playlist = riga(padre, d)
                aggiungi_categorie_playlist(d, iid_playlist)

        if self.cartella_reg:
            self.el_liste.move(self.cartella_reg, "", "end")
        self.el_liste.move(iptv, "", "end")
        self.segna_sotto()

    def lista_dell_ultimo_canale(self):
        """La lista da aprire all'avvio: quella ricordata, se contiene
        ancora l'ultimo canale; se no quella, fra tutte, che ce l'ha; se
        nessuna, la ricordata e basta."""
        ricordata = self.cfg.get("lista")
        canale = self.cfg.get("canale")
        if ricordata and self.liste:

            ricordata = self.liste[self.trova_lista(ricordata)]
        if not canale:
            return ricordata
        ordine = ([ricordata] if ricordata else []) + \
                 [d for d in self.liste if d != ricordata]
        for d in ordine:
            try:
                dati_cache = _cache_lista_valida(d)
                if dati_cache and any(c[1] == canale for c in dati_cache.get("canali", [])):
                    return d
            except Exception:
                continue
        return ricordata

    def trova_lista(self, scegli):
        """L'indice della lista da aprire: quella ricordata, cercata per
        percorso esatto, poi per percorso vero (se c'e' un link o la
        cartella e' scritta in un altro modo), poi per nome. Se non si
        trova, la prima."""
        if not scegli:
            return 0
        if scegli in self.liste:
            return self.liste.index(scegli)
        try:
            vero = os.path.realpath(scegli)
            for i, d in enumerate(self.liste):
                if os.path.realpath(d) == vero:
                    return i
        except Exception:
            pass
        nome = nome_di(scegli)
        for i, d in enumerate(self.liste):
            if nome_di(d) == nome:
                return i
        return 0

    def non_sul_verde(self, albero, tag):
        """Il verde vince sul blu: se la selezione e' finita sulla riga
        in uso, la si toglie, cosi' la riga resta verde."""
        for iid in albero.selection():
            if tag in albero.item(iid, "tags"):
                albero.selection_remove(iid)

    def segna_in_uso(self, dove):
        """La lista da cui vengono i canali diventa viola, l'altra torna
        normale."""
        for d, iid in self.iid_di.items():
            if self.el_liste.exists(iid):
                self.tag_riga(
                    iid,
                    d == dove and not getattr(self, "_categoria_m3u_attiva", None))
        for iid in list(self.iid_reg) + list(self.iid_media):
            if self.el_liste.exists(iid):
                self.tag_riga(iid, False)
        self.non_sul_verde(self.el_liste, "usata")

    def icona_segnalibro(self, colore):
        k = (colore, "segnalibro")
        if k not in self.cartelle:
            self.cartelle[k] = segnalibro(colore, dentro=RIDOTTE) or self.vuoto
        return self.cartelle[k]

    def icona_cartella(self, colore, aperta):
        k = (colore, aperta)
        if k not in self.cartelle:
            self.cartelle[k] = cartellina(colore, aperta, dentro=RIDOTTE) or self.vuoto
        return self.cartelle[k]

    def apri_chiudi_cartella(self, aperta, iid=None):
        """Aggiorna icona e stato persistente della cartella."""
        if iid is None:
            iid = self.el_liste.focus()
        if not iid or not self.el_liste.exists(iid):
            return

        colore = getattr(self, "cartella_di", {}).get(iid)
        if colore is not None:
            self.el_liste.item(
                iid,
                image=self.icona_cartella(colore, aperta)
            )

        chiave = getattr(self, "chiave_di", {}).get(iid)
        if chiave:
            self.cfg.setdefault("aperte", {})[chiave] = bool(aperta)
            scrivi_config(self.cfg)

    def lista_selezionata(self):
        """Il percorso della riga selezionata, o None se e' una cartella."""
        s = self.el_liste.selection()
        if not s:
            return None
        for d, iid in self.iid_di.items():
            if iid == s[0]:
                return d
        return None

    def scegli_lista(self):
        s = self.el_liste.selection()
        if s and s[0] in getattr(self, "iid_file", {}):
            self.suona_da_barra(("importati", None), self.iid_file[s[0]])
            return
        if s and s[0] in getattr(self, "iid_url", {}):
            cat, u = self.iid_url[s[0]]
            self.suona_da_barra(("url", cat), u)
            return
        if s and s[0] in self.iid_reg:
            self.mostra_registrazioni(self.iid_reg[s[0]])
            return
        if s and s[0] in self.iid_media:
            self.mostra_media(self.iid_media[s[0]])
            return
        if s and s[0] in getattr(self, "iid_categoria_m3u", {}):
            d, nome_categoria = self.iid_categoria_m3u[s[0]]
            self._categoria_m3u_attiva = (d, nome_categoria)
            for _d, _iid in self.iid_di.items():
                if self.el_liste.exists(_iid):
                    self.tag_riga(_iid, False)
            for _iid, (_d, _cat) in self.iid_categoria_m3u.items():
                if self.el_liste.exists(_iid):
                    self.tag_riga(_iid, (_d, _cat) == (d, nome_categoria))
            if d != self.cfg.get("lista") or self.reg_in_uso is not None \
                    or self.media_in_uso is not None:
                self.reg_in_uso = self.media_in_uso = None
                self.carica(d)
            else:
                self.filtra()
            return

        d = self.lista_selezionata()
        if d:
            self._categoria_m3u_attiva = None
            for _iid in self.iid_categoria_m3u:
                if self.el_liste.exists(_iid):
                    self.tag_riga(_iid, False)
            for _d, _iid in self.iid_di.items():
                if self.el_liste.exists(_iid):
                    self.tag_riga(_iid, _d == d)
            if d != self.cfg.get("lista") or self.reg_in_uso is not None \
                    or self.media_in_uso is not None:
                self.reg_in_uso = self.media_in_uso = None
                self.carica(d)
            else:
                self.filtra()

    def suona_da_barra(self, chiave, dove):
        """Un file importato o un URL aperto, cliccato nella barra: a
        sinistra la sua lista (se non c'e' gia') e parte lui."""
        if self.media_in_uso != chiave:
            self.mostra_media(chiave)
        if dove in (self.file_in_onda, self.cfg.get("canale")) and self.nome_in_onda \
                and not getattr(self, "fermato", None):
            return
        i = next((k for k, c in enumerate(self.visti) if c[1] == dove), -1)
        if i >= 0 and self.elenco.exists(str(i)):
            self.elenco.selection_set(str(i))
            self.elenco.see(str(i))
        if chiave[0] == "url":
            self.apri_url_da(dove)
        else:
            self.riproduci(dove)

    def mostra_registrazioni(self, chiave):
        """A sinistra, al posto dei canali, i programmi registrati su quel
        canale quel giorno: 'Evening News' e sotto '21:00'. Doppio clic e parte."""
        self.reg_in_uso, self.media_in_uso = chiave, None
        voci = dict(self.reg).get(chiave, [])

        self.ora_reg = {f: ora for ora, titolo, f in voci}
        self.canali = [(titolo, f, None) for ora, titolo, f in voci]

        def durate(file_=[f for _o, _t, f in voci]):
            for f in file_:
                durata_di(f)
            if self.reg_in_uso == chiave:
                self.root.after(0, self.filtra)
        threading.Thread(target=durate, daemon=True).start()
        self.ordine_vero = list(self.canali)
        if self.cfg.get("shuffle"):
            self.mescola(disegna=False)
        self.filtra()
        for iid, k in self.iid_reg.items():
            if self.el_liste.exists(iid):
                self.tag_riga(iid, k == chiave)
        for iid in list(self.iid_media) + list(self.iid_di.values()):
            if self.el_liste.exists(iid):
                self.tag_riga(iid, False)
        self.non_sul_verde(self.el_liste, "usata")
        self.scrivi(_("%s - %d recordings") % ("%s %s" % chiave, len(voci)))

    def riproduci(self, f, nome=None):
        """Una registrazione nel lettore: al posto del canale, finche' non
        se ne sceglie un altro. La progress e' quanto del file e' passato."""
        if self.registrando:
            self.ferma_registrazione()
        self.fermato = None
        self.faccia(self.b_pausa, "pausa", _("Pause"))
        if getattr(self, "mai_partito", False):
            self.mai_partito = False
            self.root.after_idle(self.posa_barra)
        if os.path.isabs(f) and not f.startswith(REGISTRAZIONI + os.sep):
            self.cfg["recenti"] = ([f] + [x for x in self.cfg.get("recenti", []) if x != f])[:12]
        self.file_in_onda = f
        self._lista_in_onda = None
        self._vista_onda = self.vista_corrente()
        if os.path.isabs(f) and os.path.splitext(f)[1][1:].lower() in AUDIO:
            self.richiedi_foto_artisti(f)
        self.nome = self.nome_in_onda = nome or os.path.splitext(os.path.basename(f))[0]
        # Ogni nuovo brano deve selezionare subito la propria immagine:
        # cover immediata, foto artista se gia' presente in cache.
        self.mostra_sfondo(True)
        self.ide_in_onda = None
        dvd = f.startswith("dvd://")
        est = "dvd" if dvd else "cd" if e_cd(f) else \
            os.path.splitext(urlparse(f).path if e_media_url(f) else f)[1][1:].lower()
        self.et_formato.config(text="")

        self.percorso_mpv = "dvd://" if dvd else "cdda://" if e_cd(f) else f
        self.varianti, self.quale = [], -1
        self.mostra_ingranaggio()
        self.da_riallineare = False


        self.attesa_da = 0.0 if dvd else time.time()
        self.mostra_carico(True)
        self.colora_stato(self.colore_canale(self.nome, f))
        self.segna_in_onda(f)
        self.icona_preferito()


        self.riprendi_da = float(self.cfg.get("posizioni", {}).get(f, 0.0))
        if self.riprendi_da < 10:
            self.riprendi_da = 0.0
        try:
            self.mpv["audio-files"] = []
            if dvd:


                self.mpv["dvd-device"] = f[len("dvd://"):]
                self.mpv.play("dvd://")
            elif e_cd(f):


                dev, _s, n = f[len("cdda://"):].partition("#")
                self.mpv["cdda-device"] = dev
                self.mpv["cdda-span-a"] = int(n or 1)
                self.mpv["cdda-span-b"] = int(n or 1)
                self.mpv.play("cdda://")
            else:
                self.mpv.play(f)
        except Exception as e:
            self.scrivi(_("cannot read %s: %s") % (self.nome, e), errore=True)
            return
        self.scrivi_riga()

    def carica(self, dove):
        self.reg_in_uso = self.media_in_uso = None
        self.cfg["lista"] = dove
        scrivi_config(self.cfg)
        self.scrivi(_("loading %s...") % nome_di(dove))

        token = getattr(self, "_xvb_carica_token", 0) + 1
        self._xvb_carica_token = token

        dati_cache = _cache_lista_memoria_valida(dove)
        if dati_cache is not None:
            LOGHI.update(dati_cache.get("loghi", {}))
            CHIAVI.update(dati_cache.get("chiavi", {}))
            canali = [
                tuple(c) for c in dati_cache.get("canali", [])
            ]
            categorie = {
                url: (categoria or _cat_vuota(dove))
                for url, categoria in dati_cache.get("categorie", {}).items()
            }
            self._xvb_carica_completato(
                token, dove, (canali, categorie, None)
            )
            return

        def lavora():
            try:
                canali = leggi_lista(dove)
                try:
                    categorie = categorie_m3u(dove, solo_cache=True)
                except Exception:
                    categorie = {}
                risultato = (canali, categorie, None)
            except Exception as e:
                risultato = (None, None, str(e))

            try:
                self.root.after(
                    0, lambda: self._xvb_carica_completato(
                        token, dove, risultato))
            except Exception:
                pass

        threading.Thread(
            target=lavora,
            name="XVB-IPTV-Loader",
            daemon=True
        ).start()

    def _xvb_carica_completato(self, token, dove, risultato):
        if token != getattr(self, "_xvb_carica_token", None):
            return

        canali, categorie, errore = risultato

        if errore:
            self.scrivi(
                _("cannot read %s: %s") % (nome_di(dove), errore),
                errore=True
            )
            return

        self.canali = canali
        self._mappa_categorie_m3u[dove] = categorie or {}
        self.cfg["lista"] = dove
        scrivi_config(self.cfg)
        
        

        self.filtra()
        self.segna_in_uso(dove)
        self.scrivi(
            _("%s - %d channels") % (nome_di(dove), len(canali))
        )
        if any(os.path.isabs(c[1]) for c in canali):
            threading.Thread(target=self.anteprime_lista, args=(dove, list(canali)),
                             daemon=True).start()

        ultimo = self.cfg.get("canale")
        if ultimo:
            for nome, url, ide in canali:
                if url == ultimo:
                    self.root.after(
                        300,
                        lambda n=nome, u=url, i=ide:
                            self.ricorda_canale(n, u, i)
                    )
                    break

    def anteprime_lista(self, dove, canali):
        """In un thread: copertine e poster dei file locali di una lista
        (i preferiti musica e video), che dopo il riavvio non ci sono ancora;
        poi la lista si ridisegna, se e' ancora quella."""
        if self.cfg.get("segno", "logo") != "logo":
            return
        fatte = 0
        for _n, f, _i in canali:
            if not os.path.isabs(f) or f in self.anteprime:
                continue
            if self.cfg.get("lista") != dove or self.media_in_uso is not None:
                return
            self.anteprime[f] = anteprima_di(f)
            self.loghi_img.pop(f, None)
            fatte += 1
        if fatte and self.cfg.get("lista") == dove and self.media_in_uso is None:
            self.root.after(0, self.filtra)

    def ricorda_canale(self, nome, url, ide):
        """L'ultimo canale visto, pronto ma fermo: selezionato nella lista,
        e Play (o doppio clic) lo avvia. Se sta gia' andando qualcosa, non
        si tocca niente."""
        if self.nome_in_onda or self.file_in_onda:
            return
        i = next((k for k, c in enumerate(self.visti) if c[1] == url), -1)
        if i >= 0 and self.elenco.exists(str(i)):
            self.elenco.selection_set(str(i))
            self.elenco.see(str(i))
        self.fermato = ("canale", nome, url, ide)
        self.faccia(self.b_pausa, "play", _("Resume"))

    def chiedi_lista_url(self):
        """Una lista da url, col nome che si vuole: resta url, la copia
        sta in cache e si riscarica quando e' vecchia."""
        url = self.chiedi(_("Add URL"), _("URL of the playlist (m3u):"))
        if not url or not url.strip():
            return
        url = url.strip()
        if url in self.cfg.get("liste_url", {}):
            self.scrivi(_("%s is already there") % nome_di(url))
            return
        base = os.path.basename(urlparse(url).path.rstrip("/"))
        base = re.sub(r"\.(m3u8?|txt)$", "", base, flags=re.I) or "playlist"
        nome = self.chiedi(_("Add URL"), _("Name:"), base)
        if not nome or not nome.strip():
            return
        self.cfg.setdefault("liste_url", {})[url] = nome.strip()
        scrivi_config(self.cfg)
        self.rifai_liste(scegli=url)

    def togli_lista_url(self, url):
        self.cfg.get("liste_url", {}).pop(url, None)
        scrivi_config(self.cfg)
        self.scrivi(_("removed %s") % nome_di(url))
        self.rifai_liste(scegli=self.cfg.get("lista") if self.cfg.get("lista") != url else None)

    def ricarica_liste(self):
        """Reload: le copie delle liste da url si buttano, cosi' si
        riscaricano, e anche i colori presi dai loghi si rifanno; poi si
        rilegge tutto."""
        with _CACHE_PARSED_LOCK:
            _CACHE_PARSED_MEMORIA.clear()
        self.colori, self.in_coda, self.coda_colori[:] = {}, set(), []
        scrivi_colori(self.colori)
        for u in self.cfg.get("liste_url", {}):
            try:
                os.remove(os.path.join(CACHE_LISTE, hashlib.md5(u.encode()).hexdigest() + ".m3u"))
            except Exception:
                pass
        self.rifai_liste(scegli=self.cfg.get("lista"))

    def aggiungi_file(self):

        f = filedialog.askopenfilename(
            title=_("Choose a playlist"),
            filetypes=[(_("All files"), "*"), (_("Playlists"), "*.m3u *.m3u8")])
        if f:
            if not e_una_lista(f):
                self.scrivi(_("%s does not look like an m3u playlist") % os.path.basename(f), errore=True)
                return
            self.aggiungi(f)

    def apri_file(self):
        """Un video o un brano dal disco: finisce fra gli Importati nella
        barra di destra (come la playlist di VLC) e parte subito."""
        f = sfoglia(self.root, _("Open file..."), self.cfg.get("ultima_cartella", CASA))
        if not f:
            return
        self.cfg["ultima_cartella"] = os.path.dirname(f)
        self.apri_da_fuori([f])

    def apri_da_fuori(self, dove):
        """File e cartelle che arrivano da fuori: "Apri con XVB" dal file
        manager, la riga di comando, un'altra XVB aperta dopo, i recenti.
        Le cartelle entrano nella barra come con Open folder; le playlist
        m3u fra le playlist; i file multimediali: se stanno in una cartella
        gia' aggiunta si apre quella, se no vanno fra gli Importati. Parte
        il primo."""
        self.root.deiconify()
        self.root.lift()
        file_, primo = [], None
        for d in dove:
            d = os.path.abspath(d)
            if os.path.isdir(d):
                if d not in self.cfg.get("cartelle", []):
                    self.cfg.setdefault("cartelle", []).append(d)
                primo = primo or ("cartella", d)
            elif os.path.isfile(d) and e_media(d):
                file_.append(d)
            elif os.path.isfile(d):
                self.aggiungi(d)
                return
        if file_:
            f = file_[0]
            cartella = next((c for c in self.cfg.get("cartelle", [])
                             if f.startswith(c.rstrip(os.sep) + os.sep)), None)
            if len(file_) == 1 and cartella:
                primo = ("cartella", cartella)
            else:
                importati = [x for x in self.cfg.get("importati", []) if x not in file_]
                self.cfg["importati"] = importati + file_
                primo = ("importati", None)
        if not primo:
            return
        scrivi_config(self.cfg)
        self.rifai_albero()
        self.mostra_media(primo)
        if file_:
            f = file_[0]
            i = next((k for k, c in enumerate(self.visti) if c[1] == f), -1)
            if i >= 0 and self.elenco.exists(str(i)):
                self.elenco.selection_set(str(i))
                self.elenco.see(str(i))
            self.riproduci(f)

    def recenti(self):
        """Gli ultimi file aperti che ci sono ancora, dal piu' recente."""
        return [f for f in self.cfg.get("recenti", []) if os.path.isfile(f)][:8]

    def pulisci_recenti(self):
        self.cfg["recenti"] = []
        scrivi_config(self.cfg)

    def apri_cartella_media(self):
        """Una cartella di video o musica: entra nella barra di destra con
        l'icona del player; a sinistra i file che ci sono dentro."""
        c = sfoglia(self.root, _("Open folder..."), self.cfg.get("ultima_cartella", CASA),
                    file_=False)
        if not c:
            return
        self.cfg["ultima_cartella"] = c
        if c not in self.cfg.get("cartelle", []):
            self.cfg.setdefault("cartelle", []).append(c)
            scrivi_config(self.cfg)
        self.rifai_albero()
        self.mostra_media(("cartella", c))

    def guarda_dischi(self):
        """Ogni 5 secondi: se un disco e' entrato o uscito (o una ISO e'
        stata montata o smontata) la barra di destra si rifa'; se era quello
        aperto, a sinistra tornano i canali."""
        adesso = dischi() + cd_audio()
        if adesso != self.dischi_visti:
            self.dischi_visti = adesso
            if (self.media_in_uso and self.media_in_uso[0] in ("disco", "cd")
                    and self.media_in_uso[1] not in [mp for mp, _n in adesso]):
                self.media_in_uso = None
                self.rifai_liste(scegli=self.cfg.get("lista"))
            else:
                self.rifai_albero()
        self.root.after(5000, self.guarda_dischi)

    def controlla_copertine(self):
        """All'avvio, in sottofondo: per tutte le cartelle aggiunte e gli
        importati, scarica poster e copertine che mancano. In silenzio:
        dice qualcosa solo se ha scaricato; chi e' gia' stato cercato
        senza trovare niente (file privati) non si ricerca."""
        file_ = []
        for c in self.cfg.get("cartelle", []):
            file_ += [f for _n, f, _i in media_in(c)]
        file_ += [f for f in self.cfg.get("importati", []) if os.path.isfile(f)]
        if file_:
            threading.Thread(target=self._scarica_copertine, args=(file_, None, True),
                             daemon=True).start()

    def scarica_copertine(self):
        """File > Download posters and covers: per i film della lista a
        sinistra il poster da TMDB, salvato accanto al film col suo nome
        (.jpg); per la musica la copertina dell'album da MusicBrainz,
        salvata come cover.jpg nella cartella. Quello che c'e' gia' non si
        tocca. In sottofondo."""
        if not (self.media_in_uso or self.reg_in_uso) or not self.canali:
            self.scrivi(_("open a folder of films or music first"))
            return
        file_ = [f for _n, f, _i in self.canali if os.path.isabs(f)]
        threading.Thread(target=self._scarica_copertine, args=(file_, self.media_in_uso),
                         daemon=True).start()

    def _scarica_copertine(self, file_, chiave, zitto=False):
        fatti, nuove, album_visti = 0, 0, set()
        chiave_tmdb = TMDB_CHIAVE
        lingua = LINGUE_TMDB.get(LINGUA, "en-US")
        try:
            with open(FILE_PROVATI, encoding="utf-8") as h:
                provati = json.load(h)
        except Exception:
            provati = {}
        gia_provati = dict(provati)
        for n, f in enumerate(file_, 1):
            if not zitto:
                self.scrivi(_("downloading posters and covers... %d/%d") % (n, len(file_)))
            cartella, base = os.path.split(f)

            voce = cartella if os.path.splitext(f)[1][1:].lower() in AUDIO else f
            if zitto and voce in gia_provati:
                continue
            provati[voce] = 1
            radice = os.path.splitext(base)[0]
            audio = os.path.splitext(f)[1][1:].lower() in AUDIO
            try:
                if not audio:
                    if not chiave_tmdb:
                        continue


                    titolo, anno = titolo_anno(f)
                    info = info_tmdb(chiave_tmdb, titolo, anno, lingua)
                    if info is None:
                        continue
                    generi = generi_tmdb(chiave_tmdb, lingua, self.info)
                    info["generi"] = [generi.get(g, "") for g in info["generi"] if generi.get(g)]
                    self.info[f] = info
                    nuove += 1
                    gia = [radice + e for e in (".jpg", ".jpeg", ".png")] + \
                          ["poster.jpg", "poster.jpeg", "poster.png"]
                    if info["poster"] and not any(os.path.isfile(os.path.join(cartella, g))
                                                  for g in gia):
                        scarica_in(info["poster"], os.path.join(cartella, radice + ".jpg"))
                        fatti += 1
                else:
                    if not HA_TAG:
                        continue
                    t = mutagen.File(f, easy=True)
                    tags = t.tags if t is not None and t.tags else {}
                    artista = (tags.get("albumartist") or tags.get("artist") or [""])[0].strip()
                    album = (tags.get("album") or [""])[0].strip()
                    titolo = (tags.get("title") or [""])[0].strip()
                    ha_anno = str((tags.get("date") or [""])[0])[:4].isdigit()
                    if not (artista and album):

                        t2, a2, b2, y2 = dal_percorso(f, self.cfg.get("cartelle", []))
                        artista, album, titolo = artista or a2, album or b2, titolo or t2
                        ha_anno = ha_anno or bool(y2)
                    if artista and titolo and not album:


                        suo = [radice + e for e in (".jpg", ".jpeg", ".png")]
                        if any(os.path.isfile(os.path.join(cartella, g)) for g in suo) \
                                or copertina_dentro(f) is not None:
                            continue
                        urls, anno_mb, album_mb = copertina_da_brano(artista, titolo)
                        time.sleep(1.1)
                        if album_mb or anno_mb:
                            self.info[f] = {"album": album_mb, "anno": anno_mb}
                            nuove += 1
                        for url in urls:
                            try:
                                scarica_in(url, os.path.join(cartella, radice + ".jpg"))
                                fatti += 1
                                break
                            except Exception:
                                continue
                        continue
                    if cartella in album_visti:
                        continue
                    album_visti.add(cartella)
                    gia = [c + e for c in ("cover", "folder") for e in (".jpg", ".jpeg", ".png")]
                    ha_copertina = any(os.path.isfile(os.path.join(cartella, g)) for g in gia) \
                        or copertina_dentro(f) is not None

                    if not (artista and album) or (ha_copertina and ha_anno):
                        continue
                    url, anno_mb = copertina_musicbrainz(artista, album)
                    time.sleep(1.1)
                    if anno_mb and not ha_anno:
                        self.info[cartella] = {"anno": anno_mb}
                        nuove += 1
                    if url and not ha_copertina:
                        scarica_in(url, os.path.join(cartella, "cover.jpg"))
                        fatti += 1
            except Exception:
                continue
        try:
            os.makedirs(os.path.dirname(FILE_PROVATI), exist_ok=True)
            with open(FILE_PROVATI, "w", encoding="utf-8") as h:
                json.dump(provati, h)
        except Exception:
            pass
        if nuove:
            scrivi_json(FILE_INFO, self.info)
        if fatti or not zitto:
            self.scrivi(_("%d posters and covers downloaded") % fatti)
        if chiave is None:
            chiave = self.media_in_uso
        if nuove and not fatti and chiave and self.media_in_uso == chiave:
            self.root.after(0, self.filtra)
            self.root.after(0, self.scrivi_riga)
        if fatti and chiave and self.media_in_uso == chiave:

            self.anteprime.clear()
            self.loghi_img = {k: v for k, v in self.loghi_img.items() if not os.path.isabs(k)}
            threading.Thread(target=self.tagga, args=(chiave,), daemon=True).start()

    def mostra_media(self, chiave):
        """A sinistra, al posto dei canali, i file di quella cartella o
        gli importati. La scansione delle cartelle avviene in background."""
        tipo, c = chiave

        # Uscendo da una sottocategoria M3U, il filtro non deve
        # rimanere attivo sulle librerie locali.
        self._categoria_m3u_attiva = None

        # Invalida le operazioni IPTV ancora in corso.
        self._xvb_carica_token = getattr(self, "_xvb_carica_token", 0) + 1
        self._xvb_filtra_token = getattr(self, "_xvb_filtra_token", 0) + 1

        self.reg_in_uso, self.media_in_uso = None, chiave
        token = getattr(self, "_media_scan_token", 0) + 1
        self._media_scan_token = token
        self._media_scansione_in_corso = False


        if tipo == "cartella" or (tipo == "disco" and not e_dvd(c)):
            nome = os.path.basename(c.rstrip(os.sep)) or c
            self._media_scansione_in_corso = True
            self.canali, self.ordine_vero, self.visti = [], [], []
            self._render_token = getattr(self, "_render_token", 0) + 1
            self.elenco.delete(*self.elenco.get_children())
            self.applica_poster()
            self._aggiorna_selezione_media(chiave)
            self.scrivi(_("Loading %s...") % nome)

            self._media_scan_streaming = not bool(self.cerca.get()) and not self.cfg.get("shuffle")
            self._media_render_token = token
            self._media_render_chiave = chiave
            self._media_rendered = 0
            self._media_render_attivo = False
            self._media_render_finalizzato = False
            self._render_colori = []
            self._render_adesso = self.file_in_onda or self.cfg.get("canale")

            def lavora():
                batch = []
                inviati = False
                try:
                    for dove, sotto, nomi in os.walk(c):
                        sotto[:] = sorted((d for d in sotto if not d.startswith(".")), key=str.lower)
                        for n in sorted(nomi, key=str.lower):
                            if n.startswith(".") or not e_media(n):
                                continue
                            batch.append((os.path.splitext(n)[0], os.path.join(dove, n), None))
                            if len(batch) >= 30:
                                pronto = batch
                                batch = []
                                inviati = True
                                self.root.after(0, lambda pronto=pronto: self._media_blocco_pronto(
                                    token, chiave, nome, pronto, False))
                    self.root.after(0, lambda pronto=batch: self._media_blocco_pronto(
                        token, chiave, nome, pronto, True))
                except Exception:
                    try:
                        self.root.after(0, lambda pronto=batch: self._media_blocco_pronto(
                            token, chiave, nome, pronto, True))
                    except Exception:
                        pass

            threading.Thread(target=lavora, daemon=True).start()
            return

        if tipo == "url":
            self.canali = [(self.nome_di_url(u), u, None) for u in self.url_di(c)]
            nome = _(dict(CATEGORIE_URL).get(c, "Temporary"))
        elif tipo == "cd":
            tracce = dict(cd_audio()).get(c, 0)
            self.canali = [(_("Track %d") % i, "cdda://%s#%d" % (c, i), None)
                           for i in range(1, tracce + 1)]
            nome = _("Audio CD")
        elif tipo == "disco":
            nome = os.path.basename(c.rstrip(os.sep)) or c

            url = "dvd://" + c
            self.canali = [(nome, url, None)]
            if url not in self.info and url not in getattr(self, "dvd_cercati", set()):
                self.dvd_cercati = getattr(self, "dvd_cercati", set()) | {url}
                cerca = self.cfg.get("nomi_dvd", {}).get(url) or titolo_da_etichetta(nome)
                threading.Thread(target=self.cerca_dvd, args=(url, cerca), daemon=True).start()
        else:
            self.canali = [(os.path.splitext(os.path.basename(f))[0], f, None)
                           for f in self.cfg.get("importati", []) if os.path.isfile(f)]
            nome = _("Imported media")
        self._completa_mostra_media(chiave, nome)

    def _media_blocco_pronto(self, token, chiave, nome, blocco, finale):
        """Aggiunge i file appena trovati e li mostra senza aspettare la scansione completa."""
        if token != getattr(self, "_media_scan_token", None) or self.media_in_uso != chiave:
            return
        if blocco:
            self.canali.extend(blocco)
            if self._media_scan_streaming and not self.cerca.get() and not self.cfg.get("shuffle"):
                inizio = len(self.visti)
                self.visti.extend(blocco)
                self.ordine_vero.extend(blocco)
                for i, (_nome, f, _ide) in enumerate(blocco, start=inizio):
                    self.posto_file[f] = i
                self._media_render_pump(token)
        if not finale:
            return

        self._media_scansione_in_corso = False
        if not self._media_scan_streaming or self.cerca.get() or self.cfg.get("shuffle"):
            self._completa_mostra_media(chiave, nome)
            return

        self.scrivi(_("%s - %d files") % (nome, len(self.canali)))
        threading.Thread(target=self.tagga, args=(chiave,), daemon=True).start()
        self._media_render_pump(token)

    def _media_render_pump(self, token):
        """Disegna i risultati progressivi in blocchi da 100 righe."""
        if (token != getattr(self, "_media_render_token", None)
                or self.media_in_uso != getattr(self, "_media_render_chiave", None)):
            return
        if self._media_render_attivo:
            return
        self._media_render_attivo = True

        def disegna():
            if (token != getattr(self, "_media_render_token", None)
                    or self.media_in_uso != getattr(self, "_media_render_chiave", None)):
                return
            inizio = self._media_rendered
            fine = min(inizio + 100, len(self.visti))
            for i in range(inizio, fine):
                nome, url, ide = self.visti[i]
                self.elenco.insert("", "end", iid=str(i), text=self.testo_riga(nome, url, ide),
                                   image=self.pallino_di(nome, url),
                                   colore=self.colore_canale(nome, url),
                                   logo=self.logo_di(url, self.colore_canale(nome, url)) or self.icona_file_di(url))
                if url not in self.colori and url in LOGHI and url not in self.in_coda:
                    self.in_coda.add(url)
                    self._render_colori.append(url)
            self._media_rendered = fine
            if fine < len(self.visti):
                self.root.after(1, disegna)
                return

            self._media_render_attivo = False
            if self._media_scansione_in_corso:
                return
            if self._media_render_finalizzato:
                return
            self._media_render_finalizzato = True
            if self._render_colori:
                self.coda_colori.extend(self._render_colori)
                self._render_colori = []
            adesso = self._render_adesso
            if adesso and self.nome_in_onda:
                self.segna_in_onda(adesso)
            elif self.file_in_onda:
                self.segna_in_onda(self.file_in_onda)
            elif getattr(self, "fermato", None):
                i = next((k for k, c in enumerate(self.visti) if c[1] == self.fermato[2]), -1)
                if i >= 0 and self.elenco.exists(str(i)):
                    self.elenco.selection_set(str(i))

        self.root.after(1, disegna)

    def _aggiorna_selezione_media(self, chiave):
        """Evidenzia la cartella aperta nella barra laterale."""
        for iid, k in self.iid_media.items():
            if self.el_liste.exists(iid):
                self.tag_riga(iid, k == chiave)
                if k == chiave:
                    self.mostra_riga(iid)
        for iid in (list(self.iid_reg) + list(self.iid_di.values())
                    + list(self.iid_categoria_m3u)):
            if self.el_liste.exists(iid):
                self.tag_riga(iid, False)
        self.non_sul_verde(self.el_liste, "usata")

    def _completa_mostra_media(self, chiave, nome):
        """Prepara e mostra una libreria gia' scandita."""
        if self.media_in_uso != chiave:
            return
        self.ordine_vero = list(self.canali)
        if self.cfg.get("shuffle"):
            self.mescola(disegna=False)
        self.filtra()
        self._aggiorna_selezione_media(chiave)
        self.scrivi(_("%s - %d files") % (nome, len(self.canali)))
        tipo = chiave[0]
        if tipo not in ("url", "cd"):
            threading.Thread(target=self.tagga, args=(chiave,), daemon=True).start()

    def aggiungi(self, dove):
        dove = os.path.abspath(dove)
        if dove in LISTE_NASCOSTE:
            LISTE_NASCOSTE.discard(dove)
            self.cfg["liste_nascoste"] = [x for x in self.cfg.get("liste_nascoste", [])
                                          if os.path.abspath(x) != dove]
            scrivi_config(self.cfg)
        if dove not in self.tutte_le_liste():
            self.cfg.setdefault("liste", []).append(dove)
            scrivi_config(self.cfg)
        self.rifai_liste(scegli=dove)

    def menu_liste(self, ev):
        """Clic destro su una riga della barra: Rename, per playlist, guide
        e (a sinistra) indirizzi aperti."""
        iid = self.el_liste.identify_row(ev.y)
        if not iid:
            return
        if iid in getattr(self, "iid_categoria_m3u", {}):
            d, cat = self.iid_categoria_m3u[iid]
            if os.path.basename(os.path.dirname(d)) == PREFERITI:
                self.tendina.apri(ev.x_root, ev.y_root, [
                    (_("Rename..."), lambda: self.rinomina_playlist_pref(d, cat)),
                    (_("Delete"), lambda: self.togli_playlist_pref(d, cat))])
        elif iid in self.iid_epg or iid in self.iid_di.values():
            dl = next((x for x, i in self.iid_di.items() if i == iid), "")
            if os.path.basename(os.path.dirname(dl)) != PREFERITI:
                self.tendina.apri(ev.x_root, ev.y_root, [(_("Rename..."), lambda: self.rinomina(iid))])
        elif iid in getattr(self, "iid_url", {}):
            cat, u = self.iid_url[iid]
            voci = [(_("Rename..."), lambda: self.rinomina_url(u))]
            altre = [(k, n) for k, n in CATEGORIE_URL + ((None, "Temporary"),) if k != cat]
            voci += [(_("Set cover..."), lambda: self.metti_copertina(u))]
            if os.path.isfile(copertina_url(u)):
                voci.append((_("Remove cover"), lambda: self.togli_copertina(u)))
            voci += [None] + [(_("Move to %s") % _(n), lambda k=k: self.sposta_url(u, cat, k))
                              for k, n in altre]
            self.tendina.apri(ev.x_root, ev.y_root, voci)
        elif iid in getattr(self, "iid_file", {}):
            f = self.iid_file[iid]
            self.tendina.apri(ev.x_root, ev.y_root,
                              [(_("Set cover..."), lambda: self.metti_copertina(f))])
        return "break"

    def metti_copertina(self, dove):
        """Un'immagine scelta dal disco come copertina. Per un indirizzo va
        in ~/.config/xvb/copertine/; per un file sul disco accanto a lui,
        col suo nome (la usano anche mpv e la lista)."""
        f = self.file_in_onda or ""
        da = os.path.dirname(dove) if os.path.isabs(dove) else self.cfg.get("ultima_cartella", CASA)
        img = sfoglia(self.root, _("Choose an image"), da, tipi=IMMAGINI)
        if not img:
            return
        fuori = copertina_url(dove) if not os.path.isabs(dove) else \
            os.path.splitext(dove)[0] + ".jpg"
        try:
            os.makedirs(os.path.dirname(fuori), exist_ok=True)
            if HA_PIL:
                import io
                with open(img, "rb") as h:
                    dati_file = h.read()

                dati_png = svg_in_png(dati_file)
                im = Image.open(io.BytesIO(dati_png))

                trasp = im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info
                if trasp and not os.path.isabs(dove) and not dove.startswith("dvd://"):
                    im = im.convert("RGBA")
                    im.thumbnail((800, 800))
                    im.save(fuori, "PNG")
                else:
                    im = im.convert("RGB")
                    im.thumbnail((800, 800))
                    im.save(fuori, "JPEG", quality=90)
            else:
                import shutil
                shutil.copyfile(img, fuori)
        except Exception as e:
            self.scrivi(_("cannot read %s: %s") % (os.path.basename(img), e), errore=True)
            return
        self.copertina_cambiata(dove)

    def scegli_colore(self, nome, url):
        """Un colore scelto a mano per un canale: vince su quello preso dal
        suo logo (pallino, linea di avanzamento, comandi)."""
        from tkinter import colorchooser
        c = colorchooser.askcolor(color=self.colore_canale(nome, url),
                                  parent=self.root, title=_("Choose a color"))[1]
        if not c:
            return
        self.cfg.setdefault("colori_canali", {})[url] = c
        self.colore_cambiato()

    def togli_colore(self, url):
        self.cfg.get("colori_canali", {}).pop(url, None)
        self.colore_cambiato()

    def colore_cambiato(self):
        scrivi_config(self.cfg)
        self.filtra()

    def togli_copertina(self, dove):
        try:
            os.remove(copertina_url(dove))
        except OSError:
            pass
        self.copertina_cambiata(dove)

    def copertina_cambiata(self, dove):
        """Via le copie in memoria, e si ridisegna: lista, e lo sfondo se
        quello e' in riproduzione."""
        self.loghi_img.pop(dove, None)
        self.anteprime.pop(dove, None)
        if self.media_in_uso:
            self.mostra_media(self.media_in_uso)
        else:
            self.filtra()
        if dove in (self.file_in_onda, self.cfg.get("canale")) and self.sfondo_su:
            self.mostra_sfondo(True)

    def sposta_url(self, url, da, a):
        """Un indirizzo da una categoria all'altra (es. un provvisorio che
        si decide di tenere fra le radio)."""
        self.metti_url(a, url)
        scrivi_config(self.cfg)
        self.rifai_albero()
        if self.media_in_uso in (("url", da), ("url", a)):
            self.mostra_media(self.media_in_uso)

    def menu_elenco(self, ev, y=None):
        """Clic destro a sinistra. Canali IPTV: Rename e il logo. File e
        indirizzi: la copertina."""
        try:
            r = int(self.elenco.index("@%d,%d" % (COLONNA + 5, ev.y if y is None else y)).split(".")[0])
            iid = self.elenco.voci[self.elenco._dove(r)[0]]
            nome, url, _i = self.visti[int(iid)]
        except (tk.TclError, ValueError, IndexError):
            return "break"
        if url.startswith("dvd://"):
            voci = [(_("Rename..."), lambda: self.rinomina_dvd(url, nome)),
                    (_("Set cover..."), lambda: self.metti_copertina(url))]
            if os.path.isfile(copertina_url(url)):
                voci.append((_("Remove cover"), lambda: self.togli_copertina(url)))
            self.tendina.apri(ev.x_root, ev.y_root, voci)
            return "break"
        if url.startswith(REGISTRAZIONI + os.sep) or e_cd(url):
            return "break"
        if not self.media_in_uso and not self.reg_in_uso:
            voci = [(_("Rename..."), lambda: self.rinomina_canale(nome, url)),
                    (_("Set logo..."), lambda: self.metti_copertina(url))]
            if os.path.isfile(copertina_url(url)):
                voci.append((_("Remove logo"), lambda: self.togli_copertina(url)))
            voci.append((_("Set color..."), lambda: self.scegli_colore(nome, url)))
            if url in self.cfg.get("colori_canali", {}):
                voci.append((_("Remove color"), lambda: self.togli_colore(url)))
        else:
            voci = [(_("Set cover..."), lambda: self.metti_copertina(url))]
            if not os.path.isabs(url) and os.path.isfile(copertina_url(url)):
                voci.append((_("Remove cover"), lambda: self.togli_copertina(url)))
        self.tendina.apri(ev.x_root, ev.y_root, voci)
        return "break"

    def nome_canale(self):
        """Il nome del canale in onda come lo si vede: quello scelto con
        Rename, se c'e'."""
        u = self.cfg.get("canale")
        if u and not self.file_in_onda:
            return self.cfg.get("nomi_canali", {}).get(u) or self.nome
        return self.nome

    def rinomina_canale(self, nome, url):
        """Un nome nuovo per un canale IPTV: si vede nella lista e nella riga
        di stato; la playlist non si tocca e la guida continua a trovarlo
        col nome vero. Vuoto = torna quello della playlist."""
        nomi = self.cfg.setdefault("nomi_canali", {})
        nuovo = self.chiedi(_("Rename..."), _("Name:"), nomi.get(url) or nome)
        if nuovo is None:
            return
        if nuovo.strip() and nuovo.strip() != nome:
            nomi[url] = nuovo.strip()
        else:
            nomi.pop(url, None)
        scrivi_config(self.cfg)
        self.filtra()
        if url == self.cfg.get("canale") and not self.file_in_onda:
            self.scrivi_riga()

    def rinomina_url(self, url):
        nome = self.chiedi(_("Rename..."), _("Name:"), self.nome_di_url(url))
        if not nome or not nome.strip():
            return
        self.cfg.setdefault("nomi_url", {})[url] = nome.strip()
        scrivi_config(self.cfg)
        self.rifai_albero()
        if self.media_in_uso and self.media_in_uso[0] == "url":
            self.mostra_media(self.media_in_uso)
        if url in (self.file_in_onda, self.cfg.get("canale")):
            self.nome = self.nome_in_onda = nome.strip()
            self.scrivi_riga()

    def rinomina(self, iid):
        """Un nome nuovo per una riga della barra. Guide: il nome mostrato.
        Playlist da url: il nome mostrato. Playlist in playlists/: si
        rinomina il file vero. Le altre (aggiunte da fuori): il nome
        mostrato, il file non si tocca."""
        if iid in self.iid_epg:
            dove = self.iid_epg[iid]
            nome = self.chiedi(_("Rename..."), _("Name:"), self.nome_guida(dove))
            if not nome or not nome.strip():
                return
            self.cfg.setdefault("epg_nomi_utente", {})[dove] = nome.strip()
            scrivi_config(self.cfg)
            self.rifai_albero()
            return
        d = next((d for d, i in self.iid_di.items() if i == iid), None)
        if d is None:
            return
        nome = self.chiedi(_("Rename..."), _("Name:"), nome_di(d))
        if not nome or not nome.strip() or nome.strip() == nome_di(d):
            return
        nome = nome.strip()
        if re.match(r"https?://", d, re.I):
            self.cfg.setdefault("liste_url", {})[d] = nome
        elif os.path.dirname(os.path.abspath(d)) in CARTELLE_LISTE + CARTELLE_VECCHIE or any(
                os.path.abspath(d).startswith(c + os.sep) for c in CARTELLE_LISTE + CARTELLE_VECCHIE):
            nuovo = os.path.join(os.path.dirname(d), nome.replace(os.sep, "-") + os.path.splitext(d)[1])
            try:
                if os.path.exists(nuovo):
                    raise OSError(_("%s is already there") % os.path.basename(nuovo))
                os.rename(d, nuovo)
            except OSError as e:
                self.scrivi(_("cannot rename %s: %s") % (nome_di(d), e), errore=True)
                return
            if self.cfg.get("lista") == d:
                self.cfg["lista"] = nuovo
        else:
            self.cfg.setdefault("nomi_liste", {})[d] = nome
        scrivi_config(self.cfg)
        self.rifai_albero()
        if self.cfg.get("lista") and not self.media_in_uso and not self.reg_in_uso:
            self.segna_in_uso(self.cfg["lista"])

    def gestisci_click_sidebar(self, ev):
        iid = self.el_liste.identify_row(ev.y)
        if not iid:
            self.col_mouse = True
            return

        elemento = self.el_liste.identify_element(ev.x, ev.y)

        if elemento == "Treeitem.indicator":
            self.col_mouse = False
            return

        chiave = getattr(self, "chiave_di", {}).get(iid)

        if chiave in ("iptv", "epg", "m3u", "locale", "url_tutti"):
            self.col_mouse = False
            aperta = not bool(self.el_liste.item(iid, "open"))
            self.el_liste.focus(iid)
            self.el_liste.item(iid, open=aperta)
            self.apri_chiudi_cartella(aperta, iid)
            return "break"

        self.col_mouse = True
        return

    def clic_liste(self, ev):
        """Un clic nella barra delle liste: se e' sulla x della riga si
        toglie quella cosa (dopo conferma), senza selezionarla."""
        if self.el_liste.identify_column(ev.x) != "#1":
            return
        iid = self.el_liste.identify_row(ev.y)
        if not iid or not self.el_liste.set(iid, "x"):
            return
        self.togli_riga(iid)
        return "break"

    def conferma(self, titolo, testo, ok=None):
        """Una domanda con Delete e Cancel: True se si e' detto Delete."""
        def corpo(dentro):
            tk.Label(dentro, text=titolo, bg=PANNELLO, fg=TESTO, anchor="w",
                     font=(CARATTERE, 11, "bold", STILE_CARATTERE)).pack(fill="x")
            tk.Label(dentro, text=testo, bg=PANNELLO, fg=GRIGIO, anchor="w",
                     justify="left", wraplength=380).pack(fill="x", pady=(8, 0))
        f = Finestrella(self.root, titolo, corpo, ok=ok or _("Delete"), annulla=_("Cancel"),
                        domanda=True)
        self.root.wait_window(f)
        return bool(f.risposta)

    def conferma_via(self, nome, testo):
        return self.conferma(_("Remove %s") % nome, testo, ok=_("Remove"))

    def togli_riga(self, iid):
        """Cosa c'e' su quella riga, e come si toglie."""
        if iid in getattr(self, "iid_categoria_m3u", {}):
            d, cat = self.iid_categoria_m3u[iid]
            if os.path.basename(os.path.dirname(d)) == PREFERITI:
                self.togli_playlist_pref(d, cat)
            return
        nome = self.el_liste.item(iid, "text").strip()
        if iid in self.iid_epg:
            if self.conferma(_("Remove %s") % nome, _("The guide will be removed from the list.")):
                self.togli_epg(self.iid_epg[iid])
            return
        if iid in getattr(self, "iid_file", {}) or iid in getattr(self, "iid_url", {}):

            if iid in self.iid_file:
                self.cfg["importati"] = [x for x in self.cfg.get("importati", []) if x != self.iid_file[iid]]
                chiave = ("importati", None)
            else:
                cat, u = self.iid_url[iid]
                if cat is None:
                    self.cfg["url_aperti"] = [x for x in self.cfg.get("url_aperti", []) if x != u]
                else:
                    self.cfg["url_salvati"][cat] = [x for x in self.url_di(cat) if x != u]
                self.cfg.get("nomi_url", {}).pop(u, None)
                chiave = ("url", cat)
            scrivi_config(self.cfg)
            self.rifai_albero()
            if self.media_in_uso == chiave:
                self.mostra_media(chiave)
            return
        if iid in self.iid_media:
            tipo, c = self.iid_media[iid]
            if tipo == "url":
                if not self.conferma_via(nome, _("The addresses will be removed from the list.")):
                    return
                for u in self.url_di(c):
                    self.cfg.get("nomi_url", {}).pop(u, None)
                if c is None:
                    self.cfg["url_aperti"] = []
                else:
                    self.cfg.setdefault("url_salvati", {})[c] = []
                scrivi_config(self.cfg)
                if self.media_in_uso == (tipo, c):
                    self.media_in_uso = None
                    self.canali = []
                    self.filtra()
                self.rifai_albero()
                return
            if tipo == "cartella":
                if not self.conferma_via(nome, _("The folder will be removed from the list. Files are not touched.")):
                    return
                self.cfg["cartelle"] = [x for x in self.cfg.get("cartelle", []) if x != c]
            else:
                if not self.conferma_via(nome, _("The imported files will be removed from the list. Files are not touched.")):
                    return
                self.cfg["importati"] = []
            scrivi_config(self.cfg)
            if self.media_in_uso == (tipo, c):
                self.media_in_uso = None
                self.rifai_liste(scegli=self.cfg.get("lista"))
            else:
                self.rifai_albero()
            return
        if iid in self.iid_reg:
            canale, giorno = self.iid_reg[iid]
            file_ = [f for _o, _t, f in dict(self.reg).get((canale, giorno), [])]
            if not self.conferma(_("Delete %s") % nome,
                                 _("%d recording(s) will be deleted from disk. This cannot be undone.") % len(file_)):
                return
            for f in file_:
                try:
                    if self.file_in_onda == f:
                        self.mpv.command("stop")
                        self.file_in_onda = None
                    os.remove(f)
                except Exception as e:
                    self.scrivi(_("cannot delete %s: %s") % (os.path.basename(f), e), errore=True)
            try:
                os.rmdir(os.path.dirname(file_[0]))
            except Exception:
                pass
            if self.reg_in_uso == (canale, giorno):
                self.reg_in_uso = None
                self.rifai_liste(scegli=self.cfg.get("lista"))
            else:
                self.rifai_albero()
            return
        for d, i in self.iid_di.items():
            if i != iid:
                continue
            if re.match(r"https?://", d, re.I):
                if self.conferma(_("Remove %s") % nome, _("The playlist will be removed from the list.")):
                    self.togli_lista_url(d)
                return
            if self.conferma(_("Remove %s") % nome, _("The playlist will be removed from the list.")):
                self.nascondi_lista(d)
            return

    def nascondi_lista(self, d):
        """Toglie una lista dall'elenco. Il file sul disco non si tocca mai:
        se sta in playlists/ si ricorda di non mostrarla, e per rimetterla
        basta aggiungerla di nuovo (Add file)."""
        d = os.path.abspath(d)
        if d in self.cfg.get("liste", []):
            self.cfg["liste"] = [x for x in self.cfg["liste"] if x != d]
        else:
            nascoste = self.cfg.setdefault("liste_nascoste", [])
            if d not in nascoste:
                nascoste.append(d)
            LISTE_NASCOSTE.add(d)
        if self.cfg.get("lista") == d:
            self.cfg.pop("lista", None)
        scrivi_config(self.cfg)
        self.rifai_liste()

    def togli_lista(self):
        """Toglie la lista selezionata. Il file sul disco non si tocca."""
        dove = self.lista_selezionata()
        if not dove:
            self.scrivi(_("select the playlist to remove first"))
            return
        if dove not in self.cfg.get("liste", []):
            self.nascondi_lista(dove)
            return
        self.cfg["liste"] = [d for d in self.cfg.get("liste", []) if d != dove]
        if self.cfg.get("lista") == dove:
            self.cfg.pop("lista", None)
        scrivi_config(self.cfg)
        self.rifai_liste()


    def filtra(self):
        if self.canali and self.media_in_uso is None and self.reg_in_uso is None:
            self._filtra_iptv_async()
            return

        self._filtra_originale()

    def _filtra_iptv_async(self):
        token = getattr(self, "_xvb_filtra_token", 0) + 1
        self._xvb_filtra_token = token

        canali = list(self.canali)
        query = self.cerca.get().lower()
        nomi = dict(self.cfg.get("nomi_canali", {}))
        categoria = getattr(self, "_categoria_m3u_attiva", None)

        mappa = {}
        if categoria and categoria[0] == self.cfg.get("lista"):
            mappa = dict(
                getattr(self, "_mappa_categorie_m3u", {}).get(
                    categoria[0], {}
                )
            )

        self._render_token = getattr(self, "_render_token", 0) + 1
        self.elenco.delete(*self.elenco.get_children())
        self.applica_poster()
        self._render_colori = []
        self._render_adesso = self.file_in_onda or self.cfg.get("canale")

        def lavora():
            try:
                posti = {
                    url: i
                    for i, (_nome, url, _ide) in enumerate(canali)
                    if os.path.isabs(url)
                }

                if query:
                    filtrati = []
                    for canale in canali:
                        nome, url, _ide = canale
                        testo = nome + "\\n" + nomi.get(url, "")

                        if os.path.isabs(url):
                            try:
                                titolo, sotto = self.righe_file(url, nome)
                                testo += "\\n" + titolo + "\\n" + sotto
                            except Exception:
                                pass

                        if query in testo.lower():
                            filtrati.append(canale)
                else:
                    filtrati = canali

                if categoria and categoria[0] == self.cfg.get("lista"):
                    filtrati = [
                        c for c in filtrati
                        if mappa.get(c[1], _cat_vuota(categoria[0])) == categoria[1]
                    ]

                risultato = (filtrati, posti, None)

            except Exception as e:
                risultato = (None, None, str(e))

            try:
                self.root.after(
                    0,
                    lambda: self._filtra_iptv_completato(token, risultato)
                )
            except Exception:
                pass

        threading.Thread(
            target=lavora,
            name="XVB-IPTV-Filter",
            daemon=True
        ).start()

    def _filtra_iptv_completato(self, token, risultato):
        if token != getattr(self, "_xvb_filtra_token", None):
            return

        # Non aggiornare la lista se nel frattempo e' stata aperta
        # una libreria locale o le registrazioni.
        if self.media_in_uso is not None or self.reg_in_uso is not None:
            return

        filtrati, posti, errore = risultato

        if errore:
            self.scrivi(
                _("Error filtering channels: %s") % errore,
                errore=True
            )
            return

        self.visti = filtrati
        self.posto_file = posti

        if not self.cerca.get():
            self.indice_fresco = False

        token_render = getattr(self, "_render_token", 0) + 1
        self._render_token = token_render
        self._render_colori = []
        self._render_adesso = self.file_in_onda or self.cfg.get("canale")

        self._disegna_blocco(token_render, 0)

    def _filtra_originale(self):

        """Filtra tutti i contenuti e li disegna a piccoli blocchi.

        La ricerca lavora sull'intera libreria; soltanto la costruzione
        grafica viene suddivisa, per non bloccare Tk con migliaia di righe."""
        if getattr(self, "_media_scansione_in_corso", False):
            self._media_scan_streaming = False
        if getattr(self, "_media_render_attivo", False):
            self._media_render_token = getattr(self, "_media_render_token", 0) + 1
            self._media_render_attivo = False
        in_url = bool(self.media_in_uso and self.media_in_uso[0] == "url")
        self.posto_file = {u: i for i, (_n, u, _i) in enumerate(self.canali)
                           if os.path.isabs(u) or in_url}
        q = self.cerca.get().lower()
        nomi = self.cfg.get("nomi_canali", {})
        fonte = self.canali
        if q and self.cerca_ovunque() and not getattr(self, "_media_scansione_in_corso", False):
            self.prepara_indice()
            gia = {c[1] for c in self.canali}
            fonte = list(self.canali) + [c for c in getattr(self, "indice_ricerca", []) if c[1] not in gia]
        if q:
            self.visti = [c for c in fonte if q in self.testo_cercato(c, nomi)]
        else:
            self.visti = list(self.canali)
            self.indice_fresco = False

        categoria_attiva = getattr(self, "_categoria_m3u_attiva", None)
        if categoria_attiva and categoria_attiva[0] == self.cfg.get("lista"):
            mappa = getattr(self, "_mappa_categorie_m3u", {}).get(
                categoria_attiva[0], {})
            self.visti = [
                c for c in self.visti
                if mappa.get(c[1], _cat_vuota(categoria_attiva[0])) == categoria_attiva[1]
            ]

        token = getattr(self, "_render_token", 0) + 1
        self._render_token = token
        self.elenco.delete(*self.elenco.get_children())
        self.applica_poster()
        self._render_colori = []
        self._render_adesso = self.file_in_onda or self.cfg.get("canale")
        self._disegna_blocco(token, 0)

    def _disegna_blocco(self, token, inizio):
        """Disegna al massimo 100 righe per giro del ciclo grafico."""
        if token != getattr(self, "_render_token", None):
            return
        fine = min(inizio + 100, len(self.visti))
        for i in range(inizio, fine):
            nome, url, ide = self.visti[i]
            self.elenco.insert("", "end", iid=str(i), text=self.testo_riga(nome, url, ide),
                               image=self.pallino_di(nome, url),
                               colore=self.colore_canale(nome, url),
                               logo=self.logo_di(url, self.colore_canale(nome, url)) or self.icona_file_di(url))
            if url not in self.colori and url in LOGHI and url not in self.in_coda:
                self.in_coda.add(url)
                self._render_colori.append(url)
        if fine < len(self.visti):
            self.root.after(1, lambda: self._disegna_blocco(token, fine))
            return

        if self._render_colori:
            self.coda_colori.extend(self._render_colori)
            self._render_colori = []
        adesso = self._render_adesso
        if adesso and self.nome_in_onda:
            self.segna_in_onda(adesso)
        elif self.file_in_onda:
            self.segna_in_onda(self.file_in_onda)
        elif getattr(self, "fermato", None):
            i = next((k for k, c in enumerate(self.visti) if c[1] == self.fermato[2]), -1)
            if i >= 0 and self.elenco.exists(str(i)):
                self.elenco.selection_set(str(i))

    def cerca_ovunque(self):
        """La ricerca va su tutti i file, tranne quando a sinistra ci sono
        i canali di una playlist IPTV: li' cerca fra i canali."""
        return not (self.canali and not self.media_in_uso and not self.reg_in_uso)

    def prepara_indice(self):
        """Tutti i file di Local (cartelle e sottocartelle), Imported e
        Recordings, coi tag della musica (dalla cache; quelli mai letti si
        leggono ora). In un thread, una volta per ricerca: cosi' cartelle e
        file nuovi entrano, e intanto vale l'indice di prima."""
        if getattr(self, "indice_in_corso", False) or getattr(self, "indice_fresco", False):
            return
        self.indice_in_corso = True
        cartelle = list(self.cfg.get("cartelle", [])) + [REGISTRAZIONI]
        importati = list(self.cfg.get("importati", []))

        def lavora():
            tutti, visti = [], set()
            for c in cartelle:
                for x in media_in(c):
                    if x[1] not in visti:
                        visti.add(x[1])
                        tutti.append(x)
            for f in importati:
                if f not in visti and os.path.isfile(f):
                    visti.add(f)
                    tutti.append((os.path.splitext(os.path.basename(f))[0], f, None))
            nuovi = False
            for _n, f, _i in tutti:
                if f not in self.tag_file and os.path.splitext(f)[1][1:].lower() in AUDIO:
                    self.tag_file[f] = tag_di(f, self.tag_cache)
                    nuovi = True
            if nuovi:
                try:
                    scrivi_tag(self.tag_cache)
                except Exception:
                    pass
            self.root.after(0, lambda: self.indice_pronto(tutti))
        threading.Thread(target=lavora, daemon=True).start()

    def indice_pronto(self, tutti):
        self.indice_ricerca, self.indice_in_corso, self.indice_fresco = tutti, False, True
        if self.cerca.get():
            self.filtra()

    def testo_cercato(self, c, nomi):
        """Dove cerca la ricerca: il nome e quello dato dall'utente; per file,
        dischi e URL anche quello che si legge nella riga (titolo di TMDB,
        artista, album, anno)."""
        nome, url, _ide = c
        t = nome + "\n" + nomi.get(url, "")
        if self.media_in_uso or self.reg_in_uso or os.path.isabs(url):
            try:
                titolo, sotto = self.righe_file(url, nome)
                t += "\n" + titolo + "\n" + sotto
            except Exception:
                pass
        return t.lower()

    def testo_riga(self, nome, url, ide):
        """Le due righe di un elemento a sinistra: il nome e, sotto, il
        programma in onda (canali) o artista - titolo (file)."""
        if self.media_in_uso or self.reg_in_uso or os.path.isabs(url):
            nome, sotto = self.righe_file(url, nome)
        else:
            p = self.programma_di(ide, nome)
            sotto = p[2] if p else ""
            nome = self.cfg.get("nomi_canali", {}).get(url) or nome
        return " " + nome + ("\n " + sotto if sotto else "")

    def poster_attivo(self):
        if not self.cfg.get("poster_epg", False) or self.reg_in_uso:
            return False
        if self.media_in_uso:
            return self.media_in_uso[0] in ("cartella", "importati")
        return bool(self.epg)

    def altezza_poster_onda(self):
        """Riserva lo spazio in alto solo per i video in riproduzione (esclude l'audio)."""
        if (self.cfg.get("poster_epg", False) or not HA_PIL or self.reg_in_uso
                or getattr(self, "fermato", None)):
            return 0


        if self.file_in_onda:
            url = self.file_in_onda

            if os.path.splitext(url)[1][1:].lower() in AUDIO:
                if url not in self.anteprime:
                    self.copertina_onda(url)
                return POSTER_H if self.anteprime.get(url) else 0
            src = poster_film(url) or self.anteprime.get(url) or self.info.get(url, {}).get("poster")
            return POSTER_H if src else 0

        if self.media_in_uso:
            return 0

        url = self.cfg.get("canale")
        if not url or os.path.isabs(url):
            return 0
        for nome, u, ide in self.visti:
            if u == url:
                break
        else:
            return 0
        if not ide or ide not in self.epg:
            ide = self.epg_nomi.get(nome_piatto(nome))
        p = self.programma_di(ide, nome)
        if not p:
            return 0
        src = icona_programma_epg(ide, nome, p)
        if not src:
            return 0
        f = os.path.join(CACHE_POSTER, hashlib.md5(src.encode("utf-8")).hexdigest() + ".jpg")
        if os.path.isfile(f):
            return POSTER_H
        self.scarica_poster(src, f)
        return 0

    def copertina_onda(self, url):
        """In un thread: la copertina del brano in onda, se non c'e' ancora;
        poi il riquadro dell'anteprima si apre."""
        cerca = self.__dict__.setdefault("_copertina_in_corso", set())
        if url in cerca:
            return
        cerca.add(url)

        def lavora():
            try:
                f = anteprima_di(url)
            except Exception:
                f = None

            def fine():
                cerca.discard(url)
                self.anteprime[url] = f
                self.loghi_img.pop(url, None)
                if self.file_in_onda == url:
                    self.poster_scaricato()
            self.root.after(0, fine)
        threading.Thread(target=lavora, daemon=True).start()

    def poster_scaricato(self):
        self.applica_poster()
        self.elenco.ridisegna_colonna()

    def applica_poster(self):
        self.elenco.metti_poster(POSTER_H if self.poster_attivo() else 0)
        self.elenco.metti_poster_onda(0 if self.poster_attivo() else self.altezza_poster_onda())
        self.elenco.aggiorna_desc()

    def descrizione_di(self, iid):
        """La descrizione EPG del programma in onda sul canale (vuota se manca)."""
        if not self.epg or self.media_in_uso or self.reg_in_uso:
            return ""
        try:
            nome, url, ide = self.visti[int(iid)]
        except (ValueError, IndexError):
            return ""
        if os.path.isabs(url):
            return ""
        if not ide or ide not in self.epg:
            ide = self.epg_nomi.get(nome_piatto(nome))
        return descrizione_programma_epg(ide, nome, self.programma_di(ide, nome))

    def poster_si_no(self):
        self.cfg["poster_epg"] = not self.cfg.get("poster_epg", False)
        scrivi_config(self.cfg)
        self.applica_poster()

    def poster_di(self, iid, w, h):
        """Mostra l'anteprima per i canali TV, per i video locali e per la musica
        (con la sua copertina) in riproduzione."""
        try:
            nome, url, ide = self.visti[int(iid)]
        except (ValueError, IndexError):
            return None, ""

        if os.path.isabs(url):

            if os.path.splitext(url)[1][1:].lower() in AUDIO:
                if self.poster_attivo() or url == self.file_in_onda:
                    src = self.anteprime.get(url)
                    return (self.poster_pronto(src, w, h) if src else None), ""
                return None, ""


            if self.poster_attivo() or url == self.file_in_onda:
                src = poster_film(url) or self.anteprime.get(url) or self.info.get(url, {}).get("poster")
                return (self.poster_pronto(src, w, h) if src else None), ""
            return None, ""

        if not self.epg:
            return None, ""
        if not ide or ide not in self.epg:
            ide = self.epg_nomi.get(nome_piatto(nome))
        p = self.programma_di(ide, nome)
        if not p:
            return None, ""
        src = icona_programma_epg(ide, nome, p)
        return (self.poster_pronto(src, w, h) if src else None), p[2]

    def poster_pronto(self, src, w, h):
        """L'immagine del programma, ritagliata a riempire il riquadro; se non
        e' ancora sul disco la si scarica (un thread) e intanto None."""
        if not HA_PIL or w < 10:
            return None
        locale = os.path.isabs(src)
        if locale:
            try:
                src = "%s|%d" % (src, os.path.getmtime(src))
            except OSError:
                return None
        chiave = (src, w, h)
        img = self.poster_tk.get(chiave)
        if img is not None:
            return img
        pil = self.poster_pil.get(src)
        if pil is None:
            if locale:
                f = src.rsplit("|", 1)[0]
            else:
                f = os.path.join(CACHE_POSTER, hashlib.md5(src.encode("utf-8")).hexdigest() + ".jpg")
            if not os.path.isfile(f):
                if not locale:
                    self.scarica_poster(src, f)
                return None
            try:
                pil = Image.open(f).convert("RGB")
                pil.thumbnail((600, 600))
            except Exception:
                return None
            if len(self.poster_pil) > 150:
                self.poster_pil.clear()
            self.poster_pil[src] = pil
        k = max(w / float(pil.width), h / float(pil.height))
        im = pil.resize((max(w, int(round(pil.width * k))),
                         max(h, int(round(pil.height * k)))), Image.LANCZOS)

        x0, y0 = (im.width - w) // 2, int((im.height - h) * 0.3)
        if len(self.poster_tk) > 300:
            self.poster_tk.clear()
        from PIL import ImageDraw
        im = im.crop((x0, y0, x0 + w, y0 + h)).convert("RGBA")
        _m = Image.new("L", (w * 3, h * 3), 0)
        ImageDraw.Draw(_m).rounded_rectangle([0, 0, w * 3 - 1, h * 3 - 1], radius=RAGGIO * 3 - 6, fill=255)
        im.putalpha(_m.resize((w, h), Image.LANCZOS))
        img = ImageTk.PhotoImage(im)
        self.poster_tk[chiave] = img
        return img

    def scarica_poster(self, src, f):
        coda = self.__dict__.setdefault("poster_coda", [])
        gia = self.__dict__.setdefault("poster_gia", set())
        if src in gia:
            return
        gia.add(src)
        coda.append((src, f))
        if not self.__dict__.get("poster_thread"):
            self.poster_thread = True
            threading.Thread(target=self.lavora_poster, daemon=True).start()

    def lavora_poster(self):
        """In un thread: le immagini in coda, una alla volta, in ~/.cache/xvb/poster."""
        import io
        while self.poster_coda:
            src, f = self.poster_coda.pop(0)
            try:
                dati = urlopen(Request(src, headers={"User-Agent": UA}),
                               timeout=10).read(4 * 1024 * 1024)
                im = Image.open(io.BytesIO(dati)).convert("RGB")
                im.thumbnail((600, 600))
                os.makedirs(CACHE_POSTER, exist_ok=True)
                im.save(f, "JPEG", quality=85)
                self.root.after(0, self.poster_scaricato)
            except Exception:
                pass
        self.poster_thread = False

    def progresso_di(self, iid):
        """Per la barra in fondo alla voce, quanto e' fatto (0-1): il
        programma in onda dei canali, la posizione ricordata dei film
        iniziati. None negli altri casi."""
        try:
            nome, url, ide = self.visti[int(iid)]
        except (ValueError, IndexError):
            return None
        if self.media_in_uso or self.reg_in_uso or os.path.isabs(url):
            if not os.path.isabs(url):
                return None
            pos = self.cfg.get("posizioni", {}).get(url)
            if not pos:
                return None
            dur = self.cfg.get("durate", {}).get(url)
            if not dur:
                self.cerca_durata(url)
                return None
            return max(0.0, min(1.0, pos / float(dur)))
        if not self.epg:
            return None
        p = self.programma_di(ide, nome)
        if not p or p[1] <= p[0]:
            return None
        return max(0.0, min(1.0, (time.time() - p[0]) / float(p[1] - p[0])))

    def cerca_durata(self, f):
        """In un thread: la durata dei film iniziati prima che la si
        segnasse; poi le barre si ridisegnano."""
        cercate = self.__dict__.setdefault("durate_cercate", set())
        if f in cercate:
            return
        cercate.add(f)

        def salva(d):
            self.cfg.setdefault("durate", {})[f] = round(d, 1)
            self.elenco.ridisegna_colonna()

        def lavora():
            d = durata_di(f)
            if d:
                self.root.after(0, lambda: salva(d))
        threading.Thread(target=lavora, daemon=True).start()

    def righe_file(self, f, nome):
        """Titolo e sottotitolo di un file. Film: il titolo di TMDB e sotto
        '2023 · Drama · ★ 7.8' (senza info, l'anno dal nome). Musica: il
        titolo e sotto 'Artista · Album · 1975'."""
        if e_cd(f):
            return nome, _("Audio CD")
        if f.startswith("dvd://"):
            i = self.info.get(f)
            if not i:
                return nome, "DVD"
            pezzi = ["DVD", i.get("anno", "")] + (i.get("generi") or [])[:1]
            if i.get("voti"):
                pezzi.append("\u2605 %.1f" % float(i.get("voto", 0)))
            return i.get("titolo") or nome, " \u00b7 ".join(p for p in pezzi if p)
        if not os.path.isabs(f):
            return nome, (tipo_url(f) if self.media_in_uso and self.media_in_uso[0] == "url" else "")
        if os.path.splitext(f)[1][1:].lower() in AUDIO:
            titolo, artista, album, anno = self.tag_file.get(f, ("", "", "", ""))
            if not (titolo or artista or album):

                titolo, artista, album, anno = dal_percorso(f, self.cfg.get("cartelle", []))
            trovato = self.info.get(f, {})
            album = album or trovato.get("album", "")
            anno = anno or trovato.get("anno", "") or self.info.get(os.path.dirname(f), {}).get("anno", "")
            return titolo or nome, " \u00b7 ".join(x for x in (artista, album, anno) if x)
        if f.startswith(REGISTRAZIONI + os.sep):

            ora = getattr(self, "ora_reg", {}).get(f, "")
            try:
                durata = _DURATE.get((f, os.path.getmtime(f)))
            except OSError:
                durata = None
            return nome, " \u00b7 ".join(x for x in (ora, ore_min_sec(durata) if durata else "") if x)
        i = self.info.get(f)
        if not i:
            titolo, anno = titolo_anno(f) if not f.startswith(REGISTRAZIONI + os.sep) else (nome, None)
            tag = self.tag_file.get(f, ("", "", "", ""))
            return tag[0] or nome, anno or ""
        pezzi = [i.get("anno", "")] + (i.get("generi") or [])[:1]
        if i.get("voti"):
            pezzi.append("\u2605 %.1f" % float(i.get("voto", 0)))
        return i.get("titolo") or nome, " \u00b7 ".join(p for p in pezzi if p)

    def aggiorna_sottotitoli(self):
        """Ogni minuto: i programmi in onda sotto ai nomi dei canali
        cambiano da soli."""
        if self.epg and not self.media_in_uso and not self.reg_in_uso:
            for i, (nome, url, ide) in enumerate(self.visti):
                if not self.elenco.exists(str(i)):
                    continue
                t = self.testo_riga(nome, url, ide)
                if self.elenco.item(str(i), "text") != t:
                    self.elenco.item(str(i), text=t)
        self.applica_poster()
        self.elenco.ridisegna_colonna()
        self.root.after(60000, self.aggiorna_sottotitoli)

    def tagga(self, chiave):
        """In un thread: i tag dei file a sinistra; poi la lista si
        ridisegna, se e' ancora quella."""
        canali = list(self.canali)
        nuovi = False
        for _n, f, _i in canali:
            if f not in self.tag_file:
                self.tag_file[f] = tag_di(f, self.tag_cache)
                nuovi = True
        if nuovi:
            scrivi_tag(self.tag_cache)

        if self.media_in_uso == chiave:
            if chiave[0] == "cartella" and not self.cfg.get("shuffle"):
                def chiave_titolo(canale):
                    nome, f, _ide = canale
                    tag = self.tag_file.get(f, ("", "", "", ""))
                    info = self.info.get(f, {})
                    titolo = (
                        (tag[0] if tag else "")
                        or info.get("titolo", "")
                        or nome
                    )
                    return str(titolo).strip().casefold()

                ordinati = sorted(canali, key=chiave_titolo)
                self.root.after(
                    0,
                    lambda k=chiave, lista=ordinati:
                        self._applica_ordine_titolo(k, lista)
                )
            elif nuovi:
                self.root.after(0, self.filtra)

            if nuovi:
                self.root.after(0, self.scrivi_riga)


        if self.cfg.get("segno", "logo") == "logo":
            fatte, ultimo = 0, time.time()
            for _n, f, _i in canali:
                if self.media_in_uso != chiave or self.cfg.get("segno", "logo") != "logo":
                    return
                vecchia = self.anteprime.get(f)
                nuova = anteprima_di(f)
                if f in self.anteprime and nuova == vecchia:
                    continue
                self.anteprime[f] = nuova
                self.loghi_img.pop(f, None)
                fatte += 1


            if fatte and self.media_in_uso == chiave:
                self.root.after(0, self.filtra)

    def _applica_ordine_titolo(self, chiave, ordinati):
        """Applica l'ordine alfabetico per titolo dopo la lettura dei tag."""
        if self.media_in_uso != chiave or self.cfg.get("shuffle"):
            return
        self.canali = list(ordinati)
        self.ordine_vero = list(ordinati)
        self.filtra()
        self.scrivi_riga()

    def colore_canale(self, nome, url):
        """Il colore del canale: quello preso dal suo logo, se lo si ha;
        se no quello dal nome. Una registrazione ha quello del suo canale
        (la cartella in cui sta). Un file (musica, video) il suo posto
        nell'elenco, sulla scala dei file."""
        if url.startswith(REGISTRAZIONI + os.sep):
            return colore_di(os.path.basename(os.path.dirname(url)))
        if url in self.posto_file:
            return SCALA_FILE[self.posto_file[url] % len(SCALA_FILE)]
        return (self.cfg.get("colori_canali", {}).get(url) or self.colori.get(url)
                or colore_di(nome))

    def pallino_di(self, nome, url):
        c = self.colore_canale(nome, url)
        if c not in self.pallini:
            self.pallini[c] = pallino(c) or self.vuoto
        return self.pallini[c]

    def lavora_colori(self):
        """In un thread: prende i loghi in coda uno alla volta, ne tira
        fuori il colore e aggiorna il pallino nell'elenco."""
        while True:
            if not self.coda_colori:
                time.sleep(0.3)
                continue
            url = self.coda_colori.pop(0)
            try:
                c = colore_dal_logo(LOGHI[url])
            except Exception:
                c = None
            self.colori[url] = c or ""
            if not self.coda_colori or time.time() - getattr(self, "colori_scritti", 0) > 2:
                self.colori_scritti = time.time()
                scrivi_colori(self.colori)
            if c:
                self.root.after(0, self.aggiorna_pallino, url, c)

    def metti_segno(self, k):
        """Dal menu View: loghi, pallini o bordo davanti ai canali."""
        self.cfg["segno"] = k
        scrivi_config(self.cfg)
        self.elenco.segno = k
        self.elenco.tinte = set()
        self.filtra()
        if k == "logo" and self.media_in_uso:
            threading.Thread(target=self.tagga, args=(self.media_in_uso,), daemon=True).start()

    def icona_file_di(self, url):
        """Per un file senza copertina ne' poster (musica e video privati):
        l'icona dell'app, icon.png, nella colonna dei loghi."""
        if url.startswith("dvd://"):
            if "disco_grande" not in self.img_liste:
                self.img_liste["disco_grande"] = disco(TESTO, lato=LOGO_L, alta=LOGO_A) \
                    if os.path.isfile(os.path.join(QUI, "icone", "disco.png")) else disco(TESTO, lato=LOGO_A)
            return self.img_liste["disco_grande"]

        in_url = bool(self.media_in_uso and self.media_in_uso[0] == "url")
        if not (os.path.isabs(url) or in_url) or not self.icona_file or not HA_PIL:
            return None
        if getattr(self, "icona_colonna", None) is None:
            self.icona_colonna = carica_logo(self.icona_file, LOGO_L, LOGO_A)
        return self.icona_colonna

    def logo_di(self, url, colore=None):
        """Il logo del canale come immagine per l'elenco, dalla cache dei
        loghi (quella scaricata per i colori). None se non c'e' ancora."""
        cd = self.__dict__.setdefault("_colori_dischi", {})
        if url in self.loghi_img and (colore is None or cd.get(url, colore) == colore):
            return self.loghi_img[url]
        if not HA_PIL:
            return None
        if os.path.isabs(url):
            f = self.anteprime.get(url)
            if not f:
                return None
        elif os.path.isfile(copertina_url(url)):
            f = copertina_url(url)
        elif url not in LOGHI:
            return None
        else:
            f = os.path.join(CACHE_LOGHI, hashlib.md5(LOGHI[url].encode()).hexdigest())
        if not os.path.isfile(f) or os.path.getsize(f) == 0:
            return None


        iptv = f.startswith(CACHE_LOGHI + os.sep)

        film = url.startswith("dvd://") or (os.path.isabs(url)
                                             and os.path.splitext(url)[1][1:].lower() not in AUDIO)
        if os.path.isabs(url) and os.path.splitext(url)[1][1:].lower() in AUDIO:
            im = disco_vinile(f, LOGO_L, LOGO_A, colore)
            cd[url] = colore
        else:
            im = carica_logo(f, LOGO_L, LOGO_A, margine=6 if iptv else 0, quadrata=film)
        if im is not None:
            self.loghi_img[url] = im
        return im

    def _giradisco(self):
        """Il vinile: la copertina tonda del brano in onda gira piano in senso
        orario finche' suona; in pausa si ferma dov'e'; cambiando brano la
        riga di prima torna ferma."""
        try:
            self._giradisco_passo()
        except Exception:
            pass
        self.root.after(40, self._giradisco)

    def _giradisco_riga(self, url, im, fermo=False):
        """Girando, cambia solo la copertina nella colonna dei loghi (ridisegnare
        la riga a ogni passo andrebbe a scatti); a fine rotazione la riga si
        ridisegna una volta, con la copertina ferma."""
        for i, (_n, u, _l) in enumerate(self.visti):
            iid = str(i)
            if u != url or not self.elenco.exists(iid):
                continue
            if fermo:
                self.elenco.item(iid, logo=self.logo_di(url, self.elenco.dati[iid].get("colore"))
                                 or self.icona_file_di(url))
            else:
                self.elenco.dati[iid]["logo"] = im
                self.elenco.colonna.itemconfigure("lg_" + iid, image=im)

    def _giradisco_passo(self):
        st = self._disco
        url = self.file_in_onda
        audio = bool(HA_PIL and url and os.path.isabs(url)
                     and os.path.splitext(url)[1][1:].lower() in AUDIO)
        if st["url"] and st["url"] != url:
            vecchio = st["url"]
            st["url"] = st["base"] = st["img"] = None
            self._giradisco_riga(vecchio, None, fermo=True)
        adesso = time.monotonic()
        dt = min(0.2, adesso - st.get("t", adesso))
        st["t"] = adesso
        if not audio or getattr(self, "mpv", None) is None or self.mpv.pause:
            return
        if st["url"] != url:
            f = self.anteprime.get(url)
            if not f or not os.path.isfile(f):
                return
            colore = next((self.elenco.dati[str(i)].get("colore")
                           for i, (_n, u, _l) in enumerate(self.visti)
                           if u == url and self.elenco.exists(str(i))), None)
            st.update(url=url, ang=0.0, colore=colore, base=disco_base(f, LOGO_A * 3))
        st["ang"] = (st["ang"] + 60.0 * dt) % 360.0
        st["img"] = ImageTk.PhotoImage(
            disco_frame(st["base"], LOGO_L, LOGO_A, st["ang"], st.get("colore")))
        self._giradisco_riga(url, st["img"])

    def aggiorna_pallino(self, url, c):
        for i, (nome, u, _i) in enumerate(self.visti):
            if u == url and self.elenco.exists(str(i)):
                self.elenco.item(str(i), image=self.pallino_di(nome, u),
                                 logo=self.logo_di(u, self.colore_canale(nome, u)) or self.icona_file_di(u))
        if url == self.cfg.get("canale"):
            self.colora_stato(c)
            self.segna_in_onda(url)

    def parti(self):
        s = self.elenco.selection()
        if not s:
            return

        nome, url, ide = self.visti[int(s[0])]

        fermato = getattr(self, "fermato", None)
        if fermato and fermato[2] == url:
            self.pausa()
            return

        corrente = self.file_in_onda or self.cfg.get("canale")

        if corrente == url:
            try:
                if self.mpv.pause:
                    self.pausa()
            except Exception:
                pass
            return

        self.apri(nome, url, ide)

    def vista_corrente(self):
        """Cosa mostra l'elenco di sinistra: registrazioni, file, oppure
        una lista IPTV (con l'eventuale categoria M3U)."""
        if self.reg_in_uso is not None:
            return ("reg", self.reg_in_uso)
        if self.media_in_uso is not None:
            return ("media", self.media_in_uso)
        cat = getattr(self, "_categoria_m3u_attiva", None)
        return ("iptv", self.cfg.get("lista"), tuple(cat) if cat else None)

    def attivita(self, e=None):
        """Qualsiasi mouse, rotella o tasto: i 3 secondi ripartono."""
        self._ultima_att = time.monotonic()
        if self._att_onda is not None:
            try:
                self.root.after_cancel(self._att_onda)
            except Exception:
                pass
            self._att_onda = None
        if self._t_inatt is None:
            self._t_inatt = self.root.after(3000, self._inattivo)

    def _inattivo(self):
        self._t_inatt = None
        resto = 3.0 - (time.monotonic() - self._ultima_att)
        if resto > 0.05:
            self._t_inatt = self.root.after(int(resto * 1000) + 50, self._inattivo)
            return
        try:
            self.controlla_in_onda()
        except Exception:
            logging.exception("ritorno al canale in onda")

    def controlla_in_onda(self):
        """Dopo 3 secondi senza muovere niente: a sinistra torna la vista
        di quello che suona (lista, categoria, file...) e la sua scheda
        va in cima."""
        if (self.cerca.get() or not self.elenco.winfo_viewable()
                or self.root.grab_current() is not None):
            return
        url = self.file_in_onda or self.cfg.get("canale")
        if not url:
            return
        voluta = self._vista_onda or self.vista_corrente()
        if self.vista_corrente() != voluta:
            self.vai_alla_vista(voluta)
        self._att_onda = self.root.after(
            250, lambda: self._assesta_in_onda(url, voluta, 0, 0))

    def vai_alla_vista(self, v):
        if v[0] == "reg":
            self.mostra_registrazioni(v[1])
            return
        if v[0] == "media":
            self.mostra_media(v[1])
            return
        _t, lista, cat = v
        if not lista or lista not in self.liste:
            return
        self._categoria_m3u_attiva = cat
        if cat:
            for _d, _iid in self.iid_di.items():
                if self.el_liste.exists(_iid):
                    self.tag_riga(_iid, False)
            for _iid, (_d, _c) in self.iid_categoria_m3u.items():
                if self.el_liste.exists(_iid):
                    self.tag_riga(_iid, (_d, _c) == tuple(cat))
        else:
            for _iid in self.iid_categoria_m3u:
                if self.el_liste.exists(_iid):
                    self.tag_riga(_iid, False)
            for _d, _iid in self.iid_di.items():
                if self.el_liste.exists(_iid):
                    self.tag_riga(_iid, _d == lista)
        if (lista != self.cfg.get("lista") or self.reg_in_uso is not None
                or self.media_in_uso is not None):
            self.reg_in_uso = self.media_in_uso = None
            self.carica(lista)
        else:
            self.filtra()

    def _assesta_in_onda(self, url, voluta, tent, fermi):
        """Finche' la vista non e' pronta (caricamenti in background) si
        riprova ogni 250 ms, poi si controlla che la scheda resti in cima."""
        self._att_onda = None
        if tent > 80:
            return
        if self.vista_corrente() == voluta:
            i = next((k for k, c in enumerate(self.visti) if c[1] == url), -1)
            if i >= 0 and self.elenco.exists(str(i)):
                if self.in_cima(str(i)):
                    fermi += 1
                    if fermi >= 3:
                        return
                else:
                    fermi = 0
        self._att_onda = self.root.after(
            250, lambda: self._assesta_in_onda(url, voluta, tent + 1, fermi))

    def in_cima(self, iid):
        # Porta la scheda in cima; True se ci e' (o e' tra le ultime).
        el = self.elenco
        r = el.riga(iid)

        def prima():
            return int(el.index("@0,0").split(".")[0])

        if prima() == r:
            return True
        try:
            y = int(el.tk.call(el._w, "count", "-ypixels", "1.0", "%d.0" % r))
            tot = int(el.tk.call(el._w, "count", "-ypixels", "1.0", "end"))
            el.yview_moveto(y / float(max(tot, 1)))
        except Exception:
            tk.Text.yview(el, "%d.0" % r)
        el.update_idletasks()
        return prima() == r or el.yview()[1] >= 0.999

    def segna_in_onda(self, url):
        """La riga di quello che si sta guardando: fondo argento fisso
        (IN_RIPRODUZIONE), testo e pallino neri. Il colore del canale resta
        su comandi, riga di stato e linea di avanzamento. Quella di prima
        torna normale."""
        self.applica_poster()
        for i, (nome, u, _l) in enumerate(self.visti):
            if not self.elenco.exists(str(i)):
                continue
            if u == url:
                colore = IN_RIPRODUZIONE
                r, g, b = (int(colore[k:k + 2], 16) for k in (1, 3, 5))


                def lin(c):
                    c /= 255.0
                    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
                luce = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
                testo = "#000000" if luce > 0.4 else "#ffffff"
                self.elenco.tag_configure("onda", background=colore, foreground=testo)
                if testo not in self.pallini:
                    self.pallini[testo] = pallino(testo) or self.vuoto
                self.elenco.item(str(i), tags=("onda",), image=self.pallini[testo])
            elif "onda" in self.elenco.item(str(i), "tags"):


                self.elenco.item(str(i), tags=(), image=self.pallino_di(nome, u))
        self.non_sul_verde(self.elenco, "onda")

    def salta(self, dove):
        """Il canale (o il file) prima o dopo, nell'elenco come lo si vede
        adesso: con lo shuffle l'elenco e' gia' mescolato."""
        if not self.visti:
            return
        adesso = self.file_in_onda or self.cfg.get("canale")
        i = getattr(self, "indice", -1)
        if not (0 <= i < len(self.visti) and self.visti[i][1] == adesso):
            i = next((k for k, c in enumerate(self.visti) if c[1] == adesso), -1)
        i = (i + dove) % len(self.visti) if i >= 0 else 0
        if self.elenco.exists(str(i)):
            self.elenco.selection_set(str(i))
            self.elenco.see(str(i))
        nome, url, ide = self.visti[i]
        self.apri(nome, url, ide)

    def preferito(self):
        """Quello che va entra nei preferiti, o ne esce se c'e' gia'. Tre
        liste in playlists/favorite/: iptv.m3u per quello che viene dalle
        playlist IPTV, music.m3u per i brani e video.m3u per i video sul
        disco. Niente per quello aperto con Open URL e per i DVD (vedi
        cuore_possibile)."""
        url = self.file_in_onda or self.cfg.get("canale")
        if not self.nome_in_onda or not self.cuore_possibile(url):
            return
        media = tipo_preferiti(url)
        pref = leggi_preferiti_cat(media)
        if self.e_preferito(pref, url, self.nome_in_onda):
            pref = [c for c in pref if not (c[1] == url or
                    nome_piatto(c[0]) == nome_piatto(self.nome_in_onda))]
            self.scrivi(_("%s removed from favorites") % self.nome_in_onda)
        else:
            playlist = self.scegli_playlist_preferiti(media, self.nome_in_onda)
            if playlist is None:
                return
            pref.append((self.nome_in_onda, url, self.ide_in_onda, playlist))
            self.scrivi(_("%s added to favorites") % self.nome_in_onda)
        try:
            scrivi_preferiti(pref, media)
        except Exception as e:
            self.scrivi(_("cannot write favorites: %s") % e, errore=True)
            return
        self.icona_preferito()
        self.rifai_dopo_preferiti(media)

    def rifai_dopo_preferiti(self, media):
        """Dopo aver scritto un file dei preferiti: si rilegge subito (se no
        le playlist nell'albero restano quelle di prima) e si rifa' l'albero."""
        f = file_preferiti(media)
        try:
            leggi_lista(f)
        except Exception:
            pass
        if self.cfg.get("lista") == f:
            if os.path.isfile(f):
                self.rifai_albero()
                self.carica(f)
            else:
                self.rifai_liste()
        else:
            self.rifai_albero()

    def scegli_playlist_preferiti(self, media, nome):
        """Dove mettere il preferito: una delle playlist del suo tipo (iptv,
        music o video), o una nuova. None se si annulla."""
        f = file_preferiti(media)
        esistenti = []
        if os.path.isfile(f):
            try:
                esistenti = sorted(set(categorie_m3u(f).values()), key=str.casefold)
            except Exception:
                esistenti = []
        ultima = self.cfg.get("ultima_playlist_pref", {}).get(media)
        nuova = "\x00nuova"
        scelta = tk.StringVar(value=ultima if ultima in esistenti
                              else (esistenti[0] if esistenti else nuova))

        def corpo(dentro):
            tk.Label(dentro, text=_("Favorite"), bg=PANNELLO, fg=TESTO, anchor="w",
                     font=(CARATTERE, 11, "bold", STILE_CARATTERE)).pack(fill="x")
            tk.Label(dentro, text=nome, bg=PANNELLO, fg=GRIGIO, anchor="w",
                     justify="left", wraplength=360).pack(fill="x", pady=(2, 0))
            if esistenti:
                tk.Label(dentro, text=_("Save in:"), bg=PANNELLO, fg=GRIGIO, anchor="w"
                         ).pack(fill="x", pady=(10, 2))
                for n in esistenti + [None]:
                    tk.Radiobutton(dentro, text=n or _("New playlist..."), variable=scelta,
                                   value=n or nuova, anchor="w",
                                   bg=PANNELLO, fg=TESTO, selectcolor=CERCA, bd=0,
                                   activebackground=PANNELLO, activeforeground="#ffffff",
                                   highlightthickness=0, cursor="hand2").pack(fill="x")
        f_dlg = Finestrella(self.root, _("Favorite"), corpo, chiedi=_("Name:"),
                            valore="" if esistenti else "MyPlaylist")

        def digita(e):
            if e.keysym not in ("Return", "KP_Enter", "Escape", "Tab"):
                scelta.set(nuova)
        f_dlg.casella.bind("<Key>", digita)
        self.root.wait_window(f_dlg)
        if f_dlg.risposta is None:
            return None
        if scelta.get() == nuova:
            n = f_dlg.risposta.strip().replace('"', "'") or "MyPlaylist"
            n = next((e for e in esistenti if e.casefold() == n.casefold()), n)
        else:
            n = scelta.get()
        self.cfg.setdefault("ultima_playlist_pref", {})[media] = n
        scrivi_config(self.cfg)
        return n

    def rinomina_playlist_pref(self, d, cat):
        media = os.path.splitext(os.path.basename(d))[0]
        nome = self.chiedi(_("Rename..."), _("Name:"), cat)
        if not nome or not nome.strip() or nome.strip() == cat:
            return
        nome = nome.strip().replace('"', "'")
        pref = leggi_preferiti_cat(media)
        nome = next((e for e in {v[3] for v in pref}
                     if e != cat and e.casefold() == nome.casefold()), nome)
        pref = [(v[0], v[1], v[2], nome if v[3] == cat else v[3]) for v in pref]
        try:
            scrivi_preferiti(pref, media)
        except Exception as e:
            self.scrivi(_("cannot write favorites: %s") % e, errore=True)
            return
        if getattr(self, "_categoria_m3u_attiva", None) == (d, cat):
            self._categoria_m3u_attiva = (d, nome)
        ultime = self.cfg.get("ultima_playlist_pref", {})
        if ultime.get(media) == cat:
            ultime[media] = nome
            scrivi_config(self.cfg)
        self.rifai_dopo_preferiti(media)

    def togli_playlist_pref(self, d, cat):
        media = os.path.splitext(os.path.basename(d))[0]
        if not self.conferma_via(cat, _("The playlist will be removed from the list.")):
            return
        pref = [v for v in leggi_preferiti_cat(media) if v[3] != cat]
        try:
            scrivi_preferiti(pref, media)
        except Exception as e:
            self.scrivi(_("cannot write favorites: %s") % e, errore=True)
            return
        if getattr(self, "_categoria_m3u_attiva", None) == (d, cat):
            self._categoria_m3u_attiva = None
        self.icona_preferito()
        self.rifai_dopo_preferiti(media)

    def e_preferito(self, pref, url, nome):
        """Un canale e' fra i preferiti se c'e' con lo stesso indirizzo, o
        con lo stesso nome (stesso canale preso da un'altra lista)."""
        piatto = nome_piatto(nome)
        return any(v[1] == url or (piatto and nome_piatto(v[0]) == piatto)
                   for v in pref)

    def cuore_possibile(self, url):
        """Il cuore solo per quello che si ha davvero: i file sul disco e i
        canali (o video) che vengono da una playlist aggiunta. Non per quello
        aperto al volo con Open URL, e non per i DVD."""
        if not PREFERITI_ACCESI or not url or url.startswith("dvd://") or e_cd(url):
            return False
        if os.path.isabs(url):
            return e_media(url)
        return url != getattr(self, "al_volo", None)

    def icona_preferito(self):
        url = self.file_in_onda or self.cfg.get("canale")

        if (not self.cuore_possibile(url) or not (self.file_in_onda or self.nome_in_onda)
                or getattr(self, "fermato", None)):
            self.b_pref.pack_forget()
            return
        if not self.b_pref.winfo_manager():
            self.b_pref.pack(side="right", padx=2, pady=6, before=self.b_pieno)
        acceso = bool(url) and self.e_preferito(leggi_preferiti(tipo_preferiti(url)), url,
                                                self.nome_in_onda)
        self.faccia(self.b_pref, "favorite_on" if acceso else "favorite_off",
                    _("Favorite"))

    def shuffle(self):
        """Shuffle: l'elenco dei file a sinistra (cartelle, importati,
        registrazioni) viene mescolato, e poi si scorre dall'alto in basso
        come sempre; spento, torna l'ordine di prima. I canali no."""
        self.cfg["shuffle"] = not self.cfg.get("shuffle")
        scrivi_config(self.cfg)
        self.icona_shuffle()
        if self.media_in_uso or self.reg_in_uso:
            if self.cfg["shuffle"]:
                self.mescola()
            elif self.ordine_vero:
                self.canali = list(self.ordine_vero)
                self.filtra()
                self.mostra_in_onda()

    def mescola(self, disegna=True):
        """I file a sinistra in ordine casuale, quello in onda in cima."""
        self.ordine_vero = list(self.canali)
        mischiati = list(self.canali)
        random.shuffle(mischiati)
        in_onda = [c for c in mischiati if c[1] == self.file_in_onda]
        self.canali = in_onda + [c for c in mischiati if c[1] != self.file_in_onda]
        if disegna:
            self.filtra()
            self.mostra_in_onda()

    def mostra_in_onda(self):
        i = next((k for k, c in enumerate(self.visti) if c[1] == self.file_in_onda), 0)
        if self.elenco.exists(str(i)):
            self.elenco.see(str(i))

    def icona_shuffle(self):
        """Bianca da spento, del colore del canale da acceso."""
        if not hasattr(self, "b_shuffle"):
            return
        if self.cfg.get("shuffle") and HA_PIL and "shuffle" in self.icone_pil and self.colore_barra:
            im = self.icone_pil["shuffle"].copy()
            r, g, b = (int(self.colore_barra[k:k + 2], 16) for k in (1, 3, 5))
            tinta = Image.new("RGBA", im.size, (r, g, b, 255))
            tinta.putalpha(im.getchannel("A"))
            self.icone_pil["shuffle_on"] = tinta
            self.icone["shuffle_on"] = ImageTk.PhotoImage(tinta)
            self.faccia(self.b_shuffle, "shuffle_on", _("Shuffle"))
        else:
            self.faccia(self.b_shuffle, "shuffle", _("Shuffle"))

    def switch(self):
        """Torna al canale visto prima di questo; premuto ancora, torna
        qui: fa avanti e indietro fra gli ultimi due."""
        if not self.precedente:
            return
        nome, url = self.precedente
        nome = nome or "canale"
        i = next((k for k, c in enumerate(self.visti) if c[1] == url), -1)
        if i >= 0 and self.elenco.exists(str(i)):
            nome = self.visti[i][0]
            self.elenco.selection_set(str(i))
            self.elenco.see(str(i))
        self.apri(nome, url)

    def apri(self, nome, url, ide=None):
        self.fermato = None
        self.faccia(self.b_pausa, "pausa", _("Pause"))
        if getattr(self, "mai_partito", False):
            self.mai_partito = False
            self.root.after_idle(self.posa_barra)


        self.al_volo = url if (self.media_in_uso and self.media_in_uso[0] == "url") else None
        if url.startswith("dvd://") or e_cd(url) or url.startswith(REGISTRAZIONI + os.sep) or (
                os.path.isabs(url) and e_media(url) and os.path.isfile(url)) or e_media_url(url):
            self.riproduci(url, nome)
            return

        if self.cfg.get("canale") and self.cfg.get("canale") != url:
            self.precedente = (self.nome_in_onda, self.cfg.get("canale"))
        if self.registrando and self.cfg.get("canale") != url:
            self.ferma_registrazione()
        if self.file_in_onda:
            self.file_in_onda = None
        self.et_formato.config(text="")
        self.nome_in_onda, self.ide_in_onda = nome, ide
        self._lista_in_onda = self.cfg.get("lista")
        self._vista_onda = self.vista_corrente()
        self.attesa_da = time.time()
        self.colora_stato(self.colore_canale(nome, url))
        self.indice = next((k for k, c in enumerate(self.visti)
                            if c[1] == url and c[0] == nome), -1)
        self.mostra_carico(True)
        self.scrivi(_("looking for %s qualities...") % nome)
        self.cfg["canale"] = url
        scrivi_config(self.cfg)
        self.segna_in_onda(url)
        self.icona_preferito()
        threading.Thread(target=self._apri, args=(nome, url),
                         daemon=True).start()

    def _apri(self, nome, url):
        self.nome = nome

        try:
            self.mpv.audio_delay = float(self.cfg.get("ritardo", {}).get(url, 0.0))
        except Exception:
            pass
        self.applica_chiave(url)
        try:
            self.mpv["stream-lavf-o"] = ("" if "jmp2.uk" in url else
                "reconnect=1,reconnect_streamed=1,reconnect_on_network_error=1,"
                "reconnect_delay_max=10")
        except Exception:
            pass
        self.varianti = varianti(url)
        self.root.after(0, self.mostra_ingranaggio)
        self.affanni, self.tranquillo_da = [], time.time()
        self.ultima_discesa = 0.0
        self.storia, self.in_calo = [], 0
        self.manuale = True
        if self.varianti:


            self.quale = len(self.varianti) - 1
            self.suona(self.quale)
        else:
            self.quale = -1
            try:
                self.mpv["audio-files"] = []
            except Exception:
                pass
            self.mpv.play(self.indirizzo_chiave(url))
        self.scrivi_riga()

    def indirizzo_chiave(self, url):
        """Un canale DASH con ClearKey si apre dal manifest ripulito dei
        sottotitoli (server locale); gli altri com'e'."""
        if chiave_clearkey(CHIAVI.get(url)) and url.split("?")[0].lower().endswith(".mpd"):
            try:
                return "http://127.0.0.1:%d/m.mpd?u=%s" % (
                    server_mpd().server_address[1], quote(url, safe=""))
            except Exception:
                pass
        return url

    def applica_chiave(self, url):
        """ClearKey del canale (license-type/license-details dell'M3U): la
        chiave va al demuxer di ffmpeg insieme alla whitelist dei protocolli
        (e' la stessa opzione di mpv, quindi si riscrivono insieme)."""
        protocolli = "file,http,https,tcp,tls,crypto,data,httpproxy,udp,rtp,rtmp,rtmps,srt"
        o = "protocol_whitelist=%%%d%%%s" % (len(protocolli), protocolli)
        k = chiave_clearkey(CHIAVI.get(url))
        if k:
            o += ",cenc_decryption_key=" + k
        try:
            self.mpv["demuxer-lavf-o"] = o
        except Exception:
            pass

    def suona(self, i):
        """La variante scelta: a mpv si da' una master playlist locale con
        dentro solo quella e il suo gruppo audio, cosi' video e audio li
        legge insieme e restano a tempo (dati separati derivavano)."""
        _b, video, audio, _alta, master = self.varianti[i]
        try:
            self.mpv["audio-files"] = []
        except Exception:
            pass


        self.da_riallineare = bool(audio)
        self.suonato_da = 0.0
        try:
            os.makedirs(CACHE_VARIANTE, exist_ok=True)
            f = os.path.join(CACHE_VARIANTE, "variante%s.m3u8" % ("2" if self.schermo else ""))
            with open(f, "w", encoding="utf-8") as o:
                o.write(master)
            self.mpv.play(f)
        except Exception:
            self.mpv.play(video)

        self.cfg.setdefault("qualita", {})[self.cfg.get("canale", "")] = i

    def scegli_qualita(self, i):
        """Dal menu: -1 e' Auto, un numero e' una variante fissa."""
        if i >= 0 and i != self.quale and self.varianti:
            self.quale = i
            self.suona(i)
        self.scrivi_riga()

    def mostra_ingranaggio(self):
        """L'ingranaggio solo quando c'e' da scegliere: due qualita' o piu'."""
        b = self.b_qualita
        if len(self.varianti or []) > 1:
            if not b.winfo_ismapped():
                b.pack(side="right", padx=2, pady=6, before=self.b_audio_menu)
                self.root.after_idle(lambda: self.vesti(b))
        else:
            if self.tendina.aperta() and self.tendina.chi is b:
                self.tendina.chiudi()
            if getattr(self, "ov_menu", None):
                self.chiudi_menu_ov()
            b.pack_forget()

    def apri_menu_qualita(self):
        """La tendina delle qualita' sopra all'ingranaggio, col bordo destro
        sull'ingranaggio; la rotella fa mezzo giro e torna alla chiusura.
        Con la barra di mpv anche il menu lo disegna mpv."""
        if self.barra_in_overlay():
            if getattr(self, "ov_menu", None):
                self.chiudi_menu_ov()
            else:
                self.ov_menu_bottone = self.b_qualita
                self.ov_menu, self.ov_menu_sopra = self.voci_qualita(), None
                self.gira_ingranaggio(+1)
                self.disegna_menu_ov()
            return
        if self.tendina.aperta() and self.tendina.chi is self.b_qualita:
            self.tendina.chiudi()
            return
        self.gira_ingranaggio(+1)
        b = self.b_qualita
        self.tendina.apri(b.winfo_rootx() + b.winfo_width(), b.winfo_rooty() - 4,
                          self.voci_qualita(), sopra=True, destra=True,
                          al_chiudere=lambda: self.gira_ingranaggio(-1), chi=b)

    def gira_ingranaggio(self, verso):
        """Mezzo giro della rotella: in un senso aprendo, nell'altro
        chiudendo. Serve PIL per ruotare la png; senza, resta ferma."""
        if not self.giri_ingranaggio:
            return
        self.verso_ingranaggio = verso

        def passo():
            n = self.passo_ingranaggio + self.verso_ingranaggio
            if not (0 <= n < len(self.giri_ingranaggio)):
                return
            self.passo_ingranaggio = n
            self.b_qualita.config(image=self.giri_ingranaggio[n])
            if self.barra_in_overlay():
                self.disegna_ov()
            if self.colore_barra and HA_PIL and self.b_qualita.winfo_ismapped():

                b = self.b_qualita
                w, h, y = b.winfo_width(), b.winfo_height(), b.winfo_y()
                fondo = Image.new("RGBA", (w, h))
                for r in range(h):
                    fondo.paste(self.tinta_barra(y + r), (0, r, w, r + 1))
                im = self.icone_pil["setting"].rotate(-n * 15, resample=Image.BICUBIC)
                fondo.alpha_composite(im, ((w - im.width) // 2, (h - im.height) // 2))
                self.vestiti[b] = ImageTk.PhotoImage(fondo)
                b.config(image=self.vestiti[b])
            self.root.after(25, passo)
        passo()

    def voci_qualita(self):
        """Le righe del menu: le qualita' che il canale offre, con un
        punto su quella in uso."""
        if not self.varianti:
            return [(_("single quality"), None)]
        voci = []
        for i, (banda, _v, _a, alta, _m) in enumerate(self.varianti):
            nome, _c = qualita(alta, banda)
            segno = "*  " if i == self.quale else "   "
            voci.append((segno + nome, lambda i=i: self.scegli_qualita(i)))
        return voci


    def riga(self):
        """I tre pezzi della riga di stato: il canale, il titolo del
        programma in onda (del colore del canale) e il suo orario."""


        volo = getattr(self, "al_volo", None)
        if volo and volo in (self.file_in_onda, self.cfg.get("canale")):
            return (self.nome, tipo_url(volo), "")
        p = self.programma()
        if not p:
            if self.file_in_onda:
                titolo, sotto = self.righe_file(self.file_in_onda, self.nome)
                return (titolo, sotto, "")

            return (self.nome_canale(), "", _("not in the guide") if self.epg and self.nome
                    else "")
        inizio, fine, titolo = p
        mancanti = max(0, int((fine - time.time()) / 60))

        restano = {
            "it": "tra %d min",
            "en": "in %d min",
            "es": "en %d min",
            "fr": "dans %d min",
            "de": "in %d Min.",
            "pt": "em %d min",
            "ru": "через %d мин.",
        }.get(LINGUA, "in %d min") % mancanti
        fascia = "%s\t%s" % (
            time.strftime("%H:%M", time.localtime(inizio)), restano)
        return (self.nome_canale(), titolo or "?", fascia)

    def cambia(self, dove):
        n = max(0, min(len(self.varianti) - 1, self.quale + dove))
        if n == self.quale:
            return
        self.quale = n
        if dove < 0:
            self.ultima_discesa = time.time()
        self.affanni, self.tranquillo_da = [], time.time()
        self.storia, self.in_calo = [], 0
        self.suona(n)
        self.scrivi_riga()

    def guarda(self):
        """Una volta al secondo guarda come sta messo il buffer.

        Prima di tutto si anticipa: se il buffer cala di continuo, o il
        flusso arriva piu' piano di quanto la variante vuole, si scende
        subito, mentre c'e' ancora buffer da spendere. Poi la rete di
        sicurezza: due affanni in mezzo minuto e si scende comunque.
        Buffer pieno per un bel po' e si risale, ma non subito dopo essere
        scesi, se no si finisce a rimbalzare fra due qualita'."""
        while True:
            time.sleep(1.0)
            if self.attesa_da and time.time() - self.attesa_da > 30.0:
                self.attesa_da = 0.0
                self.root.after(0, self.canale_morto)
                continue
            if not self.varianti or self.manuale:
                continue
            try:
                stato = self.mpv.demuxer_cache_state or {}
                buf = (stato.get("cache-duration")
                       or self.mpv.demuxer_cache_duration or 0.0)
                fermo = bool(self.mpv.paused_for_cache)
                legge = float(stato.get("raw-input-rate") or 0.0)
            except Exception:
                continue
            ora = time.time()


            self.storia = [(t0, b) for t0, b in self.storia if ora - t0 <= 10.0]
            self.storia.append((ora, buf))
            cala = False
            if len(self.storia) >= 5:
                t0, b0 = self.storia[0]
                pendenza = (buf - b0) / max(1.0, ora - t0)
                cala = pendenza < IN_CALO and buf < TRANQUILLO
            banda = self.varianti[self.quale][0]


            lento = (legge > 0 and buf < TRANQUILLO and
                     legge * 8 < banda * 1.05)
            if cala or lento:
                self.in_calo += 1
                if self.in_calo >= QUANTO_IN_CALO and self.quale > 0:
                    self.cambia(-1)
                    continue
            else:
                self.in_calo = 0

            if fermo or buf < IN_AFFANNO:
                self.affanni = [t for t in self.affanni if ora - t < 30.0]
                self.affanni.append(ora)
                self.tranquillo_da = ora
                if len(self.affanni) >= 2:
                    self.cambia(-1)
            elif buf > TRANQUILLO:
                if (ora - self.tranquillo_da > QUANTO_TRANQUILLO and
                        ora - self.ultima_discesa > DOPO_UNA_DISCESA):
                    self.cambia(+1)


    def faccia(self, b, icona, testo):
        if icona in self.icone:
            b.icona = icona
            self.vesti(b)
        else:
            b.config(text=testo)

    def vesti(self, b):
        icona = getattr(b, "icona", None)
        if not icona:
            return
        pil = self.icone_pil.get(icona)
        if not self.colore_barra or pil is None:
            b.config(image=self.icone[icona], bg=BARRA, activebackground=BARRA)
            return
        if not b.winfo_ismapped():
            if icona in self.icone:
                b.config(image=self.icone[icona])
            self.root.after_idle(lambda: b.winfo_exists() and b.winfo_ismapped() and self.vesti(b))
            self.root.after(60, lambda: b.winfo_exists() and b.winfo_ismapped() and self.vesti(b))
            return
            
        w, h = b.winfo_width(), b.winfo_height()
        y = b.winfo_y()
        fondo = Image.new("RGBA", (w, h))
        for r in range(h):
            fondo.paste(self.tinta_barra(y + r), (0, r, w, r + 1))
            
        # L'effetto hover con il rettangolo bianco è stato rimosso qui sotto.
        
        fondo.alpha_composite(pil, ((w - pil.width) // 2, (h - pil.height) // 2))
        self.vestiti[b] = ImageTk.PhotoImage(fondo)
        c = self.tinta_barra(y + h // 2)
        b.config(image=self.vestiti[b], bg=c, activebackground=c)
    def imposta_timer_spegnimento(self, minuti):
        """Ferma la riproduzione dopo il tempo scelto, senza chiudere XVB."""
        self.annulla_timer_spegnimento(silenzioso=True)
        try:
            self.timer_spegnimento = self.root.after(
                int(minuti) * 60 * 1000, self.scade_timer_spegnimento
            )
        except tk.TclError:
            self.timer_spegnimento = None
            self.scrivi(_("cannot set sleep timer"), errore=True)
            return
        self.timer_fine = time.time() + int(minuti) * 60
        self.hud_disegna()
        self.scrivi(_("sleep timer set: %d min") % int(minuti))

    def annulla_timer_spegnimento(self, silenzioso=False):
        """Annulla il timer, se presente."""
        identificativo = getattr(self, "timer_spegnimento", None)
        self.timer_spegnimento = None
        self.timer_fine = None
        if identificativo:
            try:
                self.root.after_cancel(identificativo)
            except (tk.TclError, RuntimeError):
                pass
        self.hud_disegna()
        if not silenzioso:
            self.scrivi(_("sleep timer cancelled"))

    def scade_timer_spegnimento(self):
        """Scaduto il tempo: ferma il contenuto, lasciando XVB aperto."""
        self.timer_spegnimento = None
        self.timer_fine = None
        self.ferma()
        self.scrivi(_("sleep timer expired"))

    def ferma(self):
        """Stop: si ferma tutto e torna lo schermo di XVB. Play dopo lo
        stop riparte da quello che si stava guardando (i file da dove
        erano rimasti)."""
        self.annulla_timer_spegnimento(silenzioso=True)
        if self.registrando:
            self.ferma_registrazione()
        if self.file_in_onda:
            self.fermato = ("file", self.nome_in_onda, self.file_in_onda, None)
        elif self.cfg.get("canale"):
            self.fermato = ("canale", self.nome_in_onda, self.cfg.get("canale"),
                            getattr(self, "ide_in_onda", None))
        try:
            self.mpv.command("stop")
            self.mpv.pause = False
        except Exception:
            pass
        self.file_in_onda = None
        self.segna_in_onda(None)
        self.attesa_da = 0.0
        self.mostra_carico(False)
        self.mostra_sfondo(True, dissolvi=True)
        self.faccia(self.b_pausa, "play", _("Resume"))
        self.et_formato.config(text="")
        self.icona_preferito()
        self.spegni_colori()
        self.scrivi(_("ready"))

    def spegni_colori(self):
        """Da fermi niente colore del canale: sfumatura, progress, riga di
        stato e bottoni tornano come all'avvio."""
        self.colore_barra = None
        self.linea.itemconfig(self.pieno_linea, fill="#ffffff")
        self.linea.coords(self.pieno_linea, 0, 0, 0, self.alta_linea)
        for et in (self.et_titolo, self.et_orologio):
            et.config(fg=TESTO)
        self.icona_shuffle()
        self.icona_ripeti()
        self.disegna_fondo_barra()
        self.fondo_barra.delete("all")
        self.disegna_fondo_linea()
        self.vesti_tutti()

    def pausa(self):

        fermato = getattr(self, "fermato", None)
        if fermato:
            self.fermato = None
            self.faccia(self.b_pausa, "pausa", _("Pause"))
            tipo, nome, dove, ide = fermato
            if tipo == "file":
                self.riproduci(dove, nome)
            else:
                self.apri(nome, dove, ide)
            return
        if not (self.nome_in_onda or self.file_in_onda):
            return
        try:
            self.mpv.pause = not self.mpv.pause
        except Exception:
            return
        if self.mpv.pause:
            self.faccia(self.b_pausa, "play", _("Resume"))
        else:
            self.faccia(self.b_pausa, "pausa", _("Pause"))

    def muto(self):
        try:
            self.mpv.mute = not self.mpv.mute
        except Exception:
            return
        self.icona_volume()

    def rileva_tipo_uscita_audio(self):
        dispositivo = "auto"
        try:
            dispositivo = str(self.mpv["audio-device"] or "auto")
        except Exception:
            pass

        descrizione = ""
        try:
            elenco = self.mpv._get_property("audio-device-list") or []
            voce = next((v for v in elenco
                         if isinstance(v, dict) and v.get("name") == dispositivo), None)
            if voce:
                descrizione = str(voce.get("description") or "")
        except Exception:
            pass

        nome_sink = ""
        try:
            risposta = subprocess.run(
                ["pactl", "get-default-sink"],
                capture_output=True, text=True, timeout=2, check=True
            )
            nome_sink = risposta.stdout.strip()
        except Exception:
            if dispositivo not in ("auto", "pipewire", "pulse", "alsa", "jack", "sdl", "sndio"):
                nome_sink = dispositivo.split("/", 1)[-1]

        proprieta = {}
        if nome_sink:
            try:
                risposta = subprocess.run(
                    ["pactl", "-f", "json", "list", "sinks"],
                    capture_output=True, text=True, timeout=2, check=True
                )
                uscite = json.loads(risposta.stdout)
                uscita = next(
                    (u for u in uscite
                     if u.get("name") == nome_sink
                     or str(u.get("name", "")).endswith("/" + nome_sink)),
                    None
                )
                if uscita:
                    proprieta = uscita.get("properties") or {}
                    descrizione = str(
                        uscita.get("description") or descrizione
                    )
            except Exception:
                pass

        testo = " ".join(
            [nome_sink, descrizione] +
            [str(v) for k, v in proprieta.items()
             if k in ("device.bus", "device.form_factor", "device.icon_name",
                      "api.bluez5.address", "device.name")]
        ).lower()
        bus = str(proprieta.get("device.bus", "")).lower()
        forma = str(proprieta.get("device.form_factor", "")).lower()

        if (bus == "bluetooth" or "bluez" in testo
                or "bluetooth" in testo or "audio-headphones-bluetooth" in testo):
            return "bluetooth"
        if (forma in ("headset", "headphones", "earbuds")
                or any(parola in testo for parola in
                       ("headset", "headphone", "cuffie", "auricolari", "earbuds"))):
            return "cuffie"

        if not proprieta and dispositivo in (
                "auto", "pipewire", "pulse", "alsa", "jack", "sdl", "sndio"):
            try:
                risposta = subprocess.run(
                    ["wpctl", "inspect", "@DEFAULT_AUDIO_SINK@"],
                    capture_output=True, text=True, timeout=2, check=True
                )
                testo = risposta.stdout.lower()
                if "bluez" in testo or "bluetooth" in testo:
                    return "bluetooth"
                if any(parola in testo for parola in
                       ("headset", "headphone", "cuffie", "auricolari", "earbuds")):
                    return "cuffie"
            except Exception:
                pass

        return "altoparlanti"

    def applica_tipo_uscita_audio(self, tipo):
        if tipo != self.tipo_uscita_corrente:
            self.tipo_uscita_corrente = tipo
            self.icona_volume()

    def ascolta_eventi_uscita_audio(self):
        while True:
            processo = None
            try:
                processo = subprocess.Popen(
                    ["pactl", "subscribe"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    bufsize=1
                )
                for evento in processo.stdout:
                    if "on sink" in evento or "on server" in evento:
                        tipo = self.rileva_tipo_uscita_audio()
                        try:
                            self.root.after(
                                0, self.sincronizza_uscita_audio_sistema
                            )
                            self.root.after(
                                0, lambda t=tipo: self.applica_tipo_uscita_audio(t)
                            )
                        except tk.TclError:
                            return
                processo.wait()
            except Exception:
                pass
            finally:
                if processo and processo.poll() is None:
                    processo.terminate()
            time.sleep(3)

    def controlla_uscita_audio(self):
        try:
            self.sincronizza_uscita_audio_sistema()
            tipo = self.rileva_tipo_uscita_audio()
            self.applica_tipo_uscita_audio(tipo)
            self.root.after(500, self.controlla_uscita_audio)
        except tk.TclError:
            pass

    def icona_volume(self):
        """Mostra il dispositivo audio attivo, mantenendo muto e volume."""
        try:
            muto = bool(self.mpv.mute)
        except Exception:
            muto = False
        v = self.volume.get()
        if muto:
            icona = "muto"
        elif getattr(self, "tipo_uscita_corrente", "") == "bluetooth":
            icona = "cuffie_bluetooth"
        elif getattr(self, "tipo_uscita_corrente", "") == "cuffie":
            icona = "cuffie"
        elif v == 0:
            icona = "volume_off"
        elif v < 34:
            icona = "volume_low"
        elif v < 67:
            icona = "volume"
        else:
            icona = "volume_high"
        if icona not in self.icone and "volume" in self.icone:
            icona = "volume"
        self.faccia(self.b_muto, icona, _("Unmute") if muto else _("Mute"))

    def apri_volume(self):
        if self.volume_dopo:
            self.root.after_cancel(self.volume_dopo)
            self.volume_dopo = None
        if self.volume_animazione:
            self.root.after_cancel(self.volume_animazione)
            self.volume_animazione = None
        self.anima_volume(+1)

    def chiudi_volume_tra_poco(self):
        if self.tendina.aperta() and self.tendina.chi is self.b_audio_menu:
            return
        if self.volume_dopo:
            self.root.after_cancel(self.volume_dopo)
        self.volume_dopo = self.root.after(400, lambda: self.anima_volume(-1))

    def anima_volume(self, verso):
        """Il cursore si allarga (o si stringe) in cinque passi da 15 ms:
        e' lui a cambiare larghezza, l'altoparlante si sposta di conseguenza."""
        if self.volume_animazione:
            self.root.after_cancel(self.volume_animazione)
            self.volume_animazione = None
        passo = self.volume.larga // 5
        largo = max(0, min(self.volume.larga, self.volume_largo + verso * passo))
        if largo == self.volume_largo:
            return
        self.volume_largo = largo
        self.volume.config(width=largo)
        if self.barra_in_overlay():
            self.disegna_ov()
        if 0 < largo < self.volume.larga:
            self.volume_animazione = self.root.after(
                15, lambda: self.prossimo_passo_volume(verso))

    def prossimo_passo_volume(self, verso):
        self.volume_animazione = None
        self.anima_volume(verso)

    def alza_volume(self, v):
        try:
            self.mpv.volume = float(v) * (1.5 if self.cfg.get("boost") else 1.0)
        except Exception:
            pass
        self.cfg["volume"] = int(float(v))
        if hasattr(self, "b_muto"):
            self.icona_volume()

    def maniglia(self, pannello, chiave, verso):
        """Una striscia sottile accanto al pannello: trascinandola il
        pannello si allarga o si stringe; la larghezza resta salvata."""

        m = tk.Canvas(self.root, bg=PANNELLO, width=8, highlightthickness=0, bd=0,
                      cursor="sb_h_double_arrow")

        def puntini(acceso=False):
            m.delete("all")
            h, c = m.winfo_height(), ("#9a9aa8" if acceso else "#5a5a64")
            for dy in (-8, 0, 8):
                y = h // 2 + dy
                m.create_oval(2, y - 2, 6, y + 2, fill=c, outline=c)
        m.bind("<Configure>", lambda e: puntini())
        m.bind("<Enter>", lambda e: (m.config(bg=MENU), puntini(True)))
        m.bind("<Leave>", lambda e: (m.config(bg=PANNELLO), puntini()))


        attesa = {"x": None, "id": None}

        def applica():
            attesa["id"] = None
            x = attesa["x"]
            if verso > 0:
                larga = x - pannello.winfo_rootx()
            else:
                larga = pannello.winfo_rootx() + pannello.winfo_width() - x
            larga = max(140, min(int(self.root.winfo_width() * 0.6), larga))
            if larga != pannello.winfo_width():
                pannello.config(width=larga)
            self.cfg[chiave] = larga

        def trascina(ev):
            attesa["x"] = ev.x_root
            if attesa["id"] is None:
                attesa["id"] = self.root.after(30, applica)

        def fine(ev):
            if attesa["id"] is not None:
                self.root.after_cancel(attesa["id"])
                applica()
            scrivi_config(self.cfg)
            self.disegna_fondo_barra()
        m.bind("<B1-Motion>", trascina)
        m.bind("<ButtonRelease-1>", fine)
        return m

    def disponi(self, pieno):
        """Mette i pezzi al loro posto, nell'ordine giusto. L'ordine conta:
        i pannelli prima, il video per ultimo, che si prende il resto."""
        avviso_aperto = self.avviso.winfo_ismapped()
        for w in (self.cima, self.sinistra, self.destra, self.riga_stato,
                  self.barra, self.avviso, self.video, self.maniglia_sx,
                  self.maniglia_dx):
            w.pack_forget()

        self.root.config(menu="" if (pieno or getattr(self, "pip", False)) else self.barra_menu)
        if getattr(self, "pip", False):

            self.barra.place_forget()
            if hasattr(self, "mpv"):
                self.togli_ov()
            self.video.pack(side="right", fill="both", expand=True)
            return


        if not pieno:
            self.riga_stato.pack(side="bottom", fill="x")
        if avviso_aperto:
            self.avviso.pack(side="bottom", fill="x")

        if not self.nascosti.get(self.sinistra):
            self.sinistra.pack(side="left", fill="y")
            self.maniglia_sx.pack(side="left", fill="y")
        if not self.nascosti.get(self.destra):
            self.destra.pack(side="right", fill="y")
            self.maniglia_dx.pack(side="right", fill="y")
        self.video.pack(side="right", fill="both", expand=True)
        self.root.config(cursor="")

    def primo_pannello(self):
        """Il primo fra barre laterali e video nell'ordine di impacchettamento:
        quello che va al piede (comandi, avviso) si mette prima di lui, cosi'
        e' largo quanto la finestra."""
        pannelli = (self.sinistra, self.maniglia_sx, self.destra, self.maniglia_dx, self.video)
        return next((w for w in self.root.pack_slaves() if w in pannelli), self.video)

    def doppio_clic(self):
        """Doppio clic sul video: schermo intero; in PiP, torna normale.
        Sui comandi disegnati da mpv no: li' i clic sono dei bottoni."""
        if self.ov_sotto_mouse() or getattr(self, "split", False):
            return
        if self.pip:
            self.esci_pip()
        else:
            self.schermo_intero()

    STATO_SCHERMO = ("nome", "nome_in_onda", "varianti", "quale", "file_in_onda",
                     "ide_in_onda", "manuale", "affanni", "tranquillo_da",
                     "ultima_discesa", "storia", "in_calo", "da_riallineare",
                     "suonato_da", "_lista_in_onda", "colore_barra")
    VUOTO_SCHERMO = {"nome": None, "nome_in_onda": None, "varianti": [], "quale": -1,
                     "file_in_onda": None, "ide_in_onda": None,
                     "colore_barra": None}

    def split_si_no(self, ev=None):
        if self.split:
            self.chiudi_split()
        else:
            self.apri_split()

    def disponi_split(self):
        c0, c1 = self.cornici
        if self.split:
            c0.place(x=0, y=0, relwidth=1, relheight=1)
            self.vid1.place(x=0, y=0, relwidth=1, relheight=1, width=0, height=0)
            c1.place(relx=0.5, y=0, relwidth=0.5, relheight=1, height=-(self.OV_ALTA - self.OV_LINEA))
            self.vid2.place(x=0, y=0, relwidth=1, relheight=1)
            if not getattr(self, "linee", None):
                self.linee = [[tk.Frame(self.video, bg="black", bd=0) for _n in range(4)]
                              for _m in range(2)]
                self.video.bind("<Configure>", lambda e: self.margini_split(), add="+")
            t, a = 2, self.OV_ALTA - self.OV_LINEA
            for i, (su, giu, sx, dx) in enumerate(self.linee):
                x0 = 0.5 * i
                su.place(relx=x0, y=0, relwidth=0.5, height=t)
                giu.place(relx=x0, rely=1, y=-a, anchor="sw", relwidth=0.5, height=t)
                sx.place(relx=x0, y=0, width=t, relheight=1, height=-a)
                dx.place(relx=x0 + 0.5, y=0, anchor="ne", width=t, relheight=1, height=-a)
        else:
            c1.place_forget()
            c0.place(x=0, y=0, relwidth=1, relheight=1, width=0)
            self.vid1.place(x=0, y=0, relwidth=1, relheight=1, width=0, height=0)
            for gruppo in getattr(self, "linee", None) or []:
                for w in gruppo:
                    w.place_forget()
        self.margini_split()
        self.bordi_schermi()

    def margini_split(self):
        """Il video del player 1 sta nella meta' sinistra e sopra ai comandi;
        la finestra resta larga tutto, cosi' il pannello e' uno solo."""
        try:
            if self.split:
                h = max(1, self.video.winfo_height())
                self.mpv1["video-margin-ratio-right"] = 0.5
                self.mpv1["video-margin-ratio-bottom"] = min(0.5, (self.OV_ALTA - self.OV_LINEA) / float(h))
            else:
                self.mpv1["video-margin-ratio-right"] = 0
                self.mpv1["video-margin-ratio-bottom"] = 0
        except Exception:
            pass

    def bordi_schermi(self):
        for i, gruppo in enumerate(getattr(self, "linee", None) or []):
            for w in gruppo:
                w.config(bg=(getattr(self, "colore_barra", None) or "#2d9cdb")
                         if (self.split and i == self.schermo) else "black")

    def apri_split(self):
        if self.split or self.pip or self.pieno:
            return
        self.split = True
        self.vid2.place(x=0, y=0, relwidth=1, relheight=1)
        self.disponi_split()
        self.root.update_idletasks()
        if self.mpv2 is None:
            try:
                self.crea_mpv2()
            except Exception as e:
                self.split = False
                self.disponi_split()
                self.scrivi(_("Split screen failed: %s") % e, errore=True)
                return
        try:
            self.mpv2.mute = True
            self.mpv2.volume = self.mpv.volume
        except Exception:
            pass

    def chiudi_split(self):
        if not self.split:
            return
        self.seleziona_schermo(0)
        self.split = False
        try:
            self.mpv2.command("stop")
        except Exception:
            pass
        self.stato_schermi[1] = {}
        self.disponi_split()

    def crea_mpv2(self):
        b = self.buffer_attuale()
        m = mpv.MPV(
            **opzioni_mpv_xwayland(),
            wid=str(self.vid2.winfo_id()),
            cache="yes",
            demuxer_max_bytes="%dMiB" % b[2],
            demuxer_max_back_bytes="%dMiB" % b[3],
            demuxer_readahead_secs=b[4],
            cache_pause_wait=b[5],
            network_timeout=60,
            ytdl=False,
            hwdec="auto-safe",
            force_window="yes",
            volume_max=150,
            user_agent=UA,
            cursor_autohide=3000,
            stream_lavf_o=("reconnect=1,reconnect_streamed=1,"
                           "reconnect_on_network_error=1,"
                           "reconnect_delay_max=10"),
        )
        try:
            m["window-dragging"] = "no"
            m["audio-display"] = "embedded-first"
        except Exception:
            pass
        try:
            @m.key_binding("MBTN_LEFT")
            def _clic2(stato, *_a):
                if stato.startswith("d"):
                    self.root.after(0, self.clic_schermo, 1)
        except Exception:
            pass
        m.observe_property("paused-for-cache",
                           lambda n, v: self.mpv is m and self._buffer(n, v))
        m.observe_property("time-pos",
                           lambda n, v: self.mpv is m and self._va(n, v))
        try:
            @m.event_callback("end-file")
            def fine2(ev, app=self):
                if app.mpv is m:
                    app.finito_male(ev)
        except Exception:
            pass
        try:
            m.overlay_ids.update((OV_HUD,))
        except Exception:
            pass
        self.mpv2 = m

    def clic_schermo(self, n):
        if self.split and n == 0:
            try:
                y = self.root.winfo_pointery() - self.video.winfo_rooty()
                if y >= self.video.winfo_height() - self.OV_ALTA:
                    self.clic_video()
                    return
            except tk.TclError:
                pass
        if self.split and n != self.schermo:
            self.seleziona_schermo(n)
        else:
            self.clic_video()

    def seleziona_schermo(self, n):
        if not self.split or n == self.schermo:
            return
        vecchio = self.mpv
        salvo = {k: getattr(self, k, None) for k in self.STATO_SCHERMO}
        self.stato_schermi[self.schermo] = salvo
        self.schermo = n
        nuovo = dict(self.VUOTO_SCHERMO)
        nuovo.update(self.stato_schermi[n])
        for k, v in nuovo.items():
            setattr(self, k, v)
        self.mpv = self.mpv1 if n == 0 else self.mpv2
        try:
            muto, vol = bool(vecchio.mute), vecchio.volume
            vecchio.mute = True
            self.mpv.volume = vol
            self.mpv.mute = muto
        except Exception:
            pass
        if self.colore_barra:
            self.colora_stato(self.colore_barra)
        else:
            self.disegna_fondo_barra()
        self.bordi_schermi()
        self.mostra_ingranaggio()
        self.icona_volume()
        self.scrivi_riga()

    def pip_si_no(self):
        if getattr(self, "split", False):
            self.chiudi_split()
        if self.pip:
            self.esci_pip()
        else:
            self.entra_pip()

    def entra_pip(self):
        """Immagine nell'immagine: una finestrella di solo video, sempre in
        primo piano, che si sposta dalla sua barra del titolo. Passandoci
        sopra col mouse compare il tasto per tornare normale (icone/pip.png);
        anche doppio clic o Esc."""
        if self.pieno:
            self.schermo_intero(None, False)
        self.pip_prima = self.root.geometry()
        self.pip = True
        self.tendina.chiudi()
        self.disponi(False)
        self.root.attributes("-topmost", True)


        x = max(0, self.root.winfo_rootx() + self.root.winfo_width() - 480 - 20)
        y = max(0, self.root.winfo_rooty() + self.root.winfo_height() - 270 - 20)
        self.root.geometry("480x270+%d+%d" % (x, y))
        self.root.config(cursor="")
        self.sorveglia_pip()

    def esci_pip(self):
        if not self.pip:
            return
        self.pip = False
        self.b_pip_esci.place_forget()
        self.pip_disegna(False)
        self.root.attributes("-topmost", bool(self.cfg.get("in_cima")))
        if getattr(self, "pip_prima", None):
            self.root.geometry(self.pip_prima)
        self.ridisponi()

    def sorveglia_pip(self):
        """In PiP: col mouse sopra la finestra, il tasto per tornare normale
        (sopra al video di mpv, che Tk non vede); via, se ne va."""
        if not self.pip:
            return
        try:
            x, y = self.root.winfo_pointerxy()
            dentro = (self.root.winfo_rootx() <= x < self.root.winfo_rootx() + self.root.winfo_width()
                      and self.root.winfo_rooty() <= y < self.root.winfo_rooty() + self.root.winfo_height())
        except tk.TclError:
            dentro = False


        con_mpv = "pip" in self.icone_pil
        self.pip_disegna(dentro and con_mpv)
        if con_mpv:
            dentro = False
        if dentro and not self.b_pip_esci.winfo_ismapped():
            self.b_pip_esci.place(relx=1.0, x=-8, y=8, anchor="ne")
            try:
                self.b_pip_esci.lower(self.sotto)
                self.b_pip_esci.lift()
            except tk.TclError:
                pass
        elif not dentro and self.b_pip_esci.winfo_ismapped():
            self.b_pip_esci.place_forget()
        self.root.after(250, self.sorveglia_pip)

    def pip_disegna(self, si):
        """Il tasto del PiP disegnato da mpv sopra al video, con la sua
        trasparenza (un widget di Tk li' sopra avrebbe il fondo pieno)."""
        ov = getattr(self, "pip_ov", None)
        if not si:
            if ov is not None:
                try:
                    self.mpv1.overlay_remove(OV_PIP)
                except Exception:
                    pass
                self.pip_ov = self.pip_ov_misura = self.pip_ov_riquadro = None
            return
        try:
            vw, vh = self.video.winfo_width(), self.video.winfo_height()
        except tk.TclError:
            return
        try:
            ow = int(self.mpv1.osd_width or 0)
        except Exception:
            ow = 0
        k = (ow / vw) if (ow > 0 and vw > 0) else 1.0
        if ov is not None and getattr(self, "pip_ov_misura", None) == (vw, vh, ow):
            return
        lato, bordo = 28, 10
        try:
            im = self.icone_pil["pip"].copy()
            im.thumbnail((max(1, round(lato * k)), max(1, round(lato * k))), Image.LANCZOS)
            pos = (max(0, round((vw - bordo) * k) - im.width), round(bordo * k))
            im = premoltiplica(im)
            if ov is None:
                ov = mpv.ImageOverlay(self.mpv1, OV_PIP, im, pos=pos)
            else:
                ov.update(im, pos=pos)
        except Exception:
            self.pip_ov = None
            return
        self.pip_ov, self.pip_ov_misura = ov, (vw, vh, ow)
        self.pip_ov_riquadro = (pos[0] / k, pos[1] / k,
                                (pos[0] + im.width) / k, (pos[1] + im.height) / k)

    def hud_giro(self):
        self._hud_id = None
        self.hud_disegna()

    def hud_disegna(self):
        """In alto a destra sul video, disegnati da mpv: il timer di
        spegnimento (arancione, col tempo che resta) e il REC (rosso, col
        pallino che pulsa). Niente da mostrare: l'overlay sparisce."""
        if not HA_PIL:
            return
        ora = time.time()
        righe = []
        fine = getattr(self, "timer_fine", None)
        if fine:
            resto = max(0, int(math.ceil(fine - ora)))
            h, r = divmod(resto, 3600)
            righe.append(("timer", "%d:%02d:%02d" % (h, r // 60, r % 60) if h
                          else "%02d:%02d" % (r // 60, r % 60)))
        registrando = bool(getattr(self, "registrando", ""))
        if registrando:
            righe.append(("rec", getattr(self, "rec_testo", "") or "REC"))
        ov = getattr(self, "hud_ov", None)
        if not righe:
            if ov is not None:
                try:
                    (getattr(self, "hud_player", None) or self.mpv1).overlay_remove(OV_HUD)
                except Exception:
                    pass
            self.hud_ov = self.hud_chiave = None
            return
        if not getattr(self, "_hud_id", None):
            self._hud_id = self.root.after(250, self.hud_giro)
        try:
            vw, vh = self.video.winfo_width(), self.video.winfo_height()
            sp = bool(getattr(self, "split", False))
            m_hud = self.mpv if sp else self.mpv1
            vwf = vw // 2 if (sp and m_hud is not self.mpv1) else vw
            dx = vw // 2 if sp else vw
            ow = int(m_hud.osd_width or 0)
        except Exception:
            return
        if vw < 2:
            return
        k = (ow / vwf) if (ow > 0 and vwf > 0) else 1.0
        margine = 10 + (38 if getattr(self, "pip_ov", None) is not None else 0)
        chiave = (vw, vh, ow, margine, tuple(righe), self.fase_rec if registrando else None,
                  dx, id(m_hud))
        if chiave == getattr(self, "hud_chiave", None) and ov is not None:
            return
        try:
            from PIL import ImageDraw, ImageFont
            try:
                font = ImageFont.truetype(percorso_google_sans("Bold"), max(8, round(15 * k)))
            except Exception:
                font = ImageFont.load_default()
            misura = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
            alta, bordo, lato_ic, vuoto, tra = (round(28 * k), round(11 * k), round(16 * k),
                                                round(7 * k), round(6 * k))
            colori = {"timer": (255, 159, 10), "rec": (255, 69, 58)}
            misure = []
            for tipo, testo in righe:
                bb = misura.textbbox((0, 0), testo, font=font)
                misure.append((bb, bordo + lato_ic + vuoto + (bb[2] - bb[0]) + bordo))
            largo = max(m[1] for m in misure)
            im = Image.new("RGBA", (largo, len(righe) * alta + (len(righe) - 1) * tra), (0, 0, 0, 0))
            d = ImageDraw.Draw(im)
            for i, ((tipo, testo), (bb, lr)) in enumerate(zip(righe, misure)):
                x0, y0 = largo - lr, i * (alta + tra)
                c = colori[tipo]
                d.rounded_rectangle([x0, y0, largo - 1, y0 + alta - 1], radius=alta // 2,
                                    fill=(0, 0, 0, 120))
                cx, cy = x0 + bordo + lato_ic // 2, y0 + alta // 2
                if tipo == "rec":
                    t = 0.5 + 0.5 * math.cos(2 * math.pi * self.fase_rec / 12)
                    r = max(3, round(5 * k))
                    d.ellipse([cx - r, cy - r, cx + r, cy + r],
                              fill=c + (int(255 * (0.3 + 0.7 * t)),))
                elif "timer" in self.icone_pil:
                    ic = self.icone_pil["timer"].copy()
                    ic.thumbnail((lato_ic, lato_ic), Image.LANCZOS)
                    col = Image.new("RGBA", ic.size, c + (255,))
                    col.putalpha(ic.getchannel("A"))
                    im.alpha_composite(col, (cx - ic.width // 2, cy - ic.height // 2))
                d.text((x0 + bordo + lato_ic + vuoto - bb[0], y0 + (alta - (bb[3] - bb[1])) // 2 - bb[1]),
                       testo, font=font, fill=c + (255,))
            pos = (max(0, round(dx * k) - im.width - round(margine * k)), round(10 * k))
            im = premoltiplica(im)
            if ov is not None and getattr(self, "hud_player", None) is not m_hud:
                try:
                    self.hud_player.overlay_remove(OV_HUD)
                except Exception:
                    pass
                ov = None
            if ov is None:
                ov = mpv.ImageOverlay(m_hud, OV_HUD, im, pos=pos)
            else:
                ov.update(im, pos=pos)
        except Exception:
            return
        self.hud_ov, self.hud_chiave, self.hud_player = ov, chiave, m_hud

    def clic_video(self):
        """Clic sul video: Pausa/Play; preserva comandi overlay e PiP."""
        if (self.barra_in_overlay() and not self.barra_visibile
                and not getattr(self, "mai_partito", False)):
            try:
                y = self.root.winfo_pointery() - self.video.winfo_rooty()
                if y >= self.video.winfo_height() - self.OV_ALTA:
                    self.mostra_barra(True)
                    self.disegna_ov(forza=True)
            except tk.TclError:
                pass

        if self.clic_ov():
            return

        r = getattr(self, "pip_ov_riquadro", None)
        if self.pip:
            if getattr(self, "pip_ov", None) is not None and r:
                try:
                    x = self.root.winfo_pointerx() - self.video.winfo_rootx()
                    y = self.root.winfo_pointery() - self.video.winfo_rooty()
                except tk.TclError:
                    return
                if r[0] - 4 <= x <= r[2] + 4 and r[1] - 4 <= y <= r[3] + 4:
                    self.esci_pip()
            return

        self.pausa()

    def schermo_intero(self, ev=None, acceso=None):
        if getattr(self, "split", False):
            self.chiudi_split()
        self.pieno = (not self.pieno) if acceso is None else acceso
        self.root.attributes("-fullscreen", self.pieno)
        if self.pieno and self.sidebar_aperte():
            self.sidebar_prima = (self.nascosti.get(self.sinistra), self.nascosti.get(self.destra))
            self.nascosti[self.sinistra] = self.nascosti[self.destra] = True
        elif not self.pieno and getattr(self, "sidebar_prima", None):
            self.nascosti[self.sinistra], self.nascosti[self.destra] = self.sidebar_prima
            self.sidebar_prima = None
        self.ridisponi()
        self.icona_sidebar()

    def si_nasconde(self):
        """Comandi e mouse vanno e vengono quando c'e' solo il video: barre
        laterali chiuse, a schermo intero o in finestra, uguale."""
        return not self.sidebar_aperte()

    def sorveglia_mouse(self):
        """Il mouse sta sopra al video, che e' la finestra di mpv: Tk il
        movimento non lo vede. Allora si guarda dov'e' il puntatore cinque
        volte al secondo, e se si e' spostato e' mosso."""
        if not self.nasconde_barra():
            self.sorveglio = False
            return
        try:
            adesso = self.root.winfo_pointerxy()
        except Exception:
            adesso = self.dove_era
        if adesso != self.dove_era:
            self.dove_era = adesso

            try:
                r = self.root
                dentro = (r.winfo_rootx() <= adesso[0] < r.winfo_rootx() + r.winfo_width()
                          and r.winfo_rooty() <= adesso[1] < r.winfo_rooty() + r.winfo_height())
            except tk.TclError:
                dentro = False
            if dentro:
                self.mosso()
        self.root.after(80, self.sorveglia_mouse)

    def mosso(self, ev=None):
        """Il mouse si muove, i comandi si fanno vedere; sta fermo tre
        secondi e se ne vanno, col mouse."""
        if getattr(self, "pip", False):
            return
        if not self.barra_visibile:
            self.mostra_barra(True)
        self.root.config(cursor="")
        if self.timer_barra:
            self.root.after_cancel(self.timer_barra)
            self.timer_barra = None
        if self.nasconde_barra():
            self.timer_barra = self.root.after(3000, self.nascondi_barra)

    def nascondi_barra(self):
        self.timer_barra = None
        if not (self.nasconde_barra() and self.barra_visibile):
            return
        if self.ov_sotto_mouse() or getattr(self, "ov_menu", None):

            self.timer_barra = self.root.after(3000, self.nascondi_barra)
            return
        self.mostra_barra(False)
        self.root.config(cursor="none")


    def barra_in_overlay(self):
        """Col video a vista i comandi li disegna mpv sopra all'immagine,
        trasparenti; con lo sfondo di XVB (che e' di Tk) restano di Tk."""
        return HA_PIL and not getattr(self, "pip", False)

    def nasconde_barra(self):
        """Quando i comandi vanno via da soli: sopra al video sempre; sopra
        allo sfondo solo col video da solo (sidebar chiuse)."""
        if self.barra_in_overlay():

            return not getattr(self, "sfondo_su", True) and self.si_nasconde()
        return self.si_nasconde()

    def mostra_barra(self, si):
        #if getattr(self, "mai_partito", False):
          #  si = False
        self.barra_visibile = si
        if getattr(self, "pip", False):
            self.barra.place_forget()
            self.togli_ov()
            return
        if self.barra_in_overlay():
            self.barra.place_forget()
            if si:
                self.disegna_ov(forza=True)
                if not getattr(self, "ov_giro", False):
                    self.ov_giro = True
                    self.root.after(250, self.giro_ov)
            else:
                self.togli_ov()
        else:
            self.togli_ov()
            if si:
                self.barra.place(in_=self.video, relx=0, rely=1, relwidth=1, anchor="sw")
                self.barra.lift()
                self.root.after_idle(self.disegna_fondo_barra)
            else:
                self.barra.place_forget()

    def posa_barra(self):
        """Il modo dei comandi e' cambiato (parte o si ferma il video, PiP):
        li si rimette giusti e, se vanno e vengono, si guarda il mouse."""
        self.mostra_barra(True)
        self.mosso()
        if self.nasconde_barra() and not self.sorveglio:
            self.sorveglio = True
            self.dove_era = self.root.winfo_pointerxy()
            self.sorveglia_mouse()


    OV_ALTA = 96
    OV_RIGA = 58
    OV_LINEA = 30

    def elementi_barra(self):
        """I bottoni della barra di Tk nell'ordine e col lato in cui stanno:
        quelli nascosti (velocita', ingranaggio con una qualita') no."""
        sx, dx = [], []
        for w in self.barra.pack_slaves():
            if not (isinstance(w, tk.Button) or w is self.volume):
                continue
            info = w.pack_info()
            px = info.get("padx", 0)
            if isinstance(px, str):
                px = px.split()
            if isinstance(px, (tuple, list)):
                p0, p1 = int(px[0]), int(px[-1])
            else:
                try:
                    p0 = p1 = int(px)
                except (TypeError, ValueError):
                    p0 = p1 = 2
            if w is self.volume:
                larga = int(getattr(self, "volume_largo", 0))
            elif getattr(w, "icona", None):
                larga = int(float(w.cget("width")))
            else:
                larga = w.winfo_reqwidth()
            (dx if info.get("side") == "right" else sx).append((w, larga, p0, p1))
        return sx, dx

    def disegna_ov(self, forza=False):
        """La barra come immagine trasparente data a mpv: sfumatura dal
        trasparente al colore della barra, la progress, i bottoni con le
        loro icone di adesso. Si rifa' solo se qualcosa e' cambiato."""
        if not (self.barra_in_overlay() and self.barra_visibile):
            return
        try:
            vw, vh = self.video.winfo_width(), self.video.winfo_height()
            ow = int(self.mpv1.osd_width or 0)
            oh = int(self.mpv1.osd_height or 0)
        except Exception:
            return
        if vw < 50 or vh < 50 or ow <= 0 or oh <= 0:
            return
        k = ow / float(vw)
        H, R, L = self.OV_ALTA, self.OV_RIGA, self.OV_LINEA
        sx, dx = self.elementi_barra()

        posti, x = [], 0
        for w, larga, p0, p1 in sx:
            x += p0
            posti.append((w, x, larga))
            x += larga + p1
        x = vw
        for w, larga, p0, p1 in dx:
            x -= p1 + larga
            posti.append((w, x, larga))
            x -= p0
        colore = self.colore_barra
        parte = getattr(self, "parte_linea", 0.0)

        battito = 1.0
        if self.registrando:
            battito = 0.25 + 0.75 * (0.5 + 0.5 * math.cos(2 * math.pi * time.time() / 1.4))
        vol = self.volume.get()
        nome_canale = self.et_canale.cget("text")
        titolo_epg = self.et_titolo.cget("text")
        ora_epg = self.et_ora.cget("text")
        colore_titolo = self.et_titolo.cget("fg")
        chiave = (vw, vh, ow, oh, colore, int(parte * vw), vol, nome_canale,
                  titolo_epg, ora_epg, colore_titolo, getattr(self, "ov_sopra", None),
                  round(battito * 12), self.passo_ingranaggio,
                  tuple((id(w), getattr(w, "icona", None), xx, ll) for w, xx, ll in posti))
        if (not forza and chiave == getattr(self, "ov_chiave", None)
                and getattr(self, "ov", None) is not None
                and (not (nome_canale or titolo_epg or ora_epg)
                     or getattr(self, "ov_epg", None) is not None)):
            return
        from PIL import ImageDraw
        im = Image.new("RGBA", (vw, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)

        base = BARRA
        if colore:
            try:
                a_ = [int(colore[j:j + 2], 16) for j in (1, 3, 5)]
                b_ = [int(BARRA[j:j + 2], 16) for j in (1, 3, 5)]
                base = "#%02x%02x%02x" % tuple(int(y + (x_ - y) * 0.22) for x_, y in zip(a_, b_))
            except ValueError:
                pass
        r0, g0, b0 = (int(base[j:j + 2], 16) for j in (1, 3, 5))


        pc = colore or ACCENTO
        try:
            pr, pg, pb = (int(pc[j:j + 2], 16) for j in (1, 3, 5))
        except ValueError:
            pr, pg, pb = r0, g0, b0

        d.rectangle(
            [0, L + 5, vw, H],
            fill=(int(pr * 0.4), int(pg * 0.4), int(pb * 0.4), 200)
        )


        luce = Image.new("RGBA", (vw, H), (0, 0, 0, 0))
        ImageDraw.Draw(luce).rectangle([0, L, vw, L + 2], fill=(255, 255, 255, 40))
        im.alpha_composite(luce)
        if parte > 0:
            d.rectangle([0, L, int(vw * parte), L + 2], fill=colore or "#ffffff")

        if getattr(self, "ov_epg", None) is not None:
            try:
                self.mpv1.overlay_remove(OV_EPG)
            except Exception:
                pass
            self.ov_epg = None

        cy = H - R // 2
        zone = [(0, 0, vw, L + 5, "linea", None)]
        for w, x0, larga in posti:
            zone.append((x0, H - R, x0 + larga, H, "bottone", w))
            if w is self.volume:
                if larga > 20:
                    a, b = x0 + 10, x0 + larga - 10

                    luce = Image.new("RGBA", (vw, H), (0, 0, 0, 0))
                    ImageDraw.Draw(luce).line([(a, cy), (b, cy)], fill=(255, 255, 255, 40), width=4)
                    im.alpha_composite(luce)
                    xv = a + (b - a) * vol / 100.0
                    if xv > a:
                        d.line([(a, cy), (xv, cy)], fill="#ffffff", width=4)
                continue
            # Nessun riquadro attorno all'icona durante l'hover.
            icona = getattr(w, "icona", None) or ""
            if w is self.b_qualita and self.passo_ingranaggio and "setting" in self.icone_pil:
                pil = self.icone_pil["setting"].rotate(-self.passo_ingranaggio * 15,
                                                       resample=Image.BICUBIC)
            elif w is self.b_rec and self.registrando and "rec" in self.icone_pil:

                pil = self.icone_pil["rec"].copy()
                pil.putalpha(pil.getchannel("A").point(lambda v, f=battito: int(v * f)))
            else:
                pil = self.icone_pil.get(icona)

            if pil is not None and getattr(self, "ov_sopra", None) is w:
                try:
                    pil = pil.copy()
                    tinta = Image.new("RGBA", pil.size, colore or "#ffffff")
                    tinta.putalpha(pil.getchannel("A"))
                    pil = tinta
                except Exception:
                    pass

            if pil is not None:
                im.alpha_composite(pil, (int(x0 + (larga - pil.width) // 2),
                                         int(cy - pil.height // 2)))
            else:
                t = w.cget("text")
                try:
                    tb = d.textbbox((0, 0), t)
                    d.text((x0 + (larga - (tb[2] - tb[0])) // 2, cy - (tb[3] - tb[1]) // 2 - tb[1]),
                           t, fill=TESTO)
                except Exception:
                    pass
        self.ov_zone = zone
        self.ov_cima = vh - H
        self.ov_k = k
        self.ov_posti = {w: (x0, larga) for w, x0, larga in posti}
        if abs(k - 1.0) > 0.01:
            im = im.resize((max(1, round(vw * k)), max(1, round(H * k))), Image.LANCZOS)
        pos = (0, max(0, oh - im.height))
        im = premoltiplica(im)
        try:
            if getattr(self, "ov", None) is None:
                self.ov = mpv.ImageOverlay(self.mpv1, OV_BARRA, im, pos=pos)
            else:
                self.ov.update(im, pos=pos)
        except Exception:
            self.ov = None
        self.ov_chiave = chiave


    OV_MENU_RIGA = 30

    def disegna_menu_ov(self):
        voci = getattr(self, "ov_menu", None)
        ancora = getattr(self, "ov_menu_bottone", None)
        if not voci or getattr(self, "ov", None) is None or ancora not in getattr(self, "ov_posti", {}):
            self.togli_menu_ov()
            return
        from PIL import ImageDraw
        R = self.OV_MENU_RIGA
        d0 = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        try:
            from PIL import ImageFont
            _pf = percorso_google_sans("Regular")
            font_m = ImageFont.truetype(_pf, 12) if _pf else None
        except Exception:
            font_m = None
        larghe = []
        for t, _c in voci:
            t = t[3:] if t[:3] in ("*  ", "   ") else t
            try:
                b = d0.textbbox((0, 0), t, font=font_m)
                larghe.append(b[2] - b[0])
            except Exception:
                larghe.append(len(t) * 7)
        mw = max(larghe + [60]) + 44
        mh = len(voci) * R + 8
        gx, gl = self.ov_posti[ancora]
        x = max(0, min(gx + gl - mw, self.video.winfo_width() - mw))
        # il menu poggia sulla riga dei bottoni (come in Tk: 4 px sopra al bottone),
        # non sul bordo alto della barra, che e' piu' su di OV_ALTA - OV_RIGA px
        y = max(0, self.ov_cima + self.OV_ALTA - self.OV_RIGA - mh - 4)
        self.ov_menu_riquadro = (x, y, x + mw, y + mh)
        im = Image.new("RGBA", (mw, mh), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)

        r0, g0, b0 = (int(BARRA[j:j + 2], 16) for j in (1, 3, 5))
        d.rounded_rectangle([0, 0, mw - 1, mh - 1], radius=8, fill=(r0, g0, b0, 200))
        colore = self.colore_barra or BARRA
        try:
            cr, cg, cb = (int(colore[j:j + 2], 16) for j in (1, 3, 5))
        except ValueError:
            cr, cg, cb = r0, g0, b0
        velo = Image.new("RGBA", (mw, mh), (0, 0, 0, 0))
        ImageDraw.Draw(velo).rounded_rectangle([0, 0, mw - 1, mh - 1], radius=8, fill=(cr, cg, cb, 70))
        im.alpha_composite(velo)
        for i, (t, cosa) in enumerate(voci):
            yy = 4 + i * R
            scelta = t.startswith("*  ")
            t = t[3:] if t[:3] in ("*  ", "   ") else t
            if (i == getattr(self, "ov_menu_sopra", None) and cosa
                    and cosa is not _AZIONE_INFORMATIVA_AUDIO):

                luce = Image.new("RGBA", (mw, mh), (0, 0, 0, 0))
                ImageDraw.Draw(luce).rounded_rectangle([4, yy + 1, mw - 5, yy + R - 2], radius=6,
                                                       fill=(255, 255, 255, 40))
                im.alpha_composite(luce)
            if scelta:
                d.ellipse([14, yy + R // 2 - 3, 20, yy + R // 2 + 3], fill=colore if self.colore_barra else "#ffffff")

            try:
                from PIL import ImageFont
                import subprocess
                import tkinter.font as tkfont

                percorso = percorso_google_sans("Regular")
                if not percorso:
                    raise FileNotFoundError("Manca fonts/static/GoogleSans-Regular.ttf")
                font_usato = ImageFont.truetype(percorso, 12)
            except Exception:
                font_usato = ImageFont.load_default()


            try:
                b = d.textbbox((0, 0), t, font=font_usato)
                d.text((30, yy + (R - (b[3] - b[1])) // 2 - b[1]), t,
                       fill=TESTO if cosa or cosa is _AZIONE_INFORMATIVA_AUDIO else GRIGIO, font=font_usato)
            except Exception:
                pass
        k = getattr(self, "ov_k", 1.0)
        if abs(k - 1.0) > 0.01:
            im = im.resize((max(1, round(mw * k)), max(1, round(mh * k))), Image.LANCZOS)
        im = premoltiplica(im)
        try:
            self.ov_menu_img = mpv.ImageOverlay(self.mpv1, OV_MENU, im, pos=(round(x * k), round(y * k)))
        except Exception:
            self.ov_menu_img = None

    def togli_menu_ov(self):
        if getattr(self, "ov_menu_img", None) is not None:
            try:
                self.mpv1.overlay_remove(OV_MENU)
            except Exception:
                pass
        self.ov_menu_img, self.ov_menu_riquadro = None, None

    def chiudi_menu_ov(self):
        self.ov_menu_fuori = None
        if getattr(self, "ov_menu", None):
            ancora = getattr(self, "ov_menu_bottone", None)
            self.ov_menu = None
            self.ov_menu_bottone = None
            if ancora is self.b_qualita:
                self.gira_ingranaggio(-1)
            elif ancora is self.b_audio_menu:
                self.freccia_audio(False)
                self.chiudi_volume_tra_poco()
        self.togli_menu_ov()

    def riga_menu_ov(self):
        """La voce del menu delle qualita' sotto al mouse (None se fuori)."""
        r = getattr(self, "ov_menu_riquadro", None)
        if not r:
            return None
        try:
            x = self.root.winfo_pointerx() - self.video.winfo_rootx()
            y = self.root.winfo_pointery() - self.video.winfo_rooty()
        except tk.TclError:
            return None
        if not (r[0] <= x < r[2] and r[1] <= y < r[3]):
            return None
        i = int((y - r[1] - 4) // self.OV_MENU_RIGA)
        return i if 0 <= i < len(self.ov_menu or []) else -1

    def togli_ov(self):
        self.chiudi_menu_ov()
        ov = getattr(self, "ov", None)
        if ov is not None:
            try:
                self.mpv1.overlay_remove(OV_BARRA)
            except Exception:
                pass
        if getattr(self, "ov_epg", None) is not None:
            try:
                self.mpv1.overlay_remove(OV_EPG)
            except Exception:
                pass
        self.ov_epg = None
        self.ov, self.ov_chiave, self.ov_zone = None, None, []

    def dove_ov(self):
        """Il mouse in coordinate dell'immagine dei comandi; None se e' fuori."""
        try:
            x = self.root.winfo_pointerx() - self.video.winfo_rootx()
            y = self.root.winfo_pointery() - self.video.winfo_rooty() - getattr(self, "ov_cima", 0)
        except tk.TclError:
            return None
        if 0 <= x < self.video.winfo_width() and 0 <= y < self.OV_ALTA:
            return x, y
        return None

    def ov_sotto_mouse(self):
        return bool(getattr(self, "ov", None) is not None and self.dove_ov())

    def zona_ov(self):
        p = self.dove_ov()
        if p is None:
            return None
        x, y = p
        for z in getattr(self, "ov_zone", []):
            if z[0] <= x < z[2] and z[1] <= y < z[3]:
                return z, x
        return None

    def giro_ov(self):
        """Mentre i comandi di mpv sono a vista: ogni 250 ms si ridisegnano
        se e' cambiato qualcosa, e il volume si apre col mouse sopra."""
        if not (self.barra_in_overlay() and self.barra_visibile):
            self.ov_giro = False
            if self.video.cget("cursor"):
                self.video.config(cursor="")
            return
        z = self.zona_ov()
        sopra = z[0][5] if z else None
        if sopra in (self.b_muto, self.volume, self.b_audio_menu):
            if getattr(self, "ov_sopra", None) not in (self.b_muto, self.volume, self.b_audio_menu):
                self.apri_volume()
        elif (getattr(self, "ov_sopra", None) in (self.b_muto, self.volume, self.b_audio_menu)
              and not getattr(self, "ov_menu", None)):
            self.chiudi_volume_tra_poco()
        self.ov_sopra = sopra


        riga_menu = self.riga_menu_ov() if getattr(self, "ov_menu", None) else None
        voce_menu_attiva = (riga_menu is not None and riga_menu >= 0
                            and self.ov_menu[riga_menu][1] is not _AZIONE_INFORMATIVA_AUDIO)
        su_qualcosa = bool(z) or voce_menu_attiva
        mano = "hand2" if su_qualcosa else ""
        if self.video.cget("cursor") != mano:
            self.video.config(cursor=mano)
        if getattr(self, "ov_menu", None):
            r = self.riga_menu_ov()
            if r != getattr(self, "ov_menu_sopra", None):
                self.ov_menu_sopra = r
                self.disegna_menu_ov()

            if r is None and sopra is not getattr(self, "ov_menu_bottone", None):
                if not getattr(self, "ov_menu_fuori", None):
                    self.ov_menu_fuori = time.time()
                elif time.time() - self.ov_menu_fuori > 2.0:
                    self.chiudi_menu_ov()
            else:
                self.ov_menu_fuori = None
        if self.sfondo_su:
            self.rifai_sfondo()

        if getattr(self, "ov_premuto", False) and getattr(self, "ov_trascina", None):
            self.trascina_ov()
        elif not getattr(self, "ov_premuto", False):
            self.ov_trascina = None
        self.disegna_ov()


        svelto = (self.dove_ov() is not None or getattr(self, "ov_menu", None)
                  or getattr(self, "ov_trascina", None))
        self.root.after(30 if svelto else (80 if self.registrando else 200), self.giro_ov)

    def trascina_ov(self):
        try:
            x = self.root.winfo_pointerx() - self.video.winfo_rootx()
        except tk.TclError:
            return
        if self.ov_trascina == "volume" and self.volume in getattr(self, "ov_posti", {}):
            x0, larga = self.ov_posti[self.volume]
            k = (x - x0 - 10) / float(max(1, larga - 20))
            self.volume.set(int(round(max(0.0, min(1.0, k)) * 100)))
        elif self.ov_trascina == "linea" and self.file_in_onda:
            k = max(0.0, min(1.0, x / float(max(1, self.video.winfo_width()))))
            if abs(k - getattr(self, "ov_ultimo_salto", -1)) > 0.002:
                self.ov_ultimo_salto = k
                self.parte_linea = k
                try:
                    self.mpv.command("seek", "%.2f" % (k * 100), "absolute-percent")
                except Exception:
                    pass

    def clic_ov(self):
        """Un clic sui comandi disegnati da mpv: True se l'ha preso lui."""
        if not (self.barra_in_overlay() and self.barra_visibile and getattr(self, "ov", None) is not None):
            return False
        if getattr(self, "ov_menu", None):
            i = self.riga_menu_ov()
            if i is not None:
                if (i >= 0 and self.ov_menu[i][1]
                        and self.ov_menu[i][1] is not _AZIONE_INFORMATIVA_AUDIO):
                    cosa = self.ov_menu[i][1]
                    self.chiudi_menu_ov()
                    self.root.after(40, cosa)
                return True

            z = self.zona_ov()
            ancora = getattr(self, "ov_menu_bottone", None)
            self.chiudi_menu_ov()
            if z and z[0][5] is ancora:
                return True
        z = self.zona_ov()
        if z is None:
            return self.dove_ov() is not None
        (x0, y0, x1, y1, tipo, w), x = z
        self.mosso()
        self.ov_trascina = "linea" if tipo == "linea" else ("volume" if w is self.volume else None)
        if tipo == "linea":
            if self.file_in_onda:
                k = max(0.0, min(1.0, x / float(max(1, self.video.winfo_width()))))
                try:
                    self.mpv.command("seek", "%.2f" % (k * 100), "absolute-percent")
                except Exception:
                    pass
        elif w is self.volume:
            larga = max(1, x1 - x0 - 20)
            self.volume.set(int(round(max(0.0, min(1.0, (x - x0 - 10) / float(larga))) * 100)))
        elif w is not None:
            try:
                w.invoke()
            except tk.TclError:
                pass
        self.root.after(30, lambda: self.disegna_ov(forza=True))
        return True


    def menu(self, titolo, voci):
        """Una voce della barra dei menu. voci e' una funzione che da' le
        righe al momento: la tendina si riempie ogni volta che si apre,
        cosi' dice la cosa giusta (spunte, Show/Hide, recenti...)."""
        m = tk.Menu(self.barra_menu, tearoff=0, **self.stile_menu)
        m.configure(postcommand=lambda: self.riempi_menu(m, voci))
        self.barra_menu.add_cascade(label=_(titolo), menu=m)
        self.menu_cima[titolo] = (self.barra_menu.index("end"), voci)

    def riempi_menu(self, m, voci):
        """(testo, cosa) = voce, cosa None = spenta; None = separatore;
        '*  ' davanti = spuntata, '   ' = spuntabile e no; (SCORRE, voci, n)
        = un sottomenu (i capitoli), a colonne se sono tanti."""
        m.delete(0, "end")
        for c in m.winfo_children():
            c.destroy()
        m.spunte = []
        for v in voci():
            if v is None:
                m.add_separator()
            elif v[0] is SCORRE:
                sotto = tk.Menu(m, tearoff=0, **self.stile_menu)
                for i, w in enumerate(v[1]):
                    self.voce_menu(sotto, w, colonna=bool(i) and i % 25 == 0)
                m.add_cascade(label=_("Chapters"), menu=sotto)
            else:
                self.voce_menu(m, v)

    def voce_menu(self, m, v, colonna=False):
        testo, cosa = v
        extra = {"state": "normal" if cosa else "disabled"}
        if cosa:
            extra["command"] = cosa
        if colonna:
            extra["columnbreak"] = 1
        if testo[:3] in ("*  ", "   "):
            var = tk.BooleanVar(m, value=testo.startswith("*  "))
            if not hasattr(m, "spunte"):
                m.spunte = []
            m.spunte.append(var)
            m.add_checkbutton(label=testo[3:], variable=var, **extra)
        else:
            m.add_command(label=testo, **extra)
    def cambia_lingua(self, codice):
        """La lingua nuova subito, senza riavviare: si riscrive quello che
        e' a vista, i menu si rifanno da soli quando si aprono."""
        global LINGUA
        LINGUA = codice
        self.cfg["lingua"] = codice
        scrivi_config(self.cfg)
        for titolo, (i, _v) in self.menu_cima.items():
            self.barra_menu.entryconfig(i, label=_(titolo))
        self.et_liste.config(text=_("Playlists"))
        self.rifai_albero()
        vecchio = self.cerca.vuota
        self.cerca.vuota = _("Search")
        if self.cerca.testo.get() == vecchio:
            self.cerca.testo.set(self.cerca.vuota)
        try:
            fermo = bool(self.mpv.pause)
        except Exception:
            fermo = False
        self.faccia(self.b_pausa, "play" if fermo else "pausa",
                    _("Resume") if fermo else _("Pause"))
        self.icona_volume()
        self.icona_preferito()
        self.scrivi_riga() if self.nome else self.scrivi(_("ready"))


        film = [f for f, i in self.info.items() if not f.startswith("_") and "titolo" in i]
        if film and self.conferma(_("Film titles and genres"),
                                  _("Download film titles and genres again in the new language?"),
                                  ok=_("Download")):
            for f in film:
                self.info.pop(f, None)
            threading.Thread(target=self._scarica_copertine, args=(film, self.media_in_uso),
                             daemon=True).start()

        chiave, secondi = self.avviso_chiave
        if chiave and self.avviso.winfo_ismapped():
            self.et_avviso.config(text=_(chiave[0]) % chiave[1:])

    def mostra_avviso(self, testo, secondi=8, chiave=None):
        """Il pannello viola sopra ai comandi, col messaggio; sparisce
        dopo `secondi` (0 = resta finche' non ci si clicca). Con `chiave`
        (testo inglese e argomenti) si puo' ritradurre al cambio lingua."""
        self.avviso_chiave = (chiave, secondi)
        self.et_avviso.config(text=testo)
        if not self.avviso.winfo_ismapped():
            self.avviso.pack(side="bottom", fill="x", before=self.primo_pannello())
        if self.timer_avviso:
            self.root.after_cancel(self.timer_avviso)
            self.timer_avviso = None
        if secondi:
            self.timer_avviso = self.root.after(int(secondi * 1000), self.chiudi_avviso)

    def chiudi_avviso(self):
        self.timer_avviso = None
        if self.avviso.winfo_ismapped():
            self.avviso.pack_forget()


    def metti_velocita(self, v):
        self.velocita = v
        try:
            self.mpv.speed = v
        except Exception:
            pass
        if "x%g" % v in self.icone:
            self.faccia(self.b_velocita, "x%g" % v, "x%g" % v)
        self.scrivi(_("speed x%g") % v)
        self.root.after(2000, self.scrivi_riga)

    def gira_velocita(self):
        """Il bottone x1: un giro fra le velocita', lente e veloci."""
        i = VELOCITA.index(self.velocita) if self.velocita in VELOCITA else 0
        self.metti_velocita(VELOCITA[(i + 1) % len(VELOCITA)])

    def metti_proporzioni(self, val):
        self.cfg["proporzioni"], self.cfg["riempi"] = val, False
        scrivi_config(self.cfg)
        self.applica_video()

    def riempi(self):
        self.cfg["riempi"] = not self.cfg.get("riempi")
        scrivi_config(self.cfg)
        self.applica_video()

    def deinterlaccia(self):
        self.cfg["deinterlaccia"] = not self.cfg.get("deinterlaccia")
        scrivi_config(self.cfg)
        self.applica_video()

    def decodifica_hw(self):
        """Decodifica con la scheda video (VA-API, NVDEC...): molta meno CPU
        coi video grandi. auto-safe: mpv la usa solo dove e' sicura e se no
        torna da solo al software. Accesa di serie."""
        self.cfg["hwdec"] = not self.cfg.get("hwdec", True)
        scrivi_config(self.cfg)
        self.applica_video()

    def regola(self, cosa, di):
        """Luminosita', contrasto, saturazione: da -100 a 100, a passi."""
        v = max(-100, min(100, int(self.cfg.get(cosa, 0)) + di))
        self.cfg[cosa] = v
        scrivi_config(self.cfg)
        self.applica_video()
        self.scrivi("%s %+d" % (_(cosa), v))
        self.root.after(2000, self.scrivi_riga)

    def azzera_immagine(self):
        for cosa in ("brightness", "contrast", "saturation"):
            self.cfg.pop(cosa, None)
        scrivi_config(self.cfg)
        self.applica_video()


    def dispositivi_uscita_audio(self):
        """Elenco delle uscite audio con descrizioni fornite da PipeWire."""
        risultati = []
        predefinito = self.nome_uscita_predefinita()
        try:
            risposta = subprocess.run(
                ["wpctl", "status", "-n"], capture_output=True,
                text=True, timeout=4, check=True
            )
            in_uscite = False
            nodi = []
            for riga in risposta.stdout.splitlines():
                testo = re.sub(r"^[│├└─\s]+", "", riga).strip()
                if testo == "Sinks:":
                    in_uscite = True
                    continue
                if in_uscite and testo.endswith(":"):
                    break
                if not in_uscite:
                    continue
                voce = re.match(r"^(\*)?\s*(\d+)\.?\s+(\S+)", testo)
                if voce:
                    nodi.append((voce.group(2), voce.group(3), bool(voce.group(1))))
            for identificativo, nome_tecnico, attiva in nodi:
                info = ""
                try:
                    info = subprocess.run(
                        ["wpctl", "inspect", identificativo],
                        capture_output=True, text=True, timeout=3, check=True
                    ).stdout
                except Exception:
                    continue
                nome_pipewire = ""
                descrizione = ""
                for chiave in ("node.description", "device.description",
                               "device.nick", "device.product.name",
                               "api.bluez5.alias", "node.nick"):
                    voce = re.search(
                        r'^[\s*]*' + re.escape(chiave) + r'\s*=\s*"([^"]+)"',
                        info, re.MULTILINE
                    )
                    if voce and voce.group(1).strip():
                        descrizione = voce.group(1).strip()
                        if chiave in ("node.description", "device.description"):
                            break
                voce = re.search(
                    r'^[\s*]*node.name\s*=\s*"([^"]+)"',
                    info, re.MULTILINE
                )
                nome_pipewire = voce.group(1).strip() if voce else nome_tecnico
                if not descrizione:
                    descrizione = nome_pipewire
                    for prefisso in ("alsa_output.", "bluez_output.", "alsa_input.", "bluez_input."):
                        descrizione = descrizione.removeprefix(prefisso)
                    descrizione = descrizione.replace("_", " ").replace(".", " ").strip()
                risultati.append((
                    "pulse/" + nome_pipewire, descrizione,
                    attiva or nome_pipewire == predefinito
                ))
        except Exception:
            try:
                risposta = subprocess.run(
                    ["pactl", "-f", "json", "list", "sinks"],
                    capture_output=True, text=True, timeout=3, check=True
                )
                for uscita in json.loads(risposta.stdout):
                    nome = str(uscita.get("name") or "").strip()
                    if not nome:
                        continue
                    proprieta = uscita.get("properties") or {}
                    descrizione = str(
                        uscita.get("description")
                        or proprieta.get("device.description")
                        or proprieta.get("device.nick")
                        or proprieta.get("device.product.name")
                        or nome.replace("_", " ").replace(".", " ")
                    ).strip()
                    risultati.append(("pulse/" + nome, descrizione, nome == predefinito))
            except Exception:
                pass
        unici = []
        visti = set()
        for nome, descrizione, attiva in risultati:
            if nome not in visti:
                visti.add(nome)
                unici.append((nome, descrizione, attiva))
        return unici

    def nome_uscita_predefinita(self):
        try:
            risposta = subprocess.run(
                ["pactl", "get-default-sink"], capture_output=True,
                text=True, timeout=2, check=True
            )
            nome = risposta.stdout.strip()
            if nome:
                return nome
        except Exception:
            pass
        try:
            risposta = subprocess.run(
                ["wpctl", "inspect", "@DEFAULT_AUDIO_SINK@"],
                capture_output=True, text=True, timeout=3, check=True
            )
            voce = re.search(
                r'^[\s*]*node.name\s*=\s*"([^"]+)"',
                risposta.stdout, re.MULTILINE
            )
            if voce:
                return voce.group(1).strip()
        except Exception:
            pass
        return ""

    def applica_uscita_audio(self, nome=None, silenzioso=False):
        """Usa esclusivamente l'uscita predefinita scelta dal sistema."""
        predefinito = self.nome_uscita_predefinita()
        if not predefinito:
            return False
        dispositivo = "pulse/" + predefinito
        try:
            self.mpv["audio-device"] = dispositivo
            attuale = str(self.mpv["audio-device"] or "")
            if attuale and attuale != dispositivo:
                raise RuntimeError(attuale)
            return True
        except Exception as errore:
            if not silenzioso:
                self.scrivi(_("cannot follow system audio output: %s") % errore, errore=True)
            return False

    def voci_uscita_audio(self):
        """Voci informative: non selezionabili, con pallino sull'uscita attiva."""
        return [
            (("*  " if attiva else "   ") + descrizione,
             (lambda n=nome.split("/", 1)[-1]: self.imposta_uscita_audio(n)))
            for nome, descrizione, attiva in self.dispositivi_uscita_audio()
        ]

    def imposta_uscita_audio(self, nome):
        """Imposta l'uscita predefinita del sistema e porta mpv sulla stessa."""
        try:
            ident = None
            stato = subprocess.run(
                ["wpctl", "status", "-n"], capture_output=True,
                text=True, timeout=4, check=True
            ).stdout
            in_uscite = False
            for riga in stato.splitlines():
                testo = re.sub(r"^[│├└─\s]+", "", riga).strip()
                if testo == "Sinks:":
                    in_uscite = True
                    continue
                if in_uscite and testo.endswith(":"):
                    break
                if in_uscite:
                    voce = re.match(r"^\*?\s*(\d+)\.?\s+(\S+)", testo)
                    if voce and voce.group(2) == nome:
                        ident = voce.group(1)
                        break
            if not ident:
                raise RuntimeError(nome)
            subprocess.run(
                ["wpctl", "set-default", ident],
                capture_output=True, timeout=3, check=True
            )
        except Exception as errore:
            self.scrivi(_("cannot follow system audio output: %s") % errore, errore=True)
            return
        self.sincronizza_uscita_audio_sistema()
        self.applica_tipo_uscita_audio(self.rileva_tipo_uscita_audio())

    def scegli_uscita_audio(self):
        """Finestra informativa delle uscite audio pubblicate dal sistema."""
        voci = self.voci_uscita_audio()
        finestra = tk.Toplevel(self.root)
        finestra.title(_("System audio outputs"))
        finestra.configure(bg=PANNELLO)
        finestra.transient(self.root)
        finestra.resizable(True, True)
        finestra.grab_set()
        tk.Label(
            finestra,
            text=_("*  System default output") + "\n" + _("manages audio routing."),
            bg=PANNELLO,
            fg=TESTO,
            justify="left",
            font=(CARATTERE, 10, STILE_CARATTERE)
        ).pack(anchor="w", padx=16, pady=(14, 8))
        lista = tk.Listbox(
            finestra,
            height=min(10, max(2, len(voci))),
            width=58,
            bg=IN_ONDA,
            fg=TESTO,
            activestyle="none",
            relief="flat",
            highlightthickness=0,
            exportselection=False
        )
        lista.pack(fill="both", expand=True, padx=14, pady=4)

        def scelto(e):
            i = lista.nearest(e.y)
            bb = lista.bbox(i)
            if bb and bb[1] <= e.y <= bb[1] + bb[3]:
                finestra.destroy()
                voci[i][1]()
        lista.bind("<ButtonRelease-1>", scelto)
        for testo, _azione in voci:
            lista.insert("end", testo.strip())
        for i, (testo, _azione) in enumerate(voci):
            if testo.startswith("*  "):
                lista.selection_set(i)
                lista.activate(i)
                lista.see(i)
                break
        tk.Button(
            finestra,
            text=_("Close"),
            command=finestra.destroy,
            relief="flat",
            bg=ACCENTO,
            fg="#17121f",
            padx=16,
            pady=6
        ).pack(anchor="e", padx=14, pady=14)

    def sincronizza_uscita_audio_sistema(self):
        predefinito = self.nome_uscita_predefinita()
        if not predefinito:
            return
        dispositivo = "pulse/" + predefinito
        try:
            attuale = str(self.mpv["audio-device"] or "")
        except Exception:
            attuale = ""
        if attuale != dispositivo:
            self.applica_uscita_audio(silenzioso=True)

    def freccia_audio(self, su):
        icona = "aperto_su" if su else "aperto"
        if icona in self.icone:
            self.b_audio_menu.icona = icona
            self.vesti(self.b_audio_menu)
        if self.barra_in_overlay():
            self.disegna_ov()

    def chiudi_menu_uscita_audio(self):
        self.freccia_audio(False)
        self.chiudi_volume_tra_poco()

    def apri_menu_uscita_audio(self):
        voci = self.voci_uscita_audio()
        if self.barra_in_overlay():
            if getattr(self, "ov_menu", None):
                self.chiudi_menu_ov()
            else:
                self.freccia_audio(True)
                self.ov_menu_bottone = self.b_audio_menu
                self.ov_menu, self.ov_menu_sopra = voci, None
                self.disegna_menu_ov()
            return
        if self.tendina.aperta() and self.tendina.chi is self.b_audio_menu:
            self.tendina.chiudi()
            return
        self.freccia_audio(True)
        b = self.b_audio_menu
        self.tendina.apri(
            b.winfo_rootx() + b.winfo_width(),
            b.winfo_rooty() - 4,
            voci,
            sopra=True,
            destra=True,
            al_chiudere=self.chiudi_menu_uscita_audio,
            chi=b
        )

    def applica_video(self):
        """Le impostazioni video del config date a mpv: valgono per tutti
        i canali, anche ai prossimi avvii."""
        try:
            self.mpv.video_aspect_override = self.cfg.get("proporzioni", "-1")
            self.mpv.panscan = 1.0 if self.cfg.get("riempi") else 0.0
            self.mpv.deinterlace = "yes" if self.cfg.get("deinterlaccia") else "no"
            for cosa in ("brightness", "contrast", "saturation"):
                setattr(self.mpv, cosa, int(self.cfg.get(cosa, 0)))
            self.applica_filtri_audio()
        except Exception:
            pass

        hw = "auto-safe" if self.cfg.get("hwdec", True) else "no"
        if getattr(self, "hwdec_messo", "no") != hw:
            try:
                self.mpv.hwdec = hw
                self.hwdec_messo = hw
            except Exception:
                pass
        self.alza_volume(self.volume.get())

    def istantanea(self):
        try:
            os.makedirs(os.path.join(CASA, "Pictures", "xvb"), exist_ok=True)
            self.mpv.command("screenshot", "video")
            self.scrivi(_("screenshot saved in %s") % os.path.join("~", "Pictures", "xvb"))
        except Exception as e:
            self.scrivi(_("cannot take a screenshot: %s") % e, errore=True)
        self.root.after(3000, self.scrivi_riga)

    def menu_testo(self, ev):
        """Tasto destro in una casella di testo: Cut, Copy, Paste, Select all."""
        w = ev.widget
        try:
            stato = str(w.cget("state"))
        except Exception:
            return None
        if stato == "disabled":
            return None
        w.focus_set()
        scrivibile = stato != "readonly"
        try:
            selezione = bool(w.selection_present())
        except Exception:
            selezione = False
        try:
            negli_appunti = bool(self.root.clipboard_get())
        except tk.TclError:
            negli_appunti = False
        m = tk.Menu(w, tearoff=0, **self.stile_menu)
        m.add_command(label=_("Cut"), command=lambda: w.event_generate("<<Cut>>"),
                      state="normal" if selezione and scrivibile else "disabled")
        m.add_command(label=_("Copy"), command=lambda: w.event_generate("<<Copy>>"),
                      state="normal" if selezione else "disabled")
        m.add_command(label=_("Paste"), command=lambda: w.event_generate("<<Paste>>"),
                      state="normal" if negli_appunti and scrivibile else "disabled")
        m.add_separator()
        m.add_command(label=_("Select all"),
                      command=lambda: (w.select_range(0, "end"), w.icursor("end")))
        self._menu_testo = m
        try:
            m.tk_popup(ev.x_root, ev.y_root)
        finally:
            m.grab_release()
        return "break"

    def buffer_attuale(self):
        for voce in BUFFER:
            if voce[0] == self.cfg.get("buffer", "standard"):
                return voce
        return BUFFER[2]

    def metti_buffer(self, chiave):
        """Il buffer di mpv, anche mentre suona: per chi ha poca RAM."""
        for voce in BUFFER:
            if voce[0] != chiave:
                continue
            try:
                self.mpv.demuxer_max_bytes = voce[2] * 1048576
                self.mpv.demuxer_max_back_bytes = voce[3] * 1048576
                self.mpv.demuxer_readahead_secs = voce[4]
                self.mpv.cache_pause_wait = voce[5]
            except Exception:
                pass
            self.cfg["buffer"] = chiave
            scrivi_config(self.cfg)
            self.scrivi("%s: %s" % (_("Buffer"), _(voce[1])))
            return

    def sempre_in_cima(self):
        self.cfg["in_cima"] = not self.cfg.get("in_cima")
        scrivi_config(self.cfg)
        self.root.attributes("-topmost", bool(self.cfg["in_cima"]))

    def boost(self):
        self.cfg["boost"] = not self.cfg.get("boost")
        scrivi_config(self.cfg)
        self.alza_volume(self.volume.get())


    def icona_sorgente(self, nome):
        """Le icone di sempre della sidebar (music, player)."""
        for est in (".png", ".jpg", ".jpeg", ".gif"):
            g = os.path.join(QUI, "icone", nome + est)
            if os.path.isfile(g):
                if g not in self.img_liste:
                    im = carica_logo(g, PUNTO, PUNTO, RIDOTTE)
                    if im is not None:
                        self.img_liste[g] = im
                return self.img_liste.get(g, "")
        return ""

    def aggiorna_formato(self):
        """Il codec audio vero (AAC, MP3, FLAC...) in testa alla barra."""
        t = ""
        try:
            n = self.mpv._get_property("audio-codec-name")
            if n:
                t = str(n).upper() + "    "
        except Exception:
            t = ""
        if self.et_formato.cget("text") != t:
            self.et_formato.config(text=t)
        self.et_formato.config(fg="#ffffff")
        self.root.after(1000, self.aggiorna_formato)

    def disegna_tipo(self, tipo, col):
        """Icona del tipo di contenuto: musica, video o tv (IPTV)."""
        c = self.ic_tipo
        c.delete("all")
        if tipo == "musica":
            c.create_oval(2, 8, 8, 13, fill=col, outline="")
            c.create_rectangle(7, 1, 9, 10, fill=col, outline="")
            c.create_polygon(9, 1, 14, 3, 14, 6, 9, 5, fill=col, outline="")
        elif tipo == "video":
            c.create_rectangle(1, 3, 10, 11, fill=col, outline="")
            c.create_polygon(11, 7, 15, 3, 15, 11, fill=col, outline="")
        elif tipo == "tv":
            c.create_rectangle(1, 4, 15, 13, outline=col, width=2)
            c.create_line(5, 1, 8, 4, fill=col, width=2)
            c.create_line(11, 1, 8, 4, fill=col, width=2)

    def anima_livello(self):
        """Equalizzatore in barra di stato: dieci LED sui dieci canali."""
        liv = [0.0] * 10
        try:
            m = self.mpv
            if not (m.pause or m.mute):
                md = m._get_property("af-metadata/lvl") or {}
                for k in range(10):
                    p = float(md.get(
                        "lavfi.astats.%d.Peak_level" % (k + 1), "-inf"))
                    if p > -45.0:
                        liv[k] = min(1.0, (p + 45.0) / 45.0)
        except Exception:
            liv = [0.0] * 10

        b = self.livello_barre
        for k in range(10):
            b[k] = max(liv[k], b[k] * 0.7)

        c = self.livello
        c.delete("all")
        col = schiarisci(self.colore_barra) if self.colore_barra else ACCENTO
        for i, x in enumerate(b):
            for j in range(max(1, int(round(x * 5)))):
                y = 14 - j * 3
                c.create_rectangle(i * 4, y - 2, i * 4 + 3, y,
                                   fill=col, outline="")
        self.root.after(35, self.anima_livello)

    def applica_filtri_audio(self):
        """Applica normalizzazione ed equalizzatore a mpv."""
        try:
            filtri = []

            if self.cfg.get("normalizza"):
                filtri.append("dynaudnorm=f=150:g=15")

            frequenze = (
                31, 62, 125, 250, 500,
                1000, 2000, 4000, 8000, 16000
            )

            bande = self.cfg.get("eq_bande", [0] * 10)

            if not isinstance(bande, list) or len(bande) != 10:
                bande = [0] * 10

            equalizzatori = []

            for frequenza, guadagno in zip(frequenze, bande):
                guadagno = max(-12, min(12, int(guadagno)))

                if guadagno != 0:
                    equalizzatori.append(
                        "equalizer=f=%d:width_type=o:width=1:g=%d"
                        % (frequenza, guadagno)
                    )

            filtri.extend(equalizzatori)

            grafo = ",".join(["aformat=channel_layouts=stereo"] + filtri) + (
                ",acrossover=split=150 600 2400 8000:order=4th"
                "[b0][b1][b2][b3][b4];[b0][b1][b2][b3][b4]amerge=inputs=5,"
                "astats=metadata=1:reset=1:measure_perchannel=Peak_level:"
                "measure_overall=none,"
                "pan=stereo|c0=c0+c2+c4+c6+c8|c1=c1+c3+c5+c7+c9")
            self.mpv.af = "@lvl:lavfi=graph=%%%d%%%s" % (
                len(grafo.encode()), grafo)

        except Exception:
            try:
                _XVB_LOGGER.exception(
                    "Errore durante l'applicazione dei filtri audio"
                )
            except Exception:
                pass

    def equalizzatore_audio(self):
        """Finestra equalizzatore a dieci bande."""

        originali = list(self.cfg.get("eq_bande", [0] * 10))

        if len(originali) != 10:
            originali = [0] * 10

        finestra = tk.Toplevel(self.root)
        finestra.title(_("Audio equalizer"))
        finestra.configure(bg=PANNELLO)
        finestra.transient(self.root)
        finestra.resizable(False, False)
        finestra.grab_set()

        tk.Label(
            finestra,
            text=_("Adjust each frequency band"),
            bg=PANNELLO,
            fg=TESTO,
            font=(CARATTERE, 10, STILE_CARATTERE)
        ).pack(padx=16, pady=(14, 8))

        preset = (
            ("Flat", (0, 0, 0, 0, 0, 0, 0, 0, 0, 0)),
            ("Rock", (5, 4, 3, 1, -1, -1, 1, 3, 4, 5)),
            ("Pop", (-1, 2, 4, 5, 3, 0, -1, -1, -1, -1)),
            ("Jazz", (3, 2, 1, 2, -2, -2, 0, 1, 2, 3)),
            ("Classical", (0, 0, 0, 0, 0, 0, -4, -4, -4, -6)),
            ("Dance", (6, 5, 3, 0, 0, -3, -4, -4, 0, 0)),
            ("Club", (0, 0, 4, 3, 3, 3, 2, 0, 0, 0)),
            ("Party", (6, 6, 0, 0, 0, 0, 0, 0, 6, 6)),
            ("Live", (-3, 0, 3, 4, 4, 4, 3, 2, 2, 2)),
            ("Reggae", (0, 0, -1, -4, 0, 4, 4, 0, 0, 0)),
            ("Techno", (6, 5, 0, -4, -4, 0, 6, 8, 8, 7)),
            ("Soft", (4, 2, 0, -2, 0, 3, 6, 7, 8, 9)),
            ("Vocal", (-3, -3, -1, 2, 4, 4, 3, 1, 0, -1)),
            ("Full Bass", (10, 8, 6, 3, 1, 0, 0, 0, 0, 0)),
            ("Full Treble", (0, 0, 0, 0, 0, 2, 5, 8, 10, 11)),
            ("Full Bass & Treble", (7, 5, 0, -4, -3, 1, 4, 7, 9, 10)),
            ("Laptop Speakers", (5, 10, 5, -2, -2, 1, 3, 6, 8, 9)),
        )

        def nome_per(valori):
            valori = tuple(int(x) for x in valori)
            for n, v in preset:
                if v == valori:
                    return n
            return _("Custom")

        scelto = tk.StringVar(value=nome_per(originali))
        bloccato = [False]

        riga_preset = tk.Frame(finestra, bg=PANNELLO)
        riga_preset.pack(padx=16, pady=(0, 8))
        tk.Label(
            riga_preset,
            text=_("Preset"),
            bg=PANNELLO,
            fg=GRIGIO,
            font=(CARATTERE, 9, STILE_CARATTERE)
        ).pack(side="left", padx=(0, 8))
        menu_preset = tk.OptionMenu(
            riga_preset, scelto, *[n for n, _v in preset],
            command=lambda n: applica_preset(n))
        menu_preset.config(
            bg=IN_ONDA, fg=TESTO, activebackground=ACCENTO,
            activeforeground=TESTO, relief="flat", highlightthickness=0,
            bd=0, width=18, font=(CARATTERE, 9, STILE_CARATTERE))
        menu_preset["menu"].config(
            bg=PANNELLO, fg=TESTO, activebackground=ACCENTO,
            activeforeground=TESTO, bd=0,
            font=(CARATTERE, 9, STILE_CARATTERE))
        menu_preset.pack(side="left")

        frequenze = (
            "31", "62", "125", "250", "500",
            "1k", "2k", "4k", "8k", "16k"
        )

        contenitore = tk.Frame(finestra, bg=PANNELLO)
        contenitore.pack(padx=12, pady=4)

        variabili = []

        def aggiorna(_valore=None):
            self.cfg["eq_bande"] = [
                int(round(v.get())) for v in variabili
            ]
            self.applica_filtri_audio()
            if not bloccato[0]:
                scelto.set(nome_per(self.cfg["eq_bande"]))

        def applica_preset(nome):
            for n, valori in preset:
                if n == nome:
                    bloccato[0] = True
                    try:
                        for v, g in zip(variabili, valori):
                            v.set(g)
                        aggiorna()
                    finally:
                        bloccato[0] = False
                    scelto.set(nome)
                    break

        for i, frequenza in enumerate(frequenze):
            colonna = tk.Frame(contenitore, bg=PANNELLO)
            colonna.grid(row=0, column=i, padx=2, sticky="n")

            tk.Label(
                colonna,
                text=frequenza,
                bg=PANNELLO,
                fg=GRIGIO,
                font=(CARATTERE, 8, STILE_CARATTERE)
            ).pack()

            variabile = tk.DoubleVar(value=originali[i])
            variabili.append(variabile)

            tk.Scale(
                colonna,
                from_=12,
                to=-12,
                resolution=1,
                orient="vertical",
                length=185,
                showvalue=True,
                variable=variabile,
                command=aggiorna,
                bg=PANNELLO,
                fg=TESTO,
                troughcolor=IN_ONDA,
                activebackground=ACCENTO,
                highlightthickness=0,
                bd=0
            ).pack()

        tk.Label(
            finestra,
            text=_("Gain (dB) - 0 = flat"),
            bg=PANNELLO,
            fg=GRIGIO,
            font=(CARATTERE, 8, STILE_CARATTERE)
        ).pack(pady=(0, 8))

        def neutro():
            for variabile in variabili:
                variabile.set(0)
            aggiorna()

        def chiudi(salva):
            if salva:
                self.cfg["eq_bande"] = [
                    int(round(v.get())) for v in variabili
                ]
                scrivi_config(self.cfg)
            else:
                self.cfg["eq_bande"] = originali

            self.applica_filtri_audio()

            try:
                finestra.grab_release()
                finestra.destroy()
            except Exception:
                pass

        pulsanti = tk.Frame(finestra, bg=PANNELLO)
        pulsanti.pack(fill="x", padx=14, pady=(4, 14))

        tk.Button(
            pulsanti,
            text=_("Flat"),
            command=neutro,
            relief="flat",
            bg=IN_ONDA,
            fg=TESTO_ONDA,
            padx=12,
            pady=5
        ).pack(side="left")

        tk.Button(
            pulsanti,
            text=_("Cancel"),
            command=lambda: chiudi(False),
            relief="flat",
            bg=IN_ONDA,
            fg=TESTO_ONDA,
            padx=12,
            pady=5
        ).pack(side="right", padx=(6, 0))

        tk.Button(
            pulsanti,
            text=_("OK"),
            command=lambda: chiudi(True),
            relief="flat",
            bg=ACCENTO,
            fg="#17121f",
            padx=16,
            pady=5
        ).pack(side="right")

        finestra.protocol(
            "WM_DELETE_WINDOW",
            lambda: chiudi(False)
        )

        finestra.update_idletasks()

        x = self.root.winfo_rootx() + (
            self.root.winfo_width() - finestra.winfo_width()
        ) // 2

        y = self.root.winfo_rooty() + (
            self.root.winfo_height() - finestra.winfo_height()
        ) // 2

        finestra.geometry(
            "+%d+%d" % (max(0, x), max(0, y))
        )

    def normalizza(self):
        self.cfg["normalizza"] = not self.cfg.get("normalizza")
        scrivi_config(self.cfg)
        self.applica_video()

    def tracce(self, tipo):
        """Le tracce (audio o sub) del flusso in onda, da mpv."""
        try:
            return [t for t in (self.mpv.track_list or []) if t.get("type") == tipo]
        except Exception:
            return []

    def nome_traccia(self, t):
        nome = t.get("title") or t.get("lang") or ""
        extra = t.get("codec") or ""
        return ("%s (%s)" % (nome, extra) if nome and extra else nome or extra or str(t.get("id")))

    def metti_traccia(self, cosa, i):
        try:
            setattr(self.mpv, cosa, i)
        except Exception:
            pass


    def controlla_versione(self, zitto=False):
        """Chiede a GitHub l'ultima release; se e' piu' nuova lo dice
        nella riga di stato. Con zitto, se non c'e' niente non dice nulla."""
        threading.Thread(target=self._controlla_versione, args=(zitto,),
                         daemon=True).start()

    def _controlla_versione(self, zitto):
        try:
            r = Request("https://api.github.com/repos/%s/releases/latest" % REPO,
                        headers={"User-Agent": UA, "Accept": "application/vnd.github+json"})
            d = json.loads(urlopen(r, timeout=10).read().decode("utf-8", "ignore"))
            tag = str(d.get("tag_name", "")).lstrip("vV")
            pagina = d.get("html_url", "")
        except Exception as e:
            if not zitto:
                self.scrivi(_("cannot check for updates: %s") % e, errore=True)
            return
        if tag and piu_nuova(tag, VERSIONE):
            self.nuova, self.pagina_nuova = tag, pagina
            self.root.after(0, lambda: self.mostra_avviso(
                _("new version %s available: Help > Download") % tag, 0,
                chiave=("new version %s available: Help > Download", tag)))
        elif not zitto:
            self.scrivi(_("up to date (%s)") % VERSIONE)
            self.root.after(3000, self.scrivi_riga)

    def chiedi(self, titolo, domanda, valore="", maschera=False):
        """Una riga di testo dall'utente. La password può essere mascherata."""
        def corpo(dentro):
            tk.Label(dentro, text=titolo, bg=PANNELLO, fg=TESTO, anchor="w",
                     font=(CARATTERE, 11, "bold", STILE_CARATTERE)).pack(fill="x")
        f = Finestrella(
            self.root, titolo, corpo,
            chiedi=domanda, valore=valore, maschera=maschera
        )
        self.root.wait_window(f)
        return f.risposta

    def informazioni(self):
        """La finestrella About: icona, nome, versione, autore, link."""
        def corpo(dentro):
            riga = tk.Frame(dentro, bg=PANNELLO)
            riga.pack(fill="x")
            if getattr(self, "icona_finestra", None) is not None and HA_PIL:
                try:
                    im = Image.open(self.icona_file).convert("RGBA").resize((64, 64), Image.LANCZOS)
                    self.icona_about = ImageTk.PhotoImage(im)
                    tk.Label(riga, image=self.icona_about, bg=PANNELLO).pack(side="left", padx=(0, 16))
                except Exception:
                    pass
            testi = tk.Frame(riga, bg=PANNELLO)
            testi.pack(side="left", fill="x")
            tk.Label(testi, text="XVB", bg=PANNELLO, fg=TESTO, anchor="w",
                     font=(CARATTERE, 16, "bold", STILE_CARATTERE)).pack(fill="x")
            tk.Label(testi, text="Extended Video Broadcast", bg=PANNELLO, fg=GRIGIO,
                     anchor="w").pack(fill="x")
            tk.Label(testi, text=_("Version") + " " + VERSIONE, bg=PANNELLO, fg=ACCENTO,
                     anchor="w").pack(fill="x", pady=(6, 0))
            stato = (_("new version %s available") % self.nuova) if self.nuova else _("Up to date")
            tk.Label(dentro, text=stato, bg=PANNELLO, fg=TESTO, anchor="w").pack(fill="x", pady=(14, 0))
            tk.Label(dentro, text="\u00a9 %s %s" % (ANNO, AUTORE), bg=PANNELLO, fg=GRIGIO,
                     anchor="w").pack(fill="x", pady=(10, 0))
            tk.Label(dentro, text=_("MIT license"), bg=PANNELLO, fg=GRIGIO, anchor="w").pack(fill="x")
            link = tk.Label(dentro, text="github.com/" + REPO, bg=PANNELLO, fg=ACCENTO,
                            anchor="w", cursor="hand2")
            link.pack(fill="x", pady=(10, 0))
            link.bind("<Button-1>", lambda e: subprocess.Popen(["xdg-open", "https://github.com/" + REPO]))
            dona = tk.Label(dentro, text="\u2665 " + _("Donate"), bg=PANNELLO, fg=ACCENTO,
                            anchor="w", cursor="hand2")
            dona.pack(fill="x")
            dona.bind("<Button-1>", lambda e: self.dona())

            p = os.path.join(QUI, "icone", "tmdb.png")
            if HA_PIL and os.path.isfile(p):
                self.logo_tmdb = carica_logo(p, 90, 14)
                if self.logo_tmdb is not None:
                    tk.Label(dentro, image=self.logo_tmdb, bg=PANNELLO, anchor="w").pack(
                        fill="x", pady=(14, 0))
            tk.Label(dentro, text="This product uses the TMDB API but is not endorsed or "
                     "certified by TMDB.", bg=PANNELLO, fg=GRIGIO, anchor="w", justify="left",
                     wraplength=360, font=(CARATTERE, 8, STILE_CARATTERE)).pack(fill="x", pady=(4, 0))
        Finestrella(self.root, _("About XVB..."), corpo)

    def dona(self):
        try:
            subprocess.Popen(["xdg-open", DONA])
        except Exception as e:
            self.scrivi(_("cannot open %s: %s") % (DONA, e), errore=True)

    def apri_release(self):
        if self.pagina_nuova:
            try:
                subprocess.Popen(["xdg-open", self.pagina_nuova])
            except Exception as e:
                self.scrivi(_("cannot open %s: %s") % (self.pagina_nuova, e), errore=True)

    def apri_cartella_liste(self):
        cartella = cartella_liste_in_uso()
        try:
            os.makedirs(cartella, exist_ok=True)
            subprocess.Popen(["xdg-open", cartella])
        except Exception as e:
            self.scrivi(_("cannot open %s: %s") % (cartella, e), errore=True)

    def sidebar_aperte(self):
        return not (self.nascosti.get(self.sinistra) and self.nascosti.get(self.destra))

    def sidebar(self):
        """Il bottone nella barra: via tutte e due le barre laterali, o di
        nuovo tutte e due. Vale uguale a schermo intero e no."""
        via = not (self.nascosti.get(self.sinistra) and self.nascosti.get(self.destra))
        self.nascosti[self.sinistra] = self.nascosti[self.destra] = via
        self.ridisponi()
        self.icona_sidebar()

    def icona_sidebar(self):
        """L'icona del bottone delle sidebar: girata di 180 gradi quando
        sono chiuse (la png ruotata al volo con PIL)."""
        chiuse = self.nascosti.get(self.sinistra) and self.nascosti.get(self.destra)
        if "playlist" not in self.icone_pil:
            return
        self.root.update_idletasks()
        if "playlist_chiuso" not in self.icone:
            im = self.icone_pil["playlist"].rotate(180)
            self.icone_pil["playlist_chiuso"] = im
            self.icone["playlist_chiuso"] = ImageTk.PhotoImage(im)
        self.faccia(self.b_sidebar, "playlist_chiuso" if chiuse else "playlist", _("Playlists"))

    def ridisponi(self):
        """Rifa' la disposizione com'e' adesso: i comandi a vista, e se c'e'
        solo il video parte la sorveglianza del mouse che li nasconde."""
        self.disponi(self.pieno)
        self.barra_visibile = False
        self.mosso()
        if self.nasconde_barra() and not self.sorveglio:
            self.sorveglio = True
            self.dove_era = self.root.winfo_pointerxy()
            self.sorveglia_mouse()

    def nascondi(self, pannello):
        self.nascosti[pannello] = not self.nascosti.get(pannello)
        self.ridisponi()

    def ricarica_epg(self):
        """Butta le copie in cache e riscarica tutte le guide."""
        if not self.cfg.get("epg"):
            self.scrivi(_("no TV guide set"))
            return
        for dove in self.cfg["epg"]:
            try:
                if not os.path.isfile(dove):
                    f = os.path.join(CACHE_EPG, hashlib.md5(dove.encode()).hexdigest())
                    if os.path.isfile(f):
                        os.remove(f)
            except Exception:
                pass
        self.carica_guide()

    def togli_epg(self, dove):
        """Via una guida sola, quella detta nella voce di menu."""
        self.cfg["epg"] = [d for d in self.cfg.get("epg", []) if d != dove]
        scrivi_config(self.cfg)
        self.rifai_albero()
        self.scrivi(_("removed %s") % self.nome_guida(dove))
        self.cfg.get("epg_nomi", {}).pop(dove, None)
        self.cfg.get("epg_nomi_utente", {}).pop(dove, None)
        self.carica_guide()

    def apri_guida(self):
        """La finestra della griglia dei programmi; una sola alla volta."""
        if not self.epg:
            self.scrivi(_("no TV guide set"))
            return
        g = getattr(self, "finestra_guida", None)
        if g is not None and g.winfo_exists():
            g.lift()
            return
        self.finestra_guida = Guida(self)

    def chiedi_epg(self):
        """Chiede l'url (o il percorso) di una guida XMLTV e la aggiunge
        alle altre."""
        dove = self.chiedi(_("TV guide"), _("URL or file of the guide (XMLTV, .gz is fine):"))
        if not dove or not dove.strip():
            return
        dove = dove.strip()
        if dove in self.cfg.get("epg", []):
            self.scrivi(_("%s is already there") % self.nome_guida(dove))
            return
        self.cfg.setdefault("epg", []).append(dove)
        scrivi_config(self.cfg)
        self.rifai_albero()
        self.carica_guide()

    def nome_guida(self, dove):
        """Il nome di una guida: quello del file dentro all'archivio, se
        lo si e' gia' letto; se no l'ultima parola dell'url."""
        return (self.cfg.get("epg_nomi_utente", {}).get(dove)
                or self.cfg.get("epg_nomi", {}).get(dove) or nome_epg(dove))

    def carica_guide(self, silenzioso=False):
        """Tutte le guide del config, in un thread, unite in una sola."""
        threading.Thread(target=self._carica_guide,
                         args=(list(self.cfg.get("epg", [])), silenzioso), daemon=True).start()

    def _carica_guide(self, dove_tutte, silenzioso=False):
        global EPG_ICONE, EPG_ICONE_NOME, EPG_DESC, EPG_DESC_NOME
        self.guide_in_corso = True
        self.epg_tentata = time.time()
        quando = time.time()
        try:
            EPG_ICONE, EPG_ICONE_NOME, EPG_DESC, EPG_DESC_NOME = {}, {}, {}, {}
            guide = []
            for dove in dove_tutte:
                if not silenzioso:
                    self.scrivi(_("loading %s...") % self.nome_guida(dove))
                try:
                    f = prendi_epg(dove)
                    dentro = nome_dentro(f)
                    if dentro:
                        self.cfg.setdefault("epg_nomi", {})[dove] = dentro
                        scrivi_config(self.cfg)
                    guide.append(leggi_epg(f))
                except Exception as e:
                    self.scrivi(_("guide not loaded: %s") % e, errore=True)
                    time.sleep(2)
            if guide or not silenzioso:
                nomi, programmi = unisci_guide(guide)
                self.epg_nomi, self.epg = nomi, programmi
                self.epg_quando = quando
        finally:
            self.guide_in_corso = False
        if silenzioso:
            return
        self.root.after(0, self.rifai_albero)
        self.root.after(0, self.filtra)
        self.scrivi(_("guide loaded: %d channels, %d programmes") % (
            len(self.epg_nomi), sum(len(v) for v in self.epg.values())))
        self.root.after(4000, self.scrivi_riga)

    def giro_liste(self):
        """Ogni mezz'ora: le liste da url con la copia piu' vecchia di sei
        ore si riscaricano in silenzio; se una e' cambiata si rilegge l'elenco."""
        def lavoro():
            cambiata = False
            for u in list(self.cfg.get("liste_url", {})):
                try:
                    f = os.path.join(CACHE_LISTE, hashlib.md5(u.encode()).hexdigest() + ".m3u")
                    prima = None
                    if os.path.isfile(f):
                        if time.time() - os.path.getmtime(f) < LISTA_VECCHIA:
                            continue
                        with open(f, "rb") as o:
                            prima = o.read()
                    copia_lista_url(u, "forza")
                    with open(f, "rb") as o:
                        if o.read() != prima:
                            cambiata = True
                except Exception:
                    pass
            if cambiata:
                self.root.after(0, self._liste_aggiornate)
        threading.Thread(target=lavoro, daemon=True).start()
        self.root.after(30 * 60 * 1000, self.giro_liste)

    def _liste_aggiornate(self):
        if self.media_in_uso is None and self.reg_in_uso is None:
            self.rifai_liste(scegli=self.cfg.get("lista"))

    def giro_guide(self):
        """Ogni mezz'ora: se una guida ha piu' di sei ore (o il file locale e'
        cambiato) si riscaricano tutte, in silenzio. Dopo un tentativo non si
        riprova prima di un'ora."""
        try:
            ora = time.time()
            vecchia = False
            for dove in self.cfg.get("epg", []):
                if os.path.isfile(dove):
                    if os.path.getmtime(dove) > getattr(self, "epg_quando", 0):
                        vecchia = True
                else:
                    f = os.path.join(CACHE_EPG, hashlib.md5(dove.encode()).hexdigest())
                    if not os.path.isfile(f) or ora - os.path.getmtime(f) >= EPG_VECCHIA:
                        vecchia = True
            if (vecchia and self.cfg.get("epg") and not getattr(self, "guide_in_corso", False)
                    and ora - getattr(self, "epg_tentata", 0) >= 3600):
                self.carica_guide(silenzioso=True)
        except Exception:
            pass
        self.root.after(30 * 60 * 1000, self.giro_guide)

    def programma(self):
        """Il programma in onda sul canale: (inizio, fine, titolo), o
        None. Il canale si trova per tvg-id, se no per nome."""
        if not self.epg or self.file_in_onda:
            return None
        return self.programma_di(self.ide_in_onda, self.nome_in_onda)

    def accetta_trascinati(self):
        """Trascina e rilascia: file e cartelle portati sulla finestra si
        aprono come da "Apri con XVB". Serve tkdnd (il pacchetto tkdnd);
        senza, semplicemente non si puo'. tkdnd 2.6 consegna il rilascio al
        widget sotto il mouse e non risale ai genitori: ogni widget della
        finestra va registrato (e si ricontrolla ogni tanto per i nuovi)."""
        if not hasattr(self, "dnd_fatti"):
            try:
                self.root.tk.call("package", "require", "tkdnd")
            except tk.TclError:
                return
            self.dnd_fatti = set()
            self.dnd_cmd = self.root.register(self.rilasciati)
        da_fare = [self.root]
        while da_fare:
            w = da_fare.pop()
            try:

                da_fare.extend(c for c in w.winfo_children()
                               if not isinstance(c, (tk.Toplevel, tk.Menu)))
                p = str(w)
                if p in self.dnd_fatti:
                    continue
                self.root.tk.call("tkdnd::drop_target", "register", p, "DND_Files")
                self.root.tk.call("bind", p, "<<Drop:DND_Files>>", self.dnd_cmd + " %D")
                self.dnd_fatti.add(p)
            except tk.TclError:
                pass
        self.root.after(3000, self.accetta_trascinati)

    def rilasciati(self, dati):
        try:
            voci = self.root.tk.splitlist(dati)
        except tk.TclError:
            voci = ()
        dove = []
        for d in voci:
            if d.startswith("file://"):
                from urllib.parse import unquote
                d = unquote(urlparse(d).path)
            if d and os.path.exists(d):
                dove.append(d)
        if dove:
            self.root.after(0, lambda: self.apri_da_fuori(dove))
        return "copy"


    def avvia_mpris(self):
        self.mpris = avvia_mpris(self)
        if self.mpris is not None:
            self.giro_mpris()

    def giro_mpris(self):
        """Ogni secondo: quello che suona, per il pannello del desktop."""
        try:
            self.mpris.aggiorna(self.dati_mpris())
        except Exception:
            pass
        self.root.after(1000, self.giro_mpris)

    def dati_mpris(self):
        u = self.file_in_onda or self.cfg.get("canale") or ""
        fermo = bool(getattr(self, "fermato", None)) or not self.nome_in_onda or not u
        try:
            pausa = bool(self.mpv.pause)
        except Exception:
            pausa = False
        try:
            durata = float(self.mpv.duration or 0)
        except Exception:
            durata = 0
        titolo = artista = album = arte = ""
        if not fermo:
            nome = self.nome_in_onda
            if os.path.isabs(u) and os.path.splitext(u)[1][1:].lower() in AUDIO:
                titolo, artista, album, _a = self.tag_file.get(u, ("", "", "", ""))
                if not (titolo or artista or album):
                    titolo, artista, album, _a = dal_percorso(u, self.cfg.get("cartelle", []))
                album = album or self.info.get(u, {}).get("album", "")
                titolo = titolo or nome
                if u not in self.arte_mpris:
                    try:
                        self.arte_mpris[u] = anteprima_di(u) or ""
                    except Exception:
                        self.arte_mpris[u] = ""
                arte = self.arte_mpris[u]
            elif self.media_in_uso or self.reg_in_uso or os.path.isabs(u) \
                    or u.startswith("dvd://") or e_cd(u):
                titolo = self.righe_file(u, nome)[0]
            else:

                titolo = self.cfg.get("nomi_canali", {}).get(u) or nome
                ide = next((c[2] for c in self.canali if c[1] == u), None)
                p = self.programma_di(ide, nome)
                artista = p[2] if p else ""
            if not arte and os.path.isabs(u):
                arte = self.anteprime.get(u) or ""
            if not arte and os.path.isfile(copertina_url(u)):
                arte = copertina_url(u)
            if not arte and u in LOGHI:
                f = os.path.join(CACHE_LOGHI, hashlib.md5(LOGHI[u].encode()).hexdigest())
                if os.path.isfile(f) and os.path.getsize(f):
                    arte = f
        if not arte and self.icona_file:
            arte = self.icona_file
        chiave = (u, titolo)
        if chiave != getattr(self, "traccia_mpris", None):
            self.traccia_mpris = chiave
            self.num_mpris = getattr(self, "num_mpris", 0) + 1
        return {
            "stato": "Stopped" if fermo else ("Paused" if pausa else "Playing"),
            "titolo": titolo, "artista": artista, "album": album,
            "arte": os.path.abspath(arte) if arte else "",
            "durata": durata if not fermo else 0,
            "giro": self.num_mpris,
            "giro_ripeti": {"uno": "Track", "tutti": "Playlist"}.get(self.cfg.get("ripeti", ""), "None"),
            "shuffle": bool(self.cfg.get("shuffle")),
            "volume": self.volume.get() / 100.0,
        }

    def mpris_comando(self, cosa):
        fermo = bool(getattr(self, "fermato", None))
        try:
            in_pausa = bool(self.mpv.pause)
        except Exception:
            in_pausa = False
        if (cosa == "playpause"
                or (cosa == "play" and (fermo or in_pausa))
                or (cosa == "pause" and not fermo and not in_pausa)):
            self.pausa()

    def mpris_posizione(self, secondi):
        try:
            self.mpv.time_pos = max(0.0, secondi)
        except Exception:
            pass

    def mpris_shuffle(self, si):
        if bool(self.cfg.get("shuffle")) != si:
            self.shuffle()

    def mpris_alza(self):
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        except tk.TclError:
            pass

    def programma_di(self, ide, nome):
        if not self.epg:
            return None
        if not ide or ide not in self.epg:
            ide = self.epg_nomi.get(nome_piatto(nome))
        adesso = time.time()
        for inizio, fine, titolo in self.epg.get(ide or "", []):
            if inizio <= adesso < fine:
                return inizio, fine, titolo
            if inizio > adesso:
                break
        return None

    def colora_stato(self, colore):
        """La progress, il titolo del programma e la sfumatura dietro ai
        comandi del colore del pallino del canale."""
        self.linea.itemconfig(self.pieno_linea, fill=colore)
        self.et_titolo.config(fg=schiarisci(colore))
        self.et_canale.config(fg="#ffffff")
        self.et_orologio.config(fg=TESTO)
        self.et_formato.config(fg="#ffffff")
        self.colore_barra = colore
        self.icona_shuffle()
        self.icona_ripeti()
        self.disegna_fondo_barra()
        self.bordi_schermi()

    def disegna_fondo_barra(self):
        """La sfumatura: dal colore (al 22%) sotto la progress al fondo
        della barra in basso. I bottoni, che Tk non sa fare trasparenti,
        prendono il colore della sfumatura alla loro altezza."""
        c = self.fondo_barra
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        if not self.colore_barra or w < 2 or h < 2:
            for fig in self.barra.winfo_children():
                if fig is not c and fig is not self.linea:
                    try:
                        fig.config(bg=BARRA)
                    except tk.TclError:
                        pass
            return
        for y in range(h):
            c.create_line(0, y, w, y, fill=self.tinta_barra(y))
        self.disegna_fondo_linea()
        self.root.after_idle(self.vesti_tutti)

    def tinta_barra(self, y):
        """Il colore della sfumatura alla riga y della barra: dal colore
        del canale (al 22%) in cima al fondo della barra in basso."""
        h = max(1, self.fondo_barra.winfo_height())
        r1, g1, b1 = (int(self.colore_barra[k:k + 2], 16) for k in (1, 3, 5))
        r0, g0, b0 = (int(BARRA[k:k + 2], 16) for k in (1, 3, 5))
        t = 0.22 * max(0.0, 1.0 - y / float(h)) ** 1.6
        return "#%02x%02x%02x" % (int(r0 + (r1 - r0) * t), int(g0 + (g1 - g0) * t),
                                  int(b0 + (b1 - b0) * t))

    def vesti_tutti(self):
        for fig in self.barra.winfo_children():
            if isinstance(fig, tk.Button):
                self.vesti(fig)
            elif isinstance(fig, Cursore):
                fig.sfondo(self.tinta_barra if self.colore_barra else None)

    def aggiorna_linea(self):
        """Ogni mezzo secondo: la riga colorata e' quanto del programma in
        onda e' passato. Senza guida resta vuota."""
        parte = 0.0
        senza_ora = False
        if self.file_in_onda:
            try:
                pos, dur = float(self.mpv.time_pos or 0.0), float(self.mpv.duration or 0.0)
                senza_ora = dur > 0
                parte = max(0.0, min(1.0, pos / dur)) if dur > 0 else 0.0
                if dur > 0:
                    self.et_ora.config(text="  -  %s / %s" % (ore_min_sec(pos), ore_min_sec(dur)))
                    if self.mpv.path == self.percorso_mpv and not self.riprendi_da:
                        self.segna_posizione(pos, dur)
            except Exception:
                parte = 0.0
        elif self.cfg.get("canale") and not self.sfondo_su:
            p = self.programma()
            if p:


                inizio, fine, _t = p
                senza_ora = True
                parte = (time.time() - inizio) / max(1.0, fine - inizio)
                parte = max(0.0, min(1.0, parte))
            if (self.stato.cget("text") == self.riga_mostrata[0] and
                    self.riga() != self.riga_mostrata):
                self.scrivi_riga()


        if not senza_ora and self.nome_in_onda and not getattr(self, "fermato", None):
            t = time.localtime()
            parte = (t.tm_min * 60 + t.tm_sec) / 3600.0
        self.parte_linea = parte
        w = self.linea.winfo_width()
        self.linea.coords(self.pieno_linea, 0, 0, int(w * parte), self.alta_linea)
        ora = time.strftime("%a %d %b  %H:%M")
        if self.et_orologio.cget("text") != ora:
            self.et_orologio.config(text=ora)
        self.root.after(500, self.aggiorna_linea)

    def artista_del_file(self, f):
        """Artista del brano: cache RAM, indice su disco, tag solo al primo accesso."""
        if not f or not os.path.isabs(f) or os.path.splitext(f)[1][1:].lower() not in AUDIO:
            return ""
        try:
            st = os.stat(f)
            firma = [int(getattr(st, "st_mtime_ns", st.st_mtime * 1e9)), int(st.st_size)]
        except OSError:
            return ""
        # Il lock protegge solo i dizionari: mai tenerlo durante letture tag o disco.
        with self._xvb_artisti_lock:
            voce = self._xvb_artisti_file.get(f)
            if isinstance(voce, list) and len(voce) == 3 and voce[:2] == firma:
                return str(voce[2] or "")
            tag = self.tag_file.get(f)
        if not tag:
            tag = tag_di(f, self.tag_cache)
        artista = (tag[1] if tag and len(tag) > 1 else "").strip()
        if not artista:
            _titolo, artista, _album, _anno = dal_percorso(f, self.cfg.get("cartelle", []))
        artista = str(artista or "").strip()
        with self._xvb_artisti_lock:
            self.tag_file[f] = tag
            self._xvb_artisti_file[f] = firma + [artista]
            self._xvb_artisti_mem[artista.casefold()] = artista
            self._xvb_artisti_dirty += 1
            salva = self._xvb_artisti_dirty >= 8
        if salva:
            self.salva_cache_artisti()
        return artista

    def salva_cache_artisti(self):
        """Scrive l'indice in modo atomico, senza interrompere la riproduzione."""
        with self._xvb_artisti_lock:
            if not self._xvb_artisti_dirty:
                return
            try:
                os.makedirs(CACHE_ARTISTI, exist_ok=True)
                temporaneo = CACHE_ARTISTI_INDEX + ".tmp"
                with open(temporaneo, "w", encoding="utf-8") as h:
                    json.dump(self._xvb_artisti_file, h, ensure_ascii=False, separators=(",", ":"))
                os.replace(temporaneo, CACHE_ARTISTI_INDEX)
                self._xvb_artisti_dirty = 0
            except Exception:
                pass

    def percorso_foto_artista(self, artista):
        chiave = hashlib.sha256(artista.casefold().encode("utf-8")).hexdigest()
        return os.path.join(CACHE_ARTISTI, chiave + ".jpg")

    def scarica_foto_artista(self, artista):
        """Ricerca TheAudioDB in background; cache locale e cache negativa."""
        if not artista:
            return None
        os.makedirs(CACHE_ARTISTI, exist_ok=True)
        destinazione = self.percorso_foto_artista(artista)
        if os.path.isfile(destinazione) and os.path.getsize(destinazione) > 0:
            return destinazione
        mancata = destinazione + ".miss"
        try:
            if os.path.isfile(mancata) and time.time() - os.path.getmtime(mancata) < 7 * 86400:
                return None
        except OSError:
            pass
        try:
            url = "https://www.theaudiodb.com/api/v1/json/123/search.php?s=" + quote(artista)
            req = Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urlopen(req, timeout=12) as risposta:
                dati = json.loads(risposta.read(2 * 1024 * 1024).decode("utf-8", "replace"))
            artisti = dati.get("artists") or []
            trovato = None
            nome_cercato = " ".join(artista.casefold().split())
            for voce in artisti:
                nome = " ".join(str(voce.get("strArtist") or "").casefold().split())
                if nome == nome_cercato:
                    trovato = voce
                    break
            if trovato is None and len(artisti) == 1:
                trovato = artisti[0]
            if trovato is None:
                open(mancata, "w").close()
                return None
            immagine = next((trovato.get(k) for k in (
                "strArtistFanart", "strArtistFanart2", "strArtistFanart3",
                "strArtistFanart4", "strArtistThumb", "strArtistBanner"
            ) if trovato.get(k)), None)
            if not immagine:
                open(mancata, "w").close()
                return None
            req = Request(immagine, headers={"User-Agent": UA})
            with urlopen(req, timeout=18) as risposta:
                contenuto = risposta.read(15 * 1024 * 1024)
            if not contenuto:
                return None
            if HA_PIL:
                import io
                with Image.open(io.BytesIO(contenuto)) as originale:
                    im = originale.convert("RGB")
                    im.thumbnail((1600, 1000), Image.LANCZOS)
                    im.save(destinazione, "JPEG", quality=88, optimize=True)
            else:
                with open(destinazione, "wb") as h:
                    h.write(contenuto)
            try:
                os.remove(mancata)
            except OSError:
                pass
            return destinazione if os.path.isfile(destinazione) else None
        except Exception:
            return None

    def richiedi_foto_artisti(self, corrente=None):
        """Precarica le foto in un thread: nessuna scansione/tag sul thread Tk."""
        if not HA_PIL or not HA_TAG or not corrente or not os.path.isabs(corrente):
            return
        if getattr(self, "_xvb_artisti_in_corso", False):
            return
        self._xvb_artisti_in_corso = True

        def lavora():
            try:
                voci = list(getattr(self, "canali", []) or [])
                file_audio = [f for _n, f, _i in voci
                              if os.path.isabs(f) and os.path.splitext(f)[1][1:].lower() in AUDIO]
                if corrente in file_audio:
                    i = file_audio.index(corrente)
                    file_audio = file_audio[i:] + file_audio[:i]
                else:
                    file_audio.insert(0, corrente)
                visti = set()
                richieste = 0
                for f in file_audio:
                    artista = self.artista_del_file(f)
                    chiave = artista.casefold()
                    if not artista or chiave in visti:
                        continue
                    visti.add(chiave)
                    # Una foto gia' presente non genera rete, attese o ridisegni.
                    destinazione = self.percorso_foto_artista(artista)
                    if os.path.isfile(destinazione) and os.path.getsize(destinazione) > 0:
                        continue
                    foto = self.scarica_foto_artista(artista)
                    if foto:
                        def aggiorna(a=artista, p=foto):
                            corrente_attuale = self.file_in_onda or ""
                            if (corrente_attuale and os.path.isabs(corrente_attuale)
                                    and os.path.splitext(corrente_attuale)[1][1:].lower() in AUDIO
                                    and self.artista_del_file(corrente_attuale).casefold() == a.casefold()
                                    and os.path.isfile(p)):
                                self.sfondo_su = True
                                if self.sfondo_file != p:
                                    self.sfondo_file = p
                                    self.sfondo_misura = None
                                self.rifai_sfondo()
                        try:
                            self.root.after(0, aggiorna)
                        except Exception:
                            pass
                    richieste += 1
                    # Massimo 30 richieste per lotto; pausa conforme al servizio.
                    time.sleep(2.1)
                    if richieste and richieste % 30 == 0:
                        self.salva_cache_artisti()
                        time.sleep(5)
            except Exception:
                pass
            finally:
                self.salva_cache_artisti()
                self._xvb_artisti_in_corso = False

        threading.Thread(target=lavora, name="XVB-foto-artisti", daemon=True).start()

    def sfondo_giusto(self):
        """Foto artista per la musica; in attesa usa la cover del brano."""
        u = self.file_in_onda or self.cfg.get("canale") or ""
        if u and not getattr(self, "fermato", None) and self.nome_in_onda:
            if os.path.isabs(u) and os.path.splitext(u)[1][1:].lower() in AUDIO:
                artista = self.artista_del_file(u)
                if artista:
                    foto = self.percorso_foto_artista(artista)
                    if os.path.isfile(foto) and os.path.getsize(foto) > 0:
                        return foto
                cover = anteprima_di(u)
                if cover and os.path.isfile(cover):
                    return cover
            elif not os.path.isabs(u) and os.path.isfile(copertina_url(u)):
                return copertina_url(u)
        return os.path.join(QUI, "icone", "screen.png")

    def mostra_sfondo(self, si, dissolvi=False, attesa=300):
        """L'immagine di sfondo del lettore: si vede finche' non parte un
        canale, e torna quando non c'e' piu' niente che va. Con dissolvi (lo
        stop, l'avvio) resta nero per l'attesa (mezzo secondo) e poi lo sfondo
        compare piano piano, appena la finestra e' disegnata."""
        cambia = getattr(self, "sfondo_su", None) != si
        self.sfondo_su = si
        if cambia and hasattr(self, "timer_barra"):
            self.root.after_idle(self.posa_barra)
        if si:
            giusto = self.sfondo_giusto()
            if giusto != self.sfondo_file:
                self.sfondo_file, self.sfondo_misura = giusto, None
            if dissolvi:
                self.dissolvi_avvia(attesa)
                return
            self.dissolvi_ferma()
            # Non invalidare la misura se l'immagine e' gia' quella giusta:
            # evita ridisegni inutili e lampeggi durante la riproduzione.
            self.rifai_sfondo()
            for t in (300, 1500):
                self.root.after(t, lambda: self.sfondo_su and self.rifai_sfondo())
        else:
            self.dissolvi_ferma()
            self.togli_sfondo()

    def dissolvi_avvia(self, attesa=300):
        """Sfondo spento, dopo l'attesa (millisecondi) parte la dissolvenza."""
        self.dissolvi_ferma()
        self._dissolvi_att = True
        self.togli_sfondo()
        self._dissolvi_id = self.root.after(attesa, self._dissolvi_passo)

    def dissolvi_ferma(self):
        i = getattr(self, "_dissolvi_id", None)
        if i:
            try:
                self.root.after_cancel(i)
            except Exception:
                pass
        if getattr(self, "_dissolvi_att", False):
            self.sfondo_misura = None
        self._dissolvi_id = None
        self._dissolvi_att = False
        self._dissolvi_t0 = None
        self._dissolvi_w0 = None

    def _dissolvi_passo(self):
        self._dissolvi_id = None
        if not (self.sfondo_su and getattr(self, "_dissolvi_att", False)):
            return
        adesso = time.time()
        if getattr(self, "_dissolvi_t0", None) is None:
            # Si parte solo con la finestra disegnata (all'avvio mpv non ha
            # ancora la sua misura); dopo 5 secondi si mostra lo sfondo e basta.
            try:
                pronta = (int(self.mpv1.osd_width or 0) >= 2
                          and int(self.mpv1.osd_height or 0) >= 2)
            except Exception:
                pronta = False
            # La finestra deve essere davvero a schermo da un secondo:
            # il gestore delle finestre ci mette un po' a mostrarla.
            visto = getattr(self, "_finestra_vista", None)
            if visto is None and self.root.winfo_viewable():
                visto = self._finestra_vista = adesso
            pronta = pronta and visto is not None and adesso - visto >= 1.0
            if not getattr(self, "_dissolvi_w0", None):
                self._dissolvi_w0 = adesso
            if not pronta:
                if adesso - self._dissolvi_w0 > 5.0:
                    self.dissolvi_ferma()
                    self.rifai_sfondo()
                else:
                    self._dissolvi_id = self.root.after(50, self._dissolvi_passo)
                return
            self._dissolvi_t0 = adesso
        t = min(1.0, (adesso - self._dissolvi_t0) / 1.2)
        if t >= 1.0:
            self._dissolvi_att = False
            self._dissolvi_t0 = None
            self.sfondo_misura = None
            self.rifai_sfondo()
            return
        self._dissolvi_frame(t * t * (3 - 2 * t))
        self._dissolvi_id = self.root.after(50, self._dissolvi_passo)

    def _dissolvi_frame(self, k):
        """Un fotogramma della dissolvenza: lo sfondo mescolato al nero."""
        if not HA_PIL or not os.path.isfile(self.sfondo_file):
            return
        try:
            ow, oh = int(self.mpv1.osd_width or 0), int(self.mpv1.osd_height or 0)
        except Exception:
            return
        if ow < 2 or oh < 2:
            return
        try:
            chiave = (self.sfondo_file, ow, oh)
            tela = self._xvb_render_cache.get(chiave)
            if tela is None:
                im = Image.open(self.sfondo_file).convert("RGBA")
                kk = min(ow / float(im.width), oh / float(im.height))
                im = im.resize((max(1, int(im.width * kk)), max(1, int(im.height * kk))), Image.LANCZOS)
                tela = Image.new("RGBA", (ow, oh), (0, 0, 0, 255))
                tela.alpha_composite(im, ((ow - im.width) // 2, (oh - im.height) // 2))
                if len(self._xvb_render_cache) >= 8:
                    self._xvb_render_cache.pop(next(iter(self._xvb_render_cache)))
                self._xvb_render_cache[chiave] = tela
            quadro = Image.blend(Image.new("RGBA", (ow, oh), (0, 0, 0, 255)), tela,
                                 max(0.0, min(1.0, k)))
            if getattr(self, "ov_sfondo", None) is None:
                self.ov_sfondo = mpv.ImageOverlay(self.mpv1, OV_SFONDO, quadro, pos=(0, 0))
            else:
                self.ov_sfondo.update(quadro)
        except Exception:
            pass

    def rialza_sfondo(self):
        """Lo sfondo sopra a tutto nel riquadro del video, finestra di mpv
        compresa: prima sotto al fratello e poi in cima, cosi' Tk manda
        davvero l'ordine a X."""
        try:
            self.sfondo.lower(self.sotto)
            self.sfondo.lift()
        except tk.TclError:
            pass

    def rifai_sfondo_poi(self):
        if getattr(self, "sfondo_attesa", None):
            self.root.after_cancel(self.sfondo_attesa)
        self.sfondo_attesa = self.root.after(120, self._rifai_sfondo_ora)

    def _rifai_sfondo_ora(self):
        self.sfondo_attesa = None
        self.rifai_sfondo()

    def rifai_sfondo(self):
        """Lo sfondo (screen.png o la copertina scelta) disegnato da mpv,
        adattato al riquadro e con le proporzioni tenute, sotto alla barra:
        cosi' i comandi sono sempre quelli di mpv, anche da fermi."""
        if (not self.sfondo_su or not os.path.isfile(self.sfondo_file) or not HA_PIL
                or getattr(self, "_dissolvi_att", False)):
            return
        try:
            ow, oh = int(self.mpv1.osd_width or 0), int(self.mpv1.osd_height or 0)
        except Exception:
            return
        if ow < 2 or oh < 2:
            return
        if (ow, oh, self.sfondo_file) == self.sfondo_misura:
            return
        try:
            chiave = (self.sfondo_file, ow, oh)
            tela = self._xvb_render_cache.get(chiave)
            if tela is None:
                im = Image.open(self.sfondo_file).convert("RGBA")
                k = min(ow / float(im.width), oh / float(im.height))
                im = im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))), Image.LANCZOS)
                tela = Image.new("RGBA", (ow, oh), (0, 0, 0, 255))
                tela.alpha_composite(im, ((ow - im.width) // 2, (oh - im.height) // 2))
                # Cache limitata: evita crescita RAM con librerie molto grandi.
                if len(self._xvb_render_cache) >= 8:
                    self._xvb_render_cache.pop(next(iter(self._xvb_render_cache)))
                self._xvb_render_cache[chiave] = tela
            self.ov_sfondo = mpv.ImageOverlay(self.mpv1, OV_SFONDO, tela, pos=(0, 0))
            self.sfondo_misura = (ow, oh, self.sfondo_file)
        except Exception:
            pass

    def togli_sfondo(self):
        self.sfondo_misura = None
        if getattr(self, "ov_sfondo", None) is not None:
            try:
                self.mpv1.overlay_remove(OV_SFONDO)
            except Exception:
                pass
            self.ov_sfondo = None

    def mostra_carico(self, si):
        self.root.after(0, self._mostra_carico, si)

    def _mostra_carico(self, si):
        if si == self.carico:
            return
        self.carico = si
        if si:
            self._gira()
        else:
            self.et_carico.config(image=self.vuoto_carico)

    def _gira(self):
        if not self.carico or not self.giri_carico:
            return
        self.et_carico.config(image=self.giri_carico[self.angolo])
        self.angolo = (self.angolo + 1) % len(self.giri_carico)
        self.root.after(50, self._gira)

    def _buffer(self, _nome, fermo):
        """mpv dice che sta aspettando dati: si fa vedere il cerchietto."""
        if fermo:
            self.mostra_carico(True)

    def finito_male(self, ev):
        """Un file e' finito: se e' finito per un errore, il canale non
        risponde e si passa al prossimo."""
        motivo = ""
        try:
            d = ev.as_dict() if hasattr(ev, "as_dict") else {}
            if isinstance(d.get("event"), dict):
                d = dict(d, **d["event"])
            ragione = d.get("reason", getattr(getattr(ev, "data", None), "reason", ""))

            if isinstance(ragione, int):
                ragione = {0: "eof", 4: "error"}.get(ragione, str(ragione))
            motivo = str(ragione) + str(d.get("file_error", "") or d.get("error", "") or "")
        except Exception:
            pass
        if "error" in motivo.lower():
            self.root.after(0, self.canale_morto)
        elif "eof" in motivo.lower() and self.file_in_onda:
            self.root.after(0, self.prossimo_file)

    def disegna_fondo_linea(self):
        """Il fondo del canvas della progress: la traccia grigia in cima
        (2 px) e sotto la sfumatura, uguale a quella della barra."""
        c = self.linea
        c.delete("fondo")
        w, h = c.winfo_width(), c.winfo_height()

        traccia = CERCA
        if self.colore_barra:
            try:
                a_ = [int(self.colore_barra[k:k + 2], 16) for k in (1, 3, 5)]
                b_ = [int(BARRA[k:k + 2], 16) for k in (1, 3, 5)]
                traccia = "#%02x%02x%02x" % tuple(int(y + (x - y) * 0.35) for x, y in zip(a_, b_))
            except ValueError:
                pass
        c.create_rectangle(0, 0, w, 2, fill=traccia, outline="", tags="fondo")
        for y in range(2, h):
            c.create_line(0, y, w, y, tags="fondo",
                          fill=self.tinta_barra(y - 2) if self.colore_barra else BARRA)
        c.tag_lower("fondo")

    def segna_posizione(self, pos, dur):
        """Ci si ricorda dove si e' arrivati nel file (nel config, salvato
        ogni dieci secondi e alla chiusura); a meno di venti secondi dalla
        fine si dimentica, cosi' la volta dopo riparte da capo."""
        p = self.cfg.setdefault("posizioni", {})
        f = self.file_in_onda
        if dur - pos < 20:
            p.pop(f, None)
            self.cfg.get("durate", {}).pop(f, None)
        else:
            p[f] = round(pos, 1)
            self.cfg.setdefault("durate", {})[f] = round(dur, 1)
        if len(p) > 300:
            for k in list(p)[:len(p) - 300]:
                p.pop(k, None)
                self.cfg.get("durate", {}).pop(k, None)
        adesso = time.time()
        if adesso - getattr(self, "posizione_scritta", 0.0) > 10:
            self.posizione_scritta = adesso
            self.elenco.ridisegna_colonna()
            scrivi_config(self.cfg)

    def vai_a(self, ev):
        if not self.file_in_onda:
            return
        w = max(1, self.linea.winfo_width())
        k = max(0.0, min(1.0, ev.x / float(w)))
        try:
            self.mpv.command("seek", "%.2f" % (k * 100), "absolute-percent")
        except Exception:
            pass

    def avanza(self, secondi):
        """-10/+10 e Shift+freccia: avanti o indietro di dieci secondi.
        Nei file sempre; nei canali solo se mpv dice che il flusso si puo'
        scorrere (dentro al buffer o alla finestra HLS)."""
        try:
            if not self.file_in_onda and not self.mpv.seekable:
                return
            self.mpv.command("seek", str(secondi), "relative")
        except Exception:
            pass

    def prossimo_file(self):
        """Un file e' finito da solo: si passa al prossimo dell'elenco a
        sinistra (cartella, importati, registrazioni), come una playlist.
        All'ultimo ci si ferma."""
        f = self.file_in_onda
        i = next((k for k, c in enumerate(self.visti) if c[1] == f), -1)
        if i < 0:
            return
        ripeti = self.cfg.get("ripeti", "")
        if ripeti == "uno":
            self.vai_a_file(self.visti[i])
        elif i + 1 < len(self.visti):
            self.vai_a_file(self.visti[i + 1])
        elif ripeti == "tutti":
            self.vai_a_file(self.visti[0])

    def voci_capitoli(self):
        """Nel menu Playback, solo se quello che suona ha dei capitoli (DVD,
        mkv, mp4...): precedente, successivo e (fino a 99) la
        lista con l'ora d'inizio; quello in corso con l'asterisco."""
        try:
            capitoli = self.mpv.chapter_list or []
            adesso = self.mpv.chapter
        except Exception:
            return []
        if len(capitoli) < 2:
            return []
        voci = [(_("Previous chapter"), lambda: self.vai_capitolo(-1, relativo=True)),
                (_("Next chapter"), lambda: self.vai_capitolo(+1, relativo=True))]
        lista = []
        if len(capitoli) <= 99:
            for i, c in enumerate(capitoli):
                titolo = (c.get("title") or "").strip()
                if not titolo or titolo == "(unnamed)":
                    titolo = _("Chapter %d") % (i + 1)
                lista.append((("*  " if i == adesso else "   ") + titolo + "  \u00b7  "
                              + ore_min_sec(c.get("time") or 0),
                              lambda i=i: self.vai_capitolo(i)))
        if lista:

            voci += [None, (SCORRE, lista, 8)]
        return voci + [None]

    def vai_capitolo(self, n, relativo=False):
        try:
            if relativo:
                self.mpv.command("add", "chapter", n)
            else:
                self.mpv.chapter = n
        except Exception:
            pass

    def metti_ripeti(self, k):
        """Dal menu Playback: niente, il file, o tutta la lista."""
        self.cfg["ripeti"] = k
        scrivi_config(self.cfg)
        self.icona_ripeti()

    def gira_ripeti(self):
        """Il tasto della barra: niente -> tutta la lista -> il file -> niente."""
        dopo = {"": "tutti", "tutti": "uno", "uno": ""}
        self.metti_ripeti(dopo.get(self.cfg.get("ripeti", ""), ""))

    def icona_ripeti(self):
        """Spento: repeat.png bianca. Acceso, del colore del canale:
        repeat.png per tutta la lista, repeat_one.png per il file."""
        if not hasattr(self, "b_ripeti"):
            return
        k = self.cfg.get("ripeti", "")
        testo = _({"": "No repeat", "tutti": "Repeat all", "uno": "Repeat one"}[k])
        icona = "repeat_one" if (k == "uno" and "repeat_one" in self.icone_pil) else "repeat"
        if k and HA_PIL and icona in self.icone_pil:
            im = self.icone_pil[icona]
            r, g, b = ((int(self.colore_barra[j:j + 2], 16) for j in (1, 3, 5))
                       if self.colore_barra else (255, 255, 255))
            tinta = Image.new("RGBA", im.size, (r, g, b, 255))
            tinta.putalpha(im.getchannel("A"))
            self.icone_pil["ripeti_on"] = tinta
            self.icone["ripeti_on"] = ImageTk.PhotoImage(tinta)
            self.faccia(self.b_ripeti, "ripeti_on", testo)
        else:
            self.faccia(self.b_ripeti, "repeat", testo)

    def vai_a_file(self, c):
        nome, url, ide = c
        i = next((k for k, x in enumerate(self.visti) if x[1] == url), -1)
        if i >= 0 and self.elenco.exists(str(i)):
            self.elenco.selection_set(str(i))
            self.elenco.see(str(i))
        self.apri(nome, url, ide)

    def dvd_bloccato(self):
        """Un DVD per cui mpv ha dato errore: lo si dice e ci si ferma
        (un DVD non si salta al prossimo come un canale)."""
        if not (self.file_in_onda or "").startswith("dvd://"):
            return
        self.attesa_da = 0.0
        try:
            self.mpv.command("stop")
        except Exception:
            pass
        self.mostra_carico(False)
        self.mostra_sfondo(True)
        self.scrivi(_("This DVD can't be played"), errore=True)

    def canale_morto(self):
        """Il canale non va: avanti col prossimo, ma non all'infinito. Se
        li si e' provati tutti o dieci di fila, ci si ferma."""
        if (self.file_in_onda or "").startswith("dvd://"):
            self.dvd_bloccato()
            return
        self.attesa_da = 0.0
        self.mostra_carico(False)
        self.salti += 1
        if self.salti >= min(10, max(1, len(self.visti))):
            self.salti = 0
            self.scrivi(_("%s is not responding, nor are the next ones") % self.nome, errore=True)
            self.mostra_sfondo(True)
            return
        self.scrivi(_("%s is not responding: skipping to the next") % self.nome, errore=True)
        self.root.after(600, lambda: self.salta(+1))

    def ha_video(self):
        """Quello che suona ha un video vero (non solo una copertina). Se
        non si sa ancora, si risponde di si'."""
        try:
            tracce = self.mpv.track_list or []
        except Exception:
            return True
        if not tracce:
            return True
        return any(t.get("type") == "video" and not t.get("albumart") for t in tracce)

    def ha_immagine(self):
        """I file audio usano lo sfondo XVB (foto artista o cover), non la
        traccia album-art interna di mpv. I video mantengono il loro video."""
        k = self.file_in_onda or self.cfg.get("canale") or ""
        if os.path.isabs(k) and os.path.splitext(k)[1][1:].lower() in AUDIO:
            if not hasattr(self, "con_immagine"):
                self.con_immagine = {}
            self.con_immagine[k] = False
            return False
        if not hasattr(self, "con_immagine"):
            self.con_immagine = {}
        if k not in self.con_immagine:
            try:
                tracce = self.mpv.track_list or []
            except Exception:
                return True
            if not tracce:
                return True
            self.con_immagine[k] = any(t.get("type") == "video" for t in tracce)
        return self.con_immagine[k]

    def loop_ab_imposta_a(self):
        if not self.file_in_onda:
            self.scrivi(_("A-B loop works with media files"), errore=True)
            return
        try:
            pos = self.mpv.time_pos
            if pos is None:
                raise ValueError
            self.loop_ab_a = float(pos)
            self.loop_ab_b = None
            self.loop_ab_file = self.file_in_onda
            self.loop_ab_in_corso = False
            self.scrivi(_("A point set: %s") % fmtTime(self.loop_ab_a))
        except Exception:
            self.scrivi(_("cannot set A-B loop point"), errore=True)

    def loop_ab_imposta_b(self):
        if not self.file_in_onda or self.loop_ab_file != self.file_in_onda:
            self.scrivi(_("Set A before setting B"), errore=True)
            return
        try:
            pos = self.mpv.time_pos
            if pos is None or self.loop_ab_a is None:
                raise ValueError
            pos = float(pos)
            if pos <= self.loop_ab_a:
                self.scrivi(_("B point must be after A"), errore=True)
                return
            self.loop_ab_b = pos
            self.loop_ab_in_corso = True
            self.scrivi(_("A-B loop active: %s - %s") % (
                fmtTime(self.loop_ab_a), fmtTime(self.loop_ab_b)
            ))
        except Exception:
            self.scrivi(_("cannot set A-B loop point"), errore=True)

    def loop_ab_azzera(self):
        self.loop_ab_a = None
        self.loop_ab_b = None
        self.loop_ab_file = None
        self.loop_ab_in_corso = False
        self.scrivi(_("A-B loop cleared"))

    def _va(self, _nome, pos):
        # Il callback del tempo gestisce anche il loop A-B.
        if pos is not None:
            if (self.loop_ab_file == self.file_in_onda
                    and self.loop_ab_a is not None
                    and self.loop_ab_b is not None):
                try:
                    posizione = float(pos)
                    if posizione >= self.loop_ab_b:
                        if self.loop_ab_in_corso:
                            self.loop_ab_in_corso = False
                            self.mpv.command("seek", "%.3f" % self.loop_ab_a, "absolute")
                    else:
                        self.loop_ab_in_corso = True
                except Exception:
                    self.loop_ab_in_corso = False
            self.attesa_da, self.salti = 0.0, 0


            immagine = self.ha_immagine()
            if self.sfondo_su and immagine:
                self.root.after(0, self.mostra_sfondo, False)
            elif not immagine:
                locale_audio = (os.path.isabs(self.file_in_onda or "")
                                and os.path.splitext(self.file_in_onda or "")[1][1:].lower() in AUDIO)
                # Per la musica non ricalcolare lo sfondo a ogni aggiornamento
                # della progress: lo decide il brano avviato e lo aggiorna il
                # callback quando arriva la foto artista.
                if not self.sfondo_su or (not locale_audio and self.sfondo_file != self.sfondo_giusto()):
                    self.root.after(0, self.mostra_sfondo, True)
            if self.file_in_onda and getattr(self, "riprendi_da", 0.0):


                try:
                    if self.mpv.path == self.percorso_mpv:
                        da, self.riprendi_da = self.riprendi_da, 0.0
                        self.mpv.command("seek", "%.1f" % da, "absolute")
                except Exception:
                    pass
            if self.da_riallineare:


                if not self.suonato_da:
                    self.suonato_da = time.time()
                elif time.time() - self.suonato_da > 3.0:
                    self.da_riallineare = False
                    self.root.after(0, self.riallinea)
        if pos is not None and self.carico:
            try:
                if not self.mpv.paused_for_cache:
                    self.mostra_carico(False)
            except Exception:
                self.mostra_carico(False)


    def registra(self):
        """Il bottone rec: parte la registrazione del canale in onda, o si
        ferma se e' in corso. Il file va in ~/Videos/xvb, com'e' il flusso,
        senza ricodificare."""
        if self.registrando:
            self.ferma_registrazione()
            return


        f = self.file_in_onda or ""
        if f.startswith("dvd://"):


            if dvd_cifrato(f[len("dvd://"):]):
                self.scrivi(_("This DVD is protected and can't be recorded"), errore=True)
                return
        elif f and not re.match(r"https?://", f, re.I):
            return
        if not (f or self.cfg.get("canale")) or not self.nome_in_onda:
            return
        self.avvia_registrazione(self.nome_in_onda, 0.0)

    def avvia_registrazione(self, nome, fine):
        """Apre il file e dice a mpv di scriverci il flusso; con `fine`
        (secondi dal 1970) si ferma da sola a quell'ora."""
        try:
            os.makedirs(REGISTRAZIONI, exist_ok=True)


            p = None if getattr(self, "al_volo", None) else self.programma()
            titolo = (p[2] if p and p[2] else "") or nome
            pulisci = lambda t: re.sub(r"[\\/:*?\"<>|]+", "", t).strip() or "xvb"
            cartella = os.path.join(REGISTRAZIONI, pulisci(nome))
            os.makedirs(cartella, exist_ok=True)
            f = os.path.join(cartella, "%s - %s.%s" % (
                pulisci(titolo), time.strftime("%Y-%m-%d %H-%M"),
                "mkv" if self.ha_video() else "mka"))
            self.mpv.stream_record = f
        except Exception as e:
            self.scrivi(_("cannot record: %s") % e, errore=True)
            return
        self.registrando, self.fine_rec = f, fine
        self.inizio_rec = time.time()
        self.pulsa_rec()
        self.faccia(self.b_rec, "rec_stop", _("Stop"))
        self.mostra_rec()

    def ferma_registrazione(self):
        try:
            self.mpv.stream_record = ""
        except Exception:
            pass
        f = self.registrando
        self.registrando, self.fine_rec = "", 0.0
        self.faccia(self.b_rec, "rec", _("Record"))
        self.mostra_rec()
        self.scrivi(_("saved %s") % os.path.basename(f))
        self.rifai_albero()
        self.root.after(4000, self.scrivi_riga)

    def pulsa_rec(self):
        """Il pallino rosso accanto a REC si accende e si spegne piano,
        finche' si registra."""
        if not self.registrando:
            return
        self.fase_rec = (self.fase_rec + 1) % 12
        self.hud_disegna()
        self.root.after(100, self.pulsa_rec)

    def mostra_rec(self):
        """In fondo alla riga di stato, in rosso: REC, con l'ora a cui si
        ferma se e' pianificata. Vuoto se non si registra."""
        if not self.registrando:
            t = ""
        else:
            durata = int(time.time() - self.inizio_rec)
            ore, resto = divmod(durata, 3600)
            cronometro = "%d:%02d:%02d" % (ore, resto // 60, resto % 60) if ore else \
                         "%02d:%02d" % (resto // 60, resto % 60)
            t = "REC " + cronometro
            if self.fine_rec:
                t += "  " + _("until %s") % time.strftime("%H:%M", time.localtime(self.fine_rec))
        self.rec_testo = t
        self.hud_disegna()

    def pianifica(self):
        """Dal menu: ora di inizio e di fine per registrare il canale in
        onda. All'ora giusta l'app lo apre da sola e registra."""
        if not self.cfg.get("canale") or not self.nome_in_onda:
            self.scrivi(_("open the channel to record first"))
            return
        inizio = self.chiedi(_("Schedule"), _("Start (HH:MM):"), time.strftime("%H:%M"))
        if not inizio:
            return
        fine = self.chiedi(_("Schedule"), _("End (HH:MM):"))
        if not fine:
            return
        try:
            t0 = self.ora_di(inizio)
            t1 = self.ora_di(fine, dopo=t0)
        except ValueError:
            self.scrivi(_("time must be HH:MM"), errore=True)
            return
        self.piano = (self.nome_in_onda, self.cfg["canale"], self.ide_in_onda, t0, t1)
        self.scrivi(_("scheduled: %s from %s to %s") % (
            self.nome_in_onda, time.strftime("%H:%M", time.localtime(t0)),
            time.strftime("%H:%M", time.localtime(t1))))

    def ora_di(self, testo, dopo=None):
        """'21:30' -> il prossimo 21:30 in secondi dal 1970: oggi se deve
        ancora venire, se no domani. Con `dopo`, il primo dopo quell'ora."""
        h, m = (int(x) for x in testo.strip().split(":"))
        if not (0 <= h < 24 and 0 <= m < 60):
            raise ValueError(testo)
        adesso = time.localtime()
        t = time.mktime((adesso.tm_year, adesso.tm_mon, adesso.tm_mday, h, m, 0, 0, 0, -1))
        base = dopo if dopo is not None else time.time()
        while t <= base:
            t += 86400
        return t

    def annulla_piano(self):
        self.piano = None
        self.scrivi(_("schedule cancelled"))
        self.root.after(2500, self.scrivi_riga)

    def controlla_registrazione(self):
        """Ogni secondo: e' ora di partire col piano? e' ora di fermarsi?"""
        adesso = time.time()
        if self.piano and adesso >= self.piano[3]:
            nome, url, ide, _t0, t1 = self.piano
            self.piano = None
            if adesso < t1:
                if self.cfg.get("canale") != url:
                    self.apri(nome, url, ide)

                    self.root.after(5000, lambda: self.avvia_registrazione(nome, t1))
                else:
                    self.avvia_registrazione(nome, t1)
        if self.registrando and self.fine_rec and adesso >= self.fine_rec:
            self.ferma_registrazione()
        elif self.registrando:
            self.mostra_rec()
        self.root.after(1000, self.controlla_registrazione)

    def apri_registrazioni(self):
        try:
            os.makedirs(REGISTRAZIONI, exist_ok=True)
            subprocess.Popen(["xdg-open", REGISTRAZIONI])
        except Exception as e:
            self.scrivi(_("cannot open %s: %s") % (REGISTRAZIONI, e), errore=True)

    def apri_url(self):
        """Uno stream o un file in rete, al volo: parte come un canale,
        senza finire fra le playlist."""
        url = self.chiedi(_("Open URL..."), _("Address of the stream or file:"))
        if not url or not url.strip():
            return
        url = url.strip()


        nomi = self.cfg.setdefault("nomi_url", {})
        dove = tk.StringVar(value=self.cfg.get("ultima_categoria_url", "radio"))

        def corpo(dentro):
            tk.Label(dentro, text=_("Open URL..."), bg=PANNELLO, fg=TESTO, anchor="w",
                     font=(CARATTERE, 11, "bold", STILE_CARATTERE)).pack(fill="x")
            tk.Label(dentro, text=_("Save in:"), bg=PANNELLO, fg=GRIGIO, anchor="w"
                     ).pack(fill="x", pady=(10, 2))
            for k, n in CATEGORIE_URL + (("", "Temporary"),):
                tk.Radiobutton(dentro, text=_(n), variable=dove, value=k, anchor="w",
                               bg=PANNELLO, fg=TESTO, selectcolor=CERCA, bd=0,
                               activebackground=PANNELLO, activeforeground="#ffffff",
                               highlightthickness=0, cursor="hand2").pack(fill="x")
        f = Finestrella(self.root, _("Open URL..."), corpo, chiedi=_("Name:"),
                        valore=nomi.get(url) or titolo_url(url))
        self.root.wait_window(f)
        if f.risposta is None:
            return
        nome = f.risposta.strip() or titolo_url(url)
        cat = dove.get() or None
        self.cfg["ultima_categoria_url"] = dove.get()
        nomi[url] = nome
        self.metti_url(cat, url)
        scrivi_config(self.cfg)
        self.rifai_albero()

        self.mostra_media(("url", cat))
        self.suona_da_barra(("url", cat), url)

    def url_di(self, cat):
        """Gli indirizzi di una categoria: radio, musica, film (salvati) o
        None, i provvisori (url_aperti)."""
        if cat is None:
            return self.cfg.get("url_aperti", [])
        return self.cfg.get("url_salvati", {}).get(cat, [])

    def metti_url(self, cat, url):
        """L'indirizzo in cima alla sua categoria, tolto dalle altre."""
        for c, _n in CATEGORIE_URL:
            lista = self.cfg.setdefault("url_salvati", {}).setdefault(c, [])
            self.cfg["url_salvati"][c] = [u for u in lista if u != url]
        self.cfg["url_aperti"] = [u for u in self.cfg.get("url_aperti", []) if u != url]
        if cat is None:
            self.cfg["url_aperti"] = ([url] + self.cfg["url_aperti"])[:20]
        else:
            self.cfg["url_salvati"][cat] = [url] + self.cfg["url_salvati"][cat]

    def cerca_dvd(self, url, titolo):
        """In un thread: il DVD su TMDB; il poster in ~/.config/xvb/copertine
        (sul disco non si scrive). Poi la lista si ridisegna."""
        t, anno = titolo_anno(titolo + ".x")
        try:
            info = info_tmdb(TMDB_CHIAVE, t, anno, LINGUE_TMDB.get(LINGUA, "en-US"))
        except Exception:
            info = None
        if not info:
            return
        try:
            generi = generi_tmdb(TMDB_CHIAVE, LINGUE_TMDB.get(LINGUA, "en-US"), self.info)
            info["generi"] = [generi.get(g, "") for g in info["generi"] if generi.get(g)]
        except Exception:
            info["generi"] = []
        self.info[url] = info
        scrivi_json(FILE_INFO, self.info)
        if info.get("poster") and not os.path.isfile(copertina_url(url)):
            try:
                os.makedirs(COPERTINE_URL, exist_ok=True)
                scarica_in(info["poster"], copertina_url(url))
            except Exception:
                pass
        self.loghi_img.pop(url, None)
        if self.media_in_uso and self.media_in_uso[0] == "disco" and "dvd://" + self.media_in_uso[1] == url:
            self.root.after(0, self.filtra)
            self.root.after(0, self.scrivi_riga)

    def rinomina_dvd(self, url, nome):
        """Il titolo giusto di un DVD (l'etichetta del disco spesso non
        basta): si ricerca su TMDB con questo."""
        nuovo = self.chiedi(_("Rename..."), _("Name:"),
                            self.cfg.get("nomi_dvd", {}).get(url) or titolo_da_etichetta(nome))
        if not nuovo or not nuovo.strip():
            return
        self.cfg.setdefault("nomi_dvd", {})[url] = nuovo.strip()
        scrivi_config(self.cfg)
        self.info.pop(url, None)
        try:
            os.remove(copertina_url(url))
        except OSError:
            pass
        self.loghi_img.pop(url, None)
        threading.Thread(target=self.cerca_dvd, args=(url, nuovo.strip()), daemon=True).start()

    def nome_di_url(self, u):
        """Il nome dato all'indirizzo in Open URL, se no quello ricavato."""
        return self.cfg.get("nomi_url", {}).get(u) or titolo_url(u)

    def apri_url_da(self, url):
        """Un indirizzo degli URL aperti: parte, senza cuore (e' al volo)."""
        self.apri(self.nome_di_url(url), url)
        self.al_volo = url
        self.icona_preferito()


    # OpenSubtitles API login and Bearer download
    def cerca_sottotitoli_online(self):
        """Ricerca OpenSubtitles usando credenziali salvate nel config locale."""

        if not self.file_in_onda:
            self.scrivi(_("nothing is playing"))
            return

        # Credenziali OpenSubtitles in file separato.
        percorso_credenziali = os.path.expanduser(
            "~/.config/xvb/opensubtitles.json"
        )

        try:
            with open(percorso_credenziali, "r", encoding="utf-8") as f:
                credenziali = json.load(f)
            if not isinstance(credenziali, dict):
                raise ValueError("Formato credenziali non valido")
        except FileNotFoundError:
            credenziali = {}
        except Exception:
            self.scrivi(_("Unable to read OpenSubtitles credentials"), errore=True)
            return

        api_key = str(credenziali.get("api_key", "") or "").strip()
        username = str(credenziali.get("username", "") or "").strip()
        password = str(credenziali.get("password", "") or "")

        if not api_key:
            api_key = self.chiedi(
                _("OpenSubtitles API key"),
                _("Enter your OpenSubtitles API key:")
            )
            if not api_key or not api_key.strip():
                return
            api_key = api_key.strip()

        if not username:
            username = self.chiedi(
                _("OpenSubtitles username"),
                _("Enter your OpenSubtitles username:")
            )
            if not username or not username.strip():
                return
            username = username.strip()

        if not password:
            password = self.chiedi(
                _("OpenSubtitles password"),
                _("Enter your OpenSubtitles password:"),
                maschera=True
            )
            if not password:
                return

        credenziali = {
            "api_key": api_key,
            "username": username,
            "password": password
        }

        try:
            os.makedirs(os.path.dirname(percorso_credenziali), exist_ok=True)

            with open(percorso_credenziali, "w", encoding="utf-8") as f:
                json.dump(credenziali, f, indent=2)

            os.chmod(percorso_credenziali, 0o600)

            with open(percorso_credenziali, "r", encoding="utf-8") as f:
                verifica = json.load(f)

            if verifica != credenziali:
                raise ValueError("Verifica del salvataggio fallita")

        except Exception:
            self.scrivi(_("Unable to save OpenSubtitles credentials"), errore=True)
            return

        lingue = self.chiedi(
            _("Subtitle languages"),
            _("Language codes separated by commas (example: it,en):"),
            "it,en"
        )
        if not lingue or not lingue.strip():
            return

        media = str(self.file_in_onda)
        percorso = urlparse(media).path if "://" in media else media
        titolo = os.path.splitext(os.path.basename(percorso))[0]
        titolo = re.sub(r"[._]+", " ", titolo).strip()

        if not titolo:
            titolo = media

        codici = []
        for codice in lingue.split(","):
            codice = codice.strip().lower()
            if re.fullmatch(r"[a-z]{2,3}", codice) and codice not in codici:
                codici.append(codice)

        if not codici:
            self.scrivi(_("Invalid subtitle language codes"), errore=True)
            return

        self.scrivi(_("Searching online subtitles..."))

        threading.Thread(
            target=self._richiedi_sottotitoli_online,
            args=(
                api_key, username, password,
                titolo, ",".join(codici)
            ),
            daemon=True
        ).start()

    def _richiedi_sottotitoli_online(
        self, api_key, username, password, titolo, lingue
    ):
        """Login OpenSubtitles e ricerca dei sottotitoli."""

        from urllib.error import HTTPError

        try:
            intestazioni = {
                "Api-Key": api_key,
                "User-Agent": "XVB Player v" + VERSIONE,
                "Accept": "application/json",
                "Content-Type": "application/json"
            }

            corpo_login = json.dumps({
                "username": username,
                "password": password
            }).encode("utf-8")

            richiesta_login = Request(
                "https://api.opensubtitles.com/api/v1/login",
                data=corpo_login,
                headers=intestazioni,
                method="POST"
            )

            try:
                with urlopen(richiesta_login, timeout=30) as risposta:
                    login = json.loads(
                        risposta.read().decode("utf-8", "replace")
                    )
            except HTTPError as errore:
                dettaglio = errore.read().decode("utf-8", "replace")
                try:
                    dettaglio = json.loads(dettaglio).get(
                        "message", dettaglio
                    )
                except Exception:
                    pass
                raise RuntimeError(
                    "OpenSubtitles login HTTP %s: %s"
                    % (errore.code, dettaglio)
                )

            token = login.get("token")
            if not token:
                raise RuntimeError(
                    login.get("message") or "OpenSubtitles login failed"
                )

            host = str(
                login.get("base_url") or "api.opensubtitles.com"
            ).strip().rstrip("/")

            if not host.startswith("http://") and not host.startswith("https://"):
                host = "https://" + host

            base_url = host
            if not base_url.endswith("/api/v1"):
                base_url += "/api/v1"

            parametri = urlencode({
                "query": titolo,
                "languages": lingue,
                "order_by": "download_count",
                "order_direction": "desc"
            })

            url = base_url + "/subtitles?" + parametri

            richiesta = Request(url, headers={
                "Api-Key": api_key,
                "Authorization": "Bearer " + token,
                "User-Agent": "XVB Player v" + VERSIONE,
                "Accept": "application/json"
            })

            with urlopen(richiesta, timeout=30) as risposta:
                dati = json.loads(
                    risposta.read().decode("utf-8", "replace")
                )

            risultati = dati.get("data", [])

            self.root.after(
                0,
                lambda: self._mostra_risultati_sottotitoli(
                    api_key, token, base_url, risultati, titolo
                )
            )

        except HTTPError as errore:
            messaggio = "HTTP %s: %s" % (
                errore.code, errore.reason
            )
            self.root.after(
                0,
                lambda m=messaggio: self.scrivi(
                    _("Online subtitle search failed: %s") % m,
                    errore=True
                )
            )

        except Exception as errore:
            messaggio = str(errore)
            self.root.after(
                0,
                lambda m=messaggio: self.scrivi(
                    _("Online subtitle search failed: %s") % m,
                    errore=True
                )
            )
    def _mostra_risultati_sottotitoli(
        self, api_key, token, base_url, risultati, titolo
    ):
        """Mostra i risultati trovati."""
        if not risultati:
            self.scrivi(_("No online subtitles found"))
            return

        finestra = tk.Toplevel(self.root)
        finestra.title(_("Online subtitles"))
        finestra.configure(bg=PANNELLO)
        finestra.transient(self.root)
        finestra.geometry("760x390")

        tk.Label(
            finestra,
            text=_("Choose a subtitle to download"),
            bg=PANNELLO,
            fg=TESTO,
            font=(CARATTERE, 10, STILE_CARATTERE)
        ).pack(anchor="w", padx=14, pady=(12, 8))

        corpo = tk.Frame(finestra, bg=PANNELLO)
        corpo.pack(fill="both", expand=True, padx=12, pady=4)

        lista = tk.Listbox(
            corpo,
            bg=IN_ONDA,
            fg=TESTO,
            selectbackground=ACCENTO,
            selectforeground="#17121f",
            activestyle="none",
            relief="flat",
            highlightthickness=0,
            font=(CARATTERE, 9),
            exportselection=False
        )

        barra = ttk.Scrollbar(
            corpo, orient="vertical", command=lista.yview
        )
        lista.configure(yscrollcommand=barra.set)
        lista.pack(side="left", fill="both", expand=True)
        barra.pack(side="right", fill="y")

        validi = []

        for elemento in risultati:
            attributi = elemento.get("attributes") or {}
            files = attributi.get("files") or []

            file_id = next(
                (
                    f.get("file_id")
                    for f in files
                    if f.get("file_id") is not None
                ),
                None
            )
            if file_id is None:
                continue

            lingua = attributi.get("language", "?")
            release = attributi.get("release") or (
                files[0].get("file_name", "") if files else ""
            )
            uploader = (
                attributi.get("uploader") or {}
            ).get("name", "")
            downloads = attributi.get("download_count", 0)

            riga = "%s  |  %s  |  %s  |  %s %s" % (
                lingua,
                release or _("Unknown release"),
                uploader or _("Unknown uploader"),
                downloads,
                _("downloads")
            )

            lista.insert("end", riga)
            validi.append((file_id, lingua, release))

        if not validi:
            finestra.destroy()
            self.scrivi(_("No downloadable subtitles found"))
            return

        lista.selection_set(0)
        lista.activate(0)

        def scarica():
            selezione = lista.curselection()
            if not selezione:
                return

            file_id, lingua, release = validi[selezione[0]]
            pulsante.config(state="disabled")
            self.scrivi(_("Downloading subtitle..."))

            threading.Thread(
                target=self._scarica_sottotitolo_online,
                args=(
                    api_key, token, base_url, file_id,
                    lingua, release, titolo, finestra
                ),
                daemon=True
            ).start()

        fondo = tk.Frame(finestra, bg=PANNELLO)
        fondo.pack(fill="x", padx=14, pady=12)

        tk.Button(
            fondo,
            text=_("Cancel"),
            command=finestra.destroy,
            relief="flat",
            bg=IN_ONDA,
            fg=TESTO_ONDA,
            padx=14,
            pady=6
        ).pack(side="right", padx=(6, 0))

        pulsante = tk.Button(
            fondo,
            text=_("Download and load"),
            command=scarica,
            relief="flat",
            bg=ACCENTO,
            fg="#17121f",
            padx=14,
            pady=6
        )
        pulsante.pack(side="right")

        lista.bind(
            "<Double-Button-1>",
            lambda _e: scarica()
        )

    def _scarica_sottotitolo_online(
        self, api_key, token, base_url, file_id,
        lingua, release, titolo, finestra
    ):
        """Scarica il sottotitolo con API Key e token Bearer."""

        from urllib.error import HTTPError

        try:
            corpo = json.dumps({
                "file_id": file_id
            }).encode("utf-8")

            richiesta = Request(
                base_url.rstrip("/") + "/download",
                data=corpo,
                headers={
                    "Api-Key": api_key,
                    "Authorization": "Bearer " + token,
                    "User-Agent": "XVB Player v" + VERSIONE,
                    "Accept": "application/json",
                    "Content-Type": "application/json"
                },
                method="POST"
            )

            try:
                with urlopen(richiesta, timeout=30) as risposta:
                    dati = json.loads(
                        risposta.read().decode("utf-8", "replace")
                    )
            except HTTPError as errore:
                dettaglio = errore.read().decode("utf-8", "replace")
                try:
                    dettaglio = json.loads(dettaglio).get(
                        "message", dettaglio
                    )
                except Exception:
                    pass
                raise RuntimeError(
                    "OpenSubtitles download HTTP %s: %s"
                    % (errore.code, dettaglio)
                )

            link = dati.get("link")
            if not link:
                raise RuntimeError(
                    dati.get("message") or "Download URL missing"
                )

            richiesta_file = Request(link, headers={
                "User-Agent": "XVB Player v" + VERSIONE
            })

            with urlopen(richiesta_file, timeout=45) as risposta_file:
                contenuto = risposta_file.read()

            nome_file = os.path.basename(
                dati.get("file_name") or "subtitle.srt"
            )

            estensioni = (
                ".srt", ".ass", ".ssa", ".sub", ".vtt", ".ttml"
            )

            if zipfile.is_zipfile(io.BytesIO(contenuto)):
                with zipfile.ZipFile(io.BytesIO(contenuto)) as archivio:
                    candidati = [
                        n for n in archivio.namelist()
                        if not n.endswith("/")
                        and os.path.splitext(n)[1].lower() in estensioni
                    ]

                    if not candidati:
                        raise RuntimeError(
                            "Archive contains no supported subtitle"
                        )

                    candidati.sort(
                        key=lambda n: ("forced" in n.lower(), len(n))
                    )
                    selezionato = archivio.read(candidati[0])
                    nome_file = os.path.basename(candidati[0])
            else:
                selezionato = contenuto

            estensione = os.path.splitext(nome_file)[1].lower()
            if estensione not in estensioni:
                estensione = ".srt"

            cartella = os.path.join(
                CASA, ".cache", "xvb", "subtitles"
            )
            os.makedirs(cartella, exist_ok=True)

            base = re.sub(
                r"[^\w.-]+", "_", titolo, flags=re.UNICODE
            ).strip("._") or "subtitle"

            codice = re.sub(
                r"[^a-zA-Z0-9_-]", "", str(lingua)
            ) or "sub"

            destinazione = os.path.join(
                cartella,
                "%s.%s%s" % (base, codice, estensione)
            )

            if os.path.exists(destinazione):
                destinazione = os.path.join(
                    cartella,
                    "%s.%s.%d%s" % (
                        base, codice, int(time.time()), estensione
                    )
                )

            with open(destinazione, "wb") as f:
                f.write(selezionato)

            self.root.after(
                0,
                lambda p=destinazione, w=finestra:
                    self._attiva_sottotitolo_online(p, w)
            )

        except Exception as errore:
            messaggio = str(errore)
            self.root.after(
                0,
                lambda m=messaggio, w=finestra:
                    self._errore_download_sottotitolo(m, w)
            )
    def _attiva_sottotitolo_online(self, percorso, finestra):
        try:
            self.mpv.command("sub-add", percorso, "select")
            self.scrivi(
                _("subtitles: %s") % os.path.basename(percorso)
            )
            self.root.after(2500, self.scrivi_riga)

            try:
                finestra.destroy()
            except Exception:
                pass

        except Exception as errore:
            self.scrivi(
                _("cannot read %s: %s")
                % (os.path.basename(percorso), errore),
                errore=True
            )

    def _errore_download_sottotitolo(self, messaggio, finestra):
        self.scrivi(
            _("Subtitle download failed: %s") % messaggio,
            errore=True
        )
        try:
            for widget in finestra.winfo_children():
                for figlio in widget.winfo_children():
                    if (
                        isinstance(figlio, tk.Button)
                        and figlio.cget("text") == _("Download and load")
                    ):
                        figlio.config(state="normal")
        except Exception:
            pass

    def carica_sottotitoli(self):
        """Un file di sottotitoli scelto a mano per il video che va."""
        f = self.file_in_onda or ""
        da = os.path.dirname(f) if os.path.isabs(f) else self.cfg.get("ultima_cartella", CASA)
        s = sfoglia(self.root, _("Load subtitles..."), da, tipi=SOTTOTITOLI)
        if not s:
            return
        try:
            self.mpv.command("sub-add", s, "select")
        except Exception as e:
            self.scrivi(_("cannot read %s: %s") % (os.path.basename(s), e), errore=True)
            return
        self.scrivi(_("subtitles: %s") % os.path.basename(s))
        self.root.after(2500, self.scrivi_riga)

    def ritardo_sottotitoli(self, di):
        try:
            v = round(float(self.mpv["sub-delay"] or 0.0) + di, 1)
            self.mpv["sub-delay"] = v
        except Exception:
            self.scrivi(_("nothing is playing"))
            return
        self.scrivi(_("subtitle delay %+.1fs") % v)
        self.root.after(2500, self.scrivi_riga)

    def scala_sottotitoli(self, di):
        try:
            v = min(3.0, max(0.5, round(float(self.mpv["sub-scale"] or 1.0) + di, 1)))
            self.mpv["sub-scale"] = v
        except Exception:
            self.scrivi(_("nothing is playing"))
            return
        self.scrivi(_("subtitle size %d%%") % round(v * 100))
        self.root.after(2500, self.scrivi_riga)

    def azzera_sottotitoli(self):
        try:
            self.mpv["sub-delay"] = 0.0
            self.mpv["sub-scale"] = 1.0
        except Exception:
            pass
        self.scrivi(_("subtitle delay %+.1fs") % 0.0)
        self.root.after(2500, self.scrivi_riga)

    def ritardo_audio(self, di):
        """Sposta l'audio di `di` secondi rispetto al video e se lo segna
        per questo canale, cosi' la volta dopo riparte gia' giusto."""
        url = self.cfg.get("canale")
        if not url:
            return
        try:
            v = round(float(self.mpv.audio_delay or 0.0) + di, 1)
            self.mpv.audio_delay = v
        except Exception:
            return
        r = self.cfg.setdefault("ritardo", {})
        if abs(v) < 0.05:
            r.pop(url, None)
        else:
            r[url] = v
        scrivi_config(self.cfg)
        self.scrivi(_("audio delay %+.1fs") % v)
        self.root.after(2500, self.scrivi_riga)

    def azzera_ritardo(self):
        try:
            v = float(self.mpv.audio_delay or 0.0)
        except Exception:
            v = 0.0
        if v:
            self.ritardo_audio(-v)

    def riallinea(self):
        try:
            self.mpv.command("seek", "0", "relative+exact")
        except Exception:
            pass

    def scrivi(self, t, errore=False):
        """Un messaggio nella riga di stato: titolo e orario si tolgono. Gli
        errori hanno davanti l'icona icone/error.png (se c'e')."""
        if (getattr(self, "fermato", None) is None
                and (getattr(self, "file_in_onda", None)
                     or (self.cfg.get("canale") and getattr(self, "nome_in_onda", None)))):
            self.scrivi_riga()
            return
        def fai():
            img = self.icona_errore if errore and self.icona_errore else ""
            self.ic_categoria.config(image="")
            self.et_categoria.config(text="")
            self.stato.config(text=(" " + t) if img else t, image=(img if getattr(self, "categoria_in_stato", False) else ""), compound="left")
            self.et_titolo.config(text="")
            self.et_ora.config(text="")
            self.et_seguente.config(text="")
            self.et_titolo_seguente.config(text="")
            self.et_inizio.config(text="")
            self.et_ora_seguente.config(text="")
            self.fr_cat.config(image="")
            self.ic_cat.config(image="")
            self.et_cat.config(text="")
            self.disegna_tipo("", TESTO)
        self.root.after(0, fai)

    def scrivi_riga(self):
        """Mostra le informazioni EPG dei canali nella barra di stato."""
        self.riga_mostrata = self.riga()
        nome, titolo, ora = self.riga_mostrata

        prossimo = ""
        ora_seg = ""
        if self.epg and not self.file_in_onda:
            ide = self.ide_in_onda
            if not ide or ide not in self.epg:
                ide = self.epg_nomi.get(nome_piatto(self.nome_in_onda))
            adesso = time.time()
            for _inizio, _fine, _titolo in self.epg.get(ide or "", []):
                if _inizio > adesso:
                    prossimo = _titolo or ""
                    ora_seg = time.strftime("%H:%M", time.localtime(_inizio))
                    break

        etichetta_seguente = {
            "it": "a seguire: ", "en": "Next: ", "es": "a continuación: ",
            "fr": "À suivre : ", "de": "als Nächstes: ",
            "pt": "a seguir: ", "ru": "Далее: ",
        }.get(LINGUA, "Next: ")

        nome_lista, img_lista = "", ""
        if getattr(self, "fermato", None) is None and (
                self.file_in_onda or self.nome_in_onda):
            d_l = None
            try:
                if self.file_in_onda:
                    v = getattr(self, "_vista_onda", None)
                    iid_l = self.iid_media.get(v[1]) if v and v[0] == "media" else None
                else:
                    d_l = getattr(self, "_lista_in_onda", None)
                    iid_l = self.iid_di.get(d_l) if d_l else None
                if iid_l is not None and self.el_liste.exists(iid_l):
                    nome_lista = (self.el_liste.item(iid_l, "text") or "").strip()
                    img_lista = self.el_liste.item(iid_l, "image") or ""
            except Exception:
                pass
            if self.file_in_onda:
                f_ = self.file_in_onda
                est_ = os.path.splitext(
                    urlparse(f_).path if e_media_url(f_) else f_)[1][1:].lower()
                img_lista = self.icona_sorgente(
                    "music" if (est_ in AUDIO or e_cd(f_)) else "player") or img_lista
            if not nome_lista:
                if self.file_in_onda and os.path.isabs(self.file_in_onda):
                    nome_lista = os.path.basename(os.path.dirname(self.file_in_onda))
                elif d_l:
                    nome_lista = nome_vista(d_l)

        categoria = ""
        colore_categoria = TESTO
        url_in_onda = self.cfg.get("canale")
        lista_in_onda = getattr(self, "_lista_in_onda", None)
        if (url_in_onda and lista_in_onda and not self.file_in_onda
                and self.nome_in_onda and getattr(self, "fermato", None) is None):
            mappe = getattr(self, "_mappa_categorie_m3u", {})
            colori = getattr(self, "_colore_categoria_m3u", {})
            mappa = mappe.get(lista_in_onda)
            if mappa is None:
                mappa = categorie_m3u(lista_in_onda, solo_cache=True)
                mappe[lista_in_onda] = mappa
            categoria = mappa.get(url_in_onda, "")
            if categoria:
                colore_categoria = colori.get((lista_in_onda, categoria), TESTO)

        inizio_txt, sep_t, resto = (ora or "").partition("\t")
        if not sep_t:
            inizio_txt, resto = "", (ora or "")
        ora_fine = ("    " + resto) if sep_t else (("  " + resto) if resto else "")

        def fai():
            self.ic_categoria.config(image=img_lista)
            icona_cat = (
                (self.icona_segnalibro(colore_categoria)
                 if os.path.basename(os.path.dirname(lista_in_onda or "")) == PREFERITI
                 else self.icona_cartella(colore_categoria, False))
                if categoria else "")
            self.fr_cat.config(
                image=getattr(self, "frecce", {}).get("chiuso", "") if categoria else "")
            self.ic_cat.config(image=icona_cat)
            self.et_cat.config(
                text=(categoria + "    ") if categoria else "", fg=TESTO)
            self.et_categoria.config(
                text=(nome_lista + (" " if categoria else "    ")) if nome_lista else "", fg=TESTO)
            self.stato.config(text=nome, image="", compound="left", fg=TESTO)
            self.et_inizio.config(
                text=("  " + inizio_txt) if inizio_txt else "", fg=TESTO)
            self.et_titolo.config(
                text=(" " + titolo) if titolo else "",
                fg=schiarisci(self.colore_barra) if self.colore_barra else TESTO)
            self.et_ora.config(text=ora_fine, fg=TESTO)
            self.et_seguente.config(
                text=("  " + etichetta_seguente.rstrip(": ")) if prossimo else "",
                fg=TESTO)
            self.et_ora_seguente.config(
                text=(" " + ora_seg) if (prossimo and ora_seg) else "", fg=TESTO)
            self.et_titolo_seguente.config(
                text=(" " + prossimo) if prossimo else "",
                fg=schiarisci(self.colore_barra) if self.colore_barra else TESTO)
        self.root.after(0, fai)


    def ascolta(self):
        """In un thread: le XVB aperte dopo mandano qui i loro file, uno
        per riga (nessun file = porta solo la finestra davanti)."""
        while True:
            try:
                conn, _a = self.porta.accept()
                with conn:
                    dati = b""
                    while True:
                        pezzo = conn.recv(65536)
                        if not pezzo:
                            break
                        dati += pezzo
                dove = [x for x in dati.decode("utf-8", "replace").split("\n") if x]
                self.root.after(0, lambda d=dove: self.apri_da_fuori(d) if d else (
                    self.root.deiconify(), self.root.lift()))
            except Exception:
                return

    def via_i_provvisori(self):
        """I provvisori (Temporary) valgono solo finche' XVB e' aperto: via
        loro, i loro nomi e le loro copertine."""
        for u in self.cfg.get("url_aperti", []):
            self.cfg.get("nomi_url", {}).pop(u, None)
            try:
                os.remove(copertina_url(u))
            except OSError:
                pass
        self.cfg["url_aperti"] = []

    def chiudi(self):
        self.salva_cache_artisti()
        self.via_i_provvisori()
        try:
            if self.porta is not None:
                self.porta.close()
                os.remove(PORTA)
        except Exception:
            pass
        scrivi_config(self.cfg)
        try:
            if self.mpv2 is not None:
                self.mpv2.terminate()
        except Exception:
            pass
        try:
            self.mpv1.terminate()
        except Exception:
            pass
        self.root.destroy()


def corto(t, n):
    """Un testo lungo accorciato con i puntini, per i menu."""
    return t if len(t) <= n else t[:n - 1] + "\u2026"


def porta_unica():
    """Una XVB sola: se ce n'e' gia' una aperta le si passano i file e
    questa finisce qui (None); se no si apre la porta per quelle dopo."""
    try:
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.settimeout(2)
        c.connect(PORTA)
        c.sendall("\n".join(os.path.abspath(a) for a in sys.argv[1:]).encode("utf-8"))
        c.close()
        return None
    except OSError:
        pass
    try:
        os.makedirs(os.path.dirname(PORTA), exist_ok=True)
        os.remove(PORTA)
    except OSError:
        pass
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(PORTA)
        s.listen(4)
        return s
    except OSError:
        return False


if __name__ == "__main__":
    configura_logging()
    porta = porta_unica()
    if porta is not None:
        argomenti = sys.argv[1:]


        lista = None
        if len(argomenti) == 1 and os.path.isfile(argomenti[0]) and not e_media(argomenti[0]):
            lista, argomenti = argomenti[0], []
        TV(lista, da_aprire=argomenti, porta=porta or None).root.mainloop()
