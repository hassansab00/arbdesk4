# Daily Maximum Temperature Forecasting for Weather and Energy Trading

Research date 7 October 2026  
Prepared for Hassan Sabra  
Scope Scientific foundations, operational systems, quantitative methods, observations, validation, Polymarket resolution, weather derivatives, and energy futures

## Main findings

The most defensible practical approach is a station-specific probabilistic forecasting system that combines operational numerical weather predictions, statistical correction, and updates from live observations. Its main output should be a distribution of possible daily maximum temperatures and settlement outcomes. A single forecast high is useful, but insufficient for pricing narrow temperature buckets.

The scientific literature supports several workable postprocessing approaches, including ensemble model output statistics, Bayesian model averaging, quantile regression forests, and neural distributional regression. Their relative value must be established on the actual stations, prediction times, and settlement definitions being traded. Published improvements elsewhere do not establish profitability or a universal exact-temperature accuracy rate. [S15–S23]

Polymarket temperature contracts and energy instruments share meteorological inputs but require different outputs. A Polymarket contract can depend on a particular airport, a particular observation table, its temperature precision, and a fallback source. Electricity and natural-gas futures require weather-to-demand and demand-to-price models. CME temperature derivatives have explicit temperature index definitions that can use different stations and observation windows. [S1–S3, S34–S39]

Two findings materially change an implementation designed around older assumptions:

- Current example Polymarket contracts name NOAA as their primary observation source, with Weather Underground as a fallback. The NYC example explicitly selects hourly data. Do not assume every weather contract settles from the same provider or sampling frequency. [S1–S3]
- Polymarket currently lists weather-market fees. An execution model must include the applicable fees and spread, rather than assuming weather trades are fee-free. [S4–S5]

This is a literature and official-documentation review, with current contract examples. It does not include a newly run station backtest, a live trading experiment, an audit of ArbDesk4, or access to proprietary commercial forecasting performance. Mathematical examples below are labeled illustrations. Proposed engineering choices are recommendations, rather than claims that a specific deployed system already achieves them.

## 1 Define exactly what is being predicted

### Three related temperature targets

Distinguish these quantities before collecting training labels:

| Target | Meaning | Suitable use |
|---|---|---|
| Meteorological daily maximum | Maximum according to a specified instrument, averaging rule, and observation window | Scientific verification and some official climate records |
| Maximum of published observations | Highest value in a specified sequence, such as a website's hourly table | Contracts that explicitly settle from that sequence |
| Settlement outcome | Bucket assigned after the contract's source, precision, fallback, revision, and exceptional-error rules are applied | Polymarket probabilities and trade settlement |

For an idealized station temperature path, define a daily maximum as

\[
Y_d = \max_{t\in W_d} T_s(t),
\]

where \(s\) is the exact station and \(W_d\) is the valid observation window. In production, \(T_s(t)\) must stand for the instrument's defined observation rather than an unspecified instantaneous physical temperature.

For a contract that uses a published table, define a different target:

\[
Y_d^{\mathrm{published}} = \max_{t\in S_d} q\!\left(T_s(t)\right),
\]

where \(S_d\) contains eligible published observations and \(q\) represents the source's measurement, conversion, and display behavior. The contract outcome also depends on the source actually used and any allowed correction.

The practical implication is that improving a forecast of the physical high can fail to improve the contract prediction if the model is trained against the wrong observation sequence.

### Current market examples

| Example reviewed | Station | Units | Source behavior relevant to the model |
|---|---|---|---|
| Polymarket London 7 October 2026 | London City Airport EGLC | Celsius | NOAA temperature table; whole-degree precision; Weather Underground fallback |
| Polymarket NYC 7 October 2026 | LaGuardia KLGA | Fahrenheit | NOAA table with hourly data explicitly selected; Weather Underground fallback |
| Polymarket Seoul 8 October 2026 | Incheon International RKSI | Celsius | NOAA temperature table; Weather Underground fallback |
| CME US degree-day futures | Contract-specific stations, including LaGuardia for New York | Fahrenheit | Daily max and min over 0000–2359 local standard time, processed into a monthly index |
| CME European London HDD futures | Heathrow WMO 03772 | Celsius | Max and min use specified, different UTC windows |

Sources: [S1–S3, S34–S35]. These are examples reviewed on the research date, not a universal registry of all weather markets.

Keep the complete contract text and subsequent clarifications as dated records. For each market store the station, source URL, displayed units, observation selection, valid date, window and timezone interpretation, bucket boundaries, fallback deadline, correction policy, and final outcome. Resolve any ambiguity in displayed dates or timezones against the named source and contract before using the market for execution.

Do not silently apply an official climate report's daily window to a contract that names a website's observation table. Likewise, a city label is not a geocoding instruction: London City is not Heathrow, LaGuardia is not Central Park, and Incheon airport is not downtown Seoul.

## 2 Why daily maximum temperature is difficult

The daily high is a consequence of the surface energy budget, movement of air masses, turbulent mixing, and local surface conditions. It is a maximum over time, so the timing of these processes matters as much as their average intensity.

A schematic surface budget is

\[
R_n = H + LE + G + \Delta S,
\]

with net radiation \(R_n\), sensible heat \(H\), latent heat associated with evaporation \(LE\), ground heat flux \(G\), and storage \(\Delta S\). This explains the heating available at the surface; it is not by itself an equation that predicts two-metre air temperature. NOAA's energy-balance material describes the role of incoming solar and outgoing terrestrial radiation. [S6]

ECMWF identifies soil conditions, cloud properties, wind, mixing, surface representation, season, and geography among the causes of near-surface temperature error. These are useful predictors for a local correction model. [S7]

| Physical mechanism | How it affects the high | Useful predictive evidence |
|---|---|---|
| Solar heating and cloud | Cloud timing changes how much heating occurs before the peak | Shortwave radiation, cloud layers, satellite cloud development, solar elevation |
| Air-mass advection | Wind can import warmer or cooler air independently of sunshine | Pressure-level temperatures, thickness, wind vectors, frontal timing |
| Boundary-layer mixing | Mixing couples surface air to warmer or cooler layers aloft | Boundary-layer depth, vertical temperature profile, wind and stability |
| Evaporation and soil moisture | The division between latent and sensible heating changes | Soil moisture, recent rainfall, vegetation, surface fluxes |
| Marine influence | Onshore flow can stop or delay warming at a coastal station | Wind shift, pressure gradient, sea temperature, marine cloud |
| Snow cover | Albedo, insulation, and stability change the daily cycle | Snow depth or fraction, radiation, inversion indicators |
| Terrain and station exposure | A model grid cell can differ substantially from the actual site | Station elevation, grid elevation, land cover, coastal position |
| Rain and convection | Cooling and cloud can arrive abruptly before the forecast peak | Radar, precipitation probability, convective timing, cloud growth |

These rows organize physical mechanisms into proposed features; they are not fixed temperature adjustments. A correction such as “subtract two degrees whenever wind is onshore” would need local evidence and would usually be too crude.

A sunny day can still be difficult when a small shift in sea-breeze timing decides which one-degree bucket wins. A cloudy day can be forecastable when cloud persists reliably. The relevant quantity is conditional forecast error, not a city's reputation for stable weather.

## 3 The systems used by forecasting centres

### Observations and data assimilation

Operational forecasting begins with an estimate of the current atmosphere, land, and ocean. Data assimilation combines observations with a recent model forecast to build that estimate. Satellites, surface stations, aircraft, radiosondes, ships, and buoys contribute different information; observations are unevenly distributed and have measurement errors. ECMWF describes assimilation as a repeated cycle of comparing the model with new observations, updating the state, and forecasting again. [S8–S9]

