"""Plain-English reports written by IBM Granite (llama-cpp-python, on the LinuxONE VM).

Granite only WRITES: every number comes from data/processed/report_inputs/*.json (src.evidence).
check_numbers() rejects any report containing a number that is not in its input; a failing
report is regenerated once with feedback, then saved with needs_review: true.
Every report must still be read by a person before it is shown anywhere.

Levels: area (Greater Manchester), borough, station.
Output: data/processed/briefs/<level>/<name>.md (report + check result + the input numbers).

Run:  python -m src.brief --dry-run                 write prompts only (no model; works anywhere)
      python -m src.brief --level area             Greater Manchester report (VM)
      python -m src.brief --only GTO               one station (or a borough name, e.g. Stockport)
      python -m src.brief --peak both              morning (default) and evening
Model path: config.yaml granite.model_path (the Granite GGUF on the VM).
"""
import argparse
import json
import logging
import re
import time

from src.config import ROOT, load_config

log = logging.getLogger(__name__)
NUM = re.compile(r"(?<![\w.])[-−]?\d+(?:\.\d+)?")
ALWAYS_ALLOWED = {95.0}  # "95% interval" is named in the prompt itself

SYSTEM = (
    "You write short, plain-English evidence summaries about local rail reliability for councillors, "
    "campaigners and journalists in Greater Manchester. Strict rules:\n"
    "- Use ONLY the numbers in the DATA, written exactly as they appear. Never calculate, round, add, "
    "subtract or invent numbers, dates or counts.\n"
    "- Say 'associated with' or 'suggests'; never claim that deprivation causes anything.\n"
    "- Describe how the train service treats an area, not judgements about the area or its people.\n"
    "- End with the caveats in one or two plain sentences.\n"
    "- Plain prose only: no headings, bullet points or markdown."
)
GLOSSARY = (
    "Field meanings: *_pct are percentages; failure = late by five or more minutes or cancelled; "
    "expected_failure_pct is what the station's position on the network predicts; "
    "difference_from_expected_points is actual minus expected, so negative means better than expected; "
    "rank_worst counts from the least reliable station; deprivation_percentile is higher for more "
    "deprived surroundings and means the station is more deprived than that percentage of stations; "
    "rho values are rank correlations between deprivation and failure, with 95% intervals in ci."
)
TASK = {
    "area": "Write two short paragraphs for a journalist. Start with the sentence in 'conclusion' exactly "
            "as written (if present). Then give the overall failure, late and cancellation rates, the two "
            "tests (raw_test and adjusted_test with their intervals), the comparison of the most and least "
            "deprived thirds, and name the stations doing worse and better than expected.",
    "borough": "Write three or four sentences for a councillor in this borough: its failure, late and "
               "cancellation rates, how it compares with Greater Manchester, and its most and least "
               "reliable stations.",
    "station": "Write three or four sentences for a local councillor about this station: its failure rate "
               "with the interval, late and cancellation rates, how it compares with what its position "
               "predicts, its rank, and how deprived its surroundings are. Include the line-position caveat.",
}
MAX_TOKENS = {"area": 450, "borough": 260, "station": 260}


# ---------------------------------------------------------------- number check

def _numbers(text):
    return {abs(float(m.replace("−", "-"))) for m in NUM.findall(text)}


def numbers_in(payload):
    """Every number in the input JSON (values, and digits inside its strings)."""
    if isinstance(payload, dict):
        # digits in key names count too (e.g. ..._most_deprived_20pct)
        return set().union(*(numbers_in(v) | _numbers(k.replace('_', ' ')) for k, v in payload.items())) if payload else set()
    if isinstance(payload, list):
        return set().union(*(numbers_in(v) for v in payload)) if payload else set()
    if isinstance(payload, bool) or payload is None:
        return set()
    if isinstance(payload, (int, float)):
        return {abs(float(payload))}
    return _numbers(str(payload))


def check_numbers(text, payload):
    """(ok, unexpected): every number in `text` must appear in `payload`."""
    allowed = numbers_in(payload) | ALWAYS_ALLOWED
    unexpected = sorted(n for n in _numbers(text) if n not in allowed)
    return not unexpected, unexpected


# ---------------------------------------------------------------- prompts and model

def messages(payload):
    user = f"{TASK[payload['level']]}\n\n{GLOSSARY}\n\nDATA:\n{json.dumps(payload, indent=1, ensure_ascii=False)}"
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


MODEL_LABEL = "IBM Granite"      # replaced by the GGUF file name in load_granite
MIN_WORDS = 15                    # shorter output counts as a failed generation


def load_granite(cfg):
    global MODEL_LABEL
    from pathlib import Path as _P
    from llama_cpp import Llama
    g = cfg.get("granite") or {}
    path = g.get("model_path")
    if not path:
        raise SystemExit("set granite.model_path in config.yaml to the Granite GGUF file (on the VM)")
    MODEL_LABEL = f"IBM Granite ({_P(path).name})"
    log.info("loading Granite from %s", path)
    return Llama(model_path=path, n_ctx=g.get("n_ctx", 4096), seed=0, verbose=False)


