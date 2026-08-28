# Bidoo Monitor (Telegram)

Monitor personale che legge le aste pubbliche su Bidoo e invia alert su Telegram quando:

- il **valore** del prodotto (sotto l'annuncio) è **> 35 €** (configurabile);
- il **prezzo d'asta** è **sotto il 35%** del valore (configurabile);
- l'asta è **attiva** e il timer indica che sta per chiudersi (default: ≤ 5 minuti).

Non effettua login, non punta, non simula click.

## Avvertenza sui Termini Bidoo

I [Termini e Condizioni](https://it.bidoo.com/terms.php) di Bidoo vietano:

- software esterno per puntare automaticamente;
- tra gli usi non autorizzati citano anche il **software di monitoraggio dell'offerta**.

Usa questo tool a tuo rischio. Il progetto è pensato solo per uso personale e monitoraggio leggero.

## Setup

1. Installa dipendenze:

```bash
pip install -r requirements.txt
```

2. Copia la configurazione:

```bash
copy .env.example .env
```

3. Crea un bot Telegram con [@BotFather](https://t.me/BotFather) e ottieni il `chat_id` (es. con [@userinfobot](https://t.me/userinfobot)).

4. Compila `.env` con token e chat id.

5. Avvia:

```bash
python monitor.py
```

## Automazione (ogni 5 minuti)

**Non serve una repository pubblica.** Il monitor gira sul tuo PC (o su un server tuo) con i file locali e il file `.env`. Puoi anche non usare git, oppure tenere un repo **privato**.

### Opzione A — Pianificatore Windows (consigliata)

1. Apri **Utilità di pianificazione** → Crea attività di base.
2. Nome: `Bidoo Monitor`.
3. Trigger: **Ogni giorno**, ripeti ogni **5 minuti**, per **tempo indeterminato**.
4. Azione: **Avvia programma**
   - Programma: `powershell.exe`
   - Argomenti: `-ExecutionPolicy Bypass -File "C:\Users\valba\Desktop\corsi\bidoo-scraper\run-check.ps1"`
5. Avvia solo se il PC è collegato alla rete (opzionale ma utile).

Oppure da terminale, un controllo singolo:

```bash
python monitor.py --once
```

### Opzione B — Sempre acceso in background

```bash
python monitor.py
```

Con `POLL_INTERVAL=15` controlla ogni 15 secondi (più reattivo, ma il processo resta aperto).

### Ogni 5 minuti è troppo?

**No, va bene.** È più prudente del controllo ogni 15 secondi. Per Bidoo il limite non è la frequenza in sé, ma evitare richieste continue e aggressive.

| Modalità | Pro | Contro |
|----------|-----|--------|
| Ogni 5 min (`--once`) | Leggero, PC può essere spento tra un run e l'altro | Puoi perdere aste che chiudono in pochi secondi |
| Ogni 15 s (loop) | Più reattivo | Processo sempre attivo |

Consiglio: **5 minuti** se vuoi qualcosa di semplice e “a basso impatto”. Se vuoi più reattività, abbassa a 1–2 minuti o usa il loop continuo.

Gli alert non si ripetono subito: `ALERT_COOLDOWN=600` (10 min) evita spam per la stessa asta.

## Configurazione (.env)

| Variabile | Default | Descrizione |
|-----------|---------|-------------|
| `MIN_RETAIL_VALUE` | 35 | Valore minimo prodotto (€) |
| `MAX_PRICE_RATIO` | 0.35 | Soglia prezzo rispetto al valore (35%) |
| `MAX_TIMER_SECONDS` | 300 | Timer massimo per "sta per chiudersi" |
| `POLL_INTERVAL` | 15 | Secondi tra un controllo e l'altro |
| `ALERT_COOLDOWN` | 600 | Secondi prima di reinviare lo stesso alert |
| `BIDOO_URL` | https://it.bidoo.com/ | Pagina aste da monitorare |

Per monitorare una categoria specifica, imposta ad esempio:

```
BIDOO_URL=https://it.bidoo.com/?tag=smartphone
```

## Note pratiche

- Su Bidoo ogni puntata resetta il timer (spesso a pochi secondi). La regola "5 minuti dalla fine" è indicativa: il timer reale può essere molto più corto.
- Il monitor legge la pagina principale (o l'URL che imposti) e aggiorna i prezzi via `data.php`.
- Tieni `POLL_INTERVAL` non troppo basso (≥ 10–15 s) per non stressare il sito.