Methods include variational approaches such as 3D-Var and 4D-Var, ensemble Kalman filtering, and hybrid systems. Their role is broader than averaging station readings: they update a dynamically consistent atmospheric state. A small trading application should normally consume forecasts produced by these systems rather than attempt to reproduce global assimilation.

For a local application, a residual filter or observation-conditioned model can update a station forecast efficiently. That is useful local postprocessing; it does not replace the forecasting centre's assimilation system.

### Numerical weather prediction

Numerical models approximate conservation of momentum, mass, energy, and water on a three-dimensional grid. They also represent processes that cannot be resolved explicitly at that grid scale, including aspects of turbulence, radiation, cloud physics, convection, and land-surface exchange. ECMWF publishes separate IFS documentation for observations, assimilation, dynamics, physics, and ensemble prediction. [S10]

The two-metre air temperature is not interchangeable with surface skin temperature, pressure-level temperature, or a satellite land-surface-temperature estimate. ECMWF describes its two-metre temperature as a diagnostic derived between the lowest atmospheric level and the underlying surface, accounting for stability. [S11]

An atmospheric model can predict the large-scale pattern correctly while missing a station high because the grid represents different terrain, land cover, cloud timing, or coastal exposure. This is why station postprocessing remains valuable even when the upstream model is strong.

### Ensemble forecasting

An ensemble produces alternative weather trajectories by perturbing initial conditions and model representations. Its purpose is to express uncertainty that a single deterministic run cannot show. ECMWF identifies uncertainty in initial conditions and approximations in the model as the two main sources of growing forecast error. [S12]

Ensemble spread is information about predictability, not an automatic calibrated error bar. Members share a model and much of their information, and several models can share analyses, boundary conditions, or systematic errors. Treating 100 related predictions as 100 independent votes overstates evidence.

For a daily maximum, derive the maximum from each coherent member trajectory over the target window. Then postprocess the resulting distribution against matching observations. Mixing model families is useful when it adds measured predictive value, rather than merely increasing the member count.

## 4 Operational forecasts worth evaluating

The following systems are candidates for a research and production stack. The proposed roles are engineering recommendations. There is no claim that their ordering is a universal ranking for daily highs.

| System or product | Evidence and current status reviewed | Proposed role |
|---|---|---|
| ECMWF IFS deterministic and ensemble | Cycle 50r1 went live 12 May 2026; duplicate HRES and ensemble-control forecasts were unified | Global atmospheric scenarios and medium-range guidance |
| ECMWF AIFS Single and ENS | Both upgraded to version 2 in May 2026; operational ensemble available | Additional global AI scenarios to test against station labels |
| NOAA GEFS | Operational global ensemble with published reforecast resources | Independent model-family input and historical calibration research |
| NOAA HRRR | Frequently updated 3 km US system; major cycles extend to 48 hours | US short-range trajectory, cloud, wind, and convection features |
| NOAA National Blend of Models | Version 5.0 operational from May 2026; July temperature-transition update documented | Strong US probabilistic and deterministic benchmark |
| NOAA station MOS | Statistical relationships between model outputs, observations, and geoclimatic data | Inexpensive station-specific benchmark |
| NOAA LAMP | Frequently updated station guidance including temperature | Observation-aware short-range benchmark |
| Met Office UKV and MOGREPS-UK | Official regional deterministic and ensemble systems | UK station and local-process candidates |
| DWD ICON-D2 and ICON-D2-EPS | Regional grid around 2 km, with public ensemble temperature files | Stations within the documented regional domain |
| Canadian GEPS | Global ensemble with open-data documentation | Another global family, subject to incremental value testing |

Sources for configuration and status: [S13–S14, S24–S32, S57]. Grid spacing does not equal forecast accuracy at a station.

### Current changes that require engineering attention

The NOAA notice updated 2 October 2026 schedules RRFS and REFS implementation for 3 November 2026, replacing NAM, HREF, SREF, and HiresW. Earlier projected dates in model descriptions or old notices are not the current implementation schedule. Build version-aware ingestion and verify the actual cutover before switching sources. [S33]

The KMA LDAPS archive page reviewed says UM data provision stopped in March 2026. A legacy description of a 1.5 km model is therefore insufficient evidence that a proposed current Korean feed remains available. Use current KMA notices and product access documentation when choosing a feed. [S46]

### Model access and cost

ECMWF's full real-time catalogue has an open licence, but open licensing does not guarantee cost-free delivery of every product. ECMWF distinguishes the free public subset from service-based delivery. The public page reviewed lists a 0.25-degree subset, recent-run retention, and both IFS and AIFS products. [S14, S47]

For a lean system, extract the stations and predictors needed rather than repeatedly downloading global fields. Retain the model version, native resolution, delivered resolution, forecast initialization, valid times, forecast steps, and first availability. A forecast initialized at 00 UTC is not available to trade at 00 UTC simply because its filename has that timestamp.

NWS offers a free API for forecasts and observations, with rate limits and a cache-aware design. The gridpoint endpoint provides numerical layers; station coordinates should determine the relevant forecast grid. [S48–S49]

## 5 Quantitative methods with practical relevance

### Method selection

| Method | What it estimates or corrects | Best use | Main weakness |
|---|---|---|---|
| Persistence and seasonal climatology | A simple forecast or empirical distribution | Baseline and fallback | Limited response to evolving weather |
| Rolling bias correction | Recent systematic forecast error | Cheap initial correction | Limited conditional structure |
| MOS and regularized regression | Station outcome from numerical predictors and observations | Clear, small-data benchmark | Linear relationships may miss regime changes |
| EMOS | Parameters of a predictive distribution from ensemble summaries | Strong probabilistic starting model | Chosen distribution may fit complex cases poorly |
| Bayesian model averaging | Weighted mixture of calibrated component distributions | Multiple model families or scenarios | Weight and variance estimation can overfit |
| Quantile mapping | Distributional bias | Bias adjustment with adequate history | Does not guarantee conditional reliability |
| Quantile regression forests | Conditional quantiles from nonlinear predictors | Station and regime interactions | Limited extrapolation of rare extremes |
| Distributional boosting | Conditional distribution parameters with feature selection | More predictors and nonlinear correction | Tuning and overfitting risk |
| Neural distributional regression | Predictive distributions using pooled stations and flexible features | Larger archives and shared station learning | Harder to diagnose with sparse data |
| Analog ensembles | Past verifying observations for similar past forecasts | Local regimes and interpretable scenarios | Scarce analogs during unprecedented conditions |
| Kalman or state-space residual filtering | Evolving local forecast error | Same-day updates | Simple residual assumptions can fail at wind or cloud transitions |
| Ensemble copula coupling and related methods | Joint time, space, or variable dependence | Trajectories and energy indices | Marginal calibration alone does not validate dependence |
| Conformal methods | Empirical prediction intervals around a forecast | Additional coverage diagnostic or correction | Time dependence and drift complicate coverage guarantees |

These are complementary tools. The starting question is which method improves held-out station probability forecasts at the intended issue times.

### MOS and rolling correction

NOAA MOS uses relationships among numerical predictions, prior observations, and geoclimatic information to predict weather at stations or grid points. The official description identifies multiple linear regression among its statistical methods. [S24]

A simple implementation baseline for station \(s\) and lead \(h\) is

