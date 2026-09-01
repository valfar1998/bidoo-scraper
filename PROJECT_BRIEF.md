# PROJECT BRIEF — Bidoo Scraper (Resale Monitor)

> **Regola:** aggiornare questo file dopo ogni modifica architetturale o funzionale rilevante.

## Visione

Monitor **multi-sito** → stima rivendita → **alert operativi** con Max Bid, inventario post-acquisto e loop di apprendimento. Gestione **zero-CLI** via Telegram.

Obiettivo: **strumento operativo di arbitraggio**, non solo notifiche passive.

## Stack

Python 3.12 · SQLite · Telegram (inline + comandi) · Gemini (vision + embeddings) · GitHub Actions (discovery + sniper)

## Pipeline operativa

```text
[Discovery ogni 2h]  → catalog_listings (DB)
[Sniper ogni 5 min]  → solo lotti in chiusura ≤2h → stima → alert
       │
estimate_classic
  · semantic comps (embeddings)
  · shipping_matrix
  · bidding_velocity
  · lot_unbundler (manifest → articoli + canale)
  · capital_allocator (cap portafoglio / categoria)
  · compute_recommended_max_bid (ROI ≥ MIN_NET_ROI_PCT)
       │
Telegram: Max Bid + margini + [Comprato] → inventory + bozze + [Pubblica su eBay]
       │
/sold <id> <prezzo> → Precision Score + /tax_report
       │
Job settimanale repricer → /reprice suggerito
```

## Moduli chiave

| Modulo | Ruolo |
|--------|--------|
| `classic_estimator.py` | `recommended_max_bid_eur`, ROI/profitto a max bid |
| `inventory.py` | Schede acquisto/vendita, precision score, calibrazione haircut |
| `catalog_store.py` | Catalogo lotti con `ends_at` per smart polling |
| `smart_polling.py` | CLI discovery / sniper |
| `comp_embeddings.py` | Match comps semantico (Gemini + TF-IDF fallback) |
| `capital_allocator.py` | Capitale attivo, limiti categoria/brand, `/portfolio` |
| `lot_unbundler.py` | Scomposizione manifest → Vinted/eBay/Subito per articolo |
| `listing_generator.py` | Bozze annuncio copia-incolla su `[Comprato]` |
| `proxy_health.py` | Quality score proxy per dominio, failover FlareSolverr |
| `inventory_repricer.py` | Markdown dinamico lotti giacenti (job settimanale) |
| `ebay_sell.py` | eBay Inventory API — bozza/pubblicazione diretta |
| `tax_reporter.py` | Profitto netto post-tasse, export CSV commercialista |
| `telegram_topics.py` | Routing alert su topic Telegram per categoria/rischio |
| `telegram_bot.py` | `/stats` `/sold` `/portfolio` `/reprice` `/tax_report` |

### Moduli pianificati (roadmap)

| Modulo | Ruolo |
|--------|--------|
| `bidding_engine.py` | Snipe live: offerta programmatica negli ultimi secondi d'asta |
| `tests/test_e2e_pipeline.py` | Test integrazione discovery → stima → payload eBay (dati fittizi) |

## Max Bid consigliato

Formula integrata in `compute_recommended_max_bid()`:
- Considera premio asta, shipping matrix, fee marketplace, haircut categoria
- Garantisce **ROI netto ≥ `MIN_NET_ROI_PCT`** (default 35%) **e** profitto ≥ `MIN_EXPECTED_PROFIT_EUR` (50 €)
- Mostrato in alert come sezione **MAX BID (rilancio)** con margine rilancio residuo

## Inventario post-acquisto

Tabelle: `inventory`, `alert_snapshots`

1. Alert inviato → snapshot stima salvato
2. `[Comprato]` → scheda `pending` in inventario + **bozze annuncio** (titolo SEO, descrizione, prezzi rapido/max)
3. `/sold remundo:123 89.50` → profitto reale vs stimato
4. `/precision` → Precision Score (% entro ±20%, MAE €)
5. `/portfolio` → capitale attivo, esposizione per categoria, budget residuo
6. Auto-tuning: `category_haircut_adjustment()` applicato in `classic_estimator` *(oggi: delta fisso; roadmap: regressione su `/sold`)*
7. **Pianificato:** stati `returned` / `disputed`, colonna `refund_amount_eur`, comandi `/return` e `/dispute`

## Allocazione capitale

`capital_allocator.py` traccia il capitale in lotti `pending` (`inventory.buy_price_eur`).

| Controllo | Env | Default |
|-----------|-----|---------|
| Tetto portafoglio | `MAX_ACTIVE_CAPITAL_EUR` | 2000 € |
| Cap categoria testata | `MAX_CATEGORY_EXPOSURE_PCT` | 50% |
| Cap categoria non testata | `MAX_UNTESTED_CATEGORY_EXPOSURE_PCT` | 30% |
| Categorie non testate | `UNTESTED_CATEGORIES` | elettronica,smartwatch,utensili |
| Cap singolo brand | `MAX_BRAND_EXPOSURE_PCT` | 25% |

