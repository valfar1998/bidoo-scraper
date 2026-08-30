# Come funziona il Resale Monitor

Documento operativo: cosa fa il tool, da dove prende i dati, filtri, alert, cosa manca e perché.

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
ENABLED_SOURCES=remundo,prezzishock,industrial_discount,catawiki,gobid,astagiudiziaria
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
| `brands.py` | Riconoscimento marca (substring + fuzzy) |
| `comps.py` + `data/comps.csv` | Prezzi medi eBay/Vinted locali |
| `flip_rules.py` | Spedibilita, keyword, Catawiki, allowlist categorie |
| `feedback.py` | Visto / ignorato / comprato / venduto |
| `site_cooldown.py` | Rallenta PrezziShock/Antiebay se troppo rumore |
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
| `remundo` | remundo.it | Shopify `products.json` | titolo, prezzo, retail, pezzi, packing list | Bancali; niente filtro scadenza |
| `prezzishock` | PrezziShock | tabella aste ending | titolo, prezzo, countdown | Solo in chiusura |
| `industrial_discount` | Industrial Discount | HTML catalogo | titolo, prezzo, date | Skip camion; ritiro tipico |
| `catawiki` | Catawiki | HTML / NEXT_DATA (+ Playwright) | bid, stima esperta, riserva, fine | Spesso Akamai da cloud |
| `gobid` | Gobid | HTML (+ Playwright) | titolo, prezzo | WAF; cauzione in all-in |
| `astagiudiziaria` | Astagiudiziaria | catalogo JS | titolo, prezzo, localita | Ritiro sede; cauzione IVG |

### Presenti nel codice ma NON in ENABLED_SOURCES (e perche)

| Chiave | Perche non e attiva di default |
|--------|--------------------------------|
| `bidoo` | Penny auction + Cloudflare; poco flip "box" |
| `antiebay` | Rumore alto (simile PrezziShock) |
| `surplex` | Industriale, ritiro EU, poca spedibilita box |
| `bstock` | Account + spesso P.IVA |
| `merkandi` | Abbonamento a pagamento |
| `stocklots24` | Membership / prezzi dietro login |
| `ebay_source` | Serve `EBAY_APP_ID` (gratis su developer.ebay.com) |

### Comps locali

- File: `data/comps.csv`
- Update: `python update_comps.py` (eBay venduti + Vinted search)
- Regole: stdev > 40% del medio -> scarta; avg < 15 EUR -> ignora

---

## 5. Filtri (ordine logico)

1. Profilo sito (`extra_exclude` / `extra_include`)
2. Exclude patterns utente + default (voucher, lotteria, ...)
3. Pesanti / veicoli / immobili
4. Non spedibile (mobili, >10 kg, industriale, ritiro obbligatorio) — eccezione Remundo pallet
5. Iper-competitivi (iPhone, PS5, Rolex, ...) se `CLASSIC_SKIP_HYPER`
6. Finestra tempo: aste <= 4h; giudiziarie <= 24h; Remundo nessuna
7. Catawiki: riserva non raggiunta; stima > 200 EUR; bid > 60% stima
8. Remundo: cap 400 EUR; costo/pezzo; packing list (haircut se manca)
9. Comps volatili / troppo cheap
10. Feedback adattivo (dopo 20 lotti visti): marca ignorata 3+ volte -> scarta
11. Keyword negative eBay vs Vinted
12. Budget dinamico (score, moda 5-25 EUR, 40% ufficiale, pallet 400)
13. Profitto >= 20 EUR, margine >= 25%, score >= 50
14. Titolo vago / margine al filo -> score -50
15. Cooldown PrezziShock/Antiebay: 10+ scarti -> scrape ogni 8h
16. Anti-spam Telegram (`ALERT_COOLDOWN`)

---

## 6. Stima economica

**All-in** = prezzo + premio + inbound + ritiro + cauzione (Gobid/IVG)

**Rivendita** = comps (se validi) oppure retail x fattore sito oppure prezzo x moltiplicatore
meno haircut, per fattore canale, meno fee e spedizione outbound.

**Score / Confidence** 0-100: profitto, margine, marca (+20), flip-friendly, storico, allowlist (-30), ritiro, condizioni, titolo vago (-50), ...

---

## 7. Feedback personale

```text
python record_feedback.py ignored --id remundo:123456
python record_feedback.py bought  --id prezzishock:abc --title "..."
python record_feedback.py sold    --id remundo:123456
```

Stato in `.feedback.json` (non in git). Dopo 20 `seen`, i filtri si adattano.

---

## 8. Variabili .env importanti

| Variabile | Default tipico | Significato |
|-----------|----------------|-------------|
| `TELEGRAM_*` | — | Obbligatori |
| `ENABLED_SOURCES` | remundo,prezzishock,... | Fonti classiche |
| `INCLUDE_BIDOO` | false | Penny auction |
| `USE_PLAYWRIGHT` | true | Catawiki/Gobid/IVG |
| `MIN_RESALE_PROFIT_EUR` | 20 | Profitto netto minimo |
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
| Qualita foto nello score | Non analizziamo immagini in modo affidabile/leggero | Solo titoli vaghi |
| Offerte automatiche / sniping | Fuori scope e spesso vietato dai ToS | Solo alert |
| B-Stock / Merkandi / Stocklots24 attivi | Login, P.IVA, o abbonamento | Adapter esistono; non in ENABLED_SOURCES |
| eBay come fonte lotti | Serve `EBAY_APP_ID` | Vedi `GUIDA_EBAY_DEVELOPER.md` |
| Prezzi ufficiali Amazon reali | Nessuna API Pricing gratis/legale adatta | Proxy: retail_hint sito o comps |
| Packing list Remundo sempre completa | Body Shopify non sempre la espone | Flag + costo/pezzo + haircut |
| Cloud GitHub = 100% cataloghi | IP datacenter -> Cloudflare/Akamai/WAF | Catawiki/Gobid spesso 0 lotti da cloud |
| Storico vendite Vinted automatico | Account + scraping invasivo | `record_feedback.py sold` a mano |
| UI dashboard | Tool CLI + Telegram | Scelta di semplicita |

---

## 10. Checklist se "non vedo i siti"

1. Nel log deve apparire: `Fonti: remundo, prezzishock, industrial_discount, catawiki, gobid, astagiudiziaria`. Se vedi solo `bidoo` -> vecchio `monitor.py`.
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
