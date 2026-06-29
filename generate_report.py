#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ranní makléřský report — generátor poháněný Claude (Anthropic API + web search).

Co dělá:
  - Zavolá Claude (model claude-opus-4-8) se zapnutým web search.
  - Claude si stáhne AKTUÁLNÍ reálná čísla (ceny, cíle analytiků, rating)
    pro diverzifikovaný koš akcií z různých odvětví.
  - Vrátí report ve formátu, který umí načíst dashboard (index.html).
  - Uloží data/report-RRRR-MM-DD.json a data/report-latest.json.

Spouští se každé ráno přes GitHub Actions (.github/workflows/daily-report.yml).
Lokálně:  ANTHROPIC_API_KEY=... python3 generate_report.py

Poctivě: čísla jsou reálná z webu, ale 'fair value' = konsensus analytiků (odhad),
doporučení je strojová heuristika. Není to investiční rada.
"""

import os, re, json, sys, datetime

import anthropic

# ── Konfigurace ───────────────────────────────────────────────────────────────
MODEL = os.environ.get("REPORT_MODEL", "claude-opus-4-8")   # levnější: claude-sonnet-4-6
NUM_STOCKS = int(os.environ.get("REPORT_NUM_STOCKS", "10"))
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# ── Schéma reportu (popsané pro Claude, ať vrátí kompatibilní JSON) ─────────────
SCHEMA_HINT = """
Vrať JEDEN JSON objekt přesně v tomto tvaru (klíče nech anglicky, texty piš ČESKY):

{
 "date": "<dnešní datum, např. '19. června 2026'>",
 "summary": "<2-4 věty: nálada trhu dnes + proč jsou tyto tipy + POCTIVÁ věta, že fair value = konsensus analytiků a jde o orientační vodítko, ne investiční radu>",
 "sources": ["<weby, ze kterých jsi čerpal>"],
 "firms": [
   {
    "name": "<název>", "ticker": "<TICKER>",
    "region": "usa|europe|asia", "sector": "tech|healthcare|finance|industry|consumer|energy|crypto",
    "recommendation": "Koupit|Držet|Sledovat",
    "price": {
      "current": "$<cena>", "fair_value": "$<cíl analytiků>",
      "discount": "<+/-X% upside>", "signal": "<krátký signál>",
      "signal_reason": "<1 věta>", "buy_zone": {"low":"$<num>","high":"$<num>"}
    },
    "moat": {"score":"<1-10>","verdict":"<krátce>","summary":"<1 věta>"},
    "intrinsic": {"annual_return":"<+X%>","verdict":"orientační odhad","summary":"<1 věta>","risk":"<1-5>"},
    "metrics": {"Aktuální cena":"$<cena>","Fair value (cíl analytiků)":"$<cíl>","P/E":"<x>","Beta":"<num>","52t max/min":"$<hi> / $<lo>"},
    "indicators": {
      "P/E":{"value":"<x>","note":"<Levné/Férové/Drahé>"},
      "Beta":{"value":"<num>","note":"<Defenzivní/Volatilní>"},
      "Div. yield":{"value":"<%>","note":"Dividenda"},
      "Net Margin":{"value":"<%|n/a>","note":"<pozn>"}
    },
    "analysis": "<2-3 věty: co firma dělá + proč je teď v hledáčku>",
    "trade": {
      "action": "Koupit|Přikoupit|Držet|Ubrat|Prodat",
      "horizon": "<krátkodobý horizont, např. '1–4 týdny'>",
      "entry": "$<cena nebo pásmo pro vstup>",
      "stop_loss": "$<cena, kde vystoupit při ztrátě>",
      "target": "$<krátkodobý cílový kurz>",
      "position_size": "<kolik max, např. 'max 3 % satelitu'>",
      "risk_level": "<1-5>",
      "catalyst": "<co titulem PRÁVĚ hýbe / proč teď: výsledky, zpráva, průraz, momentum>",
      "action_note": "<1 věta: co konkrétně udělat dnes>"
    },
    "pros": ["<plus 1>","<plus 2>","<plus 3>"],
    "cons": ["<riziko 1>","<riziko 2>"]
   }
 ]
}
""".strip()

PROMPT = f"""Jsi aktivní trading desk pro KRÁTKODOBÉ příležitosti. Vytvoř DNEŠNÍ ranní report v češtině.

