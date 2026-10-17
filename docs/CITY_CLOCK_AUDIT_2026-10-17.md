# City-local checkpoint audit — 17 October 2026

Measured against production at 07:02 UTC. All 282 calls for 16 October across the 47 active cities matched the city's timezone and stored monthly median; zero early captures. Maximum actual capture delay was 46.77 minutes. The planned checkpoint is not the actual capture instant.

Jeddah's October reference was already 12:00 local, with 10:00 / 11:00 / 13:00 peak-relative checkpoints. The interface's UTC capture and target-date labels obscured the city clock. `fmtCityHour` also compared hours without their dates; that could render the viewer equivalent on the wrong day. Both are repaired. Historical calls/outcomes and the frozen monthly reference are unchanged. The references below were last fitted 13 September; they are seasonal timing estimates, not today's observed maximum, and not settlement confirmation. New model serving reads the live forecast peak and reports independently of these scheduling references.

| City | Timezone | Monthly median peak | Window width | Sample days |
|---|---|---:|---:|---:|
| amsterdam | Europe/Amsterdam | 12:55 | 4 h | 28 |
| ankara | Europe/Istanbul | 14:50 | 2 h | 31 |
| atlanta | America/New_York | 15:52 | 3 h | 31 |
| austin | America/Chicago | 15:53 | 2 h | 31 |
| beijing | Asia/Shanghai | 13:00 | 3 h | 29 |
| buenos_aires | America/Argentina/Buenos_Aires | 14:00 | 2 h | 28 |
| busan | Asia/Seoul | 13:00 | 4 h | 30 |
| cape_town | Africa/Johannesburg | 12:00 | 3 h | 31 |
| chengdu | Asia/Shanghai | 15:00 | 3 h | 26 |
| chicago | America/Chicago | 13:51 | 3 h | 28 |
| chongqing | Asia/Shanghai | 13:00 | 5 h | 28 |
| dallas | America/Chicago | 15:53 | 3 h | 31 |
| denver | America/Denver | 14:58 | 2 h | 31 |
| guangzhou | Asia/Shanghai | 14:00 | 2 h | 30 |
| helsinki | Europe/Helsinki | 13:50 | 5 h | 25 |
| houston | America/Chicago | 14:53 | 2 h | 31 |
| istanbul | Europe/Istanbul | 13:50 | 5 h | 27 |
| jeddah | Asia/Riyadh | 12:00 | 1 h | 31 |
| karachi | Asia/Karachi | 13:00 | 2 h | 31 |
| kuala_lumpur | Asia/Kuala_Lumpur | 13:00 | 2 h | 30 |
| london | Europe/London | 13:50 | 5 h | 26 |
| los_angeles | America/Los_Angeles | 11:53 | 3 h | 30 |
| lucknow | Asia/Kolkata | 13:30 | 2 h | 29 |
| madrid | Europe/Madrid | 17:00 | 3 h | 31 |
| manila | Asia/Manila | 12:00 | 5 h | 30 |
| mexico_city | America/Mexico_City | 13:51 | 3 h | 31 |
| miami | America/New_York | 12:53 | 3 h | 31 |
| milan | Europe/Rome | 14:50 | 3 h | 31 |
| moscow | Europe/Moscow | 13:30 | 3 h | 24 |
| munich | Europe/Berlin | 13:50 | 4 h | 31 |
| nyc | America/New_York | 14:51 | 4 h | 26 |
| panama_city | America/Panama | 12:00 | 4 h | 31 |
| paris | Europe/Paris | 14:00 | 3 h | 28 |
| qingdao | Asia/Shanghai | 12:00 | 3 h | 28 |
| san_francisco | America/Los_Angeles | 13:56 | 3 h | 31 |
| sao_paulo | America/Sao_Paulo | 14:00 | 5 h | 29 |
| seattle | America/Los_Angeles | 15:53 | 2 h | 30 |
| seoul | Asia/Seoul | 13:30 | 3 h | 26 |
| shanghai | Asia/Shanghai | 12:00 | 3 h | 27 |
| shenzhen | Asia/Shanghai | 13:00 | 4 h | 28 |
| singapore | Asia/Singapore | 13:00 | 4 h | 31 |
| tel_aviv | Asia/Jerusalem | 12:50 | 3 h | 30 |
| tokyo | Asia/Tokyo | 13:30 | 5 h | 24 |
| toronto | America/Toronto | 14:00 | 3 h | 29 |
| warsaw | Europe/Warsaw | 14:00 | 3 h | 28 |
| wellington | Pacific/Auckland | 13:00 | 5 h | 27 |
| wuhan | Asia/Shanghai | 14:00 | 4 h | 26 |

Evening-before 18:00 on D−1, morning 09:00 and noon 12:00 always mean the city's local clock. Day ahead is the last pricing before city-local midnight. Relative calls subtract two/one hours or add one hour to the median above. The actual maximum can happen earlier or later, including before noon. No clock filter claims the venue has settled.

