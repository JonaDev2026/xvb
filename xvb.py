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
import math
import os
import random
import re
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, ttk
import tkinter.font as tkfont
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

import mpv

try:                            # per i tag della musica (artista - titolo)
    import mutagen
    HA_TAG = True
except ImportError:
    HA_TAG = False
try:                            # per i loghi: ridimensiona e legge i jpg
    from PIL import Image, ImageTk
    HA_PIL = True
except ImportError:             # senza, si va di PhotoImage: solo png
    HA_PIL = False

VERSIONE = "3.1"
AUTORE = "Jonathan Sanfilippo"
ANNO = "2026"
REPO = "JonaDev2026/xvb"        # dove stanno le release, per l'avviso di aggiornamento
DONA = "https://www.paypal.com/paypalme/Jonathanuk"
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
if not os.access(QUI, os.W_OK):
    # installata nel sistema (/usr/lib/xvb): accanto all'app non si puo'
    # scrivere, la cartella buona e' quella dell'utente
    CARTELLE_LISTE = CARTELLE_LISTE[::-1]
# la vecchia lists/ si legge ancora, se c'e', ma non si crea piu'
CARTELLE_VECCHIE = tuple(c for c in (os.path.join(QUI, "lists"),
                                     os.path.join(CASA, ".local", "share", "xvb", "lists"))
                         if os.path.isdir(c))
PUNTO = 24                      # il riquadro del pallino, nell'elenco
# i colori dei pallini: quelli delle etichette del Mac
# ------------------------------------------------------------- le lingue
LINGUE = (("en", "English"), ("it", "Italiano"), ("es", "Español"), ("fr", "Français"),
          ("de", "Deutsch"), ("pt", "Português"), ("ru", "Русский"))