\[
\widehat{Y}_{s,h}=a_{s,h}+b_{s,h} f_{s,h}+\boldsymbol\beta_{s,h}^{\mathsf T}\mathbf{x},
\]

where \(f\) is numerical temperature guidance and \(\mathbf{x}\) contains selected predictors. Regularization and partial pooling are recommended when each station has limited history.

An exponentially weighted bias estimate is another useful baseline:

\[
b_{d}=(1-\lambda)b_{d-1}+\lambda(Y_d-f_d).
\]

Use only outcomes that were finalized and available before the next prediction. Select the adaptation rate from chronological validation; do not let one anomalous day trigger unrestricted changes.

NOAA-hosted research comparing deterministic postprocessing found that several corrections reduced error relative to raw guidance, with combinations of methods performing well in its experiment. Its validation used ERA5 analyzed data, so this is evidence for correction methods, not direct proof of airport settlement accuracy. [S50]

### Ensemble model output statistics

EMOS was introduced to address biased and underdispersed ensemble forecasts while using the spread-skill relationship. It estimates a predictive distribution rather than treating ensemble counts as final probabilities. [S15–S16]

A proposed temperature implementation can start with

\[
Y\mid\mathbf{x}\sim \mathcal N(\mu(\mathbf{x}),\sigma^2(\mathbf{x})),
\]

\[
\mu=a+\sum_m b_m f_m+\boldsymbol\beta^{\mathsf T}\mathbf{x},
\qquad
\sigma^2=c+dS^2,
\]

where \(S^2\) is ensemble spread and parameters must keep variance positive. A richer extension can make variance depend on model disagreement, local hour, regime, and observation residuals.

This Gaussian form is a starting hypothesis. Validate its tails and bucket probabilities. A sea-breeze-arrives versus sea-breeze-delays situation may need a mixture, or a nonparametric model, rather than one smooth symmetric density. Optimize using a proper probability score, not only error in the forecast mean.

### Bayesian model averaging and mixtures

BMA combines calibrated predictive distributions with learned weights. The original weather paper constructs a weighted density centered on bias-corrected component forecasts, addressing the underdispersion of raw ensembles. [S17]

A proposed mixture is

\[
p(Y)=\sum_k w_k\,p_k(Y),\qquad w_k\geq0,\quad\sum_k w_k=1.
\]

Weights should reflect held-out predictive value. Begin with a small number of meaningful model families and regularized weights. Do not assign every forecast website a separate independent component when they reuse the same upstream model.

Regime mixtures are also possible, but their regime probabilities must be estimated. A two-scenario story is not itself a calibrated two-scenario forecast.

### Quantile mapping and quantile regression

Quantile mapping transfers a forecast's position in its historical forecast distribution to the corresponding position in an observed distribution. It can correct systematic distributional bias. NOAA's current temperature documentation describes quantile-mapped inputs and probabilistic products. [S25]

This adjustment should use comparable season, lead, source definition, and station information. A correction fitted across mixed observation windows can learn a target mismatch rather than a weather bias. Mapping an unconditional distribution also does not establish reliable probabilities conditional on a particular weather regime.

Quantile regression forests estimate conditional quantiles without specifying a parametric density. Taillardat and colleagues studied their use for ensemble calibration alongside EMOS. [S18]

For this application, potential predictors include ensemble temperature summaries, forecast radiation, cloud layers, wind direction, humidity, rain, season, grid-to-station elevation difference, and observed temperature residuals. Check quantile ordering, calibrated coverage, and the behavior outside the historical range. Do not infer a narrow bucket probability from a few quantiles without validating the interpolation between them.

### Boosting and neural postprocessing

Nonhomogeneous boosting research reports improved daily minimum and maximum temperature predictions at five central European stations through predictor selection. Other tree-based research evaluates probabilistic correction of hourly two-metre ensemble temperature. These support testing feature-rich postprocessing, while leaving station transfer and target matching as separate questions. [S19–S20]

Rasp and Lerch studied neural distributional regression for two-metre temperature at German stations, demonstrating an approach that learns nonlinear relationships and station information. [S21–S22]

For a multi-city system, pooled training with station embeddings or explicit station features can share information. Use held-out station and time tests to find whether sharing helps a new station. A neural model trained on a small recent archive can be less reliable than a simple regularized EMOS model.

### Analogs and dependence

An analog ensemble uses observations that verified similar historical forecasts at matching locations and leads. It gives an interpretable empirical distribution, conditional on available analogs. [S23]

For energy indices and hourly paths, preserve dependence between hours, cities, and temperature extrema. Ensemble copula coupling calibrates marginals and restores a dependence structure through ensemble ranks. This is one established approach; joint distributions still require their own validation. [S40]

Conformal prediction can help assess or adjust interval coverage, but ordinary exchangeability assumptions are problematic when data drift over time. Research extends conformal methods beyond exchangeability; it does not justify claiming automatic conditional coverage for every city and weather regime. [S41]

## 6 Modern AI weather systems

Large AI weather models learn atmospheric evolution from extensive training datasets. They belong upstream of a station-specific trading model, as additional sources of atmospheric scenarios or features.

| Model family | Research or operational evidence reviewed | Interpretation for this project |
|---|---|---|
| GraphCast | Published research on medium-range global forecasting | A candidate global forecast; evaluate local extrema separately |
| GenCast | Published probabilistic global trajectories at 12-hour steps and 0.25-degree resolution | Broad uncertainty modeling; temporal detail needs attention for daily highs |
| AIFS | Current ECMWF deterministic and ensemble operational products | Directly testable global forecast inputs |
| Aurora | Published Earth-system foundation-model research, with current documentation describing expanded variants | Research candidate; verify product, variables, licence, and deployment details |

Sources: [S13, S42–S45].

GenCast's paper reports superior skill to ENS on 97.2% of 1,320 evaluated targets. That is a comparison across benchmark targets, not a 97.2% probability of getting tomorrow's airport high or settlement bucket correct. Its documented 12-hour prediction steps also matter when the high can occur between output times. [S42]

Use AI models through a versioned evaluation process: ingest their current outputs, derive the appropriate features or extrema, calibrate to the station target, and compare them with existing inputs on held-out dates. A better global score does not establish incremental local value after calibration.

LLMs can help explain structured outputs or summarize official forecast discussions. They should not invent numerical probabilities, extrapolate the day's high from a narrative alone, or modify trading probabilities without a separately verified numerical method.

## 7 Constructing a daily maximum from forecasts

### The maximum of an average is the wrong ensemble calculation

For member \(j\), calculate

\[
M_j=\max_{t\in W_d} T_j(t).
\]

Use the distribution of \(M_j\), then calibrate it. Do not calculate a single hourly ensemble-average path and call its highest point the ensemble daily-high distribution.

In general,

\[
\max_t\mathbb E[T(t)]\ne\mathbb E[\max_t T(t)].
\]

If members peak at different times, averaging first smooths the peaks. In a simple deterministic illustration, two paths with opposite values at two hours each reach 30°C, but their average path can peak at only 29°C. This is a mathematical example, not observed performance.

### Native extrema and sampled temperatures

ECMWF documents a parameter for maximum two-metre temperature in the previous six hours. It contains the interval maximum, whereas a temperature field at one valid time is instantaneous output at that time. [S51]

Native maxima can be preferable to taking the maximum of sparse snapshots, but only when their intervals align with the target. An interval spanning midnight includes information from two dates. Selecting it wholesale contaminates a daily target. Check GRIB start and end steps, interval semantics, and the required timezone before aggregation.

