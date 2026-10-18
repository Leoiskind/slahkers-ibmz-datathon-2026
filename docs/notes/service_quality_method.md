# Service Quality per Station: Hub Method

**Project:** Transport Inequality Auditor (The SLahkers, IBM Z Datathon 2026)
**Status:** agreed method (17 October 2026). Companion to `deprivation_method.md`.
**Purpose:** give each sampled Greater Manchester station a measure of how reliably the railway serves its passengers, to compare against the deprivation of the area around it.


> **As built:** the data came from **National Rail HSP** (5–16 October 2026), not Realtime Trains; RTT was used only to validate it. HSP omits about 28% of timetabled trains. Trains it didn't record are left out (an RTT check showed most of them ran), and cancellations are inferred from `late_canc_reason` or a lack of actual times (`src/journeys.py`). All Greater Manchester stations were analysed, not 20–30. The position model is **logistic regression with cross-validation grouped by station** (see `model_method.md`), not XGBoost with a date split. Per-station measures and intervals are computed in `src/evidence.py`.

---

## 1. Summary

We measure how reliably trains get people **from each station into Manchester city centre in the morning, and back home in the evening**.

- **Morning:** how late trains arrive at the **first city-centre station** they reach after leaving the origin.
- **Evening:** how late trains leaving the city centre arrive back at the **origin station**.
- **Main measure:** **morning failure rate**, the share of scheduled trains that were 5 or more minutes late at the city centre, or cancelled.

> **The question, in one sentence (for the README and video):** "How reliably does the railway get people from this neighbourhood into Manchester city centre, and back home again?"

---

## 2. Why we chose the hub method

| Reason | Explanation |
|---|---|
| **Measures the whole journey** | A train can leave a station on time and still reach Manchester 15 minutes late. Measuring at the city centre captures the delay passengers actually experience getting to work. |
| **Fair comparison** | Every station is judged against the same end point, the city centre, rather than different things at different places. |
| **Matches official definitions** | DfT and ORR define the morning peak by **arrivals into city centres between 07:00 and 09:59**. |
| **Enables journey-length normalisation** | Having a defined journey allows "delay per 30 minutes of journey", so long journeys don't look worse just for being long. |
| **Fits the network** | Greater Manchester's rail network is radial: most lines run into the city centre, which is the region's main rail destination. |

### Alternatives considered and rejected

| Alternative | Why not chosen |
|---|---|
| **Departures from each station** | Destination-neutral and simplest, but ignores delay picked up after departure. Stations where trains **start** their journey look artificially punctual, which could flatter outer (often more deprived) stations and hide the inequality we're testing for. |
| **Arrivals at each station** | Stations where trains **end** their journey show accumulated delay, so they look worse partly because of geography. |
| **Morning departures + evening arrivals at the station** | Mixes two event types and inherits both biases above. |
| **All departures and arrivals at the station** | Balanced, but harder to explain, and double counts trains. |
| **Destination-weighted (ORR origin–destination matrix)** | Most realistic, but needs a large extra dataset and per-pair train data; not feasible in the time available. |
| **Hubs in several cities** | Brings back complexity across multiple city regions; out of scope for this proof of concept. |

### Known limitation of the hub method
Not every rail trip in Greater Manchester goes to the city centre (e.g. Salford Quays, the Airport, Stockport, Bolton, other cities). We define our question as **access to the city centre**, the region's main destination, and say so explicitly. We don't claim it represents every journey.

---

## 3. Definitions

### Hub stations
| CRS | Station |
|---|---|
| MAN | Manchester Piccadilly |
| MCO | Manchester Oxford Road |
| MCV | Manchester Victoria |
| DGT | Deansgate |

Hub stations are never sampled as origins.

