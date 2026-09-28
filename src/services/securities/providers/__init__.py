"""上游資料源。每個 provider 只負責「打上游 + 解析成共用資料型別」,
不碰 DB;快取/落庫在 `quotes.py` / `search.py`。

新增付費來源(Finnhub/FMP…)時實作同樣的函式簽名,再在 `quotes.py` 的
來源選擇處接上即可(Phase 3 的管理後台切換)。
"""
