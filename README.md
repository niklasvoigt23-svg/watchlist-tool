# Watchlist Signal-Tool

Serverloses Ampel-Dashboard mit Telegram-Alarmen fuer eine Aktien-Watchlist, angelehnt an Mark
Minervinis SEPA-Ansatz: drei unabhaengige Ampeln (Trendstruktur, Relative Staerke, Fundamental)
plus Einzelereignis-Alarme (Golden/Death Cross, Volumen-Breakout, Kursbewegung, 52-Wochen-Hoch,
Pivotal News).

## Architektur

- **Backend**: Python-Skripte (`scripts/`), ausgefuehrt via GitHub Actions (`.github/workflows/screen.yml`).
- **Trigger**: bewusst **kein** `schedule:`-Cron (GitHubs eingebauter Scheduler ist bei hoher
  Last nachweislich unzuverlaessig). Stattdessen loest [cron-job.org](https://cron-job.org)
  extern per authentifiziertem POST den `workflow_dispatch`-Trigger aus.
- **Frontend**: statische Seite auf GitHub Pages (`docs/index.html`), liest `docs/results.json`.
- **Datenhaltung**: keine Datenbank, alles als Dateien im Repo (`data/watchlist.csv`,
  `data/state.json`, `docs/results.json`).
- **Alarme**: Telegram Bot API.
- **Datenquellen**: [Twelve Data](https://twelvedata.com) (Kurse/Volumen), [Finnhub](https://finnhub.io)
  (News + Fundamentaldaten via `/stock/metric`).
- **Signalberechnung**: [TA-Lib](https://ta-lib.org/) (SMA, Crossover).

## Signale

**Drei unabhaengige Dauer-Ampeln** (grau/gruen/rot), jede mit eigenem Status-Wechsel-Alarm, nur
im vollen taeglichen Lauf berechnet:

- **Trendstruktur**: Kurs > SMA50 > SMA150 > SMA200 (in der richtigen Reihenfolge) und SMA200
  seit >=20 Handelstagen steigend. Braucht >=200 Handelstage Historie.
- **Relative Staerke**: RS (ggue. SPY, 63-Tage-Fenster) > 0 und steigend ueber die letzten 20
  Handelstage. Braucht nur ~84 Handelstage Historie -- unabhaengig von Trendstruktur, kann also
  frueher aussagekraeftig sein.
- **Fundamental**: EPS-Wachstum Q/YoY >= 20 %, Umsatzwachstum Q/YoY >= 15 %, Nettomarge nicht
  ruecklaeufig ggue. Vorquartal -- alle drei ueber Finnhub Basic-Financials. Fehlt eines der drei
  Felder, zeigt die Ampel grau statt rot (siehe "Verifizierte Annahmen" oben).

**Einzelereignis-Alarme** (gededuped ueber `state.json`, pro Tag max. einmal):

- Golden/Death Cross (SMA50 x SMA200), nur im vollen Lauf.
- Volumen-Breakout (>= `VOLUME_MULTIPLIER`x 20-Tage-Durchschnitt), voller Lauf + Intraday.
- Kursbewegung (>= `MOVE_THRESHOLD` ggue. letztem Schlusskurs), voller Lauf + Intraday.
- Ausbruch ueber das 52-Wochen-Hoch (252 Handelstage, heute ausgeschlossen), voller Lauf +
  Intraday.
- Pivotal News (neue Finnhub-Company-News), voller Lauf + Intraday.

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
- **Finnhub Basic-Financials-Endpunkt** (`/stock/metric?metric=all`), fuer die Fundamental-Ampel,
  getestet mit TWST, QBTS, NSIT sowie mehreren Micro-Caps der Watchlist:
  - `epsGrowthQuarterlyYoy` und `revenueGrowthQuarterlyYoy` existieren im Gratis-Tarif, sind aber
    **prozentskaliert** (z.B. `23.24` = 23,24 %) -- der Code konvertiert das intern zu
    Dezimalbruechen, konsistent mit `EPS_GROWTH_MIN`/`REVENUE_GROWTH_MIN` in `config.py`.
  - `epsGrowthQuarterlyYoy` ist bei verlustschreibenden Wachstumswerten (z.B. TWST, QBTS) haeufig
    leer -- Finnhub laesst das Feld bewusst weg, wenn die Vorjahresquartals-EPS negativ/nahe null
    war (Wachstum in % waere dort irrefuehrend). Betroffene Ticker zeigen die Fundamental-Ampel
    korrekt grau statt rot.
  - Fuer den Margentrend ("nicht ruecklaeufig ggue. Vorquartal") gibt es **keinen passenden
    Einzelwert** in `metric` -- stattdessen liefert die Antwort zusaetzlich
    `series.quarterly.netMargin` als echte Quartalszeitreihe (bereits dezimalskaliert). Der Code
    vergleicht `netMargin[0]` (aktuelles Quartal) gegen `netMargin[1]` (Vorquartal) daraus, statt
    der TTM-Kennzahl -- das ist die einzige Moeglichkeit, tatsaechlich "gegenueber dem Vorquartal"
    zu vergleichen. Kleine Abweichung vom Nachtrags-Wortlaut ("aktuelle TTM-Marge"), aber
    sachlich das, was gemeint war.
  - Duenn abgedeckte Micro-Caps (z.B. CBRS, MMED) liefern fuer alle drei benoetigten Felder leer
    -- Fundamental-Ampel zeigt dann grau, wie bei jedem anderen Datenausfall auch.
- **Twelve Data Abdeckung** (Startticker geprueft):
  - Alle verbliebenen Ticker der Watchlist liefern taegliche Kurs-/Volumenhistorie auf dem
    Gratis-Tarif. (`VH2` und `HTFL` wurden zwischenzeitlich von dir selbst aus der Watchlist
    entfernt -- `VH2` war ohnehin nicht nutzbar: Twelve Data loeste das Symbol nur als
    *Friedrich Vorwerk Group SE*, eine deutsche XETRA-Aktie, auf, und selbst die haette einen
    bezahlten Pro/Venture-Tarif gebraucht.)
  - **Einige Ticker haben aktuell weniger als 200 Handelstage Historie** (zu neu gelistet), daher
    ist SMA200 / Trendstruktur dort noch nicht berechenbar (Dashboard zeigt grau,
    "zu wenig Historie", kein Fehlzustand). Die **Relative-Staerke-Ampel ist davon unabhaengig**
    und braucht nur ~84 Handelstage -- bei einigen dieser Ticker (z.B. CBRS) ist sie bereits
    berechenbar, obwohl Trendstruktur noch grau ist. Das ist ein direkter Vorteil der Aufteilung
    in zwei Ampeln gegenueber der alten kombinierten Trend-Template-Ampel.
- **Rate Limit**: Twelve Data Gratis-Tarif ist mit 8 Requests/Minute knapp bemessen. Das Skript
  pausiert automatisch ~8s zwischen Calls und wiederholt einen 429-Fehler bis zu zweimal mit
  laengerer Pause.
- **github.dev-Deep-Link fuer "Ticker hinzufuegen"**: in einem frischen, nicht bei GitHub
  angemeldeten Browser verlangt `github.dev/{owner}/{repo}/blob/...` zwingend eine GitHub-Anmeldung
  ("Melden Sie sich bei GitHub an, um auf den Inhalt dieses Repositorys zuzugreifen") -- und zwar
  bereits beim Oeffnen des Repos, nicht erst bei der Datei, das vorgeschlagene Fallback ohne
  Dateipfad haette also denselben Fehler gezeigt. Ob das in deinem eigenen, bereits bei
  github.com angemeldeten Browser reibungslos funktioniert, konnte ich von hier aus nicht
  verifizieren. Der Link zeigt deshalb bis auf Weiteres weiter auf den einfachen
  github.com-Zeileneditor (`/edit/main/...`), der nachweislich funktioniert -- sag Bescheid, falls
  du github.dev in deinem Browser getestet hast und es umgestellt werden soll.
- **End-to-End lokal getestet**: `--mode full` und `--mode intraday` inkl. Fundamental-Ampel und
  52-Wochen-Hoch wurden mit echten API-Keys durchlaufen, inkl. Dedup-Pruefung (Folgelauf erzeugte
  korrekt keine Wiederholungs-Alarme) und einer Migrationspruefung des alten `state.json`-Schemas.

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
`https://niklasvoigt23-svg.github.io/watchlist-tool/` erreichbar (URL nicht ungefragt weitergeben).

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

- **URL**: `https://api.github.com/repos/niklasvoigt23-svg/watchlist-tool/actions/workflows/screen.yml/dispatches`
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
`MOVE_THRESHOLD`, `RS_LOOKBACK`, `EPS_GROWTH_MIN`, `REVENUE_GROWTH_MIN`, `YEAR_HIGH_LOOKBACK`
etc.) -- nichts davon ist in der Ablauflogik hart verdrahtet.

## Explizit nicht Teil dieses Tools

Automatische VCP-/Konsolidierungserkennung, automatische Order-Ausfuehrung,
Portfolio-/Positionsmanagement, IBKR-Anbindung. Bewusst kein ROE-Schwellenwert in der
Fundamental-Ampel -- die gaengige 17%-Zahl stammt aus O'Neils CANSLIM, nicht aus Minervinis SEPA.

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
