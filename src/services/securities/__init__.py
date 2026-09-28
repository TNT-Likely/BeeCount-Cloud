"""股票持股(2026-09-28,docs/STOCK_HOLDINGS_SD.md):市場資料抓取 + 持股計算。

- `markets`:市場代碼 ↔ 幣別/時區/交易時段/Yahoo 後綴。
- `holdings`:由 stock_trade 明細即時彙總持股(移動平均成本法),App 端
  lib/services/investment/holdings_calculator.dart 是同一套算法,兩邊共用
  tests/fixtures/stock_holdings_vectors.json 測試向量。
- `providers/`:上游資料源(證交所/櫃買 OpenAPI、Yahoo Finance)。
- `quotes`:報價快取讀取 + 盤中補抓 + 收盤排程。
- `search`:證券搜尋 + 台股清單同步。
"""