Se il budget categoria è esaurito → alert con **"Capitale di categoria saturo"** e deal non viable.

## Spacchettamento lotti

`lot_unbundler.py` — Gemini (o euristica) decompone manifest OCR in articoli con:
- Canale ottimale: Vinted (moda), eBay (tech), Subito (ingombranti)
- Prezzo vendita rapida (≤7 gg) vs profitto max (≤30 gg)
- Stima rivendita lotto = somma `total_max_eur` se superiore alla stima globale

## Proxy per dominio

`proxy_health.py` — tabella `proxy_domain_stats`:
- Traccia latenza, 403/429 per dominio (es. catawiki.com)
- Escalation: direct → proxy → proxy_alt → FlareSolverr
- Integrato in `http_fetch.SessionFetcher.get_text()` senza bloccare altre fonti nel ciclo sniper
- **Pianificato:** sessioni autenticate e token di login persistenti per siti target (con `http_fetch.py`)

`ROTATING_PROXY_URL_ALT` — nodo proxy alternativo per failover.

## Repricing inventario

`inventory_repricer.py` — job settimanale (`inventory-repricer.yml`):
- Analizza lotti `pending` con giacenza > `REPRICER_STALE_DAYS` (14 gg)
- Riesegue `comp_embeddings` + Browse API fixed-price per prezzo mercato
- Se mercato sceso o giacenza critica → alert Telegram con `/reprice <id> <€>`

## eBay direct listing

`ebay_sell.py` — Sell Inventory API (OAuth user `EBAY_USER_REFRESH_TOKEN`):
- Pulsante inline **[Pubblica su eBay]** sotto bozza annuncio
- Crea inventory item + offer (bozza o live con `EBAY_AUTO_PUBLISH=true`)
- Richiede policy IDs e `EBAY_MERCHANT_LOCATION_KEY`

## Report fiscale

`tax_reporter.py` — `/tax_report [YYYY-MM]`:
- Regimi: `forfettario`, `margine` (IVA sul margine), `ordinario`
- CSV in `data/reports/tax_report_YYYY_MM.csv` inviato su Telegram
- Colonne: acquisto, vendita, profitto lordo, imponibile, tasse, netto
- **Pianificato:** detrazione perdite da resi/rimborsi (`refund_amount_eur`) in imponibile e netto

## Telegram multi-topic

`telegram_topics.py` — supergruppo forum con `message_thread_id`:
- `#elettronica-alta-confidence` → `TELEGRAM_TOPIC_ELECTRONICS`
- `#bancali` → `TELEGRAM_TOPIC_PALLET`
- `#lotti-rischiosi` → bassa confidence / molti risk
- Ops inventario/repricing/tax → topic dedicati
- **Pianificato:** `#resi-e-contestazioni` per alert resi e dispute

`TELEGRAM_TOPICS_ENABLED=true` + ID topic nel `.env`.

## Smart polling (2 velocità)

| Modalità | `MONITOR_MODE` | Frequenza | Cosa fa |
|----------|----------------|-----------|---------|
| Discovery | `discovery` | ogni 2h | Fetch catalogo → `catalog_listings`, no alert |
| Sniper | `sniper` | ogni 5 min | Solo lotti con scadenza ≤ `SNIPER_WINDOW_HOURS` (2h) |
| Full | `full` | legacy | Fetch completo + alert (aggiorna anche catalogo) |

```powershell
python smart_polling.py discovery
python smart_polling.py sniper
MONITOR_MODE=sniper python monitor_all.py
```

Workflow GitHub: `catalog-discovery.yml` (ogni 2h), `sniper-watch.yml` (ogni 5 min).

## Semantic comps

`comp_embeddings.py` — embeddings Gemini (`text-embedding-004`) con cache in `comp_embeddings` table.  
Fallback TF-IDF locale. Integrato in `market_lookup.resolve_comp()` prima del match stringa.

`SEMANTIC_COMPS=true`, soglia `SEMANTIC_COMPS_THRESHOLD=0.72`.

## Telegram Ops

| Comando | Azione |
|---------|--------|
| `/stats [ore]` | Hit rate, margine per fonte |
| `/pause <fonte> <ore>` | Cooldown manuale |
| `/force_sync` | Sync comps venduti |
| `/sold <id> <€>` | Chiudi inventario |
| `/precision` | Accuratezza algoritmo |
| `/portfolio` | Capitale attivo e limiti categoria |
| `/reprice <id> <€>` | Aggiorna listino inventario |
| `/tax_report [YYYY-MM]` | Report fiscale + CSV |
| `/return <id> [importo]` | *(pianificato)* Registra reso e rimborso |
| `/dispute <id>` | *(pianificato)* Apre contestazione post-vendita |
| `[Comprato]` | Inventario + bozze + [Pubblica su eBay] |

## Database (SQLite)

Oltre alle tabelle esistenti: `catalog_listings`, `inventory`, `alert_snapshots`, `comp_embeddings`, `proxy_domain_stats`.

