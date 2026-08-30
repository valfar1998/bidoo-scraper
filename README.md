# Resale Monitor (Telegram)

Monitor multi-sito: legge cataloghi (Remundo, PrezziShock, Catawiki, Gobid, …), stima rivendita su **eBay / Vinted / Subito**, invia alert Telegram. **Non punta e non compra.**

> Guida completa (flusso, filtri, fonti, cosa manca): **[COME_FUNZIONA.md](COME_FUNZIONA.md)**  
> Dettaglio tecnico (cosa fa ogni riga di pipeline, cosa non funziona e perché): **[DETTAGLIO_FUNZIONAMENTO.md](DETTAGLIO_FUNZIONAMENTO.md)**

## Perché questo approccio ha senso

Su Bidoo non conviene competere negli ultimi secondi (timer sempre corto, troppa competizione). Conviene:

1. **Entrare presto** su aste poco seguite in categorie di nicchia
2. **Stimare il margine reale** (prezzo asta + puntate + spedizione + commissioni)
3. **Rivendere** dove c'è domanda (Vinted per moda/bambini/casa, eBay per orologi/tecnica)

Bidoo è **OFF di default** (`INCLUDE_BIDOO=false`). Il focus è aste classiche / bancali / fallimenti.

## Categorie preconfigurate (poco battute)

| Categoria | Tag Bidoo | Piattaforma | Perché |
|-----------|-----------|-------------|--------|
| Prima infanzia e giocattoli | `prima-infanzia` | Vinted | LEGO, Chicco: alta rotazione |
| Animali domestici | `animali_domestici` | Vinted/eBay | Nicchia poco seguita |
| Piccoli elettrodomestici | `elettrodomestici` | Vinted/eBay | Kenwood, Smeg (filtra smartphone) |
| Bellezza | `bellezza` | Vinted | Piastre, epilatori mid-range |
| Orologi | `orologi` | eBay | Fossil, Casio entry |
| Sport e fitness | `sport` | Vinted/eBay | Accessori, non wearable premium |
| Casa | `casa` | Vinted | Piccoli elettrodomestici casa |

**Esclusi automaticamente:** smartphone, console, Apple, iPad, PS5, Rolex, luxury — troppo battuti su Bidoo.

## Tipi di alert

| Tipo | Quando | Utilità |
|------|--------|---------|
| `new` | Prima volta che vedi l'asta | Entrare presto su prodotti appena pubblicati |
| `quiet` | Prezzo quasi fermo dopo N controlli | Segnale di bassa competizione |
| `deal` | Margine netto stimato sopra soglia | Occasione con numeri che tornano |

Ogni messaggio include:
- **Rivendita stimata** su Vinted/eBay/Subito e max bid / budget
- **Score** e **Confidence 0–100** (marca, comps, spedibilità, margine, titolo)
- Sezioni **perché è buono** / **perché rischioso**
- Profitto netto soglia default **≥ 25 €**
- Link diretto al lotto

## Setup

```bash
pip install -r requirements.txt
copy .env.example .env
# compila TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID
```

## Altri siti (aste classiche / stock)

Ogni fonte ha uno script. L’alert confronta **sempre** tre canali di vendita: eBay, Vinted, Subito (fee, spedizione, premio acquirente, ritiro, scarto lotti).

```powershell
python monitor_prezzishock.py
python monitor_antiebay.py
python monitor_catawiki.py
python monitor_astagiudiziaria.py
python monitor_gobid.py
python monitor_industrial_discount.py
python monitor_all.py          # ENABLED_SOURCES (+ Bidoo solo se INCLUDE_BIDOO=true)
.\run-check-all.ps1
```

Script opzionali (disattivati di default per 403/WAF da cloud): `monitor_wallapop.py`, `monitor_vinted_source.py`, `monitor_subito.py`, `monitor_ebay_source.py`.

