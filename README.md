# DockVault

Backup und Wiederherstellung für Docker-Container auf **Unraid** — mit Weboberfläche,
Zeitstrahl, Container-Verwaltung und einem Restore, der gelöschte Container samt
passendem Unraid-Template zurückholt.

---

## Was es macht

Für jeden Container sichert DockVault vier Dinge:

| Bestandteil | Inhalt |
|---|---|
| `inspect.json` | Die vollständige Container-Konfiguration: Image, Environment, Ports, Mounts, Labels, Restart-Policy, Devices, Capabilities |
| `template.xml` | Das Unraid-Template aus `templates-user` — oder ein **neu erzeugtes**, falls keins existiert |
| `networks.json` | Alle Docker-Netzwerke inklusive IPAM, damit sie beim Restore rekonstruiert werden können |
| `data/*.tar.zst` | Alle Bind-Mounts und benannten Volumes, komprimiert und mit SHA256-Prüfsumme |

Beim Restore lässt sich jeder dieser Teile einzeln oder gemeinsam einspielen.

### Der entscheidende Punkt: gelöschte Container

Wenn ein Container komplett weg ist, macht DockVault Folgendes:

1. Entpackt die Daten an die Originalpfade (Rechte, Eigentümer und Zeitstempel bleiben erhalten)
2. Schreibt `my-<Name>.xml` nach `/boot/config/plugins/dockerMan/templates-user/` — **damit
   erscheint der Container wieder im Docker-Tab der Unraid-WebGUI** und lässt sich dort
   normal bearbeiten
3. Legt den Container exakt aus `inspect.json` neu an; fehlende Netzwerke, Volumes und
   Images werden dabei automatisch erstellt bzw. geladen
4. Startet ihn

Existiert kein Template (typisch für Container aus `docker compose`), **baut DockVault eins
aus der gesicherten Konfiguration** — mit Ports, Pfaden, Variablen und Labels. Vom Image
geerbte Variablen und Labels werden dabei herausgefiltert, damit das Template sauber bleibt.

---

## Installation auf Unraid

Das Image wird bei jedem Push automatisch nach
`ghcr.io/gottschalkfelix4-source/dockvault:latest` veröffentlicht.

### Über das mitgelieferte Template (empfohlen)

Im Unraid-Webterminal:

```bash
wget -O /boot/config/plugins/dockerMan/templates-user/my-dockvault.xml https://raw.githubusercontent.com/gottschalkfelix4-source/dockvault/main/template/dockvault.xml
```

Danach in der WebGUI: **Docker → Add Container → Template „dockvault"** wählen,
Pfade prüfen, **Apply**. Das Image wird dabei automatisch geladen.

### Über docker compose

```bash
docker compose up -d
```

### Selbst bauen

```bash
docker build -t dockvault:latest .
```

Die Web-Oberfläche läuft danach auf `http://<unraid-ip>:8070`.

## Sicherungsumfang: Konfiguration statt Mediathek

Ein Container mountet typischerweise zweierlei: seine **Konfiguration** unter `appdata`
und die **Nutzdaten**, die er verwaltet — Plex' Medienbibliothek, Immichs Fotos,
Downloads. Nur das Erste gehört in ein Container-Backup. Das Zweite ist um
Größenordnungen größer und wird sinnvollerweise anders gesichert.

DockVault sichert deshalb standardmäßig **nur**:

* Bind-Mounts unterhalb eines Verzeichnisses namens `appdata`
* benannte Docker-Volumes
* die Container-Konfiguration und das Unraid-Template

Erkannt wird der **Verzeichnisname**, nicht ein fester Pfad — Unraid-Pools heißen frei
wählbar, appdata liegt je nach Setup unter `/mnt/user/appdata`, `/mnt/cache/appdata` oder
`/mnt/work/appdata`. Eine Tiefengrenze (Vorgabe: 2 Ebenen) sorgt dafür, dass
durchgereichte Ordner anderer Dienste nicht als eigene Konfiguration durchgehen — etwa
`…/appdata/sabvpn/Downloads/complete`, das in einem Radarr-Backup nichts verloren hat.

Alles andere wird übersprungen und im Protokoll sowie im Manifest namentlich aufgeführt.
Die Container-Detailansicht zeigt beide Listen nebeneinander: was gesichert wird und was
bewusst fehlt — samt Begründung.

