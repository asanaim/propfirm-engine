# Spread benchmarks (collected 29 October 2026)

The engine's normal spread per symbol (`trading.spread.min_points` in
`config/policy.toml`) follows what traders pay at two prop firms (FTMO,
FundedNext) and two large brokers (Exness, OANDA). Every figure below was
read from the house's own website or its public data API on 29 October 2026.

## Method

1. **All-in cost.** The engine charges no commission, so its spread carries
   the whole trading cost. For each house:
   all-in = spread + round-trip commission, converted to the symbol's price.
   A forex commission in USD per 100,000-unit lot converts at the pair's pip
   value in USD (rates below); a percentage commission converts at the
   house's own price at the time.
2. **Median across houses.** The normal spread is the median of the all-in
   costs of the houses that quote the symbol (with four houses, the mean of
   the middle two).
3. **Rounding (explicit).** `min_points = ceil(median / point)`. The engine
   then quotes the next even number of points (bid and ask symmetric around
   the feed price), so the quoted normal spread can be 1 point above
   `min_points`.

Conversion rates (FTMO mid prices, 19:45 UTC): EUR/USD 1.116675, GBP/USD
1.337025, USD/JPY 157.911, USD/CHF 0.82588, AUD/USD 0.703445, USD/CAD
1.3983, NZD/USD 0.5632.

## What was measured

| House | Account | Figure | When | Commission used (round trip) |
|---|---|---|---|---|
| FTMO | CFD challenge | Raw spread from the live ticker on ftmo.com/en/symbols: the mean of two windows, one read at about 16:40 UTC and the median of 24 reads 19:45–19:47 UTC | 29 Oct 2026 | Forex $5 per lot; metals 0.0014 %; oil 0; crypto 0.065 % [1][2] |
| FundedNext | Stellar 1-Step / 2-Step | Raw spread from the live table on fundednext.com/symbols: the mean of two windows, one read at about 16:40 UTC and the median of 8 (forex) or 3 (others) reads 19:48–19:50 UTC | 29 Oct 2026 | Forex and oil $5 per lot (oil lot 100 barrels); metals 0.0016 %; crypto 0.04 %; charged once, at the opening [3][4][5] |
| Exness | Standard (commission 0) | `median_spread` from the public instruments API, which the site labels the previous trading day's figure; unit 10 × the instrument's point | trading day before 29 Oct 2026 | none [6] |
| OANDA | UK (OEL), spread only | The spread quoted at or below 50 % of the time, last month, from OANDA's historical-spreads data | October 2026 | none [7] |

## Per symbol

Forex in pips; other symbols in USD. "–" = the house does not offer the
symbol, or offers it on no account compared here.

