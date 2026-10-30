/** Open-source notices the portal's licences ask to show its users. */
export function NoticesPage() {
  return (
    <div className="page narrow">
      <h1>Notices</h1>
      <h2>Charts</h2>
      <p>
        TradingView Lightweight Charts™, Copyright (c) TradingView, Inc.{" "}
        <a href="https://www.tradingview.com/" rel="noopener noreferrer" target="_blank">
          https://www.tradingview.com/
        </a>
        . Licensed under the Apache License, Version 2.0.
      </p>
      <h2>Type and icons</h2>
      <p>Inter, licensed under the SIL Open Font License 1.1. Lucide icons, licensed under the ISC License.</p>
      <h2>Software</h2>
      <p>
        React (MIT License) and oidc-client-ts (Apache License 2.0). Prices on charts and price tables come from the
        firm's own feed; the source is named beside them.
      </p>
    </div>
  );
}
