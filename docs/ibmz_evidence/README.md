# IBM Z / LinuxONE evidence

Proof that the pipeline ran on an IBM LinuxONE virtual machine (s390x, big-endian), 17–18 October 2026.
Model files themselves are not committed (`models/` is gitignored); these are copies of the run records.

| File | What it shows |
|---|---|
| `morning_metadata.json`, `evening_metadata.json` | Platform `s390x` (big-endian); `nnpa_in_cpuinfo: true` (on-chip AI accelerator present); `ibm-zdnn-plugin` 2.3.0; Keras inference benchmark; the fixed main tests (logistic regression), which match the laptop (x86) run exactly |
| `granite.log` | IBM Granite 3.3 2B Instruct (big-endian GGUF) loading and generating the plain-English reports on LinuxONE via `llama-cpp-python` |
| `granite_area_morning_review.md` | Granite's unedited draft, the problems human review found (including a reversed comparison that passed the automatic number check), and the corrected published text |
| `*.png` | Screenshots: `uname -m`, `grep nnpa /proc/cpuinfo`, the model run, Granite generating |

## Results on LinuxONE (morning peak, 83 stations, 5,040 trains)

| Test (logistic regression, primary) | ρ | 95% CI |
|---|---|---|
| Raw: deprivation vs failure rate | −0.12 | −0.35 to +0.12 |
| Position-adjusted: deprivation vs residual | −0.14 | −0.37 to +0.08 |

Conclusion: **no clear evidence of unequal service in this sample.**

## Notes
- **Keras** is used to demonstrate inference with the zDNN plugin (about 125,000 predictions per second on this VM). It is *not* used for the results: its predictive accuracy on this small, mostly station-level dataset was no better than chance (AUC ≈ 0.48). We report logistic regression, as fixed in advance.
- We report inference speed only; we did not measure a CPU-only baseline, so we make no speed-up claim.
- Granite runs on 3 CPU cores at about 1 token per second, so each report takes a few minutes; this suits an overnight batch ("nightly auditor"). Every report is number-checked automatically and reviewed by a person before use.
