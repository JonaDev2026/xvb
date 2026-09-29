#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""XVB - lettore IPTV con l'adattamento della qualita' che a mpv manca.

mpv sceglie una variante e ci resta: se la banda cala si ferma invece di
scendere di qualita'. Qui l'adattamento glielo facciamo da fuori - si
guarda quanto buffer ha davanti e si ricarica il canale sulla variante
piu' leggera quando va in affanno, su quella piu' pesante quando sta
tranquillo. E' quello che fa hls.js nel browser, scritto da noi.

A sinistra i canali, ognuno col suo pallino colorato, a destra le liste,
sotto i comandi e il nome del canale che sta andando. Le liste sono i
file nella cartella playlists/ accanto all'app, qualunque nome abbiano.
Il nome e' quello del file, l'immagine accanto e' icone/default.png. L'app si ricorda le liste,
l'ultima usata, l'ultimo canale e il volume.

Uso:  python3 xvb.py [lista.m3u]
"""
import gzip
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, simpledialog, ttk
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

import mpv

try:                            # per i loghi: ridimensiona e legge i jpg
    from PIL import Image, ImageTk
    HA_PIL = True
except ImportError:             # senza, si va di PhotoImage: solo png
    HA_PIL = False

UA = "Mozilla/5.0 (X11; Linux x86_64)"
IN_AFFANNO = 3.0            # secondi di buffer sotto i quali si scende
TRANQUILLO = 15.0           # buffer pieno: si puo' risalire
QUANTO_TRANQUILLO = 20.0    # per quanto deve stare pieno prima di salire
DOPO_UNA_DISCESA = 120.0    # dopo una discesa non si risale subito
IN_CALO = -0.30             # buffer che perde piu' di 0.3 s al secondo: cala
QUANTO_IN_CALO = 4          # per quanti secondi di fila, prima di scendere

QUI = os.path.dirname(os.path.abspath(__file__))
CASA = os.path.expanduser("~")
CONFIG = os.path.join(CASA, ".config", "xvb", "xvb.json")
CONFIG_VECCHIO = os.path.join(CASA, ".config", "tv", "tv.json")   # quando si chiamava TV
# le liste che si caricano da sole: accanto all'app, e nella cartella
# dell'utente (che e' quella che vale quando l'app e' installata nel sistema)
CARTELLE_LISTE = (os.path.join(QUI, "playlists"),
                  os.path.join(CASA, ".local", "share", "xvb", "playlists"))
# la vecchia lists/ si legge ancora, se c'e', ma non si crea piu'
CARTELLE_VECCHIE = tuple(c for c in (os.path.join(QUI, "lists"),
                                     os.path.join(CASA, ".local", "share", "xvb", "lists"))
                         if os.path.isdir(c))
PUNTO = 24                      # il riquadro del pallino, nell'elenco
# i colori dei pallini: quelli delle etichette del Mac
# ------------------------------------------------------------- le lingue
LINGUE = (("en", "English"), ("it", "Italiano"), ("es", "Español"), ("fr", "Français"))
LINGUA = "en"
TESTI = {
    "it": {
        "Record": "Registra",
        "Stop": "Ferma",
        "Record now": "Registra ora",
        "Stop recording": "Ferma registrazione",
        "Schedule...": "Pianifica...",
        "Cancel schedule": "Annulla pianificazione",
        "Open recordings folder": "Apri cartella registrazioni",
        "cannot record: %s": "non riesco a registrare: %s",
        "saved %s": "salvato %s",
        "REC until %s": "REC fino alle %s",
        "open the channel to record first": "prima apri il canale da registrare",
        "Schedule": "Pianifica",
        "Start (HH:MM):": "Inizio (HH:MM):",
        "End (HH:MM):": "Fine (HH:MM):",
        "time must be HH:MM": "l'ora va scritta HH:MM",
        "scheduled: %s from %s to %s": "pianificato: %s dalle %s alle %s",
        "schedule cancelled": "pianificazione annullata",
        "Delay +100 ms": "Ritardo +100 ms", "Delay -100 ms": "Ritardo -100 ms", "Reset delay": "Azzera ritardo",
        "audio delay %+.1fs": "ritardo audio %+.1fs",
        "guide loaded: %d channels, %d programmes": "guida caricata: %d canali, %d programmi",
        "not in the guide": "non in guida",
        "Search channels": "Cerca un canale",
        "Playlists": "Playlist",
        "ready": "pronto",
        "unnamed": "senza nome",
        "single quality": "una sola qualita'",
        "missing icons: %s": "icone mancanti: %s",
        "no playlist: put one in playlists/": "nessuna playlist: mettine una in playlists/",
        "loading %s...": "carico %s...",
        "cannot read %s: %s": "non riesco a leggere %s: %s",
        "%s - %d channels": "%s - %d canali",
        "Choose a playlist": "Scegli una playlist",
        "All files": "Tutti i file",
        "%s does not look like an m3u playlist": "%s non sembra una playlist m3u",
        "select the playlist to remove first": "prima seleziona la playlist da togliere",
        "this one lives in playlists/: remove it by moving the file":
            "questa sta in playlists/: si toglie spostando il file",
        "%s removed from favorites": "%s tolto dai preferiti",
        "%s added to favorites": "%s nei preferiti",
        "cannot write favorites: %s": "non riesco a scrivere i preferiti: %s",
        "looking for %s qualities...": "cerco le qualita' di %s...",
        "TV guide": "Guida TV",
        "URL or file of the guide (XMLTV, .gz is fine):":
            "Url o file della guida (XMLTV, va bene anche .gz):",
        "loading the guide...": "carico la guida...",
        "guide not loaded: %s": "guida non caricata: %s",
        "no TV guide set": "nessuna guida impostata",
        "%s is not responding, nor are the next ones": "%s non risponde, e nemmeno i successivi",
        "%s is not responding: skipping to the next": "%s non risponde: passo al prossimo",
        "cannot open %s: %s": "non riesco ad aprire %s: %s",
        "Pause": "Pausa", "Resume": "Riprendi", "Favorite": "Preferito",
        "Fullscreen": "Schermo intero", "quality": "qualita'", "Mute": "Muto",
        "Unmute": "Audio", "Switch": "Scambia",
        "Open folder": "Apri cartella", "Reload": "Ricarica", "Add URL": "Aggiungi url",
        "Remove": "Togli", "View": "Vista", "Hide channels": "Nascondi canali",
        "Show channels": "Mostra canali", "Hide playlists": "Nascondi playlist",
        "Show playlists": "Mostra playlist", "Language": "Lingua",
    },
    "es": {
        "Record": "Grabar",
        "Stop": "Parar",
        "Record now": "Grabar ahora",
        "Stop recording": "Parar grabación",
        "Schedule...": "Programar...",
        "Cancel schedule": "Cancelar programación",
        "Open recordings folder": "Abrir carpeta de grabaciones",
        "cannot record: %s": "no puedo grabar: %s",
        "saved %s": "guardado %s",
        "REC until %s": "REC hasta las %s",
        "open the channel to record first": "abre primero el canal a grabar",
        "Schedule": "Programar",
        "Start (HH:MM):": "Inicio (HH:MM):",
        "End (HH:MM):": "Fin (HH:MM):",
        "time must be HH:MM": "la hora debe ser HH:MM",
        "scheduled: %s from %s to %s": "programado: %s de %s a %s",
        "schedule cancelled": "programación cancelada",
        "Delay +100 ms": "Retardo +100 ms", "Delay -100 ms": "Retardo -100 ms", "Reset delay": "Quitar retardo",
        "audio delay %+.1fs": "retardo de audio %+.1fs",
        "guide loaded: %d channels, %d programmes": "guía cargada: %d canales, %d programas",
        "not in the guide": "no está en la guía",
        "Search channels": "Buscar canal",
        "Playlists": "Listas",
        "ready": "listo",
        "unnamed": "sin nombre",
        "single quality": "una sola calidad",
        "missing icons: %s": "faltan iconos: %s",
        "no playlist: put one in playlists/": "ninguna lista: pon una en playlists/",
        "loading %s...": "cargando %s...",
        "cannot read %s: %s": "no puedo leer %s: %s",
        "%s - %d channels": "%s - %d canales",
        "Choose a playlist": "Elige una lista",
        "All files": "Todos los archivos",
        "%s does not look like an m3u playlist": "%s no parece una lista m3u",
        "select the playlist to remove first": "primero selecciona la lista a quitar",
        "this one lives in playlists/: remove it by moving the file":
            "esta está en playlists/: se quita moviendo el archivo",
        "%s removed from favorites": "%s quitado de favoritos",
        "%s added to favorites": "%s añadido a favoritos",
        "cannot write favorites: %s": "no puedo guardar los favoritos: %s",
        "looking for %s qualities...": "buscando las calidades de %s...",
        "TV guide": "Guía TV",
        "URL or file of the guide (XMLTV, .gz is fine):":
            "URL o archivo de la guía (XMLTV, también .gz):",
        "loading the guide...": "cargando la guía...",
        "guide not loaded: %s": "guía no cargada: %s",
        "no TV guide set": "ninguna guía configurada",
        "%s is not responding, nor are the next ones": "%s no responde, ni los siguientes",
        "%s is not responding: skipping to the next": "%s no responde: paso al siguiente",
        "cannot open %s: %s": "no puedo abrir %s: %s",
        "Pause": "Pausa", "Resume": "Reanudar", "Favorite": "Favorito",
        "Fullscreen": "Pantalla completa", "quality": "calidad", "Mute": "Silencio",
        "Unmute": "Sonido", "Switch": "Cambiar",
        "Open folder": "Abrir carpeta", "Reload": "Recargar", "Add URL": "Añadir URL",
        "Remove": "Quitar", "View": "Vista", "Hide channels": "Ocultar canales",
        "Show channels": "Mostrar canales", "Hide playlists": "Ocultar listas",
        "Show playlists": "Mostrar listas", "Language": "Idioma",
    },
    "fr": {
        "Record": "Enregistrer",
        "Stop": "Arrêter",
        "Record now": "Enregistrer maintenant",
        "Stop recording": "Arrêter l'enregistrement",
        "Schedule...": "Programmer...",
        "Cancel schedule": "Annuler la programmation",
        "Open recordings folder": "Ouvrir le dossier des enregistrements",
        "cannot record: %s": "impossible d'enregistrer : %s",
        "saved %s": "enregistré %s",
        "REC until %s": "REC jusqu'à %s",
        "open the channel to record first": "ouvre d'abord la chaîne à enregistrer",
        "Schedule": "Programmer",
        "Start (HH:MM):": "Début (HH:MM) :",
        "End (HH:MM):": "Fin (HH:MM) :",
        "time must be HH:MM": "l'heure doit être HH:MM",
        "scheduled: %s from %s to %s": "programmé : %s de %s à %s",
        "schedule cancelled": "programmation annulée",
        "Delay +100 ms": "Retard +100 ms", "Delay -100 ms": "Retard -100 ms", "Reset delay": "Remettre à zéro",
        "audio delay %+.1fs": "retard audio %+.1fs",
        "guide loaded: %d channels, %d programmes": "guide chargé : %d chaînes, %d programmes",
        "not in the guide": "absent du guide",
        "Search channels": "Rechercher une chaîne",
        "Playlists": "Listes",
        "ready": "prêt",
        "unnamed": "sans nom",
        "single quality": "une seule qualité",
        "missing icons: %s": "icônes manquantes : %s",
        "no playlist: put one in playlists/": "aucune liste : mets-en une dans playlists/",
        "loading %s...": "chargement de %s...",
        "cannot read %s: %s": "impossible de lire %s : %s",
        "%s - %d channels": "%s - %d chaînes",
        "Choose a playlist": "Choisir une liste",
        "All files": "Tous les fichiers",
        "%s does not look like an m3u playlist": "%s ne ressemble pas à une liste m3u",
        "select the playlist to remove first": "sélectionne d'abord la liste à retirer",
        "this one lives in playlists/: remove it by moving the file":
            "celle-ci est dans playlists/ : on la retire en déplaçant le fichier",
        "%s removed from favorites": "%s retiré des favoris",
        "%s added to favorites": "%s ajouté aux favoris",
        "cannot write favorites: %s": "impossible d'écrire les favoris : %s",
        "looking for %s qualities...": "recherche des qualités de %s...",
        "TV guide": "Guide TV",
        "URL or file of the guide (XMLTV, .gz is fine):":
            "URL ou fichier du guide (XMLTV, .gz accepté) :",
        "loading the guide...": "chargement du guide...",
        "guide not loaded: %s": "guide non chargé : %s",
        "no TV guide set": "aucun guide défini",
        "%s is not responding, nor are the next ones": "%s ne répond pas, ni les suivantes",
        "%s is not responding: skipping to the next": "%s ne répond pas : passage à la suivante",
        "cannot open %s: %s": "impossible d'ouvrir %s : %s",
        "Pause": "Pause", "Resume": "Reprendre", "Favorite": "Favori",
        "Fullscreen": "Plein écran", "quality": "qualité", "Mute": "Muet",
        "Unmute": "Son", "Switch": "Basculer",
        "Open folder": "Ouvrir le dossier", "Reload": "Recharger", "Add URL": "Ajouter une URL",
        "Remove": "Retirer", "View": "Affichage", "Hide channels": "Masquer les chaînes",
        "Show channels": "Afficher les chaînes", "Hide playlists": "Masquer les listes",
        "Show playlists": "Afficher les listes", "Language": "Langue",
    },
}


def _(testo):
    """Il testo nella lingua scelta; se manca, resta l'inglese."""
    return TESTI.get(LINGUA, {}).get(testo, testo)


