"""Configuration and thresholds. Adjust here, not hardcoded in logic."""

# --- Signal thresholds ---
VOLUME_MULTIPLIER = 3.0       # Kriterium 4: Tagesvolumen >= VOLUME_MULTIPLIER x 20-Tage-Durchschnitt
MOVE_THRESHOLD = 0.05         # Kriterium 5: |Bewegung| >= MOVE_THRESHOLD ggue letztem Schlusskurs

# --- Trendstruktur / RS parameters ---
MIN_HISTORY_DAYS = 200        # Mindesttiefe fuer SMA200 (Trendstruktur-Ampel)
SMA200_TREND_LOOKBACK = 20    # Handelstage, ueber die SMA200-Anstieg geprueft wird
RS_LOOKBACK = 63              # ~3 Monate, fuer RS-Berechnung
RS_TREND_LOOKBACK = 20        # Handelstage, ueber die RS-Trend geprueft wird
MIN_HISTORY_DAYS_RS = RS_LOOKBACK + RS_TREND_LOOKBACK + 1   # Mindesttiefe fuer die (von SMA200 unabhaengige) RS-Ampel
BENCHMARK_SYMBOL = "SPY"

# --- Fundamental-Ampel (Minervini SEPA, quartalsweise) ---
EPS_GROWTH_MIN = 0.20         # EPS-Wachstum Q YoY >= 20%
REVENUE_GROWTH_MIN = 0.15     # Umsatzwachstum Q YoY >= 15%
# Kein ROE-Schwellenwert: die gaengige 17%-Zahl stammt aus O'Neils CANSLIM, nicht aus Minervinis SEPA.

# --- 52-Wochen-Hoch-Ausbruch ---
YEAR_HIGH_LOOKBACK = 252      # Handelstage, heutiger Tag ausgeschlossen

# --- Pocket Pivot (Morales/Kacher) ---
POCKET_PIVOT_DOWNDAY_LOOKBACK = 10   # Handelstage vor heute, in denen nach Down-Tagen gesucht wird
POCKET_PIVOT_MIN_SMA = 50            # Zusatzfilter: Close muss ueber dieser SMA liegen

# --- NR4 / NR7 (Toby Crabel) -- nur Dashboard-Zustand, kein Alarm ---
NR4_LOOKBACK = 4
NR7_LOOKBACK = 7

# --- Aroon-Crossover (Tushar Chande) ---
AROON_PERIOD = 25
AROON_CROSS_THRESHOLD = 50    # Kreuzende Linie muss ueber diesem Wert liegen

# --- TTM Squeeze (John Carter) -- Naeherung fuer VCP, ersetzt die offene VCP-Frage nicht ---
BB_PERIOD = 20
BB_STDDEV = 2.0
KELTNER_EMA_PERIOD = 20
KELTNER_ATR_PERIOD = 20
KELTNER_ATR_MULTIPLIER = 1.5

# --- RVOL-Tier (Volumen-Rating an jedem Einzelereignis-Signal) ---
# Tagesvolumen / 20-Tage-Durchschnittsvolumen. Bei den vier Intraday-Checks ohne
# Time-of-day-Normalisierung (bekannte Einschraenkung, siehe README).
RVOL_DEAD_MAX = 0.5          # < 0.5x        -> "Dead"
RVOL_BELOW_AVG_MAX = 1.0     # 0.5x - 1.0x   -> "Below Avg"
RVOL_ABOVE_AVG_MAX = 2.0     # 1.0x - 2.0x   -> "Above Avg"
RVOL_HIGH_MAX = 3.0          # 2.0x - 3.0x   -> "High", sonst "Extreme"

# --- News ---
ENABLE_NEWS = False           # Auf Wunsch deaktiviert: Telegram soll nur trockene Signal-Alarme
                               # schicken, keine News-Artikel. Auf True setzen, um es wieder
                               # einzuschalten -- der Rest des Codes bleibt unveraendert.
NEWS_LOOKBACK_DAYS = 3         # wie weit pro Lauf zurueckgeschaut wird (Dedup laeuft ueber state.json)

# --- Data provider ---
TWELVEDATA_MIN_SECONDS_BETWEEN_CALLS = 8.0   # 8 Requests/Minute im Gratis-Tarif
TIME_SERIES_OUTPUTSIZE = 260                  # > 200 fuer SMA200 + 20 Tage Puffer

# --- Paths ---
WATCHLIST_CSV = "data/watchlist.csv"
STATE_JSON = "data/state.json"
RESULTS_JSON = "docs/results.json"