Hourly output is still a sequence of samples. It can miss a peak between samples, just as an observation table can. There are three defensible approaches to investigate:

1. Use appropriate native extrema that align with the target window.
2. Generate and calibrate sufficiently detailed coherent temperature paths.
3. Directly predict the exact daily or published maximum target using forecast summaries and observations.

For a contract that deliberately settles from hourly readings, an estimate of an unreported between-hour peak can be physically correct but irrelevant to settlement. Keep the targets separate.

### Product windows can differ from a full calendar day

NOAA's NBM weather-element documentation defines particular max and min windows; its maximum-temperature product is not automatically the maximum over every location's midnight-to-midnight civil day. A forecast-provider guide likewise describes daytime maxima separately from the night. [S25, S52]

Therefore, retain the forecast's valid interval explicitly. Convert units and align windows before comparing sources. This is one reason two providers can show different highs without one necessarily making a larger error against its own defined product.

## 8 Same day forecasting and live updates

Before the date begins, the task is a distribution over the future high. Once the day is underway, some eligible observations already exist and the question changes to how much higher the source can still report.

Let \(m_t\) be the running maximum of accepted target observations up to time \(t\), and let \(Z_t\) be the future maximum over the remaining eligible times. Then

\[
Y_d=\max(m_t,Z_t).
\]

For \(y<m_t\), the conditional probability of \(Y_d\leq y\) is zero. For \(y\geq m_t\), it equals the conditional probability that the remaining maximum \(Z_t\) is no larger than \(y\). There is generally probability mass exactly at \(m_t\), representing the possibility that the high has already happened.

This is why simply truncating a morning Gaussian forecast and renormalizing it is not generally a complete same-day model. It can omit the chance that the accepted running high remains the final high.

### Proposed same day features

- The entire eligible observation path and running maximum, rather than only the latest reading.
- Temperature change over multiple recent periods, with noise-aware smoothing.
- Observation minus forecast at matching times, and the evolution of that residual.
- Wind direction and speed changes, dew point, pressure tendency, and precipitation.
- Current cloud state, upstream cloud movement, and shortwave-radiation departure.
- Updated short-range trajectories and the distribution of possible peak times.
- Number of remaining eligible source reports before the contract window ends.
- Data age, missing reports, station flags, and source disagreement.

A basic residual model could use

\[
r_t=\rho r_{t-1}+\eta_t,\qquad
T_{\mathrm{obs},t}=T_{\mathrm{base},t}+r_t+\epsilon_t.
\]

This is a proposed state-space baseline. More flexible models can predict remaining warming directly or update member-path likelihoods using several observed variables. Residual persistence must be learned; a wind change can invalidate a previously persistent bias.

Do not add independent random errors to every hour and take their maximum without validating temporal dependence. That procedure can create unrealistically high maxima because every hour receives a separate chance at a large positive error.

### Certainty and observation quality

The lower-bound property applies to the accepted target observation sequence. A provisional high from a different source, a rounded five-minute METAR, or a reading eligible for correction is not automatically a guaranteed settlement lower bound.

NWS explains that ASOS observations, rounded disseminated METARs, and official daily extrema can differ. Reporting intervals can miss an extreme, and conversion through whole Celsius can alter the Fahrenheit values displayed. [S53]

Maintain a high-confidence observation path and explicit flags for unresolved discrepancies. The system should widen uncertainty or suspend execution when the target feed is unavailable or its rule interpretation is unresolved. A fallback source should be a separately handled target, not an invisible replacement inside the feature pipeline.

## 9 Data required for a credible research system

### Observations and labels

Use source-matched final outcomes for settlement research, alongside meteorological observations for physical modeling. NCEI provides ASOS archives at hourly and finer frequencies and station metadata resources. GHCN-Daily provides historical daily elements; its observation periods and source mix require checking before treating it as a contract label. [S54–S56]

| Dataset category | Minimum fields to retain | Why it is needed |
|---|---|---|
| Station registry | IDs, coordinates, elevation, timezone, provider mappings, effective dates | Prevents incorrect station joins |
| Raw observations | Native value and units, observation time, receipt time, report type, QC flags, source | Reconstructs what was known and eligible |
| Official extrema | Value, observation window, publication and revision times | Weather-model evaluation |
| Settlement labels | Market, rules version, source used, eligible max, winning bucket, decision time | Contract-specific evaluation |
| Forecast runs | Model and version, initialization, availability, valid time, member, parameter, interval | Reproducible input history |
| Local features | Clouds, winds, pressure, radiation, soil, regime, derived timestamps | Explains conditional temperature error |
| Model predictions | Mean or median, CDF or quantiles, bucket probabilities, model version, issue time | Calibration and audit |
| Market data | Bid, ask, depth, trades, receipt time, fees, tick sizes | Executable economic evaluation |

Keep observation time and receipt time separate. A late report describes past weather but was unavailable to an earlier trader. Keep source revisions as new versions rather than overwriting the original record.

Station metadata matter because equipment, reporting methods, sites, and observation times can change. NCEI's HOMR tracks these histories. [S55]

### Historical forecasts and reforecasts

A forecast archive stores outputs as originally produced. A reforecast runs a fixed model retrospectively to create a consistent training dataset. A reanalysis reconstructs past atmospheric conditions using a model and observations. They are related but serve different research purposes.

NOAA publishes GEFS reforecast resources. These can support bias and probability calibration, subject to version and availability checks. [S57]

ERA5 is a historical reanalysis, not an archive of forecasts available to a trader at a past issue time. Copernicus describes its reconstruction of past conditions and delayed publication. [S58]

Use reanalysis for physical study, climatological context, and model development. For a historical trading test, features must have been available at the prediction timestamp. A final ERA5 field for tomorrow's weather cannot be an input to yesterday's simulated trade. A reforecast test can assess forecasting potential, but its execution and latency assumptions must be distinguished from an as-issued trading replay.

### Proprietary data

Public weather readings become more useful when linked to the forecast vintages, exact source definition, probabilities, executable prices, and final outcomes. The potentially differentiated asset is the organized, validated archive and learned error structure. Merely accumulating public data does not make the underlying measurements exclusive.

An especially valuable record is a paired dataset: what each model predicted at each issue time, how the station path developed, what the settlement source ultimately recorded, and what market prices were executable before the information became common knowledge.

## 10 What accuracy should mean

There is no meaningful universal statement that a model predicts weather with 60% accuracy. Specify the event, station, lead, valid window, and metric.

| Metric | Interpretation | Limitation |
|---|---|---|
| MAE in °C or °F | Typical absolute point error | Does not measure probabilities |
| RMSE | Point error with extra weight on large misses | Can hide calibration and direction |
| Bias | Average signed error | Errors can cancel |
| Within 1°C | Fraction within a tolerance | Different from exact-degree accuracy |
| Exact published integer | Fraction matching the source's integer high | Sensitive to rounding and target definition |
| Exact bucket hit rate | Fraction where the highest-probability bucket wins | Depends on bucket width and issue time |
| CRPS | Distribution error over the whole temperature range | Needs a correctly defined continuous or discrete target |
| Brier score | Error in a specified event probability | One event does not describe all outcomes |
| Multiclass log loss | Probability assigned to the realized bucket | Strongly penalizes overconfidence |
| Coverage and interval width | Reliability and sharpness of intervals | Broad unconditional coverage can conceal regime failures |
| Net trading outcome | Economic result after execution costs | Can be noisy and does not isolate forecasting skill |