LINGUA = "en"
TESTI = {
    "it": {
        "Audio CD": "CD audio",
        "Track %d": "Traccia %d",
        "Set logo...": "Imposta logo...",
        "Remove logo": "Togli logo",
        "Discs": "Dischi",
        "This DVD is protected and can't be recorded": "Questo DVD è protetto e non si può registrare",
        "Set cover...": "Imposta copertina...",
        "Remove cover": "Togli copertina",
        "Choose an image": "Scegli un'immagine",
        "Radio": "Radio",
        "Music streams": "Stream musicali",
        "Film streams": "Stream film",
        "Temporary": "Provvisori",
        "Save in:": "Salva in:",
        "Move to %s": "Sposta in %s",
        "Rename...": "Rinomina...",
        "cannot rename %s: %s": "non riesco a rinominare %s: %s",
        "Opened URLs": "URL aperti",
        "The addresses will be removed from the list.": "Gli indirizzi vengono tolti dall'elenco.",
        "Open URL...": "Apri URL...",
        "Address of the stream or file:": "Indirizzo dello stream o del file:",
        "Load subtitles...": "Carica sottotitoli...",
        "Subtitle delay +100 ms": "Ritardo sottotitoli +100 ms",
        "Subtitle delay -100 ms": "Ritardo sottotitoli -100 ms",
        "Subtitles larger": "Sottotitoli più grandi",
        "Subtitles smaller": "Sottotitoli più piccoli",
        "Reset subtitles": "Azzera sottotitoli",
        "subtitles: %s": "sottotitoli: %s",
        "subtitle delay %+.1fs": "ritardo sottotitoli %+.1fs",
        "subtitle size %d%%": "sottotitoli al %d%%",
        "nothing is playing": "non c'è niente in riproduzione",
        "Recent": "Recenti",
        "Clear recent": "Svuota recenti",
        "No repeat": "Non ripetere",
        "Repeat one": "Ripeti il file",
        "Repeat all": "Ripeti tutto",
        "until %s": "fino alle %s",
        "Delete": "Elimina",
        "Delete %s": "Elimina %s",
        "The guide will be removed from the list.": "La guida verrà tolta dall'elenco.",
        "%d recording(s) will be deleted from disk. This cannot be undone.": "%d registrazione/i verranno cancellate dal disco. Non si può annullare.",
        "The playlist will be removed from the list.": "La playlist verrà tolta dall'elenco.",
        "The file %s will be deleted from disk. This cannot be undone.": "Il file %s verrà cancellato dal disco. Non si può annullare.",
        "cannot delete %s: %s": "non riesco a cancellare %s: %s",
        "%s - %d recordings": "%s - %d registrazioni",
        "Recordings": "Registrazioni",
        "Video": "Video",
        "Speed x%g": "Velocità x%g",
        "Aspect %s": "Proporzioni %s",
        "Fill screen (crop)": "Riempi lo schermo (taglia)",
        "Deinterlace": "Deinterlaccia",
        "Brightness +": "Luminosità +",
        "Brightness -": "Luminosità -",
        "Contrast +": "Contrasto +",
        "Contrast -": "Contrasto -",
        "Saturation +": "Saturazione +",
        "Saturation -": "Saturazione -",
        "Reset picture": "Azzera immagine",
        "Screenshot": "Istantanea",
        "Always on top": "Sempre in primo piano",
        "Track: %s": "Traccia: %s",
        "Subtitles off": "Sottotitoli spenti",
        "Subtitles: %s": "Sottotitoli: %s",
        "Volume boost +50%": "Volume extra +50%",
        "Normalize loudness": "Normalizza il volume",
        "speed x%g": "velocità x%g",
        "brightness": "luminosità",
        "contrast": "contrasto",
        "saturation": "saturazione",
        "screenshot saved in %s": "istantanea salvata in %s",
        "cannot take a screenshot: %s": "istantanea non riuscita: %s",
        "Programme guide": "Guida programmi",
        "no guide data for this playlist": "nessun programma in guida per questa playlist",
        "OK": "OK",
        "Cancel": "Annulla",
        "MIT license": "Licenza MIT",
        "URL of the playlist (m3u):": "Url della playlist (m3u):",
        "Name:": "Nome:",
        "About": "Info",
        "About XVB...": "Informazioni su XVB...",
        "Version": "Versione",
        "new version %s available": "nuova versione %s disponibile",
        "new version %s available: Help > Download": "nuova versione %s disponibile: Aiuto > Scarica",
        "Check for updates": "Cerca aggiornamenti",
        "Donate": "Dona",
        "Download %s": "Scarica la %s",
        "Up to date": "Aggiornata",
        "cannot check for updates: %s": "non riesco a controllare gli aggiornamenti: %s",
        "up to date (%s)": "aggiornata (%s)",
        "Remove %s": "Rimuovi %s",
        "removed %s": "rimossa %s",
        "%s is already there": "%s c'è già",
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
        "File": "File", "Playlists": "Playlist",
        "ready": "pronto",
        "unnamed": "senza nome",
        "single quality": "una sola qualita'",
        "missing icons: %s": "icone mancanti: %s",
        "no playlist: put one in playlists/": "nessuna playlist: mettine una in playlists/",
        "loading %s...": "carico %s...",
        "cannot read %s: %s": "non riesco a leggere %s: %s",
        "This DVD can't be played": "Questo DVD non si puo' riprodurre",
        "Media": "Media",
        "Playback": "Riproduzione",
        "IPTV": "IPTV",
        "Help": "Aiuto",
        "Play / Pause": "Play / Pausa",
        "Back 10 s": "Indietro 10 s",
        "Forward 10 s": "Avanti 10 s",
        "Previous": "Precedente",
        "Next": "Successivo",
        "Add playlist URL": "Aggiungi URL playlist",
        "Open playlists folder": "Apri cartella playlist",
        "Reload playlists": "Ricarica playlist",
        "Add guide URL": "Aggiungi URL guida",
        "Reload guide": "Ricarica guida",
        "Recording": "Registrazione",
        "Show list": "Mostra elenco",
        "Hide list": "Nascondi elenco",
        "Show sidebar": "Mostra barra laterale",
        "Hide sidebar": "Nascondi barra laterale",
        "%s - %d channels": "%s - %d canali",
        "Choose a playlist": "Scegli una playlist",
        "All files": "Tutti i file",
        "Open file...": "Apri file...", "Choose a file": "Scegli un file",
        "Media files": "File multimediali", "Open folder...": "Apri cartella...",
        "Download posters and covers": "Scarica poster e copertine",
        "Film titles and genres": "Titoli e generi dei film",
        "Download film titles and genres again in the new language?":
            "Riscaricare titoli e generi dei film nella nuova lingua?",
        "Download": "Scarica",
        "open a folder of films or music first": "apri prima una cartella di film o musica",
        "downloading posters and covers... %d/%d": "scarico poster e copertine... %d/%d",
        "%d posters and covers downloaded": "%d tra poster e copertine scaricati",
        "Choose a folder": "Scegli una cartella", "Imported media": "Media importati",
        "Open": "Apri", "%s - %d files": "%s - %d file",
        "The folder will be removed from the list. Files are not touched.":
            "La cartella viene tolta dall'elenco. I file non si toccano.",
        "The imported files will be removed from the list. Files are not touched.":
            "I file importati vengono tolti dall'elenco. I file non si toccano.",
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
        "Logos": "Loghi", "Dots": "Pallini", "Border": "Bordo",
        "Unmute": "Audio", "Switch": "Scambia", "Shuffle": "Casuale",
        "Open folder": "Apri cartella", "Reload": "Ricarica", "Add URL": "Aggiungi url",
        "Remove": "Rimuovi", "View": "Vista", "Hide channels": "Nascondi canali",
        "Show channels": "Mostra canali", "Hide playlists": "Nascondi playlist",
        "Show playlists": "Mostra playlist", "Language": "Lingua",
    },
    "es": {
        "Audio CD": "CD de audio",
        "Track %d": "Pista %d",
        "Set logo...": "Poner logo...",
        "Remove logo": "Quitar logo",
        "Discs": "Discos",
        "This DVD is protected and can't be recorded": "Este DVD está protegido y no se puede grabar",
        "Set cover...": "Poner portada...",
        "Remove cover": "Quitar portada",
        "Choose an image": "Elige una imagen",
        "Radio": "Radio",
        "Music streams": "Streams de música",
        "Film streams": "Streams de películas",
        "Temporary": "Temporales",
        "Save in:": "Guardar en:",
        "Move to %s": "Mover a %s",
        "Rename...": "Renombrar...",
        "cannot rename %s: %s": "no puedo renombrar %s: %s",
        "Opened URLs": "URL abiertas",
        "The addresses will be removed from the list.": "Las direcciones se quitarán de la lista.",
        "Open URL...": "Abrir URL...",
        "Address of the stream or file:": "Dirección del stream o del archivo:",
        "Load subtitles...": "Cargar subtítulos...",
        "Subtitle delay +100 ms": "Retraso de subtítulos +100 ms",
        "Subtitle delay -100 ms": "Retraso de subtítulos -100 ms",
        "Subtitles larger": "Subtítulos más grandes",
        "Subtitles smaller": "Subtítulos más pequeños",
        "Reset subtitles": "Restablecer subtítulos",
        "subtitles: %s": "subtítulos: %s",
        "subtitle delay %+.1fs": "retraso de subtítulos %+.1fs",
        "subtitle size %d%%": "subtítulos al %d%%",
        "nothing is playing": "no se está reproduciendo nada",
        "Recent": "Recientes",
        "Clear recent": "Borrar recientes",
        "No repeat": "Sin repetir",
        "Repeat one": "Repetir uno",
        "Repeat all": "Repetir todo",
        "until %s": "hasta las %s",
        "Delete": "Eliminar",
        "Delete %s": "Eliminar %s",
        "The guide will be removed from the list.": "La guía se quitará de la lista.",
        "%d recording(s) will be deleted from disk. This cannot be undone.": "%d grabación/es se borrarán del disco. No se puede deshacer.",
        "The playlist will be removed from the list.": "La lista se quitará del elenco.",
        "The file %s will be deleted from disk. This cannot be undone.": "El archivo %s se borrará del disco. No se puede deshacer.",
        "cannot delete %s: %s": "no puedo borrar %s: %s",
        "%s - %d recordings": "%s - %d grabaciones",
        "Recordings": "Grabaciones",
        "Video": "Vídeo",
        "Speed x%g": "Velocidad x%g",
        "Aspect %s": "Proporción %s",
        "Fill screen (crop)": "Llenar pantalla (recortar)",
        "Deinterlace": "Desentrelazar",
        "Brightness +": "Brillo +",
        "Brightness -": "Brillo -",
        "Contrast +": "Contraste +",
        "Contrast -": "Contraste -",
        "Saturation +": "Saturación +",
        "Saturation -": "Saturación -",
        "Reset picture": "Restablecer imagen",
        "Screenshot": "Captura",
        "Always on top": "Siempre encima",
        "Track: %s": "Pista: %s",
        "Subtitles off": "Subtítulos desactivados",
        "Subtitles: %s": "Subtítulos: %s",
        "Volume boost +50%": "Volumen extra +50%",
        "Normalize loudness": "Normalizar volumen",
        "speed x%g": "velocidad x%g",
        "brightness": "brillo",
        "contrast": "contraste",
        "saturation": "saturación",
        "screenshot saved in %s": "captura guardada en %s",
        "cannot take a screenshot: %s": "no se pudo capturar: %s",
        "Programme guide": "Guía de programas",
        "no guide data for this playlist": "sin programas en la guía para esta lista",
        "OK": "OK",
        "Cancel": "Cancelar",
        "MIT license": "Licencia MIT",
        "URL of the playlist (m3u):": "URL de la lista (m3u):",
        "Name:": "Nombre:",
        "About": "Acerca de",
        "About XVB...": "Acerca de XVB...",
        "Version": "Versión",
        "new version %s available": "nueva versión %s disponible",
        "new version %s available: Help > Download": "nueva versión %s disponible: Ayuda > Descargar",
        "Check for updates": "Buscar actualizaciones",
        "Donate": "Donar",
        "Download %s": "Descargar la %s",
        "Up to date": "Actualizada",
        "cannot check for updates: %s": "no puedo comprobar actualizaciones: %s",
        "up to date (%s)": "actualizada (%s)",
        "Remove %s": "Quitar %s",
        "removed %s": "quitada %s",
        "%s is already there": "%s ya está",
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
        "File": "Archivo", "Playlists": "Listas",
        "ready": "listo",
        "unnamed": "sin nombre",
        "single quality": "una sola calidad",
        "missing icons: %s": "faltan iconos: %s",
        "no playlist: put one in playlists/": "ninguna lista: pon una en playlists/",
        "loading %s...": "cargando %s...",
        "cannot read %s: %s": "no puedo leer %s: %s",
        "This DVD can't be played": "Este DVD no se puede reproducir",
        "Media": "Medios",
        "Playback": "Reproducción",
        "IPTV": "IPTV",
        "Help": "Ayuda",
        "Play / Pause": "Reproducir / Pausa",
        "Back 10 s": "Atrás 10 s",
        "Forward 10 s": "Adelante 10 s",
        "Previous": "Anterior",
        "Next": "Siguiente",
        "Add playlist URL": "Añadir URL de lista",
        "Open playlists folder": "Abrir carpeta de listas",
        "Reload playlists": "Recargar listas",
        "Add guide URL": "Añadir URL de guía",
        "Reload guide": "Recargar guía",
        "Recording": "Grabación",
        "Show list": "Mostrar lista",
        "Hide list": "Ocultar lista",
        "Show sidebar": "Mostrar barra lateral",
        "Hide sidebar": "Ocultar barra lateral",
        "%s - %d channels": "%s - %d canales",
        "Choose a playlist": "Elige una lista",
        "All files": "Todos los archivos",
        "Open file...": "Abrir archivo...", "Choose a file": "Elige un archivo",
        "Media files": "Archivos multimedia", "Open folder...": "Abrir carpeta...",
        "Download posters and covers": "Descargar pósters y portadas",
        "Film titles and genres": "Títulos y géneros de películas",
        "Download film titles and genres again in the new language?":
            "¿Descargar de nuevo títulos y géneros en el nuevo idioma?",
        "Download": "Descargar",
        "open a folder of films or music first": "abre primero una carpeta de películas o música",
        "downloading posters and covers... %d/%d": "descargando pósters y portadas... %d/%d",
        "%d posters and covers downloaded": "%d pósters y portadas descargados",
        "Choose a folder": "Elige una carpeta", "Imported media": "Medios importados",
        "Open": "Abrir", "%s - %d files": "%s - %d archivos",
        "The folder will be removed from the list. Files are not touched.":
            "La carpeta se quita de la lista. Los archivos no se tocan.",
        "The imported files will be removed from the list. Files are not touched.":
            "Los archivos importados se quitan de la lista. Los archivos no se tocan.",
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
        "Logos": "Logos", "Dots": "Puntos", "Border": "Borde",
        "Unmute": "Sonido", "Switch": "Cambiar", "Shuffle": "Aleatorio",
        "Open folder": "Abrir carpeta", "Reload": "Recargar", "Add URL": "Añadir URL",
        "Remove": "Quitar", "View": "Vista", "Hide channels": "Ocultar canales",
        "Show channels": "Mostrar canales", "Hide playlists": "Ocultar listas",
        "Show playlists": "Mostrar listas", "Language": "Idioma",
    },
    "fr": {
        "Audio CD": "CD audio",
        "Track %d": "Piste %d",
        "Set logo...": "Choisir le logo...",
        "Remove logo": "Retirer le logo",
        "Discs": "Disques",
        "This DVD is protected and can't be recorded": "Ce DVD est protégé et ne peut pas être enregistré",
        "Set cover...": "Choisir la pochette...",
        "Remove cover": "Retirer la pochette",
        "Choose an image": "Choisir une image",
        "Radio": "Radio",
        "Music streams": "Flux musicaux",
        "Film streams": "Flux de films",
        "Temporary": "Temporaires",
        "Save in:": "Enregistrer dans :",
        "Move to %s": "Déplacer vers %s",
        "Rename...": "Renommer...",
        "cannot rename %s: %s": "impossible de renommer %s : %s",
        "Opened URLs": "URL ouvertes",
        "The addresses will be removed from the list.": "Les adresses seront retirées de la liste.",
        "Open URL...": "Ouvrir une URL...",
        "Address of the stream or file:": "Adresse du flux ou du fichier :",
        "Load subtitles...": "Charger des sous-titres...",
        "Subtitle delay +100 ms": "Décalage sous-titres +100 ms",
        "Subtitle delay -100 ms": "Décalage sous-titres -100 ms",
        "Subtitles larger": "Sous-titres plus grands",
        "Subtitles smaller": "Sous-titres plus petits",
        "Reset subtitles": "Réinitialiser les sous-titres",
        "subtitles: %s": "sous-titres : %s",
        "subtitle delay %+.1fs": "décalage des sous-titres %+.1fs",
        "subtitle size %d%%": "sous-titres à %d%%",
        "nothing is playing": "rien n'est en lecture",
        "Recent": "Récents",
        "Clear recent": "Effacer les récents",
        "No repeat": "Ne pas répéter",
        "Repeat one": "Répéter un",
        "Repeat all": "Répéter tout",
        "until %s": "jusqu'à %s",
        "Delete": "Supprimer",
        "Delete %s": "Supprimer %s",
        "The guide will be removed from the list.": "Le guide sera retiré de la liste.",
        "%d recording(s) will be deleted from disk. This cannot be undone.": "%d enregistrement(s) seront supprimés du disque. Irréversible.",
        "The playlist will be removed from the list.": "La liste sera retirée.",
        "The file %s will be deleted from disk. This cannot be undone.": "Le fichier %s sera supprimé du disque. Irréversible.",
        "cannot delete %s: %s": "impossible de supprimer %s : %s",
        "%s - %d recordings": "%s - %d enregistrements",
        "Recordings": "Enregistrements",
        "Video": "Vidéo",
        "Speed x%g": "Vitesse x%g",
        "Aspect %s": "Format %s",
        "Fill screen (crop)": "Remplir l'écran (rogner)",
        "Deinterlace": "Désentrelacer",
        "Brightness +": "Luminosité +",
        "Brightness -": "Luminosité -",
        "Contrast +": "Contraste +",
        "Contrast -": "Contraste -",
        "Saturation +": "Saturation +",
        "Saturation -": "Saturation -",
        "Reset picture": "Réinitialiser l'image",
        "Screenshot": "Capture d'écran",
        "Always on top": "Toujours au premier plan",
        "Track: %s": "Piste : %s",
        "Subtitles off": "Sous-titres désactivés",
        "Subtitles: %s": "Sous-titres : %s",
        "Volume boost +50%": "Volume extra +50%",
        "Normalize loudness": "Normaliser le volume",
        "speed x%g": "vitesse x%g",
        "brightness": "luminosité",
        "contrast": "contraste",
        "saturation": "saturation",
        "screenshot saved in %s": "capture enregistrée dans %s",
        "cannot take a screenshot: %s": "capture impossible : %s",
        "Programme guide": "Grille des programmes",
        "no guide data for this playlist": "aucun programme dans le guide pour cette liste",
        "OK": "OK",
        "Cancel": "Annuler",
        "MIT license": "Licence MIT",
        "URL of the playlist (m3u):": "URL de la liste (m3u) :",
        "Name:": "Nom :",
        "About": "À propos",
        "About XVB...": "À propos de XVB...",
        "Version": "Version",
        "new version %s available": "nouvelle version %s disponible",
        "new version %s available: Help > Download": "nouvelle version %s disponible : Aide > Télécharger",
        "Check for updates": "Rechercher des mises à jour",
        "Donate": "Faire un don",
        "Download %s": "Télécharger la %s",
        "Up to date": "À jour",
        "cannot check for updates: %s": "impossible de vérifier les mises à jour : %s",
        "up to date (%s)": "à jour (%s)",
        "Remove %s": "Retirer %s",
        "removed %s": "%s retiré",
        "%s is already there": "%s est déjà là",
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
        "File": "Fichier", "Playlists": "Listes",
        "ready": "prêt",
        "unnamed": "sans nom",
        "single quality": "une seule qualité",
        "missing icons: %s": "icônes manquantes : %s",
        "no playlist: put one in playlists/": "aucune liste : mets-en une dans playlists/",
        "loading %s...": "chargement de %s...",
        "cannot read %s: %s": "impossible de lire %s : %s",
        "This DVD can't be played": "Ce DVD ne peut pas être lu",
        "Media": "Médias",
        "Playback": "Lecture",
        "IPTV": "IPTV",
        "Help": "Aide",
        "Play / Pause": "Lecture / Pause",
        "Back 10 s": "Reculer de 10 s",
        "Forward 10 s": "Avancer de 10 s",
        "Previous": "Précédent",
        "Next": "Suivant",
        "Add playlist URL": "Ajouter une URL de liste",
        "Open playlists folder": "Ouvrir le dossier des listes",
        "Reload playlists": "Recharger les listes",
        "Add guide URL": "Ajouter une URL de guide",
        "Reload guide": "Recharger le guide",
        "Recording": "Enregistrement",
        "Show list": "Afficher la liste",
        "Hide list": "Masquer la liste",
        "Show sidebar": "Afficher la barre latérale",
        "Hide sidebar": "Masquer la barre latérale",
        "%s - %d channels": "%s - %d chaînes",
        "Choose a playlist": "Choisir une liste",
        "All files": "Tous les fichiers",
        "Open file...": "Ouvrir un fichier...", "Choose a file": "Choisir un fichier",
        "Media files": "Fichiers multimédias", "Open folder...": "Ouvrir un dossier...",
        "Download posters and covers": "Télécharger affiches et pochettes",
        "Film titles and genres": "Titres et genres des films",
        "Download film titles and genres again in the new language?":
            "Télécharger à nouveau titres et genres dans la nouvelle langue ?",
        "Download": "Télécharger",
        "open a folder of films or music first": "ouvrez d'abord un dossier de films ou de musique",
        "downloading posters and covers... %d/%d": "téléchargement des affiches et pochettes... %d/%d",
        "%d posters and covers downloaded": "%d affiches et pochettes téléchargées",
        "Choose a folder": "Choisir un dossier", "Imported media": "Médias importés",
        "Open": "Ouvrir", "%s - %d files": "%s - %d fichiers",
        "The folder will be removed from the list. Files are not touched.":
            "Le dossier est retiré de la liste. Les fichiers ne sont pas touchés.",
        "The imported files will be removed from the list. Files are not touched.":
            "Les fichiers importés sont retirés de la liste. Les fichiers ne sont pas touchés.",
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
        "Logos": "Logos", "Dots": "Points", "Border": "Bordure",
        "Unmute": "Son", "Switch": "Basculer", "Shuffle": "Aléatoire",
        "Open folder": "Ouvrir le dossier", "Reload": "Recharger", "Add URL": "Ajouter une URL",
        "Remove": "Retirer", "View": "Affichage", "Hide channels": "Masquer les chaînes",
        "Show channels": "Afficher les chaînes", "Hide playlists": "Masquer les listes",
        "Show playlists": "Afficher les listes", "Language": "Langue",
    },
    "de": {
        "Audio CD": "Audio-CD",
        "Track %d": "Titel %d",
        "Set logo...": "Logo festlegen...",
        "Remove logo": "Logo entfernen",
        "Discs": "Datenträger",
        "This DVD is protected and can't be recorded": "Diese DVD ist geschützt und kann nicht aufgenommen werden",
        "Set cover...": "Cover festlegen...",
        "Remove cover": "Cover entfernen",
        "Choose an image": "Bild wählen",
        "Radio": "Radio",
        "Music streams": "Musik-Streams",
        "Film streams": "Film-Streams",
        "Temporary": "Temporär",
        "Save in:": "Speichern in:",
        "Move to %s": "Verschieben nach %s",
        "Rename...": "Umbenennen...",
        "cannot rename %s: %s": "%s kann nicht umbenannt werden: %s",
        "Opened URLs": "Geöffnete URLs",
        "The addresses will be removed from the list.": "Die Adressen werden aus der Liste entfernt.",
        "Open URL...": "URL öffnen...",
        "Address of the stream or file:": "Adresse des Streams oder der Datei:",
        "Load subtitles...": "Untertitel laden...",
        "Subtitle delay +100 ms": "Untertitel-Verzögerung +100 ms",
        "Subtitle delay -100 ms": "Untertitel-Verzögerung -100 ms",
        "Subtitles larger": "Untertitel größer",
        "Subtitles smaller": "Untertitel kleiner",
        "Reset subtitles": "Untertitel zurücksetzen",
        "subtitles: %s": "Untertitel: %s",
        "subtitle delay %+.1fs": "Untertitel-Verzögerung %+.1fs",
        "subtitle size %d%%": "Untertitel %d%%",
        "nothing is playing": "es wird nichts abgespielt",
        "Recent": "Zuletzt geöffnet",
        "Clear recent": "Liste leeren",
        "No repeat": "Nicht wiederholen",
        "Repeat one": "Eins wiederholen",
        "Repeat all": "Alle wiederholen",
        "until %s": "bis %s",
        "Delete": "Löschen",
        "Delete %s": "%s löschen",
        "The guide will be removed from the list.": "Der Programmführer wird aus der Liste entfernt.",
        "%d recording(s) will be deleted from disk. This cannot be undone.": "%d Aufnahme(n) werden von der Festplatte gelöscht. Das kann nicht rückgängig gemacht werden.",
        "The playlist will be removed from the list.": "Die Playlist wird aus der Liste entfernt.",
        "The file %s will be deleted from disk. This cannot be undone.": "Die Datei %s wird von der Festplatte gelöscht. Das kann nicht rückgängig gemacht werden.",
        "cannot delete %s: %s": "%s kann nicht gelöscht werden: %s",
        "%s - %d recordings": "%s - %d Aufnahmen",
        "Recordings": "Aufnahmen",
        "Video": "Video",
        "Speed x%g": "Geschwindigkeit x%g",
        "Aspect %s": "Seitenverhältnis %s",
        "Fill screen (crop)": "Bild füllen (zuschneiden)",
        "Deinterlace": "Deinterlacing",
        "Brightness +": "Helligkeit +",
        "Brightness -": "Helligkeit -",
        "Contrast +": "Kontrast +",
        "Contrast -": "Kontrast -",
        "Saturation +": "Sättigung +",
        "Saturation -": "Sättigung -",
        "Reset picture": "Bild zurücksetzen",
        "Screenshot": "Bildschirmfoto",
        "Always on top": "Immer im Vordergrund",
        "Track: %s": "Tonspur: %s",
        "Subtitles off": "Untertitel aus",
        "Subtitles: %s": "Untertitel: %s",
        "Volume boost +50%": "Lautstärke +50%",
        "Normalize loudness": "Lautstärke angleichen",
        "speed x%g": "Geschwindigkeit x%g",
        "brightness": "Helligkeit",
        "contrast": "Kontrast",
        "saturation": "Sättigung",
        "screenshot saved in %s": "Bildschirmfoto gespeichert in %s",
        "cannot take a screenshot: %s": "Bildschirmfoto nicht möglich: %s",
        "Programme guide": "Programmübersicht",
        "no guide data for this playlist": "keine Programmdaten für diese Playlist",
        "OK": "OK",
        "Cancel": "Abbrechen",
        "MIT license": "MIT-Lizenz",
        "URL of the playlist (m3u):": "URL der Playlist (m3u):",
        "Name:": "Name:",
        "About": "Über",
        "About XVB...": "Über XVB...",
        "Version": "Version",
        "new version %s available": "neue Version %s verfügbar",
        "new version %s available: Help > Download": "neue Version %s verfügbar: Hilfe > Herunterladen",
        "Check for updates": "Nach Updates suchen",
        "Donate": "Spenden",
        "Download %s": "%s herunterladen",
        "Up to date": "Aktuell",
        "cannot check for updates: %s": "Suche nach Updates nicht möglich: %s",
        "up to date (%s)": "aktuell (%s)",
        "Remove %s": "%s entfernen",
        "removed %s": "%s entfernt",
        "%s is already there": "%s ist schon vorhanden",
        "Record": "Aufnehmen",
        "Stop": "Stopp",
        "Record now": "Jetzt aufnehmen",
        "Stop recording": "Aufnahme beenden",
        "Schedule...": "Planen...",
        "Cancel schedule": "Planung abbrechen",
        "Open recordings folder": "Aufnahmeordner öffnen",
        "cannot record: %s": "Aufnahme nicht möglich: %s",
        "saved %s": "%s gespeichert",
        "REC until %s": "REC bis %s",
        "open the channel to record first": "öffne zuerst den Sender, der aufgenommen werden soll",
        "Schedule": "Planen",
        "Start (HH:MM):": "Beginn (HH:MM):",
        "End (HH:MM):": "Ende (HH:MM):",
        "time must be HH:MM": "die Zeit muss HH:MM sein",
        "scheduled: %s from %s to %s": "geplant: %s von %s bis %s",
        "schedule cancelled": "Planung abgebrochen",
        "Delay +100 ms": "Verzögerung +100 ms",
        "Delay -100 ms": "Verzögerung -100 ms",
        "Reset delay": "Verzögerung zurücksetzen",
        "audio delay %+.1fs": "Tonverzögerung %+.1fs",
        "guide loaded: %d channels, %d programmes": "Programmführer geladen: %d Sender, %d Sendungen",
        "not in the guide": "nicht im Programmführer",
        "Search channels": "Sender suchen",
        "File": "Datei",
        "Playlists": "Playlists",
        "ready": "bereit",
        "unnamed": "ohne Namen",
        "single quality": "nur eine Qualität",
        "missing icons: %s": "fehlende Symbole: %s",
        "no playlist: put one in playlists/": "keine Playlist: lege eine in playlists/ ab",
        "loading %s...": "lade %s...",
        "cannot read %s: %s": "%s kann nicht gelesen werden: %s",
        "This DVD can't be played": "Diese DVD kann nicht abgespielt werden",
        "Media": "Medien",
        "Playback": "Wiedergabe",
        "IPTV": "IPTV",
        "Help": "Hilfe",
        "Play / Pause": "Abspielen / Pause",
        "Back 10 s": "10 s zurück",
        "Forward 10 s": "10 s vor",
        "Previous": "Zurück",
        "Next": "Weiter",
        "Add playlist URL": "Playlist-URL hinzufügen",
        "Open playlists folder": "Playlist-Ordner öffnen",
        "Reload playlists": "Playlists neu laden",
        "Add guide URL": "Programmführer-URL hinzufügen",
        "Reload guide": "Programmführer neu laden",
        "Recording": "Aufnahme",
        "Show list": "Liste einblenden",
        "Hide list": "Liste ausblenden",
        "Show sidebar": "Seitenleiste einblenden",
        "Hide sidebar": "Seitenleiste ausblenden",
        "%s - %d channels": "%s - %d Sender",
        "Choose a playlist": "Playlist wählen",
        "All files": "Alle Dateien",
        "Open file...": "Datei öffnen...",
        "Choose a file": "Datei wählen",
        "Media files": "Mediendateien",
        "Open folder...": "Ordner öffnen...",
        "Download posters and covers": "Poster und Cover herunterladen",
        "Film titles and genres": "Filmtitel und Genres",
        "Download film titles and genres again in the new language?": "Filmtitel und Genres in der neuen Sprache erneut herunterladen?",
        "Download": "Herunterladen",
        "open a folder of films or music first": "öffne zuerst einen Ordner mit Filmen oder Musik",
        "downloading posters and covers... %d/%d": "lade Poster und Cover... %d/%d",
        "%d posters and covers downloaded": "%d Poster und Cover heruntergeladen",
        "Choose a folder": "Ordner wählen",
        "Imported media": "Importierte Medien",
        "Open": "Öffnen",
        "%s - %d files": "%s - %d Dateien",
        "The folder will be removed from the list. Files are not touched.": "Der Ordner wird aus der Liste entfernt. Die Dateien bleiben unberührt.",
        "The imported files will be removed from the list. Files are not touched.": "Die importierten Dateien werden aus der Liste entfernt. Die Dateien bleiben unberührt.",
        "%s does not look like an m3u playlist": "%s sieht nicht wie eine m3u-Playlist aus",
        "select the playlist to remove first": "wähle zuerst die Playlist, die entfernt werden soll",
        "this one lives in playlists/: remove it by moving the file": "diese liegt in playlists/: entferne sie, indem du die Datei verschiebst",
        "%s removed from favorites": "%s aus den Favoriten entfernt",
        "%s added to favorites": "%s zu den Favoriten hinzugefügt",
        "cannot write favorites: %s": "Favoriten können nicht gespeichert werden: %s",
        "looking for %s qualities...": "suche Qualitäten von %s...",
        "TV guide": "TV-Programm",
        "URL or file of the guide (XMLTV, .gz is fine):": "URL oder Datei des Programmführers (XMLTV, auch .gz):",
        "loading the guide...": "lade den Programmführer...",
        "guide not loaded: %s": "Programmführer nicht geladen: %s",
        "no TV guide set": "kein Programmführer eingestellt",
        "%s is not responding, nor are the next ones": "%s antwortet nicht, die nächsten auch nicht",
        "%s is not responding: skipping to the next": "%s antwortet nicht: weiter zum nächsten",
        "cannot open %s: %s": "%s kann nicht geöffnet werden: %s",
        "Pause": "Pause",
        "Resume": "Fortsetzen",
        "Favorite": "Favorit",
        "Fullscreen": "Vollbild",
        "quality": "Qualität",
        "Mute": "Stumm",
        "Logos": "Logos",
        "Dots": "Punkte",
        "Border": "Rand",
        "Unmute": "Ton an",
        "Switch": "Wechseln",
        "Shuffle": "Zufällig",
        "Open folder": "Ordner öffnen",
        "Reload": "Neu laden",
        "Add URL": "URL hinzufügen",
        "Remove": "Entfernen",
        "View": "Ansicht",
        "Hide channels": "Sender ausblenden",
        "Show channels": "Sender einblenden",
        "Hide playlists": "Playlists ausblenden",
        "Show playlists": "Playlists einblenden",
        "Language": "Sprache",
    },
    "pt": {
        "Audio CD": "CD de áudio",
        "Track %d": "Faixa %d",
        "Set logo...": "Definir logo...",
        "Remove logo": "Remover logo",
        "Discs": "Discos",
        "This DVD is protected and can't be recorded": "Este DVD é protegido e não pode ser gravado",
        "Set cover...": "Definir capa...",
        "Remove cover": "Remover capa",
        "Choose an image": "Escolha uma imagem",
        "Radio": "Rádio",
        "Music streams": "Streams de música",
        "Film streams": "Streams de filmes",
        "Temporary": "Temporários",
        "Save in:": "Salvar em:",
        "Move to %s": "Mover para %s",
        "Rename...": "Renomear...",
        "cannot rename %s: %s": "não foi possível renomear %s: %s",
        "Opened URLs": "URLs abertas",
        "The addresses will be removed from the list.": "Os endereços serão removidos da lista.",
        "Open URL...": "Abrir URL...",
        "Address of the stream or file:": "Endereço do stream ou do arquivo:",
        "Load subtitles...": "Carregar legendas...",
        "Subtitle delay +100 ms": "Atraso das legendas +100 ms",
        "Subtitle delay -100 ms": "Atraso das legendas -100 ms",
        "Subtitles larger": "Legendas maiores",
        "Subtitles smaller": "Legendas menores",
        "Reset subtitles": "Redefinir legendas",
        "subtitles: %s": "legendas: %s",
        "subtitle delay %+.1fs": "atraso das legendas %+.1fs",
        "subtitle size %d%%": "legendas em %d%%",
        "nothing is playing": "nada está sendo reproduzido",
        "Recent": "Recentes",
        "Clear recent": "Limpar recentes",
        "No repeat": "Não repetir",
        "Repeat one": "Repetir um",
        "Repeat all": "Repetir todos",
        "Audio": "Áudio",
        "until %s": "até %s",
        "Delete": "Excluir",
        "Delete %s": "Excluir %s",
        "The guide will be removed from the list.": "O guia será removido da lista.",
        "%d recording(s) will be deleted from disk. This cannot be undone.": "%d gravação(ões) serão excluídas do disco. Isso não pode ser desfeito.",
        "The playlist will be removed from the list.": "A playlist será removida da lista.",
        "The file %s will be deleted from disk. This cannot be undone.": "O arquivo %s será excluído do disco. Isso não pode ser desfeito.",
        "cannot delete %s: %s": "não foi possível excluir %s: %s",
        "%s - %d recordings": "%s - %d gravações",
        "Recordings": "Gravações",
        "Video": "Vídeo",
        "Speed x%g": "Velocidade x%g",
        "Aspect %s": "Proporção %s",
        "Fill screen (crop)": "Preencher a tela (cortar)",
        "Deinterlace": "Desentrelaçar",
        "Brightness +": "Brilho +",
        "Brightness -": "Brilho -",
        "Contrast +": "Contraste +",
        "Contrast -": "Contraste -",
        "Saturation +": "Saturação +",
        "Saturation -": "Saturação -",
        "Reset picture": "Redefinir imagem",
        "Screenshot": "Captura de tela",
        "Always on top": "Sempre no topo",
        "Track: %s": "Faixa: %s",
        "Subtitles off": "Legendas desativadas",
        "Subtitles: %s": "Legendas: %s",
        "Volume boost +50%": "Volume extra +50%",
        "Normalize loudness": "Normalizar volume",
        "speed x%g": "velocidade x%g",
        "brightness": "brilho",
        "contrast": "contraste",
        "saturation": "saturação",
        "screenshot saved in %s": "captura salva em %s",
        "cannot take a screenshot: %s": "não foi possível capturar a tela: %s",
        "Programme guide": "Guia de programação",
        "no guide data for this playlist": "sem dados de guia para esta playlist",
        "OK": "OK",
        "Cancel": "Cancelar",
        "MIT license": "Licença MIT",
        "URL of the playlist (m3u):": "URL da playlist (m3u):",
        "Name:": "Nome:",
        "About": "Sobre",
        "About XVB...": "Sobre o XVB...",
        "Version": "Versão",
        "new version %s available": "nova versão %s disponível",
        "new version %s available: Help > Download": "nova versão %s disponível: Ajuda > Baixar",
        "Check for updates": "Verificar atualizações",
        "Donate": "Doar",
        "Download %s": "Baixar %s",
        "Up to date": "Atualizado",
        "cannot check for updates: %s": "não foi possível verificar atualizações: %s",
        "up to date (%s)": "atualizado (%s)",
        "Remove %s": "Remover %s",
        "removed %s": "%s removido",
        "%s is already there": "%s já está na lista",
        "Record": "Gravar",
        "Stop": "Parar",
        "Record now": "Gravar agora",
        "Stop recording": "Parar gravação",
        "Schedule...": "Agendar...",
        "Cancel schedule": "Cancelar agendamento",
        "Open recordings folder": "Abrir pasta de gravações",
        "cannot record: %s": "não foi possível gravar: %s",
        "saved %s": "%s salvo",
        "REC until %s": "REC até %s",
        "open the channel to record first": "abra primeiro o canal a ser gravado",
        "Schedule": "Agendar",
        "Start (HH:MM):": "Início (HH:MM):",
        "End (HH:MM):": "Fim (HH:MM):",
        "time must be HH:MM": "o horário deve ser HH:MM",
        "scheduled: %s from %s to %s": "agendado: %s das %s às %s",
        "schedule cancelled": "agendamento cancelado",
        "Delay +100 ms": "Atraso +100 ms",
        "Delay -100 ms": "Atraso -100 ms",
        "Reset delay": "Redefinir atraso",
        "audio delay %+.1fs": "atraso de áudio %+.1fs",
        "guide loaded: %d channels, %d programmes": "guia carregado: %d canais, %d programas",
        "not in the guide": "fora do guia",
        "Search channels": "Buscar canais",
        "File": "Arquivo",
        "Playlists": "Playlists",
        "ready": "pronto",
        "unnamed": "sem nome",
        "single quality": "qualidade única",
        "missing icons: %s": "ícones ausentes: %s",
        "no playlist: put one in playlists/": "nenhuma playlist: coloque uma em playlists/",
        "loading %s...": "carregando %s...",
        "cannot read %s: %s": "não foi possível ler %s: %s",
        "This DVD can't be played": "Este DVD não pode ser reproduzido",
        "Media": "Mídia",
        "Playback": "Reprodução",
        "IPTV": "IPTV",
        "Help": "Ajuda",
        "Play / Pause": "Reproduzir / Pausar",
        "Back 10 s": "Voltar 10 s",
        "Forward 10 s": "Avançar 10 s",
        "Previous": "Anterior",
        "Next": "Próximo",
        "Add playlist URL": "Adicionar URL de playlist",
        "Open playlists folder": "Abrir pasta de playlists",
        "Reload playlists": "Recarregar playlists",
        "Add guide URL": "Adicionar URL de guia",
        "Reload guide": "Recarregar guia",
        "Recording": "Gravação",
        "Show list": "Mostrar lista",
        "Hide list": "Ocultar lista",
        "Show sidebar": "Mostrar barra lateral",
        "Hide sidebar": "Ocultar barra lateral",
        "%s - %d channels": "%s - %d canais",
        "Choose a playlist": "Escolha uma playlist",
        "All files": "Todos os arquivos",
        "Open file...": "Abrir arquivo...",
        "Choose a file": "Escolha um arquivo",
        "Media files": "Arquivos de mídia",
        "Open folder...": "Abrir pasta...",
        "Download posters and covers": "Baixar pôsteres e capas",
        "Film titles and genres": "Títulos e gêneros dos filmes",
        "Download film titles and genres again in the new language?": "Baixar de novo títulos e gêneros dos filmes no novo idioma?",
        "Download": "Baixar",
        "open a folder of films or music first": "abra primeiro uma pasta de filmes ou músicas",
        "downloading posters and covers... %d/%d": "baixando pôsteres e capas... %d/%d",
        "%d posters and covers downloaded": "%d pôsteres e capas baixados",
        "Choose a folder": "Escolha uma pasta",
        "Imported media": "Mídia importada",
        "Open": "Abrir",
        "%s - %d files": "%s - %d arquivos",
        "The folder will be removed from the list. Files are not touched.": "A pasta será removida da lista. Os arquivos não são alterados.",
        "The imported files will be removed from the list. Files are not touched.": "Os arquivos importados serão removidos da lista. Os arquivos não são alterados.",
        "%s does not look like an m3u playlist": "%s não parece uma playlist m3u",
        "select the playlist to remove first": "selecione primeiro a playlist a remover",
        "this one lives in playlists/: remove it by moving the file": "esta fica em playlists/: remova-a movendo o arquivo",
        "%s removed from favorites": "%s removido dos favoritos",
        "%s added to favorites": "%s adicionado aos favoritos",
        "cannot write favorites: %s": "não foi possível salvar os favoritos: %s",
        "looking for %s qualities...": "procurando qualidades de %s...",
        "TV guide": "Guia de TV",
        "URL or file of the guide (XMLTV, .gz is fine):": "URL ou arquivo do guia (XMLTV, .gz também serve):",
        "loading the guide...": "carregando o guia...",
        "guide not loaded: %s": "guia não carregado: %s",
        "no TV guide set": "nenhum guia de TV definido",
        "%s is not responding, nor are the next ones": "%s não responde, nem os próximos",
        "%s is not responding: skipping to the next": "%s não responde: indo para o próximo",
        "cannot open %s: %s": "não foi possível abrir %s: %s",
        "Pause": "Pausar",
        "Resume": "Continuar",
        "Favorite": "Favorito",
        "Fullscreen": "Tela cheia",
        "quality": "qualidade",
        "Mute": "Mudo",
        "Logos": "Logos",
        "Dots": "Pontos",
        "Border": "Borda",
        "Unmute": "Com som",
        "Switch": "Alternar",
        "Shuffle": "Aleatório",
        "Open folder": "Abrir pasta",
        "Reload": "Recarregar",
        "Add URL": "Adicionar URL",
        "Remove": "Remover",
        "View": "Exibir",
        "Hide channels": "Ocultar canais",
        "Show channels": "Mostrar canais",
        "Hide playlists": "Ocultar playlists",
        "Show playlists": "Mostrar playlists",
        "Language": "Idioma",
    },
    "ru": {
        "Audio CD": "Аудио-CD",
        "Track %d": "Трек %d",
        "Set logo...": "Задать логотип...",
        "Remove logo": "Убрать логотип",
        "Discs": "Диски",
        "This DVD is protected and can't be recorded": "Этот DVD защищён, запись невозможна",
        "Set cover...": "Задать обложку...",
        "Remove cover": "Убрать обложку",
        "Choose an image": "Выберите изображение",
        "Radio": "Радио",
        "Music streams": "Музыкальные потоки",
        "Film streams": "Видеопотоки",
        "Temporary": "Временные",
        "Save in:": "Сохранить в:",
        "Move to %s": "Переместить в %s",
        "Rename...": "Переименовать...",
        "cannot rename %s: %s": "не удалось переименовать %s: %s",
        "Opened URLs": "Открытые URL",
        "The addresses will be removed from the list.": "Адреса будут удалены из списка.",
        "Open URL...": "Открыть URL...",
        "Address of the stream or file:": "Адрес потока или файла:",
        "Load subtitles...": "Загрузить субтитры...",
        "Subtitle delay +100 ms": "Задержка субтитров +100 мс",
        "Subtitle delay -100 ms": "Задержка субтитров -100 мс",
        "Subtitles larger": "Субтитры крупнее",
        "Subtitles smaller": "Субтитры мельче",
        "Reset subtitles": "Сбросить субтитры",
        "subtitles: %s": "субтитры: %s",
        "subtitle delay %+.1fs": "задержка субтитров %+.1f с",
        "subtitle size %d%%": "размер субтитров %d%%",
        "nothing is playing": "ничего не воспроизводится",
        "Recent": "Недавние",
        "Clear recent": "Очистить недавние",
        "No repeat": "Без повтора",
        "Repeat one": "Повторять один",
        "Repeat all": "Повторять все",
        "Audio": "Аудио",
        "until %s": "до %s",
        "Delete": "Удалить",
        "Delete %s": "Удалить %s",
        "The guide will be removed from the list.": "Телегид будет удалён из списка.",
        "%d recording(s) will be deleted from disk. This cannot be undone.": "Записей будет удалено с диска: %d. Это действие нельзя отменить.",
        "The playlist will be removed from the list.": "Плейлист будет удалён из списка.",
        "The file %s will be deleted from disk. This cannot be undone.": "Файл %s будет удалён с диска. Это действие нельзя отменить.",
        "cannot delete %s: %s": "не удалось удалить %s: %s",
        "%s - %d recordings": "%s - записей: %d",
        "Recordings": "Записи",
        "Video": "Видео",
        "Speed x%g": "Скорость x%g",
        "Aspect %s": "Пропорции %s",
        "Fill screen (crop)": "Заполнить экран (обрезать)",
        "Deinterlace": "Деинтерлейсинг",
        "Brightness +": "Яркость +",
        "Brightness -": "Яркость -",
        "Contrast +": "Контраст +",
        "Contrast -": "Контраст -",
        "Saturation +": "Насыщенность +",
        "Saturation -": "Насыщенность -",
        "Reset picture": "Сбросить изображение",
        "Screenshot": "Снимок экрана",
        "Always on top": "Поверх всех окон",
        "Track: %s": "Дорожка: %s",
        "Subtitles off": "Без субтитров",
        "Subtitles: %s": "Субтитры: %s",
        "Volume boost +50%": "Усиление громкости +50%",
        "Normalize loudness": "Выравнивать громкость",
        "speed x%g": "скорость x%g",
        "brightness": "яркость",
        "contrast": "контраст",
        "saturation": "насыщенность",
        "screenshot saved in %s": "снимок сохранён в %s",
        "cannot take a screenshot: %s": "не удалось сделать снимок: %s",
        "Programme guide": "Программа передач",
        "no guide data for this playlist": "нет данных телегида для этого плейлиста",
        "OK": "OK",
        "Cancel": "Отмена",
        "MIT license": "Лицензия MIT",
        "URL of the playlist (m3u):": "URL плейлиста (m3u):",
        "Name:": "Название:",
        "About": "О программе",
        "About XVB...": "О программе XVB...",
        "Version": "Версия",
        "new version %s available": "доступна новая версия %s",
        "new version %s available: Help > Download": "доступна новая версия %s: Справка > Скачать",
        "Check for updates": "Проверить обновления",
        "Donate": "Поддержать",
        "Download %s": "Скачать %s",
        "Up to date": "Актуальная версия",
        "cannot check for updates: %s": "не удалось проверить обновления: %s",
        "up to date (%s)": "актуальная версия (%s)",
        "Remove %s": "Удалить %s",
        "removed %s": "удалено: %s",
        "%s is already there": "%s уже есть",
        "Record": "Запись",
        "Stop": "Стоп",
        "Record now": "Записать сейчас",
        "Stop recording": "Остановить запись",
        "Schedule...": "Запланировать...",
        "Cancel schedule": "Отменить план",
        "Open recordings folder": "Открыть папку записей",
        "cannot record: %s": "не удалось записать: %s",
        "saved %s": "сохранено: %s",
        "REC until %s": "REC до %s",
        "open the channel to record first": "сначала откройте канал для записи",
        "Schedule": "Запланировать",
        "Start (HH:MM):": "Начало (ЧЧ:ММ):",
        "End (HH:MM):": "Конец (ЧЧ:ММ):",
        "time must be HH:MM": "время должно быть в формате ЧЧ:ММ",
        "scheduled: %s from %s to %s": "запланировано: %s с %s до %s",
        "schedule cancelled": "план отменён",
        "Delay +100 ms": "Задержка +100 мс",
        "Delay -100 ms": "Задержка -100 мс",
        "Reset delay": "Сбросить задержку",
        "audio delay %+.1fs": "задержка звука %+.1f с",
        "guide loaded: %d channels, %d programmes": "телегид загружен: каналов %d, передач %d",
        "not in the guide": "нет в телегиде",
        "Search channels": "Поиск каналов",
        "File": "Файл",
        "Playlists": "Плейлисты",
        "ready": "готово",
        "unnamed": "без названия",
        "single quality": "одно качество",
        "missing icons: %s": "нет значков: %s",
        "no playlist: put one in playlists/": "нет плейлиста: положите его в playlists/",
        "loading %s...": "загрузка %s...",
        "cannot read %s: %s": "не удалось прочитать %s: %s",
        "This DVD can't be played": "Этот DVD не может быть воспроизведён",
        "Media": "Медиа",
        "Playback": "Плеер",
        "IPTV": "IPTV",
        "Help": "Справка",
        "Play / Pause": "Пуск / Пауза",
        "Back 10 s": "Назад 10 с",
        "Forward 10 s": "Вперёд 10 с",
        "Previous": "Предыдущий",
        "Next": "Следующий",
        "Add playlist URL": "Добавить URL плейлиста",
        "Open playlists folder": "Открыть папку плейлистов",
        "Reload playlists": "Обновить плейлисты",
        "Add guide URL": "Добавить URL телегида",
        "Reload guide": "Обновить телегид",
        "Recording": "Запись",
        "Show list": "Показать список",
        "Hide list": "Скрыть список",
        "Show sidebar": "Показать боковую панель",
        "Hide sidebar": "Скрыть боковую панель",
        "%s - %d channels": "%s - каналов: %d",
        "Choose a playlist": "Выберите плейлист",
        "All files": "Все файлы",
        "Open file...": "Открыть файл...",
        "Choose a file": "Выберите файл",
        "Media files": "Медиафайлы",
        "Open folder...": "Открыть папку...",
        "Download posters and covers": "Скачать постеры и обложки",
        "Film titles and genres": "Названия и жанры фильмов",
        "Download film titles and genres again in the new language?": "Скачать названия и жанры фильмов заново на новом языке?",
        "Download": "Скачать",
        "open a folder of films or music first": "сначала откройте папку с фильмами или музыкой",
        "downloading posters and covers... %d/%d": "загрузка постеров и обложек... %d/%d",
        "%d posters and covers downloaded": "скачано постеров и обложек: %d",
        "Choose a folder": "Выберите папку",
        "Imported media": "Импортированные файлы",
        "Open": "Открыть",
        "%s - %d files": "%s - файлов: %d",
        "The folder will be removed from the list. Files are not touched.": "Папка будет удалена из списка. Сами файлы не затрагиваются.",
        "The imported files will be removed from the list. Files are not touched.": "Импортированные файлы будут удалены из списка. Сами файлы не затрагиваются.",
        "%s does not look like an m3u playlist": "%s не похож на плейлист m3u",
        "select the playlist to remove first": "сначала выберите плейлист для удаления",
        "this one lives in playlists/: remove it by moving the file": "он лежит в playlists/: удалите его, переместив файл",
        "%s removed from favorites": "%s удалён из избранного",
        "%s added to favorites": "%s добавлен в избранное",
        "cannot write favorites: %s": "не удалось сохранить избранное: %s",
        "looking for %s qualities...": "поиск вариантов качества для %s...",
        "TV guide": "Телегид",
        "URL or file of the guide (XMLTV, .gz is fine):": "URL или файл телегида (XMLTV, можно .gz):",
        "loading the guide...": "загрузка телегида...",
        "guide not loaded: %s": "телегид не загружен: %s",
        "no TV guide set": "телегид не задан",
        "%s is not responding, nor are the next ones": "%s не отвечает, как и следующие",
        "%s is not responding: skipping to the next": "%s не отвечает: переход к следующему",
        "cannot open %s: %s": "не удалось открыть %s: %s",
        "Pause": "Пауза",
        "Resume": "Продолжить",
        "Favorite": "Избранное",
        "Fullscreen": "Полный экран",
        "quality": "качество",
        "Mute": "Без звука",
        "Logos": "Логотипы",
        "Dots": "Точки",
        "Border": "Полоса",
        "Unmute": "Со звуком",
        "Switch": "Назад к каналу",
        "Shuffle": "Перемешать",
        "Open folder": "Открыть папку",
        "Reload": "Обновить",
        "Add URL": "Добавить URL",
        "Remove": "Удалить",
        "View": "Вид",
        "Hide channels": "Скрыть каналы",
        "Show channels": "Показать каналы",
        "Hide playlists": "Скрыть плейлисты",
        "Show playlists": "Показать плейлисты",
        "Language": "Язык",
    },
}


