# Groww Trade API — Live Data (cURL)

Source: https://groww.in/trade-api/docs/curl/live-data

Fetch live data of instruments easily using APIs.

**Contents**

- [Get Quote](#get-quote)
- [Get LTP](#get-ltp)
- [Get OHLC](#get-ohlc)
- [Get Option Chain](#get-option-chain)
- [Get Greeks](#get-greeks)

---

## Get Quote

`GET https://api.groww.in/v1/live-data/quote`

This API provides the complete live data snapshot for an instrument including the latest price, market depth, ohlc, market volumes and much more. If one requires only the latest price data then the **Get LTP** api should be used. Similarly if one is interested in getting only ohlc then **Get OHLC** api should be used. Use the segment value `FNO` for derivatives, `CASH` for stocks and index contracts.

### Request

```bash
# You can also use wget
curl -X GET https://api.groww.in/v1/live-data/quote?exchange=NSE&segment=CASH&trading_symbol=NIFTY \
  -H 'Accept: application/json' \
  -H 'Authorization: Bearer {ACCESS_TOKEN}' \
  -H 'X-API-VERSION: 1.0'
```

### Request schema

| Name | Type | Description |
|---|---|---|
| exchange `*` | string | Stock exchange (see Annexures: Exchange) |
| segment `*` | string | Segment of the instrument such as CASH and FNO etc. (see Annexures: Segment) |
| trading_symbol `*` | string | Trading Symbol of the instrument as defined by the exchange |

`*` required parameters

### Response

```json
{
  "status": "SUCCESS",
  "payload": {
    "average_price": 150.25,
    "bid_quantity": 1000,
    "bid_price": 150,
    "day_change": -0.5,
    "day_change_perc": -0.33,
    "upper_circuit_limit": 151,
    "lower_circuit_limit": 148.5,
    "ohlc": "{open: 149.50,high: 150.50,low: 148.50,close: 149.50}",
    "depth": {
      "buy": [
        {
          "price": 100.5,
          "quantity": 1000
        }
      ],
      "sell": [
        {
          "price": 100.5,
          "quantity": 1000
        }
      ]
    },
    "high_trade_range": 150.5,
    "implied_volatility": 0.25,
    "last_trade_quantity": 500,
    "last_trade_time": 1633072800000,
    "low_trade_range": 148.25,
    "last_price": 149.5,
    "market_cap": 5000000000,
    "offer_price": 150.5,
    "offer_quantity": 2000,
    "oi_day_change": 100,
    "oi_day_change_percentage": 0.5,
    "open_interest": 2000,
    "previous_open_interest": 1900,
    "total_buy_quantity": 5000,
    "total_sell_quantity": 4000,
    "volume": 10000,
    "week_52_high": 160,
    "week_52_low": 140
  }
}
```

### Response Schema

| Name | Type | Description |
|---|---|---|
| status | string | SUCCESS if request is processed successfully, FAILURE if the request failed |
| average_price | decimal | Average price of the instrument in Rupees |
| bid_quantity | integer | Quantity of the bid |
| bid_price | decimal | Price of the bid |
| day_change | decimal | Day change in price |
| day_change_perc | decimal | Day change percentage |
| upper_circuit_limit | decimal | High price range |
| lower_circuit_limit | decimal | Low price range |
| open | decimal | Opening price |
| high | decimal | Highest price |
| low | decimal | Lowest price |
| close | decimal | Closing price |
| price | decimal | Price of the book entry |
| quantity | integer | Quantity of the book entry |
| high_trade_range | decimal | High trade range |
| implied_volatility | decimal | Implied volatility |
| last_trade_quantity | integer | Last trade quantity |
| last_trade_time | integer | Last trade time in epoch milliseconds |
| low_trade_range | decimal | Low trade range |
| last_price | decimal | Last traded price |
| market_cap | decimal | Market capitalization |
| offer_price | decimal | Offer price |
| offer_quantity | integer | Quantity of the offer |
| oi_day_change | decimal | Open interest day change |
| oi_day_change_percentage | decimal | Open interest day change percentage |
| open_interest | decimal | Open interest |
| previous_open_interest | decimal | Previous open interest |
| total_buy_quantity | decimal | Total buy quantity |
| total_sell_quantity | decimal | Total sell quantity |
| volume | integer | Volume of trades |
| week_52_high | decimal | 52-week high price |
| week_52_low | decimal | 52-week low price |

`*` All prices in rupees.

---

## Get LTP

`GET https://api.groww.in/v1/live-data/ltp`

The API can be used to get the latest price of an instrument. Use the segment value `FNO` for derivatives, `CASH` for stocks and indices. Upto 50 instruments are supported for each api call.

### Request

```bash
# You can also use wget
curl -X GET https://api.groww.in/v1/live-data/ltp?segment=CASH&exchange_symbols=NSE_RELIANCE,BSE_SENSEX \
  -H 'Accept: application/json' \
  -H 'Authorization: Bearer {ACCESS_TOKEN}' \
  -H 'X-API-VERSION: 1.0'
```

### Request schema

| Name | Type | Description |
|---|---|---|
| segment `*` | string | Segment of the instrument such as CASH and FNO etc. (see Annexures: Segment) |
| exchange_symbols `*` | array[string] | String of trading symbols with their respective exchanges. For example `NSE_RELIANCE` `BSE_SENSEX` `NSE_NIFTY25APR24100PE` `MCX_CRUDEOIL25JAN25FUT` |

`*` required parameters

### Response

```json
{
  "status": "SUCCESS",
  "payload": {
    "NSE_RELIANCE": 2334.2,
    "BSE_SENSEX": 73243.4
  }
}
```

### Response Schema

| Name | Type | Description |
|---|---|---|
| status | string | SUCCESS if request is processed successfully, FAILURE if the request failed |
| ltp | decimal | Last traded price |

`*` All prices in rupees.

---

## Get OHLC

`GET https://api.groww.in/v1/live-data/ohlc`

The API can be used to get the ohlc data of an instrument. Use the segment value `FNO` for derivatives, `CASH` for stocks and indices. Upto 50 instruments are supported for each API call.

> **Note:** The OHLC data retrieved using the OHLC API reflects the current time's OHLC (i.e., real-time snapshot). For interval-based OHLC data (e.g., 1-minute, 5-minute candles), please refer to the **Historical Data** API.

### Request

```bash
# You can also use wget
curl -X GET https://api.groww.in/v1/live-data/ohlc?segment=CASH&exchange_symbols=NSE_RELIANCE,BSE_SENSEX \
  -H 'Accept: application/json' \
  -H 'Authorization: Bearer {ACCESS_TOKEN}' \
  -H 'X-API-VERSION: 1.0'
```

### Request schema

| Name | Type | Description |
|---|---|---|
| segment `*` | string | Segment of the instrument such as CASH, FNO etc. (see Annexures: Segment) |
| exchange_symbols `*` | array[string] | String of trading symbols with their respective exchanges. For example `NSE_RELIANCE` `BSE_SENSEX` `NSE_NIFTY25APR24100PE` `MCX_CRUDEOIL25JANFUT` |

`*` required parameters

### Response

```json
{
  "status": "SUCCESS",
  "payload": {
    "NSE_RELIANCE": "{open: 149.50,high: 150.50,low: 148.50,close: 149.50}",
    "BSE_SENSEX": "{open: 149.50,high: 150.50,low: 148.50,close: 149.50}"
  }
}
```

### Response Schema

| Name | Type | Description |
|---|---|---|
| status | string | SUCCESS if request is processed successfully, FAILURE if the request failed |
| open | decimal | Opening price |
| high | decimal | Highest price |
| low | decimal | Lowest price |
| close | decimal | Closing price |

`*` All prices in rupees.

---

## Get Option Chain

`GET https://api.groww.in/v1/option-chain/exchange/{exchange}/underlying/{underlying}?expiry_date={expiry_date}`

This API provides the complete option chain data for FNO (Futures and Options) contracts including Greeks. Option chains are lists of available contracts for a specific underlying symbol and expiry date. This API is specifically designed for derivatives trading.

### Request

```bash
# You can also use wget
curl -X GET https://api.groww.in/v1/option-chain/exchange/{exchange}/underlying/{underlying}?expiry_date={expiry_date} \
  -H 'Accept: application/json' \
  -H 'Authorization: Bearer {ACCESS_TOKEN}' \
  -H 'X-API-VERSION: 1.0'
```

### Request schema

| Name | Type | Description |
|---|---|---|
| exchange `*` | string | Stock exchange - NSE or BSE (see Annexures: Exchange) |
| underlying `*` | string | Underlying symbol for the contract such as NIFTY, BANKNIFTY, RELIANCE etc. |
| expiry_date `*` | string | Expiry date of the contract in YYYY-MM-DD format. |

`*` required parameters

### Response

```json
{
    "status": "SUCCESS",
    "payload": {
        "underlying_ltp": 25641.7,
        "strikes": {
            "23400": {
                "CE": {
                    "greeks": {
                        "delta": 0.9936,
                        "gamma": 0,
                        "theta": -1.0787,
                        "vega": 0.6943,
                        "rho": 5.1802,
                        "iv": 25.3409
                    },
                    "trading_symbol": "NIFTY25N1823400CE",
                    "ltp": 2200,
                    "open_interest": 7,
                    "volume": 5
                },
                "PE": {
                    "greeks": {
                        "delta": -0.0064,
                        "gamma": 0,
                        "theta": -1.0787,
                        "vega": 0.6943,
                        "rho": -0.0373,
                        "iv": 25.3409
                    },
                    "trading_symbol": "NIFTY25N1823400PE",
                    "ltp": 2.05,
                    "open_interest": 7453,
                    "volume": 9339
                }
            },
            "23450": {
                "CE": {
                    "greeks": {
                        "delta": 0.9927,
                        "gamma": 0,
                        "theta": -1.2027,
                        "vega": 0.7774,
                        "rho": 5.1862,
                        "iv": 25.2306
                    },
                    "trading_symbol": "NIFTY25N1823450CE",
                    "ltp": 2082.9,
                    "open_interest": 4,
                    "volume": 0
                },
                "PE": {
                    "greeks": {
                        "delta": -0.0073,
                        "gamma": 0,
                        "theta": -1.2027,
                        "vega": 0.7774,
                        "rho": -0.0424,
                        "iv": 25.2306
                    },
                    "trading_symbol": "NIFTY25N1823450PE",
                    "ltp": 2.35,
                    "open_interest": 378,
                    "volume": 74
                }
            }
        }
    }
}
```

### Response Schema

| Name | Type | Description |
|---|---|---|
| status | string | SUCCESS if request is processed successfully, FAILURE if the request failed |
| underlying_ltp | decimal | Last Traded Price of the underlying |
| strikes | object | Strike-wise option chain data containing CE and PE contracts |
| trading_symbol | string | Trading symbol of the option contract |
| ltp | float | Last traded price of the option contract |
| open_interest | int | Open interest for the contract |
| volume | int | Total volume traded for the contract |
| delta | float | Delta measures the rate of change of option price based on every 1 rupee change in the price of underlying |
| gamma | float | Gamma measures the rate of change of delta with respect to underlying asset price |
| theta | float | Theta measures the rate of time decay of option price |
| vega | float | Vega measures the rate of change of option price based on every 1% change in implied volatility |
| rho | float | Rho measures the sensitivity of option price to changes in interest rates |
| iv | float | Implied Volatility represents the market's expectation of future volatility, expressed as a percentage |

---

## Get Greeks

`GET https://api.groww.in/v1/live-data/greeks/exchange/{exchange}/underlying/{underlying}/trading_symbol/{trading_symbol}/expiry/{expiry}`

This API provides the complete Greeks data for FNO (Futures and Options) contracts. Greeks are financial measures that help assess the risk and sensitivity of options contracts to various factors like underlying price changes, time decay, volatility, and interest rates. This API is specifically designed for derivatives trading and risk management.

### Request

```bash
# You can also use wget
curl -X GET https://api.groww.in/v1/live-data/greeks/exchange/NSE/underlying/NIFTY/trading_symbol/NIFTY25O1425100CE/expiry/2025-10-14 \
  -H 'Accept: application/json' \
  -H 'Authorization: Bearer {ACCESS_TOKEN}' \
  -H 'X-API-VERSION: 1.0'
```

### Request schema

| Name | Type | Description |
|---|---|---|
| exchange `*` | string | Stock exchange - NSE or BSE (see Annexures: Exchange) |
| underlying `*` | string | Underlying symbol for the contract such as NIFTY, BANKNIFTY, RELIANCE etc. |
| trading_symbol `*` | string | Trading Symbol of the FNO contract as defined by the exchange |
| expiry `*` | string | Expiry date of the contract in YYYY-MM-DD format |

`*` required parameters

### Response

```json
{
  "status": "SUCCESS",
  "payload": {
    "greeks": {
      "delta": 0.6006,
      "gamma": 0.0014,
      "theta": -8.1073,
      "vega": 13.1433,
      "rho": 2.7333,
      "iv": 8.2383
    }
  }
}
```

### Response Schema

| Name | Type | Description |
|---|---|---|
| status | string | SUCCESS if request is processed successfully, FAILURE if the request failed |
| delta | float | Delta measures the rate of change of option price based on every 1 rupee change in the price of underlying. |
| gamma | float | Gamma measures the rate of change of delta with respect to underlying asset price. Higher gamma means delta changes more rapidly |
| theta | float | Theta measures the rate of time decay of option price. Usually negative, indicating option value decreases over time |
| vega | float | Vega measures the rate of change of option price based on every 1% change in implied volatility of the underlying asset |
| rho | float | Rho measures the sensitivity of option price to changes in interest rates |
| iv | float | Implied Volatility represents the market's expectation of future volatility, expressed as a percentage |

---

*Navigation: Previous — Margin | Next — Historical Data*
