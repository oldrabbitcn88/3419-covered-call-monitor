# 3419 covered-call holdings monitor

Checks the official Global X HSI Covered Call Active ETF page daily at 09:17, 18:17 and 21:17 Hong Kong time. The task records new disclosed dates only; missed historical days cannot necessarily be recovered.

- `data/holdings/YYYY-MM-DD.csv`: disclosed holdings snapshot.
- `data/details/YYYY-MM-DD.json`: official option details and index close.
- `data/coverage.csv`: daily nominal coverage series.

Coverage = official short-call notional / (equity market value + long HSI futures contracts × futures price × HKD 50). Money-market ETF 3137 is excluded. This is not delta-adjusted coverage. The script rejects unknown instruments, conflicting dates, option-notional mismatch, or changed snapshots; review failed Actions runs. If the issuer changes security types or holdings format, classification must be updated before collecting again. Historical files may be revised by issuer; conflicting snapshots require manual review.

Source: https://www.globalxetfs.com.hk/funds/covered-call-etf/