def lingua_sistema():
    """La lingua del sistema, se XVB ce l'ha; se no l'inglese. Nell'ordine
    in cui la cerca gettext: LANGUAGE (anche piu' d'una), LC_ALL,
    LC_MESSAGES, LANG."""
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        codici = [v.split(".")[0].split("@")[0].split("_")[0].lower()
                  for v in os.environ.get(var, "").split(":")]
        codici = [c for c in codici if c and c not in ("c", "posix")]
        if not codici:
            continue                # non impostata: si guarda la prossima
        # la prima impostata decide: la prima lingua che XVB ha, se no inglese
        return next((c for c in codici if c in dict(LINGUE)), "en")
    return "en"


def _(testo):
    """Il testo nella lingua scelta; se manca, resta l'inglese."""
    return TESTI.get(LINGUA, {}).get(testo, testo)


VELOCITA = (1, 1.25, 1.5, 2, 0.5, 0.75)     # il giro del bottone x1
PROPORZIONI = (("Auto", "-1"), ("16:9", "16:9"), ("4:3", "4:3"), ("21:9", "21:9"))

# le barre laterali partono sempre larghe cosi' (trascinandole si cambiano,
# ma solo fino alla chiusura)
LARGA_BARRA = 220
RIDOTTE = 18       # le icone della barra di destra: lato in px (il riquadro resta 24)
IN_RIPRODUZIONE = "#c0c7d0"   # il fondo fisso della riga in riproduzione (argento)
PREFERITI = "favorite"     # la cartella dei preferiti, in playlists/
# i preferiti, per ora spenti: niente cuore e niente cartella nella barra
# (i file in playlists/favorite/ restano dove sono). True per riaccenderli
PREFERITI_ACCESI = False
PREF_IPTV = "iptv"         # dentro: iptv.m3u per i canali, music.m3u per la
PREF_MUSICA = "music"      # musica, video.m3u per i video
PREF_VIDEO = "video"
AUDIO = ("mp3", "flac", "ogg", "opus", "m4a", "aac", "wav", "wma", "ape", "mka")


def tipo_preferiti(url):
    """In quale lista dei preferiti va una cosa: iptv, music o video."""
    # i file sul disco per quello che sono, musica o video; quello che
    # viene da una lista IPTV (anche un .mp4 in rete) e' IPTV
    if not (os.path.isabs(url) and e_media(url)):
        return PREF_IPTV
    return PREF_MUSICA if os.path.splitext(url)[1][1:].lower() in AUDIO else PREF_VIDEO
ROSSO = "#ff453a"          # il suo colore: solo suo, le altre cartelle no
PALLINI = ("#ff5257", "#ff9f0a", "#ffd60a", "#30d158", "#0a84ff",
           "#bf5af2", "#ff375f", "#64d2ff")       # niente grigio: e' per l'EPG
# i file (musica, video) prendono un colore a testa, in ordine di posto
# nell'elenco, da questa scala: un caldo e un freddo alternati; si
# ripete solo dopo 16 file
SCALA_FILE = ("#ff5257", "#30d158", "#ff7f11", "#42a0ff",   # rosso, verde, arancio, blu
              "#ffd60a", "#bf5af2", "#ff6fae", "#64d2ff",   # giallo, viola, rosa, celeste
              "#c19272", "#66e3b0", "#d4af37", "#9190f9",   # marrone, menta, oro, indaco
              "#e23179", "#b4e04a", "#ff9670", "#c0c7d0")   # magenta, lime, corallo, argento
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
ZEBRA = "#252525"          # bianco 5%: una riga si' e una no nell'elenco
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
NOMI_URL = {}                   # url -> nome dato dall'utente (dal config)


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


CACHE_LISTE = os.path.join(CASA, ".cache", "xvb", "liste")
LISTA_VECCHIA = 6 * 3600        # una lista da url si riscarica dopo sei ore


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


def leggi_lista(f, solo_cache=False):
    """I canali di una lista: (nome, indirizzo, tvg-id o None). Il tvg-id
    serve a trovare il canale nella guida. Se f e' un url si legge la
    copia in cache (scaricandola se serve; con solo_cache mai)."""
    if re.match(r"https?://", f, re.I):
        if solo_cache:
            f = os.path.join(CACHE_LISTE, hashlib.md5(f.encode()).hexdigest() + ".m3u")
            if not os.path.isfile(f):
                return []
        else:
            f = copia_lista_url(f)
    righe = open(f, encoding="utf-8", errors="ignore").read().splitlines()
    fuori, nome, ide, logo = [], None, None, None
    for riga in righe:
        riga = riga.strip()
        if riga.startswith("#EXTINF"):
            nome = riga.split(",", 1)[-1].strip() or _("unnamed")
            m = re.search(r'tvg-id="([^"]*)"', riga)
            ide = m.group(1).strip() if m else None
            m = re.search(r'tvg-logo="([^"]+)"', riga)
            logo = m.group(1).strip() if m else None
        elif riga and not riga.startswith("#"):
            fuori.append((nome or riga, riga, ide or None))
            if logo:
                LOGHI[riga] = logo          # il logo del canale, per il colore
            nome, ide, logo = None, None, None
    return fuori


LOGHI = {}                      # indirizzo canale -> url del logo
CACHE_LOGHI = os.path.join(CASA, ".cache", "xvb", "loghi")
# le copertine scelte a mano per gli indirizzi (radio, stream): restano
COPERTINE_URL = os.path.join(CASA, ".config", "xvb", "copertine")
IMMAGINI = ("jpg", "jpeg", "png", "webp", "bmp")


def copertina_url(u):
    return os.path.join(COPERTINE_URL, hashlib.md5(u.encode("utf-8")).hexdigest() + ".jpg")
FILE_COLORI = os.path.join(CASA, ".cache", "xvb", "colori.json")
FILE_TAG = os.path.join(CASA, ".cache", "xvb", "tag.json")
RIGA_ELENCO = 44                # due righe: nome e, sotto, programma o tag
LOGO_L, LOGO_A = 56, 44         # logo / copertina nella colonna a sinistra dell'elenco
COLONNA = LOGO_L + 4            # la colonna dei loghi: fuori dalle righe, alta quanto la voce
CACHE_ANTEPRIME = os.path.join(CASA, ".cache", "xvb", "anteprime")


def copertina_dentro(f):
    """La copertina incorporata in un brano (mp3, flac, m4a, ogg...), come
    bytes; None se non c'e'."""
    if not HA_TAG:
        return None
    try:
        t = mutagen.File(f)
        if t is None:
            return None
        if getattr(t, "pictures", None):            # flac
            return t.pictures[0].data
        if t.tags:
            for k in t.tags.keys():
                if str(k).startswith("APIC"):       # mp3
                    return t.tags[k].data
            if "covr" in t.tags:                    # m4a
                return bytes(t.tags["covr"][0])
            if "metadata_block_picture" in t.tags:  # ogg / opus
                import base64
                from mutagen.flac import Picture
                return Picture(base64.b64decode(t.tags["metadata_block_picture"][0])).data
    except Exception:
        pass
    return None


TMDB = "https://api.themoviedb.org/3"
# la chiave dell'app (token di sola lettura): vale per tutti
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
    if " - " in titolo:                 # 'Artista - Titolo'
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
                if not artista:                 # .../Artista/Album/
                    artista = nonno.replace("_", " ").strip()
            elif not anno and not artista:
                # una cartella sola sotto quella aggiunta (Music/Litfiba/):
                # e' l'artista, l'album non si sa (si cerchera' il brano).
                # Con l'anno nel nome (Music/Terremoto (1993)/) resta album
                artista, album = album, ""
    if artista and album.lower() == artista.lower():
        album = ""                      # .../Artista/Artista - Titolo: e' la cartella dell'artista
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
            # prima gli album veri, poi i piu' vecchi
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


def anteprima_di(f):
    """L'immagine per un file nell'elenco, salvata in cache: per la musica
    la copertina (dentro al file, o cover/folder.jpg nella cartella); per
    i video un'immagine con lo stesso nome o poster/folder.jpg, se no un
    fotogramma preso con ffmpeg. Torna il percorso del file in cache, o
    None."""
    cartella, base = os.path.split(f)
    radice = os.path.splitext(base)[0]
    audio = os.path.splitext(f)[1][1:].lower() in AUDIO
    # prima un'immagine accanto al file (si guarda ogni volta: un poster
    # messo dopo vale subito); la cache e' per percorso e data di quella
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
            # un fotogramma dal video: a 30 s, o a 1 s se e' piu' corto
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
        open(fuori, "wb").close()           # vuoto = provato, niente da fare
    except Exception:
        pass
    return None
CARATTERE = "DejaVu Sans"       # il carattere dell'elenco, deciso qui e non dal sistema
NOME_PX = 13                    # il nome del canale / il titolo, in pixel
SOTTO_PX = 12                   # il sottotitolo (programma o artista), in pixel


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
        dati = urlopen(r, timeout=10).read()   # prima si scarica tutto, poi si scrive
        with open(f, "wb") as o:
            o.write(dati)
    try:
        im = Image.open(f).convert("RGBA")
    except Exception:
        os.remove(f)                        # non e' un'immagine: via, si riprova
        raise
    im.thumbnail((48, 48))
    secchi = {}
    for r, g, b, a in im.getdata():
        if a < 128:
            continue
        h, l, sa = colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)
        if sa < 0.35 or l < 0.12 or l > 0.92:
            continue                        # grigi, neri e bianchi non contano
        k = int(h * 12) % 12                # dodici tinte
        tot = secchi.setdefault(k, [0, 0.0, 0.0, 0.0])
        tot[0] += 1
        tot[1] += h
        tot[2] += l
        tot[3] += sa
    if not secchi:
        return None
    n, h, l, sa = max(secchi.values(), key=lambda t: t[0])
    h, l, sa = h / n, l / n, sa / n
    # luce e saturazione del logo, tenute in una fascia che si veda sullo
    # scuro senza sbiadire; solo i blu, che vengono cupi, si alzano un po'
    l = min(0.72, max(0.55, l))
    sa = max(0.75, sa)
    if 0.55 <= h <= 0.75:
        l = max(l, 0.63)
    r, g, b = colorsys.hls_to_rgb(h, l, sa)
    return "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))


