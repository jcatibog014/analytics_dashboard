# Benchmarks

Made by `flask --app tallyho benchmark` on 2026-11-10 (sqlite).
911,701 order lines and 495,450 orders, 730 days of sample sales.
Importing them took 42.3 s (21,536 lines a second).

Questions about the last 365 days, the best of 3 runs:

| Question | Raw tables | Rollups | Faster | Rows read (raw / rollup) |
|---|---:|---:|---:|---:|
| KPI totals | 652.5 ms | 81.0 ms | 8x | 268,788 / 7,294 |
| Revenue by day (with a 7-day average) | 609.5 ms | 5.5 ms | 112x | 268,788 / 7,294 |
| Sales by region | 639.6 ms | 3.3 ms | 193x | 268,788 / 7,294 |
| Top 10 products | 990.9 ms | 64.7 ms | 15x | 494,972 / 134,577 |

## The index

The raw KPI query for the last 30 days, with its index: 57.9 ms; without it: 129.3 ms.

## Paging

Page 14,587 of Records: OFFSET 494.3 ms, keyset 0.9 ms.
