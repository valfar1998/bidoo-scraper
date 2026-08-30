# Cosa fa esattamente il programma (dettaglio operativo)

Questo file descrive il comportamento **reale** del codice, sito per sito: cosa scarica, come lo trasforma, quando scarta, e **cosa non può funzionare** (e perché). Non è marketing: è la mappa per debug.

Documento più corto: `COME_FUNZIONA.md`.

---

## 0. Cosa NON è

- Non punta, non compra, non fa sniping, non accede ad account venditore per inserire offerte.
- Non è un’API ufficiale dei marketplace (salvo eBay Finding se imposti `EBAY_APP_ID`).
- Non garantisce che un “Catalogo: N” diventi un alert Telegram. N è “lotti letti”; l’alert è un sottoinsieme dopo filtri economici.

---

## 1. Avvio: un giro completo

Punto di ingresso consigliato: `python monitor_all.py` (o GitHub Actions / `run-check.ps1`).

1. Legge `.env` (`python-dotenv`). Su Actions le variabili arrivano dal workflow, **non** dal tuo `.env` locale.
2. Costruisce la lista fonti da `ENABLED_SOURCES` (se vuota: `DEFAULT_ENABLED_SOURCES` in `site_profiles.py`).
3. Bidoo parte **solo** se `INCLUDE_BIDOO=true` (default `false`).
4. Per ogni fonte chiama `classic_monitor.run_source(nome)`.
5. Se una fonte lancia eccezione, `monitor_all.py` stampa l’errore e passa alla successiva (exit code 1 a fine giro se ci sono errori).

Ordine attuale (voluto): prima siti HTTP più “aperti”, poi classificati, **in fondo** Catawiki / Gobid / Astagiudiziaria (WAF lenti). Se il job da 35 minuti scade, le fonti in coda possono non girare.

---

## 2. Un sito: `run_source` passo-passo

File: `classic_monitor.py`.