| Symbol | FTMO | FundedNext | Exness | OANDA | Median | `min_points` | Quoted |
|---|---|---|---|---|---|---|---|
| EUR/USD | 0.60 | 0.90 | 0.80 | 0.80 | 0.80 | 8 | 0.8 pip |
| GBP/USD | 1.40 | 0.60 | 1.00 | 1.20 | 1.10 | 11 | 1.2 pip |
| USD/JPY | 1.09 | 1.51 | 1.00 | 1.60 | 1.30 | 14 | 1.4 pip |
| USD/CHF | 0.96 | 1.76 | 1.30 | 1.50 | 1.40 | 14 | 1.4 pip |
| AUD/USD | 0.80 | 1.10 | 0.90 | 1.30 | 1.00 | 10 | 1.0 pip |
| USD/CAD | 1.20 | 1.35 | 1.40 | 1.80 | 1.37 | 14 | 1.4 pip |
| NZD/USD | 0.93 | 1.25 | 1.40 | 1.50 | 1.32 | 14 | 1.4 pip |
| EUR/GBP | 0.92 | 1.52 | 1.30 | 1.10 | 1.20 | 12 | 1.2 pip |
| EUR/JPY | – | 1.94 | 1.60 | 2.70 | 1.94 | 20 | 2.0 pip |
| EUR/CHF | – | 1.69 | 1.60 | 2.00 | 1.69 | 17 | 1.8 pip |
| EUR/AUD | – | 2.06 | 1.70 | 3.50 | 2.06 | 21 | 2.2 pip |
| EUR/CAD | – | 2.55 | 1.50 | 2.70 | 2.55 | 26 | 2.6 pip |
| EUR/NZD | – | 3.41 | 2.20 | 6.60 | 3.41 | 35 | 3.6 pip |
| GBP/JPY | 3.34 | 2.51 | 2.00 | 3.80 | 2.93 | 30 | 3.0 pip |
| GBP/CHF | – | 1.96 | 1.50 | 2.50 | 1.96 | 20 | 2.0 pip |
| GBP/AUD | – | 2.29 | 2.10 | 5.00 | 2.29 | 23 | 2.4 pip |
| GBP/CAD | – | 2.52 | 1.80 | 3.70 | 2.52 | 26 | 2.6 pip |
| AUD/JPY | 1.74 | 1.79 | 1.10 | 2.30 | 1.76 | 18 | 1.8 pip |
| AUD/CAD | – | 2.25 | 1.80 | 2.40 | 2.25 | 23 | 2.4 pip |
| CAD/JPY | – | 1.94 | 1.10 | 2.50 | 1.94 | 20 | 2.0 pip |
| CHF/JPY | – | 2.29 | 2.10 | 3.90 | 2.29 | 23 | 2.4 pip |
| XAU/USD | 0.406 | 0.544 | 0.240 | 0.529 | 0.468 | 47 | $0.48 |
| XAG/USD | 0.0362 | 0.0638 | 0.0250 | 0.0290 | 0.0326 | 33 | $0.034 |
| WTI | 0.0735 | 0.118 | 0.014 | 0.036 | 0.0548 | 6 | $0.06 |
| Brent | 0.0655 | 0.1195 | 0.017 | 0.040 | 0.0528 | 6 | $0.06 |
| BTC/USD | 53.55 | 53.13 | 6.40 | – | 53.13 | 5313 | $53.14 |
| ETH/USD | 2.117 | 1.485 | 1.000 | – | 1.485 | 149 | $1.50 |
| XRP/USD | 0.00233 | 0.00152 | – | – | 0.00193 | 20 | $0.0020 |
| LTC/USD | 0.188 | 1.233 | – | – | 0.711 | 72 | $0.72 |
| SOL/USD | – | – | – | – | – | 10 [E] | $0.10 |

FTMO's ticker covers ten forex pairs; the crosses it does not show have
three houses. SOL/USD has no benchmark (FTMO and FundedNext do not list
it, Exness does not offer it on the Standard account, OANDA UK offers no
crypto); 10 points is an estimate.

## Raw figures

Raw spreads, in the house's price (window 1 / window 2 median):

| Symbol | FTMO | FundedNext |
|---|---|---|
| EUR/USD | 0.00001 / 0.00001 | 0.00000 / 0.00008 |
| GBP/USD | 0.00009 / 0.00009 | 0.00000 / 0.00002 |
| USD/JPY | 0.003 / 0.003 | 0.006 / 0.008505 |
| USD/CHF | 0.00005 / 0.00006 | 0.00010 / 0.00017 |
| AUD/USD | 0.00003 / 0.00003 | 0.00003 / 0.00009 |
| USD/CAD | 0.00004 / 0.00006 | 0.00004 / 0.00009 |
| NZD/USD | 0.00004 / 0.000045 | 0.00005 / 0.0001 |
| EUR/GBP | 0.00005 / 0.00006 | 0.00010 / 0.00013 |
| EUR/JPY | – | 0.010 / 0.01301 |
| EUR/CHF | – | 0.00010 / 0.000155 |
| EUR/AUD | – | 0.00012 / 0.00015 |
| EUR/CAD | – | 0.00016 / 0.00021 |
| EUR/NZD | – | 0.00024 / 0.000265 |
| GBP/JPY | 0.025 / 0.026 | 0.014 / 0.020505 |
| GBP/CHF | – | 0.00013 / 0.00018 |
| GBP/AUD | – | 0.00013 / 0.000185 |
| GBP/CAD | – | 0.00015 / 0.000215 |
| AUD/JPY | 0.009 / 0.010 | 0.009 / 0.01101 |
| AUD/CAD | – | 0.00014 / 0.000170 |
| CAD/JPY | – | 0.010 / 0.01301 |
| CHF/JPY | – | 0.015 / 0.015005 |
| XAU/USD | 0.35 / 0.35 | 0.36 / 0.60 |
| XAG/USD | 0.036 / 0.035 | 0.055 / 0.071 |
| WTI | 0.079 / 0.068 | 0.067 / 0.06901 |
| Brent | 0.071 / 0.060 | 0.069 / 0.07001 |
| BTC/USD | 1.00 / 1.00 | 20.65 / 20.90 |
| ETH/USD | 0.60 / 0.60 | 0.55 / 0.55 |
| XRP/USD | 0.0015 / 0.0015 | 0.0010 / 0.00101 |
| LTC/USD | 0.15 / 0.15 | 1.20 / 1.22 |