| Script | Sito | Credenziali | Note |
|--------|------|-------------|------|
| `monitor.py` | Bidoo | nessuna per leggere | OFF di default; Cloudflare da datacenter |
| `monitor_prezzishock.py` | PrezziShock | registrazione gratis **per offrire** | Catalogo pubblico, asta classica |
| `monitor_antiebay.py` | Antiebay | registrazione gratis per offrire | Molti annunci già chiusi |
| `monitor_catawiki.py` | Catawiki | account gratis per offrire; premio ~9–12%+IVA | Spesso Akamai: `USE_PLAYWRIGHT=true` |
| `monitor_astagiudiziaria.py` | Astagiudiziaria | MyAsta gratis (email); **cauzione IVG** per offrire | Catalogo JS; ritiro in sede |
| `monitor_gobid.py` | Gobid | registrazione + **deposito cauzionale** per asta | WAF; beni mobili da procedure |
| `monitor_surplex.py` | Surplex | account gratis per offrire | Lotti industriali, ritiro EU |
| `monitor_industrial_discount.py` | Industrial Discount | registrazione gratis per Proxy Bid | Filtra autocarri/macchinari pesanti |
| `monitor_bstock.py` | B-Stock | **BSTOCK_EMAIL / PASSWORD** | Pallet resi; 15–20% scarto già in stima |
| `monitor_merkandi.py` | Merkandi | **abbonamento** + email/password | Non è un’asta, è B2B a prezzo fisso |
| `monitor_stocklots24.py` | Stocklots24 | registrazione consigliata | Prezzi pieni spesso dopo login |
| `monitor_ebay_source.py` | eBay (fonte lotti) | **EBAY_APP_ID** (opzionale) | Non nel default (403 da cloud) |

`ENABLED_SOURCES` di default: remundo, prezzishock, antiebay, industrial_discount, catawiki, gobid, astagiudiziaria.  
Documentazione filtri/score: [COME_FUNZIONA.md](COME_FUNZIONA.md) · [DETTAGLIO_FUNZIONAMENTO.md](DETTAGLIO_FUNZIONAMENTO.md).

## Uso

### Controllo singolo (consigliato — ogni 5 min)

```bash
python monitor_all.py
```

Solo Bidoo (sconsigliato): `python monitor.py --once`.

Oppure doppio click / Pianificatore Windows (`setup-windows-task.ps1` → `run-check.ps1` → `monitor_all.py`).

### Loop continuo (PC acceso)

```powershell
.\run-watch.ps1
```

## Automazione ogni 5 minuti

### Consigliato — Pianificatore Windows

```powershell
# PowerShell COME AMMINISTRATORE
powershell -ExecutionPolicy Bypass -File .\setup-windows-task.ps1
```

Esegue `run-check.ps1` dal **tuo IP di casa**. Il PC deve essere acceso.

> **Nota:** l'alert `quiet` richiede almeno 2 controlli (es. 10 minuti con intervallo 5 min) per rilevare poca attività.

### GitHub Actions

GitHub Actions **può** automatizzarlo, ma con limiti importanti.

#### Ogni minuto? No — e non solo per scelta

| Limite | Dettaglio |
|--------|-----------|
| **Cron minimo GitHub** | `*/5 * * * *` = ogni **5 minuti**. Non puoi schedulare ogni 1 minuto |
| **Cloud runner** | IP datacenter → Cloudflare blocca Bidoo → **quasi mai funziona** |
| **7 categorie per giro** | Un run può durare 1–3 minuti; sotto i 5 min rischi sovrapposizioni |
| **Alert `quiet`** | Serve almeno 2 run distinti → con 5 min = ~10 min minimo |

Per questo progetto **5 minuti è l'intervallo giusto**: non stai snipando negli ultimi secondi, stai scoprendo aste nuove o tranquille.

#### Opzione A — Cloud (multi-sito; WAF possibile)

Workflow: `.github/workflows/monitor.yml` → **Resale Monitor (cloud)**

