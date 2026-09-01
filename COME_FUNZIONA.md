# Come funziona il Resale Monitor

Documento operativo: cosa fa il tool, da dove prende i dati, filtri, alert, cosa manca e perché.

Il dettaglio sito-per-sito (fetch, WAF, perché 0 alert): **[DETTAGLIO_FUNZIONAMENTO.md](DETTAGLIO_FUNZIONAMENTO.md)**.

---

## 1. Idea in una frase

**Non punta e non compra.** Legge cataloghi pubblici (o semi-pubblici), stima se un lotto e rivendibile su **eBay / Vinted / Subito** con margine sopra soglia, e se si manda un **alert Telegram** con score, confidence, perche e rischi.

Bidoo (penny auction) e **disattivato di default** (`INCLUDE_BIDOO=false`): le puntate non sono flip-friendly come un asta classica o un bancale a prezzo fisso.

---

## 2. Come si lancia

| Come | Comando / file | Cosa fa |
|------|----------------|---------|
| Tutto (consigliato) | `python monitor_all.py` | Fonti in `ENABLED_SOURCES` (+ Bidoo solo se `INCLUDE_BIDOO=true`) |
| Un sito | `python monitor_remundo.py` (ecc.) | Solo quella fonte |
| Windows ogni 5 min | `setup-windows-task.ps1` -> `run-check.ps1` | Chiama `monitor_all.py` |
| GitHub Actions cloud | workflow **Resale Monitor (cloud)** | `monitor_all.py` su Ubuntu (WAF possibile) |
| GitHub Actions casa | workflow **Resale Monitor (self-hosted)** | Stesso, ma IP di casa |

Secrets obbligatori su GitHub: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

### Perche prima vedevi solo Bidoo

Il workflow cloud (`monitor.yml`) chiamava ancora:

```text
python monitor.py --once
```

cioe **solo Bidoo**. Ora chiama `monitor_all.py` con:

```text
ENABLED_SOURCES=prezzishock,catawiki,gobid,astagiudiziaria,industrial_discount,remundo
INCLUDE_BIDOO=false
```

Dopo il push: Actions -> **Resale Monitor (cloud)** -> **Run workflow**.

---

## 3. Flusso dati (pipeline)

```text
[Sito web] -> sources/*.py -> SourceListing
                |
         classic_monitor.run_source
                |
    filtri profilo + pesanti + hyper + spedibilita
                |
         classic_estimator.estimate_classic
           (comps, brand, feedback, budget, score)
                |
         verdict == conviene && score >= MIN
                |
         Telegram (build_classic_alert)
```

### File chiave

| File | Ruolo |
|------|--------|
| `monitor_all.py` | Orchestratore multi-sito |
| `classic_monitor.py` | Un giro per fonte: fetch -> stima -> alert |
| `classic_estimator.py` | Costo all-in, canali eBay/Vinted/Subito, score, budget |
| `listing.py` | Modello `SourceListing` normalizzato |
| `site_profiles.py` | Premio, ritiro, haircut, note Telegram per sito |
| `sources/*.py` | Scraping / API read-only per catalogo |
| `http_fetch.py` | HTTP + Playwright opzionale |
| `brands.py` | Marca + allowlist premium (+30) |
| `comps.py` + `data/comps.csv` | Prezzi medi eBay/Vinted locali (stdev) |
| `flip_rules.py` | Spedibilita, keyword, Catawiki, allowlist categorie |
| `photo_check.py` | HEAD / dimensioni foto (se `image_url` presente) |
| `feedback.py` | Visto / ignorato / comprato / venduto |
| `site_cooldown.py` | Fetch piu lento/veloce in base agli alert |
| `money.py` | Parse euro, categoria da titolo, tempo rimanente |
| `filters.py` | Esclusioni + iper-competitivi |
| `telegram_notifier.py` | Invio messaggio HTML |
| `update_comps.py` | Aggiorna CSV comps (~7 giorni) |
| `record_feedback.py` | CLI: segna ignorato/comprato/venduto |

---

## 4. Fonti dati (cosa prende e da dove)

### Abilitate di default (`ENABLED_SOURCES`)

