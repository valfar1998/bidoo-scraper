# Guida eBay Developers Program (per questo progetto)

Serve **solo** a far leggere allo script le aste eBay (“lotto stock”, rimanenze).  
Non ti serve un negozio, non paghi eBay, non autorizzi vendite.

## 1. Crea l’account developer

1. Apri https://developer.ebay.com/
2. Accedi con il tuo account eBay (o registrati).
3. Accetta i termini del **eBay Developers Program**.

## 2. Crea un’applicazione e copia l’App ID

1. Menu: **My Account → Application Keys**  
   (nella pagina Get Started c’è anche “Get your application keys”).
2. **Create a keyset** / crea un’app. Nome esempio: `resale-monitor`.
3. Apri il keyset **Production** (non Sandbox).
4. Copia **App ID (Client ID)**.  
   Per `monitor_ebay_source.py` **basta questa**.  
   Cert ID e Dev ID non servono per la ricerca aste.

## 3. Mettila nel progetto

Nel file `.env` (stessa cartella degli script):

```
EBAY_APP_ID=incolla_qui_l_App_ID
EBAY_GLOBAL_ID=EBAY-IT
```

Poi:

```powershell
python monitor_ebay_source.py
```

Se manca l’App ID, lo script stampa che non parte.  
Se eBay risponde errore sulla Finding API (è vecchia), incolla l’errore in chat e si passa alla Browse API (lì servono anche Client Secret e un token applicativo).

## Cosa non fare

- Non usare le chiavi **Sandbox** per il monitor reale.
- Non condividere l’App ID in pubblico / GitHub.
- Non serve “user token” OAuth finché cerchiamo solo annunci pubblici.
- eBay come **fonte** (lotti da comprare) è opzionale: i siti a cui sei già iscritto (Remundo, PrezziShock, Catawiki, Gobid, IVG, Industrial Discount) funzionano **senza** questa API.
