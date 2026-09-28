# Stations around each settlement airport (plan v2.4 P3.10 part 7)

The METAR temperatures of every active city's settlement station and of the stations around it, for the study in `tools/p310_neighbours.py` (`docs/NEIGHBOUR_STATIONS_2026-09-28.md`).

- **Source:** the Iowa Environmental Mesonet's ASOS/METAR archive (`asos.py`): routine and special reports, `tmpf` converted to °C at one decimal. Station lists come from IEM's network files, `NETWORKS` in the tool.
- **Period:** 1 Dec 2025 00:00 to 26 Sep 2026 23:58 UTC. IEM's end date is exclusive, so the tool's `END` of 27 Sep gives no readings on 27 Sep.
- **The live tick reads the same service** (`asos.py`, every hour at about :36). A research fetch asks one station at a time and backs off. On 28 Sep, the 16:36Z tick's observation fetch got a 503 while this download's first, batched requests were getting 503 too; the cause is not established (P5.12 row).
- **Fetched:** 28 Sep 2026, one station per request. IEM refused nine at once (503) and asked for fewer requests (429), so the tool waits between requests. The raw per-station downloads are cached in `cache/`, which is not committed.

## Files

- **`candidates.csv`:** each city's settlement station (`role` = settlement) and up to 8 nearest stations 10–150 km away (`candidate`), with the distance in km.
  - It holds 48 cities and 242 candidates, 288 distinct stations in all.
  - US networks use FAA ids (`ORD` for KORD).
- **`chosen.json`:** per city,
  - the settlement station;
  - the neighbours: the 4 nearest candidates with a report in at least 70% of the period's hours;
  - every station's hourly coverage.
  - The coverage denominator includes 27 Sep, which has no readings, so every coverage is understated by about 0.3 percentage points.
- **`metar.csv.gz`:** `city_key`, `station`, `minute` (UTC epoch minutes), `temp_c`, for each city's settlement station and chosen neighbours. It holds 1,876,936 readings for 48 cities.

## Coverage

- **32 of 48 cities** have at least 2 neighbours, 121 neighbours in all.
- **16 do not.** Either their nearby stations report only part of the time in IEM's archive (Tokyo's RJTI 37% and RJTF 40% of hours), or there is no station in the ring (Karachi), or IEM has no network (Wellington). The list: Ankara, Busan, Cape Town, Chengdu, Chongqing, Guangzhou, Jeddah, Karachi, Lucknow, Qingdao, Shenzhen, Tel Aviv, Tokyo, Wellington, Wuhan, Zhengzhou.