1. Controlla Telegram (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`). Senza, esce.
2. `should_skip(source)` (`site_cooldown.py`): 10 scarti senza alert → skip ~8h; 0 alert per **7 giorni** → fetch 1 volta/giorno; **3+ alert in 48h** → fetch ~2h.
3. Stampa profilo economico (premio, ritiro, ecc. da `site_profiles.py`).
4. Carica:
   - `data/comps.csv` (prezzi medi eBay/Vinted locali);
   - `.feedback.json` (visto / ignorato / comprato / venduto).
5. Apre **un** `SessionFetcher` (`http_fetch.py`) per tutto il catalogo di quella fonte.
6. `sources.fetch_source(chiave)` → lista di `SourceListing` (titolo, url, prezzo, tempo, extra).
7. Filtro tempo:
   - aste classiche: solo `remaining` tra 0 e `MAX_HOURS_TO_END` (default 4h). **Senza countdown nel HTML il lotto sparisce** (non “unknown”).
   - giudiziarie (`industrial_discount`, `gobid`, `astagiudiziaria`): finestra 24h **oppure** tiene i lotti **senza** data (`keep_unknown=True`).
   - Remundo (bancali): **nessun** filtro scadenza.
8. Per ogni listing: storico prezzi (`AuctionHistory`), `feedback.record_seen`, `pick_classic` (filtri + stima).
9. Se passa: Telegram, anti-spam `ALERT_COOLDOWN` sullo stesso `source:id`.
10. Salva feedback, cooldown, history, `.alert_state.json`.

`pick_classic` scarta **prima** della stima se: prezzo ≤ 0, profilo exclude/include, `EXCLUDE_PATTERNS`, oggetto pesante, non spedibile (tranne bancali), iper-competitivo (iPhone/PS5/Galaxy/AirPods/… tranne prezzo < 20 € e pallet).

Poi `estimate_classic`: se `verdict != conviene` o `score < MIN_RESALE_SCORE` → scarto. Questo è il motivo tipico di “Catalogo 33, alert 0”.

---

## 3. Come si scarica una pagina (il cuore “ricezione dati”)

File: `http_fetch.py`.

### 3.1 HTTP (`requests.Session`)

- Una sessione per fonte: **cookie persistenti** tra homepage e catalogo.
- Header tipo Chrome 124 (Accept-Language it, Sec-CH-UA, gzip).
- `warm(url)`: visita la home prima del catalogo (cookie / session id).
- Ogni GET: fino a `FETCH_RETRIES` (default 3) con pausa crescente.
- Se status **403 / 429 / 503** → retry, poi Playwright se `USE_PLAYWRIGHT=true`.
- Se il body è una **pagina muro** (Cloudflare “ci siamo quasi”, Akamai, Access Denied, captcha, HTML troppo corto) → stesso percorso, anche con HTTP 200.

### 3.2 Playwright (stesso fetcher, browser **riusato**)

- Un Chromium per tutta la fonte, non un browser nuovo per ogni URL (prima si lanciava N volte: lento e spesso il job moriva su Catawiki).
- `navigator.webdriver` mascherato; locale `it-IT`, fuso `Europe/Rome`.
- Attesa `PLAYWRIGHT_WAIT_MS` (default 4500) + fino a ~12s se la pagina è ancora un challenge.
- Timeout goto: `PLAYWRIGHT_GOTO_MS` (default 35s).

### 3.3 JSON

- Retry HTTP; se fallisce, Playwright apre l’URL JSON e tenta di `json.loads` dal testo pagina.
- Usato da Remundo (`products.json`), Wallapop API, Vinted API, eBay Finding API.

**Limite invalicabile:** se il datacenter (GitHub `ubuntu-latest`) è in blacklist Akamai/Cloudflare, né retry né Playwright headless bastano. Serve IP di casa (self-hosted o PC).

---

## 4. Cosa fa ogni fonte (dato in → listing)

Ogni adapter sta in `sources/<nome>.py` e deve produrre `SourceListing`.

### 4.1 Remundo (`remundo`) — di solito il più affidabile

- **Cosa:** Shopify `https://remundo.it/products.json` pagine 1–7 (50 prodotti) + collection JSON (casa, elettronica, fai-da-te, bellezza, abbigliamento).
- **Dati:** titolo, prezzo variante disponibile, retail da testo “Retail x.xxx”, pezzi da “N Pezzi”, packing list da HTML descrizione, **`image_url`** dalla prima immagine prodotto.
- **Filtro tempo:** nessuno.
- **Perché 0 alert con 33 lotti:** quasi sempre `MAX_PALLET_EUR=400` o `MAX_COST_PER_PIECE_EUR=15`, non un fetch fallito.
- **Cosa può fallire:** `products.json` 403 (raro). Se una pagina JSON fallisce, **non** interrompe più tutto il catalogo (prova le altre).

### 4.2 PrezziShock (`prezzishock`)

- **Cosa:** tabelle PHP `auctions_show.php` ending (0/100/200) + new + featured. Parser `php_auction.py` (id da href `auction_id` / `auction_details`).
- **Dati:** titolo, prezzo EUR, spedizione se in colonna, n. offerte, countdown ultima cella; **`image_url` solo se c’è `<img>` nella riga** (senza img non scarta per “manca foto”).
- **Scarta già in parser:** “asta terminata”, buy-out, riga senza prezzo.
- **Filtro tempo:** ≤ 4h. Molte aste lette ma **oltre 4h** non arrivano a Telegram.
- **Cooldown:** 10+ scarti e 0 alert → skip 8h; anche regole globali 3 giorni / 2 alert 24h.
- **Cosa non funziona:** HTML cambiato (classi/tabelle) → 0 lotti anche con HTTP 200. Cloudflare da cloud possibile.

### 4.3 Antiebay (`antiebay`)

- Come PrezziShock: ending + new + featured + alcune categorie.
- Catalogo pubblico spesso pieno di aste già chiuse; il parser le salta. **Pochi lotti live** è normale.
- Stesso cooldown rumore.

### 4.4 Industrial Discount (`industrial_discount`)

- Home → link `/aste/` (fino a 16 aste) → in ogni pagina link `/lotti/`.
- Prezzo da “€” o “da 1.234”; tempo da testo card.
- **Giudiziaria:** 24h **o** senza data. Se il HTML non ha data, restano in lista; poi filtri ritiro/pesante/score li ammazzano.
- Camion/escavatori: exclude profilo + `SKIP_HEAVY_ITEMS`.

### 4.5 Wallapop / Vinted / Subito / eBay-fonte — **disattivati di default**

Adapter ancora nel repo ma **non** in `ENABLED_SOURCES`: da GitHub cloud danno quasi sempre 403/WAF. Riattivabili in `.env` se giri da casa. I parser (API/HTML) restano in `sources/`.

### 4.6 Vinted fonte (`vinted_source`)

- API catalog `vinted.it/api/v2/catalog/items` (15–60 €) poi HTML catalog.
- Si compra su Vinted per **rivendere su eBay** (canale Vinted bloccato in stima, per non auto-cannibalizzare).
- Stesso problema 403 da cloud. Condizioni “usato molto” / keyword Vinted possono azzerare il canale eBay se il titolo è sporco.

### 4.7 Subito (`subito`)

- Pagine `annunci-italia/vendita/usato/?q=…&shp=true` (spedizione).
- Parser `__NEXT_DATA__` (subject, price, urn) o card HTML.
- **Cosa non funziona da cloud:** Akamai “Access Denied” su hades e spesso sulle HTML. Da casa: dipende da cookie/JS.
- Stima: miglior canale eBay/Vinted (non “rivendi su Subito” come best, il best è solo eBay/Vinted).

### 4.8 eBay come fonte (`ebay_source`)

- Con `EBAY_APP_ID`: Finding API `findItemsAdvanced`, aste, `EndTimeSoonest`.
- Senza chiave: HTML `ebay.it/sch` `LH_Auction=1` `_sop=1` (in chiusura), fino a 40 item per keyword.
- Finding API è **deprecata da eBay**; può morire o rate-limit. HTML cambia spesso (classi `s-item__*`) → 0 risultati con pagina piena di JS.
- Filtro 4h: se il countdown HTML non si parsa, l’asta **esce**.
- Stima: non usa eBay come canale di **vendita** (evita buy-ebay-sell-ebay).

### 4.9 Catawiki (`catawiki`)

- Ricerche `CATAWIKI_QUERIES` (default **casio g-shock, profumo, lego**) `sort=ending_soon`. Poche query = meno timeout Playwright.
- Parser: `__NEXT_DATA__` (bid, stima min/max, `reserve_met`, fine) oppure regex `/it/l/ID`.
- **Akamai** è il blocco principale. Playwright aiuta in casa, poco su IP GitHub.
- Filtri extra: bid > 60% stima, stima min > 150 €, riserva non raggiunta, categoria fuori allowlist.
- 4h: senza `remaining_seconds` dal JSON, lotto scartato dal filtro tempo.

### 4.10 Gobid (`gobid`)

- Pagine aste + categorie (abbigliamento, varie, gaming, elettronica, orologeria, giocattoli).
- Parser: solo link `/lotti/` `/lotto/` con **prezzo in nodi che contengono €** (niente menu/breadcrumb). Categorie flip prima (elettronica, orologeria, gaming).
- **WAF.** Titoli rumore (menu). Prezzo 0 → `pick_classic` scarta. Countdown se presente nel blob.
- Cauzione in stima: `GOBID_DEPOSIT_EUR` (default 50), ritiro profilo 50 €.

### 4.11 Astagiudiziaria (`astagiudiziaria`)

- Categorie beni mobili (abbigliamento, arredo, informatica, hobby, giocattoli).
- Catalogo spesso **JS**: senza Playwright la pagina è vuota. Con Playwright da cloud può restare vuota (bot score).
- Link filtrati su href che contengono bene/avviso/dettaglio/scheda.
- Ritiro + cauzione giudiziaria in all-in.

### 4.12 Bidoo (`monitor.py`) — disattivato di default

- Penny auction, puntate, Cloudflare aggressivo su Actions. Non è nel modello “compra lotto / rivendi in scatola”.

### 4.13 Presenti nel codice ma NON nel giro default

| Fonte | Perché non è “on” |
|--------|-------------------|
| Wallapop / Vinted / Subito / eBay-fonte | 403/WAF da cloud; disattivati di default |
| Surplex | Macchinari, ritiro EU |
| B-Stock | Login, spesso P.IVA |
| Merkandi | Abbonamento |
| Stocklots24 | Prezzi dietro login/fee |

Gli adapter esistono; attivarli in `ENABLED_SOURCES` non li rende magicamente pubblici.

---

## 5. Dopo il catalogo: stima e filtri (perché “0 alert” è spesso corretto)

File: `classic_estimator.py`, `flip_rules.py`, `filters.py`, `comps.py`, `brands.py`, `feedback.py`.

**Costo all-in** = prezzo + premio acquirente + inbound + ritiro (se previsto) + cauzione (Gobid/IVG).

**Rivendita** = comps CSV se match (stdev ≤ 40%, media ≥ 15 €) **altrimenti** retail × fattore **solo se 20–200 €** **altrimenti** prezzo × moltiplicatore. **Classificati (Vinted/Wallapop/Subito): niente moltiplicatore**; serve comps e prezzo ≤ 70% della media. Se stima < 15 € → scarto.

**Budget max bid** = min(curva score 25/40/60/100 €, 40% valore ufficiale, cap moda 25 €, cap pallet 400 €).

**Score:** profitto, margine, marca premium **+30** / riconosciuta **+10**, comps affidabili **+15** / volatili **−20**, allowlist categoria −30, ritiro −15, titolo vago **scarto**, margine < 25 € **scarto**, 25–30 € **−30**, > 40 € **+20**, storico (ignora 3× −20, compra 2× +20, vendi 1× +30). Confidence 0–100 da marca/comps/spedibilità/margine/titolo.

**Foto (`photo_check.py`):** se `extra` ha `image_url` / `has_image` → missing o &lt; 300 px scarta; URL stock −20. Su GitHub Actions il HEAD immagine è saltato. Gobid senza URL immagine **non** viene scartato solo per assenza foto.

Un lotto **letto bene** può essere scartato per: bancale troppo caro, non flip (divano), keyword “rotto”, Catawiki stima alta, PrezziShock oltre 4h, cooldown sito.

---

## 6. Telegram

`build_classic_alert` in `classic_monitor.py` + `telegram_notifier.py`:

- HTML, link lotto, max bid / budget, all-in, tre canali eBay/Vinted/Subito
- **Score** e **Confidence 0–100** (marca, comps, spedibilità, margine, titolo)
- Sezioni **perché è buono** / **perché potrebbe essere rischioso** (fino a 6 bullet ciascuna)

Se i secret Actions sono sbagliati, il giro muore subito (non è un problema di scrape).

---

## 7. File di stato (locale / cache Actions)

| File | Ruolo |
|------|--------|
| `.alert_state.json` | ultimo alert per lotto (cooldown) |
| `.auction_history.json` | prezzi nel tempo (alert quiet) |
| `.feedback.json` | visto/ignorato/comprato/venduto |
| `.site_cooldown.json` | skip siti rumorosi |
| `data/comps.csv` | medie eBay/Vinted |
| `.env` | **non** committare (token) |

---

## 8. Cosa non funziona (tabella onesta)

| Sintomo | Causa vera | Cosa non risolverà il codice |
|---------|------------|------------------------------|
| Catawiki/Gobid/Subito/Wallapop/Vinted **Catalogo: 0** da Actions | IP datacenter + WAF (Akamai/Cloudflare). Playwright headless è comunque un bot. | Più retry. Serve runner **self-hosted** o PC di casa. |
| Log tagliato dopo header Catawiki | Job timeout / Playwright 55s × N query | Non è “filtro”; il giro non è arrivato a Gobid. |
| Remundo 33 lotti, 0 alert | Cap 400 € / costo pezzo | Lo scrape **funziona**. Alza cap se vuoi rumore. |
| Industrial 50 lotti, 1 in 24h | Pochi lotti con data in finestra | Parser date HTML incompleto su molte card. |
| PrezziShock skip cooldown | 10 scarti senza alert | Aspettare 8h o cancellare `.site_cooldown.json`. |
| eBay HTML 0 aste | Pagina JS / 403 / regex `s-item` obsoleta | App ID Finding (finché vive) o cambio markup eBay. |
| Prezzo 0 su Gobid/Catawiki | HTML senza € nel nodo parsato | `pick_classic` li butta. Serve parser più stretto sulla card, non più HTTP. |
| Comps “30 prodotti” | CSV seed, non API sold live | `update_comps.py` da casa (eBay/Vinted possono 403 da cloud). |
| Foto / qualità annuncio | HEAD/dimensioni se c’è URL; no AI | Stock / tiny / missing quando `image_url` è valorizzato |
| Offerta automatica | Voluta assente | ToS. |
| Amazon “prezzo ufficiale” | Nessuna API Pricing gratis/legale | Si usa `retail_hint` o comps/0.55. |
| Packing list Remundo sempre | Body Shopify incompleto | Flag + haircut, non magia. |

---

## 9. Come massimizzare i dati **in pratica**

1. Girare da **casa** o self-hosted (`USE_PLAYWRIGHT=true`).
2. Non aspettarsi Wallapop/Subito/Catawiki pieni su GitHub cloud.
3. Se vuoi più alert Remundo: `MAX_PALLET_EUR` / `MAX_COST_PER_PIECE_EUR`.
4. Se vuoi più aste PrezziShock in Telegram: `MAX_HOURS_TO_END=8` o `12` (più rumore).
5. `python update_comps.py` periodico da casa.
6. `record_feedback.py` dopo 20 lotti visti i filtri si personalizzano.
7. Se Catawiki mangia il timeout: toglila temporaneamente da `ENABLED_SOURCES` per far girare Gobid/IVG.

---

## 10. Variabili che influenzano solo il fetch

| Variabile | Default | Effetto |
|-----------|---------|---------|
| `USE_PLAYWRIGHT` | true in Actions | Fallback browser su 403/challenge |
| `FETCH_RETRIES` | 3 | Tentativi HTTP |
| `PLAYWRIGHT_WAIT_MS` | 4500 | Pausa dopo load |
| `PLAYWRIGHT_GOTO_MS` | 35000 | Timeout navigazione (più basso per non bruciare Catawiki) |
| `CATAWIKI_QUERIES` | casio g-shock, profumo, lego | Poche search Catawiki |
| `ENABLED_SOURCES` | prezzishock,catawiki,gobid,astagiudiziaria,industrial_discount,remundo | Chi viene interrogato |

---

## 11. Mappa file (chi fa cosa)

| File | Responsabilità |
|------|----------------|
| `monitor_all.py` | Orchestratore fonti |
| `classic_monitor.py` | Giro singolo: fetch → filtri tempo → stima → Telegram |
| `classic_estimator.py` | All-in, canali, score, budget, Catawiki/Remundo rules |
| `photo_check.py` | HEAD / pixel JPEG-PNG se c’è `image_url` |
| `http_fetch.py` | GET, retry, challenge, Playwright condiviso |
| `sources/*.py` | HTML/JSON → `SourceListing` |
| `php_auction.py` | Tabelle PrezziShock/Antiebay (+ img se presente) |
| `site_profiles.py` | Fee, ritiro, kind, exclude |
| `flip_rules.py` | Spedibilità, keyword, allowlist, Catawiki |
| `brands.py` + `brands_extra.py` | Marca da titolo + premium allowlist |
| `comps.py` | CSV comparables + `reliable` / `too_volatile` |
| `feedback.py` | Score ± da ignora/compra/vendi |
| `site_cooldown.py` | Skip / frequenza per sito |
| `telegram_notifier.py` | Invio |
| `update_comps.py` / `record_feedback.py` | Manutenzione dati |

Fine. Se un log non coincide con questa pagina, vince il log: i siti cambiano HTML senza preavviso.
