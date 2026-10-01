# XVB Player

A media player for Linux Mint 22+, built on libmpv. It plays local
videos and music, video DVDs, data discs and ISO images, and IPTV playlists with a TV
guide and recording.

![XVB](preview.png)

*[Versione italiana più sotto](#xvb-player-italiano).*

## Features

- Local videos and music, with resume, shuffle, repeat and seeking
- Open files and folders from the file manager (Open with XVB), by drag and drop, or from recent files
- Search the list by name, title, artist, album or year
- Media keys and the desktop sound panel (MPRIS): title, cover and controls
- Radio, music and film streams by URL, saved with a name and a cover of your choice, or opened just once
- Titles, covers and posters fetched automatically (TMDB, MusicBrainz), also for untagged music from folder and file names
- Right-click: rename playlists, guides, streams and channels, set a cover or a channel logo
- Audio CDs, video DVDs (not encrypted), data discs and ISO images, several at once; Blu-ray is not supported
- IPTV: m3u playlists from file or URL, XMLTV guide
- Recording of IPTV channels, radio and streams (also scheduled) and of unencrypted DVDs
- Controls drawn over the video, semi-transparent, hiding by themselves when only the video is shown
- Video and audio adjustments, hardware decoding (on by default), tracks, chapters, external subtitles with delay and size, screenshots, picture in picture
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

- **Media** — open a file, a folder of videos and music, or a URL; recent files.
- **IPTV** — add playlists and TV guides, record and schedule recordings.
- **Playback**, **Video**, **Audio** — playback, picture and sound controls.
- **View** — full screen, layout and language.
- **Sidebar** — **Discs** (CDs, DVDs, ISO images), **Local** (media folders, imported files), **URL** (radio, music streams, film streams, temporary), **Recordings** and **IPTV** (playlists, TV guides).

| Key | Action |
|---|---|
| Space | Pause / resume |
| ← → | Previous / next |
| Shift + ← → | Seek 10 seconds |
| ↑ ↓ | Volume |
| M | Mute |
| Backspace | Previous channel (IPTV) |
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

- Video e musica locali, con ripresa, riproduzione casuale, ripetizione e avanzamento
- Apertura di file e cartelle dal file manager (Apri con XVB), trascinandoli sulla finestra o dai file recenti
- Ricerca nella lista per nome, titolo, artista, album o anno
- Tasti multimediali e pannello audio del desktop (MPRIS): titolo, copertina e comandi
- Radio, stream musicali e di film da URL, salvati con un nome e una copertina a scelta, o aperti una volta sola
- Titoli, copertine e poster scaricati in automatico (TMDB, MusicBrainz), anche per la musica senza tag a partire dai nomi di cartelle e file
- Clic destro: rinomina playlist, guide, stream e canali, imposta una copertina o il logo di un canale
- CD audio, DVD video (non cifrati), dischi di dati e immagini ISO, anche più insieme; Blu-ray non supportati
- IPTV: playlist m3u da file o URL, guida XMLTV
- Registrazione di canali IPTV, radio e stream (anche programmata) e dei DVD non cifrati
- Comandi disegnati sopra al video, semitrasparenti, che si nascondono da soli quando c'è solo il video
- Regolazioni video e audio, decodifica hardware (attiva di serie), tracce, capitoli, sottotitoli esterni con ritardo e dimensione, istantanee, immagine nell'immagine (PiP)
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

- **Media** — apri un file, una cartella di video e musica, o un URL; file recenti.
- **IPTV** — aggiungi playlist e guide TV, registra e programma le registrazioni.
- **Playback**, **Video**, **Audio** — comandi di riproduzione, immagine e suono.
- **View** — schermo intero, layout e lingua.
- **Barra laterale** — **Discs** (CD, DVD, immagini ISO), **Local** (cartelle media, file importati), **URL** (radio, stream musicali, stream di film, provvisori), **Recordings** e **IPTV** (playlist, guide TV).

| Tasto | Azione |
|---|---|
| Spazio | Pausa / riprendi |
| ← → | Precedente / successivo |
| Shift + ← → | Avanti / indietro di 10 secondi |
| ↑ ↓ | Volume |
| M | Muto |
| Backspace | Canale precedente (IPTV) |
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