1. Repo → **Settings → Secrets and variables → Actions**
2. Aggiungi:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
3. **Actions** → "Resale Monitor (cloud)" → **Run workflow**

Esegue `monitor_all.py` (Remundo, PrezziShock, …). Catawiki/Gobid da IP GitHub possono dare **0 lotti** (Akamai/WAF). Remundo/PrezziShock di solito rispondono meglio.

#### Opzione B — Self-hosted runner (consigliata su GitHub)

Workflow: `.github/workflows/monitor-self-hosted.yml` → **Resale Monitor (self-hosted)**  
Il job gira sul **tuo PC** (IP di casa), come il Pianificatore Windows.

1. Repo → **Settings → Actions → Runners → New self-hosted runner**
2. Scegli **Windows**, segui i comandi di installazione sul tuo PC
3. Il runner deve restare **online** (PC acceso)
4. Aggiungi i secrets Telegram come sopra
5. Disabilita o ignora il workflow cloud se usi solo questo
6. Il cron `*/5 * * * *` parte automaticamente

Il workflow salva `.auction_history.json`, feedback e comps in cache tra un run e l’altro.

#### Opzione C — Pianificatore Windows (più semplice di GitHub)

Se non ti serve GitHub, `setup-windows-task.ps1` fa la stessa cosa senza runner:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup-windows-task.ps1
```

Puoi anche impostare **ogni 2–3 minuti** nel Pianificatore (GitHub non lo permette).

#### Vuoi controlli più frequenti?

| Metodo | Intervallo minimo realistico |
|--------|------------------------------|
| GitHub Actions cron | 5 min |
| Pianificatore Windows | 1–2 min (sconsigliato sotto i 3) |
| `run-watch.ps1` in locale | `POLL_INTERVAL=120` nel `.env` |

Sotto i 3 minuti rischi rate-limit Bidoo e run sovrapposti senza guadagno reale.

### GitHub Actions cloud ≠ affidabile

Bidoo blocca gli IP datacenter (Cloudflare). Usa il PC di casa o un runner self-hosted.

## Configurazione (.env)

| Variabile | Default | Descrizione |
|-----------|---------|-------------|
| `RESALE_CATEGORIES` | preset | Tag Bidoo separati da virgola |
| `MIN_RESALE_PROFIT_EUR` | 25 | Profitto netto minimo stimato (hard floor anche in codice) |
| `MIN_RESALE_MARGIN_PCT` | 25 | Margine % minimo sul costo totale |
| `MIN_RESALE_SCORE` | 50 | Score rivendita minimo (0–100) |
| `ENABLED_SOURCES` | remundo,prezzishock,… | Fonti classiche (no Wallapop/Vinted/Subito/eBay di default) |
| `ALERT_KINDS` | deal | Tipi di notifica |
| `QUIET_MIN_OBSERVATIONS` | 2 | Controlli minimi per asta tranquilla |
| `QUIET_MAX_PRICE_DELTA_CENTS` | 15 | Max movimento prezzo (cent) |
| `BID_COST_ESTIMATE` | 0.20 | Stima €/puntata |
| `SHIPPING_COST_EUR` | 8 | Buffer spedizione nel calcolo |
| `ALERT_COOLDOWN` | 1800 | Anti-spam (secondi) |

## Strategia rivendita

1. **Verifica sempre** i prezzi venduti su Vinted/eBay prima di puntare
2. **Imposta un tetto** di puntate (usa AutoPuntata ufficiale Bidoo con limite)
3. **Preferisci aste `quiet`** — meno rilanci = meno costo puntate
4. **Conta il costo reale:** prezzo asta + puntate spese, non solo il "valore" Bidoo
5. **Compralo Ora** a volte conviene se hai già speso molte puntate

## Avvertenza Termini Bidoo

I [Termini e Condizioni](https://it.bidoo.com/terms.php) vietano software per puntare automaticamente e citano tra gli usi non autorizzati il **software di monitoraggio dell'offerta**. Uso personale a tuo rischio.
