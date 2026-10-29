# XVB Player

⚠️ **Requires an X11 session.** Fully compatible (100%) and optimized for **Ubuntu 24.04 LTS**, **Linux Mint 22**, and **Debian 13**.

A media player built on libmpv. It plays local videos and music, video DVDs, data discs and ISO images, IPTV playlists with a TV guide and recording, and RTSP network security cameras.

![XVB](preview.png)

## Features

- Local videos and music, with resume, shuffle, repeat and seeking
- Open files and folders from the file manager (Open with XVB), by drag and drop, or from recent files
- Search the list by name, title, artist, album or year
- Media keys and the desktop sound panel (MPRIS): title, cover and controls
- Radio, music, film, and **RTSP/ONVIF security camera streams** by URL, saved with a name and a cover of your choice, or opened just once
- Titles, covers and posters fetched automatically (TMDB, MusicBrainz), also for untagged music from folder and file names
- Right-click: rename playlists, guides, streams and channels, set a cover or a channel logo
- Audio CDs, video DVDs (not encrypted), data discs and ISO images, several at once; Blu-ray is not supported
- IPTV: m3u playlists from file or URL, XMLTV guide
- **Advanced PVR recording**: stream dump for IPTV channels, radio, RTSP feeds, and unencrypted DVDs (supports instant and scheduled recordings)
- Controls drawn over the video, semi-transparent, hiding by themselves when only the video is shown
- Video and audio adjustments, hardware decoding (on by default), tracks, chapters, external subtitles (SRT, ASS/SSA) with delay and size synchronization, screenshots, picture in picture (PiP)
- Dark interface in English, Italian, Spanish, French, German, Portuguese and Russian, following the system language

## Installation & System Requirements

- **Supported Operating Systems (100% Compatible):** 
  - Ubuntu 24.04 LTS
  - Linux Mint 22
  - Debian 13
- **Display Server:** **X11 Session Only**

⚠️ **Important Notice on Display Servers:** XVB Player is 100% stable and fully optimized for **X11**. Running under Wayland is not supported and may lead to window management issues or interface glitches due to Tkinter limitations. Ensure you log in using an X11 (Xorg) desktop session.

### Installing via `.deb` Package

Download the `.deb` package from the [latest release](../../releases/latest) and run:

```bash
sudo apt install ./xvb_*_all.deb
```

Once installed, XVB Player will be available in your applications menu or via terminal by typing `xvb`.

### Running from Source

To install dependencies and run the player directly from source:

```bash
sudo apt install -y python3-tk python3-pil python3-pil.imagetk python3-mpv python3-mutagen ffmpeg libmpv2
git clone https://github.com/JonaDev2026/xvb.git ~/xvb
python3 ~/xvb/xvb.py
```

## Usage

- **Media** — Open a file, a folder containing videos/music, or a stream URL; access recent files.
- **IPTV** — Add playlists and TV guides, record streams, and schedule recordings.
- **Playback, Video, Audio** — Playback controls, image adjustments, and sound options.
- **View** — Toggle full screen, change layout, and select language.
- **Sidebar** — Quick navigation for **Discs** (CDs, DVDs, ISO images), **Local** (media folders, imported files), **URL** (radio, music streams, film streams, RTSP cameras, temporary links), **Recordings**, and **IPTV** (playlists, TV guides).

| Key | Action |
|---|---|
| Space | Pause / Resume |
| ← → | Previous / Next |
| Shift + ← → | Seek 10 seconds |
| ↑ ↓ | Volume control |
| M | Mute / Unmute |
| Backspace | Previous channel (IPTV) |
| F11 | Toggle Full Screen |

Configuration files and user settings are saved in `~/.config/xvb/`. PVR recordings are stored in `~/Videos/xvb/`.

## Legal

XVB Player is a neutral media playback tool. It does not provide, host, or distribute channels, credentials, or copyrighted content. Use XVB only with streams and media you are legally authorized to access. Avoid piracy or circumventing digital protections.

This product uses the TMDB API but is not endorsed or certified by TMDB. Album cover artwork is sourced from MusicBrainz and the Cover Art Archive.

## Support

XVB is free and open-source. If you find this software useful, you can support its development via [PayPal](https://www.paypal.com/paypalme/Jonathanuk).  
Report bugs or submit feature requests on the [GitHub Issues](../../issues) page.

## License

MIT © 2026 Jonathan Sanfilippo
