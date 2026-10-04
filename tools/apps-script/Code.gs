/**
 * Watchlist Tool - Datenbruecke (Google Apps Script, an das Sheet "Watchlist Tool - Master" gebunden)
 *
 * Holt die oeffentlichen Ergebnis-Dateien des Screeners (GitHub) und legt sie in drei Tabs ab,
 * die das Wochenblick-Artifact ueber den Google-Sheets-Connector liest:
 *   Results     eine Zeile je Ticker: Kurs, drei Ampeln (inkl. Grund bei grau), NR4/NR7
 *   Signal-Log  ein Eintrag je (Datum, Ticker, Signaltyp), waechst mit, aelter als LOG_KEEP_DAYS faellt weg
 *   Meta        Stand des letzten Abrufs und Status
 *
 * Es werden nur oeffentliche URLs gelesen. Keine Passwoerter, keine Schluessel, kein Schreibzugriff
 * auf GitHub. Alle Zellen sind Text (Dezimalpunkt), damit die Sheet-Locale nichts umformatiert.
 *
 * Einrichtung: siehe README.md, Abschnitt "Wochenblick und Sheet-Drehscheibe".
 */

const SOURCE = {
  resultsUrl: 'https://niklasvoigt23-svg.github.io/watchlist-tool/results.json',
  stateUrl: 'https://raw.githubusercontent.com/niklasvoigt23-svg/watchlist-tool/main/data/state.json',
};
const TABS = { results: 'Results', log: 'Signal-Log', meta: 'Meta' };
const LOG_KEEP_DAYS = 45;
const SCRIPT_VERSION = '1.0';

const RESULTS_HEADER = [
  'ticker', 'company_name', 'price', 'data_status',
  'trendstruktur', 'trendstruktur_reason',
  'relative_staerke', 'relative_staerke_reason',
  'fundamental', 'fundamental_reason',
  'nr4', 'nr7', 'note',
];
const LOG_HEADER = ['date', 'ticker', 'type', 'label', 'direction', 'value', 'rvol_ratio', 'rvol_tier', 'source'];

// Spalte des Status je Ampel in RESULTS_HEADER (der Grund steht direkt dahinter).
const AMPEL_COLUMNS = [['trendstruktur', 4], ['relative_staerke', 6], ['fundamental', 8]];

// state.json speichert je Signaltyp nur das Datum des letzten Alarms. Fallback fuer Tage, an denen
// results.json (events_today) nicht abgegriffen wurde; ohne Details wie Wert und RVOL.
const STATE_SIGNALS = {
  golden_cross_last_alert_date: { type: 'golden_cross', label: 'Golden Cross 50/200', direction: 'bullish' },
  death_cross_last_alert_date: { type: 'death_cross', label: 'Death Cross 50/200', direction: 'bearish' },
  volume_breakout_last_alert_date: { type: 'volume_breakout', label: 'Volumen-Breakout', direction: 'bullish' },
  move_alert_last_alert_date: { type: 'move', label: 'Kursbewegung', direction: 'neutral' },
  year_high_last_alert_date: { type: 'year_high', label: '52-Wochen-Hoch', direction: 'bullish' },
  pocket_pivot_last_alert_date: { type: 'pocket_pivot', label: 'Pocket Pivot', direction: 'bullish' },
  aroon_bull_last_alert_date: { type: 'aroon_bullish', label: 'Aroon-Crossover (bullisch)', direction: 'bullish' },
  aroon_bear_last_alert_date: { type: 'aroon_bearish', label: 'Aroon-Crossover (bearisch)', direction: 'bearish' },
  squeeze_bull_last_alert_date: { type: 'squeeze_bullish', label: 'TTM Squeeze Fire (bullisch)', direction: 'bullish' },
  squeeze_bear_last_alert_date: { type: 'squeeze_bearish', label: 'TTM Squeeze Fire (bearisch)', direction: 'bearish' },
};

// ---------------------------------------------------------------------------------------------
// Reine Funktionen (ohne Google-APIs, einzeln testbar)
// ---------------------------------------------------------------------------------------------

function numText_(value, digits) {
  if (typeof value !== 'number' || !isFinite(value)) return '';
  return String(Number(value.toFixed(digits === undefined ? 4 : digits)));
}

/**
 * "+5.5%" -> "5.5", "-5.9%" -> "-5.9". Sheets liest Text mit fuehrendem "+" als Formel (#ERROR!),
 * deshalb steht im Log nur die Zahl; Vorzeichen und Prozentzeichen setzt das Artifact beim Anzeigen.
 */