ECMWF's verification guidance distinguishes accuracy, skill against a reference, and economic utility, with reliability central to probability assessment. [S59]

### Why narrow buckets are demanding

For an illustrative Gaussian forecast whose mean is exactly at a bucket centre, a one-degree Celsius interval contains

\[
P(|Y-\mu|<0.5)=2\Phi(0.5/\sigma)-1.
\]

| Forecast standard deviation in °C | Probability in a centred 1°C interval | Probability in a centred 2°F interval |
|---|---|---|
| 0.50 | 68.3% | 73.3% |
| 0.75 | 49.5% | 54.1% |
| 1.00 | 38.3% | 42.1% |
| 1.50 | 26.1% | 28.9% |
| 2.00 | 19.7% | 21.9% |

These are calculated illustrations, not observed forecast scores. They assume a Gaussian distribution, centred intervals, and a continuous temperature target. Published rounded-temperature settlement can require a different transformation. The calculation shows why an error scale that looks small for a normal forecast can still distribute probability across several trading buckets.

For the one-degree illustration, obtaining 60% probability in the centred interval requires a standard deviation around 0.59°C. That is not a promised operational error level. Miscentering, systematic bias, multimodality, and measurement behavior change the result.

### Calibration is a measurable statement

If a model repeatedly gives an outcome 70% probability, that outcome should occur approximately 70% of the time over comparable predictions. Reliability should be checked by lead, station, season, regime, and probability range, with enough samples and honest uncertainty intervals.

A high NO win rate against rare buckets can be achieved by identifying obviously unlikely outcomes. It does not prove positive expected value: the NO price may already be too high. Conversely, a forecast can make money at a modest hit rate when the successful outcomes pay enough relative to their purchase prices.

## 11 Validation that can support a trading decision

### Separate weather and trading evaluations

First verify that the meteorological model improves station forecasts. Then verify that settlement probabilities improve over appropriate baselines. Finally, test execution on time-matched prices and depth. Combining everything into one P&L number makes it difficult to distinguish a weather problem from a target, pricing, or execution problem.

Recommended forecast baselines are persistence, seasonal climatology, official guidance, raw global and local models, a simple station correction, and a calibrated ensemble. Market probabilities are an additional comparison when time-matched and converted from executable prices carefully.

### Chronological testing

1. Define prediction schedules and targets before fitting.
2. Train on an initial period and tune on a later validation period.
3. Lock the choice of features, model, calibration, and decision rule.
4. Evaluate on untouched future dates.
5. Advance the training window and repeat under a documented schedule.
6. Run a prospective paper test using inputs archived at actual receipt times.

For daily outcomes, split by date or weather episode rather than randomly splitting intraday rows. Many snapshots of the same day share one final outcome. Nearby cities can share the same air mass. Use block-based uncertainty estimates that respect this dependence.

Evaluate different leads explicitly: previous evening, morning, near the likely peak, and after it. Define them by station-local times or hours until the target window closes; “D0” by itself combines very different tasks.

### Recommended diagnostics

- Station and lead tables for MAE, bias, CRPS, log loss, and interval coverage.
- Reliability curves for each bucket or threshold, with sample counts.
- PIT or discrete/randomized PIT diagnostics appropriate to the target.
- Performance in clear, cloudy, frontal, marine, wet, snow, and extreme-temperature cases.
- Missed-peak timing and observation-sampling diagnostics.
- Differences between physical-high predictions and source-table predictions.
- Incremental value of each forecast family and observation feature.
- Performance before and after upstream model upgrades.
- Execution results at realistic sizes, with fees and slippage.

A 60% hit rate from 100 independent events has an approximate 95% sampling uncertainty of about ±10 percentage points. Around 400 independent events reduces that approximation to roughly ±5 points. Weather dependence reduces effective sample size. Thousands of intraday rows do not establish thousands of independent cases.

### Leakage to exclude

Common examples include final revised observations in an earlier live decision, model runs downloaded after the simulated trade, source changes applied retrospectively, future cloud observations, final reanalysis fields, backfilled labels joined to incorrect local dates, and city selection optimized on the test period.

Tuning the profit threshold or strategy after seeing test P&L is also leakage. Retain a final forward evaluation after all changes. Report selected-trade coverage so a high hit rate from a tiny hand-picked subset is not presented as all-city predictive performance.

## 12 Turning forecasts into Polymarket probabilities

### Outcome probabilities

For compatible continuous bounds,

\[
p_k=P(a_k\leq Y<b_k)=F(b_k)-F(a_k).
\]

For rounded or sampled settlement, calculate probabilities through the source and contract transformation instead. In general,

\[
p_k=P\!\left(g(\text{observation path},\text{source state},\text{rules})=k\right).
\]

Monte Carlo simulation of coherent station paths and eligible reports is one proposed way to approximate this. A direct model for the published high is another. Do not assume that every one-degree bucket is exactly the half-degree rounding interval around its displayed number without verifying the source conversion and reporting rules.

If outcomes are mutually exclusive and collectively exhaustive, their model probabilities should sum to one. Check that this property follows from the actual event structure. Tail buckets should include all eligible tail values; do not discard extreme possibilities and renormalize without justification.

### Expected value and execution

For a YES share bought at executable price \(q\), with probability \(p\) of a one-dollar payout,

\[
\mathbb E[\mathrm{profit\ per\ share}]=p-q-c,
\]

where \(c\) is the expected all-in per-share cost under the execution scenario. For the complementary NO outcome use \(1-p\) and its own executable price and costs. This assumes the described binary payoff and holding to resolution.

The implementation should use size-weighted execution prices, actual applicable fee parameters, and realistic fills. A displayed percentage or stale last trade is not necessarily a price at which the desired position can be bought. A posted limit order is not a completed trade.

Example, purely illustrative: a model gives a bucket 42% probability; buying YES at 34 cents with one cent of all-in costs implies seven cents of modeled expected profit per share. If the true probability is only 33%, the trade has negative expected value. Model uncertainty can therefore exceed the apparent pricing edge.

Use conservative probability estimates and paper-tested admission rules rather than automatically trading every small model-price difference. Aggregate risk by event and weather episode. Several NO positions on one mutually exclusive set are not independent investments.

### Where an advantage could arise

Potentially testable sources include better local correction, correct interpretation of the settlement source, faster response to a relevant observation or cloud change, more reliable probabilities during a specific regime, and execution discipline. Each is a hypothesis until demonstrated after costs.

The simplest forecast consensus is already accessible to other participants. A narrow probability distribution and an impressive chart do not establish an advantage. Measure whether the application improves prediction and obtains favorable executable prices at the same timestamps.

## 13 Weather derivatives and energy futures

### Weather derivatives

CME US degree-day contracts use the mean of daily maximum and minimum temperatures recorded over the specified local-standard-time window. The reviewed rules define a monthly accumulation, contract stations, and settlement processing. [S34]

Using the contract's required Fahrenheit temperatures,

\[
\overline T_d=(T_{\max,d}+T_{\min,d})/2,
\]

\[
\mathrm{HDD}_d=\max(65-\overline T_d,0),\qquad
\mathrm{CDD}_d=\max(\overline T_d-65,0).
\]

European HDD and CAT products have their own definitions, station windows, and units. The reviewed European HDD rules use an 18°C base and specific observation windows, including Heathrow's different maximum and minimum periods. [S35–S36]

Forecast the index for each coherent scenario, then summarize its distribution. Do not apply the degree-day formula only to the expected daily temperature, because thresholding is nonlinear:

\[
\mathbb E[\max(X-b,0)]\ne\max(\mathbb E[X]-b,0)
\]

in general. Keep dependence between maximum and minimum, across dates, and across locations. Monthly risk cannot be reconstructed by summing independent daily uncertainty estimates when weather episodes persist.

For a partly completed contract month, combine the observed index accumulation with a forecast for remaining dates. Beyond the reliable weather horizon, use properly calibrated seasonal distributions and their uncertainty. A seasonal anomaly forecast is not a precise daily-high forecast months in advance.

Physical expected index value alone is not a complete market valuation for every derivative. Contract payoff, risk premium, liquidity, discounting where relevant, and market conditions can affect the tradable price. Weather derivatives are not automatically priced by a uniquely hedgeable risk-neutral weather process.

### Natural gas and electricity

EIA explains HDD and CDD as indicators of heating and cooling requirements. Its analysis describes winter residential and commercial heating demand and summer electricity-related gas demand. [S37–S38]

For a regional energy application, extend the station system to forecast:

- Minimum temperatures and full hourly temperature paths.
- Degree days weighted by population, gas customers, or relevant electricity load.
- Humidity and the timing and duration of heat or cold.
- Wind and solar generation scenarios.
- Spatial coincidence of weather across demand centres.
- Weather-sensitive supply constraints where relevant.

CPC publishes population and heating-fuel-customer weighted degree-day resources. These are useful benchmarks and inputs; a trading model can estimate more appropriate weights for its target region. [S39]

Then connect weather to demand using a separate model, for example

\[
L_{r,t}=g_r(T_{r,t},T_{r,t-1:t-m},\mathrm{humidity},\mathrm{calendar},\mathrm{economic\ state})+\epsilon_{r,t}.
\]

This is a proposed modeling template, not a fitted regional relationship. Demand persistence, weekends, holidays, structural changes, electrification, and nonlinear temperature responses can matter. Validate both total load and the relevant peak or gas-demand quantities.

The price model must then incorporate demand alongside storage, production, LNG and other flows, generation availability, fuel competition, and the information already reflected in the market. EIA's storage dashboard documents weather alongside storage and other gas-market fundamentals. [S60]

### Forecast revisions and market surprise

For energy futures, a useful proposed feature is the change in expected weighted HDD or CDD between model vintages, including its spatial pattern and uncertainty. A temperature forecast above the seasonal normal can be bearish if it is less hot than the market expected, or overwhelmed by other fundamentals.

Retain both changes from the prior run and changes from a specified market-consensus proxy. Compare weather-only, fundamentals-only, and combined models on the same held-out periods. Tests should use the relevant instrument and horizon rather than assuming that daily city-high accuracy transfers to a monthly gas future.

An energy futures system therefore requires three validated relationships: weather to regional conditions, conditions to demand or supply, and those changes to tradable prices. Improving the first relationship is necessary for a weather-driven system but does not prove the other two.

## 14 Selecting cities and stations

Do not select cities solely because their historical highs vary little. Low climate variability, low forecast error, narrow conditional uncertainty, and profitable market pricing are different properties.

For a pilot, a limited set of five to ten well-observed stations is a reasonable scope recommendation. Choose it using actual data availability and historical errors, then prospectively validate the selection. This is an engineering scope choice, not a research finding that a specific ten cities are best.

| Selection factor | Evidence to collect |
|---|---|
| Target clarity | Stable station and unambiguous source and window |
| Observation quality | Timeliness, missing rate, revisions, source consistency |
| Forecastability | Held-out station CRPS, bias, bucket scores by lead and regime |
| Learnability | Enough paired forecast and outcome history |
| Market usability | Depth, spread, fees, and available position sizes |
| Distinctive local information | Measurable benefit from cloud, wind, or station features |
| Portfolio concentration | Dependence with other stations and weather episodes |

Coastal stations warrant explicit verification of grid and land-sea treatment; ECMWF documents the difficulties of coastal interpolation and strong local gradients. [S61]

Prefer a dynamic eligibility rule over permanent city rankings. A station can be suitable during settled conditions and unsuitable on a front or marine-cloud transition. A model should communicate uncertainty and reduce activity when its evidence is weak.

## 15 Recommended system design

The following design is a proposed architecture for this use case, not a statement about the current ArbDesk4 implementation.

| Layer | Function | Reviewable output |
|---|---|---|
| Target registry | Defines station, window, source, units, and contract rules | Versioned target definition |
| Data archive | Records forecasts, observations, receipts, revisions, and markets | Reproducible as-of history |
| Feature processing | Aligns windows, units, stations, and forecast versions | Validated feature rows and data flags |
| Meteorological models | Produce station distributions and coherent paths | Temperature CDFs, quantiles, peak times |
| Same-day updates | Conditions remaining warming on observations | Running-high and remaining-high distributions |
| Settlement adapter | Applies source and contract behavior | Bucket probabilities and unresolved-rule flags |
| Energy adapter | Produces max/min paths, weighted indices, and demand scenarios | Weather index and demand distributions |
| Decision model | Compares modeled value with executable prices | Cost-adjusted opportunities and abstentions |
| Evaluation | Measures errors, reliability, economics, and drift | Versioned prospective results |

Start with two weather models: a transparent calibrated baseline and one nonlinear challenger. Additional upstream forecasts must demonstrate incremental value. Keep the price decision separate so changes to execution do not silently redefine the meteorological forecast.

A useful user interface should show the exact station and date, target definition, latest eligible observation, running high, forecast median, probability interval, bucket probabilities, expected peak window, key conditional risks, input age, and historical reliability for that lead. The energy view should instead emphasize weighted degree-day revisions, scenario demand, and relevant fundamentals.

The forecast should be reproducible from its model version and archived input IDs. Explain which features affected the forecast, while avoiding unsupported certainty from narrative explanations.

## 16 Research and development sequence

Progress by evidence gates rather than by how many methods have been added.

1. **Target and data gate.** Build an audited station and contract registry. Demonstrate correct date, units, source precision, valid windows, and as-of joins on sample days, including source failures.
2. **Baseline gate.** Assemble forecast-observation pairs and compute matching station and settlement scores for existing guidance. Identify systematic local errors and the leads worth studying.
3. **Calibration gate.** Fit regularized bias correction and EMOS, then test on untouched dates. Demonstrate improvements in proper probability scores without unacceptable coverage failures.
4. **Nonlinear gate.** Add QRF or distributional boosting, and compare with the calibrated baseline. Use feature ablations and blocked uncertainty estimates.
5. **Same-day gate.** Model remaining warming and source-eligible observations. Verify the probability mass at the existing high and behavior across cloud or wind transitions.
6. **Settlement gate.** Replay actual source rules, fallback paths, and final outcomes. Demonstrate that bucket scores correspond to the contract target.
7. **Execution gate.** Run a prospective paper experiment with realistic bids, asks, depth, fees, and fill assumptions. Lock decision rules before judging the result.
8. **Energy gate.** Add minimum temperatures, regional scenarios, degree days, demand, and fundamentals. Validate each link to the intended futures instrument.

An initial short paper period is useful for operational failure discovery; it is not enough to establish year-round predictive quality. Rare regimes and extremes require longer history and explicit uncertainty.

For ArbDesk4 or WXPredict, this sequence provides a model specification and an audit checklist. It does not imply that replacing the current engine is justified without comparing its actual inputs, targets, outputs, and prospective scores.

## 17 What can realistically be differentiated

