"""Configuration and thresholds. Adjust here, not hardcoded in logic."""

# --- Signal thresholds ---
VOLUME_MULTIPLIER = 3.0       # Kriterium 4: Tagesvolumen >= VOLUME_MULTIPLIER x 20-Tage-Durchschnitt
MOVE_THRESHOLD = 0.05         # Kriterium 5: |Bewegung| >= MOVE_THRESHOLD ggue letztem Schlusskurs

# --- Trend template / RS parameters ---
MIN_HISTORY_DAYS = 200        # Mindesttiefe fuer SMA200
SMA200_TREND_LOOKBACK = 20    # Handelstage, ueber die SMA200-Anstieg geprueft wird
RS_LOOKBACK = 63              # ~3 Monate, fuer RS-Berechnung
RS_TREND_LOOKBACK = 20        # Handelstage, ueber die RS-Trend geprueft wird
BENCHMARK_SYMBOL = "SPY"

# --- News ---
ENABLE_NEWS = True            # Feature-Flag; auf False setzen falls Finnhub-Endpunkt nicht verfuegbar
NEWS_LOOKBACK_DAYS = 3         # wie weit pro Lauf zurueckgeschaut wird (Dedup laeuft ueber state.json)

# --- Data provider ---
TWELVEDATA_MIN_SECONDS_BETWEEN_CALLS = 8.0   # 8 Requests/Minute im Gratis-Tarif
TIME_SERIES_OUTPUTSIZE = 260                  # > 200 fuer SMA200 + 20 Tage Puffer

# --- Paths ---
WATCHLIST_CSV = "data/watchlist.csv"
STATE_JSON = "data/state.json"
RESULTS_JSON = "docs/results.json"