**Wichtig:** Übersprungen wird nur das *Archivieren der Daten*. `inspect.json` und das
Template behalten **alle** Mounts. Ein wiederhergestellter Plex-Container ist also
vollständig verdrahtet und findet seine Mediathek an Ort und Stelle wieder — sie wurde
ja nie angefasst.

Zwei Stellschrauben in den Einstellungen:

* **Zusätzlich sichern** — einzelne Pfade außerhalb von `appdata` doch mitnehmen
* **Sicherungsumfang: Alle Mounts** — alles archivieren (Vorsicht bei Medienshares)

## Backup-Ziel: lokal oder SMB

Unter **Einstellungen → Backup-Ziel** wird festgelegt, wohin die Archive geschrieben werden.

**Lokaler Pfad** (Voreinstellung) — die Sicherungen landen in dem Verzeichnis, das beim
Anlegen des Containers auf `/backups` gemountet wurde.

**SMB-Freigabe** — DockVault bindet die Freigabe selbst ein; es ist kein Mount auf dem
Unraid-Host und kein Unassigned-Devices-Plugin nötig. In der Oberfläche werden Server,
Freigabe, optionaler Unterordner, Benutzer, Passwort, Domäne und SMB-Version eingetragen.
**Verbindung testen** prüft Erreichbarkeit, Anmeldung, Schreibrechte und freien Platz,
ohne das laufende Ziel anzufassen. Ein angegebener Unterordner wird automatisch angelegt,
falls er noch fehlt.

Dafür braucht der Container zwei Capabilities — im mitgelieferten Template stehen sie
bereits in den *Extra Parameters*:

```
--cap-add SYS_ADMIN --cap-add DAC_READ_SEARCH
```

`SYS_ADMIN` erlaubt das Einhängen, `DAC_READ_SEARCH` braucht `mount.cifs` selbst, um die
Zugangsdatendatei zu lesen. Fehlt eine davon, nennt DockVault sie beim Test namentlich,
statt die kryptische Meldung `Unable to apply new capability set.` durchzureichen.

Das Passwort steht in `/config/settings.json` (nur für root lesbar) und wird von der API
nie im Klartext zurückgegeben. Für den Mount landet es in einer Credentials-Datei unter
`/run` — nie auf der Kommandozeile, wo es in der Prozessliste sichtbar wäre.

Solange ein eingerichtetes SMB-Ziel **nicht** eingebunden ist, verweigert DockVault jedes
Backup. Sonst lägen die Archive unbemerkt im Container und wären beim nächsten Neustart weg.

### Benötigte Mounts

| Host | Im Container | Zweck |
|---|---|---|
| `/var/run/docker.sock` | `/var/run/docker.sock` | **Pflicht** — Docker-API für Backup, Restore, Steuerung |
| `/mnt/user/appdata/dockvault` | `/config` | Einstellungen und Backup-Index (SQLite) |
| `/mnt/user/backups/dockvault` | `/backups` | Die Sicherungsarchive (entfällt praktisch, wenn ein SMB-Ziel genutzt wird) |
| `/mnt/user` | `/mnt/user` | Container-Daten lesen und beim Restore zurückschreiben |
| `/boot/config` | `/boot/config` | Unraid-Templates lesen und schreiben |
| `/var/lib/docker/volumes` | `/var/lib/docker/volumes` | Benannte Volumes |

Ohne `/boot/config` funktioniert alles außer der Template-Wiederherstellung — gelöschte
Container werden dann zwar korrekt neu angelegt, tauchen aber nicht im Docker-Tab auf.

> **Sicherheitshinweis:** Der Docker-Socket gibt dem Container faktisch Root-Rechte auf dem
> Host. DockVault bringt bewusst keine eigene Authentifizierung mit — betreibe es nur im
> vertrauenswürdigen LAN, oder setze einen Reverse-Proxy mit Login davor (z. B. Zoraxy).

---

## Die Oberfläche

**Übersicht** — Schutzstatus aller Container, ungesicherte Container mit Ein-Klick-Backup,
belegter Speicher, wiederherstellbare Waisen.

**Zeitstrahl** — Jede Sicherung, Wiederherstellung und jedes Systemereignis auf einer
Zeitachse, eine Spur pro Container. Zoom von 24 Stunden bis 1 Jahr, Farbe zeigt den Status,
Klick auf einen Punkt öffnet die Details und den Restore. Darunter die Chronologie als Liste.