### "First hub" and "last hub"
Many trains call at more than one city-centre station (e.g. Deansgate → Oxford Road → Piccadilly).
- **Morning:** a train's hub is the **first** of the four it reaches **after** leaving the origin. Only that stop counts, so there's no double counting.
- **Evening:** a train's hub is the **last** of the four it calls at **before** reaching the origin.
- A train that never calls at any hub is **excluded** (it isn't a city-centre journey).

The hub is decided **per train**, not fixed per station, so stations served by more than one line are handled correctly.

### Peak windows (scheduled public timetable times, UK local time)
| Peak | Train selected if… | Lateness measured at |
|---|---|---|
| **Morning** | Scheduled **arrival at its first hub** is **07:00–09:59** | Arrival at the first hub |
| **Evening** | Scheduled **departure from its last hub** is **16:00–18:59** | Arrival at the origin (home) |

- **Weekdays only**, excluding bank holidays.
- Use **public timetable times** (HSP `gbtt_pta`/`gbtt_ptd`; RTT `scheduleAdvertised`), not internal working timetable times.
- **Planned cancellations** made in advance are excluded but counted.

### Outcome for each train
| Outcome | Rule |
|---|---|
| **Cancelled** | Cancelled outright; **or skipped the origin** (a scheduled call that didn't happen); **or** (morning) terminated before reaching any hub; **or** (evening) terminated before reaching the origin |
| **Late** | Ran, and **≥ 5 minutes** late at the measuring stop |
| **On time** | Ran, and **< 5 minutes** late at the measuring stop |
| **Missing** | No actual time and no cancellation flag; **excluded from rates and counted** |

`lateness_min` = actual minus scheduled, in minutes; **early arrivals count as 0**.

Skipped stops and early terminations count as cancellations **for that station**. This captures passengers being stranded, which can particularly affect end-of-line stations when trains are cut short to recover the timetable.

---

## 4. Per-station measures (calculated separately for each peak)

| Column | Definition | Role |
|---|---|---|
| `failure_rate` | (late + cancelled) ÷ all scheduled trains (excluding missing) | **Main measure (morning)** |
| `cancel_rate` | cancelled ÷ all scheduled | Supporting; shows being stranded |
| `late_rate` | late ÷ trains that ran | Supporting |
| `mean_delay_min` | mean `lateness_min` over trains that ran | Supporting |
| `p90_delay_min` | 90th percentile of `lateness_min` | Supporting; shows unpredictability |
| `delay_per_30` | `mean_delay_min` ÷ mean scheduled journey minutes × 30 (morning: origin → hub; evening: hub → origin) | Supporting (both peaks) |
| `n_scheduled` | number of scheduled trains | Reliability flag (flag < 30) |
| `n_missing` | trains with no actual time | Data-quality flag |

**Why failure rate is the main measure:** if late rate were the main measure, cancelled trains would disappear from the calculation, and a station whose trains are often cancelled could look *better*. Failure rate counts both ways a passenger is let down. It also mirrors the industry's own punctuality measure, in which cancellations count as failures.

### Uncertainty
- **Per-station intervals:** 95% bootstrap, **resampling service dates** (trains on the same day share causes such as weather and incidents).
- **Cross-station tests:** 95% bootstrap, **resampling stations**.

---

## 5. How the measures are used

1. **Main test (fixed before seeing any results):** Spearman correlation between `dep_score` (from `deprivation_method.md`) and **morning `failure_rate`** across sampled stations, with a bootstrap interval over stations. A positive value means more deprived areas get worse service.
2. **Position-adjusted test:** an XGBoost "expected lateness" model trained on **position-only features**. Residual = actual minus expected, averaged per station, then Spearman with `dep_score`.
   - Features: scheduled minutes from origin to hub, number of stops between origin and hub, stops the train made before the origin, time of day, day-level weather.
   - **Excluded:** deprivation (it's what we're testing) and upstream delay (it's an outcome, and could hide the very effect we're testing).
   - Split training and test data **by date**, not randomly.
3. **For communication:** compare the most and least deprived thirds of stations (difference with a bootstrap interval). This presents the main test; it isn't a separate finding.
4. **Charts:** scatter of `dep_score` (x) against morning failure rate (y), labelled by station and coloured by operator.
5. **Granite brief:** receives only precomputed numbers and caveats.

**Secondary measures** (cancellation rate, evening measures, etc.) are reported as supporting evidence, not as additional tests.

---

## 6. Station sample

- Compute `dep_score` for **every** Greater Manchester station first (no API calls).
- Choose **20–30 origin stations** spread across **deprivation fifths** and across **lines** (north, south, east, west), including a few **matched pairs** (similar journey time to the city centre, different deprivation).
- Record each station's **possible hubs** from a day of real timetable data, not from memory.
- Add more stations later if the data pull runs quickly.

---

## 7. Data collection

### Option 1: Realtime Trains API (expected route)
Base URL `https://data.rtt.io`, bearer token from https://api-portal.rtt.io/. Limits: 30 requests/minute, 750/hour, 9,000/day. History is limited per key (default 14 days; check `/api/info`).

| Peak | Requests per origin per day |
|---|---|
| Morning | `/gb-nr/location` at **each possible hub**, `filterFrom=<origin>`, window covering 07:00–09:59. Keep each train's **earliest** hub arrival, which is its first hub. |
| Evening | `/gb-nr/location` at each possible hub, `filterTo=<origin>`, 16:00–18:59 → list of trains; then `/gb-nr/location` at the **origin**, `filterFrom=<hub>`, a later window (e.g. 16:00–21:00) → arrival times. Match on train identity. |

Volume: about 3 requests per origin per day, so 25 stations × 10 weekdays ≈ 750 requests (about 1 hour).

Fields to use (verify against a real response): `scheduleAdvertised`, `realtimeActual`, `realtimeAdvertisedLateness`, `isCancelled`, `cancellationReasonCode`, `reasons`.

### Option 2: HSP (if credentials arrive)
`serviceMetrics` per origin–hub pair and peak (query a **wider time window** and filter by scheduled hub time afterwards, as HSP's time-filter semantics are unverified), then `serviceDetails` per train ID. Fields: `gbtt_pta`, `gbtt_ptd`, `actual_ta`, `actual_td`, `late_canc_reason`. Slow: cache everything and run as a background job.

### Rules for both
- **Cache every response** to `data/cache/` and skip anything already cached.
- **Make one test call first** (one origin, one hub, one day), save the raw JSON, and write the parser against it.
- **Check by hand:** how trains calling at several hubs appear, how a skipped stop appears, whether fully cancelled trains are returned at all, and how times crossing midnight are formatted.
- Every row carries a `source` column (`rtt`, `hsp` or `mock`). Mock data is never presented as a result.

---

## 8. Journey table (one row per train per origin per peak)

```
source, station_crs, hub_crs, peak, service_date, train_id, operator,
sched_time, actual_time, lateness_min, outcome, cancel_reason,
scheduled_journey_min, stops_between, stops_before, planned_cancel_flag
```

Saved as `data/processed/journeys.parquet`. Station measures (`data/processed/station_quality.parquet`) are group-bys on this table and join to `station_deprivation.parquet` on `station_crs`.

---

## 9. Config (`config.yaml`)

```yaml
hubs: [MAN, MCO, MCV, DGT]
stations:                         # 20–30, chosen across deprivation fifths and lines
  - {crs: XXX, possible_hubs: [MCV]}    # fill from real timetable data
peaks:
  am: {select_by: first_hub_arrival,  window: ["07:00", "09:59"], measure_at: first_hub}
  pm: {select_by: last_hub_departure, window: ["16:00", "18:59"], measure_at: origin}
weekdays_only: true
exclude: [bank_holidays, planned_cancellations]
late_threshold_min: 5
primary_measure: am_failure_rate  # fixed before seeing results
dates: {last_n_weekdays: 10}      # adjust to the API's history limit
min_trains_flag: 30
```

---

## 10. Caveats (state in the README and brief)

- **City-centre access only:** we measure journeys to and from Manchester city centre, not every trip people make.
- **Short time window:** about 10 weekdays (limited by API history), so results reflect recent performance only.
- **Sample of stations:** 20–30 stations, so intervals are wide; results are exploratory ("suggests", not "proves").
- **Operator and line confounding:** different lines are run by different operators on different infrastructure. With this many stations we can't fully control for it, so charts are coloured by operator and it's mentioned as a caveat.
- **Data completeness:** trains with no recorded time are excluded and counted; if fully cancelled trains are missing from the source, cancellation rates are undercounted.

---

## 11. Attribution
- Realtime Trains data: credit Realtime Trains per their terms (check before public display).
- HSP data (if used): National Rail Enquiries / Darwin, attribution required.
