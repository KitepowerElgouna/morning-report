#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Výsledkovka tipů (track record) + podklad pro follow-up v ranním reportu.

- log_new(report, date): zapíše nové 'Koupit' tipy do data/tips-log.json
- evaluate(): deterministicky vyhodnotí otevřené tipy proti Yahoo cenám
  (zásah cíle / stopu / vypršení horizontu), spočítá statistiky
  → data/tips-results.json (čte ho appka, záložka Výsledky)
- followup_context(): textový blok o otevřených tipech pro prompt generátoru
- enrich_followup(report): doplní do report['follow_up'] reálná čísla

Pozn.: kontrola je snapshotová (2× denně) — intradenní dotyk stopu/cíle mezi
běhy se pozná až při další kontrole; stop má přednost (konzervativní).
"""

import json, os, re, sys, glob, datetime, time, urllib.request

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
LOG = os.path.join(DATA, "tips-log.json")
RESULTS = os.path.join(DATA, "tips-results.json")
UA = {"User-Agent": "Mozilla/5.0"}


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save(path, obj):
    os.makedirs(DATA, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _money(s):
    if isinstance(s, (int, float)):
        return float(s)
    if not isinstance(s, str):
        return None
    t = "".join(c for c in s.replace(",", "") if c.isdigit() or c == ".")
    try:
        return float(t)
    except ValueError:
        return None


def _horizon_days(s):
    """'1–4 týdny' → 28; '2–6 týdnů' → 42; '10 dní' → 10; default 28."""
    if not isinstance(s, str):
        return 28
    nums = [int(n) for n in re.findall(r"\d+", s)]
    if not nums:
        return 28
    n = max(nums)
    low = s.lower()
    if "týd" in low or "tyd" in low or "week" in low:   # týdny
        return n * 7
    if "měs" in low or "mes" in low or "month" in low:  # měsíce
        return n * 30
    return n  # dny


def _yahoo(ticker):
    ysym = str(ticker).replace(".", "-")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ysym}?range=1d&interval=1d"
    for _ in range(3):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=10) as r:
                d = json.load(r)
            return d["chart"]["result"][0]["meta"].get("regularMarketPrice")
        except Exception:
            time.sleep(1)
    return None


def log_new(report, date=None):
    """Zaloguj nové nákupní tipy (Koupit/Přikoupit) jako otevřené pozice."""
    date = date or datetime.date.today().isoformat()
    log = _load(LOG, [])
    open_tk = {e["ticker"] for e in log if e.get("status") == "open"}
    added = 0
    for f in report.get("firms", []):
        tr = f.get("trade") or {}
        if tr.get("action") not in ("Koupit", "Přikoupit"):
            continue
        tk = (f.get("ticker") or "").upper()
        entry = _money((f.get("price") or {}).get("current"))
        stop = _money(tr.get("stop_loss"))
        target = _money(tr.get("target")) or _money((f.get("price") or {}).get("fair_value"))
        if not tk or "/" in tk or not entry or not stop:
            continue
        if tk in open_tk:
            continue  # už otevřený z dřívějška — neduplikuj
        log.append({
            "id": f"{date}-{tk}", "date": date, "ticker": tk,
            "name": f.get("name"), "action": tr.get("action"),
            "entry": round(entry, 2), "stop": round(stop, 2),
            "target": round(target, 2) if target else None,
            "horizon_days": _horizon_days(tr.get("horizon")),
            "status": "open", "outcome": None,
            "last_price": round(entry, 2), "pl_pct": 0.0, "closed_date": None,
        })
        open_tk.add(tk)
        added += 1
    _save(LOG, log)
    print(f"tips: zalogováno {added} nových tipů", file=sys.stderr)
    return added


def evaluate():
    """Aktualizuj otevřené tipy živými cenami; uzavři zásah stopu/cíle/vypršení."""
    log = _load(LOG, [])
    today = datetime.date.today()
    for e in log:
        if e.get("status") != "open":
            continue
        price = _yahoo(e["ticker"])
        if price is None:
            continue
        entry = e.get("entry") or 0
        e["last_price"] = round(price, 2)
        if entry:
            e["pl_pct"] = round((price / entry - 1) * 100, 1)
        stop, target = e.get("stop"), e.get("target")
        started = datetime.date.fromisoformat(e["date"])
        expired = (today - started).days > int(e.get("horizon_days") or 28)
        if stop and price <= stop:  # stop má přednost (konzervativní)
            e.update(status="closed", outcome="stop", closed_date=today.isoformat(),
                     pl_pct=round((stop / entry - 1) * 100, 1) if entry else e["pl_pct"])
        elif target and price >= target:
            e.update(status="closed", outcome="cíl", closed_date=today.isoformat(),
                     pl_pct=round((target / entry - 1) * 100, 1) if entry else e["pl_pct"])
        elif expired:
            e.update(status="closed", outcome="vypršel", closed_date=today.isoformat())
    _save(LOG, log)

    closed = [e for e in log if e.get("status") == "closed"]
    open_ = [e for e in log if e.get("status") == "open"]
    wins = [e for e in closed if (e.get("pl_pct") or 0) > 0]
    losses = [e for e in closed if (e.get("pl_pct") or 0) <= 0]
    stats = {
        "closed": len(closed), "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else None,
        "avg_win": round(sum(e["pl_pct"] for e in wins) / len(wins), 1) if wins else None,
        "avg_loss": round(sum(e["pl_pct"] for e in losses) / len(losses), 1) if losses else None,
        "avg_pl": round(sum((e.get("pl_pct") or 0) for e in closed) / len(closed), 1) if closed else None,
        "open": len(open_),
    }
    _save(RESULTS, {
        "updated": today.isoformat(), "stats": stats,
        "open": sorted(open_, key=lambda e: e["date"], reverse=True),
        "closed": sorted(closed, key=lambda e: e.get("closed_date") or "", reverse=True)[:100],
    })
    print(f"tips: vyhodnoceno — open {len(open_)}, closed {len(closed)}", file=sys.stderr)


def followup_context():
    """Textový blok pro prompt: stav otevřených tipů (po evaluate())."""
    log = _load(LOG, [])
    open_ = [e for e in log if e.get("status") == "open"]
    if not open_:
        return ""
    lines = ["OTEVŘENÉ TIPY z minulých reportů (vrať ke KAŽDÉMU položku v poli follow_up):"]
    for e in open_:
        pl = e.get("pl_pct") or 0
        lines.append(
            f"- {e['ticker']} ({e.get('action')} {e['date']}): vstup ${e['entry']}, "
            f"teď ${e.get('last_price')} ({pl:+.1f} %), stop ${e['stop']}, cíl ${e.get('target')}"
        )
    return "\n".join(lines)


def enrich_followup(report):
    """Doplň do follow_up reálná čísla; zahoď položky k neexistujícím tipům."""
    log = _load(LOG, [])
    open_by_tk = {e["ticker"]: e for e in log if e.get("status") == "open"}
    out = []
    for fu in report.get("follow_up") or []:
        tk = (fu.get("ticker") or "").upper()
        e = open_by_tk.get(tk)
        if not e:
            continue
        fu.update(ticker=tk, entry=e.get("entry"), current=e.get("last_price"),
                  pl_pct=e.get("pl_pct"), stop=e.get("stop"))
        out.append(fu)
    if out:
        report["follow_up"] = out
    else:
        report.pop("follow_up", None)
    return report


def backfill():
    """Jednorázově naplň log z archivovaných denních reportů."""
    files = sorted(glob.glob(os.path.join(DATA, "report-????-??-??.json")))
    for path in files:
        date = os.path.basename(path)[7:17]
        log_new(_load(path, {}), date)
    evaluate()


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "evaluate"
    if mode == "backfill":
        backfill()
    else:
        evaluate()