| Chiave | Sito | Cosa legge | Dati tipici | Note |
|--------|------|------------|-------------|------|
| `prezzishock` | PrezziShock | tabelle aste **ending** | titolo, prezzo, countdown | Solo in chiusura |
| `catawiki` | Catawiki | HTML / NEXT_DATA (+ Playwright) | bid, stima, riserva | 3 query flip; Akamai da cloud |
| `gobid` | Gobid | HTML (+ Playwright), prezzo solo nodi € | titolo, prezzo | Cauzione per categoria |
| `astegiudiziarie` | astegiudiziarie.it | API Search/Map + XML ministero | prezzo base, titolo, scadenza offerte | Solo mobili: abbigliamento, orologi, elettronica |
| `astagiudiziaria` | Astagiudiziaria | catalogo JS / JSON-LD | titolo, prezzo | Ritiro sede; cauzione IVG |
| `industrial_discount` | Industrial Discount | HTML catalogo | titolo, prezzo, date | Skip camion |
| `remundo` | remundo.it | Shopify `products.json` | bancali | Niente filtro scadenza |

### Presenti nel codice ma NON in ENABLED_SOURCES (e perche)

| Chiave | Perche non e attiva di default |
|--------|--------------------------------|
| `bidoo` | Penny auction + Cloudflare |
| `antiebay` | Rumore, tante aste chiuse |
| `wallapop` | 403/WAF; margine finto senza comps |
| `vinted_source` | Stesso: senza comps sotto-70% **non** alert |
| `subito` | Akamai/403 |
| `ebay_source` | 403 da cloud |
| `surplex` | Industriale, ritiro EU, poca spedibilita box |
| `bstock` | Account + spesso P.IVA |
| `merkandi` | Abbonamento a pagamento |
| `stocklots24` | Membership / prezzi dietro login |

### Comps locali

- File: `data/comps.csv`
- Update: `python update_comps.py` (eBay venduti + Vinted search)
- Regole: stdev > 40% del medio -> scarta (+ score -20); stdev < 25% -> score +15; avg < 15 EUR -> ignora

---

## 5. Filtri (ordine logico)

1. Profilo sito (`extra_exclude` / `extra_include`)
2. Exclude patterns utente + default (voucher, lotteria, ...)
3. Pesanti / veicoli / immobili
4. Non spedibile (mobili, >8 kg, lotto misto/pallet/bancale, ritiro su aste classiche) — eccezione Remundo pallet; giudiziarie non auto-scartate solo per “ritiro sede”
5. Iper-competitivi (iPhone, Galaxy, PS5/PS4, Xbox, AirPods, Dyson Supersonic, …) se `CLASSIC_SKIP_HYPER`, tranne prezzo < 20 €
6. Finestra tempo: aste <= 4h; giudiziarie <= 24h; Remundo nessuna
7. Catawiki: riserva, stima min > 150, spread stima > 2.5, arte/gioielli/orologi premium, bid > 60%
8. Remundo: cap 400 EUR; costo/pezzo; packing list (haircut se manca)
9. Titolo con meno di 3 parole utili / solo marca / solo “lotto stock” → **scarto**
10. Foto: se `image_url` presente e manca / < 300 px / placeholder → scarto; stock → score -20
11. Comps volatili / too cheap; classificato senza comps o prezzo > 70% comps → scarto
12. Feedback: ignora 3x → -20; ignora 5x → blacklist; compra 2x → +20; vendi 1x → +30; vendi 3x → premium
13. Keyword negative eBay vs Vinted
14. Budget per score e per categoria (moda 25, elettronica 60, utensili 40, …)
15. Profitto minimo per categoria (utensili/casa 15, moda/profumi 20, elettronica 25)
16. Margine 25-30 EUR → score -30; margine > 40 EUR → score +20
17. Cooldown: 10+ scarti → ~8h; 0 alert per **7 giorni** → 1 fetch/giorno; **3+ alert in 48h** → ~2h
18. Anti-spam Telegram (`ALERT_COOLDOWN`)

---

## 6. Stima economica

**All-in** = prezzo + premio + inbound + ritiro + cauzione (Gobid/IVG)

**Rivendita** = comps (se validi) **oppure** retail × fattore **solo se 20–200 €** **oppure** prezzo × moltiplicatore. **Annunci usati (Vinted/Wallapop/Subito):** niente moltiplicatore; serve comps e prezzo ≤ 70% della media. Se stima < 15 € → scarto.