PREFERITI = "favorite"     # la cartella dei preferiti, in playlists/
ROSSO = "#ff453a"          # il suo colore: solo suo, le altre cartelle no
PALLINI = ("#ff5257", "#ff9f0a", "#ffd60a", "#30d158", "#0a84ff",
           "#bf5af2", "#ff375f", "#64d2ff", "#8e8e93")
# i colori: Material dark. Il fondo e' quasi nero, i pannelli una
# superficie appena piu' chiara, e tutto quello che "si alza" e' bianco
# messo sopra in trasparenza: i bottoni all'8 per cento, la riga
# selezionata al 14. Tk la trasparenza vera non ce l'ha, quindi sono i
# grigi che verrebbero fuori da quel bianco sopra al pannello.
FONDO = "#121212"          # lo sfondo
PANNELLO = "#1e1e1e"       # la superficie dei pannelli
BARRA = "#121212"          # la barra dei comandi
STATO = "#0c0c0c"          # la riga col nome del canale, piu' scura
TASTO = "#2c2c2c"          # bianco 8% sopra al pannello: bottoni, casella
SCELTO = "#3a3a3a"         # bianco 14%: la riga selezionata
TESTO = "#e0e0e0"          # bianco 87%: il testo
GRIGIO = "#9e9e9e"         # bianco 60%: il testo secondario
ACCENTO = "#bb86fc"        # il colore primario, per volume e caricamento
IN_ONDA = "#3b2f4f"        # il primario in trasparenza: cio' che sta andando
TESTO_ONDA = "#e9ddff"     # il testo sopra al viola


# ------------------------------------------------------- quello che ricorda
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


# ------------------------------------------------------------- le liste
def nome_di(dove):
    """Il nome da mostrare: quello del file, senza cartella ne' estensione."""
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


def liste_in(cartella):
    """Le liste dentro a una cartella, qualunque nome abbiano, con o senza
    estensione: basta che dentro siano m3u."""
    fuori = []
    try:
        for n in sorted(os.listdir(cartella)):
            f = os.path.join(cartella, n)
            if not n.startswith(".") and os.path.isfile(f) and e_una_lista(f):
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


def leggi_lista(f):
    """I canali di una lista sul disco: (nome, indirizzo, tvg-id o None).
    Il tvg-id serve a trovare il canale nella guida."""
    righe = open(f, encoding="utf-8", errors="ignore").read().splitlines()
    fuori, nome, ide = [], None, None
    for riga in righe:
        riga = riga.strip()
        if riga.startswith("#EXTINF"):
            nome = riga.split(",", 1)[-1].strip() or _("unnamed")
            m = re.search(r'tvg-id="([^"]*)"', riga)
            ide = m.group(1).strip() if m else None
        elif riga and not riga.startswith("#"):
            fuori.append((nome or riga, riga, ide or None))
            nome, ide = None, None
    return fuori


def immagine_lista(dove):
    """L'immagine accanto al nome della lista: icone/default.png.
    Torna il percorso, o None."""
    for est in (".png", ".jpg", ".jpeg", ".gif"):
        f = os.path.join(QUI, "icone", "default" + est)
        if os.path.isfile(f):
            return f
    return None