function valueText_(value) {
  if (value === null || value === undefined) return '';
  const match = String(value).trim().match(/^([+-]?\d+(?:[.,]\d+)?)\s*%?$/);
  return match ? String(parseFloat(match[1].replace(',', '.'))) : '';
}

/** Text, der mit = + - @ beginnt, wuerde von Sheets als Formel gelesen; ein Leerzeichen davor verhindert das. */
function safeText_(value) {
  const text = value === null || value === undefined ? '' : String(value);
  return /^[=+\-@]/.test(text) ? ' ' + text : text;
}

function isIsoDate_(value) {
  return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value);
}

function shiftDate_(iso, days) {
  const d = new Date(iso + 'T00:00:00Z');
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

/**
 * Zeilen fuer den Results-Tab. previousRows (alter Tab-Inhalt ohne Kopfzeile) behaelt den Grund einer
 * grauen Ampel, wenn der neue Lauf (Intraday) keinen mitliefert.
 */
function buildResultsRows_(results, previousRows) {
  const previous = {};
  (previousRows || []).forEach(function (row) { previous[row[0]] = row; });
  const tickers = (results && results.tickers) || {};

  return Object.keys(tickers).sort().map(function (ticker) {
    const r = tickers[ticker] || {};
    const old = previous[ticker];
    const row = [ticker, safeText_(r.company_name), numText_(r.price, 4), r.data_status || ''];
    AMPEL_COLUMNS.forEach(function (column) {
      const block = r[column[0]] || {};
      const status = block.status || 'grey';
      let reason = block.reason || '';
      if (!reason && status === 'grey' && old && old[column[1]] === 'grey') reason = old[column[1] + 1] || '';
      row.push(status, reason);
    });
    row.push(r.nr4_active ? 'TRUE' : 'FALSE', r.nr7_active ? 'TRUE' : 'FALSE', safeText_(r.note));
    return row;
  });
}

/**
 * Fuehrt bestehendes Log, die Ereignisse des aktuellen Laufs (results.events_today) und das
 * state.json-Fallback zusammen. Schluessel: Datum|Ticker|Typ. Details aus results ersetzen einen
 * Fallback-Eintrag. Gibt die Zeilen nach Datum und Ticker sortiert zurueck.
 */
function mergeLogRows_(existingRows, results, state, todayIso) {
  const byKey = {};
  const order = [];
  function put(row) {
    const key = row[0] + '|' + row[1] + '|' + row[2];
    if (!byKey[key]) order.push(key);
    byKey[key] = row;
  }
  function has(key) { return !!byKey[key]; }

  (existingRows || []).forEach(function (row) {
    if (row[0] && row[1] && row[2]) {
      const full = LOG_HEADER.map(function (_, i) { return row[i] === undefined ? '' : String(row[i]); });
      put(full);
    }
  });

  const runDate = String((results && results.last_run_at) || '').slice(0, 10);
  if (isIsoDate_(runDate)) {
    const tickers = (results && results.tickers) || {};
    Object.keys(tickers).forEach(function (ticker) {
      const events = (tickers[ticker] || {}).events_today;
      if (!Array.isArray(events)) return;
      events.forEach(function (e) {
        if (!e || !e.type) return;
        const key = runDate + '|' + ticker + '|' + e.type;
        if (has(key) && byKey[key][8] !== 'state') return;
        put([runDate, ticker, e.type, safeText_(e.label), e.direction || '',
             valueText_(e.value), numText_(e.rvol_ratio, 2), e.rvol_tier || '', 'results']);
      });
    });
  }

  const cutoff = shiftDate_(todayIso, -LOG_KEEP_DAYS);
  Object.keys(state || {}).forEach(function (ticker) {
    const entry = state[ticker] || {};
    Object.keys(STATE_SIGNALS).forEach(function (stateKey) {
      const date = entry[stateKey];
      if (!isIsoDate_(date) || date < cutoff) return;
      const meta = STATE_SIGNALS[stateKey];
      const key = date + '|' + ticker + '|' + meta.type;
      if (has(key)) return;
      put([date, ticker, meta.type, meta.label, meta.direction, '', '', '', 'state']);
    });
  });

  const rows = order.map(function (key) { return byKey[key]; })
    .filter(function (row) { return row[0] >= cutoff; });
  rows.sort(function (a, b) {
    if (a[0] !== b[0]) return a[0] < b[0] ? -1 : 1;
    if (a[1] !== b[1]) return a[1] < b[1] ? -1 : 1;
    return a[2] < b[2] ? -1 : a[2] > b[2] ? 1 : 0;
  });
  return rows;
}

// ---------------------------------------------------------------------------------------------
// Google-Teil
// ---------------------------------------------------------------------------------------------

function fetchJson_(url) {
  const response = UrlFetchApp.fetch(url + '?_=' + Date.now(), { muteHttpExceptions: true });
  const code = response.getResponseCode();
  if (code !== 200) throw new Error('HTTP ' + code + ' bei ' + url.split('/').pop());
  return JSON.parse(response.getContentText());
}

function cellText_(value) {
  if (value instanceof Date) return Utilities.formatDate(value, 'UTC', 'yyyy-MM-dd');
  return value === null || value === undefined ? '' : String(value);
}

function readRows_(sheet, columns) {
  const last = sheet.getLastRow();
  if (last < 2) return [];
  return sheet.getRange(2, 1, last - 1, columns).getValues()
    .map(function (row) { return row.map(cellText_); });
}

function sheet_(ss, name) {
  return ss.getSheetByName(name) || ss.insertSheet(name);
}

function writeTable_(sheet, header, rows) {
  const data = [header].concat(rows);
  sheet.clearContents();
  const range = sheet.getRange(1, 1, data.length, header.length);
  range.setNumberFormat('@');
  range.setValues(data);
  sheet.setFrozenRows(1);
}

function readMeta_(ss) {
  const sheet = ss.getSheetByName(TABS.meta);
  const meta = {};
  if (!sheet || sheet.getLastRow() < 1) return meta;
  sheet.getRange(1, 1, sheet.getLastRow(), 2).getValues().forEach(function (row) {
    if (row[0]) meta[String(row[0])] = cellText_(row[1]);
  });
  return meta;
}

function writeMeta_(ss, meta) {
  const sheet = sheet_(ss, TABS.meta);
  const keys = ['fetched_at', 'status', 'last_success_at', 'source_last_run_at', 'source_mode',
                'ticker_count', 'warning', 'script_version'];
  const rows = keys.map(function (key) { return [key, meta[key] === undefined ? '' : String(meta[key])]; });
  sheet.clearContents();
  const range = sheet.getRange(1, 1, rows.length, 2);
  range.setNumberFormat('@');
  range.setValues(rows);
}

function refreshWatchlistData() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const now = new Date();
  const meta = readMeta_(ss);
  meta.fetched_at = now.toISOString();
  meta.script_version = SCRIPT_VERSION;
  meta.warning = '';

  try {
    const results = fetchJson_(SOURCE.resultsUrl);
    if (!results || !results.tickers || Object.keys(results.tickers).length === 0) {
      throw new Error('results.json enthaelt keine Ticker');
    }

    let state = {};
    try {
      state = fetchJson_(SOURCE.stateUrl);
    } catch (stateError) {
      meta.warning = 'state.json nicht ladbar (' + stateError.message + '), Signal-Log nur aus results.json';
    }

    const resultsSheet = sheet_(ss, TABS.results);
    writeTable_(resultsSheet, RESULTS_HEADER,
      buildResultsRows_(results, readRows_(resultsSheet, RESULTS_HEADER.length)));

    const logSheet = sheet_(ss, TABS.log);
    writeTable_(logSheet, LOG_HEADER,
      mergeLogRows_(readRows_(logSheet, LOG_HEADER.length), results, state, now.toISOString().slice(0, 10)));

    meta.status = 'ok';
    meta.last_success_at = now.toISOString();
    meta.source_last_run_at = results.last_run_at || '';
    meta.source_mode = results.last_run_mode || '';
    meta.ticker_count = Object.keys(results.tickers).length;
  } catch (error) {
    meta.status = 'Fehler: ' + error.message;
  }
  writeMeta_(ss, meta);
}

// ---------------------------------------------------------------------------------------------
// Bedienung
// ---------------------------------------------------------------------------------------------

function onOpen() {
  SpreadsheetApp.getUi().createMenu('Watchlist-Daten')
    .addItem('Jetzt aktualisieren', 'refreshWatchlistData')
    .addItem('Stündlichen Abruf einrichten', 'installHourlyTrigger')
    .addToUi();
}

function installHourlyTrigger() {
  ScriptApp.getProjectTriggers()
    .filter(function (trigger) { return trigger.getHandlerFunction() === 'refreshWatchlistData'; })
    .forEach(function (trigger) { ScriptApp.deleteTrigger(trigger); });
  ScriptApp.newTrigger('refreshWatchlistData').timeBased().everyHours(1).create();
}
