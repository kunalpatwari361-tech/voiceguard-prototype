"""Make the app's translations for every Indian language (android assets/i18n/<code>.json).

Collects every UI text written as tr("English", "Hindi") in the Android code, plus the fixed English/Hindi texts the
server sends (advice, tips, verdicts), and machine-translates the English into each language with Google Translate.
English and Hindi stay exactly as written in the code. Existing translations are kept, so re-running only translates
new texts. Texts built at run time (with $name etc.) are skipped and show in English.

Usage: python scripts/make_translations.py [codes...]     (needs internet)
"""
import json
import re
import socket
import sys
import time
from pathlib import Path

import requests
import urllib3.util.connection

urllib3.util.connection.allowed_gai_family = lambda: socket.AF_INET   # IPv6 to Google hangs on this network

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "android" / "app" / "src" / "main" / "java"
OUT = ROOT / "android" / "app" / "src" / "main" / "assets" / "i18n"
SERVER = ROOT / "backend" / "app"
# app language code -> Google Translate code (the app lists only the ones that get a file)
LANGS = {"bn": "bn", "te": "te", "mr": "mr", "ta": "ta", "ur": "ur", "gu": "gu", "kn": "kn", "ml": "ml", "or": "or",
         "pa": "pa", "as": "as", "mai": "mai", "sat": "sat", "ks": "ks", "ne": "ne", "sd": "sd", "kok": "gom",
         "doi": "doi", "mni": "mni-Mtei", "brx": "brx", "sa": "sa"}
KT = re.compile(r'\btr\(\s*"((?:[^"\\]|\\.)*)"\s*,\s*"((?:[^"\\]|\\.)*)"\s*\)', re.S)
PY_TUPLE = re.compile(r'\(\s*"([^"{}\\]+)"\s*,\s*"([^"{}\\]*[ऀ-ॿ][^"{}\\]*)"\s*\)')
PY_DICT = re.compile(r'"en":\s*"([^"{}\\]+)"\s*,\s*"hi":\s*"([^"{}\\]*[ऀ-ॿ][^"{}\\]*)"')


def unescape(s: str) -> str:
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), s)


def collect() -> list[str]:
    texts = set()
    for f in SRC.rglob("*.kt"):
        for en, _ in KT.findall(f.read_text(encoding="utf-8")):
            if "$" not in en and en.strip():
                texts.add(unescape(en))
    for f in SERVER.rglob("*.py"):
        s = f.read_text(encoding="utf-8")
        for rx in (PY_TUPLE, PY_DICT):
            texts.update(en for en, _ in rx.findall(s) if en.strip())
    return sorted(texts)


def google(text: str, tl: str) -> str:
    r = requests.post("https://translate.googleapis.com/translate_a/single",
                      params={"client": "gtx", "sl": "en", "tl": tl, "dt": "t"}, data={"q": text}, timeout=30)
    r.raise_for_status()
    return "".join(seg[0] for seg in r.json()[0] if seg and seg[0])


def translate_all(texts: list[str], tl: str) -> dict[str, str]:
    out, batch, size = {}, [], 0
    batches = []
    for t in texts:
        if batch and (size + len(t) > 3500 or len(batch) >= 40):
            batches.append(batch)
            batch, size = [], 0
        batch.append(t)
        size += len(t) + 1
    if batch:
        batches.append(batch)
    for b in batches:
        lines = None
        for attempt in range(6):
            try:
                res = google("\n".join(x.replace("\n", " ") for x in b), tl).split("\n")
                lines = res if len(res) == len(b) else []
                break
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 400:
                    raise
                print(f"    {tl}: translator busy, waiting {30 * (attempt + 1)}s", flush=True)
                time.sleep(30 * (attempt + 1))   # too many requests: slow down instead of giving up
            except requests.RequestException:
                time.sleep(10 * (attempt + 1))
        if lines is None:
            raise RuntimeError("translator unavailable - run again later")
        if not lines:   # the line count changed: translate this batch one text at a time
            for x in b:
                try:
                    lines.append(google(x, tl))
                except requests.RequestException:
                    lines.append(x)
                time.sleep(0.6)
        # keep the original leading/trailing spaces (texts like " (you)" are joined to others in the app)
        out.update({en: en[:len(en) - len(en.lstrip())] + t.strip() + en[len(en.rstrip()):]
                    for en, t in zip(b, lines) if t.strip()})
        time.sleep(1.5)
    return out


def main():
    codes = sys.argv[1:] or list(LANGS)
    texts = collect()
    print(f"{len(texts)} texts to translate", flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    for code in codes:
        path = OUT / f"{code}.json"
        have = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        todo = [t for t in texts if t not in have]
        t0 = time.time()
        try:
            google("Settings", LANGS[code])
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 400:
                print(f"{code}: not supported by the translator - skipped", flush=True)
                continue
            print(f"{code}: translator busy - waiting 60s", flush=True)
            time.sleep(60)
        try:
            new = translate_all(todo, LANGS[code]) if todo else {}
        except Exception as e:
            print(f"{code}: FAILED {type(e).__name__}: {e}", flush=True)
            continue
        table = {k: v for k, v in (have | new).items() if k in texts}
        same = sum(1 for k, v in table.items() if k == v)
        if len(table) < len(texts) * 0.8 or same > len(table) * 0.5:
            print(f"{code}: not supported by the translator ({same}/{len(table)} unchanged) - skipped", flush=True)
            path.unlink(missing_ok=True)
            continue
        path.write_text(json.dumps(table, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")
        sample = table.get("Settings") or next(iter(table.values()))
        print(f"{code}: {len(table)} texts ({len(new)} new) in {time.time() - t0:.0f}s - e.g. Settings = {sample}", flush=True)


if __name__ == "__main__":
    main()
