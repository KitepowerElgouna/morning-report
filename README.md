# Ranní akciový report 📈

Každé ráno automaticky vygeneruje report „kam by se dalo investovat" (top akcie
z různých odvětví, reálná data + makléřský komentář od Claude) a ukáže ho na webu.
Otevřeš adresu (nebo ikonu na iPhonu) → čteš report. Žádné ruční nahrávání.

## Jak to funguje

```
GitHub Actions (ráno, cron)  →  generate_report.py  →  Claude + web search
        →  data/report-latest.json  →  GitHub Pages (index.html)  →  iPhone / web
```

- **Motor:** `generate_report.py` zavolá Claude (Anthropic API) se zapnutým web
  searchem; Claude stáhne aktuální ceny, cíle analytiků a ratingy a vrátí report.
- **Spouštění:** `.github/workflows/daily-report.yml` (cron + tlačítko „Run workflow").
- **Hosting:** GitHub Pages servíruje `index.html`, který si po otevření sám natáhne
  `data/report-latest.json`.

## Nastavení (jednorázově)

1. **API klíč.** Repo → *Settings → Secrets and variables → Actions → New repository secret*
   - Name: `ANTHROPIC_API_KEY`
   - Value: tvůj klíč z https://console.anthropic.com (Settings → API Keys)
2. **(Volitelně) levnější model.** Tamtéž záložka *Variables* → `REPORT_MODEL` = `claude-sonnet-4-6` (~poloviční cena).
3. **Zapnout Pages.** Repo → *Settings → Pages* → Source: *Deploy from a branch*, Branch: `main` / `/ (root)`.
4. **Otestovat.** Repo → *Actions → Ranní report → Run workflow*. Po doběhnutí se v `data/` objeví report.
5. **iPhone.** Otevři adresu Pages v Safari → Sdílet → *Přidat na plochu*.

## Náklady

- GitHub (veřejné repo + Pages + Actions): **zdarma**.
- Anthropic API: závisí na modelu a délce reportu. Orientačně nižší desítky €/měsíc
  při denním Opus 4.8; levnější s `claude-sonnet-4-6` a během víkendů se negeneruje.

## ⚠️ Poctivě

Čísla jsou reálná z webu, ale **„fair value" = konsensus analytiků** (odhad, ne záruka)
a **doporučení je strojová heuristika**. Je to **orientační vodítko, ne investiční rada.**
Rozhodnutí dělej sám.
