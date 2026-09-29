# XVB — Extended Video Broadcast

Lettore IPTV per Linux, leggero, scritto in Python su libmpv. Legge le
playlist m3u (da disco o da url), mostra la guida TV XMLTV, registra,
tiene i preferiti, e parla quattro lingue. Interfaccia scura, tutta
disegnata dall'app, senza dipendenze grafiche oltre a Tk.

![XVB](preview.png)

## Indice

- [Installazione](#installazione)
- [Le playlist](#le-playlist)
- [I canali](#i-canali)
- [La guida TV](#la-guida-tv)
- [Registrare](#registrare)
- [Preferiti](#preferiti)
- [Video e audio](#video-e-audio)
- [La finestra](#la-finestra)
- [Menu e comandi](#menu-e-comandi)
- [Tastiera](#tastiera)
- [Dove finiscono le cose](#dove-finiscono-le-cose)
- [Aggiornamenti e versioni](#aggiornamenti-e-versioni)
- [Se qualcosa non va](#se-qualcosa-non-va)

## Installazione

### Pacchetto .deb (Debian, Ubuntu, Linux Mint)

Scarica `xvb_<versione>_all.deb` dall'ultima [release](../../releases/latest) e:

```
sudo apt install ./xvb_1.7_all.deb
```

apt tira dentro da solo le dipendenze (`python3-tk`, `python3-pil`,
`python3-pil.imagetk`, `python3-mpv`, `libmpv2` o `libmpv1`) e mette XVB
nel menu, sotto Audio e video, e come comando `xvb`. Per aggiornare si
installa il .deb nuovo sopra al vecchio; config, playlist, preferiti,
guida e registrazioni restano dove sono. Per toglierla: `sudo apt remove xvb`.

Da installata, l'app gira come processo `xvb` (non `python3`), così nel
monitor di sistema ha nome e icona suoi.

### Senza pacchetto

```
sudo apt install -y libmpv2 python3-tk python3-pil python3-pil.imagetk python3-mpv
git clone https://github.com/JonaDev2026/xvb.git ~/xvb
python3 ~/xvb/xvb.py
```

Così le playlist vanno in `~/xvb/playlists/`. Se `libmpv2` non c'è sulla
tua versione usa `libmpv1`; se manca `python3-mpv`, `pip install
python-mpv --break-system-packages`. Si può passare una lista all'avvio:
`python3 xvb.py ~/lista.m3u`.

### Fare il pacchetto

Dalla cartella del repo (con `xvb.py`, `icone/` e `icon.png`):
`sh fai-deb.sh` produce `xvb_<versione>_all.deb` con la versione scritta
in `xvb.py` (`VERSIONE = "..."`). Serve solo `dpkg-deb`, che c'è già.

## Le playlist

Stanno nella barra a destra. L'app legge tutto quello che trova in
`playlists/` (`~/.local/share/xvb/playlists/` da installata, `playlists/`
accanto a `xvb.py` altrimenti): qualunque nome, con o senza estensione,
basta che dentro sia una lista m3u. All'avvio, se la cartella non c'è, la
crea. Playlists → Open folder la apre nel file manager, Reload la rilegge.

**Categorie.** Una sottocartella è una categoria: compare come cartella
colorata, chiusa, e con un clic si apre e mostra le liste che ha dentro.
Il nome è quello della cartella. Se la lista in uso sta in una cartella,
quella si apre da sola all'avvio.

```
playlists/
    italia.m3u
    country/
        uk.m3u
        francia
    favorite/
        favorite.m3u      <- i preferiti, li fa l'app (cartella rossa)
```

**Da url.** Playlists → Add URL: incolli l'url della lista e le dai un
nome. Resta un url: la copia sta in `~/.cache/xvb/liste/` e si riscarica
quando la apri se ha più di sei ore (Playlists → Reload la riscarica
subito; senza rete si usa l'ultima copia). Nella barra ha l'icona del
globo. Si toglie con Playlists → Remove <nome> o con la ✕.

**Colori.** Le cartelle e i globi prendono i colori a turno, sempre un
caldo e un freddo alternati (arancio, verde, giallo, blu, ambra, viola,
celeste…); il rosso è solo dei preferiti, il grigio solo di EPG e
Recordings. Accanto a ogni lista c'è `icone/default.png`.

**La ✕.** Ogni riga della barra a destra (liste, guide, registrazioni)
ha una ✕ sul bordo: cliccandola si toglie quella cosa, dopo una
conferma. Per le liste su disco e le registrazioni cancella davvero i
file; per liste da url e guide le toglie solo dall'elenco.

**Memoria.** L'app ricorda l'ultima playlist e l'ultimo canale e alla
riapertura riparte da lì (se il canale sta in un'altra lista, apre
quella). Ricorda anche volume, lingua, guide, larghezza delle barre e
tutte le impostazioni video/audio.

## I canali

Nella barra a sinistra, con la casella di ricerca in cima (filtra mentre
scrivi; Esc la svuota). Doppio clic o Invio per vedere un canale, le
frecce ‹ › nella barra dei comandi o ← → sulla tastiera per passare al
precedente o al successivo, il bottone switch per tornare al canale di
prima (e ripremuto, di nuovo a questo).

**I pallini.** Ogni canale ha un pallino colorato, stile etichette del
Mac. Se la lista ha i loghi (`tvg-logo`), il colore viene dal logo: l'app
lo scarica in `~/.cache/xvb/loghi/`, butta bianchi, neri e grigi, prende
la tinta dominante e la porta a una luminosità che si veda sullo scuro
(i blu un po' più chiari). Il calcolo si fa una volta e resta in
`~/.cache/xvb/colori.json`; Playlists → Reload lo rifà. Senza logo il
colore dipende dal nome, quindi è sempre lo stesso.

**Il canale in onda.** La sua riga prende come sfondo il colore del
pallino, con testo e pallino bianchi o neri a seconda di quanto è
chiaro. Lo stesso colore va sul titolo del programma nella riga sotto,
sulla progress e nella sfumatura dietro ai comandi.

**Qualità.** Se il canale offre più qualità (master HLS), l'app parte
dalla migliore e ci resta: si cambia solo dal menu dell'ingranaggio,
che elenca le qualità con nome e risoluzione (360p, 720p HD, 1080p Full
HD, 4K…) e la spunta su quella in uso. Le varianti solo audio vengono
scartate. Per far leggere a mpv video e audio insieme (nei flussi con
l'audio in un gruppo separato) l'app scrive una master playlist locale
con la sola variante scelta: `~/.cache/xvb/variante.m3u8`.

**Canali morti.** Se un canale dà errore o non parte entro 30 secondi
si passa al successivo; dopo dieci di fila si ferma e lo dice.

## La guida TV

TV guide → Add URL: l'url (o il percorso) di una guida XMLTV, `.xml` o
`.gz` (lo capisce dal contenuto). Se ne possono aggiungere più d'una, per
esempio una per paese: si uniscono, e per un canale vale la prima che
lo ha. Ognuna compare sotto la cartella EPG col nome del file (quello
dentro all'archivio, se c'è) e ha la sua voce Remove nel menu e la ✕.

La guida si scarica in `~/.cache/xvb/epg/` e si riusa per sei ore, poi si
riprende all'avvio; TV guide → Reload la riscarica subito. In memoria
tiene solo i programmi da un'ora fa in poi, così anche le guide da
decine di MB non pesano.

I canali si trovano col `tvg-id` della lista; se manca, per nome, ridotto
all'osso (minuscolo, senza spazi e punteggiatura, senza HD/FHD/4K in
coda), così "RAI 1 HD" e "Rai1" combaciano. Gli orari hanno il fuso
scritto nella guida e vengono mostrati nell'ora locale del PC, quindi
sono giusti ovunque tu sia.

**Nella riga sotto il video:** canale, titolo del programma in onda (col
colore del canale) e orario, "Rai 1 - Tg1 (20:00 - 20:30)". La riga
sopra i comandi è la progress: quanto del programma è passato, si
riempie da sinistra e cambia da sola al programma dopo. Se il canale
non è in guida lo dice; senza guida la progress resta vuota.

**La griglia.** TV guide → Programme guide, o il bottone EPG nella barra:
una finestra con una riga per canale della lista aperta, le ore in
cima, i programmi come blocchi, il blocco in onda sfumato col colore
del canale, la riga lilla è adesso. Da mezz'ora fa a sei ore avanti;
rotella per le righe, Shift+rotella per il tempo; ore e nomi restano
fermi mentre scorri. Clic su un nome o su un blocco apre il canale; Esc
chiude; si ridisegna ogni 30 secondi.

## Registrare

Il bottone rec registra il canale in onda così com'è, senza
ricodificare (`stream-record` di mpv): zero CPU in più, audio e video
insieme, nella qualità che stai guardando. Il file va in
`~/Videos/xvb/<canale>/<Programma> - <data> <ora>.mkv`, col titolo preso
dalla guida (se manca, il nome del canale). Ripremi per fermare;
cambiando canale si ferma da sola. Mentre registra, in fondo alla riga
di stato pulsa un pallino rosso con "REC" e il tempo che scorre.

**Pianificare.** Record → Schedule…: ora di inizio e di fine (HH:MM) per
il canale in onda; se l'ora è passata intende domani. All'ora giusta
l'app apre il canale, se non ci sei, e registra fino alla fine ("REC
until 21:30"). L'app deve restare aperta. Record → Cancel schedule la
annulla.

**Rivedere.** Nella barra a destra, sotto Recordings, una voce per
canale e giorno ("Rai 1 · 29/09/2026"), dalla più recente. Cliccandola,
a sinistra al posto dei canali compaiono i programmi registrati quel
giorno con l'ora, "20:00 Tg1": doppio clic e parte nel lettore, ‹ ›
passano al precedente e al successivo, la progress mostra a che punto
del file sei. Per tornare ai canali clicca una playlist. La ✕ sulla
voce cancella dal disco tutte le registrazioni di quel giorno, dopo
conferma. Record → Open recordings folder apre la cartella.

## Preferiti

Il cuore nella barra dei comandi mette o toglie il canale in onda dai
preferiti. I preferiti sono una lista vera,
`playlists/favorite/favorite.m3u`, che compare come cartella rossa: la
crea al primo preferito e la cancella quando resta vuota. Un canale
conta come preferito anche se viene da un'altra lista con un altro
indirizzo, purché abbia lo stesso nome.

## Video e audio

Menu **Video**:
- Velocità: x0.5, x0.75, x1, x1.25, x1.5, x2. Anche col bottone x1 nella
  barra, che cicla x1 → x1.25 → x1.5 → x2 → x0.5 → x0.75. Sui flussi live
  sopra x1 il buffer si consuma; ha senso soprattutto sulle registrazioni.
- Proporzioni: Auto, 16:9, 4:3, 21:9; Fill screen taglia le bande.
- Deinterlace, per i canali SD che sfarfallano.
- Luminosità, contrasto, saturazione a passi di 10, e Reset picture.
- Screenshot: un fotogramma in `~/Pictures/xvb/`.
- Always on top: la finestra resta sopra alle altre.

Menu **Audio**:
- Traccia audio e sottotitoli, quando il flusso ne ha (compaiono solo
  allora), con la spunta su quella in uso.
- Volume boost +50%, per i canali bassi.
- Normalize loudness: livella il volume (pubblicità, film).
- Ritardo audio: +100 ms / −100 ms / Reset, anche coi tasti `+` e `-`.
  Serve per i canali con l'audio in anticipo sul video (le due tracce
  hanno basi tempo diverse, non lo può correggere nessun player da
  solo): regoli finché combacia e il valore resta salvato per quel
  canale, la volta dopo riparte già giusto.

Tutte queste impostazioni restano nel config, tranne la velocità che
riparte da x1.

## La finestra

- **Barra in alto** con i menu: Playlists, TV guide, View, Record, Video,
  Audio, About, Language. I menu, le finestre di dialogo e le conferme
  sono disegnati dall'app, scuri come il resto.
- **Barre laterali**: il primo bottone a sinistra nella barra dei comandi
  le toglie e le rimette tutte e due (l'icona si gira quando sono
  chiuse), in finestra e a schermo intero; View → Hide/Show le gestisce
  una per una. Si allargano trascinando la striscia fra la barra e il
  video (da 140 px al 60% della finestra); la larghezza resta salvata.
- **Comandi**: [sidebar] [‹ pausa ›] … [altoparlante, volume, ingranaggio]
  [switch, rec] [x1, EPG] [schermo intero, cuore]. Il volume è una barra
  bianca stile YouTube; l'icona dell'altoparlante cambia con il livello
  (spento, basso, medio, alto, muto). Dietro ai comandi c'è una
  sfumatura del colore del canale che parte dalla progress e si spegne
  verso il basso; le icone ci stanno sopra senza fondo.
- **Schermo intero**: il bottone, F11 o View → Fullscreen; Esc per uscire.
  Con le barre laterali chiuse, comandi e mouse spariscono dopo tre
  secondi fermi e tornano muovendo il mouse — anche in finestra. Con le
  barre aperte restano.
- **Sfondo**: quando non va niente il lettore mostra `icone/screen.png`.
- **Avvisi**: un pannello viola sopra ai comandi, con "xvb" a sinistra,
  per i messaggi importanti (per esempio una versione nuova); sparisce
  da solo o con un clic.
- **Lingua**: menu Language, English / Italiano / Español / Français; si
  applica subito e resta salvata. I testi dell'app sono in inglese di
  base, le traduzioni stanno in un dizionario in cima a `xvb.py`.
- **About**: About → About XVB… mostra icona, versione, autore, licenza
  e il link al repo.

## Menu e comandi

| Menu | Voci |
|---|---|
| Playlists | Add URL, Open folder, Reload, Remove <lista da url> |
| TV guide | Programme guide, Add URL, Reload, Remove <guida> |
| View | Fullscreen, Hide/Show channels, Hide/Show playlists |
| Record | Record now / Stop recording, Schedule…, Cancel schedule, Open recordings folder |
| Video | Speed, Aspect, Fill screen, Deinterlace, Brightness/Contrast/Saturation, Reset picture, Screenshot, Always on top |
| Audio | Track, Subtitles, Volume boost, Normalize loudness, Delay +/−, Reset delay |
| About | About XVB…, Check for updates, Download <versione> |
| Language | English, Italiano, Español, Français |

## Tastiera

| Tasto | Cosa fa |
|---|---|
| Spazio | pausa / riprendi |
| ← → | canale precedente / successivo (anche Pag↑ Pag↓) |
| ↑ ↓ | volume (anche la rotella sopra al cursore) |
| M | muto |
| + − | ritardo audio ±100 ms |
| F11 | schermo intero; Esc per uscire |
| Esc nella ricerca | svuota la casella |

Con il fuoco nella casella di ricerca i tasti scrivono lì; un clic sul
video li rimette al lettore.

## Dove finiscono le cose

- impostazioni: `~/.config/xvb/xvb.json`
- playlist e preferiti: `~/.local/share/xvb/playlists/` (da installata), o `playlists/` accanto a `xvb.py`
- registrazioni: `~/Videos/xvb/<canale>/`
- istantanee: `~/Pictures/xvb/`
- guide TV scaricate: `~/.cache/xvb/epg/`
- liste da url scaricate: `~/.cache/xvb/liste/`
- loghi e colori dei canali: `~/.cache/xvb/loghi/`, `~/.cache/xvb/colori.json`
- la playlist della qualità in uso: `~/.cache/xvb/variante.m3u8`

Tutta la cache si può cancellare: si rifà da sola.

## Aggiornamenti e versioni

All'avvio, dopo qualche secondo, l'app chiede a GitHub l'ultima release:
se è più nuova lo dice nel pannello viola e in About compare "Download
<versione>", che apre la pagina della release. About → Check for
updates lo fa a comando.

Per pubblicare una versione nuova: si alza `VERSIONE` in `xvb.py`, si
rifà il pacchetto con `sh fai-deb.sh`, e il tag della release deve
essere `v` + lo stesso numero (es. `v1.8`), perché è quello che l'app
confronta.

## Se qualcosa non va

**Si vede ma non si sente, o è nero con l'audio** – è il flusso, non
l'app: prova un altro canale.

**Audio in anticipo sul video su un canale** – anche quello è il flusso:
Audio → Delay +100 ms (o `+`) finché combacia, poi resta salvato.

**Un canale non è in guida** – la lista non ha il `tvg-id` giusto e il
nome è troppo diverso da quello della guida. Si può mettere il
`tvg-id` a mano nella riga `#EXTINF` della lista.

**Non parte e dice `No module named mpv`** – manca `python3-mpv` (o
`pip install python-mpv --break-system-packages`).

**Non parte e dice che manca `libmpv`** – `sudo apt install libmpv2` (o
`libmpv1`).

**All'avvio scrive "missing icons"** – mancano delle png in `icone/`:
dice quali.

**La sfumatura o le icone composte non si vedono** – manca `python3-pil`.

**Alta CPU** – la decodifica è software (`hwdec=no`, l'accelerazione dava
schermo nero su alcuni flussi). Su un PC normale un 1080p sta sotto il
10%.

## Licenza

MIT. © 2026 Jonathan Sanfilippo.
