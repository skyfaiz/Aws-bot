# Live Paper Trading Bot — Architecture Plan

**Instrument**: MCX Crude Oil Options (CE / PE)  
**Exchange**: MCX | **Segment**: COMMODITY  
**Data Source**: Groww Trade API (live LTP polling)  
**Execution**: Paper trades only — no real orders are placed  
**Deployment**: AWS EC2 (Linux, runs as a systemd service)  
**Default Strategy**: HalfTrend (modular — easily swappable)

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [High-Level Data Flow](#2-high-level-data-flow)
3. [Project File Structure](#3-project-file-structure)
4. [Module-by-Module Design](#4-module-by-module-design)
   - 4.1 config/config.yaml
   - 4.2 instrument_loader.py
   - 4.3 groww_client.py
   - 4.4 candle_builder.py
   - 4.5 strategies/ (Strategy Layer)
   - 4.6 option_selector.py
   - 4.7 paper_trader.py
   - 4.8 trade_log.py
   - 4.9 bot.py (Orchestrator)
5. [Instrument Filtering Logic](#5-instrument-filtering-logic)
6. [Candle Building from LTP Ticks](#6-candle-building-from-ltp-ticks)
7. [Strategy Interface & Modularity](#7-strategy-interface--modularity)
8. [Option Selection Logic](#8-option-selection-logic)
9. [Paper Trade Execution](#9-paper-trade-execution)
10. [Stop Loss & Take Profit Modes](#10-stop-loss--take-profit-modes)
11. [Trade Log & P&L Tracking](#11-trade-log--pl-tracking)
12. [Market Hours & Session Management](#12-market-hours--session-management)
13. [EC2 Deployment Architecture](#13-ec2-deployment-architecture)
14. [Configuration Parameters Reference](#14-configuration-parameters-reference)
15. [Key Design Decisions & Rationale](#15-key-design-decisions--rationale)
16. [Future Enhancements](#16-future-enhancements)

---

## 1. System Overview

The bot continuously polls live price data from the Groww API for MCX Crude Oil Futures (used as the **underlying signal instrument**), builds OHLCV candles, and runs a configurable trading strategy on each completed candle. When the strategy generates a directional signal:

- **BUY signal** → simulate buying a **CE (Call) option** at the ATM strike
- **SELL signal** → simulate buying a **PE (Put) option** at the ATM strike

The bot monitors the open option position every tick against configurable **stop loss** and **take profit** levels. When an exit condition is met, the paper trade is closed and the result is logged. The bot never places a real order — it only simulates execution using live market prices.

### Key Constraints

| Constraint | Detail |
|---|---|
| Exchange | MCX |
| Underlying | CRUDEOIL (nearest-expiry FUT contract) |
| Trade instrument | CRUDEOIL CE or PE options |
| Lot size | 100 barrels per lot |
| Strike interval | 50 points |
| Execution | Paper only (no real API order calls) |
| Max open positions | 1 at a time (single-leg) |
| Market hours | 09:00 – 23:30 IST (Mon–Fri) |
| Auto square-off | 23:15 IST (configurable) |

---

## 2. High-Level Data Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                     instrument.csv                               │
│  Filter: exchange=MCX, underlying=CRUDEOIL, type∈{FUT,CE,PE}    │
└────────────┬────────────────────────────────────────────────────┘
             │  Builds in-memory instrument catalogue
             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Groww API Client                              │
│  GET /v1/live-data/ltp  (poll every ~5 seconds)                  │
│  Instruments polled: [nearest CRUDEOIL FUT] + [open option]      │
└────────────┬────────────────────────────────────────────────────┘
             │  Raw LTP ticks (timestamp, last_price)
             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Candle Builder                                │
│  Aggregates ticks → OHLCV candles of configurable interval       │
│  Emits a completed candle at each interval boundary              │
└────────────┬────────────────────────────────────────────────────┘
             │  Sliding window of last N completed candles (DataFrame)
             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Strategy Engine                               │
│  strategy.generate_signal(candles) → Signal                      │
│  Default: HalfTrendStrategy (amplitude, deviation, atr_period)   │
│  Pluggable: any class implementing BaseStrategy                  │
└────────────┬────────────────────────────────────────────────────┘
             │  Signal: BUY | SELL | HOLD | EXIT
             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Option Selector                               │
│  signal=BUY  → find ATM CE for nearest expiry                    │
│  signal=SELL → find ATM PE for nearest expiry                    │
│  Returns: trading_symbol, exchange_token, lot_size, strike       │
└────────────┬────────────────────────────────────────────────────┘
             │  Option contract details
             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Paper Trader                                  │
│  Open position: record entry LTP of the option                   │
│  Per-tick: monitor SL / TP against current option LTP            │
│  Close position: record exit LTP, reason, gross/net P&L          │
└────────────┬────────────────────────────────────────────────────┘
             │  Completed trade record
             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Trade Log                                     │
│  Append trade to CSV                                             │
│  Update running metrics: cumulative P&L, win rate, drawdown      │
│  Print session summary to stdout / log file                      │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. Project File Structure

```
tradingbot/
│
├── config/
│   └── config.yaml               # All tunable parameters (single source of truth)
│
├── strategies/
│   ├── __init__.py               # Exports BaseStrategy, Signal, registry
│   ├── base.py                   # Abstract base class + Signal enum
│   └── halftrend.py              # HalfTrend strategy implementation
│
├── core/
│   ├── __init__.py
│   ├── instrument_loader.py      # Load & filter instrument.csv
│   ├── groww_client.py           # Groww REST API wrapper (auth, LTP, retry)
│   ├── candle_builder.py         # Tick → OHLCV aggregator with sliding window
│   ├── option_selector.py        # ATM/OTM strike selection, expiry logic
│   ├── paper_trader.py           # Position management, SL/TP enforcement
│   └── trade_log.py              # CSV logging, running metrics, session summary
│
├── bot.py                        # Main orchestrator — event loop entry point
│
├── halftrend.py                  # (existing) — raw indicator calculation, untouched
├── backtester.py                 # (existing) — offline backtester, untouched
├── app.py                        # (existing) — Streamlit backtester UI, untouched
├── instrument.csv                # (existing) — instrument master data
│
├── logs/
│   └── trades_YYYY-MM-DD.csv     # Daily auto-generated trade log
│
└── deploy/
    ├── tradingbot.service        # systemd unit file for EC2
    └── setup_ec2.sh              # EC2 bootstrap script
```

---

## 4. Module-by-Module Design

---

### 4.1 `config/config.yaml`

Single YAML file that controls every tunable parameter. The bot reads this at startup and validates all fields. No hardcoding anywhere in the source.

```yaml
# ── Groww API ──────────────────────────────────────────────────────
api:
  access_token: "${GROWW_ACCESS_TOKEN}"   # injected from environment variable
  base_url: "https://api.groww.in/v1"
  version: "1.0"
  poll_interval_seconds: 5               # how often to call the LTP API
  max_retries: 3
  retry_backoff_seconds: 2

# ── Instrument selection ────────────────────────────────────────────
instrument:
  exchange: "MCX"
  underlying_symbol: "CRUDEOIL"          # not CRUDEOILM (mini)
  segment: "COMMODITY"
  expiry_selection: "nearest"            # nearest | next | specific
  specific_expiry: null                  # only used if expiry_selection=specific
  option_type_on_buy: "CE"              # what to buy when signal=BUY
  option_type_on_sell: "PE"             # what to buy when signal=SELL
  strike_selection: "ATM"               # ATM | 1_OTM | 2_OTM | 1_ITM
  lots: 1                               # number of lots per trade

# ── Candle settings ─────────────────────────────────────────────────
candles:
  interval_minutes: 5                   # candle interval for strategy
  warmup_candles: 120                   # min candles before first signal
  max_window: 500                       # max candles kept in memory

# ── Strategy ────────────────────────────────────────────────────────
strategy:
  name: "halftrend"                     # must match a key in the strategy registry
  params:
    amplitude: 2
    channel_deviation: 2.0
    atr_period: 100

# ── Risk management ─────────────────────────────────────────────────
risk:
  stop_loss:
    mode: "fixed_pct"                   # none | fixed_inr | fixed_pct | atr
    value: 30.0                         # % of option entry price (for fixed_pct)
                                        # ₹ amount (for fixed_inr)
                                        # ATR multiplier applied to underlying ATR (for atr)
  take_profit:
    mode: "reward_risk"                 # none | fixed_inr | fixed_pct | atr | reward_risk
    value: 2.0                          # reward:risk ratio (for reward_risk)
                                        # other modes: same unit interpretation as SL

# ── Session ─────────────────────────────────────────────────────────
session:
  timezone: "Asia/Kolkata"
  market_open: "09:00"
  market_close: "23:30"
  auto_squareoff_time: "23:15"          # force-close open position before market close
  trade_only_between:                   # optional time window filter
    start: "09:05"
    end: "23:10"

# ── Paper trading capital ────────────────────────────────────────────
paper:
  initial_capital_inr: 100000.0         # starting virtual capital
```

---

### 4.2 `core/instrument_loader.py`

Responsible for loading `instrument.csv` into memory and exposing fast lookup methods for the rest of the bot.

**Responsibilities:**
- Load the full CSV at startup (once)
- Filter and index MCX CRUDEOIL instruments into three catalogues:
  1. **FUT catalogue**: nearest-expiry and next-expiry futures contracts (for underlying LTP)
  2. **CE catalogue**: all call options, indexed by `(expiry_date, strike_price)`
  3. **PE catalogue**: all put options, indexed by `(expiry_date, strike_price)`
- Provide helper methods:

```
InstrumentLoader
├── load(csv_path)                      → loads and indexes CSV
├── get_underlying_fut(expiry="nearest") → returns the FUT instrument row
├── get_option(type, expiry, strike)    → returns CE or PE instrument row
├── list_expiries(type)                 → sorted list of available expiry dates
└── get_groww_symbol(row)               → returns the groww_symbol field (MCX_CRUDEOIL...)
```

**Filtering rules applied during load:**
- `exchange == "MCX"`
- `underlying_symbol == "CRUDEOIL"` (exclude CRUDEOILM / mini contracts)
- `instrument_type in ["FUT", "CE", "PE"]`
- `sell_allowed == 1` and `buy_allowed == 1`
- `expiry_date >= today` (drop expired contracts)

**Nearest expiry logic:**
- Sort valid expiry dates ascending
- Select index 0 as "nearest", index 1 as "next"
- This is recalculated at startup; the bot can reload intraday if the nearest contract expires mid-session

---

### 4.3 `core/groww_client.py`

Thin wrapper around the Groww REST API. Handles authentication, request construction, error handling, and rate limiting.

**Key methods:**

```
GrowwClient(access_token, base_url, version, max_retries, retry_backoff)
│
├── get_ltp(exchange_symbols: list[str], segment: str) → dict[str, float]
│   Calls GET /v1/live-data/ltp
│   Returns: {"MCX_CRUDEOIL19OCT26FUT": 6523.0, ...}
│
├── get_quote(exchange, segment, trading_symbol) → dict
│   Calls GET /v1/live-data/quote
│   Used for full snapshot (market depth, OI, etc.) — optional/diagnostic
│
└── _request(method, path, params) → dict
    Handles: Authorization header, X-API-VERSION header,
             HTTP error codes, retry with exponential backoff,
             logging of failed requests
```

**Important notes:**
- The LTP API supports up to 50 symbols per call. The bot will batch: `[CRUDEOIL_FUT_SYMBOL, OPEN_OPTION_SYMBOL]` in a single call.
- Segment for MCX instruments must be `"COMMODITY"` (confirmed from instrument.csv).
- The `access_token` is loaded from the environment variable `GROWW_ACCESS_TOKEN` — never hardcoded or stored in config files in plaintext.
- All API calls are logged at DEBUG level.

---

### 4.4 `core/candle_builder.py`

Converts a stream of LTP ticks (price, timestamp) into completed OHLCV candles of a configurable interval.

**Design:**

```
CandleBuilder(interval_minutes: int, max_window: int)
│
├── add_tick(price: float, timestamp: datetime) → Candle | None
│   - Accumulates ticks into the current (open) candle
│   - When a tick crosses the interval boundary:
│       → seals the current candle (returns it)
│       → starts a new candle
│   - Returns None while the current candle is still open
│
├── get_candles() → pd.DataFrame
│   Returns the sliding window of completed candles as a DataFrame
│   with columns: Datetime, Open, High, Low, Close, Volume (tick count)
│
└── is_warm() → bool
    True once warmup_candles completed candles have been accumulated
```

**Candle sealing rules:**
- A candle covers `[interval_start, interval_start + interval_minutes)`.
- Boundaries are aligned to the clock (e.g., 5-minute candles close at 09:05, 09:10, 09:15...).
- If no ticks arrive during a candle period (API outage, lunch break), the previous close is carried forward as a flat OHLCV candle. This prevents gaps in the indicator calculation.
- Volume is the number of ticks received during the interval (a proxy for activity, since the LTP API does not provide true trade volume per tick).

**Warmup:**
- The strategy requires `warmup_candles` completed candles before it starts generating signals.
- During warmup, `is_warm()` returns False and the bot skips signal generation.

---

### 4.5 `strategies/` — Strategy Layer

This is the core modularity layer. Every strategy must implement the same interface so the rest of the bot is completely strategy-agnostic.

#### `strategies/base.py`

```python
from abc import ABC, abstractmethod
from enum import Enum
import pandas as pd

class Signal(Enum):
    BUY  = "BUY"    # open a CE position (or add if pyramiding enabled)
    SELL = "SELL"   # open a PE position (or add if pyramiding enabled)
    EXIT = "EXIT"   # close existing position regardless of type
    HOLD = "HOLD"   # do nothing

class BaseStrategy(ABC):
    """
    All strategies must implement this interface.

    Input:  candles — DataFrame with columns: Datetime, Open, High, Low, Close, Volume
                      Rows are in chronological order; last row is the most recently
                      completed candle.

    Output: Signal enum value

    The strategy must be stateless between calls (or manage its own state internally).
    It must NOT look at future candles (no lookahead).
    """

    @abstractmethod
    def generate_signal(self, candles: pd.DataFrame) -> Signal:
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @property
    @abstractmethod
    def params(self) -> dict:
        """Return current parameter values as a dict (for logging)."""
        ...
```

#### `strategies/__init__.py` — Strategy Registry

```python
STRATEGY_REGISTRY = {
    "halftrend": HalfTrendStrategy,
    # Future additions:
    # "rsi":        RSIStrategy,
    # "macd":       MACDStrategy,
    # "supertrend": SuperTrendStrategy,
}

def load_strategy(name: str, params: dict) -> BaseStrategy:
    if name not in STRATEGY_REGISTRY:
        raise ValueError(f"Unknown strategy: {name!r}. Available: {list(STRATEGY_REGISTRY)}")
    return STRATEGY_REGISTRY[name](**params)
```

#### `strategies/halftrend.py`

Wraps the existing `halftrend.py` indicator calculation (which is left completely unchanged).

**Signal generation logic:**

The existing `calculate_halftrend()` function outputs `Buy` and `Sell` boolean columns. The strategy wrapper interprets these for live use:

| Condition on last completed candle | Signal emitted |
|---|---|
| `Buy == True` and no open position | `BUY` |
| `Sell == True` and no open position | `SELL` |
| `Sell == True` and an open CE position exists | `EXIT` then `SELL` |
| `Buy == True` and an open PE position exists | `EXIT` then `BUY` |
| Neither `Buy` nor `Sell` | `HOLD` |

The strategy class receives the candle DataFrame and calls `calculate_halftrend()` internally. It only looks at the **last completed candle's** `Buy`/`Sell` flags. It does not re-examine earlier candles on each tick.

**Parameters exposed in config:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `amplitude` | int | 2 | HalfTrend amplitude (rolling window) |
| `channel_deviation` | float | 2.0 | ATR multiplier for channel bands |
| `atr_period` | int | 100 | EWM alpha period for ATR smoothing |

---

### 4.6 `core/option_selector.py`

Translates a directional signal into a specific option contract to paper-trade.

**Inputs:**
- `signal`: `BUY` or `SELL`
- `underlying_ltp`: current price of the CRUDEOIL FUT
- `expiry_selection`: nearest / next (from config)
- `strike_selection`: ATM / 1_OTM / 2_OTM / 1_ITM (from config)
- The in-memory instrument catalogue (CE / PE tables from `InstrumentLoader`)

**ATM Strike Calculation:**

MCX Crude Oil options have strikes at 50-point intervals (e.g., 6450, 6500, 6550...).

```
atm_strike = round(underlying_ltp / 50) * 50
```

For OTM/ITM offsets, the strike is shifted by N × 50:

| `strike_selection` | CE (BUY signal) | PE (SELL signal) |
|---|---|---|
| `ATM` | atm_strike | atm_strike |
| `1_OTM` | atm_strike + 50 | atm_strike − 50 |
| `2_OTM` | atm_strike + 100 | atm_strike − 100 |
| `1_ITM` | atm_strike − 50 | atm_strike + 50 |

**Fallback handling:** If the selected strike doesn't exist in the catalogue (e.g., deep OTM with no contract), step toward ATM until a valid contract is found.

**Return value:** An `OptionContract` dataclass containing:
```python
@dataclass
class OptionContract:
    trading_symbol: str       # e.g., "CRUDEOIL17NOV2610000CE"
    groww_symbol: str         # e.g., "MCX_CRUDEOIL17NOV2610000CE"
    exchange: str             # "MCX"
    segment: str              # "COMMODITY"
    option_type: str          # "CE" or "PE"
    strike: float
    expiry_date: date
    lot_size: int             # 100
    exchange_token: int
```

---

### 4.7 `core/paper_trader.py`

Manages the lifecycle of a single paper position: open → monitor → close.

**State:**

```python
@dataclass
class OpenPosition:
    contract: OptionContract
    entry_time: datetime
    entry_option_ltp: float       # option price at entry
    entry_underlying_ltp: float   # underlying price at entry (for ATR-based SL/TP)
    entry_atr: float              # underlying ATR at signal candle
    lots: int
    sl_price: float | None        # option price level that triggers SL
    tp_price: float | None        # option price level that triggers TP
    signal: Signal                # BUY or SELL (for record keeping)
```

**Methods:**

```
PaperTrader(config: RiskConfig, lots: int)
│
├── open_position(contract, option_ltp, underlying_ltp, atr) → OpenPosition
│   - Calculates SL and TP based on the configured mode (see Section 10)
│   - Sets the active position
│   - Returns the position object (logged by trade_log)
│
├── check_exit(current_option_ltp, current_underlying_ltp, current_signal) → ExitEvent | None
│   - Called on every tick while a position is open
│   - Returns ExitEvent if any exit condition is triggered, else None
│
├── close_position(exit_event) → CompletedTrade
│   - Clears active position
│   - Returns CompletedTrade for logging
│
├── has_open_position() → bool
│
└── get_unrealized_pnl(current_option_ltp) → float
    Returns current mark-to-market P&L on open position (₹)
```

**Exit priority order (checked in this sequence on every tick):**

1. Stop loss: `current_option_ltp <= sl_price`
2. Take profit: `current_option_ltp >= tp_price`
3. Strategy EXIT signal (opposite candle signal, on candle close only)
4. Auto square-off time reached
5. Market close

**P&L calculation:**
```
lot_size = 100 barrels
gross_pnl = (exit_option_ltp - entry_option_ltp) × lots × lot_size
net_pnl   = gross_pnl  (paper trading — no brokerage modelled, but can be added)
```

---

### 4.8 `core/trade_log.py`

Persistent and in-memory record of all paper trades for the session.

**CSV schema (one row per completed trade):**

| Column | Type | Description |
|---|---|---|
| `trade_id` | str | UUID |
| `entry_time` | datetime | When position was opened |
| `exit_time` | datetime | When position was closed |
| `signal` | str | BUY or SELL |
| `option_type` | str | CE or PE |
| `trading_symbol` | str | Full option trading symbol |
| `expiry_date` | date | Option expiry |
| `strike` | float | Option strike price |
| `entry_underlying_ltp` | float | Underlying (FUT) price at entry |
| `exit_underlying_ltp` | float | Underlying (FUT) price at exit |
| `entry_option_ltp` | float | Option entry price |
| `exit_option_ltp` | float | Option exit price |
| `lots` | int | Number of lots |
| `lot_size` | int | Barrels per lot (100) |
| `sl_price` | float | Stop loss level on option |
| `tp_price` | float | Take profit level on option |
| `exit_reason` | str | stop_loss / take_profit / signal_exit / squareoff / market_close |
| `gross_pnl_inr` | float | (exit_ltp − entry_ltp) × lots × lot_size |
| `net_pnl_inr` | float | Same as gross for paper trading |
| `cumulative_pnl_inr` | float | Running total |
| `session_date` | date | Trading date |
| `strategy_name` | str | Strategy used |
| `strategy_params` | str | JSON-serialized strategy parameters |

**Running metrics (computed in-memory, printed at session end):**

- Total trades
- Winning trades / losing trades
- Win rate %
- Average profit on winners (₹)
- Average loss on losers (₹)
- Profit factor
- Net P&L for session (₹)
- Cumulative P&L all-time (₹)
- Max drawdown (peak-to-trough in ₹)
- Largest single win / largest single loss

---

### 4.9 `bot.py` — Main Orchestrator

The entry point. Wires all components together and runs the event loop.

**Startup sequence:**

```
1. Load config.yaml
2. Validate config (fail fast on missing access_token, bad params)
3. InstrumentLoader.load("instrument.csv")
4. Select nearest CRUDEOIL FUT → underlying_symbol
5. Instantiate GrowwClient
6. Instantiate CandleBuilder(interval_minutes, max_window, warmup_candles)
7. Instantiate Strategy via registry (from config.strategy.name + params)
8. Instantiate OptionSelector
9. Instantiate PaperTrader
10. Instantiate TradeLog (opens or appends today's CSV in logs/)
11. Log startup summary (strategy, instrument, SL/TP config, capital)
12. Enter main event loop
```

**Main event loop (runs every `poll_interval_seconds`):**

```python
while True:
    now = current_time_ist()

    # ── Market hours gate ──────────────────────────────────────────
    if not is_market_open(now):
        sleep(30)
        continue

    if now >= auto_squareoff_time and paper_trader.has_open_position():
        close_position(reason="squareoff")
        break  # or sleep until next session

    # ── Fetch live prices ──────────────────────────────────────────
    symbols_to_poll = [underlying_groww_symbol]
    if paper_trader.has_open_position():
        symbols_to_poll.append(open_position.contract.groww_symbol)

    prices = groww_client.get_ltp(symbols_to_poll, segment="COMMODITY")
    underlying_ltp = prices[underlying_groww_symbol]

    # ── Monitor open position (tick-level SL/TP check) ────────────
    if paper_trader.has_open_position():
        option_ltp = prices.get(open_position.contract.groww_symbol)
        exit_event = paper_trader.check_exit(option_ltp, underlying_ltp, last_signal)
        if exit_event:
            trade = paper_trader.close_position(exit_event)
            trade_log.record(trade)
            log.info(f"[EXIT] {exit_event.reason} | P&L: ₹{trade.net_pnl_inr:.2f}")

    # ── Feed tick to candle builder ────────────────────────────────
    completed_candle = candle_builder.add_tick(underlying_ltp, now)

    # ── Strategy evaluation (only on candle close) ─────────────────
    if completed_candle and candle_builder.is_warm():
        candles = candle_builder.get_candles()
        signal = strategy.generate_signal(candles)
        last_signal = signal
        log.debug(f"[CANDLE CLOSE] Signal: {signal.value}")

        if signal in (Signal.BUY, Signal.SELL) and not paper_trader.has_open_position():
            contract = option_selector.select(signal, underlying_ltp)
            # fetch fresh option LTP for entry price
            opt_price = groww_client.get_ltp([contract.groww_symbol], "COMMODITY")
            entry_ltp = opt_price[contract.groww_symbol]
            current_atr = candles.iloc[-1].get("ATR", 0.0)
            position = paper_trader.open_position(contract, entry_ltp, underlying_ltp, current_atr)
            log.info(f"[ENTRY] {signal.value} {contract.trading_symbol} @ ₹{entry_ltp:.2f}")
            log.info(f"        SL=₹{position.sl_price} | TP=₹{position.tp_price}")

        elif signal == Signal.EXIT and paper_trader.has_open_position():
            exit_event = ExitEvent(reason="signal_exit", option_ltp=option_ltp, underlying_ltp=underlying_ltp)
            trade = paper_trader.close_position(exit_event)
            trade_log.record(trade)

    sleep(poll_interval_seconds)
```

---

## 5. Instrument Filtering Logic

From `instrument.csv`, the relevant columns for filtering are:

| Column | Meaning |
|---|---|
| `exchange` | Must be `MCX` |
| `underlying_symbol` | Must be `CRUDEOIL` (not `CRUDEOILM`) |
| `instrument_type` | `FUT`, `CE`, or `PE` |
| `segment` | `COMMODITY` |
| `expiry_date` | Used for nearest-expiry selection |
| `strike_price` | Used for ATM calculation |
| `lot_size` | 100 for CRUDEOIL; 10 for CRUDEOILM |
| `tick_size` | 100.0 for FUT; 10.0 for options |
| `groww_symbol` | Used directly as `exchange_symbol` in API calls |
| `buy_allowed` | Must be `1` |
| `sell_allowed` | Must be `1` |

**Example instruments selected after filtering:**

| trading_symbol | instrument_type | expiry_date | strike_price |
|---|---|---|---|
| `CRUDEOIL19OCT26FUT` | FUT | 2026-10-19 | 0 |
| `CRUDEOIL19NOV26FUT` | FUT | 2026-11-19 | 0 |
| `CRUDEOIL17NOV2610000CE` | CE | 2026-11-17 | 10000 |
| `CRUDEOIL17NOV2610000PE` | PE | 2026-11-17 | 10000 |
| `CRUDEOIL17NOV2610050CE` | CE | 2026-11-17 | 10050 |

---

## 6. Candle Building from LTP Ticks

Since the Groww API provides point-in-time LTP (not a tick stream), the CandleBuilder works as follows:

```
Poll #1  09:01:05  price=6523  → current candle [09:00–09:05]: O=6523 H=6523 L=6523 C=6523
Poll #2  09:01:10  price=6525  → [09:00–09:05]: O=6523 H=6525 L=6523 C=6525
...
Poll #N  09:04:58  price=6510  → [09:00–09:05]: O=6523 H=6530 L=6505 C=6510
Poll #N+1 09:05:03 price=6512  → SEAL [09:00–09:05] candle: O=6523 H=6530 L=6505 C=6510
                                 START new candle [09:05–09:10]: O=6512...
```

**Why not use the Historical Data API for candles?**  
The Groww Historical Data API provides past candles but has a delay. For live trading decisions on the just-completed candle, LTP polling is more reliable and avoids dependency on a second API endpoint. The tradeoff is that Volume is approximated as tick count, not true traded volume.

**Candle window kept in memory:**  
At minimum, enough candles to satisfy the strategy warmup. For HalfTrend with `atr_period=100`, at least 150 candles are needed. The `max_window` config cap (default 500) prevents unbounded memory growth.

---

## 7. Strategy Interface & Modularity

To add a new strategy:

1. Create `strategies/my_strategy.py` implementing `BaseStrategy`
2. Register it in `strategies/__init__.py`
3. Change `strategy.name` in `config.yaml` to the new key
4. Restart the bot

No other files change. The bot, paper trader, and trade log are all completely strategy-agnostic.

**Example of what a future RSI strategy would look like:**

```python
class RSIStrategy(BaseStrategy):
    def __init__(self, period: int = 14, overbought: float = 70, oversold: float = 30):
        self._period = period
        self._overbought = overbought
        self._oversold = oversold

    @property
    def name(self) -> str:
        return "rsi"

    @property
    def params(self) -> dict:
        return {"period": self._period, "overbought": self._overbought, "oversold": self._oversold}

    def generate_signal(self, candles: pd.DataFrame) -> Signal:
        # compute RSI on candles["Close"], return Signal.BUY / SELL / HOLD
        ...
```

**Strategies that could be added in the future:**

| Strategy | BUY condition | SELL condition |
|---|---|---|
| `halftrend` (default) | HalfTrend Buy signal | HalfTrend Sell signal |
| `rsi` | RSI crosses above oversold level | RSI crosses below overbought level |
| `macd` | MACD line crosses above signal line | MACD line crosses below signal line |
| `supertrend` | Price closes above SuperTrend line | Price closes below SuperTrend line |
| `ema_crossover` | Fast EMA crosses above slow EMA | Fast EMA crosses below slow EMA |
| `bollinger_breakout` | Price closes above upper band | Price closes below lower band |

---

## 8. Option Selection Logic

**Full selection flow:**

```
1. Determine expiry
   ├── expiry_selection = "nearest" → first available expiry > today
   └── expiry_selection = "next"    → second available expiry

2. Determine option type
   ├── signal = BUY  → type = CE
   └── signal = SELL → type = PE

3. Calculate ATM strike
   atm = round(underlying_ltp / 50) * 50

4. Apply strike offset (from config)
   ├── "ATM"   → strike = atm
   ├── "1_OTM" → strike = atm + 50 (CE) or atm - 50 (PE)
   ├── "2_OTM" → strike = atm + 100 (CE) or atm - 100 (PE)
   └── "1_ITM" → strike = atm - 50 (CE) or atm + 50 (PE)

5. Look up contract in catalogue
   key = (expiry_date, strike, type)
   if not found → step 1 strike toward ATM and retry

6. Return OptionContract dataclass
```

**Example:**
- Underlying LTP = 6,523
- ATM strike = round(6523 / 50) × 50 = 6,500
- BUY signal, ATM selection → `CRUDEOIL<expiry>6500CE`

---

## 9. Paper Trade Execution

**Opening a position:**

The bot calls `groww_client.get_ltp([option_groww_symbol])` immediately after a signal is confirmed on candle close. The returned LTP is used as the **entry price**. This simulates a market-price execution. No bid-ask spread modelling in the initial version (can be added later).

**Monitoring while open:**

On every poll tick, the bot fetches the LTP of both the underlying and the open option simultaneously (single API call, two symbols). The option LTP is compared against the SL and TP price levels.

**Paper trade record lifecycle:**

```
Signal detected on candle close
    → fetch option LTP                    [ENTRY]
    → compute SL/TP levels
    → log "OPEN" event

Each tick while position is open:
    → fetch option LTP + underlying LTP
    → compare against SL/TP
    → log unrealized P&L periodically (every N ticks)

Exit condition triggered:
    → fetch option LTP                    [EXIT]
    → compute gross P&L
    → write completed trade to CSV
    → log "CLOSE" event with reason
```

---

## 10. Stop Loss & Take Profit Modes

The SL and TP are always expressed as levels on the **option's LTP**, making them directly comparable to what is monitored each tick. However, the basis for calculating the distance can vary:

### Mode 1: `none`
No SL or TP. Position is held until the strategy generates an opposite signal or the auto-squareoff time is reached.

### Mode 2: `fixed_inr`
A fixed rupee amount from the option entry price.
```
sl_price = entry_option_ltp - sl_value_inr
tp_price = entry_option_ltp + tp_value_inr
```

### Mode 3: `fixed_pct`
A percentage of the option entry price.
```
sl_price = entry_option_ltp × (1 - sl_pct / 100)
tp_price = entry_option_ltp × (1 + tp_pct / 100)
```
Example: entry=₹200, SL=30% → sl_price=₹140; TP=60% → tp_price=₹320

### Mode 4: `atr`
Uses the ATR of the **underlying** (computed by the strategy on the signal candle) to set a distance on the option price. The delta of the option is used to translate underlying movement to option price movement.

```
underlying_sl_distance = sl_value × entry_atr
option_sl_distance     = underlying_sl_distance × option_delta   # estimated
sl_price = entry_option_ltp - option_sl_distance
```

Note: in the initial version, a fixed estimated delta (e.g., 0.5 for ATM) is used. A more accurate approach (future enhancement) would call the Get Greeks API.

### Mode 5: `reward_risk` (TP only)
Requires a non-`none` SL mode to define the risk distance first, then sets TP as a multiple of that distance.
```
risk_distance = entry_option_ltp - sl_price
tp_price      = entry_option_ltp + (tp_value × risk_distance)
```
Example: entry=₹200, SL=₹140 (risk=₹60), R:R=2 → TP=₹200 + 2×60 = ₹320

### Summary table

| SL Mode | SL Basis | TP Mode | TP Basis |
|---|---|---|---|
| `none` | — | `none` | — |
| `fixed_inr` | ₹ amount | `fixed_inr` | ₹ amount |
| `fixed_pct` | % of entry | `fixed_pct` | % of entry |
| `atr` | Underlying ATR × multiplier | `atr` | Same |
| — | — | `reward_risk` | N × SL distance |

---

## 11. Trade Log & P&L Tracking

**File naming:** `logs/trades_YYYY-MM-DD.csv` — one file per calendar day, auto-created.

**Append behavior:** If the bot restarts during the same day, it appends to the existing file. Cumulative P&L is recalculated by summing all `net_pnl_inr` rows in the file.

**Console / log output pattern:**

```
[09:15:00] CANDLE CLOSE | Signal: BUY
[09:15:01] ENTRY  | CRUDEOIL17NOV266500CE @ ₹185.00 | SL=₹129.50 | TP=₹296.00
[09:25:00] TICK   | Option LTP=₹202.00 | Unrealized P&L=+₹1,700.00
[10:10:00] TICK   | Option LTP=₹130.00 — STOP LOSS TRIGGERED
[10:10:01] EXIT   | @ ₹130.00 | Reason: stop_loss | Net P&L=-₹5,500.00
[10:10:01] SESSION| Trades=1 | Win=0 | Loss=1 | Net P&L=-₹5,500.00
```

**Session summary printed on shutdown:**
```
═══════════════════════════════════════════════
  PAPER TRADING SESSION SUMMARY
  Date          : 2026-10-13
  Strategy      : halftrend (amp=2, dev=2.0, atr=100)
  Instrument    : MCX CRUDEOIL CE/PE (ATM, nearest expiry)
───────────────────────────────────────────────
  Total trades      : 5
  Winners           : 3  (60.0%)
  Losers            : 2  (40.0%)
  Avg win           : ₹8,200.00
  Avg loss          : ₹4,100.00
  Profit factor     : 2.00
  Net P&L (session) : ₹16,400.00
  Cumulative P&L    : ₹16,400.00
  Max drawdown      : ₹5,500.00
  Initial capital   : ₹1,00,000.00
  Final equity      : ₹1,16,400.00
═══════════════════════════════════════════════
```

---

## 12. Market Hours & Session Management

MCX Crude Oil trading hours (IST):

| Day | Open | Close |
|---|---|---|
| Monday – Friday | 09:00 | 23:30 |
| Saturday | Closed | — |
| Sunday | Closed | — |

**Bot behavior timeline each day:**

```
08:55  Bot starts (or was already running from previous day)
09:00  Market open — polling begins, candle building starts
09:05  (or after warmup_candles) First signal possible
...    Normal operation: poll → candle → signal → trade
23:15  Auto-squareoff time: if any open position, force-close it immediately
23:30  Market close: polling stops, session summary printed
23:30  Bot idles in sleep loop, waiting for 09:00 next day
```

**Handling weekends and holidays:**
- The bot checks `is_market_open()` at the top of every loop iteration.
- If the current time is outside market hours, it sleeps for 30 seconds before rechecking.
- The bot does not maintain a holiday calendar in v1; it relies purely on market hours. A future enhancement is to integrate an MCX holiday list.

---

## 13. EC2 Deployment Architecture

### EC2 Instance Recommendation

| Parameter | Recommendation |
|---|---|
| Instance type | `t3.micro` or `t3.small` |
| OS | Ubuntu 22.04 LTS |
| Region | `ap-south-1` (Mumbai) — lowest latency to Groww API |
| Storage | 8 GB gp3 SSD |

### Running as a systemd Service

The bot runs as a persistent background process managed by systemd. If it crashes, systemd automatically restarts it.

**`deploy/tradingbot.service`:**
```ini
[Unit]
Description=MCX Crude Oil Paper Trading Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/tradingbot
ExecStart=/home/ubuntu/tradingbot/venv/bin/python bot.py
Restart=on-failure
RestartSec=10
StandardOutput=append:/home/ubuntu/tradingbot/logs/bot.log
StandardError=append:/home/ubuntu/tradingbot/logs/bot_error.log
EnvironmentFile=/home/ubuntu/tradingbot/.env

[Install]
WantedBy=multi-user.target
```

**`.env` file (on EC2, never committed to git):**
```
GROWW_ACCESS_TOKEN=your_real_token_here
```

### Deployment Steps (`deploy/setup_ec2.sh` outline)

```bash
# 1. Install Python 3.11+
sudo apt update && sudo apt install -y python3.11 python3.11-venv

# 2. Clone the repo
git clone <repo_url> /home/ubuntu/tradingbot
cd /home/ubuntu/tradingbot

# 3. Create virtual environment
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 4. Set up environment
cp .env.example .env
nano .env  # fill in GROWW_ACCESS_TOKEN

# 5. Create logs directory
mkdir -p logs

# 6. Install and enable systemd service
sudo cp deploy/tradingbot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable tradingbot
sudo systemctl start tradingbot

# 7. Check status
sudo systemctl status tradingbot
tail -f logs/bot.log
```

### Log Rotation

Configure `logrotate` to rotate `logs/bot.log` and `logs/trades_*.csv` daily:
- Retain 30 days of bot logs
- Retain trade CSVs indefinitely (they are small and contain the full trading history)

### Monitoring

- **Health check**: A lightweight optional Flask endpoint (`/health`) that returns the current session state (open position, last tick time, session P&L) — accessible from a local SSH tunnel.
- **Alert on stall**: If no LTP tick is received for more than 60 seconds during market hours, the bot logs a WARNING. This catches API auth expiry or network issues.
- **Groww access token expiry**: The bot should catch HTTP 401 responses and log a CRITICAL alert. The user must renew the token manually (token management is out of scope for v1).

---

## 14. Configuration Parameters Reference

| Config Key | Type | Default | Effect |
|---|---|---|---|
| `api.poll_interval_seconds` | int | 5 | How often LTP is fetched |
| `api.max_retries` | int | 3 | API call retry limit |
| `instrument.underlying_symbol` | str | CRUDEOIL | Switch to CRUDEOILM for mini contracts |
| `instrument.expiry_selection` | str | nearest | nearest / next / specific |
| `instrument.strike_selection` | str | ATM | ATM / 1_OTM / 2_OTM / 1_ITM |
| `instrument.lots` | int | 1 | Lots per trade (position size) |
| `candles.interval_minutes` | int | 5 | Candle interval for strategy |
| `candles.warmup_candles` | int | 120 | Candles required before first signal |
| `strategy.name` | str | halftrend | Key in strategy registry |
| `strategy.params.amplitude` | int | 2 | HalfTrend amplitude |
| `strategy.params.channel_deviation` | float | 2.0 | HalfTrend ATR multiplier |
| `strategy.params.atr_period` | int | 100 | HalfTrend ATR smoothing period |
| `risk.stop_loss.mode` | str | fixed_pct | none / fixed_inr / fixed_pct / atr |
| `risk.stop_loss.value` | float | 30.0 | SL value (unit depends on mode) |
| `risk.take_profit.mode` | str | reward_risk | none / fixed_inr / fixed_pct / atr / reward_risk |
| `risk.take_profit.value` | float | 2.0 | TP value (unit depends on mode) |
| `session.auto_squareoff_time` | str | 23:15 | Force-close all positions |
| `paper.initial_capital_inr` | float | 100000.0 | Starting virtual capital |

---

## 15. Key Design Decisions & Rationale

### Why poll LTP instead of using a WebSocket stream?
The Groww Trade API documentation (as scraped) only exposes REST endpoints for live data. There is no WebSocket API documented. Polling every 5 seconds gives sub-10-second latency on signal detection, which is adequate for 5-minute candle trading.

### Why run strategy only on candle close?
Running the strategy on every tick would produce excessive signals and lookahead artifacts. The HalfTrend (and most other indicators) are designed to be evaluated once per completed candle. This also matches how the existing backtester (`backtester.py`) works — each row in the backtest corresponds to a completed candle.

### Why use the FUT as the underlying instead of a spot price?
MCX Crude Oil does not have a spot price traded on an exchange in India. The nearest-expiry futures contract (`CRUDEOIL19OCT26FUT`) is the liquid underlying reference, exactly as used in the existing `wti_crude_5m_7d.csv` historical data. The HalfTrend strategy was developed and tested against futures prices.

### Why only one open position at a time?
Keeps position management simple and unambiguous. A new signal while a position is already open is ignored (or optionally triggers an exit of the existing position). Pyramiding can be added later as an explicit config option.

### Why keep `halftrend.py`, `backtester.py`, and `app.py` completely untouched?
Separation of concerns. The backtest system (offline, historical) should remain stable and independently runnable. The live bot is a parallel system that **reuses** `halftrend.py` but does not modify it. This means the same indicator code is used in both backtest and live modes — no divergence risk.

### Why config.yaml instead of command-line arguments?
A YAML file is easier to version-control, review, and audit. All parameter changes have a written record. On EC2, the same config file is checked into git (without secrets) and the access token is injected via environment variable, keeping credentials out of version control.

---

## 16. Future Enhancements

| Enhancement | Description | Priority |
|---|---|---|
| Greeks-based SL/TP | Call `Get Greeks` API to get real delta, use it to translate underlying SL distance to option SL distance | Medium |
| Multiple simultaneous positions | Allow running CE + PE hedged positions, or multiple underlyings | Low |
| Holiday calendar | Integrate MCX holiday list to avoid polling on exchange holidays | Medium |
| Telegram / email alerts | Send alerts on trade open/close and session summary | Medium |
| Token auto-refresh | Detect 401 responses and auto-refresh the Groww access token | High |
| Web dashboard | Lightweight Flask+HTMX page showing live position, tick, equity curve | Low |
| Backtester integration | After a live session, automatically replay the session's candles through the backtester to compare live vs. backtest results | Medium |
| Lot sizing by capital % | Auto-compute lots based on risk % of capital instead of fixed lot count | Medium |
| Slippage model | Model bid-ask spread using market depth from `Get Quote` API | Low |
| Multi-strategy ensemble | Run multiple strategies and only trade when all agree (consensus) | Low |

---

*Document generated: 2026-10-13*  
*Based on: Groww Trade API Live Data docs, instrument.csv (MCX CRUDEOIL instruments), existing halftrend.py / backtester.py / app.py codebase.*