The viable differentiation hypothesis is specialized station and settlement modeling, backed by a carefully timestamped archive and honest evaluation. A small team can consume world-class public atmospheric forecasts and focus on the last mile: local error, relevant observations, target interpretation, calibrated uncertainty, and execution.

The research does not establish that a small team can consistently beat all major forecast providers, or achieve 60% exact-bucket accuracy across every city and issue time. It does establish credible methods to test a narrower claim.

Define a claim such as: “At these stations and specified issue times, our probabilities improve on the chosen baseline in a prospective sample, with these costs and this uncertainty.” That is more informative than an overall accuracy badge.

Public forecasts and public sensor data are widely accessible. Durable value is more likely to come from clean linked histories, reliable operation, demonstrated conditional calibration, and a usable workflow than from merely listing many strategies or collecting many API readings.

## 18 Evidence strength and limitations

| Conclusion | Evidence strength | Limit |
|---|---|---|
| Numerical models plus calibrated postprocessing are an established forecasting approach | Strong published and operational evidence | Exact implementation must match target |
| Live observations can inform short-range forecasts | Strong operational basis, including LAMP | Amount of improvement is station and lead dependent |
| Narrow temperature buckets require distributional forecasting | Mathematical and decision-theoretic basis | Accuracy depends on measurement and target behavior |
| Current example Polymarket contracts use NOAA and named stations | Direct contract evidence | Other markets and future rule versions can differ |
| Some current weather markets have trading fees | Direct platform documentation | Obtain applicable market-specific values at execution |
| Maximum temperature alone is insufficient for a complete energy trading model | Direct index definitions and energy-demand evidence | Contribution to price prediction requires empirical testing |
| A specialized application could outperform a baseline | Supported research hypothesis | Not established for these stations without testing |
| A particular engine is profitable or institutionally validated | Not established by this review | Requires its own prospective results, audit, and deployment evidence |

Search results included old versions, failed page opens, and AI-generated market narratives. Current official notices and explicit contract rules were preferred where they contradicted older descriptions. Market-context narratives were not treated as scientific forecast evidence. No precision accuracy or return estimate is inferred from generic model benchmarks.

## Sources and reading guide

All sources below were consulted through web retrieval on 7 October 2026. Publication dates are given where useful. Operational sources are subject to changes; archive the effective version used in research or execution.

### Contracts and fees

