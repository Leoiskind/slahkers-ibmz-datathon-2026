# Granite report: human review (area, morning peak)

**Model:** IBM Granite 3.3 2B Instruct, big-endian GGUF (`granite-3.3-2b-instruct-be.Q4_K_M.gguf`), via `llama-cpp-python` on IBM LinuxONE (s390x, 3 CPU cores). Generation time: 734.6 s, 133 words, 1 attempt.
**Automatic number check:** passed (every number in the text appears in the input data).
**Human review:** **failed and corrected.** The number check cannot catch errors in the *meaning* of sentences.

## 1. Granite draft (unedited)

> In Greater Manchester, during peak morning hours, trains experienced a failure rate of 30% and a late rate of 25%, with cancellations accounting for 5%. These rates are associated with the stations, with Levenshulme, Patricroft, and Flixton showing higher-than-expected failure rates, while Ashton-under-Lyne, Hyde North, and Gorton performed better than expected. The adjusted test, which accounts for network position, suggests a slightly weaker correlation between deprivation and reliability, with rho values of -0.14 (95% CI: -0.37 to 0.08). However, the raw test indicates a weaker correlation, at -0.12 (95% CI: -0.35 to 0.12). The most deprived third had a failure rate of 30%, slightly higher than the least deprived third's 32%. Despite these figures, no clear evidence of unequal service is apparent, as the data serves as a tool for ongoing, station-specific checks.

## 2. Problems found in review

| # | Issue | Severity |
|---|---|---|
| 1 | "30%, slightly **higher** than … 32%": the comparison is **reversed** (30% is lower). | Critical: inverts the headline comparison |
| 2 | Calls −0.14 "weaker" and −0.12 "weaker"; contradictory, and −0.14 is slightly stronger. Both intervals include zero. | Major |
| 3 | "These rates are associated with the stations": meaningless. | Minor |
| 4 | Conclusion muddled ("the data serves as a tool"). | Minor |
| 5 | Required caveats omitted (HSP-recorded trains only, 10 weekdays, Metrolink/buses excluded, associations not causes). | Major |

## 3. Published version (human-corrected)

> In Greater Manchester's morning peak, 30% of the trains we analysed failed: 25% arrived 5 or more minutes late and 5% were cancelled. Levenshulme, Patricroft and Flixton did worse than their position on the network would predict, while Ashton-under-Lyne, Hyde North and Gorton did better. Stations serving the most deprived third of areas had a failure rate of 30%, slightly lower than the 32% for the least deprived third. Neither the raw test (rho −0.12, 95% CI −0.35 to 0.12) nor the test adjusted for network position (rho −0.14, 95% CI −0.37 to 0.08) shows a clear relationship between deprivation and reliability. No clear evidence of unequal service in this sample; here's a tool to keep checking, station by station. These are associations, not proof of cause, based on 10 weekdays (5 to 16 October 2026) of trains recorded in National Rail's Historical Service Performance data; Metrolink trams and buses are not included.

## 4. What we learned
- A number check is necessary but not sufficient: a small model can use only correct numbers and still state the comparison backwards.
- Every Granite report is therefore a **draft**; a person checks direction words ("higher", "lower", "stronger", "weaker") against the data before publication.
- Next step: an automatic **direction check** comparing "higher/lower" claims with the input values, and a larger instruct model when available.