def completion_prompt(payload):
    """Plain-text prompt for base (non-chat) models that have no chat template."""
    user = messages(payload)[1]["content"]
    return f"{SYSTEM}\n\n{user}\n\nREPORT (plain English, no headings):\n"


def _chat(llm, msgs, payload):
    out = llm.create_chat_completion(messages=msgs, temperature=0.0,
                                     max_tokens=MAX_TOKENS[payload["level"]])
    return (out["choices"][0]["message"]["content"] or "").strip()


def _complete(llm, payload):
    out = llm.create_completion(prompt=completion_prompt(payload), temperature=0.0,
                                max_tokens=MAX_TOKENS[payload["level"]], repeat_penalty=1.15,
                                stop=["\nDATA:", "```", "\n\n\n", "<|"])
    return (out["choices"][0]["text"] or "").strip()


def _check(text, payload):
    """Number check plus a minimum length: empty or near-empty output is a failure."""
    if len(text.split()) < MIN_WORDS:
        return False, [f"too short ({len(text.split())} words)"]
    return check_numbers(text, payload)


def generate(llm, payload):
    """Chat first; if the model returns (almost) nothing, e.g. a base model without a chat
    template, fall back to plain completion. Returns (text, ok, bad, attempts)."""
    msgs = messages(payload)
    text, ok, bad, attempt = "", False, [], 0
    for attempt, mode in enumerate(("chat", "chat", "completion"), start=1):
        t0 = time.perf_counter()
        text = _chat(llm, msgs, payload) if mode == "chat" else _complete(llm, payload)
        ok, bad = _check(text, payload)
        log.info("  attempt %d (%s): %.1fs, %d words, check %s", attempt, mode, time.perf_counter() - t0,
                 len(text.split()), "passed" if ok else f"FAILED {bad}")
        if ok:
            return text, ok, bad, attempt
        if mode == "chat" and len(text.split()) < MIN_WORDS:
            continue                      # empty chat answer: go straight on (2nd chat, then completion)
        msgs = msgs + [{"role": "assistant", "content": text},
                       {"role": "user", "content": f"Your answer contained numbers that are not in the DATA: "
                                                   f"{bad}. Rewrite it using only numbers from the DATA."}]
    return text, ok, bad, attempt


def write_report(path, name, payload, text, ok, bad, attempts):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"<!-- generated by {MODEL_LABEL} via src/brief.py; {name} -->\n"
        f"<!-- number_check: {'passed' if ok else 'FAILED'}; unexpected: {bad}; attempts: {attempts}; "
        f"needs_review: {str(not ok).lower()}; human_reviewed: false -->\n\n"
        f"{text}\n\n---\n\n<details><summary>Input numbers</summary>\n\n```json\n"
        f"{json.dumps(payload, indent=2, ensure_ascii=False)}\n```\n</details>\n", encoding="utf-8")


# ---------------------------------------------------------------- run

def select(inputs, level, peak, only):
    for f in sorted(inputs.glob("*.json")):
        payload = json.loads(f.read_text(encoding="utf-8"))
        if level and payload["level"] != level:
            continue
        if peak != "both" and payload["peak"] != peak:
            continue
        if only and only.lower() not in {str(payload.get(k, "")).lower() for k in ("crs", "borough", "area")}:
            continue
        yield f.stem, payload


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", choices=["area", "borough", "station"])
    ap.add_argument("--peak", default="morning", choices=["morning", "evening", "both"])
    ap.add_argument("--only", help="station CRS or borough name")
    ap.add_argument("--dry-run", action="store_true", help="write prompts only, no model")
    args = ap.parse_args()
    cfg = load_config()
    proc = cfg["paths"]["data_dir"] / "processed"
    items = list(select(proc / "report_inputs", args.level, args.peak, args.only))
    if not items:
        raise SystemExit("no matching report inputs: run python -m src.evidence first")

    out = proc / "briefs"
    if args.dry_run:
        for name, payload in items:
            p = out / "_prompts" / f"{name}.txt"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("\n\n".join(f"[{m['role']}]\n{m['content']}" for m in messages(payload)), encoding="utf-8")
        print(f"dry run: {len(items)} prompts -> {out / '_prompts'}")
        return

    llm = load_granite(cfg)
    failed = 0
    for name, payload in items:
        log.info("%s", name)
        text, ok, bad, attempts = generate(llm, payload)
        failed += not ok
        write_report(out / payload["level"] / f"{name}.md", name, payload, text, ok, bad, attempts)
    print(f"{len(items)} reports -> {out}; number check failed (needs review): {failed}")
    print("Read every report before it is used anywhere (human review).")


if __name__ == "__main__":
    main()
