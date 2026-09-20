# Watchlist Signal-Tool

Serverloses Chart-Ampel-Dashboard mit Telegram-Alarmen fuer eine Aktien-Watchlist. Rein
technische Signale (Kurs, Volumen, News) auf Basis einer Trend-/Momentum-Strategie angelehnt
an Mark Minervinis SEPA-Ansatz. Keine Fundamentaldaten.

## Architektur

- **Backend**: Python-Skripte (`scripts/`), ausgefuehrt via GitHub Actions (`.github/workflows/screen.yml`).
- **Trigger**: bewusst **kein** `schedule:`-Cron (GitHubs eingebauter Scheduler ist bei hoher
  Last nachweislich unzuverlaessig). Stattdessen loest [cron-job.org](https://cron-job.org)
  extern per authentifiziertem POST den `workflow_dispatch`-Trigger aus.
- **Frontend**: statische Seite auf GitHub Pages (`docs/index.html`), liest `docs/results.json`.
- **Datenhaltung**: keine Datenbank, alles als Dateien im Repo (`data/watchlist.csv`,
  `data/state.json`, `docs/results.json`).
- **Alarme**: Telegram Bot API.
- **Datenquellen**: [Twelve Data](https://twelvedata.com) (Kurse/Volumen), [Finnhub](https://finnhub.io) (News).
- **Signalberechnung**: [TA-Lib](https://ta-lib.org/) (SMA, Crossover).

## Wichtig: Sichtbarkeit dieses Repos

Dieses Repo ist bewusst **oeffentlich**, weil GitHub Pages auf einem kostenlosen persoenlichen
Account nur aus einem oeffentlichen Repository heraus veroeffentlicht werden kann (GitHub Pro
waere fuer ein privates Repo noetig, aber selbst dann ist die publizierte Seite ohne
GitHub-Enterprise-Cloud-Organisation technisch oeffentlich erreichbar -- es gibt auf
persoenlichen Accounts keinen echten Login-Schutz fuer Pages). Das heisst: **Watchlist,
Signal-Historie (`data/state.json`) und Commit-Verlauf sind fuer jeden auf GitHub einsehbar**,
nicht nur das Dashboard selbst. API-Keys sind trotzdem sicher -- GitHub Actions maskiert
Secrets in Logs immer, unabhaengig von der Repo-Sichtbarkeit. Falls sich das spaeter doch als
unpraktikabel herausstellt: Upgrade auf GitHub Pro (4 $/Monat) + Repo auf privat stellen ist
jederzeit nachtraeglich moeglich.

## Verifizierte Annahmen (vor dem Bau geprueft)

- **Finnhub-News-Endpunkt**: funktioniert auf dem kostenlosen Tarif (`company-news`), getestet
  mit TWST, QBTS, NSIT -- alle drei lieferten aktuelle Artikel. News-Feature ist aktiv
  (`ENABLE_NEWS = True` in `scripts/config.py`).
- **Twelve Data Abdeckung** (17 Startticker geprueft):
  - 16 von 17 Tickern liefern taegliche Kurs-/Volumenhistorie auf dem Gratis-Tarif.
  - **VH2 ist NICHT nutzbar**: Twelve Data loest `VH2` nur als *Friedrich Vorwerk Group SE*
    (deutsche Aktie, XETR/Frankfurt/Muenchen/Wien, EUR) auf, und selbst dieses Symbol
    verlangt einen bezahlten Pro/Venture-Tarif ("This symbol is available starting with the
    Pro or Venture plan"). Falls mit `VH2` ein anderer (z.B. US-OTC-) Titel gemeint war, bitte
    Ticker in `data/watchlist.csv` korrigieren. Bis dahin zeigt das Dashboard fuer VH2 grau
    ("keine Daten") und die Zeile ist im Code/CSV entsprechend markiert.
  - **4 Ticker haben aktuell weniger als 200 Handelstage Historie** (zu neu gelistet), daher
    ist SMA200 / Trend-Template dort noch nicht berechenbar (Dashboard zeigt grau,
    "zu wenig Historie", kein Fehlzustand):
    - CBRS (~88 Handelstage)
    - MMED (~136 Handelstage)
    - YSWY (~104 Handelstage)
    - APMD (~35 Handelstage)

    Das behebt sich von selbst, sobald diese Titel laenger gelistet sind.
- **Rate Limit**: Twelve Data Gratis-Tarif ist mit 8 Requests/Minute knapp bemessen. Das Skript
  pausiert automatisch ~8s zwischen Calls und wiederholt einen 429-Fehler einmal mit laengerer
  Pause. Bei 17-50 Tickern bleibt der Tagesverbrauch (800/Tag) trotzdem deutlich unter dem Limit.
- **End-to-End lokal getestet**: `--mode full` und `--mode intraday` wurden mit echten API-Keys
  gegen alle 17 Ticker durchlaufen, inkl. Dedup-Pruefung (zweiter Lauf direkt danach erzeugte
  korrekt keine Wiederholungs-Alarme).

## Setup

### 1. Repository

Push dieses Projekt in ein **oeffentliches** GitHub-Repository (siehe Hinweis oben zur
Sichtbarkeit).

### 2. GitHub Secrets

Unter **Settings -> Secrets and variables -> Actions -> New repository secret** anlegen:

| Secret | Wert |
|---|---|
| `TWELVEDATA_API_KEY` | dein Twelve-Data-API-Key (twelvedata.com/account/api-keys) |
| `FINNHUB_API_KEY` | dein Finnhub-API-Key (finnhub.io/dashboard) |
| `TELEGRAM_BOT_TOKEN` | siehe Telegram-Setup unten |
| `TELEGRAM_CHAT_ID` | siehe Telegram-Setup unten |

### 3. GitHub Pages aktivieren

**Settings -> Pages -> Build and deployment -> Source: "Deploy from a branch"**, Branch
**`main`**, Ordner **`/docs`**, speichern. Die Seite ist danach unter
`https://{owner}.github.io/{repo}/` erreichbar (URL nicht ungefragt weitergeben).

Trag die Repo-Adresse zusaetzlich in `docs/index.html` ein (Zeile `const REPO = "OWNER/REPO";`)
-- das speist den "Ticker hinzufuegen"-Link.

### 4. Personal Access Token fuer cron-job.org

1. GitHub -> **Settings -> Developer settings -> Personal access tokens -> Fine-grained tokens
   -> Generate new token**.
2. Name z.B. `watchlist-cron-dispatch`, Ablaufdatum nach Wunsch (laengste sinnvolle Laufzeit
   oder "No expiration", dann aber im Kalender fuer Erneuerung vormerken).
3. **Repository access**: "Only select repositories" -> dieses Repo waehlen.
4. **Permissions**: unter "Repository permissions" -> **Actions: Read and write** (ist der
   einzige benoetigte Scope fuer `workflow_dispatch`).
5. Token generieren, **einmalig sichtbar** -- direkt in cron-job.org einsetzen oder sicher
   zwischenspeichern (z.B. Passwortmanager, nicht in dieses Repo committen).

### 5. cron-job.org einrichten

Auf [cron-job.org](https://cron-job.org/en/) registrieren (kostenlos), dann **fuenf** Cronjobs
anlegen (Wochentage Mo-Fr, Berliner Zeit):

| Uhrzeit (Berlin) | `mode`-Wert im Body |
|---|---|
| 16:00 | `intraday` |
| 18:00 | `intraday` |
| 20:00 | `intraday` |
| 22:00 | `intraday` |
| 22:30 | `full` |

> Diese Uhrzeiten sind Ausgangswerte und noch nicht final gegen US-Marktzeiten (inkl.
> Sommer-/Winterzeit) abgeglichen -- einfach in cron-job.org anpassen, es steckt nichts davon
> hart codiert im Skript. `mode=full` sollte nach US-Handelsschluss laufen (kann je nach
> Sommer-/Winterzeit zwischen 21:00 und 22:30 Berliner Zeit liegen).

Fuer jeden Job in cron-job.org:

- **URL**: `https://api.github.com/repos/{owner}/{repo}/actions/workflows/screen.yml/dispatches`
- **Methode**: `POST`
- **Header**:
  - `Authorization: Bearer <DEIN_GITHUB_PAT>`
  - `Accept: application/vnd.github+json`
  - `Content-Type: application/json`
- **Body** (JSON):
  ```json
  { "ref": "main", "inputs": { "mode": "intraday" } }
  ```
  (fuer den 22:30-Job entsprechend `"mode": "full"`)
- **Zeitplan**: die jeweilige Uhrzeit, Wochentage Mo-Fr, Zeitzone Europe/Berlin.

### 6. Telegram-Bot einrichten

1. In Telegram [@BotFather](https://t.me/botfather) oeffnen, `/newbot`, Namen und Username
   vergeben -> Bot-Token erhalten.
2. Dem neuen Bot einmal selbst schreiben (z.B. "Hallo"), dann im Browser
   `https://api.telegram.org/bot<TOKEN>/getUpdates` aufrufen -- die eigene `chat.id` aus der
   JSON-Antwort ablesen.
3. Token als `TELEGRAM_BOT_TOKEN`, Chat-ID als `TELEGRAM_CHAT_ID` unter den Repo-Secrets
   hinterlegen (siehe Schritt 2).

### 7. Einmal manuell testen

Vor der produktiven Anbindung von cron-job.org: **Actions -> Watchlist Screen -> Run workflow**,
`mode = full` waehlen, Lauf beobachten. Alternativ per `gh`:

```bash
gh workflow run screen.yml -f mode=full
```

Pruefen: Workflow gruen, `data/state.json` und `docs/results.json` wurden committet, Dashboard
zeigt Daten, und falls Alarme ausgeloest wurden, kam eine Telegram-Nachricht an.

## Watchlist pflegen

`data/watchlist.csv` direkt ueber GitHubs Web-Editor bearbeiten (Spalten `ticker,since,note`),
auch vom Handy aus moeglich. Auf dem Dashboard fuehrt der Button "Ticker hinzufuegen" direkt
zum Editor.

## Konfiguration anpassen

Schwellenwerte und Parameter stehen zentral in `scripts/config.py` (`VOLUME_MULTIPLIER`,
`MOVE_THRESHOLD`, `RS_LOOKBACK` etc.) -- nichts davon ist in der Ablauflogik hart verdrahtet.

## Explizit nicht Teil dieses Tools

Fundamentaldaten (EPS, Umsatz, ROE, Margen), automatische VCP-/Konsolidierungserkennung,
automatische Order-Ausfuehrung, Portfolio-/Positionsmanagement, IBKR-Anbindung.

## Lokale Entwicklung

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
$env:TWELVEDATA_API_KEY = "..."
$env:FINNHUB_API_KEY = "..."
.venv\Scripts\python scripts\screen.py --mode full
```

`TA-Lib` hat fertige Wheels fuer Windows/macOS/Linux auf PyPI, lokal ist i.d.R. kein manueller
C-Build noetig. Auf dem GitHub-Actions-Runner (Ubuntu) baut der Workflow die TA-Lib-C-Bibliothek
einmalig aus dem Quellcode und cached sie.