Prices for percentage commissions (mid, 19:45–19:50 UTC): FTMO XAU
4,019.99, XAG 52.1615, BTC 80,838.98, ETH 2,333.95, XRP 1.28445, LTC
57.925; FundedNext XAU 4,020.73, XAG 52.2365, BTC 80,883.33, ETH 2,336.715,
XRP 1.2836, LTC 58.535.

Exness Standard `median_spread` (pips; metals, oil, crypto in units of 10
points): EUR/USD 0.8, GBP/USD 1.0, USD/JPY 1.0, USD/CHF 1.3, AUD/USD 0.9,
USD/CAD 1.4, NZD/USD 1.4, EUR/GBP 1.3, EUR/JPY 1.6, EUR/CHF 1.6, EUR/AUD
1.7, EUR/CAD 1.5, EUR/NZD 2.2, GBP/JPY 2.0, GBP/CHF 1.5, GBP/AUD 2.1,
GBP/CAD 1.8, AUD/JPY 1.1, AUD/CAD 1.8, CAD/JPY 1.1, CHF/JPY 2.1; XAUUSD 24
(point 0.001), XAGUSD 2.5 (0.001), USOIL 1.4 (0.001), UKOIL 1.7 (0.001),
BTCUSD 64 (0.01), ETHUSD 10 (0.01).

OANDA, 50 % of the time at or below, last month (pips): EUR/USD 0.8,
GBP/USD 1.2, USD/JPY 1.6, USD/CHF 1.5, AUD/USD 1.3, USD/CAD 1.8, NZD/USD
1.5, EUR/GBP 1.1, EUR/JPY 2.7, EUR/CHF 2.0, EUR/AUD 3.5, EUR/CAD 2.7,
EUR/NZD 6.6, GBP/JPY 3.8, GBP/CHF 2.5, GBP/AUD 5.0, GBP/CAD 3.7, AUD/JPY
2.3, AUD/CAD 2.4, CAD/JPY 2.5, CHF/JPY 3.9; XAU_USD 52.9 (pip 0.01),
XAG_USD 289.7 (pip 0.0001), WTICO_USD 3.6 (pip 0.01), BCO_USD 4.0 (pip
0.01).

## Limitations

- **Prop-firm figures are live readings, not averages.** The two windows,
  about 16:40 UTC (London close) and 19:45 UTC (New York afternoon), differ
  by up to 0.8 pip for the same pair (FundedNext EUR/USD 0.0 and 0.8). The
  brokers' figures cover the previous trading day (Exness) and a month
  (OANDA).
- **FundedNext commission.** The general rules page says "Commission
  charged per side"; the help article for the Stellar Instant account says
  commission is charged only at the opening, with "Opening 1 lot = $7
  commission" and nothing at closing. The once-per-trade reading is used.
  Read as charged on both sides, 17 forex `min_points` values would rise by
  1 to 9 points (largest: EUR/NZD 35 → 44), ETH/USD 149 → 212, BTC/USD
  5313 → 5355, XRP/USD 20 → 22 and LTC/USD 72 → 73.
