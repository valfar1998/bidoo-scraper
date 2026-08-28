# Bidoo Monitor (Telegram)

Monitor che legge le aste pubbliche su Bidoo e invia alert su Telegram quando trova **prodotti** (non puntate/buoni) con un potenziale affare.

## Criteri di alert (default)

| Regola | Valore |
|--------|--------|
| Valore prodotto | **> 50 €** |
| Risparmio nominale | **≥ 30 €** (valore − prezzo asta) |
| Prezzo asta sotto | **15%** se valore ≥ 100 €, **25%** se 50–99 € |
| Timer attivo | **≤ 5 min** (radar) o **≤ 60 s** (snipe) |
| Esclusioni | Aste Puntate, buoni, voucher, forzieri |

Il messaggio Telegram include anche una **stima** del costo se dovessi rilanciare fino alla soglia (ricorda: le puntate costano ~0,20 € ciascuna).

Non effettua login, non punta, non simula click.

## Avvertenza sui Termini Bidoo

I [Termini e Condizioni](https://it.bidoo.com/terms.php) vietano software per puntare automaticamente e citano tra gli usi non autorizzati il **software di monitoraggio dell'offerta**. Uso personale a tuo rischio.

## Setup

```bash
pip install -r requirements.txt
copy .env.example .env
# compila TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID
```

## Due modalità

### Radar (cloud / ogni 5 min)

Panoramica ampia, timer fino a 5 minuti. Ideale per **GitHub Actions** o `python monitor.py --once`.

```bash
python monitor.py --once --mode radar
```

### Snipe (locale / PC acceso)

Più reattivo: timer ≤ 60 s, controllo ogni 15 s. Per cogliere chiusure rapide.

```bash
python monitor.py --mode snipe
# oppure
.\run-snipe.ps1
```

| Modalità | Timer max | Poll | Dove |
|----------|-----------|------|------|
| `radar` | 300 s | 30 s | GitHub Actions, Pianificatore |
| `snipe` | 60 s | 15 s | PC acceso in locale |

**Consiglio:** Actions in radar + snipe in locale quando sei al PC.

## GitHub Actions (PC spento)

1. Secret: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
2. Actions → **Bidoo Monitor** → **Run workflow**
3. Parte ogni 5 minuti in modalità **radar**

Su Actions usa Playwright perché Bidoo blocca spesso gli IP datacenter (403).

## Pianificatore Windows

```powershell
# ogni 5 min, modalità radar
.\run-check.ps1
```

## Configurazione (.env)

| Variabile | Default | Descrizione |
|-----------|---------|-------------|
| `MIN_RETAIL_VALUE` | 50 | Valore minimo prodotto (€) |
| `MIN_SAVINGS_EUR` | 30 | Risparmio minimo valore − prezzo |
| `HIGH_VALUE_THRESHOLD` | 100 | Soglia per tier prezzo alto |
| `MAX_PRICE_RATIO_HIGH` | 0.15 | Max % prezzo se valore ≥ 100 € |
| `MAX_PRICE_RATIO_MID` | 0.25 | Max % prezzo se valore 50–99 € |
| `MONITOR_MODE` | radar | `radar` o `snipe` |
| `MAX_TIMER_SECONDS` | auto | Override timer (300 radar / 60 snipe) |
| `POLL_INTERVAL` | auto | Secondi tra cicli in loop |
| `ALERT_COOLDOWN` | 600 | Anti-spam per stessa asta |
| `BID_COST_ESTIMATE` | 0.20 | Stima €/puntata nel messaggio |
| `BIDOO_URL` | it.bidoo.com | Categoria/tag da monitorare |

Esempi categoria:

```
BIDOO_URL=https://it.bidoo.com/?tag=smartphone
BIDOO_URL=https://it.bidoo.com/?tag=elettrodomestici
```

## Fare affari: cosa guardare tu

1. **Prezzo asta** ≠ costo totale. Conta anche le puntate che usi.
2. Usa **AutoPuntata** ufficiale Bidoo con un limite, non bot esterni.
3. **Snipe** in locale per le chiusure; **radar** in cloud per non perdere opportunità grossolane.
4. Filtra per **categoria** che ti interessa davvero.
5. Valuta **Compralo Ora** se hai già speso molte puntate in un'asta.