ZDROJE (čerpej přes web_search/web_fetch a KŘÍŽOVĚ ověřuj — víc zdrojů = míň chyb):
- Cena + fundamenty: Yahoo Finance, StockAnalysis.com, Finviz.
- Cíle a rating analytiků: StockAnalysis, MarketBeat, TipRanks, Zacks, Finnhub.
- Zprávy a katalyzátory: Benzinga, MarketWatch, Finviz news, CNBC, Reuters.
- Sentiment / momentum: StockTwits, Finviz (relativní objem, výkonnost).
- Ověřené výkazy: SEC EDGAR. Makro: FRED (sazby Fedu, inflace, výnosy).
- Krypto (jen pokud je relevantní): CoinGecko, CoinGlass.

POSTUP:
1) MAKRO: nejdřív krátce zjisti náladu trhu dnes (indexy, sazby Fedu, hlavní zpráva dne)
   z CNBC/Reuters/FRED — půjde do summary.
2) Najdi {NUM_STOCKS} titulů s aktuálním pohybem/katalyzátorem (dny–týdny), VYŠŠÍ riziko,
   napříč odvětvími. Pro KAŽDÝ ověř AKTUÁLNÍ cenu a cíl analytiků MINIMÁLNĚ ze DVOU zdrojů
   (např. Yahoo + StockAnalysis/Finviz). Když se cena liší o víc než ~3 %, vezmi
   konzervativnější a zmiň to v analýze. Zjisti i P/E, beta, 52t max/min a CO TITULEM
   PRÁVĚ HÝBE (katalyzátor: výsledky, zpráva, průraz). NIC si nevymýšlej — co nenajdeš, "n/a".
3) upside = cíl/cena - 1. recommendation: "Koupit" (upside>=10 % a Buy/Strong Buy, nebo
   >=15 %), "Sledovat" (<=-8 % nebo Sell), jinak "Držet". U KAŽDÉHO povinně trade plán:
   akce, STOP-LOSS (povinné!), krátkodobý cíl, velikost pozice (žádná > ~5 % satelitu).
   Seřaď podle síly příležitosti/momenta.
4) {SCHEMA_HINT}

DŮLEŽITÉ:
- Pole "sources" vyplň SKUTEČNĚ použitými weby.
- summary začni makro větou + poctivá věta: jde o KRÁTKODOBÉ RIZIKOVÉ tipy pro "satelitní"
  peníze, drž stop-lossy a velikost pozice, časté přetrádování škodí.