**Score 0-100:** profitto, margine, marca premium **+30** / riconosciuta **+10**, comps affidabili **+15** / volatili **-20**, flip-friendly, allowlist (-30), ritiro, condizioni, storico feedback.

**Confidence 0-100** (separata dallo score): marca, comps, spedibilita, margine, titolo. In Telegram: sezioni *perche e buono* / *perche potrebbe essere rischioso*.

---

## 7. Feedback personale

```text
python record_feedback.py ignored --id remundo:123456
python record_feedback.py bought  --id prezzishock:abc --title "..."
python record_feedback.py sold    --id remundo:123456
```

Stato in `.feedback.json` (non in git). Effetti immediati sullo score: ignora 3x marca **-20**, compra 2x **+20**, vendi 1x **+30**.

---

## 8. Variabili .env importanti

| Variabile | Default tipico | Significato |
|-----------|----------------|-------------|
| `TELEGRAM_*` | — | Obbligatori |
| `ENABLED_SOURCES` | remundo,prezzishock,... | Fonti classiche |
| `INCLUDE_BIDOO` | false | Penny auction |
| `USE_PLAYWRIGHT` | true | Catawiki/Gobid/IVG |
| `MIN_RESALE_PROFIT_EUR` | 25 | Profitto netto minimo |
| `MIN_RESALE_SCORE` | 50 | Score minimo |
| `MAX_HOURS_TO_END` | 4 | Solo aste in chiusura |
| `MAX_BUY_OF_OFFICIAL_PCT` | 40 | Tetto vs valore ufficiale |
| `MAX_PALLET_EUR` | 400 | Cap Remundo |
| `GOBID_DEPOSIT_EUR` | 50 | Cauzione stimata |
| `PICKUP_COST_EUR` | 35 | Ritiro se non nel profilo |

Vedi `.env.example`.

---

## 9. Cosa manca da fare (e perche)

| Mancanza | Perche non c'e (ancora) | Impatto |
|----------|-------------------------|---------|
| API ufficiali eBay/Vinted per comps | Finding API serve App ID; Vinted senza API sold pubblica stabile | Comps = CSV + scraping leggero |
| Analisi foto AI / OCR | Solo HEAD/dimensioni leggere se c’e URL | Stock / missing / tiny |
| Offerte automatiche / sniping | Fuori scope e spesso vietato dai ToS | Solo alert |
| B-Stock / Merkandi / Stocklots24 attivi | Login, P.IVA, o abbonamento | Adapter esistono; non in ENABLED_SOURCES |
| Wallapop / Vinted / Subito / eBay-fonte nel default | 403/WAF da cloud | Adapter in repo; riattivabili in `.env` da casa |
| eBay come fonte lotti | Serve `EBAY_APP_ID`; da cloud spesso 403 | `monitor_ebay_source.py` opzionale |
| Prezzi ufficiali Amazon reali | Nessuna API Pricing gratis/legale adatta | Proxy: retail_hint sito o comps |
| Packing list Remundo sempre completa | Body Shopify non sempre la espone | Flag + costo/pezzo + haircut |
| Cloud GitHub = 100% cataloghi | IP datacenter -> Cloudflare/Akamai/WAF | Catawiki/Gobid spesso 0 lotti da cloud |
| Storico vendite Vinted automatico | Account + scraping invasivo | `record_feedback.py sold` a mano |
| UI dashboard | Tool CLI + Telegram | Scelta di semplicita |

---

## 10. Checklist se "non vedo i siti"

1. Nel log deve apparire: `Fonti: prezzishock, catawiki, gobid, astagiudiziaria, industrial_discount, remundo`. Se vedi solo `bidoo` -> vecchio `monitor.py`.
2. Secrets Telegram ok.
3. Dopo il push: **Run workflow** sul job aggiornato.
4. Da cloud: 0 lotti su Catawiki/Gobid / WAF -> normale; prova self-hosted o PC di casa.
5. Filtro <= 4 ore: se niente in chiusura, 0 alert non significa 0 scrape.
6. Locale: `.env` con `INCLUDE_BIDOO=false` e `ENABLED_SOURCES=...`.

---

## 11. Avvertenze

- Uso personale a tuo rischio; rispetta i Termini dei siti.
- Le stime sono euristiche, non consulenza finanziaria.
- Non committare `.env` (token Telegram).