- **S1** [Polymarket London 7 October 2026 market rules](https://polymarket.com/event/highest-temperature-in-london-on-october-7-2026). Direct example of station, NOAA source, precision, fallback, and revision behavior.
- **S2** [Polymarket NYC 7 October 2026 market rules](https://polymarket.com/event/highest-temperature-in-nyc-on-october-7-2026). Direct example specifying LaGuardia and hourly source data.
- **S3** [Polymarket Seoul 8 October 2026 market rules](https://polymarket.com/event/highest-temperature-in-seoul-on-october-8-2026). Direct example using Incheon International.
- **S4** [Polymarket fee schedule](https://polymarket.com/fees). Current weather category appears in the schedule.
- **S5** [Polymarket Trading Fees help article](https://help.polymarket.com/en/articles/13364478-trading-fees). Fee framework and distinction between maker and taker treatment.

### Physics and operational foundations

- **S6** [NOAA The Earth Atmosphere Energy Balance](https://www.noaa.gov/jetstream/atmosphere/energy). Surface and atmospheric heating context.
- **S7** [ECMWF Use of super site observations to evaluate near surface temperature forecasts](https://www.ecmwf.int/en/newsletter/161/meteorology/use-super-site-observations-evaluate-near-surface-temperature). Physical sources of station temperature error, 2019.
- **S8** [ECMWF Data assimilation](https://www.ecmwf.int/en/research/data-assimilation). Assimilation purpose and operational cycle.
- **S9** [ECMWF Earth system data assimilation fact sheet](https://www.ecmwf.int/en/about/media-centre/focus/2020/fact-sheet-earth-system-data-assimilation). Why observations and forecasts are combined.
- **S10** [ECMWF IFS documentation](https://www.ecmwf.int/en/publications/ifs-documentation). Current model documentation by scientific component.
- **S11** [ECMWF Assimilation and modelling of two metre temperature](https://confluence.ecmwf.int/spaces/FUG/pages/673550482/Section+2A.1.4.9+Assimilation+and+modelling+2m+temperature). Diagnostic temperature and stability treatment.
- **S12** [ECMWF Quantifying forecast uncertainty](https://www.ecmwf.int/en/research/modelling-and-prediction/quantifying-forecast-uncertainty). Ensemble rationale. Some configuration details on this page predate later upgrades.
- **S13** [ECMWF Significant IFS and AIFS upgrade goes live](https://www.ecmwf.int/en/about/media-centre/news/2026/ifs-cycle-50r1-aifsv2-live). Operational upgrade dated 12 May 2026.
- **S14** [ECMWF Open data](https://www.ecmwf.int/en/forecasts/datasets/open-data). Current access, variables, resolution, forecast steps, and retention.

### Statistical forecasting research

- **S15** Gneiting, Raftery, Westveld and Goldman, 2005. [Calibrated Probabilistic Forecasting Using Ensemble Model Output Statistics and Minimum CRPS Estimation](https://journals.ametsoc.org/view/journals/mwre/133/5/mwr2904.1.xml). DOI 10.1175/MWR2904.1.
- **S16** [University of Washington EMOS research summary](https://stat.uw.edu/research/tech-reports/calibrated-probabilistic-forecasting-using-ensemble-model-output-statistics-and-minimum-crps). Accessible original-author summary.
- **S17** Raftery, Gneiting, Balabdaoui and Polakowski, 2005. [Using Bayesian Model Averaging to Calibrate Forecast Ensembles](https://journals.ametsoc.org/view/journals/mwre/133/5/mwr2906.1.xml). DOI 10.1175/MWR2906.1.
- **S18** Taillardat, Mestre, Zamo and Naveau, 2016. [Météo France record for Calibrated Ensemble Forecasts Using Quantile Regression Forests and EMOS](https://bibliotheque.meteo.fr/pub/DOC00036367-calibrated-ensemble-forecasts-using-quantile-regre.html). DOI 10.1175/MWR-D-15-0260.1.
- **S19** [Nonhomogeneous Boosting for Predictor Selection in Ensemble Postprocessing](https://journals.ametsoc.org/view/journals/mwre/145/1/mwr-d-16-0088.1.xml), 2017. Direct relevance to minimum and maximum temperatures.
- **S20** [Postprocessing of Ensemble Weather Forecast Using Decision Tree Based Probabilistic Forecasting Methods](https://journals.ametsoc.org/view/journals/wefo/38/1/WAF-D-22-0006.1.xml), 2023. Tree-based methods for hourly temperature.
- **S21** Rasp and Lerch, 2018. [Neural networks for postprocessing ensemble weather forecasts](https://arxiv.org/abs/1805.09091). Original-author preprint; DOI 10.1175/MWR-D-18-0187.1.
- **S22** [Rasp and Lerch accompanying research implementation](https://github.com/slerch/ppnn). Author-maintained code and description.
- **S23** Delle Monache and colleagues, 2013. [Probabilistic Weather Prediction with an Analog Ensemble](https://journals.ametsoc.org/doi/pdf/10.1175/MWR-D-12-00281.1). Analog definition and probabilistic forecast approach.

### Operational station and regional systems

- **S24** [NOAA Model Output Statistics](https://vlab.noaa.gov/web/mdl/mos). Official MOS description.
- **S25** [NOAA NBM Weather Elements](https://vlab.noaa.gov/web/mdl/nbm-weather-elements). Temperature products, bias correction, and valid windows.
- **S26** [NOAA NBM Versions](https://vlab.noaa.gov/web/mdl/nbm-versions). Version 5.0 and July 2026 temperature update.
- **S27** [NOAA Localized Aviation MOS Program](https://vlab.noaa.gov/web/mdl/lamp). Current LAMP description and station temperature guidance.
- **S28** [NOAA HRRR](https://emc.ncep.noaa.gov/emc/pages/numerical_forecast_systems/hrrr.php). Model frequency, resolution, and forecast horizons; future-system projections on this page can be outdated.
- **S29** [Met Office ensemble system](https://www.metoffice.gov.uk/research/weather/ensemble-forecasting/mogreps). Regional and global ensemble description.
- **S30** [Met Office numerical weather prediction models](https://www.metoffice.gov.uk/research/approach/modelling-systems/unified-model/weather-forecasting). Deterministic and ensemble configurations.
- **S31** [DWD ICON D2 database documentation](https://www.dwd.de/SharedDocs/downloads/DE/modelldokumentationen/nwv/icon_d2/icon_d2_dbbeschr_aktuell.pdf?nn=344870&view=nasPublication). Regional domain and grid definition. [Public ensemble temperature directory](https://opendata.dwd.de/weather/nwp/icon-d2-eps/grib/03/t_2m/).
- **S32** [Environment and Climate Change Canada GEPS open data documentation](https://eccc-msc.github.io/open-data/msc-data/nwp_geps/readme_geps_en/). Global ensemble and access description.
- **S33** [NWS notification index](https://www.weather.gov/notification/). SCN 26-48 updated 2 October 2026 schedules RRFS and REFS for 3 November 2026; use dated notices rather than legacy projected dates.

### Energy and joint probability

- **S34** [Current CME Rulebook Chapter 403](https://www.cmegroup.com/rulebook/CME/IV/400/403/403.pdf). US degree-day definitions, stations, windows, and monthly accumulation.
- **S35** [Current CME Rulebook Chapter 406](https://www.cmegroup.com/content/dam/cmegroup/rulebook/CME/IV/400/406/406.pdf). European HDD stations, UTC windows, and Celsius base.
- **S36** [CME Temperature Based Indexes](https://www.cmegroup.com/trading/weather/temperature-based-indexes.html). HDD, CDD, and CAT overview.
- **S37** [EIA Degree days](https://www.eia.gov/energyexplained/units-and-calculators/degree-days.php). Heating and cooling calculations and interpretation.
- **S38** [EIA US natural gas consumption has winter and summer peaks](https://www.eia.gov/TODAYINENERGY/detail.php?id=42815). Weather channels affecting demand; historical quantitative examples are not current estimates.
- **S39** [NOAA CPC Degree Days Statistics](https://cpc.ncep.noaa.gov/products/analysis_monitoring/cdus/degree_days/index.shtml). Population and heating-fuel-customer weighting and data resources.
- **S40** Schefzik, Thorarinsdottir and Gneiting, 2013. [Uncertainty Quantification in Complex Simulation Models Using Ensemble Copula Coupling](https://arxiv.org/abs/1302.7149). Temporal, spatial, and cross-variable dependence.
- **S41** Barber, Candès, Ramdas and Tibshirani. [Conformal prediction beyond exchangeability](https://arxiv.org/abs/2202.13415). Coverage assumptions and distribution drift.

### AI systems and data engineering

- **S42** Price and colleagues, 2024. [Probabilistic weather forecasting with machine learning](https://doi.org/10.1038/s41586-024-08252-9). GenCast peer-reviewed research and benchmark definition.
- **S43** Lam and colleagues, 2023. [Learning skillful medium range global weather forecasting](https://pubmed.ncbi.nlm.nih.gov/37962497/). Original GraphCast paper metadata and abstract; DOI 10.1126/science.adi2336.
- **S44** Bodnar and colleagues, 2025. [A foundation model for the Earth system](https://www.nature.com/articles/s41586-025-09005-y). Aurora peer-reviewed research.
- **S45** [Aurora documentation](https://microsoft.github.io/aurora/). Current variants and implementation entry point.
- **S46** [KMA LDAPS archive](https://data.kma.go.kr/data/rmt/rmtList.do?code=340&pgmNo=65). Legacy regional product description and March 2026 UM provision notice.
- **S47** [ECMWF October 2025 open data access explanation](https://confluence.ecmwf.int/spaces/DAC/pages/514772476/What+does+Open+Data+in+October+2025+mean+for+me). Distinguishes licence and information costs from delivery service charges.
- **S48** [NWS API Web Service](https://www.weather.gov/documentation/services-web-api). Forecast and observation API access and rate-limit guidance.
- **S49** [NWS gridpoint API FAQ](https://weather-gov.github.io/api/gridpoints). Forecast-grid mapping and numerical layers.
- **S50** [NOAA repository Comparing and Combining Deterministic Surface Temperature Postprocessing Methods over the United States](https://repository.library.noaa.gov/view/noaa/45318), 2021. Correction comparisons using GEFS reforecasts and ERA5 verification.
- **S51** [ECMWF parameter 121](https://codes.ecmwf.int/grib/param-db/121). Six-hour maximum two-metre temperature definition.
- **S52** [Met Office What does this forecast mean](https://weather.metoffice.gov.uk/guides/what-does-this-forecast-mean). Daytime and nighttime definitions and sampled versus maximum temperature.
- **S53** [NWS Weather Observations and Climate Products FAQ](https://www.weather.gov/lot/weather_observations_faq). ASOS averaging, rounding, reporting intervals, and local-standard-time climate windows.
- **S54** [NCEI Automated Surface Weather Observing Systems](https://www.ncei.noaa.gov/products/land-based-station/automated-surface-weather-observing-systems). Hourly and finer observation archives.
- **S55** [NCEI Historical Observing Metadata Repository](https://www.ncei.noaa.gov/access/homr/). Station histories, observation times, equipment, and locations.
- **S56** [NCEI GHCN Daily documentation](https://www.ncei.noaa.gov/pub/data/cdo/documentation/GHCND_documentation.pdf). Daily observation elements and dataset scope.
- **S57** [NOAA GEFS reanalysis and reforecast resources](https://psl.noaa.gov/news/2022/042122a.html). Fixed-model retrospective forecast resources and scientific references.
- **S58** [Copernicus Climate reanalysis](https://climate.copernicus.eu/climate-reanalysis). ERA5 purpose, coverage, and publication delay.
- **S59** [ECMWF Statistical Concepts for Probabilistic Data](https://confluence.ecmwf.int/pages/viewpage.action?pageId=240849388). Reliability, skill, and economic utility.
- **S60** [EIA Natural Gas Storage Dashboard notes and sources](https://www.eia.gov/naturalgas/storage/dashboard-content/notes_sources.php). Weather weighting alongside storage and other market fundamentals.
- **S61** [ECMWF Interpolating Land and Sea Points](https://confluence.ecmwf.int/spaces/FUG/pages/673550684/Section+3.4.1+Interpolating+Land+and+Sea+Points). Coastal grid interpolation and local gradients.

### Suggested reading order

Begin with the actual intended contract and station sources, then S24–S27 for established station forecasting, S15–S21 for probabilistic methods, and S53 for observation behavior. For energy work, read S34–S39 before choosing forecasting targets. Use S13–S14 and S33 to keep operational ingestion aligned with current model versions.