Model choice is horizon-specific. On 602 matched first calls, 13–16 October, excluding post-peak calls: served engine 265 hits, S10 rd1 278, WXPredict v2 303, WXPredict v3 305; corresponding log loss 1.387 / 1.281 / 1.143 / 1.108. The market had 376 hits and 0.836 log loss. Only four dates are present, with partial first/last dates: this is preliminary evidence and not proof of a trading edge. The morning engine hit 65/147 against v3 55/147, so morning stays on its incumbent. The two completely covered dates reverse the v2/v3 bucket ranking (190 vs 186 hits on 374 calls); v3 retains the better probability score. Its use below is Hassan's explicit serving decision, not an automatic statistical promotion.

Hassan, 17 October: "te best precitive model sould take te eavy liftin in te predcition" and "keeop te multiple weater ensebmble weater models live and accuratel active". This authorizes a bounded, reversible forecast-serving choice (G5), not capital allocation. `wxrd3-horizons-v1` selects the existing hash-checked five-seed v3 for same-day noon/later and peak-relative checkpoints (from 09:00, excluding morning). Day-ahead/evening and morning retain their existing paths. Priors are fresh reports no older than 90 minutes, at least four weather inputs and twenty known forecast hours, whole exact ladders, valid probabilities and recorded inputs before serving. Missing/invalid inputs or weights keep the baseline; every failure is logged. The baseline's existing trading-eligibility gates remain. The primary uses direct bucket probabilities, no fabricated normal centre/width and no old probability calibration. An exact feature vector, hash, receipt, history commit and model version accompany each primary prediction in the existing archived `wx_features` record. No blinded score or candidate holdout is read or changed.

A read-only rehearsal using production inputs known at 07:02:20 UTC (17,193 latest capture keys, all 47 city definitions and 94 open markets) built 18 eligible ladders in 8.08 seconds locally, including Jeddah's pre-peak checkpoint; every distribution summed to one. This is a model/data-path rehearsal, not a production latency measurement or an accuracy claim. The final Python suite passed 4,203 tests with 9 skips in 172.19 seconds; database contracts and web checks passed. First real post-deploy serving remains required.


## Recent timing diagnostic

The latest 30 completed city-local days in derived_city_day_peak (at least 12 readings, maxima between 08:00 and 22:00, earliest equal maximum) give the following medians. These are operational readings, not a new settlement authority or a replacement of recorded checkpoint schedules. Sampling, season and today's weather can move the actual peak. Paris and Istanbul differ by 1.5 hours from the stored October references. The page labels the reference as typical; WXPredict also reads the day's own forecast curve and station reports rather than assuming the reference must be today's peak.

| City | Completed days | Recent median (local) | Difference from monthly reference |
|---|---:|---:|---:|
| paris | 29 | 15.50 h | 1.50 h |
| istanbul | 23 | 12.33 h | -1.50 h |
| munich | 30 | 14.83 h | 1.00 h |
| shenzhen | 30 | 14.00 h | 1.00 h |
| cape_town | 29 | 13.00 h | 1.00 h |
| qingdao | 29 | 13.00 h | 1.00 h |
| jeddah | 30 | 11.00 h | -1.00 h |
| toronto | 27 | 15.00 h | 1.00 h |
| tokyo | 24 | 12.50 h | -1.00 h |
| chongqing | 26 | 14.00 h | 1.00 h |
| lucknow | 29 | 12.50 h | -1.00 h |
| amsterdam | 28 | 13.92 h | 1.00 h |
| mexico_city | 30 | 14.70 h | 0.85 h |
| seattle | 30 | 15.13 h | -0.76 h |
| beijing | 30 | 13.75 h | 0.75 h |
| tel_aviv | 30 | 12.08 h | -0.75 h |
| houston | 28 | 14.21 h | -0.67 h |
| chicago | 25 | 14.50 h | 0.65 h |
| atlanta | 30 | 15.29 h | -0.58 h |
| london | 28 | 14.33 h | 0.50 h |
| milan | 29 | 15.33 h | 0.50 h |
| chengdu | 28 | 14.50 h | -0.50 h |
| seoul | 29 | 13.00 h | -0.50 h |
| shanghai | 26 | 11.50 h | -0.50 h |
| moscow | 28 | 14.00 h | 0.50 h |
| karachi | 30 | 12.50 h | -0.50 h |
| ankara | 28 | 14.33 h | -0.50 h |
| austin | 29 | 15.42 h | -0.46 h |
| dallas | 28 | 15.58 h | -0.30 h |
| los_angeles | 29 | 12.17 h | 0.29 h |
| kuala_lumpur | 30 | 13.25 h | 0.25 h |
| guangzhou | 30 | 14.25 h | 0.25 h |
| miami | 30 | 12.98 h | 0.10 h |
| san_francisco | 30 | 13.88 h | -0.05 h |
| nyc | 26 | 14.80 h | -0.05 h |
| denver | 29 | 14.97 h | 0.00 h |
| helsinki | 25 | 13.83 h | 0.00 h |
| wuhan | 27 | 14.00 h | 0.00 h |
| madrid | 29 | 17.00 h | 0.00 h |
| manila | 30 | 12.00 h | 0.00 h |
| panama_city | 30 | 12.00 h | 0.00 h |
| sao_paulo | 27 | 14.00 h | 0.00 h |
| busan | 29 | 13.00 h | 0.00 h |
| buenos_aires | 28 | 14.00 h | 0.00 h |
| singapore | 30 | 13.00 h | 0.00 h |
| warsaw | 28 | 14.00 h | 0.00 h |
| wellington | 25 | 13.00 h | 0.00 h |