**Container** — Alle Container als Karten mit Zustand, Backup-Status und Template-Status.
Sichern, Starten, Stoppen, Neustarten, Logs ansehen, Details inklusive Backup-Vorschau
(„was genau würde gesichert und wie groß ist das?"), und für Container ohne Template:
Template erzeugen und direkt in Unraid speichern.

**Backups** — Alle Stände pro Container mit Roh- und Archivgröße, Dauer, Prüfsummen und
Inhaltsverzeichnis. Einzelne Stände lassen sich **anheften**, dann werden sie von der
automatischen Aufbewahrung nie gelöscht.

**Wiederherstellen** — Fehlende Container mit Backup ganz oben, danach Templates ohne
Container, darunter alle Sicherungen. Der Assistent führt in zwei Schritten durch Umfang
und Bestätigung und zeigt vorher genau, was passieren wird.

**Zeitpläne** — Cron-basierte automatische Sicherungen, wahlweise für alle Container oder
eine Auswahl. Neue Container werden bei „alle" automatisch mit erfasst.

**Aufträge** — Verlauf aller Läufe mit vollständigem Protokoll; laufende Jobs lassen sich
abbrechen.

---

## Konsistenz

Standardmäßig wird ein Container vor der Sicherung **angehalten** und danach wieder
gestartet. Das ist der einzige zuverlässige Weg, bei Datenbanken (Postgres, SQLite, Redis)
ein konsistentes Backup zu bekommen. Wer das nicht will, schaltet es in den Einstellungen
ab — dann können Datenbankdateien im Archiv beschädigt sein.

## Aufbewahrung

Nach jedem Backup und nachts um 04:30 Uhr werden alte Stände gelöscht, gesteuert über
Anzahl und Höchstalter. Das jeweils **neueste** Backup eines Containers und alle
angehefteten Stände bleiben immer erhalten.

---

## Aufbau

```
app/
  main.py         FastAPI-App, Statik-Auslieferung, Lifecycle
  api.py          REST-Endpunkte
  config.py       Einstellungen (Umgebung + settings.json)
  db.py           SQLite: Backups, Jobs, Zeitpläne, Ereignisse
  docker_api.py   Docker-Engine-Zugriff
  unraid.py       Templates lesen, schreiben, erzeugen
  archive.py      tar + zstd/gzip mit Fortschritt, Prüfsumme, Pfad-Schutz
  backup.py       Backup-Engine
  restore.py      Restore-Engine
  runner.py       Hintergrund-Jobs mit Live-Fortschritt
  storage.py      Backup-Ziel: lokal oder SMB einbinden, testen, überwachen
  scheduler.py    Cron-Zeitpläne (APScheduler)
  events.py       Server-Sent-Events an die Oberfläche
web/              Oberfläche: reine ES-Module, kein Build-Schritt
template/         Unraid-Template für DockVault selbst
```

Ablage der Sicherungen:

```
/backups/<container>/<JJJJMMTT-HHMMSS>/
  manifest.json      Metadaten, Prüfsummen, Optionen des Laufs
  inspect.json       vollständige Container-Konfiguration
  networks.json      Netzwerk-Definitionen
  template.xml       Unraid-Template
  data/
    volume-<name>.tar.zst
    bind-<name>.tar.zst
```

Das Format ist bewusst offen: Jedes Archiv lässt sich auch ohne DockVault mit
`tar --zstd -xf` entpacken. Der Index in `/config` ist nur ein Cache — geht er verloren,
liest **„Index neu einlesen"** alles aus den `manifest.json`-Dateien zurück.

## API

Die vollständige OpenAPI-Dokumentation liegt unter `/api/docs`.

---

## Entwicklung

```bash
pip install -r requirements.txt
DV_CONFIG_DIR=./data/config DV_BACKUP_DIR=./data/backups python -m app.main
```

Die Oberfläche braucht keinen Build-Schritt — `web/` wird direkt ausgeliefert.

## Lizenz

MIT — siehe [LICENSE](LICENSE).

## Sicherheitsgrenzen

* Restore schreibt ausschließlich unterhalb von `/mnt/user`, `/mnt/cache`, `/mnt/disk1`,
  `/mnt/disks`, `/var/lib/docker/volumes` und `/boot/config`. Alles andere wird abgelehnt.
* Beim Entpacken werden absolute Pfade, `..`-Segmente und aus dem Ziel herausführende
  Links verworfen.
* Ein vorhandenes Template wird vor dem Überschreiben als `.bak-<Zeitstempel>` gesichert.
* DockVault sichert und stoppt sich nie selbst.
