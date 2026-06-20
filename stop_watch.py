#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hlídač stop-lossů — běží na GitHub Actions během obchodních hodin.
Stáhne aktuální ceny (Yahoo, zdarma), porovná je s tvými stop-lossy a když
cena spadne POD stop, pošle push notifikaci přes ntfy.sh (i když máš appku zavřenou).

Konfigurace (v repu → Settings):
  - secret  POSITIONS  = JSON tvých pozic, např.:
        [{"ticker":"NVO","stop":42.0,"name":"Novo Nordisk"},
         {"ticker":"SHEL","stop":72.0}]
  - variable NTFY_TOPIC = tvůj soukromý ntfy topic (dlouhý náhodný řetězec)

Dedup: stav (na co už upozornil) drží v data/stop-state.json, ať nespamuje.
"""

import os, json, urllib.request, urllib.error

STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "stop-state.json")
UA = {"User-Agent": "Mozilla/5.0"}


def yahoo_price(ticker):
    ysym = ticker.replace(".", "-")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ysym}?range=1d&interval=1d"
    for _ in range(3):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=12) as r:
                d = json.load(r)
            return d["chart"]["result"][0]["meta"].get("regularMarketPrice")
        except Exception:
            continue
    return None


def ntfy(topic, title, message):
    if not topic:
        return
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}",
        data=message.encode("utf-8"),
        headers={"Title": title, "Priority": "high", "Tags": "warning"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print("ntfy chyba:", e)


def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return set(json.load(f).get("alerted", []))
    except Exception:
        return set()


def save_state(alerted):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump({"alerted": sorted(alerted)}, f, ensure_ascii=False, indent=2)


def main():
    positions = json.loads(os.environ.get("POSITIONS", "[]") or "[]")
    topic = os.environ.get("NTFY_TOPIC", "")
    if not positions:
        print("Žádné pozice (secret POSITIONS prázdný) — končím.")
        return

    alerted = load_state()
    for pos in positions:
        ticker = pos.get("ticker")
        stop = pos.get("stop")
        if not ticker or stop is None:
            continue
        price = yahoo_price(ticker)
        if price is None:
            print(f"{ticker}: cenu nešlo stáhnout")
            continue
        tk = ticker.upper()
        if price <= float(stop):
            if tk not in alerted:
                name = pos.get("name", tk)
                ntfy(topic, f"Stop-loss: {tk}",
                     f"{name} spadl pod stop-loss. Cena {price}, stop {stop}. Zvaž výstup podle plánu.")
                alerted.add(tk)
                print(f"⚠️ {tk} pod stopem ({price} ≤ {stop}) — push odeslán")
            else:
                print(f"{tk} stále pod stopem (už upozorněno)")
        else:
            if tk in alerted:
                alerted.discard(tk)
                print(f"{tk} se vrátil nad stop ({price} > {stop}) — reset")
            else:
                print(f"{tk} OK ({price} > {stop})")

    save_state(alerted)


if __name__ == "__main__":
    main()