**Pianificato su `inventory`:** stati `returned`, `disputed`; colonna `refund_amount_eur`.

## Comandi

```powershell
python monitor_all.py                    # MONITOR_MODE=full|discovery|sniper
python smart_polling.py discovery
python smart_polling.py sniper
python telegram_bot.py                   # polling comandi + feedback
```

## Variabili .env chiave

| Variabile | Default | Descrizione |
|-----------|---------|-------------|
| `MONITOR_MODE` | full | full / discovery / sniper |
| `SNIPER_WINDOW_HOURS` | 2 | Finestra sniper |
| `MIN_NET_ROI_PCT` | 35 | Target ROI per Max Bid |
| `MIN_EXPECTED_PROFIT_EUR` | 50 | Profitto minimo Max Bid |
| `SEMANTIC_COMPS` | true | Embeddings per comps |
| `SEMANTIC_COMPS_THRESHOLD` | 0.72 | Soglia similarità |
| `MAX_ACTIVE_CAPITAL_EUR` | 2000 | Tetto capitale in lotti pending |
| `MAX_UNTESTED_CATEGORY_EXPOSURE_PCT` | 30 | Cap % su categorie senza vendite |
| `CAPITAL_ALLOCATOR` | true | Abilita controllo portafoglio |
| `LOT_UNBUNDLER` | true | Scomposizione manifest lotti |
| `PROXY_HEALTH` | true | Failover proxy per dominio |
| `ROTATING_PROXY_URL_ALT` | — | Proxy alternativo failover |
| `REPRICER_STALE_DAYS` | 14 | Giacenza prima del markdown |
| `TAX_REGIME` | forfettario | Regime fiscale report |
| `TELEGRAM_TOPICS_ENABLED` | false | Routing forum topics |
| `EBAY_USER_REFRESH_TOKEN` | — | OAuth venditore eBay |
| `DRY_RUN` | false | *(pianificato)* Simula ingestione/stima/snipe senza acquisti né scritture DB prod |

## GitHub Actions

| Workflow | Schedule |
|----------|----------|
| `catalog-discovery.yml` | `0 */2 * * *` |
| `sniper-watch.yml` | `*/5 * * * *` |
| `sold-comps-sync.yml` | dom 03:00 (+ backup DB) |
| `health-digest.yml` | lun 09:00 |
| `inventory-repricer.yml` | lun 10:00 |

## Roadmap — prossime implementazioni

### Automazione checkout e snipe live

- **`bidding_engine.py`** — modulo per l'invio programmatico dell'offerta finale negli ultimi secondi dell'asta (snipe live).
- **`http_fetch.py` + `proxy_health.py`** — estensione per sessioni autenticate e token di login persistenti sui siti target.
- **Playwright** — automazione del flusso di pagamento e checkout post-vittoria (dopo aggiudicazione).

> Nota compliance: verificare sempre i Termini d'uso di ogni piattaforma; snipe/checkout automatico può violare le regole del sito.

### Dynamic haircut e learning loop

- **`inventory.py`** — sostituire il delta fisso in `category_haircut_adjustment()` con regressione statistica locale sui dati storici di `/sold`.
- **`classic_estimator.py`** — applicazione dinamica dei nuovi coefficienti di rischio per ricalcolare `recommended_max_bid_eur` e ROI target.

### Gestione resi, contestazioni e post-vendita

- **Schema SQLite** — stati `returned`, `disputed`; colonna `refund_amount_eur` su `inventory`.
- **`telegram_bot.py`** — comandi `/return <id> [importo]` e `/dispute <id>`.
- **`tax_reporter.py`** — imponibile e netto nei CSV aggiornati per detrarre perdite da resi e rimborsi.
- **`telegram_topics.py`** — topic `#resi-e-contestazioni`.

### Modalità dry-run e test E2E

- **`smart_polling.py` + `monitor_all.py`** — env `DRY_RUN=true`: simula ingestione, stima e snipe senza acquisti né modifiche al DB di produzione.
- **`tests/test_e2e_pipeline.py`** — suite integrazione end-to-end con dati fittizi: discovery → stima → payload eBay.

## Changelog architettura

| Data | Modifica |
|------|----------|
| 2026-08-31 | SQLite, ROI dinamico, vision, Telegram ops, backup, health |
| 2026-08-31 | **Max Bid consigliato** (ROI-integrated) in alert |
| 2026-08-31 | **Inventario** + `/sold` + Precision Score + haircut auto-tune |
| 2026-08-31 | **Smart polling** discovery/sniper + workflows dedicati |
| 2026-08-31 | **Semantic comps** (Gemini embeddings + TF-IDF) |
| 2026-08-31 | **Repricing**, **eBay Sell API**, **tax report**, **Telegram topics** |
| 2026-09-01 | **Roadmap:** snipe live (`bidding_engine`), sessioni auth, checkout Playwright, haircut regressivo, resi/dispute, `DRY_RUN`, test E2E |