COLORI_VERSIONE = 4             # si alza quando cambia il calcolo: i vecchi si rifanno


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
    """L'immagine accanto al nome: per le playlist IPTV icone/tv.png, per il
    resto (guide, registrazioni) e se tv.png manca icone/default.png.
    Torna il percorso, o None."""
    nomi = (["tv"] if dove else []) + ["default"]
    for n in nomi:
        for est in (".png", ".jpg", ".jpeg", ".gif"):
            f = os.path.join(QUI, "icone", n + est)
            if os.path.isfile(f):
                return f
    return None


def carica_logo(f, larga, alta, dentro=None):
    """Un'immagine come immagine Tk, dentro un riquadro fisso, centrata.
    Con `dentro` (px) l'immagine e' piu' piccola del riquadro, che resta
    quello (cosi' resta allineata alle altre). None se non si legge."""
    try:
        if HA_PIL:
            im = Image.open(f).convert("RGBA")
            k = min((dentro or larga) / float(max(1, im.width)),
                    (dentro or alta) / float(max(1, im.height)))
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
    # il corpo con la linguetta in alto a sinistra
    d.line([(3 * u, 19 * u), (3 * u, 5 * u), (10 * u, 5 * u), (12 * u, 7.5 * u),
            (21 * u, 7.5 * u), (21 * u, 19 * u), (3 * u, 19 * u)],
           fill=colore, width=sp, joint="curve")
    if aperta:
        # la falda davanti, aperta: parte piu' in basso e si allarga
        d.line([(3 * u, 19 * u), (6 * u, 11.5 * u), (23 * u, 11.5 * u),
                (20 * u, 19 * u)], fill=colore, width=sp, joint="curve")
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
    d.ellipse((3 * u, 3 * u, 21 * u, 21 * u), outline=colore, width=sp)   # la sfera
    d.ellipse((8 * u, 3 * u, 16 * u, 21 * u), outline=colore, width=sp)   # il meridiano
    d.line([(3 * u, 12 * u), (21 * u, 12 * u)], fill=colore, width=sp)     # l'equatore
    d.line([(5 * u, 7.5 * u), (19 * u, 7.5 * u)], fill=colore, width=sp)   # i paralleli
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
    d.ellipse((3 * u, 3 * u, 21 * u, 21 * u), outline=colore, width=sp)       # il disco
    d.ellipse((9.5 * u, 9.5 * u, 14.5 * u, 14.5 * u), outline=colore, width=sp)  # il buco
    d.arc((6 * u, 6 * u, 18 * u, 18 * u), 200, 250, fill=colore, width=int(1.2 * u))  # il riflesso
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
            for i in range(200):                    # 200 settori sparsi nel file
                h.seek((settori * i // 200) * 2048)
                s = h.read(2048)
                if len(s) < 2048 or s[:4] != b"\x00\x00\x01\xba" or (s[4] & 0xC0) != 0x40:
                    continue                        # non e' un pack MPEG-2
                p = 14 + (s[13] & 7)
                if s[p:p + 3] != b"\x00\x00\x01" or not (s[p + 3] == 0xBD or 0xC0 <= s[p + 3] <= 0xEF):
                    continue                        # pacchetti di navigazione: mai cifrati
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


# cartelle e globi: un caldo e un freddo, sempre alternati, rossi esclusi
# (il rosso e' dei preferiti) e niente grigio
CALDI = ("#ff9f0a", "#ffd60a", "#ffb340")               # arancio, giallo, ambra
FREDDI = ("#30d158", "#0a84ff", "#bf5af2", "#64d2ff")   # verde, blu, viola, celeste
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
        # vuoti: via il file; e la cartella, se non resta l'altro elenco
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
        for nome, url, ide in canali:
            o.write("#EXTINF:-1%s,%s\n%s\n" % (
                ' tvg-id="%s"' % ide if ide else "", nome, url))


CACHE_EPG = os.path.join(CASA, ".cache", "xvb", "epg")
CACHE_VARIANTE = os.path.join(CASA, ".cache", "xvb")
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
    if not n and len(pezzi) > 1:        # es. .../film.ism/.m3u8: il pezzo prima
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
# la porta per tenere una XVB sola (le altre le passano i file)
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
        if flag & 4:                              # FEXTRA
            p += 2 + int.from_bytes(testa[p:p + 2], "little")
        if flag & 8:                              # FNAME
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
        # l'ultimo pezzo dell'url: /epg/uk.xml.gz -> uk.xml, /gzip -> gzip;
        # senza percorso resta il dominio
        nome = os.path.basename(u.path.rstrip("/"))
        if not nome:
            return dominio
    else:
        nome = os.path.basename(dove.rstrip("/")) or dove
    nome = re.sub(r"\.gz$", "", nome, flags=re.I)     # uk.xml.gz -> uk.xml
    return nome if nome.lower().endswith(".xml") else nome + ".xml"   # sono sempre xml


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
        # solo l'inizio: un master e' pochi kB, e se l'indirizzo e' un
        # file video (o una radio) non lo si scarica tutto per niente
        testo = urlopen(r, timeout=10).read(262144).decode("utf-8", "ignore")
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
        # stile YouTube: traccia grigia, la parte piena bianca, niente pomello
        self.create_line(x0, y, x1, y, width=4, fill="#4a4a4a", capstyle="round")
        if x > x0:
            self.create_line(x0, y, x, y, width=4, fill="#ffffff", capstyle="round")


class Guida(tk.Toplevel):
    """La griglia dei programmi, stile guida TV: una riga per canale della
    lista aperta, il tempo che scorre a destra, i programmi come blocchi;
    la riga lilla e' adesso. La riga delle ore e la colonna dei nomi
    restano ferme mentre si scorre. Clic sul nome o su un blocco per
    vedere il canale. Rotella per le righe, Shift+rotella per il tempo."""

    ORA = 300           # pixel per ora
    RIGA = 40           # altezza di una riga
    NOMI = 190          # larghezza della colonna dei nomi
    TESTA = 28          # altezza della riga delle ore

    def __init__(self, app):
        tk.Toplevel.__init__(self, app.root, bg=FONDO)
        self.app = app
        self.title(_("TV guide"))
        self.geometry("1100x620")
        self.minsize(700, 300)
        if app.icona_finestra is not None:
            self.iconphoto(False, app.icona_finestra)
        # tre tele: le ore in alto (scorre solo in orizzontale), i nomi a
        # sinistra (solo in verticale), la griglia in mezzo
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(1, weight=1)
        tk.Frame(self, bg=PANNELLO, width=self.NOMI, height=self.TESTA).grid(row=0, column=0, sticky="nsew")
        self.testa = tk.Canvas(self, bg=PANNELLO, height=self.TESTA, highlightthickness=0, bd=0)
        self.testa.grid(row=0, column=1, sticky="nsew")
        self.nomi = tk.Canvas(self, bg=FONDO, width=self.NOMI, highlightthickness=0, bd=0)
        self.nomi.grid(row=1, column=0, sticky="nsew")
        self.tela = tk.Canvas(self, bg=FONDO, highlightthickness=0, bd=0)
        self.tela.grid(row=1, column=1, sticky="nsew")
        self.da = int(time.time() // 1800 * 1800) - 1800    # mezz'ora prima, tondo
        self.fino = self.da + 6 * 3600
        self.righe = []             # (nome, url, ide, programmi)
        self.blocchi = {}           # (w, h, colore, fondo) -> immagine del blocco in onda
        self.raccogli()
        self.tela.bind("<Configure>", lambda e: self.disegna())
        for c in (self.tela, self.nomi, self.testa):
            c.bind("<Button-4>", lambda e: self.scorri_y(-1))
            c.bind("<Button-5>", lambda e: self.scorri_y(+1))
            c.bind("<MouseWheel>", lambda e: self.scorri_y(-1 if e.delta > 0 else +1))
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
        # le ore
        t = self.da
        while t <= self.fino:
            x = self.x_di(t)
            h.create_line(x, self.TESTA - 6, x, self.TESTA, fill="#3a3a3a")
            h.create_text(x + 6, self.TESTA // 2, anchor="w", fill=GRIGIO,
                          text=time.strftime("%H:%M", time.localtime(t)))
            c.create_line(x, 0, x, alto, fill="#2a2a2a")
            t += 1800
        # le righe
        for i, (nome, url, ide, prog) in enumerate(self.righe):
            y0 = i * self.RIGA
            y1 = y0 + self.RIGA
            fondo = PANNELLO if i % 2 else FONDO
            c.create_rectangle(0, y0, largo, y1, fill=fondo, outline="")
            colore = self.app.colore_canale(nome, url)
            for a, b, titolo in prog:
                xa, xb = max(self.x_di(a), 0), self.x_di(b)
                in_onda = a <= adesso < b
                # il blocco in onda: il colore del canale in una sfumatura
                # morbida, piu' presente in alto e che si spegne in basso
                if in_onda and HA_PIL:
                    im = self.blocco(int(xb - xa - 2), self.RIGA - 8, colore, fondo)
                    c.create_image(xa + 1, y0 + 4, anchor="nw", image=im, tags=("prog", url))
                else:
                    c.create_rectangle(xa + 1, y0 + 4, xb - 1, y1 - 4,
                                       fill=colore if in_onda else TASTO, outline="",
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
            # la colonna dei nomi, col pallino disegnato (liscio)
            n.create_rectangle(0, y0, self.NOMI, y1, fill=fondo, outline="", tags=("nome", url))
            n.create_image(17, (y0 + y1) // 2, image=self.app.pallino_di(nome, url),
                           tags=("nome", url))
            n.create_text(30, (y0 + y1) // 2, anchor="w", text=nome[:24],
                          fill=TESTO, tags=("nome", url))
        # adesso
        x = self.x_di(adesso)
        c.create_line(x, 0, x, alto, fill=ACCENTO, width=2)
        h.create_polygon(x - 6, self.TESTA - 8, x + 6, self.TESTA - 8, x, self.TESTA, fill=ACCENTO)
        # le ore e la griglia allineate
        h.xview_moveto(c.xview()[0])
        n.yview_moveto(c.yview()[0])

    def blocco(self, w, h, colore, fondo):
        """L'immagine del blocco in onda: il colore del canale fuso col
        fondo della riga, al 55% in alto e al 20% in basso, angoli
        morbidi. Tenuta in cache per misura e colore."""
        w = max(1, w)
        k = (w, h, colore, fondo)
        if k in self.blocchi:
            return self.blocchi[k]
        r1, g1, b1 = (int(colore[i:i + 2], 16) for i in (1, 3, 5))
        r0, g0, b0 = (int(fondo[i:i + 2], 16) for i in (1, 3, 5))
        im = Image.new("RGB", (w, h))
        px = im.load()
        for y in range(h):
            t = 0.55 - 0.35 * y / float(max(1, h - 1))
            c = (int(r0 + (r1 - r0) * t), int(g0 + (g1 - g0) * t), int(b0 + (b1 - b0) * t))
            for x in range(w):
                px[x, y] = c
        self.blocchi[k] = ImageTk.PhotoImage(im)
        return self.blocchi[k]

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


class Tendina(object):
    """Il menu a tendina disegnato noi, nello stile delle finestrelle:
    scuro, bordo sottile, voci con aria, spunta lilla su quella attiva,
    separatori fra i gruppi. Si chiude scegliendo, cliccando fuori o con
    Esc. Una sola aperta alla volta."""

    ALTA = 30                   # altezza di una voce

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
        top.geometry("+-9000+-9000")        # nasce fuori dallo schermo, niente lampo
        dentro = tk.Frame(top, bg=PANNELLO)
        dentro.pack(padx=1, pady=1)
        larga = max([len(v[0]) for v in voci if v] + [18]) * 8 + 60
        # misurata davvero col font: certe lingue (il russo) hanno lettere
        # piu' larghe della stima e il testo si tagliava
        try:
            f = tkfont.nametofont("TkDefaultFont")
            larga = max([larga] + [f.measure(v[0][3:] if v[0][:3] in ("*  ", "   ") else v[0]) + 70
                                   for v in voci if v])
        except tk.TclError:
            pass
        for voce in voci:
            if voce is None:
                tk.Frame(dentro, bg=SCELTO, height=1).pack(fill="x", padx=10, pady=4)
                continue
            testo, cosa = voce
            spunta = testo.startswith("*  ")
            if testo[:3] in ("*  ", "   "):
                testo = testo[3:]
            riga = tk.Frame(dentro, bg=PANNELLO, height=self.ALTA, width=larga)
            riga.pack(fill="x")
            riga.pack_propagate(False)
            colore = TESTO if cosa else GRIGIO
            segno = tk.Label(riga, text="\u2713" if spunta else "", bg=PANNELLO,
                             fg=ACCENTO, width=2, anchor="center")
            segno.pack(side="left", padx=(8, 0))
            et = tk.Label(riga, text=testo, bg=PANNELLO, fg=colore, anchor="w")
            et.pack(side="left", fill="both", expand=True, padx=(4, 16))
            if cosa:
                for w in (riga, segno, et):
                    w.bind("<Enter>", lambda e, r=riga, a=segno, b=et: self.accendi(r, a, b, True))
                    w.bind("<Leave>", lambda e, r=riga, a=segno, b=et: self.accendi(r, a, b, False))
                    w.bind("<Button-1>", lambda e, c=cosa: self.scegli(c))
        top.update_idletasks()
        w, h = top.winfo_reqwidth(), top.winfo_reqheight()
        if sopra:
            y -= h
        if destra:
            x -= w
        # dentro allo schermo
        x = max(0, min(x, top.winfo_screenwidth() - w))
        y = max(0, min(y, top.winfo_screenheight() - h))
        top.geometry("+%d+%d" % (x, y))
        top.bind("<Button-1>", self.fuori)
        top.bind("<Escape>", lambda e: self.chiudi())
        self.top = top
        # il grab solo quando la finestra e' davvero a video, se no Tk
        # si arrabbia (e X anche); e mai su una finestra gia' chiusa.
        # Le attese stanno sulla finestra principale e non sulla tendina:
        # chiusa la tendina, un'attesa appesa a lei finiva a chiamare le
        # funzioni della tendina dopo (Tk riusa i nomi dei comandi), e
        # usciva "missing 1 required positional argument: 'e'"
        self.root.after(30, lambda: self.prendi(top))
        # si chiude da sola: dopo due secondi col mouse fuori, o subito se
        # si passa a un'altra finestra del desktop (se no restava sopra)
        self.fuori_da = None
        self.aveva_fuoco = False
        # dove sta la finestra adesso: se si sposta o cambia misura, la
        # tendina (che e' una finestrella a parte, ferma) si chiude
        self.finestra_era = self.root.winfo_geometry()
        self.root.after(150, lambda: self.sorveglia(top))

    def sorveglia(self, top):
        if self.top is not top or not top.winfo_exists():
            return
        try:
            # un'altra finestra davanti: il fuoco lascia XVB. Conta solo se
            # prima lo si era visto dentro (con certi gestori di finestre la
            # tendina il fuoco non lo prende mai: si chiuderebbe e si
            # riaprirebbe di continuo, lampeggiando)
            if self.root.winfo_geometry() != getattr(self, "finestra_era", None):
                self.chiudi()                     # finestra spostata o ridimensionata
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
            return                              # su un'altra voce della barra: si apre quella
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
        self.root.after(40, cosa)           # la voce parte a tendina gia' chiusa

    def fuori(self, ev):
        """Un clic fuori dalla tendina la chiude (col grab arriva a lei)."""
        t = self.top
        if t is None:
            return
        if not (0 <= ev.x < t.winfo_width() and 0 <= ev.y < t.winfo_height()):
            altrove = self.altrove
            self.chiudi()
            if altrove:
                # col grab il clic e' arrivato a noi: se era su un'altra
                # voce della barra in alto, si apre quella
                self.root.after(60, lambda: altrove(ev.x_root, ev.y_root))

    def chiudi(self):
        if self.top is None:
            return
        top, self.top = self.top, None
        try:
            top.grab_release()
        except tk.TclError:
            pass
        # si distrugge dopo che l'evento in corso e' finito: distruggerla
        # dentro al suo stesso clic e' quello che faceva saltare X
        self.root.after_idle(lambda: top.winfo_exists() and top.destroy())
        f, self.al_chiudere = self.al_chiudere, None
        # chi e quando: passandoci sopra subito dopo non si riapre (lampeggio)
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
        self.rientro = tk.PhotoImage(width=10, height=1)     # l'aria fra il bordo e il testo
        self.stacco = tk.PhotoImage(width=1, height=3)       # 3 px di aria fra una voce e l'altra
        # i loghi e le copertine stanno in una colonna a parte, a sinistra,
        # fuori dalle righe (niente zebra ne' selezione sotto), alti quanto
        # tutta la voce: un Canvas sopra al margine sinistro del testo, che
        # si ridisegna quando la lista scorre o cambia
        self.colonna = tk.Canvas(self, bg=PANNELLO, highlightthickness=0, bd=0,
                                 width=COLONNA, cursor="arrow")
        self.colonna_dopo = None
        self.config(yscrollcommand=lambda *a: self.ridisegna_colonna())
        self.bind("<Configure>", lambda e: self.ridisegna_colonna(), add="+")
        self.colonna.bind("<Button-1>", lambda e: self.clic_y(e.y))
        self.colonna.bind("<Button-4>", lambda e: self.yview_scroll(-3, "units"))
        self.colonna.bind("<Button-5>", lambda e: self.yview_scroll(3, "units"))
        self.colonna.bind("<MouseWheel>", lambda e: self.yview_scroll(-3 if e.delta > 0 else 3, "units"))
        self.tag_configure("stacco", font=(CARATTERE, -1), spacing1=0, spacing3=0)
        self.corti = {}                     # pallino -> lo stesso, tagliato in altezza
        # niente comportamento da editor: si tiene solo quello nostro
        self.bindtags((str(self), str(self.winfo_toplevel()), "all"))
        self.voci = []                      # iid, in ordine
        self.posto = {}                     # iid -> posto in voci
        self.dati = {}                      # iid -> dict(text, image, tags, colore, img)
        self.scelta = None
        self.tag_configure("scelto", background=SCELTO, foreground="#ffffff")
        # una riga si' e una no col fondo un filo piu' chiaro, per leggere meglio
        self.tag_configure("zebra", background=ZEBRA)
        self.tag_lower("zebra")
        # il nome bianco con un po' d'aria sopra, il sottotitolo attaccato
        # sotto e con l'aria in fondo
        self.tag_configure("nome", foreground="#ffffff", spacing1=6, spacing3=0,
                           font=(CARATTERE, -NOME_PX))
        self.tag_configure("sotto", spacing1=0, spacing3=6, font=(CARATTERE, -SOTTO_PX))
        self.tag_configure("onda")
        self.tag_raise("onda")
        self.tag_raise("scelto")
        self.bind("<Button-1>", self.clic)
        self.bind("<Button-4>", lambda e: self.yview_scroll(-3, "units"))
        self.bind("<Button-5>", lambda e: self.yview_scroll(3, "units"))
        self.bind("<MouseWheel>", lambda e: self.yview_scroll(-3 if e.delta > 0 else 3, "units"))
        self.bind("<Up>", lambda e: self.sposta(-1))
        self.bind("<Down>", lambda e: self.sposta(+1))
        self.tinte = set()                  # i tag colore gia' fatti (uno per colore, non per voce)
        self.segno = "logo"                 # logo, dot o bordo: cosa marca il canale
        self.rientro_dot = tk.PhotoImage(width=PUNTO + 6, height=1)    # sotto al pallino

    # --- come la Treeview
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
            self.ridisegna_colonna()
        else:
            for iid in iids:
                if iid in self.dati:
                    r = self.riga(iid)
                    tk.Text.delete(self, "%d.0" % r, "%d.0" % (r + 3))
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
        if self.scelta and vedi:
            self.tag_add("scelto", *self.spanne(self.scelta))
            self.tag_raise("scelto")
        self.event_generate("<<TreeviewSelect>>")

    def selection_remove(self, iid):
        if self.scelta == iid:
            self.selection_set(None)

    def see(self, iid):
        if iid in self.dati:
            tk.Text.see(self, "%d.0" % (self.riga(iid) + 1))
            tk.Text.see(self, "%d.0" % self.riga(iid))

    # --- il disegno
    def riga(self, iid):
        return 3 * self.posto[iid] + 1

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
            # c'e' gia': si riscrive al suo posto
            tk.Text.delete(self, "%d.0" % r, "%d.0" % (r + 3))
        # un tag per colore (non per voce: con migliaia di tag il Text
        # rallenta), sotto a tutti gli altri
        colore = d["colore"] or GRIGIO
        tinta = "c" + colore
        if tinta not in self.tinte:
            self.tinte.add(tinta)
            # col bordo il margine sinistro e' colorato; coi loghi il margine
            # e' la colonna dei loghi; coi pallini niente
            if self.segno == "bordo":
                self.tag_configure(tinta, foreground=colore, lmargin1=3, lmargin2=3,
                                   lmargincolor=colore)
            elif self.segno == "logo":
                self.tag_configure(tinta, foreground=colore, lmargin1=COLONNA,
                                   lmargin2=COLONNA, lmargincolor=PANNELLO)
            else:
                self.tag_configure(tinta, foreground=colore, lmargin1=0, lmargin2=0)
            self.tag_lower(tinta, "zebra")
        zebra = ("zebra",) if self.posto[iid] % 2 else ()
        extra = tuple(d["tags"]) + (("scelto",) if self.scelta == iid else ())
        # tre righe: nome, sottotitolo, stacco. Il bordo colorato e' il
        # margine sinistro del tag colore, su tutte e due le righe; il
        # rientro e' un'immagine trasparente. L'interlinea dei tag vale
        # solo se il tag sta sul primo carattere, per questo anche le
        # immagini hanno i tag
        tk.Text.insert(self, "%d.0" % r, "\n", ("stacco",))
        tk.Text.insert(self, "%d.0" % r, " " + (sotto or " ") + "\n", (tinta, "sotto") + zebra + extra)
        tk.Text.insert(self, "%d.0" % r, " " + nome + "\n", (tinta, "nome") + zebra + extra)
        # davanti al nome: il pallino (coi pallini) o niente; i loghi stanno
        # nella colonna a parte
        if self.segno == "dot" and d.get("image") is not None:
            self.image_create("%d.0" % r, image=self.corto(d["image"]), align="bottom", padx=4)
            sotto_img = self.rientro_dot
        else:
            self.image_create("%d.0" % r, image=self.rientro)
            sotto_img = self.rientro
        self.tag_add(tinta, "%d.0" % r); self.tag_add("nome", "%d.0" % r)
        self.image_create("%d.0" % (r + 1), image=sotto_img)
        self.tag_add(tinta, "%d.0" % (r + 1)); self.tag_add("sotto", "%d.0" % (r + 1))
        self.image_create("%d.0" % (r + 2), image=self.stacco)
        self.tag_add("stacco", "%d.0" % (r + 2))
        for t in zebra + extra:
            self.tag_add(t, "%d.0" % r); self.tag_add(t, "%d.0" % (r + 1))
        d["img"] = True
        self.ridisegna_colonna()

    def ridisegna_colonna(self):
        if self.colonna_dopo is None:
            self.colonna_dopo = self.after_idle(self._colonna)

    def _colonna(self):
        """I loghi delle voci che si vedono, ognuno centrato sull'altezza
        della sua voce (nome + sottotitolo); dove non c'e' logo, il pallino."""
        self.colonna_dopo = None
        c = self.colonna
        if self.segno != "logo":
            c.place_forget()
            return
        c.place(x=0, y=0, width=COLONNA, relheight=1)
        c.delete("all")
        if not self.voci:
            return
        try:
            su = int(self.index("@0,0").split(".")[0])
            giu = int(self.index("@0,%d" % self.winfo_height()).split(".")[0])
        except (tk.TclError, ValueError):
            return
        for i in range(max(0, (su - 1) // 3), min(len(self.voci), (giu - 1) // 3 + 1)):
            iid = self.voci[i]
            r = 3 * i + 1
            a, b = self.dlineinfo("%d.0" % r), self.dlineinfo("%d.0" % (r + 1))
            if a is None and b is None:
                continue
            y0 = a[1] if a else b[1] - 20
            y1 = (b[1] + b[3]) if b else a[1] + a[3]
            d = self.dati[iid]
            img = d.get("logo") or (self.corto(d["image"]) if d.get("image") is not None else None)
            if img is not None:
                c.create_image(COLONNA // 2, (y0 + y1) // 2, image=img)

    def clic_y(self, y):
        """Un clic nella colonna dei loghi vale come sulla sua voce."""
        self.focus_set()
        try:
            r = int(self.index("@%d,%d" % (COLONNA + 5, y)).split(".")[0])
        except (tk.TclError, ValueError):
            return "break"
        i = (r - 1) // 3
        if 0 <= i < len(self.voci):
            self.selection_set(self.voci[i])
        return "break"

    # --- il mouse e i tasti
    def clic(self, ev):
        self.focus_set()
        try:
            r = int(self.index("@%d,%d" % (ev.x, ev.y)).split(".")[0])
        except (tk.TclError, ValueError):
            return "break"
        i = (r - 1) // 3
        if 0 <= i < len(self.voci):
            self.selection_set(self.voci[i])
        return "break"

    def sposta(self, dove):
        if not self.voci:
            return "break"
        i = self.voci.index(self.scelta) if self.scelta in self.dati else -1
        i = max(0, min(len(self.voci) - 1, i + dove))
        self.selection_set(self.voci[i], vedi=True)       # dalla tastiera: si vede
        self.see(self.voci[i])
        return "break"


class Finestrella(tk.Toplevel):
    """Una finestrella nostra, scura come i pannelli, centrata sulla
    finestra dell'app, al posto di quelle grigie di Tk. Invio = OK,
    Esc = annulla. Con `chiedi` ha una casella di testo e torna quello
    che si e' scritto (None se si annulla); senza, e' solo da leggere."""

    def __init__(self, root, titolo, corpo, chiedi=None, valore="",
                 ok=None, annulla=None, larga=420, domanda=False):
        tk.Toplevel.__init__(self, root, bg=PANNELLO)
        self.withdraw()                     # si fa vedere solo quando e' al centro
        self.title(titolo)
        self.transient(root)
        self.resizable(False, False)
        self.risposta = None
        dentro = tk.Frame(self, bg=PANNELLO)
        dentro.pack(padx=22, pady=(18, 16))
        corpo(dentro)                       # chi chiama disegna il contenuto
        if chiedi is not None:
            tk.Label(dentro, text=chiedi, bg=PANNELLO, fg=GRIGIO, anchor="w"
                     ).pack(fill="x", pady=(10, 4))
            self.casella = tk.Entry(dentro, bg=TASTO, fg=TESTO, insertbackground=ACCENTO,
                                    relief="flat", bd=0, highlightthickness=1,
                                    highlightbackground=SCELTO, highlightcolor=ACCENTO,
                                    font=("TkDefaultFont", 10), width=max(30, larga // 9))
            self.casella.pack(fill="x", ipady=6)
            self.casella.insert(0, valore)
            self.casella.select_range(0, "end")
        bottoni = tk.Frame(dentro, bg=PANNELLO)
        bottoni.pack(fill="x", pady=(16, 0))

        def bottone(testo, cosa, primario=False):
            b = tk.Button(bottoni, text=testo, command=cosa, relief="flat", bd=0,
                          highlightthickness=0, cursor="hand2", padx=16, pady=5,
                          bg=ACCENTO if primario else TASTO,
                          fg="#1a1a1a" if primario else TESTO,
                          activebackground=SCELTO, activeforeground="#ffffff")
            b.pack(side="right", padx=(8, 0))
            return b
        bottone(ok or _("OK"), self.va_bene, primario=True)
        if chiedi is not None or domanda:
            bottone(annulla or _("Cancel"), self.lascia)
        self.bind("<Return>", lambda e: self.va_bene())
        self.bind("<Escape>", lambda e: self.lascia())
        self.protocol("WM_DELETE_WINDOW", self.lascia)
        # al centro della finestra dell'app
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
    voci = []                                   # (percorso, e' cartella)
    scelto = [None]

    def corpo(dentro):
        tk.Label(dentro, text=_("Choose a file") if file_ else _("Choose a folder"),
                 bg=PANNELLO, fg=TESTO, anchor="w",
                 font=("TkDefaultFont", 11, "bold")).pack(fill="x")
        testa = tk.Frame(dentro, bg=PANNELLO)
        testa.pack(fill="x", pady=(10, 4))
        su = tk.Button(testa, text="\u2191", relief="flat", bd=0, highlightthickness=0,
                       cursor="hand2", padx=10, pady=3, bg=TASTO, fg=TESTO,
                       activebackground=SCELTO, activeforeground="#ffffff")
        su.pack(side="left", padx=(0, 6))
        percorso = tk.Entry(testa, bg=TASTO, fg=TESTO, insertbackground=ACCENTO,
                            relief="flat", bd=0, highlightthickness=1,
                            highlightbackground=SCELTO, highlightcolor=ACCENTO,
                            font=("TkDefaultFont", 10))
        percorso.pack(side="left", fill="x", expand=True, ipady=5)
        # l'elenco e' un albero come quello dei canali, stesso stile, con
        # la cartellina disegnata davanti alle cartelle
        riquadro = tk.Frame(dentro, bg=TASTO, width=520, height=PUNTO * 12 + 8)
        riquadro.pack(fill="both", expand=True, pady=(0, 2))
        riquadro.pack_propagate(False)
        elenco = ttk.Treeview(riquadro, show="tree", style="Canali.Treeview",
                              selectmode="browse")
        elenco.pack(fill="both", expand=True, padx=4, pady=4)
        icone[0] = cartellina(GRIGIO, False) or tk.PhotoImage(width=PUNTO, height=PUNTO)
        icone[1] = tk.PhotoImage(width=PUNTO, height=PUNTO)

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
                        elenco.insert("", "end", iid=str(len(voci)), text=" " + n, image=icone[1])
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
        # ci si segna la selezione man mano: a OK premuto la finestra e'
        # gia' distrutta e non si puo' piu' chiedere all'albero
        elenco.bind("<<TreeviewSelect>>", lambda e: sel.__setitem__(
            0, voci[int(elenco.selection()[0])] if elenco.selection() else None))
        riempi()
        # i tasti vanno subito all'elenco. L'attesa si mette sulla finestra
        # principale, non su un pezzo della finestrella: se questa si chiude
        # prima, un'attesa rimasta appesa a lei puo' finire a chiamare la
        # funzione sbagliata (Tk riusa i nomi dei comandi)
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


class TV(object):
    def __init__(self, lista=None, da_aprire=None, porta=None):
        self.cfg = leggi_config()
        global LINGUA
        # quella scelta dal menu; se non se n'e' mai scelta una, quella del
        # sistema (se XVB ce l'ha, se no l'inglese)
        if self.cfg.get("lingua") in dict(LINGUE):
            LINGUA = self.cfg["lingua"]
        else:
            LINGUA = lingua_sistema()
        # i provvisori di una XVB chiusa male: via anche loro
        if self.cfg.get("url_aperti"):
            for u in self.cfg["url_aperti"]:
                self.cfg.get("nomi_url", {}).pop(u, None)
            self.cfg["url_aperti"] = []
        # le guide: una lista di url o file (prima era una sola stringa)
        e = self.cfg.get("epg")
        self.cfg["epg"] = [e] if isinstance(e, str) and e else (e if isinstance(e, list) else [])
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
        self.inizio_rec = 0.0
        self.nuova, self.pagina_nuova = "", ""      # versione nuova su GitHub, se c'e'
        self.velocita = 1
        self.file_in_onda = None            # la registrazione in riproduzione
        self.iid_reg, self.reg, self.reg_in_uso = {}, [], None
        self.iid_media, self.media_in_uso = {}, None     # cartelle e importati
        self.ordine_vero = []               # l'ordine dei file prima dello shuffle
        self.posto_file = {}                # percorso -> posto nell'elenco (colore)
        self.sfondo_su = False              # (c'e' gia' qui: la progress puo' partire prima)
        self.tag_file = {}                  # percorso file -> tag (seconda riga)
        self.info = leggi_json(FILE_INFO)   # film e album: le info scaricate
        self.loghi_img = {}                 # url canale -> logo come immagine Tk
        self.anteprime = {}                 # percorso file -> jpg in cache (copertina/fotogramma)
        self.riprendi_da = 0.0
        self.percorso_mpv = None            # il file come lo chiama mpv
        self.dischi_visti = []              # i dischi montati l'ultima volta
        self.tag_cache = leggi_tag()
        self.icona_finestra, self.icona_file = None, ""
        self.piano = None                            # (nome, url, ide, inizio, fine)
        self.ultima_discesa = 0.0
        self.tranquillo_da = time.time()
        self.affanni = []

        self.root = tk.Tk(className="xvb")
        self.root.title("XVB v" + VERSIONE)
        # l'icona del programma sulla barra della finestra: icon.png
        # accanto all'app, o quella messa dal pacchetto
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
        # una casella vuota della misura dei pallini, per le liste senza
        # immagine: cosi' i nomi restano in colonna
        self.vuoto = tk.PhotoImage(width=PUNTO, height=PUNTO)
        self.pallini = {c: pallino(c) for c in PALLINI}
        self.colori = leggi_colori()        # url canale -> colore dal logo
        self.coda_colori = []
        self.in_coda = set()                # loghi che si stanno guardando
        threading.Thread(target=self.lavora_colori, daemon=True).start()
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
        self.icone, self.icone_pil = {}, {}     # png come Tk e come PIL
        self.vestiti = {}                       # bottone -> immagine composta
        for n in ("play", "pausa", "switch", "playlist", "favorite_on", "favorite_off",
                  "rec", "rec_stop",
                  "volume", "volume_high",
                  "volume_low", "volume_off", "muto", "pieno", "prima", "dopo",
                  "aperto", "chiuso", "setting", "epg", "shuffle", "stop"):
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
                    # png che Tk non digerisce (16 bit, palette...): PIL
                    try:
                        self.icone[n] = ImageTk.PhotoImage(
                            Image.open(p).convert("RGBA"))
                    except Exception:
                        pass
        # EPG e' una scritta, non una png: la si disegna come icona, cosi'
        # sta sulla sfumatura senza fondo come le altre
        if "epg" not in self.icone:
            self.icona_testo("epg", "EPG")
        for v in VELOCITA:
            self.icona_testo("x%g" % v, "x%g" % v)
        self.icona_testo("-10", "-10")
        self.icona_testo("+10", "+10")
        # quelle che mancano o non si aprono si dicono, cosi' si vede subito
        self.icone_mancanti = [n for n in (
            "play", "pausa", "switch", "playlist", "favorite_on", "favorite_off",
            "rec", "rec_stop", "volume",
            "volume_high", "volume_low",
            "volume_off", "muto", "pieno", "prima", "dopo", "aperto", "chiuso",
            "setting", "stop") if n not in self.icone]
        if self.icone_mancanti:
            sys.stderr.write("missing icons in %s: %s\n" % (
                os.path.join(QUI, "icone"), ", ".join(self.icone_mancanti)))

        # --- in alto: la barra dei menu, disegnata noi (quella di Tk su
        # Linux prende il tema di sistema, non il nostro)
        self.cima = tk.Frame(self.root, bg=BARRA, height=28)
        self.cima.pack(side="top", fill="x")
        self.cima.pack_propagate(False)
        self.menu_cima = {}
        self.tendina = Tendina(self.root)
        # prima quello che vale per ogni file (un player), poi l'IPTV
        # tutta in un menu suo, poi vista e aiuto
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
            (_("Back 10 s"), lambda: self.avanza(-10)),
            (_("Forward 10 s"), lambda: self.avanza(+10)),
            None,
            (_("Previous"), lambda: self.salta(-1)),
            (_("Next"), lambda: self.salta(+1)),
            None,
            (("*  " if self.cfg.get("shuffle") else "   ") + _("Shuffle"), self.shuffle),
            None] + [
            (("*  " if self.cfg.get("ripeti", "") == k else "   ") + _(n),
             lambda k=k: self.metti_ripeti(k))
            for k, n in (("", "No repeat"), ("uno", "Repeat one"), ("tutti", "Repeat all"))] + [
            None] + [
            (("*  " if v == self.velocita else "   ") + _("Speed x%g") % v,
             lambda v=v: self.metti_velocita(v)) for v in sorted(VELOCITA)])
        self.menu("Video", lambda: [
            (("*  " if self.cfg.get("proporzioni", "-1") == val and not self.cfg.get("riempi")
              else "   ") + _("Aspect %s") % nome, lambda val=val: self.metti_proporzioni(val))
            for nome, val in PROPORZIONI] + [
            (("*  " if self.cfg.get("riempi") else "   ") + _("Fill screen (crop)"), self.riempi),
            None,
            (("*  " if self.cfg.get("deinterlaccia") else "   ") + _("Deinterlace"), self.deinterlaccia),
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
            (("*  " if t["selected"] else "   ") + _("Track: %s") % self.nome_traccia(t),
             lambda i=t["id"]: self.metti_traccia("aid", i))
            for t in self.tracce("audio")] + ([None] if self.tracce("audio") else []) + [
            (("*  " if not any(t["selected"] for t in self.tracce("sub")) else "   ") + _("Subtitles off"),
             lambda: self.metti_traccia("sid", "no"))] + [
            (("*  " if t["selected"] else "   ") + _("Subtitles: %s") % self.nome_traccia(t),
             lambda i=t["id"]: self.metti_traccia("sid", i))
            for t in self.tracce("sub")] + [
            (_("Load subtitles..."), self.carica_sottotitoli if self.file_in_onda else None),
            (_("Subtitle delay +100 ms"), lambda: self.ritardo_sottotitoli(+0.1)),
            (_("Subtitle delay -100 ms"), lambda: self.ritardo_sottotitoli(-0.1)),
            (_("Subtitles larger"), lambda: self.scala_sottotitoli(+0.1)),
            (_("Subtitles smaller"), lambda: self.scala_sottotitoli(-0.1)),
            (_("Reset subtitles"), self.azzera_sottotitoli)] + [None] + [
            (("*  " if self.cfg.get("boost") else "   ") + _("Volume boost +50%"), self.boost),
            (("*  " if self.cfg.get("normalizza") else "   ") + _("Normalize loudness"), self.normalizza),
            None,
            (_("Delay +100 ms"), lambda: self.ritardo_audio(+0.1)),
            (_("Delay -100 ms"), lambda: self.ritardo_audio(-0.1)),
            (_("Reset delay"), self.azzera_ritardo)])
        # l'IPTV: playlist, guida e registrazione, ognuna col suo titolo
        # grigio (una voce spenta) e separate da una riga
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
            (("*  " if self.cfg.get("in_cima") else "   ") + _("Always on top"), self.sempre_in_cima),
            None,
            (_("Show list") if self.nascosti.get(self.sinistra) else _("Hide list"),
             lambda: self.nascondi(self.sinistra)),
            (_("Show sidebar") if self.nascosti.get(self.destra) else _("Hide sidebar"),
             lambda: self.nascondi(self.destra)),
            None] + [
            (("*  " if self.cfg.get("segno", "logo") == k else "   ") + _(n),
             lambda k=k: self.metti_segno(k))
            for k, n in (("logo", "Logos"), ("dot", "Dots"), ("bordo", "Border"))] + [
            None,
            (_("Language"), None)] + [
            (("*  " if codice == LINGUA else "   ") + nome,
             lambda c=codice: self.cambia_lingua(c)) for codice, nome in LINGUE])
        self.menu("Help", lambda: [
            (_("About XVB..."), self.informazioni),
            None,
            (_("Check for updates"), self.controlla_versione),
            (_("Download %s") % self.nuova if self.nuova else _("Up to date"),
             self.apri_release if self.nuova else None),
            None,
            (_("Donate"), self.dona)])

        self.sinistra = tk.Frame(self.root, bg=PANNELLO,
                                 width=LARGA_BARRA)
        self.sinistra.pack(side="left", fill="y")
        self.sinistra.pack_propagate(False)
        self.cerca = Ricerca(self.sinistra, self.filtra, vuota=_("Search channels"))
        self.cerca.pack(fill="x", padx=10, pady=10)
        st = ttk.Style(self.root)
        st.theme_use("clam")
        st.configure("Canali.Treeview", background=PANNELLO,
                     fieldbackground=PANNELLO, foreground=TESTO,
                     rowheight=RIGA_ELENCO, borderwidth=0, relief="flat")
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
        self.elenco = Elenco(self.sinistra)
        self.elenco.segno = self.cfg.get("segno", "logo")
        self.elenco.colonna.bind("<Double-Button-1>", lambda e: self.parti())
        self.elenco.tag_configure("onda", background=IN_ONDA,
                                  foreground=TESTO_ONDA)
        self.elenco.bind("<<TreeviewSelect>>", lambda e: self.non_sul_verde(
            self.elenco, "onda"))

        self.elenco.column("#0", width=200, stretch=True)
        self.elenco.pack(fill="both", expand=True, padx=2, pady=(0, 8))
        self.elenco.bind("<Double-Button-1>", lambda e: self.parti())
        self.elenco.bind("<Button-3>", self.menu_elenco)
        self.elenco.colonna.bind("<Button-3>", self.menu_elenco)
        self.elenco.bind("<Return>", lambda e: self.parti())

        # --- a destra: le liste
        self.destra = tk.Frame(self.root, bg=PANNELLO,
                               width=LARGA_BARRA)
        # le maniglie fra le barre laterali e il video: si trascinano
        self.maniglia_sx = self.maniglia(self.sinistra, "larga_sx", +1)
        self.maniglia_dx = self.maniglia(self.destra, "larga_dx", -1)
        self.maniglia_sx.pack(side="left", fill="y")
        self.destra.pack(side="right", fill="y")
        self.destra.pack_propagate(False)
        self.maniglia_dx.pack(side="right", fill="y")   # dopo la barra: le sta a sinistra
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
                             rowheight=PUNTO + 8, borderwidth=0, relief="flat",
                             indent=14)     # piu' stretto: ora ci sono tre livelli
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
        # la x a destra di ogni riga: la toglie, chiedendo prima
        self.el_liste.column("x", width=26, stretch=False, anchor="center")
        self.el_liste.pack(fill="both", expand=True, padx=2, pady=(0, 8))
        self.el_liste.bind("<Button-1>", self.clic_liste)
        self.el_liste.bind("<Button-3>", self.menu_liste)
        # la selezione grigia solo muovendosi con la tastiera: dopo un clic
        # col mouse la si toglie (quello che si apre resta viola lo stesso)
        self.el_liste.bind("<Button-1>", lambda e: setattr(self, "col_mouse", True), add="+")
        self.el_liste.bind("<KeyPress>", lambda e: setattr(self, "col_mouse", False), add="+")
        # muovendosi con le frecce non si apre niente (si ricaricava quello
        # in onda): si apre col clic, o con Invio
        self.el_liste.bind("<Return>", lambda e: self.scegli_lista())
        self.el_liste.bind("<<TreeviewSelect>>", lambda e: (
            getattr(self, "col_mouse", False) and self.scegli_lista(),
            self.non_sul_verde(self.el_liste, "usata"),
            getattr(self, "col_mouse", False) and self.el_liste.selection() and
            self.root.after_idle(lambda: self.el_liste.selection_remove(*self.el_liste.selection()))))
        # le righe dentro alle cartelle: il grigio chiaro della zebra di
        # sinistra (creato prima di "usata", cosi' la riga in uso vince)
        self.el_liste.tag_configure("sotto", background=ZEBRA)
        self.el_liste.tag_configure("usata", background=IN_ONDA,
                                    foreground=TESTO_ONDA)
        # il verde vince sul blu: se si seleziona proprio la riga in uso,
        # la selezione si toglie, cosi' resta verde e non si accende
        self.img_liste = {}                 # percorso -> immagine
        self.globi = {}                     # colore -> globo disegnato
        self.el_liste.bind("<<TreeviewOpen>>",
                           lambda e: self.apri_chiudi_cartella(True))
        self.el_liste.bind("<<TreeviewClose>>",
                           lambda e: self.apri_chiudi_cartella(False))

        # --- sotto: stato e comandi
        self.riga_stato = tk.Frame(self.root, bg=STATO, height=28)
        self.riga_stato.pack(side="bottom", fill="x")
        self.riga_stato.pack_propagate(False)
        # l'ora, a sinistra di tutto, del colore del canale
        self.et_orologio = tk.Label(self.riga_stato, text=time.strftime("%H:%M"),
                                    bg=STATO, fg=TESTO)
        self.et_orologio.pack(side="left", padx=(10, 0), fill="y")
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
        self.stato = tk.Label(self.riga_stato, text=_("ready"), anchor="w",
                              bg=STATO, fg="#ffffff")
        self.stato.pack(side="left", padx=(8, 0), fill="y")
        # il titolo del programma, del colore del canale, e il suo orario
        self.et_titolo = tk.Label(self.riga_stato, text="", bg=STATO, fg=TESTO)
        self.et_titolo.pack(side="left", fill="y")
        self.et_ora = tk.Label(self.riga_stato, text="", bg=STATO, fg="#ffffff")
        self.et_ora.pack(side="left", fill="y")
        # il formato del file (mp3, mkv...), del colore del canale
        self.et_formato = tk.Label(self.riga_stato, text="", bg=STATO, fg=TESTO)
        self.et_formato.pack(side="left", fill="y")
        # il pallino rosso che pulsa mentre si registra, poi REC e il tempo
        self.et_recdot = tk.Label(self.riga_stato, bg=STATO, bd=0)
        self.et_recdot.pack(side="left", fill="y", padx=(10, 0))
        self.et_rec = tk.Label(self.riga_stato, text="", bg=STATO, fg="#ff453a")
        self.et_rec.pack(side="left", fill="y")
        self.dot_rec = []                   # i fotogrammi del pallino, dal pieno allo spento
        if HA_PIL:
            r0, g0, b0 = (int(STATO[k:k + 2], 16) for k in (1, 3, 5))
            for i in range(12):
                t = 0.5 + 0.5 * math.cos(2 * math.pi * i / 12)   # 1 -> 0 -> 1
                c = "#%02x%02x%02x" % (int(r0 + (0xff - r0) * t), int(g0 + (0x45 - g0) * t),
                                       int(b0 + (0x3a - b0) * t))
                self.dot_rec.append(pallino(c, lato=16))
        self.fase_rec = 0
        self.barra = tk.Frame(self.root, bg=BARRA, height=40)
        self.barra.pack(side="bottom", fill="x")
        self.barra.pack_propagate(False)        # altezza fissa, comandi al centro
        # il pannello viola degli avvisi: compare sopra ai comandi, alto
        # come la riga del programma, e se ne va da solo o con un clic
        self.avviso = tk.Frame(self.root, bg=IN_ONDA, height=42)
        self.avviso.pack_propagate(False)
        self.et_xvb = tk.Label(self.avviso, text="xvb", bg=IN_ONDA, fg=ACCENTO,
                               font=("TkDefaultFont", 11, "bold"))
        self.et_xvb.pack(side="left", padx=(12, 10), fill="y")
        self.et_avviso = tk.Label(self.avviso, text="", bg=IN_ONDA, fg=TESTO_ONDA,
                                  anchor="w")
        self.et_avviso.pack(side="left", fill="both", expand=True)
        for w in (self.avviso, self.et_xvb, self.et_avviso):
            w.bind("<Button-1>", lambda e: self.chiudi_avviso())
        self.timer_avviso, self.avviso_chiave = None, (None, 0)
        # la riga in cima alla barra, stile YouTube: traccia grigia e, in
        # bianco, quanto manca alla fine del programma (dalla guida)
        # dietro ai comandi, dalla progress in giu', una sfumatura del suo
        # colore che si spegne verso il fondo
        self.fondo_barra = tk.Canvas(self.barra, bg=BARRA, highlightthickness=0, bd=0)
        self.fondo_barra.place(x=0, y=0, relwidth=1, relheight=1)
        self.colore_barra = None
        self.fondo_barra.bind("<Configure>", lambda e: self.disegna_fondo_barra())
        # la progress: la riga e' 2 px, ma il canvas e' alto 10 cosi' il
        # mouse la prende anche un po' sotto; gli 8 px in piu' sono dipinti
        # come la sfumatura, e non si vedono
        self.linea = tk.Canvas(self.barra, height=10, bg=BARRA,
                               highlightthickness=0, bd=0)
        # sovrapposto alla barra, cosi' non le ruba spazio
        self.linea.place(x=0, y=0, relwidth=1, height=10)
        self.pieno_linea = self.linea.create_rectangle(0, 0, 0, 2,
                                                       fill="#ffffff", outline="")
        self.linea.bind("<Configure>", lambda e: self.disegna_fondo_linea())
        # clic (o trascinamento) sulla progress: si va a quel punto del
        # file; vale per file e registrazioni, non per i canali in diretta
        # (li' il cursore non cambia)
        self.alta_linea = 2
        self.linea.bind("<Enter>", lambda e: self.linea.config(
            cursor="hand2" if self.file_in_onda else ""))
        self.linea.bind("<Button-1>", self.vai_a)
        self.linea.bind("<B1-Motion>", self.vai_a)
        self.barra.bind("<Button-1>", lambda e: e.y < 10 and self.vai_a(e))
        self.root.after(500, self.aggiorna_linea)
        self.root.after(1000, self.controlla_registrazione)
        self.root.after(60000, self.aggiorna_sottotitoli)
        self.root.after(8000, self.controlla_copertine)
        self.root.after(5000, self.guarda_dischi)

        # a sinistra, a gruppi: [sidebar]  [< -10 play +10 >  rec switch shuffle]
        self.b_sidebar = self.tasto("playlist", _("Playlists"), self.sidebar, 8)
        self.tasto("prima", "<", lambda: self.salta(-1), 3, padx=(24, 2))
        self.tasto("-10", "-10", lambda: self.avanza(-10), 4)
        self.b_pausa = self.tasto("pausa", _("Pause"), self.pausa, 8)
        self.tasto("stop", _("Stop"), self.ferma, 4)
        self.tasto("+10", "+10", lambda: self.avanza(+10), 4)
        self.tasto("dopo", ">", lambda: self.salta(+1), 3)
        self.b_rec = self.tasto("rec", _("Record"), self.registra, padx=(24, 2))
        self.tasto("switch", _("Switch"), self.switch, 6)
        self.b_shuffle = self.tasto("shuffle", _("Shuffle"), self.shuffle, 7)
        # a destra, da destra a sinistra: cuore, schermo intero, [EPG x1],
        # ingranaggio, volume col suo muto
        self.b_pref = self.tasto("favorite_off", _("Favorite"), self.preferito,
                                 lato="right")
        self.b_pieno = self.tasto("pieno", _("Fullscreen"), self.schermo_intero, 14,
                                  lato="right")
        self.b_pref.pack_forget()           # il cuore c'e' solo quando serve
        self.tasto("epg", "EPG", self.apri_guida, 4, lato="right", padx=(2, 6))
        self.b_velocita = self.tasto("x1", "x1", self.gira_velocita, 4, lato="right",
                                     padx=(6, 2))
        # l'ingranaggio apre la tendina delle qualita' sopra di se'
        self.b_qualita = self.tasto("setting", _("quality"), self.apri_menu_qualita,
                                    lato="right")
        self.volume = Cursore(self.barra, self.alza_volume, larga=85)
        self.volume.pack(side="right", padx=0)
        self.b_muto = self.tasto("volume", _("Mute"), self.muto, lato="right")
        # come YouTube: il cursore sta chiuso, con l'altoparlante attaccato
        # all'ingranaggio; passando col mouse sull'altoparlante si apre
        # verso sinistra, spingendo l'altoparlante, e il resto non si muove.
        # Si richiude quando il mouse va via da tutti e due
        self.volume_largo, self.volume_dopo = 0, None
        self.volume.config(width=0)
        for w in (self.b_muto, self.volume):
            w.bind("<Enter>", lambda e: self.apri_volume(), add="+")
            w.bind("<Leave>", lambda e: self.chiudi_volume_tra_poco(), add="+")
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
        # la disposizione vera (comandi e stato al piede, larghi quanto la
        # finestra) prima che mpv ci metta dentro la sua finestra
        self.disponi(False)
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
            # la finestra video resta viva anche a lettore fermo: quando
            # mpv la smonta (a fine file) rimette il gestore errori X di
            # default al posto di quello di Tk, e da li' in poi il primo
            # errore X innocuo (BadWindow) chiude tutta l'app
            force_window="yes",
            volume_max=150,                 # per il boost
            screenshot_directory=os.path.join(CASA, "Pictures", "xvb"),
            screenshot_template="xvb-%tY-%tm-%td_%tH-%tM-%tS",
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
        # la copertina dentro agli mp3 (python-mpv la spegne di suo): il
        # valore "embedded-first" c'e' da mpv 0.37; su Debian 12 e Ubuntu
        # 22.04 (mpv piu' vecchi) si usa "attachment", che fa lo stesso
        # doppio clic sul video: schermo intero. Sul video i clic li riceve
        # la finestra di mpv, non Tk: glielo si chiede a lui
        try:
            @self.mpv.on_key_press("MBTN_LEFT_DBL")
            def _doppio():
                self.root.after(0, self.schermo_intero)
        except Exception:
            pass
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
        if self.cfg.get("in_cima"):
            self.root.attributes("-topmost", True)
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
        self.sfondo.bind("<Double-Button-1>", lambda e: self.schermo_intero())
        # un fratello di un pixel: serve solo per poter rialzare lo sfondo
        # sopra la finestra di mpv (Tk non manda l'ordine a X se crede di
        # essere gia' in cima)
        self.sotto = tk.Frame(self.video, bg="black", width=1, height=1)
        self.sotto.place(x=0, y=0)
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
                              (tk.Entry, tk.Listbox, ttk.Treeview, tk.Text)):
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
        tasto("<Shift-Left>", lambda: self.avanza(-10))
        tasto("<Shift-Right>", lambda: self.avanza(+10))
        # anche con l'elenco a fuoco (dopo un clic su una voce)
        self.elenco.bind("<Left>", lambda e: self.salta(-1))
        self.elenco.bind("<Right>", lambda e: self.salta(+1))
        self.elenco.bind("<Shift-Left>", lambda e: self.avanza(-10))
        self.elenco.bind("<Shift-Right>", lambda e: self.avanza(+10))
        self.root.bind("<Prior>", lambda e: self.salta(-1))
        self.root.bind("<Next>", lambda e: self.salta(+1))
        self.root.bind("<F11>", self.schermo_intero)
        self.root.bind("<Escape>", lambda e: self.schermo_intero(None, False))
        self.root.protocol("WM_DELETE_WINDOW", self.chiudi)

        if lista and os.path.isfile(lista):
            # una playlist passata all'avvio: si apre quella
            lista = os.path.abspath(lista)
            if lista not in self.tutte_le_liste():
                self.cfg.setdefault("liste", []).append(lista)
            self.rifai_liste(scegli=lista)
        else:
            # se no si parte neutri: nessuna lista aperta, niente selezionato,
            # a sinistra vuoto. Sceglie chi usa XVB (l'ultimo canale resta
            # ricordato: aprendo la sua lista e' selezionato, pronto)
            self.cfg["lista"] = None
            self.rifai_albero()
            self.canali = []
            self.filtra()
            self.scrivi(_("ready"))
        if da_aprire:
            self.root.after(300, lambda: self.apri_da_fuori(da_aprire))
        # le XVB aperte dopo (es. "Apri con" dal file manager) non partono:
        # passano i file a questa, che li apre
        self.porta = porta
        if porta is not None:
            threading.Thread(target=self.ascolta, daemon=True).start()
        threading.Thread(target=self.guarda, daemon=True).start()
        if self.cfg.get("epg"):
            self.carica_guide()
        # dopo un po', in silenzio: c'e' una versione nuova su GitHub?
        self.root.after(8000, lambda: self.controlla_versione(zitto=True))

    # ------------------------------------------------- i pezzi di finestra
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
                font = ImageFont.truetype("DejaVuSans-Bold.ttf", 11)
            except Exception:
                font = ImageFont.load_default()
            im = Image.new("RGBA", (34, 24), (0, 0, 0, 0))
            d = ImageDraw.Draw(im)
            x0, y0, x1, y1 = d.textbbox((0, 0), testo, font=font)
            d.text(((34 - (x1 - x0)) // 2 - x0, (24 - (y1 - y0)) // 2 - y0), testo,
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
            # nella barra i bottoni sono solo l'icona, senza scatola:
            # il fondo e' quello del pannello e si accende solo al tocco
            b.config(image=self.icone[icona], width=34, height=30, bg=BARRA)
            b.icona = icona
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

    def tag_riga(self, iid, usata):
        """I tag di una riga della barra: "usata" se e' quella in uso, e
        "sotto" se sta dentro a una cartella (il fondo grigio chiaro)."""
        if usata:
            self.el_liste.item(iid, tags=("usata",))     # la riga in uso: solo lei
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
        self.el_liste.delete(*self.el_liste.get_children())
        self.iid_di = {}                    # percorso -> riga nell'albero
        self.iid_epg = {}                   # riga -> guida
        adesso = self.cfg.get("lista")

        posto = [0]                         # globi e cartelle: un giro solo

        def riga(padre, d):
            f = immagine_lista(d)
            if f and f not in self.img_liste:
                im = carica_logo(f, PUNTO, PUNTO, RIDOTTE)
                if im is not None:
                    self.img_liste[f] = im
            img = self.img_liste.get(f, self.vuoto)
            tv = bool(f) and os.path.splitext(os.path.basename(f))[0] == "tv"
            if re.match(r"https?://", d, re.I) and not tv:
                # senza tv.png le liste da url hanno un globo, coi colori
                # delle cartelle uno dopo l'altro; con tv.png anche loro la tv
                colore = COLORI_CARTELLE[posto[0] % len(COLORI_CARTELLE)]
                posto[0] += 1
                if colore not in self.globi:
                    self.globi[colore] = globo(colore, dentro=RIDOTTE) or self.vuoto
                img = self.globi[colore]
            iid = self.el_liste.insert(padre, "end", text=" " + nome_di(d),
                                       image=img, values=("\u2715",),
                                       tags=("usata",) if d == adesso else ())
            self.iid_di[d] = iid

        self.cartella_di = {}
        # ogni cartella resta aperta o chiusa come la si e' lasciata, anche
        # quando la barra si rifa' e dopo un riavvio; se non la si e' mai
        # toccata, vale il suo modo di sempre (es. aperta se c'e' dentro
        # la lista in uso)
        self.chiave_di = {}                 # riga della cartella -> chiave
        aperte = self.cfg.get("aperte", {})

        def aperta_di(chiave, di_norma):
            return bool(aperte.get(chiave, di_norma))

        # tutto quello che e' IPTV (guide, registrazioni, playlist) sta in
        # una cartella grigia sua; a fine giro la si sposta in fondo, dopo
        # i media e i preferiti
        aperta = aperta_di("iptv", self.cfg.get("iptv_aperta", True))
        iptv = self.el_liste.insert("", "end", text=" IPTV",
                                    image=self.icona_cartella(GRIGIO, aperta), open=aperta)
        self.cartella_di[iptv] = GRIGIO
        self.chiave_di[iptv] = "iptv"
        self.iid_iptv = iptv
        # in cima, se ci sono, le guide: direttamente dentro IPTV, col nome
        # del file (niente piu' cartella EPG)
        if self.cfg.get("epg"):
            padre = iptv
            f = immagine_lista("")
            if f and f not in self.img_liste:
                im = carica_logo(f, PUNTO, PUNTO, RIDOTTE)
                if im is not None:
                    self.img_liste[f] = im
            for d in self.cfg["epg"]:
                iid = self.el_liste.insert(padre, "end", image=self.img_liste.get(f, self.vuoto),
                                           text=" " + self.nome_guida(d), values=("\u2715",))
                self.iid_epg[iid] = d
        # le registrazioni: una cartella grigia con i file di ~/Videos/xvb,
        # dal piu' recente; cliccando uno si riproduce nel lettore
        self.iid_reg = {}
        self.cartella_reg = None
        self.reg = registrazioni()
        if self.reg:
            aperta = aperta_di("registrazioni", self.reg_in_uso is not None)
            # l'icona: icone/vhs.png (18 px come music e radio); se manca, la
            # cartella grigia che si apre e si chiude
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
        # --- LOCAL (cartella azzurra) e URL (globo verde), prima di IPTV.
        # Sotto: video con player.png, musica con music.png, radio con
        # radio.png (in icone/; se mancano, player e globo)
        self.iid_media = {}

        def icona(nome, riserva, dentro=None):
            f = os.path.join(QUI, "icone", nome + ".png")
            if f not in self.img_liste:
                im = carica_logo(f, PUNTO, PUNTO, dentro) if os.path.isfile(f) else None
                self.img_liste[f] = im
            return self.img_liste[f] or riserva
        img_pl = icona("player", self.vuoto, dentro=RIDOTTE)
        # music e radio un po' piu' piccole (riempiono tutto il quadrato,
        # player no): 18 px dentro al solito riquadro da 24
        img_mu = icona("music", img_pl, dentro=RIDOTTE)
        if "url" not in self.globi:
            self.globi["url"] = globo("#64d2ff", dentro=RIDOTTE) or self.vuoto
        img_ra = icona("radio", self.globi["url"], dentro=RIDOTTE)
        img_ti = icona("timer", self.globi["url"], dentro=RIDOTTE)     # i provvisori

        def img_di(lista):
            """player o music: quella dei file che sono di piu'."""
            audio = sum(1 for _n, f, _i in lista if os.path.splitext(f)[1][1:].lower() in AUDIO)
            return img_mu if lista and audio * 2 > len(lista) else img_pl

        # in cima, sempre per primi: i dischi montati (CD, DVD, e anche piu'
        # ISO insieme), in una cartella "Discs" con sotto uno per riga
        if "disco" not in self.img_liste:
            self.img_liste["disco"] = disco(TESTO, dentro=RIDOTTE) or self.vuoto
        montati, cd = dischi(), cd_audio()
        if montati or cd:
            aperta = aperta_di("dischi", True)
            lettore = self.el_liste.insert("", 0, text=" %s (%d)" % (_("Discs"), len(montati) + len(cd)),
                                           image=self.img_liste["disco"], open=aperta)
            self.chiave_di[lettore] = "dischi"
            for dev, tracce in cd:
                # il CD audio: music.png, "CD audio (12)"
                chiave = ("cd", dev)
                iid = self.el_liste.insert(lettore, "end", text=" %s (%d)" % (_("Audio CD"), tracce),
                                           image=img_mu,
                                           tags=("usata",) if chiave == self.media_in_uso else ())
                self.iid_media[iid] = chiave
            for mp, nome in montati:
                # DVD video: player.png; disco di dati: music o player, a
                # seconda dei file che ci sono di piu'
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
                                      image=self.icona_cartella("#ffd60a", aperta), open=aperta)
        self.cartella_di[locale] = "#ffd60a"          # gialla
        self.chiave_di[locale] = "locale"
        for c in self.cfg.get("cartelle", []):
            # la cartella col totale dei file, e sotto, apribili, le sue
            # sottocartelle dirette che hanno dei media, ognuna col suo
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
                figlio = self.el_liste.insert(iid, "end", text=" %s (%d)" % (d, len(dentro)),
                                              image=img_di(dentro),
                                              tags=("usata",) if k == self.media_in_uso else ())
                self.iid_media[figlio] = k
        # i file importati: una cartella, e sotto un file per riga con la x
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
        # URL: radio, stream musicali, stream di film (salvati) e i
        # provvisori; ognuna una cartella con sotto un indirizzo per riga
        if "azzurro" not in self.globi:
            self.globi["azzurro"] = globo("#0a84ff", dentro=RIDOTTE) or self.vuoto     # il blu di Apple
        aperta = aperta_di("url_tutti", True)
        rete = self.el_liste.insert("", "end", text=" URL", image=self.globi["azzurro"], open=aperta)
        self.chiave_di[rete] = "url_tutti"
        for cat, etichetta, img in (("radio", "Radio", img_ra), ("musica", "Music streams", img_mu),
                                    ("film", "Film streams", img_pl), (None, "Temporary", img_ti)):
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
        # poi le liste sciolte, e le categorie con le loro dentro
        gia = set()
        for nome, dentro in categorie():
            gia.update(dentro)
        for d in self.liste:
            if d not in gia:
                riga(iptv, d)
        for nome, dentro in categorie():
            if nome == PREFERITI and not PREFERITI_ACCESI:
                continue
            colore = colore_cartella(nome, posto[0])
            if nome != PREFERITI:
                posto[0] += 1
            aperta = aperta_di("cat:" + nome, any(d == adesso for d in dentro))
            # i preferiti restano fuori: dentro ci sono anche musica e video
            padre = self.el_liste.insert("" if nome == PREFERITI else iptv, "end",
                                         text=" " + nome,
                                         image=self.icona_cartella(colore, aperta),
                                         open=aperta)
            self.cartella_di[padre] = colore
            self.chiave_di[padre] = "cat:" + nome
            for d in dentro:
                riga(padre, d)
        # in fondo: Recordings (non e' solo IPTV: anche radio e stream), poi IPTV
        if self.cartella_reg:
            self.el_liste.move(self.cartella_reg, "", "end")
        if self.el_liste.get_children(iptv):
            self.el_liste.move(iptv, "", "end")
        else:
            self.el_liste.delete(iptv)
        self.segna_sotto()

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
                if any(url == canale for _n, url, _i in leggi_lista(d, solo_cache=True)):
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
                self.tag_riga(iid, d == dove)
        for iid in list(self.iid_reg) + list(self.iid_media):
            if self.el_liste.exists(iid):
                self.tag_riga(iid, False)
        self.non_sul_verde(self.el_liste, "usata")

    def icona_cartella(self, colore, aperta):
        k = (colore, aperta)
        if k not in self.cartelle:
            self.cartelle[k] = cartellina(colore, aperta, dentro=RIDOTTE) or self.vuoto
        return self.cartelle[k]

    def apri_chiudi_cartella(self, aperta):
        """La cartella cambia faccia quando si apre e quando si chiude."""
        iid = self.el_liste.focus()
        if iid in self.cartella_di:
            self.el_liste.item(iid, image=self.icona_cartella(
                self.cartella_di[iid], aperta))
        chiave = getattr(self, "chiave_di", {}).get(iid)
        if chiave:
            # si ricorda aperta o chiusa, per quando la barra si rifa'
            self.cfg.setdefault("aperte", {})[chiave] = aperta
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
        d = self.lista_selezionata()
        # si ricarica anche se e' la stessa di prima, quando a sinistra ci
        # sono le registrazioni o i file: serve per tornare ai canali
        if d and (d != self.cfg.get("lista") or self.reg_in_uso is not None
                  or self.media_in_uso is not None):
            self.reg_in_uso = self.media_in_uso = None
            self.carica(d)

    def suona_da_barra(self, chiave, dove):
        """Un file importato o un URL aperto, cliccato nella barra: a
        sinistra la sua lista (se non c'e' gia') e parte lui."""
        if self.media_in_uso != chiave:
            self.mostra_media(chiave)
        if dove in (self.file_in_onda, self.cfg.get("canale")) and self.nome_in_onda \
                and not getattr(self, "fermato", None):
            return                          # sta gia' andando: non si ricomincia
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
        # il titolo del programma come nome, l'ora nel sottotitolo
        self.ora_reg = {f: ora for ora, titolo, f in voci}
        self.canali = [(titolo, f, None) for ora, titolo, f in voci]
        # le durate (ffprobe) in un thread: poi la lista si ridisegna
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
        self.fermato = None                 # parte altro: lo stop di prima non conta piu'
        if os.path.isabs(f) and not f.startswith(REGISTRAZIONI + os.sep):
            self.cfg["recenti"] = ([f] + [x for x in self.cfg.get("recenti", []) if x != f])[:12]
        self.file_in_onda = f
        self.nome = self.nome_in_onda = nome or os.path.splitext(os.path.basename(f))[0]
        self.ide_in_onda = None
        dvd = f.startswith("dvd://")
        est = "dvd" if dvd else "cd" if e_cd(f) else \
            os.path.splitext(urlparse(f).path if e_media_url(f) else f)[1][1:].lower()
        self.et_formato.config(text=("  -  " + est) if est else "")
        # quello che mpv dira' di stare suonando (per il riprendi)
        self.percorso_mpv = "dvd://" if dvd else "cdda://" if e_cd(f) else f
        self.varianti, self.quale = [], -1
        self.da_riallineare = False
        # sui DVD niente conto alla rovescia: il lettore puo' metterci un
        # po' a partire; se non va, lo dice mpv con un errore
        self.attesa_da = 0.0 if dvd else time.time()
        self.mostra_carico(True)
        self.colora_stato(self.colore_canale(self.nome, f))
        self.segna_in_onda(f)
        self.icona_preferito()
        # dove si era rimasti l'ultima volta: si riparte da li' (dopo i
        # primi dieci secondi, se no da capo)
        self.riprendi_da = float(self.cfg.get("posizioni", {}).get(f, 0.0))
        if self.riprendi_da < 10:
            self.riprendi_da = 0.0
        try:
            self.mpv["audio-files"] = []
            if dvd:
                # il disco (o la ISO montata) e' il dispositivo, e si
                # suona il film principale
                self.mpv["dvd-device"] = f[len("dvd://"):]
                self.mpv.play("dvd://")
            elif e_cd(f):
                # CD audio: cdda://<lettore>#<traccia>; a mpv il lettore e
                # la traccia (dalla traccia alla traccia)
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
        self.reg_in_uso = self.media_in_uso = None      # a sinistra tornano i canali
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
            self.scrivi(_("cannot read %s: %s") % (nome_di(dove), e), errore=True)
            return
        self.canali = canali
        self.cfg["lista"] = dove
        scrivi_config(self.cfg)
        self.root.after(0, self.filtra)
        self.root.after(0, self.segna_in_uso, dove)
        self.scrivi(_("%s - %d channels") % (nome_di(dove), len(canali)))
        # l'ultimo canale visto, se sta in questa lista: non parte da solo
        # (sceglie chi usa XVB), ma e' selezionato e Play lo fa partire
        ultimo = self.cfg.get("canale")
        if ultimo:
            for nome, url, ide in canali:
                if url == ultimo:
                    self.root.after(300, lambda n=nome, u=url, i=ide:
                                    self.ricorda_canale(n, u, i))
                    break

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
        self.colori, self.in_coda, self.coda_colori[:] = {}, set(), []
        scrivi_colori(self.colori)
        for u in self.cfg.get("liste_url", {}):
            try:
                os.remove(os.path.join(CACHE_LISTE, hashlib.md5(u.encode()).hexdigest() + ".m3u"))
            except Exception:
                pass
        self.rifai_liste(scegli=self.cfg.get("lista"))

    def aggiungi_file(self):
        # "tutti i file" per primo: una lista puo' non avere l'estensione
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
                self.aggiungi(d)                # una playlist
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
        lingua = LINGUE_TMDB.get(LINGUA, "en-US")     # titoli e generi nella lingua dell'app
        try:
            with open(FILE_PROVATI, encoding="utf-8") as h:
                provati = json.load(h)
        except Exception:
            provati = {}
        gia_provati = dict(provati)         # quelli delle volte prima (non di questo giro)
        for n, f in enumerate(file_, 1):
            if not zitto:
                self.scrivi(_("downloading posters and covers... %d/%d") % (n, len(file_)))
            cartella, base = os.path.split(f)
            # in silenzio (all'avvio) non si ricerca quello gia' cercato
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
                    # le informazioni (titolo, anno, generi, voto) sempre;
                    # il poster solo se accanto al film non c'e' gia'
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
                        # niente tag: artista, album e titolo dalle cartelle
                        t2, a2, b2, y2 = dal_percorso(f, self.cfg.get("cartelle", []))
                        artista, album, titolo = artista or a2, album or b2, titolo or t2
                        ha_anno = ha_anno or bool(y2)
                    if artista and titolo and not album:
                        # l'album non si sa: si cerca il brano, e la copertina
                        # va accanto a lui col suo nome (vale solo per lui)
                        suo = [radice + e for e in (".jpg", ".jpeg", ".png")]
                        if any(os.path.isfile(os.path.join(cartella, g)) for g in suo) \
                                or copertina_dentro(f) is not None:
                            continue
                        urls, anno_mb, album_mb = copertina_da_brano(artista, titolo)
                        time.sleep(1.1)     # MusicBrainz: una richiesta al secondo
                        if album_mb or anno_mb:
                            self.info[f] = {"album": album_mb, "anno": anno_mb}
                            nuove += 1
                        for url in urls:    # la prima che c'e' davvero
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
                    # si chiede a MusicBrainz se manca la copertina o l'anno
                    if not (artista and album) or (ha_copertina and ha_anno):
                        continue
                    url, anno_mb = copertina_musicbrainz(artista, album)
                    time.sleep(1.1)         # MusicBrainz: una richiesta al secondo
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
            # le anteprime si rifanno: le immagini nuove passano davanti
            self.anteprime.clear()
            self.loghi_img = {k: v for k, v in self.loghi_img.items() if not os.path.isabs(k)}
            threading.Thread(target=self.tagga, args=(chiave,), daemon=True).start()

    def mostra_media(self, chiave):
        """A sinistra, al posto dei canali, i file di quella cartella o
        gli importati. Doppio clic e parte."""
        tipo, c = chiave
        self.reg_in_uso, self.media_in_uso = None, chiave
        if tipo == "cartella":
            self.canali, nome = media_in(c), os.path.basename(c.rstrip(os.sep)) or c
        elif tipo == "url":
            self.canali = [(self.nome_di_url(u), u, None) for u in self.url_di(c)]
            nome = _({"radio": "Radio", "musica": "Music streams", "film": "Film streams"}.get(c, "Temporary"))
        elif tipo == "cd":
            tracce = dict(cd_audio()).get(c, 0)
            self.canali = [(_("Track %d") % i, "cdda://%s#%d" % (c, i), None)
                           for i in range(1, tracce + 1)]
            nome = _("Audio CD")
        elif tipo == "disco":
            nome = os.path.basename(c.rstrip(os.sep)) or c
            # un DVD video e' un film solo; un disco di dati, i suoi file.
            # Il film: titolo, anno, genere e poster da TMDB, cercati col
            # nome del disco (o con quello dato con Rename)
            if e_dvd(c):
                url = "dvd://" + c
                self.canali = [(nome, url, None)]
                if url not in self.info and url not in getattr(self, "dvd_cercati", set()):
                    self.dvd_cercati = getattr(self, "dvd_cercati", set()) | {url}
                    cerca = self.cfg.get("nomi_dvd", {}).get(url) or titolo_da_etichetta(nome)
                    threading.Thread(target=self.cerca_dvd, args=(url, cerca), daemon=True).start()
            else:
                self.canali = media_in(c)
        else:
            self.canali = [(os.path.splitext(os.path.basename(f))[0], f, None)
                           for f in self.cfg.get("importati", []) if os.path.isfile(f)]
            nome = _("Imported media")
        self.ordine_vero = list(self.canali)
        if self.cfg.get("shuffle"):
            self.mescola(disegna=False)
        self.filtra()
        for iid, k in self.iid_media.items():
            if self.el_liste.exists(iid):
                self.tag_riga(iid, k == chiave)
                if k == chiave:
                    self.mostra_riga(iid)
        for iid in list(self.iid_reg) + list(self.iid_di.values()):
            if self.el_liste.exists(iid):
                self.tag_riga(iid, False)
        self.non_sul_verde(self.el_liste, "usata")
        self.scrivi(_("%s - %d files") % (nome, len(self.canali)))
        if tipo not in ("url", "cd"):       # indirizzi e CD: niente tag ne' copertine
            threading.Thread(target=self.tagga, args=(chiave,), daemon=True).start()

    def aggiungi(self, dove):
        dove = os.path.abspath(dove)
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
        if iid in self.iid_epg or iid in self.iid_di.values():
            self.tendina.apri(ev.x_root, ev.y_root, [(_("Rename..."), lambda: self.rinomina(iid))])
        elif iid in getattr(self, "iid_url", {}):
            cat, u = self.iid_url[iid]
            voci = [(_("Rename..."), lambda: self.rinomina_url(u))]
            altre = [(k, n) for k, n in (("radio", "Radio"), ("musica", "Music streams"),
                                         ("film", "Film streams"), (None, "Temporary")) if k != cat]
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
                im = Image.open(img).convert("RGB")
                im.thumbnail((800, 800))
                im.save(fuori, "JPEG", quality=90)
            else:
                import shutil
                shutil.copyfile(img, fuori)
        except Exception as e:
            self.scrivi(_("cannot read %s: %s") % (os.path.basename(img), e), errore=True)
            return
        self.copertina_cambiata(dove)

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
            self.filtra()                   # i canali IPTV: il logo nuovo
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
            iid = self.elenco.voci[(r - 1) // 3]
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
                     font=("TkDefaultFont", 11, "bold")).pack(fill="x")
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
        nome = self.el_liste.item(iid, "text").strip()
        if iid in self.iid_epg:
            if self.conferma(_("Remove %s") % nome, _("The guide will be removed from the list.")):
                self.togli_epg(self.iid_epg[iid])
            return
        if iid in getattr(self, "iid_file", {}) or iid in getattr(self, "iid_url", {}):
            # una riga sola: via dall'elenco, senza chiedere (non si cancella niente)
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
                os.rmdir(os.path.dirname(file_[0]))       # la cartella del canale, se vuota
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
            if not self.conferma(_("Delete %s") % nome,
                                 _("The file %s will be deleted from disk. This cannot be undone.") % d):
                return
            try:
                os.remove(d)
                cartella = os.path.dirname(d)
                if os.path.basename(cartella) == PREFERITI and not os.listdir(cartella):
                    os.rmdir(cartella)
            except Exception as e:
                self.scrivi(_("cannot delete %s: %s") % (nome, e), errore=True)
                return
            self.cfg["liste"] = [x for x in self.cfg.get("liste", []) if x != d]
            if self.cfg.get("lista") == d:
                self.cfg.pop("lista", None)
            scrivi_config(self.cfg)
            self.rifai_liste()
            return

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
        # il posto di ogni file nell'elenco intero (non filtrato), per il colore
        self.posto_file = {u: i for i, (_n, u, _i) in enumerate(self.canali) if os.path.isabs(u)}
        q = self.cerca.get().lower()
        nomi = self.cfg.get("nomi_canali", {})
        self.visti = [c for c in self.canali if q in c[0].lower() or q in nomi.get(c[1], "").lower()]
        self.elenco.delete(*self.elenco.get_children())
        adesso = self.file_in_onda or self.cfg.get("canale")
        da_fare = []
        for i, (nome, url, ide) in enumerate(self.visti[:3000]):
            self.elenco.insert("", "end", iid=str(i), text=self.testo_riga(nome, url, ide),
                               image=self.pallino_di(nome, url),
                               colore=self.colore_canale(nome, url),
                               logo=self.logo_di(url) or self.icona_file_di(url))
            if url not in self.colori and url in LOGHI and url not in self.in_coda:
                self.in_coda.add(url)
                da_fare.append(url)
        if da_fare:
            self.coda_colori.extend(da_fare)
        if adesso and self.nome_in_onda:
            self.segna_in_onda(adesso)
        elif self.file_in_onda:
            self.segna_in_onda(self.file_in_onda)
        elif getattr(self, "fermato", None):
            # fermo (all'avvio o dopo lo stop): resta selezionato quello
            # che Play farebbe ripartire, anche quando la lista si ridisegna
            i = next((k for k, c in enumerate(self.visti) if c[1] == self.fermato[2]), -1)
            if i >= 0 and self.elenco.exists(str(i)):
                self.elenco.selection_set(str(i))

    def testo_riga(self, nome, url, ide):
        """Le due righe di un elemento a sinistra: il nome e, sotto, il
        programma in onda (canali) o artista - titolo (file)."""
        if self.media_in_uso or self.reg_in_uso or os.path.isabs(url):
            nome, sotto = self.righe_file(url, nome)
        else:
            p = self.programma_di(ide, nome)
            sotto = p[2] if p else ""
            nome = self.cfg.get("nomi_canali", {}).get(url) or nome   # rinominato
        return " " + nome + ("\n " + sotto if sotto else "")

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
                # niente tag: si ricava tutto da cartelle e nome del file
                titolo, artista, album, anno = dal_percorso(f, self.cfg.get("cartelle", []))
            trovato = self.info.get(f, {})      # album e anno cercando il brano
            album = album or trovato.get("album", "")
            anno = anno or trovato.get("anno", "") or self.info.get(os.path.dirname(f), {}).get("anno", "")
            return titolo or nome, " \u00b7 ".join(x for x in (artista, album, anno) if x)
        if f.startswith(REGISTRAZIONI + os.sep):
            # registrazione: sotto l'ora e, se si sa gia', la durata
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
            for i, (nome, url, ide) in enumerate(self.visti[:3000]):
                if not self.elenco.exists(str(i)):
                    continue
                t = self.testo_riga(nome, url, ide)
                if self.elenco.item(str(i), "text") != t:
                    self.elenco.item(str(i), text=t)
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
                self.root.after(0, self.filtra)
                self.root.after(0, self.scrivi_riga)
        # le anteprime (copertine e fotogrammi) solo se si vedono i loghi;
        # la lista si ridisegna ogni tanto man mano che arrivano
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
                if time.time() - ultimo > 1.5:
                    ultimo = time.time()
                    self.root.after(0, self.filtra)
            if fatte and self.media_in_uso == chiave:
                self.root.after(0, self.filtra)

    def colore_canale(self, nome, url):
        """Il colore del canale: quello preso dal suo logo, se lo si ha;
        se no quello dal nome. Una registrazione ha quello del suo canale
        (la cartella in cui sta). Un file (musica, video) il suo posto
        nell'elenco, sulla scala dei file."""
        if url.startswith(REGISTRAZIONI + os.sep):
            return colore_di(os.path.basename(os.path.dirname(url)))
        if os.path.isabs(url) and url in self.posto_file:
            return SCALA_FILE[self.posto_file[url] % len(SCALA_FILE)]
        return self.colori.get(url) or colore_di(nome)

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
            self.colori[url] = c or ""      # "" = provato, niente colore
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
        self.elenco.tinte = set()           # i tag colore si rifanno col margine giusto
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
        # i file sul disco e, nella lista degli URL aperti, gli indirizzi
        in_url = bool(self.media_in_uso and self.media_in_uso[0] == "url")
        if not (os.path.isabs(url) or in_url) or not self.icona_file or not HA_PIL:
            return None
        if getattr(self, "icona_colonna", None) is None:
            self.icona_colonna = carica_logo(self.icona_file, LOGO_L, LOGO_A)
        return self.icona_colonna

    def logo_di(self, url):
        """Il logo del canale come immagine per l'elenco, dalla cache dei
        loghi (quella scaricata per i colori). None se non c'e' ancora."""
        if url in self.loghi_img:
            return self.loghi_img[url]
        if not HA_PIL:
            return None
        if os.path.isabs(url):
            f = self.anteprime.get(url)      # fatta dal thread dei tag
            if not f:
                return None
        elif os.path.isfile(copertina_url(url)):
            f = copertina_url(url)           # scelta a mano (radio, stream)
        elif url not in LOGHI:
            return None
        else:
            f = os.path.join(CACHE_LOGHI, hashlib.md5(LOGHI[url].encode()).hexdigest())
        if not os.path.isfile(f) or os.path.getsize(f) == 0:
            return None
        im = carica_logo(f, LOGO_L, LOGO_A)
        if im is not None:
            self.loghi_img[url] = im
        return im

    def aggiorna_pallino(self, url, c):
        for i, (nome, u, _i) in enumerate(self.visti[:3000]):
            if u == url and self.elenco.exists(str(i)):
                self.elenco.item(str(i), image=self.pallino_di(nome, u),
                                 logo=self.logo_di(u) or self.icona_file_di(u))
        if url == self.cfg.get("canale"):
            self.colora_stato(c)
            self.segna_in_onda(url)

    def parti(self):
        s = self.elenco.selection()
        if s:
            nome, url, ide = self.visti[int(s[0])]
            self.apri(nome, url, ide)

    def segna_in_onda(self, url):
        """La riga di quello che si sta guardando: fondo argento fisso
        (IN_RIPRODUZIONE), testo e pallino neri. Il colore del canale resta
        su comandi, riga di stato e linea di avanzamento. Quella di prima
        torna normale."""
        for i, (nome, u, _l) in enumerate(self.visti[:3000]):
            if not self.elenco.exists(str(i)):
                continue
            if u == url:
                colore = IN_RIPRODUZIONE
                r, g, b = (int(colore[k:k + 2], 16) for k in (1, 3, 5))
                # sui colori molto accesi (verde, giallo, azzurro...) testo
                # nero, sugli altri bianco: la luminanza vera del colore
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
                # solo quella di prima torna normale: ridisegnarle tutte
                # (migliaia) rallentava ogni cambio di lista e di canale
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
        pref = leggi_preferiti(media)
        if self.e_preferito(pref, url, self.nome_in_onda):
            pref = [c for c in pref if not (c[1] == url or
                    nome_piatto(c[0]) == nome_piatto(self.nome_in_onda))]
            self.scrivi(_("%s removed from favorites") % self.nome_in_onda)
        else:
            pref.append((self.nome_in_onda, url, self.ide_in_onda))
            self.scrivi(_("%s added to favorites") % self.nome_in_onda)
        try:
            scrivi_preferiti(pref, media)
        except Exception as e:
            self.scrivi(_("cannot write favorites: %s") % e, errore=True)
            return
        self.icona_preferito()
        f = file_preferiti(media)
        if self.cfg.get("lista") == f:
            if os.path.isfile(f):
                self.rifai_albero()
                self.carica(f)              # e' la lista aperta: si aggiorna
            else:
                self.rifai_liste()          # svuotata: si passa a un'altra
        else:
            self.rifai_albero()

    def e_preferito(self, pref, url, nome):
        """Un canale e' fra i preferiti se c'e' con lo stesso indirizzo, o
        con lo stesso nome (stesso canale preso da un'altra lista)."""
        piatto = nome_piatto(nome)
        return any(u == url or (piatto and nome_piatto(n) == piatto)
                   for n, u, _i in pref)

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
        # il cuore si vede solo dove ha senso
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
        self.fermato = None                 # parte altro: lo stop di prima non conta piu'
        # viene da una lista (Open URL lo rimette dopo); dalla lista degli
        # URL aperti resta roba al volo, senza cuore
        self.al_volo = url if (self.media_in_uso and self.media_in_uso[0] == "url") else None
        if url.startswith("dvd://") or e_cd(url) or url.startswith(REGISTRAZIONI + os.sep) or (
                os.path.isabs(url) and e_media(url) and os.path.isfile(url)) or e_media_url(url):
            self.riproduci(url, nome)
            return
        # ci si ricorda del canale di prima, per lo switch
        if self.cfg.get("canale") and self.cfg.get("canale") != url:
            self.precedente = (self.nome_in_onda, self.cfg.get("canale"))
        if self.registrando and self.cfg.get("canale") != url:
            self.ferma_registrazione()          # cambio canale: si chiude il file
        if self.file_in_onda:
            self.file_in_onda = None
        self.et_formato.config(text="")
        self.nome_in_onda, self.ide_in_onda = nome, ide
        self.attesa_da = time.time()
        self.colora_stato(self.colore_canale(nome, url))    # come il pallino
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
        """La tendina delle qualita' sopra all'ingranaggio, col bordo destro
        sull'ingranaggio; la rotella fa mezzo giro e torna alla chiusura."""
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
            if self.colore_barra and HA_PIL and self.b_qualita.winfo_ismapped():
                # sulla sfumatura: il fotogramma composto sulla fettina
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

    # ------------------------------------------------------ l'adattamento
    def riga(self):
        """I tre pezzi della riga di stato: il canale, il titolo del
        programma in onda (del colore del canale) e il suo orario."""
        # aperto al volo con Open URL: non viene da una lista IPTV, quindi
        # niente guida; sotto, cos'e' e da dove viene
        volo = getattr(self, "al_volo", None)
        if volo and volo in (self.file_in_onda, self.cfg.get("canale")):
            return (self.nome, tipo_url(volo), "")
        p = self.programma()
        if not p:
            if self.file_in_onda:
                titolo, sotto = self.righe_file(self.file_in_onda, self.nome)
                return (titolo, sotto, "")
            # con la guida caricata ma senza questo canale lo si dice
            return (self.nome_canale(), "", _("not in the guide") if self.epg and self.nome
                    else "")
        inizio, fine, titolo = p
        return (self.nome_canale(), titolo or "?", "%s - %s" % (
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
            b.icona = icona
            self.vesti(b)
        else:
            b.config(text=testo)

    def vesti(self, b):
        """Il bottone prende come immagine la fettina di sfumatura che gli
        sta sotto con la sua icona sopra: cosi' non ha un fondo suo e la
        sfumatura passa dietro. Senza sfumatura (o senza PIL) resta la png."""
        icona = getattr(b, "icona", None)
        if not icona:
            return
        pil = self.icone_pil.get(icona)
        if not self.colore_barra or pil is None or not b.winfo_ismapped():
            b.config(image=self.icone[icona], bg=BARRA)
            return
        w, h = b.winfo_width(), b.winfo_height()
        y = b.winfo_y()
        fondo = Image.new("RGBA", (w, h))
        for r in range(h):
            fondo.paste(self.tinta_barra(y + r), (0, r, w, r + 1))
        fondo.alpha_composite(pil, ((w - pil.width) // 2, (h - pil.height) // 2))
        self.vestiti[b] = ImageTk.PhotoImage(fondo)
        b.config(image=self.vestiti[b], bg=self.tinta_barra(y + h // 2))

    def ferma(self):
        """Stop: si ferma tutto e torna lo schermo di XVB. Play dopo lo
        stop riparte da quello che si stava guardando (i file da dove
        erano rimasti)."""
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
        self.attesa_da = 0.0
        self.mostra_carico(False)
        self.mostra_sfondo(True)
        self.faccia(self.b_pausa, "play", _("Resume"))
        self.et_formato.config(text="")
        self.icona_preferito()              # fermo: il cuore sparisce
        self.scrivi(_("ready"))

    def pausa(self):
        # dopo lo stop, play fa ripartire quello di prima
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

    def apri_volume(self):
        if self.volume_dopo:
            self.root.after_cancel(self.volume_dopo)
            self.volume_dopo = None
        self.anima_volume(+1)

    def chiudi_volume_tra_poco(self):
        if self.volume_dopo:
            self.root.after_cancel(self.volume_dopo)
        self.volume_dopo = self.root.after(400, lambda: self.anima_volume(-1))

    def anima_volume(self, verso):
        """Il cursore si allarga (o si stringe) in cinque passi da 15 ms:
        e' lui a cambiare larghezza, l'altoparlante si sposta di conseguenza."""
        passo = self.volume.larga // 5
        largo = max(0, min(self.volume.larga, self.volume_largo + verso * passo))
        if largo == self.volume_largo:
            return
        self.volume_largo = largo
        self.volume.config(width=largo)
        if 0 < largo < self.volume.larga:
            self.root.after(15, lambda: self.anima_volume(verso))

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
        # 8 px con tre puntini a meta' altezza: si capisce che si tira
        m = tk.Canvas(self.root, bg=PANNELLO, width=8, highlightthickness=0, bd=0,
                      cursor="sb_h_double_arrow")

        def puntini(acceso=False):
            m.delete("all")
            h, c = m.winfo_height(), ("#9a9aa8" if acceso else "#5a5a64")
            for dy in (-8, 0, 8):
                y = h // 2 + dy
                m.create_oval(2, y - 2, 6, y + 2, fill=c, outline=c)
        m.bind("<Configure>", lambda e: puntini())
        m.bind("<Enter>", lambda e: (m.config(bg=SCELTO), puntini(True)))
        m.bind("<Leave>", lambda e: (m.config(bg=PANNELLO), puntini()))

        def trascina(ev):
            if verso > 0:
                larga = ev.x_root - pannello.winfo_rootx()
            else:
                larga = pannello.winfo_rootx() + pannello.winfo_width() - ev.x_root
            larga = max(140, min(int(self.root.winfo_width() * 0.6), larga))
            pannello.config(width=larga)
            self.cfg[chiave] = larga

        def fine(ev):
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
        if not pieno:
            self.cima.pack(side="top", fill="x")
        # comandi e riga di stato al piede, larghi quanto tutta la finestra
        # (prima delle barre laterali, che stanno sopra di loro)
        if not pieno:
            self.riga_stato.pack(side="bottom", fill="x")
            self.barra.pack(side="bottom", fill="x")
        if avviso_aperto:
            self.avviso.pack(side="bottom", fill="x")
        # le barre laterali: aperte o chiuse, uguale a schermo intero e no
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
            self.barra.pack(side="bottom", fill="x", before=self.primo_pannello())
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
        # passandoci sopra si apre, senza clic (ma non se si e' appena
        # chiusa proprio questa: se no si apre e chiude di continuo)
        def entra(e):
            et.config(bg=SCELTO)
            chi, quando = getattr(self.tendina, "chiusa", (None, 0))
            if self.tendina.chi is et or (chi is et and time.time() - quando < 0.6):
                return
            self.apri_menu_cima(et, voci)
        et.bind("<Enter>", entra)
        et.bind("<Leave>", lambda e: self.tendina.chi is not et and et.config(bg=BARRA))
        et.bind("<Button-1>", lambda e: self.apri_menu_cima(et, voci))
        self.menu_cima[titolo] = (et, voci)

    def apri_menu_cima(self, et, voci):
        if self.tendina.aperta() and self.tendina.chi is et:
            self.tendina.chiudi()
            return
        self.tendina.apri(et.winfo_rootx(), et.winfo_rooty() + et.winfo_height(), voci(),
                          al_chiudere=lambda: et.config(bg=BARRA), chi=et,
                          altrove=self.clic_altrove)
        et.config(bg=SCELTO)

    def clic_altrove(self, x, y, subito=False):
        """Con una tendina aperta si e' cliccato fuori (o ci si e' passati
        sopra, subito=True): se era su un'altra voce della barra in alto, si
        apre la sua tendina. True se l'ha aperta."""
        for titolo, (et, voci) in self.menu_cima.items():
            if et is self.tendina.chi:
                continue
            x0, y0 = et.winfo_rootx(), et.winfo_rooty()
            if x0 <= x < x0 + et.winfo_width() and y0 <= y < y0 + et.winfo_height():
                self.root.after(10, lambda et=et, voci=voci: self.apri_menu_cima(et, voci))
                return True
        return False

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
        self.rifai_albero()
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
        # titoli e generi dei film sono nella lingua di quando si sono
        # scaricati: si chiede se riscaricarli in quella nuova
        film = [f for f, i in self.info.items() if not f.startswith("_") and "titolo" in i]
        if film and self.conferma(_("Film titles and genres"),
                                  _("Download film titles and genres again in the new language?"),
                                  ok=_("Download")):
            for f in film:
                self.info.pop(f, None)
            threading.Thread(target=self._scarica_copertine, args=(film, self.media_in_uso),
                             daemon=True).start()
        # l'avviso viola, se c'e', nella lingua nuova
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

    # ------------------------------------------------ video e audio in piu'
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

    def applica_video(self):
        """Le impostazioni video del config date a mpv: valgono per tutti
        i canali, anche ai prossimi avvii."""
        try:
            self.mpv.video_aspect_override = self.cfg.get("proporzioni", "-1")
            self.mpv.panscan = 1.0 if self.cfg.get("riempi") else 0.0
            self.mpv.deinterlace = "yes" if self.cfg.get("deinterlaccia") else "no"
            for cosa in ("brightness", "contrast", "saturation"):
                setattr(self.mpv, cosa, int(self.cfg.get(cosa, 0)))
            self.mpv.af = "lavfi=[dynaudnorm=f=150:g=15]" if self.cfg.get("normalizza") else ""
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

    def sempre_in_cima(self):
        self.cfg["in_cima"] = not self.cfg.get("in_cima")
        scrivi_config(self.cfg)
        self.root.attributes("-topmost", bool(self.cfg["in_cima"]))

    def boost(self):
        self.cfg["boost"] = not self.cfg.get("boost")
        scrivi_config(self.cfg)
        self.alza_volume(self.volume.get())

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

    # ------------------------------------------------- l'aggiornamento
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

    def chiedi(self, titolo, domanda, valore=""):
        """Una riga di testo dall'utente, nella finestrella nostra. None
        se annulla."""
        def corpo(dentro):
            tk.Label(dentro, text=titolo, bg=PANNELLO, fg=TESTO, anchor="w",
                     font=("TkDefaultFont", 11, "bold")).pack(fill="x")
        f = Finestrella(self.root, titolo, corpo, chiedi=domanda, valore=valore)
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
                     font=("TkDefaultFont", 16, "bold")).pack(fill="x")
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
            # la dicitura che TMDB chiede a chi usa la sua API, col logo
            p = os.path.join(QUI, "icone", "tmdb.png")
            if HA_PIL and os.path.isfile(p):
                self.logo_tmdb = carica_logo(p, 90, 14)
                if self.logo_tmdb is not None:
                    tk.Label(dentro, image=self.logo_tmdb, bg=PANNELLO, anchor="w").pack(
                        fill="x", pady=(14, 0))
            tk.Label(dentro, text="This product uses the TMDB API but is not endorsed or "
                     "certified by TMDB.", bg=PANNELLO, fg=GRIGIO, anchor="w", justify="left",
                     wraplength=360, font=("TkDefaultFont", 8)).pack(fill="x", pady=(4, 0))
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
        if "playlist_chiuso" not in self.icone:
            im = self.icone_pil["playlist"].rotate(180)
            self.icone_pil["playlist_chiuso"] = im
            self.icone["playlist_chiuso"] = ImageTk.PhotoImage(im)
        self.faccia(self.b_sidebar, "playlist_chiuso" if chiuse else "playlist", _("Playlists"))

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
        """Butta le copie in cache e riscarica tutte le guide."""
        if not self.cfg.get("epg"):
            self.scrivi(_("no TV guide set"))
            return
        for dove in self.cfg["epg"]:
            try:
                if not os.path.isfile(dove):
                    f = os.path.join(CACHE_EPG, hashlib.md5(dove.encode()).hexdigest())
                    if os.path.isfile(f):
                        os.remove(f)            # via la copia: si riscarica
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
        self.carica_guide()                 # si rifa' l'unione con quelle rimaste

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

    def carica_guide(self):
        """Tutte le guide del config, in un thread, unite in una sola."""
        threading.Thread(target=self._carica_guide, args=(list(self.cfg.get("epg", [])),),
                         daemon=True).start()

    def _carica_guide(self, dove_tutte):
        nomi, programmi = {}, {}
        for dove in dove_tutte:
            self.scrivi(_("loading %s...") % self.nome_guida(dove))
            try:
                f = prendi_epg(dove)
                dentro = nome_dentro(f)
                if dentro:
                    self.cfg.setdefault("epg_nomi", {})[dove] = dentro
                    scrivi_config(self.cfg)
                n, p = leggi_epg(f)
            except Exception as e:
                self.scrivi(_("guide not loaded: %s") % e, errore=True)
                time.sleep(2)
                continue
            for k, v in n.items():
                nomi.setdefault(k, v)       # la prima guida che ha un nome vince
            for k, v in p.items():
                if k not in programmi:
                    programmi[k] = v
        self.epg_nomi, self.epg = nomi, programmi
        self.root.after(0, self.rifai_albero)   # coi nomi veri dei file
        self.root.after(0, self.filtra)         # coi programmi sotto ai nomi
        self.scrivi(_("guide loaded: %d channels, %d programmes") % (
            len(nomi), sum(len(v) for v in programmi.values())))
        self.root.after(4000, self.scrivi_riga)

    def programma(self):
        """Il programma in onda sul canale: (inizio, fine, titolo), o
        None. Il canale si trova per tvg-id, se no per nome."""
        if not self.epg or self.file_in_onda:
            return None
        return self.programma_di(self.ide_in_onda, self.nome_in_onda)

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
        self.et_titolo.config(fg=colore)
        self.et_orologio.config(fg=colore)
        self.et_formato.config(fg=colore)
        self.colore_barra = colore
        self.icona_shuffle()
        self.disegna_fondo_barra()

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
                    fig.config(bg=BARRA)
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
        if self.file_in_onda:
            try:
                pos, dur = float(self.mpv.time_pos or 0.0), float(self.mpv.duration or 0.0)
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
                # con la guida: quanto del programma e' passato, vuota
                # all'inizio, piena alla fine
                inizio, fine, _t = p
                parte = (time.time() - inizio) / max(1.0, fine - inizio)
                parte = max(0.0, min(1.0, parte))
            if (self.stato.cget("text") == self.riga_mostrata[0] and
                    self.riga() != self.riga_mostrata):
                self.scrivi_riga()          # e' cambiato programma
        w = self.linea.winfo_width()
        self.linea.coords(self.pieno_linea, 0, 0, int(w * parte), self.alta_linea)
        ora = time.strftime("%H:%M")
        if self.et_orologio.cget("text") != ora:
            self.et_orologio.config(text=ora)
        self.root.after(500, self.aggiorna_linea)
        self.root.after(1000, self.controlla_registrazione)

    def sfondo_giusto(self):
        """Mentre suona un indirizzo con la copertina scelta: quella; se no
        (e da fermi) lo schermo di XVB."""
        u = self.file_in_onda or self.cfg.get("canale") or ""
        if u and not os.path.isabs(u) and not getattr(self, "fermato", None) \
                and self.nome_in_onda and os.path.isfile(copertina_url(u)):
            return copertina_url(u)
        return os.path.join(QUI, "icone", "screen.png")

    def mostra_sfondo(self, si):
        """L'immagine di sfondo del lettore: si vede finche' non parte un
        canale, e torna quando non c'e' piu' niente che va."""
        self.sfondo_su = si
        if si:
            giusto = self.sfondo_giusto()
            if giusto != self.sfondo_file:
                self.sfondo_file, self.sfondo_misura = giusto, None
            self.rifai_sfondo()
            self.sfondo.place(x=0, y=0, relwidth=1, relheight=1)
            self.rialza_sfondo()
            # la finestra di mpv (force-window) nasce per conto suo, anche
            # dopo: si torna sopra un paio di volte per non finirci sotto
            for t in (300, 1500, 4000):
                self.root.after(t, lambda: self.sfondo_su and self.rialza_sfondo())
        else:
            self.sfondo.place_forget()

    def rialza_sfondo(self):
        """Lo sfondo sopra a tutto nel riquadro del video, finestra di mpv
        compresa: prima sotto al fratello e poi in cima, cosi' Tk manda
        davvero l'ordine a X."""
        try:
            self.sfondo.lower(self.sotto)
            self.sfondo.lift()
        except tk.TclError:
            pass

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
            if isinstance(d.get("event"), dict):     # python-mpv vecchio (Ubuntu 22.04)
                d = dict(d, **d["event"])
            ragione = d.get("reason", getattr(getattr(ev, "data", None), "reason", ""))
            # le versioni vecchie danno un numero: 0 = fine del file, 4 = errore
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
        c.create_rectangle(0, 0, w, 2, fill="#3a3a3a", outline="", tags="fondo")
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
        else:
            p[f] = round(pos, 1)
        if len(p) > 300:                    # non cresce all'infinito
            for k in list(p)[:len(p) - 300]:
                p.pop(k, None)
        adesso = time.time()
        if adesso - getattr(self, "posizione_scritta", 0.0) > 10:
            self.posizione_scritta = adesso
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
            self.vai_a_file(self.visti[i])          # lo stesso, da capo
        elif i + 1 < len(self.visti):
            self.vai_a_file(self.visti[i + 1])
        elif ripeti == "tutti":
            self.vai_a_file(self.visti[0])          # finita la lista, si ricomincia

    def metti_ripeti(self, k):
        """Dal menu Playback: niente, il file, o tutta la lista."""
        self.cfg["ripeti"] = k
        scrivi_config(self.cfg)

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
            self.dvd_bloccato()                 # un DVD non si salta
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
        """Quello che suona ha qualcosa da vedere: un video o la copertina
        (per mpv e' una traccia video anche lei). Si chiede una volta per
        cosa che suona, poi si ricorda."""
        k = self.file_in_onda or self.cfg.get("canale") or ""
        if not hasattr(self, "con_immagine"):
            self.con_immagine = {}
        if k not in self.con_immagine:
            try:
                tracce = self.mpv.track_list or []
            except Exception:
                return True
            if not tracce:
                return True                 # non ancora caricato: non si decide
            self.con_immagine[k] = any(t.get("type") == "video" for t in tracce)
        return self.con_immagine[k]

    def _va(self, _nome, pos):
        """Il tempo avanza: sta suonando davvero, il cerchietto va via."""
        if pos is not None:
            self.attesa_da, self.salti = 0.0, 0     # va: niente da saltare
            # senza niente da vedere (musica senza copertina, radio) resta
            # lo schermo di XVB; con un'immagine (video o copertina) va via
            immagine = self.ha_immagine()
            if self.sfondo_su and immagine:
                self.root.after(0, self.mostra_sfondo, False)
            elif not immagine and (not self.sfondo_su or self.sfondo_file != self.sfondo_giusto()):
                self.root.after(0, self.mostra_sfondo, True)
            if self.file_in_onda and getattr(self, "riprendi_da", 0.0):
                # solo quando e' davvero il file nuovo a suonare: subito
                # dopo play() arriva ancora qualche tempo di quello di prima
                try:
                    if self.mpv.path == self.percorso_mpv:
                        da, self.riprendi_da = self.riprendi_da, 0.0
                        self.mpv.command("seek", "%.1f" % da, "absolute")
                except Exception:
                    pass
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
        # canali IPTV, stream e radio da URL, file in rete. Non i file sul
        # disco (ci sono gia'), le registrazioni, e i DVD
        f = self.file_in_onda or ""
        if f.startswith("dvd://"):
            # un DVD si registra solo se non e' cifrato (una copia fatta in
            # casa); quelli protetti no, anche se il sistema li fa vedere
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
            # il nome: il programma in onda (dalla guida), se no il canale;
            # per quello aperto da URL la guida non conta
            p = None if getattr(self, "al_volo", None) else self.programma()
            titolo = (p[2] if p and p[2] else "") or nome
            pulisci = lambda t: re.sub(r"[\\/:*?\"<>|]+", "", t).strip() or "xvb"
            cartella = os.path.join(REGISTRAZIONI, pulisci(nome))   # una per canale
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
        self.rifai_albero()                 # compare fra le registrazioni
        self.root.after(4000, self.scrivi_riga)

    def pulsa_rec(self):
        """Il pallino rosso accanto a REC si accende e si spegne piano,
        finche' si registra."""
        if not self.registrando or not self.dot_rec:
            return
        self.et_recdot.config(image=self.dot_rec[self.fase_rec])
        self.fase_rec = (self.fase_rec + 1) % len(self.dot_rec)
        self.root.after(100, self.pulsa_rec)

    def mostra_rec(self):
        """In fondo alla riga di stato, in rosso: REC, con l'ora a cui si
        ferma se e' pianificata. Vuoto se non si registra."""
        if not self.registrando:
            t = ""
            self.et_recdot.config(image="")
        else:
            durata = int(time.time() - self.inizio_rec)
            ore, resto = divmod(durata, 3600)
            cronometro = "%d:%02d:%02d" % (ore, resto // 60, resto % 60) if ore else \
                         "%02d:%02d" % (resto // 60, resto % 60)
            t = " REC " + cronometro
            if self.fine_rec:
                t += "  " + _("until %s") % time.strftime("%H:%M", time.localtime(self.fine_rec))
        self.et_rec.config(text=t)

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
                    # il tempo di partire, poi si registra
                    self.root.after(5000, lambda: self.avvia_registrazione(nome, t1))
                else:
                    self.avvia_registrazione(nome, t1)
        if self.registrando and self.fine_rec and adesso >= self.fine_rec:
            self.ferma_registrazione()
        elif self.registrando:
            self.mostra_rec()               # il cronometro avanza
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
        # il nome e dove metterlo: Radio, Music streams, Film streams (restano)
        # o Temporary (provvisorio). Proposti il nome ricavato dall'indirizzo
        # e l'ultima scelta; Annulla = non si apre niente
        nomi = self.cfg.setdefault("nomi_url", {})
        dove = tk.StringVar(value=self.cfg.get("ultima_categoria_url", ""))

        def corpo(dentro):
            tk.Label(dentro, text=_("Open URL..."), bg=PANNELLO, fg=TESTO, anchor="w",
                     font=("TkDefaultFont", 11, "bold")).pack(fill="x")
            tk.Label(dentro, text=_("Save in:"), bg=PANNELLO, fg=GRIGIO, anchor="w"
                     ).pack(fill="x", pady=(10, 2))
            for k, n in (("radio", "Radio"), ("musica", "Music streams"),
                         ("film", "Film streams"), ("", "Temporary")):
                tk.Radiobutton(dentro, text=_(n), variable=dove, value=k, anchor="w",
                               bg=PANNELLO, fg=TESTO, selectcolor=TASTO, bd=0,
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
        # a sinistra la sua lista (rifatta: c'e' lui in cima), e parte
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
        for c in ("radio", "musica", "film"):
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
        def fai():
            img = self.icona_errore if errore and self.icona_errore else ""
            self.stato.config(text=(" " + t) if img else t, image=img, compound="left")
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
            self.stato.config(text=nome, image="")
            self.et_titolo.config(text=("  -  " + titolo) if titolo else "")
            self.et_ora.config(text=("  (" + ora + ")") if titolo and ora else
                               ("  -  " + ora if ora else ""))
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
        self.via_i_provvisori()
        try:
            if self.porta is not None:
                self.porta.close()
                os.remove(PORTA)
        except Exception:
            pass
        scrivi_config(self.cfg)
        try:
            self.mpv.terminate()
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
        os.remove(PORTA)                # rimasta da una XVB chiusa male
    except OSError:
        pass
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(PORTA)
        s.listen(4)
        return s
    except OSError:
        return False                    # niente porta: si va lo stesso


if __name__ == "__main__":
    porta = porta_unica()
    if porta is not None:
        argomenti = sys.argv[1:]
        # una playlist m3u come prima (la si apre fra le playlist); file
        # multimediali e cartelle si aprono come dal menu Media
        lista = None
        if len(argomenti) == 1 and os.path.isfile(argomenti[0]) and not e_media(argomenti[0]):
            lista, argomenti = argomenti[0], []
        TV(lista, da_aprire=argomenti, porta=porta or None).root.mainloop()
