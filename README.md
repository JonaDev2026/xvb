# XVB Player

A media player for Linux Mint 22+, built on libmpv. It plays local
videos and music, video DVDs, data discs and ISO images, and IPTV playlists with a TV
guide and recording.

![XVB](preview.png)

*[Versione italiana più sotto](#xvb-player-italiano).*

## Features

- Local videos and music, with resume, shuffle and seeking
- Titles, covers and posters fetched automatically (TMDB, MusicBrainz)
- Video DVDs (not encrypted), data CDs and DVDs, and ISO images; audio CDs and Blu-ray are not supported
- IPTV: m3u playlists from file or URL, XMLTV guide, recording and scheduling
- Favourites for channels, music and videos
- Video and audio adjustments, tracks and subtitles, screenshots
- Dark interface in English, Italian, Spanish, French, German, Portuguese and Russian, following the system language

## Installation

Supported system: **Linux Mint 22+**.

Download the `.deb` from the [latest release](../../releases/latest) and run:

```
sudo apt install ./xvb_*_all.deb
```

XVB is then available in the applications menu, or as `xvb` from a terminal.

To run it from source:

```
sudo apt install -y python3-tk python3-pil python3-pil.imagetk python3-mpv python3-mutagen ffmpeg libmpv2
git clone https://github.com/JonaDev2026/xvb.git ~/xvb
python3 ~/xvb/xvb.py
```

## Usage

- **Media** — open a file or a folder of videos and music.
- **IPTV** — add playlists and TV guides, record and schedule recordings.
- **Playback**, **Video**, **Audio** — playback, picture and sound controls.
- **View** — full screen, layout and language.
- **Sidebar** — media folders, discs and favourites on top; playlists, TV guides and recordings inside the IPTV folder.

| Key | Action |
|---|---|
| Space | Pause / resume |
| ← → | Previous / next |
| Shift + ← → | Seek 10 seconds |
| ↑ ↓ | Volume |
| M | Mute |
| F11 | Full screen |

Settings are stored in `~/.config/xvb/`, recordings in `~/Videos/xvb/`.

## Legal

XVB is a neutral playback tool: it does not provide channels, credentials,
or content. Use XVB only with streams you are authorized to access. Avoid
piracy or any method to bypass copyright protections.

This product uses the TMDB API but is not endorsed or certified by TMDB.
Album covers from MusicBrainz and the Cover Art Archive.

## Support

XVB is free. If you find it useful, you can support its development via
[PayPal](https://www.paypal.com/paypalme/Jonathanuk).
Bugs and suggestions: [issues](../../issues).

## License

MIT © 2026 Jonathan Sanfilippo

---

# XVB Player (italiano)

Lettore multimediale per Linux Mint 22+, basato su libmpv. Riproduce
video e musica locali, DVD video, dischi di dati e immagini ISO, e playlist IPTV con guida
TV e registrazione.

## Funzioni

- Video e musica locali, con ripresa, riproduzione casuale e avanzamento
- Titoli, copertine e poster scaricati in automatico (TMDB, MusicBrainz)
- DVD video (non cifrati), CD e DVD di dati, e immagini ISO; CD audio e Blu-ray non sono supportati
- IPTV: playlist m3u da file o URL, guida XMLTV, registrazione e programmazione
- Preferiti per canali, musica e video
- Regolazioni video e audio, tracce e sottotitoli, istantanee
- Interfaccia scura in inglese, italiano, spagnolo, francese, tedesco, portoghese e russo, secondo la lingua del sistema

## Installazione

Sistema supportato: **Linux Mint 22+**.

Scarica il `.deb` dall'[ultima release](../../releases/latest) ed esegui:

```
sudo apt install ./xvb_*_all.deb
```

XVB si trova poi nel menu delle applicazioni, o come `xvb` da terminale.

Per avviarlo dal sorgente:

```
sudo apt install -y python3-tk python3-pil python3-pil.imagetk python3-mpv python3-mutagen ffmpeg libmpv2
git clone https://github.com/JonaDev2026/xvb.git ~/xvb
python3 ~/xvb/xvb.py
```

## Uso

- **Media** — apri un file o una cartella di video e musica.
- **IPTV** — aggiungi playlist e guide TV, registra e programma le registrazioni.
- **Playback**, **Video**, **Audio** — comandi di riproduzione, immagine e suono.
- **View** — schermo intero, layout e lingua.
- **Barra laterale** — cartelle media, dischi e preferiti in alto; playlist, guide TV e registrazioni nella cartella IPTV.

| Tasto | Azione |
|---|---|
| Spazio | Pausa / riprendi |
| ← → | Precedente / successivo |
| Shift + ← → | Avanti / indietro di 10 secondi |
| ↑ ↓ | Volume |
| M | Muto |
| F11 | Schermo intero |

Le impostazioni sono in `~/.config/xvb/`, le registrazioni in `~/Videos/xvb/`.

## Note legali

XVB è uno strumento di riproduzione neutro: non fornisce canali,
credenziali o contenuti. Usa XVB solo con flussi a cui sei autorizzato
ad accedere. Niente pirateria né metodi per aggirare le protezioni del
copyright.

Questo prodotto usa le API di TMDB ma non è approvato né certificato da
TMDB. Copertine degli album da MusicBrainz e dal Cover Art Archive.

## Supporto

XVB è gratuito. Se ti è utile, puoi sostenerne lo sviluppo tramite
[PayPal](https://www.paypal.com/paypalme/Jonathanuk).
Bug e suggerimenti: [issues](../../issues).

## Licenza

MIT © 2026 Jonathan Sanfilippo
