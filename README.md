# XVB

Lettore IPTV per Linux, leggero, fatto in Python su mpv. Apre le liste
`.m3u` e `.m3u8` che hai sul disco e si adatta da solo alla qualità del
flusso: se la connessione non regge scende di qualità invece di
bloccarsi, e quando sta tranquilla risale.

## Cosa serve

- Linux con Python 3 (provato su Linux Mint, va anche su Ubuntu e Debian)
- mpv, la libreria (`libmpv`)
- Tk per Python (`python3-tk`)
- il pacchetto Python `python-mpv`
- PIL (`python3-pil`), per i loghi e i pallini

## Installazione

Apri il terminale e incolla, un blocco alla volta:

```
sudo apt install -y libmpv2 python3-tk python3-pil python3-pil.imagetk python3-pip
```

Se `libmpv2` non esiste sulla tua versione, usa `libmpv1` al suo posto.

```
pip install python-mpv --break-system-packages
```

Poi crea la cartella dell'app e mettici dentro i file:

```
mkdir -p ~/xvb/icone
```

- `xvb.py` in `~/xvb/`
- le icone (`play.png`, `pausa.png`, `stop.png`, `prima.png`, `dopo.png`,
  `volume.png`, `muto.png`, `pieno.png`) in `~/xvb/icone/`
- se vuoi la tua icona nel menu, un `icon.png` quadrato in `~/xvb/`

Le liste mettile in `~/xvb/lists/` (o in `~/.local/share/xvb/lists/`):
all'avvio l'app carica da sola tutto quello che trova lì dentro, con qualunque nome, con o senza estensione —
basta che dentro sia una lista m3u. Una lista che sta altrove la aggiungi
dall'app col bottone.

## L'immagine delle liste

Accanto al nome di ogni lista, a destra, l'app mette la stessa immagine
per tutte: `img/default.png`, in una cartella `img/` che sta nella stessa
cartella delle liste. Va bene anche jpg o gif. Se non c'è, resta solo il
nome.

```
~/xvb/lists/
    italia.m3u
    sport.m3u
    img/
        default.png
```

I canali invece hanno ognuno un pallino colorato, stile etichette del
Mac: il colore dipende dal nome, quindi ogni canale ha sempre il suo. Il
logo del canale che stai guardando compare nella barra dei comandi, in
basso a sinistra.

## Avvio

```
python3 ~/xvb/xvb.py
```

Le liste in `~/xvb/lists/` sono già a destra. Per una che sta altrove
premi **Aggiungi una lista** e scegli il file. Doppio clic su un canale
per vederlo.

Puoi anche passare una lista direttamente:

```
python3 ~/xvb/xvb.py ~/percorso/lista.m3u
```

## Nel menu delle applicazioni

Per avere XVB nel menu, sotto Audio e video, con la sua icona:

```
cat > ~/.local/share/applications/xvb.desktop <<'EOF'
[Desktop Entry]
Version=1.0
Type=Application
Name=XVB
Comment=IPTV
Exec=python3 /home/TUONOME/tv/xvb.py
Path=/home/TUONOME/tv
Icon=/home/TUONOME/tv/icon.png
Terminal=false
Categories=AudioVideo;Video;Player;TV;
StartupNotify=true
StartupWMClass=Xvb
EOF
update-desktop-database ~/.local/share/applications
```

Al posto di `TUONOME` metti il tuo nome utente (lo vedi con `whoami`).

## Come si usa

| Cosa | Come |
|---|---|
| Cambiare canale | doppio clic nell'elenco, le frecce ai lati di play, oppure ← → sulla tastiera |
| Cercare un canale | scrivi nella casella in alto a sinistra |
| Pausa / riprendi | il bottone, oppure spazio |
| Volume | il cursore in basso a destra, la rotella sopra al cursore, oppure ↑ ↓ |
| Muto | il bottone accanto al volume, oppure M |
| Schermo intero | il bottone, oppure F11; Esc per uscire |
| Togliere una lista | selezionala a destra e premi il bottone Togli, oppure Canc (quelle in `lists/` si tolgono spostando il file) |

Nell'elenco dei canali la riga **viola** è il canale che stai guardando,
quella **grigia** è quella selezionata. Lo stesso a destra per le liste.

A schermo intero i comandi compaiono muovendo il mouse e spariscono da
soli. In basso, la riga di stato dice quale qualità sta usando: cambia
da sola, e quando lo fa lo scrive.

L'app si ricorda le liste, l'ultima usata, l'ultimo canale visto e il
volume: alla riapertura riparte da dove eri.

## Dove finiscono le cose

- impostazioni: `~/.config/xvb/xvb.json`
- loghi dei canali scaricati: `~/.cache/xvb/loghi/` (si può cancellare, si riscarica)

## Se qualcosa non va

**`pip` dice "externally managed environment"** – è normale sulle
distribuzioni recenti: serve `--break-system-packages`, come scritto sopra.

**Si vede ma non si sente, o si sente ma è nero** – è il flusso, non
l'app: prova un altro canale. Le qualità che hanno solo l'audio l'app le
salta già da sola.

**Non parte e dice `No module named mpv`** – `python-mpv` non è
installato per questo Python. Rilancia il comando `pip` di sopra.

**Non parte e dice che manca `libmpv`** – installa `libmpv2` (o
`libmpv1`) con `apt`.

**Nel menu non compare** – il file deve chiamarsi esattamente
`xvb.desktop`, niente `.download` o `.txt` in fondo, e deve stare in
`~/.local/share/applications/`.

**Il logo nella barra non si vede** – manca `python3-pil`: senza legge
solo i PNG. Oppure quella lista non ha i loghi dentro (`tvg-logo`).