- **FTMO crypto commission.** FTMO's symbols page lists 0.065 %; it does
  not say per side or round trip. Its forex ($5) and metals (0.0014 %)
  figures equal twice the per-side rates in FTMO's 25 Sep 2025 trading
  update, so 0.065 % is read as round trip by the same convention.
- **OANDA units for metals and oil** (pip 0.01 gold and oil, 0.0001
  silver) are not stated on the page; they are the only units that put
  OANDA within the range of the other three houses (a factor of 10 either
  way would make OANDA's gold spread $5.29 or $0.053).
- **Crypto spreads are a fixed number of points**, while the prop firms'
  crypto commissions are a percentage of the price. At other prices the
  all-in cost moves with the price; recalibrate when BTC or ETH moves
  more than about 20 % from the prices above.
- **Thin crypto coverage.** XRP and LTC have two houses each; LTC's two
  differ by a factor of 6.6 (FTMO $0.19, FundedNext $1.23). SOL has none.
- **Reviewed and not used.** IC Markets' spreads page shows no date and its
  tables are inconsistent (headline EUR/USD average 0.1 pip, table 0.01;
  Standard-account averages below their minimums). Pepperstone's
  instrument pages give averages for July 2024. Exness XRP, LTC and SOL
  are marked not tradable on the Standard account.

## Behaviour around the normal spread

- Calm market: the normal spread above.
- Fast market: the median of the last 20 price changes, if larger, at most
  10 × the normal spread.
- News windows: × 3.
- Daily rollover, 16:55 to 17:15 New York: × 3 (the larger of news and
  rollover, not both). Brokers widen spreads there as liquidity moves from
  New York to Asia [8]; OANDA publishes its averages both with and without
  17:00–19:00 New York for this reason [7]. The size of the widening is
  not published, so × 3 is a [DEFAULT].

## Recalibration

Repeat this review at least quarterly and whenever a house changes its
commissions. In P3, record spreads hour by hour from live accounts and
replace the live readings, the [E] value for SOL/USD and the [DEFAULT]
multipliers with measured figures.

## Sources

1. FTMO, Symbols (live ticker and symbol specifications), read 29 Oct 2026: https://ftmo.com/en/symbols/
2. FTMO, Trading update, 25 Sep 2025 (forex $2.50 per lot per side; metals 0.0007 % per side): https://ftmo.com/en/blog/trading-updates/trading-update-25-sep-2025/
3. FundedNext, Symbol specifications (live table), read 29 Oct 2026: https://fundednext.com/symbols
4. FundedNext, CFDs symbols and conditions (commission by account model): https://fundednext.com/general-rules/cfds/symbols-and-conditions
5. FundedNext Help Center, commission charges for the Stellar Instant account (charged only at the opening): https://help.fundednext.com/en/articles/11641300-what-are-the-commission-charges-for-the-stellar-instant-account
6. Exness, public instruments API for the Standard account (`mt5_mini_real_vc`), read 29 Oct 2026: https://www.exness.com/pwapi/api/v1/account-types/mt5_mini_real_vc/instruments/ ; table footnote on https://www.exness.com/commodities/
7. OANDA, Historical spreads (UK), read 29 Oct 2026: https://www.oanda.com/uk-en/trading/historical-spreads/ ; data from https://labs-api.oanda.com/v1/spreads/spread-stability-and-average?instrument=EUR_USD&division=OEL
8. Fusion Markets, rollover: https://fusionmarkets.freshdesk.com/support/solutions/articles/51000307963
9. Twelve Data, WebSocket FAQ (no bid or ask over WebSocket, the reason the engine sets spreads itself): https://support.twelvedata.com/en/articles/5194610-websocket-faq
10. IC Markets, Spreads (reviewed, not used): https://www.icmarkets.com/global/en/trading-pricing/spreads/
11. Pepperstone, EUR/USD instrument page (reviewed, not used; July 2024 data): https://pepperstone.com/fr-fr/trading/instruments/trade-forex/eurusd
