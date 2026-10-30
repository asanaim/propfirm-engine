/*!
 * TradingView Lightweight Charts™
 * Copyright (с) 2025 TradingView, Inc. https://www.tradingview.com/
 */
import {
  CandlestickSeries,
  createChart,
  type CandlestickData,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef } from "react";
import type { Bar, Quote } from "../api/types";

const INTERVAL_MS: Record<string, number> = {
  "1m": 60_000,
  "5m": 300_000,
  "15m": 900_000,
  "1h": 3_600_000,
  "4h": 14_400_000,
  "1d": 86_400_000,
};

// Display only: the chart library draws numbers, so points become decimals here.
function toCandle(bar: Bar, scale: number): CandlestickData {
  return {
    time: (bar.time / 1000) as UTCTimestamp,
    open: bar.open / scale,
    high: bar.high / scale,
    low: bar.low / scale,
    close: bar.close / scale,
  };
}

function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** Candles from the firm's own prices (TradingView Lightweight Charts; its
attribution logo links to tradingview.com, as its licence asks). */
export function PriceChart({
  bars,
  digits,
  interval,
  quote,
  label,
}: {
  bars: Bar[];
  digits: number;
  interval: string;
  quote: Quote | undefined;
  label: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const last = useRef<CandlestickData | null>(null);
  const scale = 10 ** digits;

  useEffect(() => {
    const element = box.current;
    if (element === null) return;
    const created = createChart(element, {
      autoSize: true,
      layout: { attributionLogo: true },
      timeScale: { timeVisible: interval !== "1d", secondsVisible: false },
    });
    const paint = () => {
      const text = token("--text");
      const grid = token("--border");
      created.applyOptions({
        layout: { background: { color: token("--surface") }, textColor: text },
        grid: { vertLines: { color: grid }, horzLines: { color: grid } },
      });
    };
    paint();
    const themes = new MutationObserver(paint);
    themes.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    chart.current = created;
    series.current = created.addSeries(CandlestickSeries, {
      upColor: "#1f8a5b",
      downColor: "#c4372d",
      borderVisible: false,
      wickUpColor: "#1f8a5b",
      wickDownColor: "#c4372d",
    });
    return () => {
      themes.disconnect();
      created.remove();
      chart.current = null;
      series.current = null;
    };
  }, [interval]);

  useEffect(() => {
    const s = series.current;
    if (s === null) return;
    s.applyOptions({ priceFormat: { type: "price", precision: digits, minMove: 1 / scale } });
    const candles = bars.map((b) => toCandle(b, scale));
    s.setData(candles);
    last.current = candles.at(-1) ?? null;
    chart.current?.timeScale().fitContent();
  }, [bars, digits, scale]);

  useEffect(() => {
    const s = series.current;
    const mid = quote?.mid;
    const at = quote?.source_ts;
    if (s === null || mid === null || mid === undefined || at === null || at === undefined) return;
    const price = mid / scale;
    const length = INTERVAL_MS[interval] ?? 60_000;
    const current = last.current;
    const start = interval === "1d" && current !== null ? Number(current.time) * 1000 : at - (at % length);
    let next: CandlestickData;
    if (current !== null && Number(current.time) * 1000 === start) {
      next = { ...current, high: Math.max(current.high, price), low: Math.min(current.low, price), close: price };
    } else if (current === null || start > Number(current.time) * 1000) {
      next = { time: (start / 1000) as UTCTimestamp, open: price, high: price, low: price, close: price };
    } else {
      return; // an older price than the last bar: ignore
    }
    s.update(next);
    last.current = next;
  }, [quote, interval, scale]);

  // a figure, not role="img": the chart holds a link (the attribution logo)
  return (
    <figure className="chart-figure" aria-label={label}>
      <div className="chart" ref={box} />
    </figure>
  );
}