def carica_logo(f, larga, alta):
    """Un'immagine come immagine Tk, dentro un riquadro fisso, centrata.
    None se non si legge."""
    try:
        if HA_PIL:
            im = Image.open(f).convert("RGBA")
            k = min(larga / float(max(1, im.width)),
                    alta / float(max(1, im.height)))
            im = im.resize((max(1, int(im.width * k)),
                            max(1, int(im.height * k))), Image.LANCZOS)
            box = Image.new("RGBA", (larga, alta), (0, 0, 0, 0))
            box.paste(im, ((larga - im.width) // 2, (alta - im.height) // 2))
            return ImageTk.PhotoImage(box)
        im = tk.PhotoImage(file=f)          # solo png e gif, senza PIL
        k = max(1, int(round(max(im.height() / float(alta),
                                 im.width() / float(larga)))))
        return im.subsample(k, k) if k > 1 else im
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
        return "2160p 4K", "#bf5af2"
    if alta >= 1080:
        return "1080p Full HD", "#30d158"
    if alta >= 720:
        return "720p HD", "#0a84ff"
    if alta >= 480:
        return "%dp SD" % alta, "#ff9f0a"
    if alta > 0:
        return "%dp" % alta, "#ff5257"
    return "%.0f kbit/s" % (banda / 1000.0), GRIGIO


def cartellina(colore, aperta=False, lato=PUNTO):
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
    # il corpo con la linguetta in alto a sinistra
    d.line([(3 * u, 19 * u), (3 * u, 5 * u), (10 * u, 5 * u), (12 * u, 7.5 * u),
            (21 * u, 7.5 * u), (21 * u, 19 * u), (3 * u, 19 * u)],
           fill=colore, width=sp, joint="curve")
    if aperta:
        # la falda davanti, aperta: parte piu' in basso e si allarga
        d.line([(3 * u, 19 * u), (6 * u, 11.5 * u), (23 * u, 11.5 * u),
                (20 * u, 19 * u)], fill=colore, width=sp, joint="curve")
    return ImageTk.PhotoImage(im.resize((lato, lato), Image.LANCZOS))


def colore_di(nome):
    """Il colore del pallino di un canale: sempre lo stesso per lo stesso
    nome, cosi' non cambia a ogni apertura."""
    n = int(hashlib.md5(nome.encode("utf-8")).hexdigest()[:8], 16)
    return PALLINI[n % len(PALLINI)]


COLORI_CARTELLE = tuple(c for c in PALLINI if c not in ("#ff5257", "#ff375f"))


def colore_cartella(nome, posto):
    """Il colore di una cartella: rosso solo per i preferiti; le altre,
    nell'ordine in cui stanno, prendono i colori dei pallini (rossi
    esclusi) uno dopo l'altro, e si ricomincia solo finito il giro."""
    if nome == PREFERITI:
        return ROSSO
    return COLORI_CARTELLE[posto % len(COLORI_CARTELLE)]


def file_preferiti():
    """La lista dei preferiti: playlists/favorite/favorite.m3u."""
    return os.path.join(CARTELLE_LISTE[0], PREFERITI, PREFERITI + ".m3u")


def leggi_preferiti():
    f = file_preferiti()
    if not os.path.isfile(f):
        return []
    try:
        return leggi_lista(f)
    except Exception:
        return []


def scrivi_preferiti(canali):
    f = file_preferiti()
    os.makedirs(os.path.dirname(f), exist_ok=True)
    with open(f, "w", encoding="utf-8") as o:
        o.write("#EXTM3U\n")
        for nome, url, ide in canali:
            o.write("#EXTINF:-1%s,%s\n%s\n" % (
                ' tvg-id="%s"' % ide if ide else "", nome, url))


CACHE_EPG = os.path.join(CASA, ".cache", "xvb", "epg")
CACHE_VARIANTE = os.path.join(CASA, ".cache", "xvb")
REGISTRAZIONI = os.path.join(CASA, "Videos", "xvb")
EPG_VECCHIA = 6 * 3600          # dopo sei ore la guida si riscarica


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
    r = Request(dove, headers={"User-Agent": UA})
    dati = urlopen(r, timeout=60).read()
    with open(f, "wb") as o:
        o.write(dati)
    return f


def nome_epg(dove):
    """Come chiamare la guida nella barra: se e' un url, il dominio; se e'
    un file, il suo nome."""
    if re.match(r"https?://", dove, re.I):
        dominio = urlparse(dove).netloc.lower()
        return dominio[4:] if dominio.startswith("www.") else dominio
    return os.path.basename(dove.rstrip("/")) or dove


def nome_piatto(nome):
    """Un nome di canale ridotto all'osso per confrontarlo con la guida:
    minuscolo, senza spazi, punteggiatura e senza HD/FHD/4K in coda."""
    n = re.sub(r"[^a-z0-9]+", "", (nome or "").lower())
    return re.sub(r"(fhd|uhd|hd|4k|sd)$", "", n)


def leggi_epg(f):
    """La guida XMLTV, anche compressa (gz): torna (nomi, programmi).
    nomi: nome mostrato (minuscolo) -> id del canale.
    programmi: id -> [(inizio, fine, titolo)] ordinati, solo quelli da
    un'ora fa in poi, per non tenere in memoria giorni interi."""
    with open(f, "rb") as h:
        testa = h.read(2)
    apri = gzip.open if testa == b"\x1f\x8b" else open
    nomi, programmi = {}, {}
    da = time.time() - 3600
    with apri(f, "rb") as h:
        for _ev, el in ET.iterparse(h):
            if el.tag == "channel":
                ide = el.get("id") or ""
                for n in el.findall("display-name"):
                    if n.text:
                        nomi.setdefault(nome_piatto(n.text), ide)
                el.clear()
            elif el.tag == "programme":
                inizio, fine = ora_xmltv(el.get("start")), ora_xmltv(el.get("stop"))
                if inizio is not None and fine is not None and fine >= da:
                    t = el.find("title")
                    titolo = (t.text or "").strip() if t is not None else ""
                    programmi.setdefault(el.get("channel") or "", []).append(
                        (inizio, fine, titolo))
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
    try:
        r = Request(url, headers={"User-Agent": UA})
        testo = urlopen(r, timeout=10).read().decode("utf-8", "ignore")
    except Exception:
        return []
    if "#EXT-X-STREAM-INF" not in testo:
        return []
    gruppi, media, testa = {}, {}, ["#EXTM3U"]
    for riga in testo.splitlines():
        riga = riga.strip()
        if riga.startswith("#EXT-X-MEDIA") and "TYPE=AUDIO" in riga:
            g = re.search(r'GROUP-ID="([^"]*)"', riga)
            u = re.search(r'URI="([^"]*)"', riga)
            if g and u:
                assoluto = urljoin(url, u.group(1))
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
            # e' una variante con il video? In tanti master la piu' leggera
            # e' solo audio: a sceglierla si sente e lo schermo resta nero
            c = re.search(r'CODECS="([^"]*)"', riga)
            codec = c.group(1).lower() if c else ""
            video = True
            if codec and "RESOLUTION=" not in riga:
                # dichiara i codec e non c'e' niente di video: e' solo audio
                video = any(k in codec for k in ("avc", "hvc", "hev", "av01",
                                                 "vp0", "dvh", "mp4v"))
        elif riga and not riga.startswith("#"):
            if video:
                indirizzo = urljoin(url, riga)
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

    def __init__(self, dove, cambia, alta=36, vuota="Search channels"):
        tk.Canvas.__init__(self, dove, height=alta, bg=PANNELLO,
                           highlightthickness=0, bd=0)
        self.alta, self.cambia, self.vuota = alta, cambia, vuota
        self.testo = tk.StringVar()
        self.casella = tk.Entry(self, textvariable=self.testo, bg=TASTO,
                                fg=TESTO, insertbackground=ACCENTO,
                                relief="flat", bd=0, highlightthickness=0,
                                font=("TkDefaultFont", 10))
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
        # la pillola: due tondi e un rettangolo in mezzo
        for x in (0, w - 2 * r):
            self.create_oval(x, 0, x + 2 * r, h, fill=TASTO, outline="",
                             tags="pillola")
        self.create_rectangle(r, 0, w - r, h, fill=TASTO, outline="",
                              tags="pillola")
        # la lente
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

    def disegna(self):
        self.delete("all")
        y, x0, x1 = self.alta // 2, 10, self.larga - 10
        x = x0 + (x1 - x0) * self.valore / 100.0
        # stile YouTube: traccia grigia, la parte piena bianca, niente pomello
        self.create_line(x0, y, x1, y, width=4, fill="#4a4a4a", capstyle="round")
        if x > x0:
            self.create_line(x0, y, x, y, width=4, fill="#ffffff", capstyle="round")


class TV(object):
    def __init__(self, lista=None):
        self.cfg = leggi_config()
        global LINGUA
        if self.cfg.get("lingua") in dict(LINGUE):
            LINGUA = self.cfg["lingua"]
        # la cartella delle liste ci deve essere sempre: se manca la si
        # fa, cosi' uno sa subito dove mettere le cose
        for c in CARTELLE_LISTE:
            try:
                os.makedirs(c, exist_ok=True)
            except Exception:
                pass
        self.canali, self.visti, self.liste = [], [], []
        self.varianti, self.quale = [], -1
        self.manuale = True
        self.storia, self.in_calo = [], 0
        self.nome, self.nome_in_onda, self.precedente = "", "", None
        self.epg_nomi, self.epg, self.ide_in_onda = {}, {}, None
        self.nascosti = {}                  # pannello -> nascosto dal menu View
        self.riga_mostrata = ("", "", "")
        self.da_riallineare, self.suonato_da = False, 0.0
        self.registrando, self.fine_rec = "", 0.0    # file in corso, quando fermarsi
        self.piano = None                            # (nome, url, ide, inizio, fine)
        self.ultima_discesa = 0.0
        self.tranquillo_da = time.time()
        self.affanni = []

        self.root = tk.Tk(className="xvb")
        self.root.title("XVB")
        # una casella vuota della misura dei pallini, per le liste senza
        # immagine: cosi' i nomi restano in colonna
        self.vuoto = tk.PhotoImage(width=PUNTO, height=PUNTO)
        self.pallini = {c: pallino(c) for c in PALLINI}
        self.cartelle = {}                  # (colore, aperta) -> icona
        self.iid_di = {}
        self.cartella_di = {}               # riga della cartella -> colore
        self.root.geometry("1280x720")
        self.root.configure(bg=FONDO)
        f = os.path.join(QUI, "icon.png")
        if os.path.isfile(f):
            try:
                self.icona = tk.PhotoImage(file=f)
                self.root.iconphoto(True, self.icona)
            except Exception:
                pass

        # --- a sinistra: i canali
        self.icone = {}
        for n in ("play", "pausa", "switch", "playlist", "favorite_on", "favorite_off",
                  "rec", "rec_stop",
                  "volume", "volume_high",
                  "volume_low", "volume_off", "muto", "pieno", "prima", "dopo",
                  "aperto", "chiuso", "setting"):
            p = os.path.join(QUI, "icone", n + ".png")
            if os.path.isfile(p):
                try:
                    self.icone[n] = tk.PhotoImage(file=p)
                except Exception:
                    # png che Tk non digerisce (16 bit, palette...): PIL
                    try:
                        self.icone[n] = ImageTk.PhotoImage(
                            Image.open(p).convert("RGBA"))
                    except Exception:
                        pass
        # quelle che mancano o non si aprono si dicono, cosi' si vede subito
        self.icone_mancanti = [n for n in (
            "play", "pausa", "switch", "playlist", "favorite_on", "favorite_off",
            "rec", "rec_stop", "volume",
            "volume_high", "volume_low",
            "volume_off", "muto", "pieno", "prima", "dopo", "aperto", "chiuso",
            "setting") if n not in self.icone]
        if self.icone_mancanti:
            sys.stderr.write("missing icons in %s: %s\n" % (
                os.path.join(QUI, "icone"), ", ".join(self.icone_mancanti)))

        # --- in alto: la barra dei menu, disegnata noi (quella di Tk su
        # Linux prende il tema di sistema, non il nostro)
        self.cima = tk.Frame(self.root, bg=BARRA, height=28)
        self.cima.pack(side="top", fill="x")
        self.cima.pack_propagate(False)
        self.menu_cima = {}
        self.menu_aperto = tk.Menu(self.root, tearoff=0, bg=TASTO, fg=TESTO,
                                   activebackground=SCELTO,
                                   activeforeground="#ffffff", bd=0,
                                   relief="flat")
        self.menu("Playlists", lambda: [
            (_("Open folder"), self.apri_cartella_liste),
            (_("Reload"), lambda: self.rifai_liste(scegli=self.cfg.get("lista")))])
        self.menu("TV guide", lambda: [
            (_("Add URL"), self.chiedi_epg),
            (_("Reload"), self.ricarica_epg if self.cfg.get("epg") else None),
            (_("Remove"), self.togli_epg if self.cfg.get("epg") else None)])
        self.menu("View", lambda: [
            (_("Fullscreen"), self.schermo_intero),
            (_("Show channels") if self.nascosti.get(self.sinistra) else _("Hide channels"),
             lambda: self.nascondi(self.sinistra)),
            (_("Show playlists") if self.nascosti.get(self.destra) else _("Hide playlists"),
             lambda: self.nascondi(self.destra))])
        self.menu("Record", lambda: [
            (_("Stop recording") if self.registrando else _("Record now"), self.registra),
            (_("Schedule..."), self.pianifica),
            (_("Cancel schedule"), self.annulla_piano if self.piano else None),
            (_("Open recordings folder"), self.apri_registrazioni)])
        self.menu("Audio", lambda: [
            (_("Delay +100 ms"), lambda: self.ritardo_audio(+0.1)),
            (_("Delay -100 ms"), lambda: self.ritardo_audio(-0.1)),
            (_("Reset delay"), self.azzera_ritardo)])
        self.menu("Language", lambda: [
            (("*  " if codice == LINGUA else "   ") + nome,
             lambda c=codice: self.cambia_lingua(c)) for codice, nome in LINGUE])

        self.sinistra = tk.Frame(self.root, bg=PANNELLO, width=220)
        self.sinistra.pack(side="left", fill="y")
        self.sinistra.pack_propagate(False)
        self.cerca = Ricerca(self.sinistra, self.filtra, vuota=_("Search channels"))
        self.cerca.pack(fill="x", padx=10, pady=10)
        st = ttk.Style(self.root)
        st.theme_use("clam")
        st.configure("Canali.Treeview", background=PANNELLO,
                     fieldbackground=PANNELLO, foreground=TESTO,
                     rowheight=PUNTO + 8, borderwidth=0, relief="flat")
        # senza la cornicetta chiara che il tema disegna intorno
        st.layout("Canali.Treeview",
                  [("Canali.Treeview.treearea", {"sticky": "nswe"})])
        # i colori delle righe (il verde del canale in onda) valgono solo
        # se la mappa dello stile non li copre: e' un difetto noto di Tk
        def mappa(che):
            return [e for e in st.map("Treeview", query_opt=che)
                    if e[:2] != ("!disabled", "!selected")]
        st.map("Canali.Treeview",
               background=[("selected", SCELTO)] + mappa("background"),
               foreground=[("selected", "#ffffff")] + mappa("foreground"))
        self.elenco = ttk.Treeview(self.sinistra, show="tree",
                                   style="Canali.Treeview", selectmode="browse")
        self.elenco.tag_configure("onda", background=IN_ONDA,
                                  foreground=TESTO_ONDA)
        self.elenco.bind("<<TreeviewSelect>>", lambda e: self.non_sul_verde(
            self.elenco, "onda"))

        self.elenco.column("#0", width=200, stretch=True)
        self.elenco.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.elenco.bind("<Double-Button-1>", lambda e: self.parti())
        self.elenco.bind("<Return>", lambda e: self.parti())

        # --- a destra: le liste
        self.destra = tk.Frame(self.root, bg=PANNELLO, width=220)
        self.destra.pack(side="right", fill="y")
        self.destra.pack_propagate(False)
        self.et_liste = tk.Label(self.destra, text=_("Playlists"), bg=PANNELLO,
                                 fg=GRIGIO, anchor="w")
        self.et_liste.pack(fill="x", padx=10, pady=(10, 4))
        # le frecce delle cartelle sono icone/aperto.png e icone/chiuso.png
        stile_liste = "Canali.Treeview"
        if "aperto" in self.icone and "chiuso" in self.icone:
            try:
                # piu' piccole delle altre icone e un po' trasparenti
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
                             rowheight=PUNTO + 8, borderwidth=0, relief="flat")
                st.map("Liste.Treeview",
                       background=[("selected", SCELTO)] + mappa("background"),
                       foreground=[("selected", "#ffffff")] + mappa("foreground"))
                stile_liste = "Liste.Treeview"
            except tk.TclError:
                pass
        self.el_liste = ttk.Treeview(self.destra, show="tree",
                                     style=stile_liste,
                                     selectmode="browse")
        self.el_liste.column("#0", width=200, stretch=True)
        self.el_liste.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.el_liste.bind("<<TreeviewSelect>>", lambda e: (
            self.scegli_lista(), self.non_sul_verde(self.el_liste, "usata")))
        self.el_liste.tag_configure("usata", background=IN_ONDA,
                                    foreground=TESTO_ONDA)
        # il verde vince sul blu: se si seleziona proprio la riga in uso,
        # la selezione si toglie, cosi' resta verde e non si accende
        self.img_liste = {}                 # percorso -> immagine
        self.el_liste.bind("<<TreeviewOpen>>",
                           lambda e: self.apri_chiudi_cartella(True))
        self.el_liste.bind("<<TreeviewClose>>",
                           lambda e: self.apri_chiudi_cartella(False))

        # --- sotto: stato e comandi
        self.riga_stato = tk.Frame(self.root, bg=STATO, height=42)
        self.riga_stato.pack(side="bottom", fill="x")
        self.riga_stato.pack_propagate(False)   # alta il doppio
        self.stato = tk.Label(self.riga_stato, text=_("ready"), anchor="w",
                              bg=STATO, fg="#ffffff")
        self.stato.pack(side="left", padx=(8, 0), fill="y")
        # il titolo del programma, del colore del canale, e il suo orario
        self.et_titolo = tk.Label(self.riga_stato, text="", bg=STATO, fg=TESTO)
        self.et_titolo.pack(side="left", fill="y")
        self.et_ora = tk.Label(self.riga_stato, text="", bg=STATO, fg="#ffffff")
        self.et_ora.pack(side="left", fill="y")
        self.et_rec = tk.Label(self.riga_stato, text="", bg=STATO, fg="#ff453a")
        self.et_rec.pack(side="left", fill="y")
        self.barra = tk.Frame(self.root, bg=BARRA, height=40)
        self.barra.pack(side="bottom", fill="x")
        self.barra.pack_propagate(False)        # altezza fissa, comandi al centro
        # la riga in cima alla barra, stile YouTube: traccia grigia e, in
        # bianco, quanto manca alla fine del programma (dalla guida)
        self.linea = tk.Canvas(self.barra, height=2, bg="#3a3a3a",
                               highlightthickness=0, bd=0)
        self.linea.pack(side="top", fill="x")
        self.pieno_linea = self.linea.create_rectangle(0, 0, 0, 2,
                                                       fill="#ffffff", outline="")
        self.root.after(500, self.aggiorna_linea)
        self.root.after(1000, self.controlla_registrazione)

        # a sinistra, a gruppi: [sidebar]  [< play >]
        self.tasto("playlist", _("Playlists"), self.sidebar, 8)
        self.tasto("prima", "<", lambda: self.salta(-1), 3, padx=(6, 2))
        self.b_pausa = self.tasto("pausa", _("Pause"), self.pausa, 8)
        self.tasto("dopo", ">", lambda: self.salta(+1), 3)
        # a destra, da destra a sinistra: cuore, schermo intero, [switch
        # rec], ingranaggio, volume col suo muto
        self.b_pref = self.tasto("favorite_off", _("Favorite"), self.preferito,
                                 lato="right")
        self.tasto("pieno", _("Fullscreen"), self.schermo_intero, 14,
                   lato="right")
        # poi, da destra a sinistra, [switch rec] dopo l'ingranaggio
        self.b_rec = self.tasto("rec", _("Record"), self.registra, lato="right",
                                padx=(2, 6))
        self.tasto("switch", _("Switch"), self.switch, 6, lato="right",
                   padx=(6, 2))
        self.menu_qualita = tk.Menu(self.root, tearoff=0, bg=TASTO, fg=TESTO,
                                    activebackground=SCELTO,
                                    activeforeground="#ffffff", bd=0,
                                    relief="flat")
        # un bottone normale che apre il menu lui, sopra di se': il
        # Menubutton di Tk a volte non si apre, questo si'
        self.b_qualita = self.tasto("setting", _("quality"), self.apri_menu_qualita,
                                    lato="right")
        self.volume = Cursore(self.barra, self.alza_volume, larga=85)
        self.volume.pack(side="right", padx=(0, 6))
        self.b_muto = self.tasto("volume", _("Mute"), self.muto, lato="right")
        # i fotogrammi del mezzo giro, da 0 a 180 gradi
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
        # --- il video in mezzo
        self.video = tk.Frame(self.root, bg="black")
        self.video.pack(side="right", fill="both", expand=True)
        self.video.bind("<Button-1>", lambda e: self.root.focus_set())
        self.root.update()

        self.mpv = mpv.MPV(
            wid=str(self.video.winfo_id()),
            cache="yes",
            demuxer_max_bytes="256MiB",
            demuxer_max_back_bytes="64MiB",
            demuxer_readahead_secs=20,
            cache_pause_wait=5,
            network_timeout=30,
            ytdl=False,
            hwdec="no",
            user_agent=UA,
            cursor_autohide=3000,           # sul video il cursore lo nasconde mpv
            stream_lavf_o=("reconnect=1,reconnect_streamed=1,"
                           "reconnect_on_network_error=1,"
                           "reconnect_delay_max=5"),
        )
        # la master playlist locale rimanda a indirizzi http: ffmpeg da un
        # file non li aprirebbe, se non glielo si permette. Il valore ha
        # delle virgole dentro, che per mpv separano le coppie: si passa
        # con la lunghezza davanti (%N%), che e' il suo modo di quotare
        protocolli = "file,http,https,tcp,tls,crypto,data,httpproxy"
        try:
            self.mpv["demuxer-lavf-o"] = "protocol_whitelist=%%%d%%%s" % (
                len(protocolli), protocolli)
        except Exception:
            pass
        self.volume.set(int(self.cfg.get("volume", 85)))
        self.icona_volume()
        if self.icone_mancanti:
            self.scrivi(_("missing icons: %s") % ", ".join(self.icone_mancanti))

        # il cerchietto del caricamento: sta sopra al video, in mezzo, e
        # si vede mentre si cercano le qualita' e mentre si riempie il buffer
        # il carico: icone/loading.png che gira, prima del nome del canale
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
        # icone/screen.png sopra al video quando non c'e' niente che va
        self.sfondo = tk.Label(self.video, bg="black", bd=0)
        self.sfondo_file = os.path.join(QUI, "icone", "screen.png")
        self.sfondo_misura, self.sfondo_img, self.sfondo_su = None, None, False
        self.video.bind("<Configure>", lambda e: self.rifai_sfondo())
        self.mostra_sfondo(True)
        self.mpv.observe_property("paused-for-cache", self._buffer)
        self.mpv.observe_property("time-pos", self._va)

        self.pieno, self.barra_visibile, self.timer_barra = False, True, None
        self.sorveglio, self.dove_era = False, (0, 0)
        self.attesa_da, self.salti = 0.0, 0
        # mpv dice quando un file finisce male: canale morto, si salta
        try:
            @self.mpv.event_callback("end-file")
            def fine(ev, app=self):
                app.finito_male(ev)
        except Exception:
            pass
        # i tasti valgono quando non si sta scrivendo nella casella di
        # ricerca ne' scorrendo un elenco, se no le frecce fanno due cose
        def tasto(nome, cosa):
            def f(ev):
                if isinstance(self.root.focus_get(),
                              (tk.Entry, tk.Listbox, ttk.Treeview)):
                    return
                cosa()
            self.root.bind(nome, f)

        tasto("<space>", self.pausa)
        tasto("<m>", self.muto)
        # + e - spostano l'audio di 100 ms: + lo ritarda (audio in
        # anticipo sul video), - lo anticipa. Ricordato per canale
        tasto("<plus>", lambda: self.ritardo_audio(+0.1))
        tasto("<KP_Add>", lambda: self.ritardo_audio(+0.1))
        tasto("<minus>", lambda: self.ritardo_audio(-0.1))
        tasto("<KP_Subtract>", lambda: self.ritardo_audio(-0.1))
        tasto("<Up>", lambda: self.volume.set(self.volume.get() + 5))
        tasto("<Down>", lambda: self.volume.set(self.volume.get() - 5))
        tasto("<Left>", lambda: self.salta(-1))
        tasto("<Right>", lambda: self.salta(+1))
        self.root.bind("<Prior>", lambda e: self.salta(-1))
        self.root.bind("<Next>", lambda e: self.salta(+1))
        self.root.bind("<F11>", self.schermo_intero)
        self.root.bind("<Escape>", lambda e: self.schermo_intero(None, False))
        self.root.protocol("WM_DELETE_WINDOW", self.chiudi)

        if lista and os.path.isfile(lista):
            lista = os.path.abspath(lista)
            if lista not in self.tutte_le_liste():
                self.cfg.setdefault("liste", []).append(lista)
        self.rifai_liste(scegli=lista)
        threading.Thread(target=self.guarda, daemon=True).start()
        if self.cfg.get("epg"):
            threading.Thread(target=self._carica_epg, args=(self.cfg["epg"],),
                             daemon=True).start()

    # ------------------------------------------------- i pezzi di finestra
    def bottone(self, dove, testo, cosa):
        return tk.Button(dove, text=testo, command=cosa, bg=TASTO, fg=TESTO,
                         relief="flat", bd=0, highlightthickness=0,
                         activebackground=SCELTO, activeforeground="#ffffff",
                         cursor="hand2")

    def tasto(self, icona, testo, cosa, largo=6, lato="left", padx=2):
        """Un bottone della barra; padx=(6, 2) apre un gruppo nuovo, con
        piu' aria a sinistra."""
        b = self.bottone(self.barra, testo, cosa)
        if icona in self.icone:
            # nella barra i bottoni sono solo l'icona, senza scatola:
            # il fondo e' quello del pannello e si accende solo al tocco
            b.config(image=self.icone[icona], width=34, height=30, bg=BARRA)
        else:
            b.config(width=largo)
        b.pack(side=lato, padx=padx, pady=6)
        return b

    # ------------------------------------------------------------ le liste
    def tutte_le_liste(self):
        """Quelle nella cartella playlists/ e nelle sue categorie, che si
        caricano da sole, piu' i file aggiunti a mano. Gli avanzi nel file
        di configurazione - liste sparite, o che stanno gia' in playlists/ -
        si tolgono da soli."""
        fuori = liste_in_cartella()
        for _nome, dentro in categorie():
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
        return fuori + buone

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
            self.el_liste.see(self.iid_di[self.liste[i]])
        self.carica(self.liste[i])

    def rifai_albero(self):
        """Solo la barra delle liste, senza aprirne nessuna."""
        self.liste = self.tutte_le_liste()
        self.el_liste.delete(*self.el_liste.get_children())
        self.iid_di = {}                    # percorso -> riga nell'albero
        adesso = self.cfg.get("lista")

        def riga(padre, d):
            f = immagine_lista(d)
            if f and f not in self.img_liste:
                im = carica_logo(f, PUNTO, PUNTO)
                if im is not None:
                    self.img_liste[f] = im
            iid = self.el_liste.insert(padre, "end", text=" " + nome_di(d),
                                       image=self.img_liste.get(f, self.vuoto),
                                       tags=("usata",) if d == adesso else ())
            self.iid_di[d] = iid

        # in cima, se c'e', la guida: una cartella EPG col nome del file
        self.cartella_di = {}
        if self.cfg.get("epg"):
            padre = self.el_liste.insert("", "end", text=" EPG",
                                         image=self.icona_cartella(GRIGIO, True),
                                         open=True)
            self.cartella_di[padre] = GRIGIO
            f = immagine_lista("")
            if f and f not in self.img_liste:
                im = carica_logo(f, PUNTO, PUNTO)
                if im is not None:
                    self.img_liste[f] = im
            self.el_liste.insert(padre, "end", image=self.img_liste.get(f, self.vuoto),
                                 text=" " + nome_epg(self.cfg["epg"]))
        # poi le liste sciolte, e le categorie con le loro dentro
        gia = set()
        for nome, dentro in categorie():
            gia.update(dentro)
        for d in self.liste:
            if d not in gia:
                riga("", d)
        posto = 0
        for nome, dentro in categorie():
            colore = colore_cartella(nome, posto)
            if nome != PREFERITI:
                posto += 1
            aperta = any(d == adesso for d in dentro)
            padre = self.el_liste.insert("", "end", text=" " + nome,
                                         image=self.icona_cartella(colore, aperta),
                                         open=aperta)
            self.cartella_di[padre] = colore
            for d in dentro:
                riga(padre, d)

    def lista_dell_ultimo_canale(self):
        """La lista da aprire all'avvio: quella ricordata, se contiene
        ancora l'ultimo canale; se no quella, fra tutte, che ce l'ha; se
        nessuna, la ricordata e basta."""
        ricordata = self.cfg.get("lista")
        canale = self.cfg.get("canale")
        if ricordata and self.liste:
            # la ricordata, com'e' scritta nell'elenco di adesso
            ricordata = self.liste[self.trova_lista(ricordata)]
        if not canale:
            return ricordata
        ordine = ([ricordata] if ricordata else []) + \
                 [d for d in self.liste if d != ricordata]
        for d in ordine:
            try:
                if any(url == canale for _n, url, _i in leggi_lista(d)):
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
                self.el_liste.item(iid, tags=("usata",) if d == dove else ())
        self.non_sul_verde(self.el_liste, "usata")

    def icona_cartella(self, colore, aperta):
        k = (colore, aperta)
        if k not in self.cartelle:
            self.cartelle[k] = cartellina(colore, aperta) or self.vuoto
        return self.cartelle[k]

    def apri_chiudi_cartella(self, aperta):
        """La cartella cambia faccia quando si apre e quando si chiude."""
        iid = self.el_liste.focus()
        if iid in self.cartella_di:
            self.el_liste.item(iid, image=self.icona_cartella(
                self.cartella_di[iid], aperta))

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
        d = self.lista_selezionata()
        if d and d != self.cfg.get("lista"):
            self.carica(d)

    def carica(self, dove):
        # ci si segna subito quale, non a fine caricamento: se si chiude
        # prima che abbia finito, la lista e' ricordata lo stesso
        self.cfg["lista"] = dove
        scrivi_config(self.cfg)
        self.scrivi(_("loading %s...") % nome_di(dove))
        threading.Thread(target=self._carica, args=(dove,), daemon=True).start()

    def _carica(self, dove):
        try:
            canali = leggi_lista(dove)
        except Exception as e:
            self.scrivi(_("cannot read %s: %s") % (nome_di(dove), e))
            return
        self.canali = canali
        self.cfg["lista"] = dove
        scrivi_config(self.cfg)
        self.root.after(0, self.filtra)
        self.root.after(0, self.segna_in_uso, dove)
        self.scrivi(_("%s - %d channels") % (nome_di(dove), len(canali)))
        # l'ultimo canale visto, se sta in questa lista, riparte da solo
        ultimo = self.cfg.get("canale")
        if ultimo:
            for nome, url, ide in canali:
                if url == ultimo:
                    self.root.after(300, lambda n=nome, u=url, i=ide:
                                    self.apri(n, u, i))
                    break

    def aggiungi_file(self):
        # "tutti i file" per primo: una lista puo' non avere l'estensione
        f = filedialog.askopenfilename(
            title=_("Choose a playlist"),
            filetypes=[(_("All files"), "*"), (_("Playlists"), "*.m3u *.m3u8")])
        if f:
            if not e_una_lista(f):
                self.scrivi(_("%s does not look like an m3u playlist") % os.path.basename(f))
                return
            self.aggiungi(f)

    def aggiungi(self, dove):
        dove = os.path.abspath(dove)
        if dove not in self.tutte_le_liste():
            self.cfg.setdefault("liste", []).append(dove)
            scrivi_config(self.cfg)
        self.rifai_liste(scegli=dove)

    def togli_lista(self):
        """Toglie la lista selezionata. Il file sul disco non si tocca."""
        dove = self.lista_selezionata()
        if not dove:
            self.scrivi(_("select the playlist to remove first"))
            return
        if dove not in self.cfg.get("liste", []):
            self.scrivi(_("this one lives in playlists/: remove it by moving the file"))
            return
        self.cfg["liste"] = [d for d in self.cfg.get("liste", []) if d != dove]
        if self.cfg.get("lista") == dove:
            self.cfg.pop("lista", None)
        scrivi_config(self.cfg)
        self.rifai_liste()

    # ------------------------------------------------------------ i canali
    def filtra(self):
        q = self.cerca.get().lower()
        self.visti = [c for c in self.canali if q in c[0].lower()]
        self.elenco.delete(*self.elenco.get_children())
        adesso = self.cfg.get("canale")
        for i, (nome, url, _ide) in enumerate(self.visti[:3000]):
            self.elenco.insert("", "end", iid=str(i), text=" " + nome,
                               image=self.pallini[colore_di(nome)],
                               tags=("onda",) if url == adesso else ())

    def parti(self):
        s = self.elenco.selection()
        if s:
            nome, url, ide = self.visti[int(s[0])]
            self.apri(nome, url, ide)

    def segna_in_onda(self, url):
        """La riga del canale che si sta guardando diventa verde, e quella
        di prima torna normale."""
        for i, (_n, u, _l) in enumerate(self.visti[:3000]):
            if self.elenco.exists(str(i)):
                self.elenco.item(str(i), tags=("onda",) if u == url else ())
        self.non_sul_verde(self.elenco, "onda")

    def salta(self, dove):
        """Il canale prima o dopo, nell'elenco come lo si vede adesso."""
        if not self.visti:
            return
        adesso = self.cfg.get("canale")
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
        """Il canale in onda entra nei preferiti, o ne esce se c'e' gia'.
        I preferiti sono la lista playlists/favorite/favorite.m3u."""
        url = self.cfg.get("canale")
        if not url or not self.nome_in_onda:
            return
        pref = leggi_preferiti()
        if any(u == url for _n, u, _i in pref):
            pref = [c for c in pref if c[1] != url]
            self.scrivi(_("%s removed from favorites") % self.nome_in_onda)
        else:
            pref.append((self.nome_in_onda, url, self.ide_in_onda))
            self.scrivi(_("%s added to favorites") % self.nome_in_onda)
        try:
            scrivi_preferiti(pref)
        except Exception as e:
            self.scrivi(_("cannot write favorites: %s") % e)
            return
        self.icona_preferito()
        self.rifai_albero()
        if self.cfg.get("lista") == file_preferiti():
            self.carica(file_preferiti())   # e' la lista aperta: si aggiorna

    def icona_preferito(self):
        url = self.cfg.get("canale")
        acceso = bool(url) and any(u == url for _n, u, _i in leggi_preferiti())
        self.faccia(self.b_pref, "favorite_on" if acceso else "favorite_off",
                    _("Favorite"))

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
        # ci si ricorda del canale di prima, per lo switch
        if self.cfg.get("canale") and self.cfg.get("canale") != url:
            self.precedente = (self.nome_in_onda, self.cfg.get("canale"))
        if self.registrando and self.cfg.get("canale") != url:
            self.ferma_registrazione()          # cambio canale: si chiude il file
        self.nome_in_onda, self.ide_in_onda = nome, ide
        self.attesa_da = time.time()
        self.colora_stato(colore_di(nome))          # come il pallino
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
        # il ritardo audio ricordato per questo canale, se no zero
        try:
            self.mpv.audio_delay = float(self.cfg.get("ritardo", {}).get(url, 0.0))
        except Exception:
            pass
        self.varianti = varianti(url)
        self.affanni, self.tranquillo_da = [], time.time()
        self.ultima_discesa = 0.0
        self.storia, self.in_calo = [], 0
        self.manuale = True
        if self.varianti:
            # si parte dalla qualita' migliore e ci si resta: da li' in
            # poi decide l'utente dal menu, l'app non cambia da sola
            self.quale = len(self.varianti) - 1
            self.suona(self.quale)
        else:
            self.quale = -1
            try:
                self.mpv["audio-files"] = []
            except Exception:
                pass
            self.mpv.play(url)
        self.scrivi_riga()

    def suona(self, i):
        """La variante scelta: a mpv si da' una master playlist locale con
        dentro solo quella e il suo gruppo audio, cosi' video e audio li
        legge insieme e restano a tempo (dati separati derivavano)."""
        _b, video, audio, _alta, master = self.varianti[i]
        try:
            self.mpv["audio-files"] = []
        except Exception:
            pass
        # con l'audio in un gruppo a parte, appena parte si fa un
        # riallineamento (vedi _va): ci si segna che tocca farlo
        self.da_riallineare = bool(audio)
        self.suonato_da = 0.0
        try:
            os.makedirs(CACHE_VARIANTE, exist_ok=True)
            f = os.path.join(CACHE_VARIANTE, "variante.m3u8")
            with open(f, "w", encoding="utf-8") as o:
                o.write(master)
            self.mpv.play(f)
        except Exception:
            self.mpv.play(video)            # senza cache: la variante nuda
        # ci si segna dove si e' arrivati su questo canale
        self.cfg.setdefault("qualita", {})[self.cfg.get("canale", "")] = i

    def scegli_qualita(self, i):
        """Dal menu: -1 e' Auto, un numero e' una variante fissa."""
        if i >= 0 and i != self.quale and self.varianti:
            self.quale = i
            self.suona(i)
        self.scrivi_riga()

    def apri_menu_qualita(self):
        """Il menu si apre sopra al bottone, allineato a sinistra."""
        m = self.menu_qualita
        if m.winfo_ismapped():
            m.unpost()
            return
        m.delete(0, "end")
        for testo, cosa in self.voci_qualita():
            m.add_command(label=testo, command=cosa,
                          state="normal" if cosa else "disabled")
        m.update_idletasks()
        x = self.b_qualita.winfo_rootx()
        y = self.b_qualita.winfo_rooty() - m.winfo_reqheight() - 4
        self.gira_ingranaggio(+1)
        m.tk_popup(x, max(0, y))     # si richiude da solo cliccando fuori
        self.root.after(150, self.menu_chiuso)

    def menu_chiuso(self):
        """Quando il menu sparisce, la rotella torna indietro."""
        if self.menu_qualita.winfo_ismapped():
            self.root.after(150, self.menu_chiuso)
        else:
            self.gira_ingranaggio(-1)

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

    # ------------------------------------------------------ l'adattamento
    def riga(self):
        """I tre pezzi della riga di stato: il canale, il titolo del
        programma in onda (del colore del canale) e il suo orario."""
        p = self.programma()
        if not p:
            # con la guida caricata ma senza questo canale lo si dice
            return (self.nome, "", _("not in the guide") if self.epg and self.nome else "")
        inizio, fine, titolo = p
        return (self.nome, titolo or "?", "%s - %s" % (
            time.strftime("%H:%M", time.localtime(inizio)),
            time.strftime("%H:%M", time.localtime(fine))))

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
                legge = float(stato.get("raw-input-rate") or 0.0)  # byte/s
            except Exception:
                continue
            ora = time.time()
            # --- si anticipa: il buffer sta calando, o il flusso arriva
            # piu' piano di quanto la variante chiede. Se dura qualche
            # secondo si scende ORA, finche' c'e' ancora buffer da spendere
            self.storia = [(t0, b) for t0, b in self.storia if ora - t0 <= 10.0]
            self.storia.append((ora, buf))
            cala = False
            if len(self.storia) >= 5:
                t0, b0 = self.storia[0]
                pendenza = (buf - b0) / max(1.0, ora - t0)   # s di buffer al s
                cala = pendenza < IN_CALO and buf < TRANQUILLO
            banda = self.varianti[self.quale][0]
            # la velocita' di lettura vale solo mentre legge davvero: a
            # buffer pieno mpv smette di leggere e la velocita' va a zero
            lento = (legge > 0 and buf < TRANQUILLO and
                     legge * 8 < banda * 1.05)
            if cala or lento:
                self.in_calo += 1
                if self.in_calo >= QUANTO_IN_CALO and self.quale > 0:
                    self.cambia(-1)
                    continue
            else:
                self.in_calo = 0
            # --- e la rete di sicurezza: si e' gia' fermato o quasi
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

    # ---------------------------------------------------------- i comandi
    def faccia(self, b, icona, testo):
        if icona in self.icone:
            b.config(image=self.icone[icona])
        else:
            b.config(text=testo)

    def pausa(self):
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

    def icona_volume(self):
        """L'altoparlante secondo il volume: spento, basso, medio, alto;
        rosso se e' in muto."""
        try:
            muto = bool(self.mpv.mute)
        except Exception:
            muto = False
        v = self.volume.get()
        if muto:
            icona = "muto"
        elif v == 0:
            icona = "volume_off"
        elif v < 34:
            icona = "volume_low"
        elif v < 67:
            icona = "volume"
        else:
            icona = "volume_high"
        if icona not in self.icone and "volume" in self.icone:
            icona = "volume"                    # manca la png: quella di base
        self.faccia(self.b_muto, icona, _("Unmute") if muto else _("Mute"))

    def alza_volume(self, v):
        try:
            self.mpv.volume = float(v)
        except Exception:
            pass
        self.cfg["volume"] = int(float(v))
        if hasattr(self, "b_muto"):
            self.icona_volume()

    def disponi(self, pieno):
        """Mette i pezzi al loro posto, nell'ordine giusto. L'ordine conta:
        i pannelli prima, il video per ultimo, che si prende il resto."""
        for w in (self.cima, self.sinistra, self.destra, self.riga_stato,
                  self.barra, self.video):
            w.pack_forget()
        if not pieno:
            self.cima.pack(side="top", fill="x")
        # le barre laterali: aperte o chiuse, uguale a schermo intero e no
        if not self.nascosti.get(self.sinistra):
            self.sinistra.pack(side="left", fill="y")
        if not self.nascosti.get(self.destra):
            self.destra.pack(side="right", fill="y")
        if not pieno:
            self.riga_stato.pack(side="bottom", fill="x")
            self.barra.pack(side="bottom", fill="x")
        self.video.pack(side="right", fill="both", expand=True)
        self.root.config(cursor="")

    def schermo_intero(self, ev=None, acceso=None):
        self.pieno = (not self.pieno) if acceso is None else acceso
        self.root.attributes("-fullscreen", self.pieno)
        self.ridisponi()

    def si_nasconde(self):
        """Comandi e mouse vanno e vengono quando c'e' solo il video: barre
        laterali chiuse, a schermo intero o in finestra, uguale."""
        return not self.sidebar_aperte()

    def sorveglia_mouse(self):
        """Il mouse sta sopra al video, che e' la finestra di mpv: Tk il
        movimento non lo vede. Allora si guarda dov'e' il puntatore cinque
        volte al secondo, e se si e' spostato e' mosso."""
        if not self.si_nasconde():
            self.sorveglio = False
            return
        try:
            adesso = self.root.winfo_pointerxy()
        except Exception:
            adesso = self.dove_era
        if adesso != self.dove_era:
            self.dove_era = adesso
            self.mosso()
        self.root.after(200, self.sorveglia_mouse)

    def mosso(self, ev=None):
        """Il mouse si muove, i comandi si fanno vedere; sta fermo tre
        secondi e se ne vanno, col mouse."""
        if not self.barra_visibile:
            self.barra.pack(side="bottom", fill="x", before=self.video)
            self.barra_visibile = True
        self.root.config(cursor="")
        if self.timer_barra:
            self.root.after_cancel(self.timer_barra)
            self.timer_barra = None
        if self.si_nasconde():
            self.timer_barra = self.root.after(3000, self.nascondi_barra)

    def nascondi_barra(self):
        self.timer_barra = None
        if self.si_nasconde() and self.barra_visibile:
            self.barra.pack_forget()
            self.barra_visibile = False
            self.root.config(cursor="none")

    # ------------------------------------------------- il caricamento
    # ------------------------------------------------------- i menu in alto
    def menu(self, titolo, voci):
        """Una voce della barra in alto; voci e' una funzione che da' le
        righe al momento, cosi' dicono la cosa giusta (Show/Hide...)."""
        et = tk.Label(self.cima, text=_(titolo), bg=BARRA, fg=TESTO,
                      padx=10, cursor="hand2")
        et.pack(side="left", fill="y")
        et.bind("<Enter>", lambda e: et.config(bg=SCELTO))
        et.bind("<Leave>", lambda e: et.config(bg=BARRA))
        et.bind("<Button-1>", lambda e: self.apri_menu_cima(et, voci))
        self.menu_cima[titolo] = (et, voci)

    def apri_menu_cima(self, et, voci):
        m = self.menu_aperto
        if m.winfo_ismapped():
            m.unpost()
            return
        m.delete(0, "end")
        for testo, cosa in voci():
            m.add_command(label=testo, command=cosa,
                          state="normal" if cosa else "disabled")
        m.tk_popup(et.winfo_rootx(), et.winfo_rooty() + et.winfo_height())

    def cambia_lingua(self, codice):
        """La lingua nuova subito, senza riavviare: si riscrive quello che
        e' a vista, i menu si rifanno da soli quando si aprono."""
        global LINGUA
        LINGUA = codice
        self.cfg["lingua"] = codice
        scrivi_config(self.cfg)
        for titolo, (et, _v) in self.menu_cima.items():
            et.config(text=_(titolo))
        self.et_liste.config(text=_("Playlists"))
        vecchio = self.cerca.vuota
        self.cerca.vuota = _("Search channels")
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

    def apri_cartella_liste(self):
        cartella = CARTELLE_LISTE[0]
        try:
            os.makedirs(cartella, exist_ok=True)
            subprocess.Popen(["xdg-open", cartella])
        except Exception as e:
            self.scrivi(_("cannot open %s: %s") % (cartella, e))

    def sidebar_aperte(self):
        return not (self.nascosti.get(self.sinistra) and self.nascosti.get(self.destra))

    def sidebar(self):
        """Il bottone nella barra: via tutte e due le barre laterali, o di
        nuovo tutte e due. Vale uguale a schermo intero e no."""
        via = not (self.nascosti.get(self.sinistra) and self.nascosti.get(self.destra))
        self.nascosti[self.sinistra] = self.nascosti[self.destra] = via
        self.ridisponi()

    def ridisponi(self):
        """Rifa' la disposizione com'e' adesso: i comandi a vista, e se c'e'
        solo il video parte la sorveglianza del mouse che li nasconde."""
        self.disponi(self.pieno)            # in finestra impacchetta anche la barra
        self.barra_visibile = not self.pieno
        self.mosso()                        # a schermo intero la mette lui
        if self.si_nasconde() and not self.sorveglio:
            self.sorveglio = True
            self.dove_era = self.root.winfo_pointerxy()
            self.sorveglia_mouse()

    def nascondi(self, pannello):
        self.nascosti[pannello] = not self.nascosti.get(pannello)
        self.ridisponi()

    def ricarica_epg(self):
        dove = self.cfg.get("epg")
        if not dove:
            self.scrivi(_("no TV guide set"))
            return
        try:
            if not os.path.isfile(dove):
                f = os.path.join(CACHE_EPG, hashlib.md5(dove.encode()).hexdigest())
                if os.path.isfile(f):
                    os.remove(f)                # via la copia: si riscarica
        except Exception:
            pass
        threading.Thread(target=self._carica_epg, args=(dove,), daemon=True).start()

    def togli_epg(self):
        self.cfg["epg"] = ""
        scrivi_config(self.cfg)
        self.epg_nomi, self.epg = {}, {}
        self.rifai_albero()
        self.scrivi_riga()

    def chiedi_epg(self):
        """Chiede l'url (o il percorso) della guida XMLTV e la carica."""
        dove = simpledialog.askstring(
            _("TV guide"), _("URL or file of the guide (XMLTV, .gz is fine):"),
            initialvalue=self.cfg.get("epg", ""), parent=self.root)
        if dove is None:
            return
        dove = dove.strip()
        self.cfg["epg"] = dove
        scrivi_config(self.cfg)
        self.rifai_albero()
        if not dove:
            self.epg_nomi, self.epg = {}, {}
            self.scrivi_riga()
            return
        threading.Thread(target=self._carica_epg, args=(dove,),
                         daemon=True).start()

    def _carica_epg(self, dove):
        self.scrivi(_("loading the guide..."))
        try:
            f = prendi_epg(dove)
            nomi, programmi = leggi_epg(f)
        except Exception as e:
            self.scrivi(_("guide not loaded: %s") % e)
            return
        self.epg_nomi, self.epg = nomi, programmi
        self.scrivi(_("guide loaded: %d channels, %d programmes") % (
            len(nomi), sum(len(v) for v in programmi.values())))
        self.root.after(4000, self.scrivi_riga)

    def programma(self):
        """Il programma in onda sul canale: (inizio, fine, titolo), o
        None. Il canale si trova per tvg-id, se no per nome."""
        if not self.epg:
            return None
        ide = self.ide_in_onda
        if not ide or ide not in self.epg:
            ide = self.epg_nomi.get(nome_piatto(self.nome_in_onda))
        adesso = time.time()
        for inizio, fine, titolo in self.epg.get(ide or "", []):
            if inizio <= adesso < fine:
                return inizio, fine, titolo
            if inizio > adesso:
                break
        return None

    def colora_stato(self, colore):
        """La progress e il titolo del programma del colore del pallino
        del canale."""
        self.linea.itemconfig(self.pieno_linea, fill=colore)
        self.et_titolo.config(fg=colore)

    def aggiorna_linea(self):
        """Ogni mezzo secondo: la riga bianca e' il tempo che manca alla
        fine del programma in onda. Senza guida resta vuota."""
        parte = 0.0
        if self.cfg.get("canale") and not self.sfondo_su:
            p = self.programma()
            if p:
                # con la guida: quanto manca alla fine del programma,
                # piena all'inizio, vuota alla fine
                inizio, fine, _t = p
                parte = (fine - time.time()) / max(1.0, fine - inizio)
                parte = max(0.0, min(1.0, parte))
            if (self.stato.cget("text") == self.riga_mostrata[0] and
                    self.riga() != self.riga_mostrata):
                self.scrivi_riga()          # e' cambiato programma
        w = self.linea.winfo_width()
        self.linea.coords(self.pieno_linea, 0, 0, int(w * parte), 2)
        self.root.after(500, self.aggiorna_linea)
        self.root.after(1000, self.controlla_registrazione)

    def mostra_sfondo(self, si):
        """L'immagine di sfondo del lettore: si vede finche' non parte un
        canale, e torna quando non c'e' piu' niente che va."""
        self.sfondo_su = si
        if si:
            self.rifai_sfondo()
            self.sfondo.place(x=0, y=0, relwidth=1, relheight=1)
            tk.Misc.tkraise(self.sfondo)
        else:
            self.sfondo.place_forget()

    def rifai_sfondo(self):
        """L'immagine adattata al riquadro del video, proporzioni tenute."""
        if not self.sfondo_su or not os.path.isfile(self.sfondo_file):
            return
        w, h = max(1, self.video.winfo_width()), max(1, self.video.winfo_height())
        if (w, h) == self.sfondo_misura:
            return
        self.sfondo_misura = (w, h)
        try:
            if HA_PIL:
                im = Image.open(self.sfondo_file).convert("RGBA")
                k = min(w / float(im.width), h / float(im.height))
                im = im.resize((max(1, int(im.width * k)),
                                max(1, int(im.height * k))), Image.LANCZOS)
                self.sfondo_img = ImageTk.PhotoImage(im)
            else:
                self.sfondo_img = tk.PhotoImage(file=self.sfondo_file)
            self.sfondo.config(image=self.sfondo_img)
        except Exception:
            pass

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
            motivo = str(d.get("reason", "")) + str(d.get("file_error", ""))
            if not motivo:
                motivo = str(getattr(getattr(ev, "data", None), "reason", ""))
        except Exception:
            pass
        if "error" in motivo.lower():
            self.root.after(0, self.canale_morto)

    def canale_morto(self):
        """Il canale non va: avanti col prossimo, ma non all'infinito. Se
        li si e' provati tutti o dieci di fila, ci si ferma."""
        self.attesa_da = 0.0
        self.mostra_carico(False)
        self.salti += 1
        if self.salti >= min(10, max(1, len(self.visti))):
            self.salti = 0
            self.scrivi(_("%s is not responding, nor are the next ones") % self.nome)
            self.mostra_sfondo(True)
            return
        self.scrivi(_("%s is not responding: skipping to the next") % self.nome)
        self.root.after(600, lambda: self.salta(+1))

    def _va(self, _nome, pos):
        """Il tempo avanza: sta suonando davvero, il cerchietto va via."""
        if pos is not None:
            self.attesa_da, self.salti = 0.0, 0     # va: niente da saltare
            if self.sfondo_su:
                self.root.after(0, self.mostra_sfondo, False)
            if self.da_riallineare:
                # audio e video da due playlist partono da pezzi diversi e
                # all'inizio sono sfasati: dopo tre secondi di gioco un
                # seek sul punto in cui si e' (dentro al buffer, senza
                # ricaricare) svuota i decoder e li fa ripartire insieme
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

    # -------------------------------------------------- la registrazione
    def registra(self):
        """Il bottone rec: parte la registrazione del canale in onda, o si
        ferma se e' in corso. Il file va in ~/Videos/xvb, com'e' il flusso,
        senza ricodificare."""
        if self.registrando:
            self.ferma_registrazione()
            return
        if not self.cfg.get("canale") or not self.nome_in_onda:
            return
        self.avvia_registrazione(self.nome_in_onda, 0.0)

    def avvia_registrazione(self, nome, fine):
        """Apre il file e dice a mpv di scriverci il flusso; con `fine`
        (secondi dal 1970) si ferma da sola a quell'ora."""
        try:
            os.makedirs(REGISTRAZIONI, exist_ok=True)
            pulito = re.sub(r"[^\w\-]+", "_", nome).strip("_") or "xvb"
            f = os.path.join(REGISTRAZIONI, "%s_%s.mkv" % (
                pulito, time.strftime("%Y-%m-%d_%H-%M")))
            self.mpv.stream_record = f
        except Exception as e:
            self.scrivi(_("cannot record: %s") % e)
            return
        self.registrando, self.fine_rec = f, fine
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
        self.root.after(4000, self.scrivi_riga)

    def mostra_rec(self):
        """In fondo alla riga di stato, in rosso: REC, con l'ora a cui si
        ferma se e' pianificata. Vuoto se non si registra."""
        if not self.registrando:
            t = ""
        elif self.fine_rec:
            t = "  -  " + _("REC until %s") % time.strftime("%H:%M", time.localtime(self.fine_rec))
        else:
            t = "  -  REC"
        self.et_rec.config(text=t)

    def pianifica(self):
        """Dal menu: ora di inizio e di fine per registrare il canale in
        onda. All'ora giusta l'app lo apre da sola e registra."""
        if not self.cfg.get("canale") or not self.nome_in_onda:
            self.scrivi(_("open the channel to record first"))
            return
        inizio = simpledialog.askstring(_("Schedule"), _("Start (HH:MM):"),
                                        initialvalue=time.strftime("%H:%M"),
                                        parent=self.root)
        if not inizio:
            return
        fine = simpledialog.askstring(_("Schedule"), _("End (HH:MM):"),
                                      parent=self.root)
        if not fine:
            return
        try:
            t0 = self.ora_di(inizio)
            t1 = self.ora_di(fine, dopo=t0)
        except ValueError:
            self.scrivi(_("time must be HH:MM"))
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
                    # il tempo di partire, poi si registra
                    self.root.after(5000, lambda: self.avvia_registrazione(nome, t1))
                else:
                    self.avvia_registrazione(nome, t1)
        if self.registrando and self.fine_rec and adesso >= self.fine_rec:
            self.ferma_registrazione()
        self.root.after(1000, self.controlla_registrazione)

    def apri_registrazioni(self):
        try:
            os.makedirs(REGISTRAZIONI, exist_ok=True)
            subprocess.Popen(["xdg-open", REGISTRAZIONI])
        except Exception as e:
            self.scrivi(_("cannot open %s: %s") % (REGISTRAZIONI, e))

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

    def scrivi(self, t):
        """Un messaggio nella riga di stato: titolo e orario si tolgono."""
        def fai():
            self.stato.config(text=t)
            self.et_titolo.config(text="")
            self.et_ora.config(text="")
        self.root.after(0, fai)

    def scrivi_riga(self):
        """Il canale, il titolo del programma e l'orario nella riga di
        stato, e ci si segna cos'e' stato scritto: quando cambia
        programma si riscrive da sola, ma solo se non c'e' sopra un altro
        messaggio."""
        self.riga_mostrata = self.riga()
        nome, titolo, ora = self.riga_mostrata

        def fai():
            self.stato.config(text=nome)
            self.et_titolo.config(text=("  -  " + titolo) if titolo else "")
            self.et_ora.config(text=("  (" + ora + ")") if titolo and ora else
                               ("  -  " + ora if ora else ""))
        self.root.after(0, fai)


    def chiudi(self):
        scrivi_config(self.cfg)
        try:
            self.mpv.terminate()
        except Exception:
            pass
        self.root.destroy()


if __name__ == "__main__":
    TV(sys.argv[1] if len(sys.argv) > 1 else None).root.mainloop()
