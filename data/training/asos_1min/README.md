# US one-minute station readings (plan v2.4 P3.10 part 5)

The minute-by-minute temperature (`tmpf`, whole °F) of the US cities' current settlement stations, 28 Feb to 27 Sep 2026 (UTC).

- **Source:** IEM's one-minute ASOS archive (`asos1min.py`), which republishes NCEI's. It is not published in real time.
- **Fetched:** 28 Sep 2026 by `tools/p310_one_minute.py`, one station and month at a time.
- **Files:** one file per station, `<ICAO>.csv.gz`, with columns `utc_epoch`, `tmpf`.

| station | minutes |
|---|---|
| KATL | 243,029 |
| KAUS | 184,452 |
| KORD | 172,847 |
| KDAL | 242,007 |
| KHOU | 259,724 |
| KLAX | 189,996 |
| KMIA | 222,263 |
| KLGA | 189,761 |
| KSFO | 240,889 |
| KSEA | 250,207 |

- **Missing:** Denver's KBKF (Buckley Space Force Base) returned no minutes; it is not in the archive.
- **Used by:** `docs/ONE_MINUTE_READINGS_2026-09-28.md`.
