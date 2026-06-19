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
    "analysis": "<3-4 věty: co firma dělá, cena vs cíl a upside, P/E/marže/beta, a poctivá věta o konsensu analytiků>",
    "pros": ["<plus 1>","<plus 2>","<plus 3>"],
    "cons": ["<riziko 1>","<riziko 2>"]
   }
 ]
}
""".strip()

PROMPT = f"""Jsi zkušený akciový analytik. Vytvoř DNEŠNÍ ranní investiční report v češtině.

POSTUP:
1) Použij web search a zjisti AKTUÁLNÍ reálná čísla (dnešní/poslední close) pro {NUM_STOCKS}
   kvalitních velkých firem napříč různými odvětvími (tech, zdravotnictví, finance,
   průmysl, spotřeba, energetika). Vyber zajímavý mix — ne jen ty nejznámější.
   Pro každou zjisti: aktuální cenu, průměrný cíl analytiků (analyst price target),
   rating analytiků, P/E, beta, čistou marži, dividendu, 52týdenní max/min.
   NIC si nevymýšlej — co nenajdeš, dej "n/a".

2) Spočítej upside = cíl/cena - 1. Doporučení:
   - "Koupit" když upside >= 10 % A rating Buy/Strong Buy (nebo upside >= 15 %)
   - "Sledovat" když upside <= -8 % nebo rating Sell
   - jinak "Držet"
   Seřaď firmy podle upside sestupně (nejatraktivnější nahoře).

3) {SCHEMA_HINT}

DŮLEŽITÉ:
- Výstupem tvé poslední zprávy je POUZE ten jeden JSON objekt (žádný text okolo,
  žádné ```json ohraničení nutné — ale pokud ho dáš, vlož dovnitř jen validní JSON).
- Čísla reálná z webu; 'fair value' = konsensus analytiků (NE záruka); doporučení je
  orientační heuristika, ne investiční rada — uveď to v summary.
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
        {"type": "web_search_20260209", "name": "web_search"},
        {"type": "web_fetch_20260209", "name": "web_fetch"},
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


def main():
    today = datetime.date.today().isoformat()
    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"Generuji report ({MODEL}, {NUM_STOCKS} firem)…", file=sys.stderr)
    report = generate()
    report.setdefault("generated_at", today)

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
