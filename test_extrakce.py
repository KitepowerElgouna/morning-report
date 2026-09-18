#!/usr/bin/env python3
"""Hledání reportu v odpovědi modelu (★ 18. 9. 2026).

Oba běhy 18. 9. spadly na „Report neobsahuje pole 'firms'" — kód se od
13. 9. neměnil a 17. 9. oba prošly. Model po hodině rešerše vrátí platný
JSON, jen ne vždy s `firms` nahoře: typicky ho obalí (`{"report": …}`)
nebo pošle víc objektů a ten první nese jen `date`/`summary`. Hledá se
proto REPORT, ne „první JSON v textu".

Co test drží:
  • čistý JSON, JSON v ```json bloku, text okolo;
  • zabalený report i víc objektů za sebou (dnešní dva scénáře);
  • report s PRÁZDNÝM `firms` neprojde nikdy (radši žádný než prázdný);
  • vadný JSON před dobrým se přeskočí;
  • chyba pojmenuje klíče, které přišly (aby bylo co diagnostikovat);
  • surová odpověď se uloží na disk.

Běží bez sítě a bez modelu — je to jen rozbor textu.
"""
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ["REPORT_SUROVA"] = str(Path(tempfile.mkdtemp()) / "surova.txt")
os.environ.setdefault("ANTHROPIC_API_KEY", "")

_spec = importlib.util.spec_from_file_location("gr", ROOT / "generate_report.py")
gr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gr)

CHYBY = []


def zkus(nazev: str, podminka: bool, detail: str = "") -> None:
    print(("✓ " if podminka else "✗ ") + nazev
          + ("" if podminka else f" — {detail}"))
    if not podminka:
        CHYBY.append(nazev)


REPORT = {"date": "18. září 2026", "summary": "nálada trhu",
          "sources": ["yahoo"], "firms": [{"name": "Nvidia", "ticker": "NVDA"}]}

zkus("čistý JSON projde",
     gr.extract_json(json.dumps(REPORT))["firms"][0]["ticker"] == "NVDA")

zkus("JSON v ```json bloku s textem okolo",
     gr.extract_json("Report:\n```json\n" + json.dumps(REPORT)
                     + "\n```\nHotovo.")["firms"][0]["name"] == "Nvidia")

zkus("★ zabalený report {\"report\": …} se rozbalí",
     gr.extract_json(json.dumps({"report": REPORT, "meta": {"turns": 28}}))
     ["firms"][0]["ticker"] == "NVDA")

zkus("★ víc objektů za sebou — vezme ten s firms",
     gr.extract_json("Nejdřív:\n"
                     + json.dumps({"date": "18. září 2026", "themes": [{"event": "x"}]})
                     + "\n\nA report:\n" + json.dumps(REPORT))
     ["firms"][0]["name"] == "Nvidia")

zkus("zanoření do tří úrovní se najde",
     gr.extract_json(json.dumps({"a": {"b": {"report": REPORT}}}))
     ["firms"][0]["ticker"] == "NVDA")

try:
    gr.extract_json(json.dumps({"date": "x", "firms": []}))
    zkus("prázdné firms = chyba (report bez titulů nikdy neprojde)", False, "prošlo")
except ValueError as exc:
    zkus("prázdné firms = chyba (report bez titulů nikdy neprojde)",
         "firms" in str(exc), str(exc))

try:
    gr.extract_json("Bohužel jsem to nestihl.")
    zkus("bez JSONu = chyba", False, "prošlo")
except ValueError as exc:
    zkus("bez JSONu = chyba", "validní JSON" in str(exc), str(exc))

try:
    gr.extract_json(json.dumps({"date": "x", "summary": "y", "poznamka": "z"}))
    zkus("chyba vypíše klíče prvního JSONu", False, "prošlo")
except ValueError as exc:
    zkus("chyba vypíše klíče prvního JSONu",
         "date" in str(exc) and "poznamka" in str(exc), str(exc))

zkus("vadný JSON se přeskočí a vezme se ten platný",
     gr.extract_json('{"firms": [ {"name": "X",, } ] }\n' + json.dumps(REPORT))
     ["firms"][0]["ticker"] == "NVDA")

kde = gr.uloz_surovou_odpoved("odpověď modelu bez reportu", "test")
zkus("surová odpověď se uloží",
     bool(kde) and "odpověď modelu bez reportu" in Path(kde).read_text(encoding="utf-8"),
     str(kde))

print()
print("test_extrakce: " + ("OK" if not CHYBY else f"CHYBY: {len(CHYBY)}"))
sys.exit(1 if CHYBY else 0)
