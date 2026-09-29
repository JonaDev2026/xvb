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
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, ttk
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

import mpv

try:                            # per i loghi: ridimensiona e legge i jpg
    from PIL import Image, ImageTk
    HA_PIL = True
except ImportError:             # senza, si va di PhotoImage: solo png
    HA_PIL = False

VERSIONE = "2.3"
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
LINGUE = (("en", "English"), ("it", "Italiano"), ("es", "Español"), ("fr", "Français"))
LINGUA = "en"
TESTI = {
    "it": {
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
        "new version %s available: About > Download": "nuova versione %s disponibile: Info > Scarica",
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
        "%s - %d channels": "%s - %d canali",
        "Choose a playlist": "Scegli una playlist",
        "All files": "Tutti i file",
        "Open file...": "Apri file...", "Choose a file": "Scegli un file",
        "Media files": "File multimediali", "Open folder...": "Apri cartella...",
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
        "Unmute": "Audio", "Switch": "Scambia",
        "Open folder": "Apri cartella", "Reload": "Ricarica", "Add URL": "Aggiungi url",
        "Remove": "Rimuovi", "View": "Vista", "Hide channels": "Nascondi canali",
        "Show channels": "Mostra canali", "Hide playlists": "Nascondi playlist",
        "Show playlists": "Mostra playlist", "Language": "Lingua",
    },
    "es": {
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
        "new version %s available: About > Download": "nueva versión %s disponible: Acerca de > Descargar",
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
        "%s - %d channels": "%s - %d canales",
        "Choose a playlist": "Elige una lista",
        "All files": "Todos los archivos",
        "Open file...": "Abrir archivo...", "Choose a file": "Elige un archivo",
        "Media files": "Archivos multimedia", "Open folder...": "Abrir carpeta...",
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
        "Unmute": "Sonido", "Switch": "Cambiar",
        "Open folder": "Abrir carpeta", "Reload": "Recargar", "Add URL": "Añadir URL",
        "Remove": "Quitar", "View": "Vista", "Hide channels": "Ocultar canales",
        "Show channels": "Mostrar canales", "Hide playlists": "Ocultar listas",
        "Show playlists": "Mostrar listas", "Language": "Idioma",
    },
    "fr": {
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
        "new version %s available: About > Download": "nouvelle version %s disponible : À propos > Télécharger",
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
        "%s - %d channels": "%s - %d chaînes",
        "Choose a playlist": "Choisir une liste",
        "All files": "Tous les fichiers",
        "Open file...": "Ouvrir un fichier...", "Choose a file": "Choisir un fichier",
        "Media files": "Fichiers multimédias", "Open folder...": "Ouvrir un dossier...",
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


VELOCITA = (1, 1.25, 1.5, 2, 0.5, 0.75)     # il giro del bottone x1
PROPORZIONI = (("Auto", "-1"), ("16:9", "16:9"), ("4:3", "4:3"), ("21:9", "21:9"))

PREFERITI = "favorite"     # la cartella dei preferiti, in playlists/
ROSSO = "#ff453a"          # il suo colore: solo suo, le altre cartelle no
PALLINI = ("#ff5257", "#ff9f0a", "#ffd60a", "#30d158", "#0a84ff",
           "#bf5af2", "#ff375f", "#64d2ff")       # niente grigio: e' per l'EPG
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
FILE_COLORI = os.path.join(CASA, ".cache", "xvb", "colori.json")


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


def globo(colore, lato=PUNTO):
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
    return ImageTk.PhotoImage(im.resize((lato, lato), Image.LANCZOS))


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


def file_preferiti():
    """La lista dei preferiti: playlists/favorite/favorite.m3u, quella che
    c'e' gia' (in qualunque cartella delle liste); se non c'e' ancora,
    nella cartella in uso."""
    for c in CARTELLE_LISTE:
        f = os.path.join(c, PREFERITI, PREFERITI + ".m3u")
        if os.path.isfile(f):
            return f
    return os.path.join(cartella_liste_in_uso(), PREFERITI, PREFERITI + ".m3u")


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
    if not canali:
        # vuoti: via file e cartella, cosi' favorite sparisce dalla barra
        try:
            os.remove(f)
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
         "ogv", "m4v", "mp3", "flac", "ogg", "opus", "m4a", "aac", "wav", "wma", "ape")


def e_media(f):
    return os.path.splitext(f)[1][1:].lower() in MEDIA


def media_in(cartella):
    """I file audio/video dentro a una cartella, primo livello, per nome:
    (nome senza estensione, percorso, None) come i canali."""
    try:
        nomi = sorted(os.listdir(cartella), key=str.lower)
    except Exception:
        return []
    return [(os.path.splitext(n)[0], os.path.join(cartella, n), None)
            for n in nomi if not n.startswith(".") and e_media(n)
            and os.path.isfile(os.path.join(cartella, n))]


def ore_min_sec(secondi):
    """1:02:03 o 02:03, come il cronometro del REC."""
    secondi = int(max(0, secondi))
    ore, resto = divmod(secondi, 3600)
    return ("%d:%02d:%02d" % (ore, resto // 60, resto % 60) if ore
            else "%02d:%02d" % (resto // 60, resto % 60))


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
            if not f.lower().endswith((".mkv", ".mp4", ".ts")):
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
        # si arrabbia (e X anche); e mai su una finestra gia' chiusa
        top.after(30, lambda: self.prendi(top))

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
        self.chi = None
        if f:
            f()


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


def sfoglia(root, titolo, da, file_=True):
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
                    if os.path.isfile(p) and e_media(n):
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
        dentro.after(50, elenco.focus_set)      # i tasti vanno subito all'elenco

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
    def __init__(self, lista=None):
        self.cfg = leggi_config()
        global LINGUA
        if self.cfg.get("lingua") in dict(LINGUE):
            LINGUA = self.cfg["lingua"]
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
                  "aperto", "chiuso", "setting", "epg"):
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
        self.tendina = Tendina(self.root)
        self.menu("File", lambda: [
            (_("Open file..."), self.apri_file),
            (_("Open folder..."), self.apri_cartella_media)])
        self.menu("Playlists", lambda: [
            (_("Add URL"), self.chiedi_lista_url),
            (_("Open folder"), self.apri_cartella_liste),
            (_("Reload"), self.ricarica_liste)] + (
            [None] if self.cfg.get("liste_url") else []) + [
            (_("Remove %s") % n, lambda u=u: self.togli_lista_url(u))
            for u, n in self.cfg.get("liste_url", {}).items()])
        self.menu("TV guide", lambda: [
            (_("Programme guide"), self.apri_guida),
            None,
            (_("Add URL"), self.chiedi_epg),
            (_("Reload"), self.ricarica_epg if self.cfg.get("epg") else None)] + (
            [None] if self.cfg.get("epg") else []) + [
            (_("Remove %s") % self.nome_guida(d), lambda d=d: self.togli_epg(d))
            for d in self.cfg.get("epg", [])])
        self.menu("View", lambda: [
            (_("Fullscreen"), self.schermo_intero),
            (_("Show channels") if self.nascosti.get(self.sinistra) else _("Hide channels"),
             lambda: self.nascondi(self.sinistra)),
            (_("Show playlists") if self.nascosti.get(self.destra) else _("Hide playlists"),
             lambda: self.nascondi(self.destra))])
        self.menu("Record", lambda: [
            (_("Stop recording") if self.registrando else _("Record now"), self.registra),
            None,
            (_("Schedule..."), self.pianifica),
            (_("Cancel schedule"), self.annulla_piano if self.piano else None),
            None,
            (_("Open recordings folder"), self.apri_registrazioni)])
        self.menu("Video", lambda: [
            (("*  " if v == self.velocita else "   ") + _("Speed x%g") % v,
             lambda v=v: self.metti_velocita(v)) for v in sorted(VELOCITA)] + [None] + [
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
            (_("Screenshot"), self.istantanea),
            (("*  " if self.cfg.get("in_cima") else "   ") + _("Always on top"), self.sempre_in_cima)])
        self.menu("Audio", lambda: [
            (("*  " if t["selected"] else "   ") + _("Track: %s") % self.nome_traccia(t),
             lambda i=t["id"]: self.metti_traccia("aid", i))
            for t in self.tracce("audio")] + ([None] if self.tracce("audio") else []) + [
            (("*  " if not any(t["selected"] for t in self.tracce("sub")) else "   ") + _("Subtitles off"),
             lambda: self.metti_traccia("sid", "no"))] + [
            (("*  " if t["selected"] else "   ") + _("Subtitles: %s") % self.nome_traccia(t),
             lambda i=t["id"]: self.metti_traccia("sid", i))
            for t in self.tracce("sub")] + [None] + [
            (("*  " if self.cfg.get("boost") else "   ") + _("Volume boost +50%"), self.boost),
            (("*  " if self.cfg.get("normalizza") else "   ") + _("Normalize loudness"), self.normalizza),
            None,
            (_("Delay +100 ms"), lambda: self.ritardo_audio(+0.1)),
            (_("Delay -100 ms"), lambda: self.ritardo_audio(-0.1)),
            (_("Reset delay"), self.azzera_ritardo)])
        self.menu("About", lambda: [
            (_("About XVB..."), self.informazioni),
            None,
            (_("Check for updates"), self.controlla_versione),
            (_("Download %s") % self.nuova if self.nuova else _("Up to date"),
             self.apri_release if self.nuova else None),
            None,
            (_("Donate"), self.dona)])
        self.menu("Language", lambda: [
            (("*  " if codice == LINGUA else "   ") + nome,
             lambda c=codice: self.cambia_lingua(c)) for codice, nome in LINGUE])

        self.sinistra = tk.Frame(self.root, bg=PANNELLO,
                                 width=int(self.cfg.get("larga_sx", 220)))
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
        self.destra = tk.Frame(self.root, bg=PANNELLO,
                               width=int(self.cfg.get("larga_dx", 220)))
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
                             rowheight=PUNTO + 8, borderwidth=0, relief="flat")
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
        self.el_liste.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.el_liste.bind("<Button-1>", self.clic_liste)
        self.el_liste.bind("<<TreeviewSelect>>", lambda e: (
            self.scegli_lista(), self.non_sul_verde(self.el_liste, "usata")))
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
        self.linea = tk.Canvas(self.barra, height=2, bg="#3a3a3a",
                               highlightthickness=0, bd=0)
        self.linea.pack(side="top", fill="x")
        self.pieno_linea = self.linea.create_rectangle(0, 0, 0, 2,
                                                       fill="#ffffff", outline="")
        self.root.after(500, self.aggiorna_linea)
        self.root.after(1000, self.controlla_registrazione)

        # a sinistra, a gruppi: [sidebar]  [< play rec switch >]
        self.b_sidebar = self.tasto("playlist", _("Playlists"), self.sidebar, 8)
        self.tasto("prima", "<", lambda: self.salta(-1), 3, padx=(24, 2))
        self.b_pausa = self.tasto("pausa", _("Pause"), self.pausa, 8)
        self.b_rec = self.tasto("rec", _("Record"), self.registra)
        self.tasto("switch", _("Switch"), self.switch, 6)
        self.tasto("dopo", ">", lambda: self.salta(+1), 3)
        # a destra, da destra a sinistra: cuore, schermo intero, [EPG x1],
        # ingranaggio, volume col suo muto
        self.b_pref = self.tasto("favorite_off", _("Favorite"), self.preferito,
                                 lato="right")
        self.tasto("pieno", _("Fullscreen"), self.schermo_intero, 14,
                   lato="right")
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
            # la copertina dentro agli mp3 (python-mpv la spegne di suo)
            audio_display="embedded-first",
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
            self.el_liste.see(self.iid_di[self.liste[i]])
        self.carica(self.liste[i])

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
                im = carica_logo(f, PUNTO, PUNTO)
                if im is not None:
                    self.img_liste[f] = im
            img = self.img_liste.get(f, self.vuoto)
            if re.match(r"https?://", d, re.I):
                # le liste da url hanno un globo, coi colori delle cartelle
                # uno dopo l'altro
                colore = COLORI_CARTELLE[posto[0] % len(COLORI_CARTELLE)]
                posto[0] += 1
                if colore not in self.globi:
                    self.globi[colore] = globo(colore) or self.vuoto
                img = self.globi[colore]
            iid = self.el_liste.insert(padre, "end", text=" " + nome_di(d),
                                       image=img, values=("\u2715",),
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
            for d in self.cfg["epg"]:
                iid = self.el_liste.insert(padre, "end", image=self.img_liste.get(f, self.vuoto),
                                           text=" " + self.nome_guida(d), values=("\u2715",))
                self.iid_epg[iid] = d
        # le registrazioni: una cartella grigia con i file di ~/Videos/xvb,
        # dal piu' recente; cliccando uno si riproduce nel lettore
        self.iid_reg = {}
        self.reg = registrazioni()
        if self.reg:
            aperta = self.reg_in_uso is not None
            padre = self.el_liste.insert("", "end", text=" " + _("Recordings"),
                                         image=self.icona_cartella(GRIGIO, aperta),
                                         open=aperta)
            self.cartella_di[padre] = GRIGIO
            f_img = immagine_lista("")
            for (canale, giorno), _v in self.reg:
                try:
                    bello = time.strftime("%d/%m/%Y", time.strptime(giorno, "%Y-%m-%d"))
                except ValueError:
                    bello = giorno
                iid = self.el_liste.insert(padre, "end", text=" %s \u00b7 %s" % (canale, bello),
                                           image=self.img_liste.get(f_img, self.vuoto),
                                           values=("\u2715",),
                                           tags=("usata",) if (canale, giorno) == self.reg_in_uso else ())
                self.iid_reg[iid] = (canale, giorno)
        # le cartelle di video e musica, e i file importati: icone/player.png
        self.iid_media = {}
        f_pl = os.path.join(QUI, "icone", "player.png")
        if os.path.isfile(f_pl) and f_pl not in self.img_liste:
            im = carica_logo(f_pl, PUNTO, PUNTO)
            if im is not None:
                self.img_liste[f_pl] = im
        img_pl = self.img_liste.get(f_pl, self.vuoto)
        media = [(("cartella", c), os.path.basename(c.rstrip(os.sep)) or c)
                 for c in self.cfg.get("cartelle", [])]
        if self.cfg.get("importati"):
            media.append((("importati", None), _("Imported media")))
        for chiave, nome in media:
            iid = self.el_liste.insert("", "end", text=" " + nome, image=img_pl,
                                       values=("\u2715",),
                                       tags=("usata",) if chiave == self.media_in_uso else ())
            self.iid_media[iid] = chiave
        # poi le liste sciolte, e le categorie con le loro dentro
        gia = set()
        for nome, dentro in categorie():
            gia.update(dentro)
        for d in self.liste:
            if d not in gia:
                riga("", d)
        for nome, dentro in categorie():
            colore = colore_cartella(nome, posto[0])
            if nome != PREFERITI:
                posto[0] += 1
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
                self.el_liste.item(iid, tags=("usata",) if d == dove else ())
        for iid in list(self.iid_reg) + list(self.iid_media):
            if self.el_liste.exists(iid):
                self.el_liste.item(iid, tags=())
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
        s = self.el_liste.selection()
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

    def mostra_registrazioni(self, chiave):
        """A sinistra, al posto dei canali, i programmi registrati su quel
        canale quel giorno: '21:00 Evening News'. Doppio clic e parte."""
        self.reg_in_uso, self.media_in_uso = chiave, None
        voci = dict(self.reg).get(chiave, [])
        self.canali = [("%s  %s" % (ora, titolo), f, None) for ora, titolo, f in voci]
        self.filtra()
        for iid, k in self.iid_reg.items():
            if self.el_liste.exists(iid):
                self.el_liste.item(iid, tags=("usata",) if k == chiave else ())
        for iid in list(self.iid_media) + list(self.iid_di.values()):
            if self.el_liste.exists(iid):
                self.el_liste.item(iid, tags=())
        self.non_sul_verde(self.el_liste, "usata")
        self.scrivi(_("%s - %d recordings") % ("%s %s" % chiave, len(voci)))

    def riproduci(self, f, nome=None):
        """Una registrazione nel lettore: al posto del canale, finche' non
        se ne sceglie un altro. La progress e' quanto del file e' passato."""
        if self.registrando:
            self.ferma_registrazione()
        self.file_in_onda = f
        self.nome = self.nome_in_onda = nome or os.path.splitext(os.path.basename(f))[0]
        self.ide_in_onda = None
        est = os.path.splitext(f)[1][1:].lower()
        self.et_formato.config(text=("  -  " + est) if est else "")
        self.varianti, self.quale = [], -1
        self.da_riallineare = False
        self.attesa_da = time.time()
        self.mostra_carico(True)
        self.colora_stato(self.colore_canale(self.nome, f))
        self.segna_in_onda(f)
        self.icona_preferito()
        try:
            self.mpv["audio-files"] = []
            self.mpv.play(f)
        except Exception as e:
            self.scrivi(_("cannot read %s: %s") % (self.nome, e))
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
                self.scrivi(_("%s does not look like an m3u playlist") % os.path.basename(f))
                return
            self.aggiungi(f)

    def apri_file(self):
        """Un video o un brano dal disco: finisce fra gli Importati nella
        barra di destra (come la playlist di VLC) e parte subito."""
        f = sfoglia(self.root, _("Open file..."), self.cfg.get("ultima_cartella", CASA))
        if not f:
            return
        self.cfg["ultima_cartella"] = os.path.dirname(f)
        importati = [x for x in self.cfg.get("importati", []) if x != f] + [f]
        self.cfg["importati"] = importati
        scrivi_config(self.cfg)
        self.rifai_albero()
        self.mostra_media(("importati", None))
        i = next((k for k, c in enumerate(self.visti) if c[1] == f), -1)
        if i >= 0 and self.elenco.exists(str(i)):
            self.elenco.selection_set(str(i))
            self.elenco.see(str(i))
        self.riproduci(f)

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

    def mostra_media(self, chiave):
        """A sinistra, al posto dei canali, i file di quella cartella o
        gli importati. Doppio clic e parte."""
        tipo, c = chiave
        self.reg_in_uso, self.media_in_uso = None, chiave
        if tipo == "cartella":
            self.canali, nome = media_in(c), os.path.basename(c.rstrip(os.sep)) or c
        else:
            self.canali = [(os.path.splitext(os.path.basename(f))[0], f, None)
                           for f in self.cfg.get("importati", []) if os.path.isfile(f)]
            nome = _("Imported media")
        self.filtra()
        for iid, k in self.iid_media.items():
            if self.el_liste.exists(iid):
                self.el_liste.item(iid, tags=("usata",) if k == chiave else ())
                if k == chiave:
                    self.el_liste.see(iid)
        for iid in list(self.iid_reg) + list(self.iid_di.values()):
            if self.el_liste.exists(iid):
                self.el_liste.item(iid, tags=())
        self.non_sul_verde(self.el_liste, "usata")
        self.scrivi(_("%s - %d files") % (nome, len(self.canali)))

    def aggiungi(self, dove):
        dove = os.path.abspath(dove)
        if dove not in self.tutte_le_liste():
            self.cfg.setdefault("liste", []).append(dove)
            scrivi_config(self.cfg)
        self.rifai_liste(scegli=dove)

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
        if iid in self.iid_media:
            tipo, c = self.iid_media[iid]
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
                    self.scrivi(_("cannot delete %s: %s") % (os.path.basename(f), e))
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
                if os.path.basename(cartella) == PREFERITI:
                    os.rmdir(cartella)
            except Exception as e:
                self.scrivi(_("cannot delete %s: %s") % (nome, e))
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
        q = self.cerca.get().lower()
        self.visti = [c for c in self.canali if q in c[0].lower()]
        self.elenco.delete(*self.elenco.get_children())
        adesso = self.file_in_onda or self.cfg.get("canale")
        da_fare = []
        for i, (nome, url, _ide) in enumerate(self.visti[:3000]):
            self.elenco.insert("", "end", iid=str(i), text=" " + nome,
                               image=self.pallino_di(nome, url))
            if url not in self.colori and url in LOGHI and url not in self.in_coda:
                self.in_coda.add(url)
                da_fare.append(url)
        if da_fare:
            self.coda_colori.extend(da_fare)
        if adesso and self.nome_in_onda:
            self.segna_in_onda(adesso)
        elif self.file_in_onda:
            self.segna_in_onda(self.file_in_onda)

    def colore_canale(self, nome, url):
        """Il colore del canale: quello preso dal suo logo, se lo si ha;
        se no quello dal nome. Una registrazione ha quello del suo canale
        (la cartella in cui sta)."""
        if url.startswith(REGISTRAZIONI + os.sep):
            return colore_di(os.path.basename(os.path.dirname(url)))
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
            scrivi_colori(self.colori)
            if c:
                self.root.after(0, self.aggiorna_pallino, url, c)

    def aggiorna_pallino(self, url, c):
        for i, (nome, u, _i) in enumerate(self.visti[:3000]):
            if u == url and self.elenco.exists(str(i)):
                self.elenco.item(str(i), image=self.pallino_di(nome, u))
        if url == self.cfg.get("canale"):
            self.colora_stato(c)
            self.segna_in_onda(url)

    def parti(self):
        s = self.elenco.selection()
        if s:
            nome, url, ide = self.visti[int(s[0])]
            self.apri(nome, url, ide)

    def segna_in_onda(self, url):
        """La riga del canale che si sta guardando prende il colore del
        suo pallino come sfondo; testo e pallino diventano neri o bianchi
        a seconda di quanto e' chiaro. Quella di prima torna normale."""
        for i, (nome, u, _l) in enumerate(self.visti[:3000]):
            if not self.elenco.exists(str(i)):
                continue
            if u == url:
                colore = self.colore_canale(nome, u)
                r, g, b = (int(colore[k:k + 2], 16) for k in (1, 3, 5))
                luce = 0.299 * r + 0.587 * g + 0.114 * b
                testo = "#000000" if luce > 150 else "#ffffff"
                self.elenco.tag_configure("onda", background=colore, foreground=testo)
                if testo not in self.pallini:
                    self.pallini[testo] = pallino(testo) or self.vuoto
                self.elenco.item(str(i), tags=("onda",), image=self.pallini[testo])
            else:
                self.elenco.item(str(i), tags=(), image=self.pallino_di(nome, u))
        self.non_sul_verde(self.elenco, "onda")

    def salta(self, dove):
        """Il canale prima o dopo, nell'elenco come lo si vede adesso."""
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
        """Il canale in onda entra nei preferiti, o ne esce se c'e' gia'.
        I preferiti sono la lista playlists/favorite/favorite.m3u."""
        url = self.cfg.get("canale")
        if not url or not self.nome_in_onda:
            return
        pref = leggi_preferiti()
        if self.e_preferito(pref, url, self.nome_in_onda):
            pref = [c for c in pref if not (c[1] == url or
                    nome_piatto(c[0]) == nome_piatto(self.nome_in_onda))]
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
        f = file_preferiti()
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

    def icona_preferito(self):
        url = self.cfg.get("canale")
        acceso = bool(url) and self.e_preferito(leggi_preferiti(), url, self.nome_in_onda)
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
        if url.startswith(REGISTRAZIONI + os.sep) or (
                os.path.isabs(url) and e_media(url) and os.path.isfile(url)):
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
        p = self.programma()
        if not p:
            # con la guida caricata ma senza questo canale lo si dice
            return (self.nome, "", _("not in the guide") if self.epg and self.nome
                    and not self.file_in_onda else "")
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
        m = tk.Frame(self.root, bg=PANNELLO, width=5, cursor="sb_h_double_arrow")
        m.bind("<Enter>", lambda e: m.config(bg=SCELTO))
        m.bind("<Leave>", lambda e: m.config(bg=PANNELLO))

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
        # le barre laterali: aperte o chiuse, uguale a schermo intero e no
        if not self.nascosti.get(self.sinistra):
            self.sinistra.pack(side="left", fill="y")
            self.maniglia_sx.pack(side="left", fill="y")
        if not self.nascosti.get(self.destra):
            self.destra.pack(side="right", fill="y")
            self.maniglia_dx.pack(side="right", fill="y")
        if not pieno:
            self.riga_stato.pack(side="bottom", fill="x")
            self.barra.pack(side="bottom", fill="x")
        if avviso_aperto:
            self.avviso.pack(side="bottom", fill="x")
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

    def clic_altrove(self, x, y):
        """Con una tendina aperta si e' cliccato fuori: se era su un'altra
        voce della barra in alto, si apre la sua tendina."""
        for titolo, (et, voci) in self.menu_cima.items():
            x0, y0 = et.winfo_rootx(), et.winfo_rooty()
            if x0 <= x < x0 + et.winfo_width() and y0 <= y < y0 + et.winfo_height():
                self.root.after(10, lambda et=et, voci=voci: self.apri_menu_cima(et, voci))
                return

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
            self.avviso.pack(side="bottom", fill="x", before=self.video)
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
            self.scrivi(_("cannot take a screenshot: %s") % e)
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
                self.scrivi(_("cannot check for updates: %s") % e)
            return
        if tag and piu_nuova(tag, VERSIONE):
            self.nuova, self.pagina_nuova = tag, pagina
            self.root.after(0, lambda: self.mostra_avviso(
                _("new version %s available: About > Download") % tag, 0,
                chiave=("new version %s available: About > Download", tag)))
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
        Finestrella(self.root, _("About XVB..."), corpo)

    def dona(self):
        try:
            subprocess.Popen(["xdg-open", DONA])
        except Exception as e:
            self.scrivi(_("cannot open %s: %s") % (DONA, e))

    def apri_release(self):
        if self.pagina_nuova:
            try:
                subprocess.Popen(["xdg-open", self.pagina_nuova])
            except Exception as e:
                self.scrivi(_("cannot open %s: %s") % (self.pagina_nuova, e))

    def apri_cartella_liste(self):
        cartella = cartella_liste_in_uso()
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
        return self.cfg.get("epg_nomi", {}).get(dove) or nome_epg(dove)

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
                self.scrivi(_("guide not loaded: %s") % e)
                time.sleep(2)
                continue
            for k, v in n.items():
                nomi.setdefault(k, v)       # la prima guida che ha un nome vince
            for k, v in p.items():
                if k not in programmi:
                    programmi[k] = v
        self.epg_nomi, self.epg = nomi, programmi
        self.root.after(0, self.rifai_albero)   # coi nomi veri dei file
        self.scrivi(_("guide loaded: %d channels, %d programmes") % (
            len(nomi), sum(len(v) for v in programmi.values())))
        self.root.after(4000, self.scrivi_riga)

    def programma(self):
        """Il programma in onda sul canale: (inizio, fine, titolo), o
        None. Il canale si trova per tvg-id, se no per nome."""
        if not self.epg or self.file_in_onda:
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
        """La progress, il titolo del programma e la sfumatura dietro ai
        comandi del colore del pallino del canale."""
        self.linea.itemconfig(self.pieno_linea, fill=colore)
        self.et_titolo.config(fg=colore)
        self.et_orologio.config(fg=colore)
        self.et_formato.config(fg=colore)
        self.colore_barra = colore
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
        self.linea.coords(self.pieno_linea, 0, 0, int(w * parte), 2)
        ora = time.strftime("%H:%M")
        if self.et_orologio.cget("text") != ora:
            self.et_orologio.config(text=ora)
        self.root.after(500, self.aggiorna_linea)
        self.root.after(1000, self.controlla_registrazione)

    def mostra_sfondo(self, si):
        """L'immagine di sfondo del lettore: si vede finche' non parte un
        canale, e torna quando non c'e' piu' niente che va."""
        self.sfondo_su = si
        if si:
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
            motivo = str(d.get("reason", "")) + str(d.get("file_error", ""))
            if not motivo:
                motivo = str(getattr(getattr(ev, "data", None), "reason", ""))
        except Exception:
            pass
        if "error" in motivo.lower():
            self.root.after(0, self.canale_morto)
        elif "eof" in motivo.lower() and self.file_in_onda:
            self.root.after(0, self.prossimo_file)

    def prossimo_file(self):
        """Un file e' finito da solo: si passa al prossimo dell'elenco a
        sinistra (cartella, importati, registrazioni), come una playlist.
        All'ultimo ci si ferma."""
        f = self.file_in_onda
        i = next((k for k, c in enumerate(self.visti) if c[1] == f), -1)
        if i < 0 or i + 1 >= len(self.visti):
            return
        if self.elenco.exists(str(i + 1)):
            self.elenco.selection_set(str(i + 1))
            self.elenco.see(str(i + 1))
        nome, url, ide = self.visti[i + 1]
        self.apri(nome, url, ide)

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
        if not self.cfg.get("canale") or not self.nome_in_onda or self.file_in_onda:
            return                          # niente canale, o e' una registrazione
        self.avvia_registrazione(self.nome_in_onda, 0.0)

    def avvia_registrazione(self, nome, fine):
        """Apre il file e dice a mpv di scriverci il flusso; con `fine`
        (secondi dal 1970) si ferma da sola a quell'ora."""
        try:
            os.makedirs(REGISTRAZIONI, exist_ok=True)
            # il nome: il programma in onda (dalla guida), se no il canale
            p = self.programma()
            titolo = (p[2] if p and p[2] else "") or nome
            pulisci = lambda t: re.sub(r"[\\/:*?\"<>|]+", "", t).strip() or "xvb"
            cartella = os.path.join(REGISTRAZIONI, pulisci(nome))   # una per canale
            os.makedirs(cartella, exist_ok=True)
            f = os.path.join(cartella, "%s - %s.mkv" % (
                pulisci(titolo), time.strftime("%Y-%m-%d %H-%M")))
            self.mpv.stream_record = f
        except Exception as e:
            self.scrivi(_("cannot record: %s") % e)
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
        elif self.registrando:
            self.mostra_rec()               # il cronometro avanza
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