- Výstupem tvé poslední zprávy je POUZE ten jeden JSON objekt (žádný text okolo).
"""


def extract_json(text: str) -> dict:
    """Vytáhne první vyvážený {...} JSON objekt z textu."""
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            c = text[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            else:
                if c == '"':
                    in_str = True
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = text[start:i + 1]
                        try:
                            return json.loads(candidate)
                        except json.JSONDecodeError:
                            break  # zkus další '{'
        start = text.find("{", start + 1)
    raise ValueError("V odpovědi nebyl nalezen validní JSON objekt.")


def generate() -> dict:
    client = anthropic.Anthropic()  # bere ANTHROPIC_API_KEY z prostředí
    tools = [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": 30},
        {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 30},
    ]
    messages = [{"role": "user", "content": PROMPT}]

    # Server-side nástroje (web search) běží ve smyčce; při 'pause_turn' pokračujeme.
    for _ in range(8):
        with client.messages.stream(
            model=MODEL,
            max_tokens=32000,
            thinking={"type": "adaptive"},
            tools=tools,
            messages=messages,
        ) as stream:
            msg = stream.get_final_message()

        if msg.stop_reason == "pause_turn":
            messages = [
                {"role": "user", "content": PROMPT},
                {"role": "assistant", "content": msg.content},
            ]
            continue
        break

    if msg.stop_reason == "refusal":
        raise RuntimeError("Model odmítl požadavek (stop_reason=refusal).")

    text = "".join(b.text for b in msg.content if b.type == "text")
    report = extract_json(text)
    if not report.get("firms"):
        raise ValueError("Report neobsahuje pole 'firms'.")
    return report


def _yahoo_price(ticker):
    import urllib.request
    ysym = ticker.replace(".", "-")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ysym}?range=1d&interval=1d"
    for _ in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as r:
                d = json.load(r)
            return d["chart"]["result"][0]["meta"].get("regularMarketPrice")
        except Exception:
            continue
    return None


def _parse_price(s):
    if not isinstance(s, str):
        return None
    t = "".join(c for c in s if c.isdigit() or c == ".")
    try:
        return float(t)
    except ValueError:
        return None


def validate_prices(report):
    """Deterministická pojistka: ověř ceny proti Yahoo; když se report liší >5 %,
    oprav cenu na reálnou a přepočítej potenciál. Tím se chytnou Claudovy překlepy."""
    for f in report.get("firms", []):
        tk = f.get("ticker", "")
        if not tk or "/" in tk or tk.upper().startswith("BTC"):
            continue
        real = _yahoo_price(tk)
        if real is None:
            continue
        price = f.get("price") or {}
        cur = _parse_price(price.get("current"))
        if not cur or abs(cur / real - 1) <= 0.05:
            continue
        sym = price["current"][:1] if isinstance(price.get("current"), str) and price["current"][:1] in "$€£" else "$"
        price["current"] = f"{sym}{real:,.0f}" if real >= 1000 else f"{sym}{real:.2f}"
        fv = _parse_price(price.get("fair_value"))
        if fv:
            up = (fv / real - 1) * 100
            price["discount"] = f"{'+' if up >= 0 else ''}{up:.0f}% upside"
            # sjednoť doporučení s opravenou cenou (ať není „Koupit" se záporným potenciálem)
            if up <= -8:
                f["recommendation"] = "Sledovat"
            elif up < 10:
                f["recommendation"] = "Držet"
            trade = f.get("trade") or {}
            if trade.get("action") in ("Koupit", "Přikoupit") and up < 10:
                trade["action"] = "Ubrat" if up <= -8 else "Držet"
                f["trade"] = trade
        f["price"] = price
        f.setdefault("metrics", {})["Aktuální cena"] = price["current"]
        print(f"   ⚠️ {tk}: cena opravena {cur} → {real} (reálná z Yahoo)", file=sys.stderr)
    return report


def reconcile_recs(report):
    """Sjednoť doporučení s potenciálem: nikdy 'Koupit' se záporným/malým upside."""
    import re
    for f in report.get("firms", []):
        d = (f.get("price") or {}).get("discount")
        if not isinstance(d, str):
            continue
        m = re.search(r"[-+]?[0-9]+(\.[0-9]+)?", d.replace(",", "."))
        if not m:
            continue
        up = float(m.group())
        if up < 10:
            if f.get("recommendation") in ("Koupit", "Přikoupit"):
                f["recommendation"] = "Sledovat" if up <= -8 else "Držet"
            tr = f.get("trade") or {}
            if tr.get("action") in ("Koupit", "Přikoupit"):
                tr["action"] = "Ubrat" if up <= -8 else "Držet"
                f["trade"] = tr
    return report


def main():
    today = datetime.date.today().isoformat()
    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"Generuji report ({MODEL}, {NUM_STOCKS} firem)…", file=sys.stderr)
    report = generate()
    print("Ověřuji ceny proti Yahoo…", file=sys.stderr)
    report = validate_prices(report)
    report = reconcile_recs(report)
    report.setdefault("generated_at", today)
    # Vždy oraziítkuj report SKUTEČNÝM dnešním datem (Claude občas píše staré z webu).
    _cz = {1: "ledna", 2: "února", 3: "března", 4: "dubna", 5: "května", 6: "června",
           7: "července", 8: "srpna", 9: "září", 10: "října", 11: "listopadu", 12: "prosince"}
    _d = datetime.date.today()
    report["date"] = f"{_d.day}. {_cz[_d.month]} {_d.year}"

    dated = os.path.join(OUT_DIR, f"report-{today}.json")
    latest = os.path.join(OUT_DIR, "report-latest.json")
    for path in (dated, latest):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

    recs = [f"{x.get('ticker')}={x.get('recommendation')}" for x in report["firms"]]
    print(f"✅ Hotovo: {len(report['firms'])} firem → {os.path.basename(dated)} + report-latest.json")
    print("   " + ", ".join(recs))


if __name__ == "__main__":
    main()
