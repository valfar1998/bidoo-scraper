"""Bot Telegram: inline keyboard, comandi ops (/pause, /stats, /force_sync)."""

from __future__ import annotations

import json
import argparse
import os
import subprocess
import sys
import time

import requests
from dotenv import load_dotenv

from brands import find_brand
from feedback import FeedbackStore
from flip_rules import infer_flip_tag
from health_digest import build_stats_table
from inventory import (
    create_from_bought,
    format_sale_report,
    get_alert_snapshot,
    get_inventory_item,
    precision_metrics,
    record_sale,
    update_ebay_listing,
)
from inventory_repricer import format_suggestion_html, record_reprice
from listing_generator import generate_listing_drafts
from capital_allocator import format_portfolio_telegram
from ebay_sell import create_ebay_listing_from_snapshot, sell_api_enabled
from tax_reporter import format_tax_report_telegram, parse_period_arg, send_tax_report_document
from telegram_topics import resolve_ops_topic
from site_cooldown import set_manual_pause
from site_profiles import PROFILES

API = "https://api.telegram.org/bot{token}/{method}"


def _api(token: str, method: str, payload: dict) -> dict:
    response = requests.post(API.format(token=token, method=method), json=payload, timeout=35)
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method}: {data}")
    return data


def _authorized_chat(chat_id: int | str) -> bool:
    allowed = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not allowed:
        return True
    return str(chat_id) == allowed


def build_feedback_keyboard(listing_id: str) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "Ignora", "callback_data": f"i:{listing_id}"},
                {"text": "Comprato", "callback_data": f"b:{listing_id}"},
                {"text": "Errore Stima", "callback_data": f"e:{listing_id}"},
            ]
        ]
    }


def build_listing_keyboard(listing_id: str) -> dict | None:
    if not sell_api_enabled():
        return None
    return {
        "inline_keyboard": [
            [{"text": "Pubblica su eBay", "callback_data": f"p:{listing_id}"}],
        ]
    }


def parse_callback(data: str) -> tuple[str, str] | None:
    if not data or ":" not in data:
        return None
    action_code, listing_id = data.split(":", 1)
    action_map = {
        "i": "ignored",
        "b": "bought",
        "e": "estimate_error",
        "p": "publish_ebay",
    }
    action = action_map.get(action_code)
    if not action or not listing_id:
        return None
    return action, listing_id


def _reply(
    token: str,
    chat_id: int | str,
    text: str,
    *,
    reply_markup: dict | None = None,
    topic_kind: str | None = None,
) -> None:
    payload: dict = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    thread_id = resolve_ops_topic(topic_kind) if topic_kind else None
    if thread_id is not None:
        payload["message_thread_id"] = thread_id
    _api(token, "sendMessage", payload)


def handle_callback(token: str, callback: dict) -> None:
    callback_id = callback.get("id")
    data = str(callback.get("data") or "")
    message = callback.get("message") or {}
    chat_id = message.get("chat", {}).get("id")
    if chat_id is not None and not _authorized_chat(chat_id):
        return
    parsed = parse_callback(data)
    if not parsed:
        if callback_id:
            _api(token, "answerCallbackQuery", {"callback_query_id": callback_id, "text": "Azione non valida"})
        return
    action, listing_id = parsed

    if action == "publish_ebay":
        snap = get_alert_snapshot(listing_id)
        inv = get_inventory_item(listing_id)
        if not snap and inv:
            try:
                snap = json.loads(str(inv.get("snapshot_json") or "{}"))
            except Exception:
                snap = {}
        if not snap:
            if callback_id:
                _api(token, "answerCallbackQuery", {"callback_query_id": callback_id, "text": "Snapshot non trovato"})
            return
        listing = snap.get("listing") or {}
        estimate = snap.get("estimate") or {}
        merged = {
            **listing,
            "title": listing.get("title") or listing_id,
            "estimate": estimate,
            "category_name": estimate.get("category_tag") or listing.get("category_tag"),
        }
        try:
            result = create_ebay_listing_from_snapshot(
                merged, listing_id=listing_id, publish=False
            )
            update_ebay_listing(
                listing_id,
                offer_id=result["offer_id"],
                listing_url=result.get("listing_url") or "",
                list_price_eur=result["price_eur"],
            )
            msg = (
                f"✅ <b>eBay</b> bozza creata per <code>{listing_id}</code>\n"
                f"SKU: {result['sku']}\n"
                f"Prezzo: {result['price_eur']:.2f} €\n"
                f"Offer ID: <code>{result['offer_id']}</code>"
            )
            if result.get("listing_url"):
                msg += f"\n<a href=\"{result['listing_url']}\">Apri inserzione</a>"
            else:
                msg += "\nPubblica da Seller Hub o imposta EBAY_AUTO_PUBLISH=true"
            if chat_id:
                _reply(token, chat_id, msg, topic_kind="inventory")
            if callback_id:
                _api(token, "answerCallbackQuery", {"callback_query_id": callback_id, "text": "Bozza eBay creata"})
        except Exception as exc:
            if callback_id:
                _api(token, "answerCallbackQuery", {"callback_query_id": callback_id, "text": "Errore eBay"})
            if chat_id:
                _reply(token, chat_id, f"❌ eBay: {exc}", topic_kind="inventory")
        return

    store = FeedbackStore.load()
    previous = store.find(listing_id)
    title = ""
    if previous:
        title = str(previous.get("title") or "")
    if not title:
        text = str(message.get("text") or message.get("caption") or "")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("<") and len(stripped) > 8:
                title = stripped
                break
    source = listing_id.split(":", 1)[0] if ":" in listing_id else ""
    brand = find_brand(title) or (previous.get("brand") if previous else None)
    category = (previous.get("category") if previous else "") or infer_flip_tag(title)
    store.record(
        action,
        listing_id=listing_id,
        title=title or listing_id,
        brand=brand,
        category=category,
        source=source,
    )
    store.save()
    if action == "bought":
        item = create_from_bought(listing_id, title=title or listing_id)
        if item and chat_id:
            _reply(
                token,
                chat_id,
                f"📦 <b>Inventario</b>: scheda creata per <code>{listing_id}</code>\n"
                f"Max bid consigliato: {item.recommended_max_bid_eur:.2f} €\n"
                f"Profitto stimato: {item.estimated_profit_eur:.0f} €\n"
                f"Chiudi con: /sold {listing_id} &lt;prezzo_vendita&gt;",
                topic_kind="inventory",
            )
            snap = get_alert_snapshot(listing_id)
            if snap:
                listing = snap.get("listing") or {}
                estimate = snap.get("estimate") or {}
                merged = {
                    **listing,
                    "title": title or listing.get("title") or listing_id,
                    "estimate": estimate,
                    "category_name": estimate.get("category_tag") or listing.get("category_tag"),
                }
                ebay_kb = build_listing_keyboard(listing_id)
                for draft in generate_listing_drafts(merged, unbundle=listing.get("unbundle")):
                    _reply(token, chat_id, draft, reply_markup=ebay_kb, topic_kind="inventory")
    labels = {
        "ignored": "Ignorato",
        "bought": "Comprato",
        "estimate_error": "Errore stima registrato",
        "publish_ebay": "Pubblicato su eBay",
    }
    if callback_id:
        _api(
            token,
            "answerCallbackQuery",
            {"callback_query_id": callback_id, "text": labels.get(action, "OK")},
        )
    if chat_id:
        _reply(token, chat_id, f"✓ {labels.get(action, action)}: <code>{listing_id}</code>")


def _help_text() -> str:
    return (
        "<b>Comandi disponibili</b>\n"
        "/stats — ultime 24h per fonte\n"
        "/pause &lt;fonte&gt; &lt;ore&gt; — cooldown manuale\n"
        "/force_sync — aggiorna comps venduti\n"
        "/sold &lt;id&gt; &lt;prezzo&gt; — chiudi inventario\n"
        "/portfolio — capitale attivo e limiti categoria\n"
        "/reprice &lt;id&gt; &lt;prezzo&gt; — aggiorna listino inventario\n"
        "/tax_report [YYYY-MM] — report fiscale CSV\n"
        "/precision — accuratezza stime vs vendite reali\n"
        "/help — questo messaggio"
    )


def handle_command(token: str, message: dict) -> None:
    chat_id = message.get("chat", {}).get("id")
    if chat_id is None or not _authorized_chat(chat_id):
        return
    text = str(message.get("text") or "").strip()
    if not text.startswith("/"):
        return
    parts = text.split()
    command = parts[0].split("@")[0].lower()

    if command in ("/start", "/help"):
        _reply(token, chat_id, _help_text())
        return

    if command == "/stats":
        hours = 24
        if len(parts) >= 2:
            try:
                hours = max(1, int(parts[1]))
            except ValueError:
                pass
        _reply(token, chat_id, build_stats_table(hours=hours))
        return

    if command == "/pause":
        if len(parts) < 3:
            sources = ", ".join(sorted(PROFILES.keys()))
            _reply(token, chat_id, f"Uso: /pause &lt;fonte&gt; &lt;ore&gt;\nFonti: {sources}")
            return
        source = parts[1].lower()
        if source not in PROFILES:
            _reply(token, chat_id, f"Fonte sconosciuta: <code>{source}</code>")
            return
        try:
            hours = float(parts[2].replace(",", "."))
        except ValueError:
            _reply(token, chat_id, "Ore non valide.")
            return
        set_manual_pause(source, hours)
        _reply(token, chat_id, f"⏸ <b>{source}</b> in pausa per <b>{hours:g}h</b>.")
        return

    if command == "/precision":
        metrics = precision_metrics()
        _reply(
            token,
            chat_id,
            f"🎯 <b>Precision Score</b>\n"
            f"Vendite chiuse: {metrics['n']}\n"
            f"Accuratezza (±20%): <b>{metrics['precision_pct']:.0f}%</b>\n"
            f"Errore medio assoluto: {metrics['mae_eur']:.0f} €",
        )
        return

    if command == "/portfolio":
        _reply(token, chat_id, format_portfolio_telegram(), topic_kind="ops")
        return

    if command == "/reprice":
        if len(parts) < 3:
            _reply(token, chat_id, "Uso: /reprice &lt;listing_id&gt; &lt;nuovo_prezzo_eur&gt;")
            return
        listing_id = parts[1]
        try:
            new_price = float(parts[2].replace(",", "."))
        except ValueError:
            _reply(token, chat_id, "Prezzo non valido.")
            return
        if not record_reprice(listing_id, new_price):
            _reply(token, chat_id, f"Nessun lotto pending per <code>{listing_id}</code>.")
            return
        _reply(
            token,
            chat_id,
            f"📉 Listino aggiornato: <code>{listing_id}</code> → <b>{new_price:.2f} €</b>",
            topic_kind="repricing",
        )
        return

    if command == "/tax_report":
        from datetime import datetime

        if len(parts) >= 2:
            try:
                year, month = parse_period_arg(parts[1])
            except ValueError:
                _reply(token, chat_id, "Uso: /tax_report [YYYY-MM]")
                return
        else:
            now = datetime.now()
            year, month = now.year, now.month
        _reply(token, chat_id, format_tax_report_telegram(year, month), topic_kind="tax")
        try:
            send_tax_report_document(token, str(chat_id), year, month)
        except Exception as exc:
            _reply(token, chat_id, f"⚠️ CSV non inviato: {exc}", topic_kind="tax")
        return

    if command == "/sold":
        if len(parts) < 3:
            _reply(token, chat_id, "Uso: /sold &lt;listing_id&gt; &lt;prezzo_vendita_eur&gt;")
            return
        listing_id = parts[1]
        try:
            sold_price = float(parts[2].replace(",", "."))
        except ValueError:
            _reply(token, chat_id, "Prezzo non valido.")
            return
        item = record_sale(listing_id, sold_price)
        if not item:
            _reply(token, chat_id, f"Nessuna scheda inventario per <code>{listing_id}</code>. Usa prima [Comprato].")
            return
        fb = FeedbackStore.load()
        fb.record(
            "sold",
            listing_id=listing_id,
            title=item.title,
            brand=item.brand,
            category=item.category,
            source=item.source,
        )
        fb.save()
        _reply(token, chat_id, format_sale_report(item))
        return

    if command == "/force_sync":
        _reply(token, chat_id, "⏳ Avvio sync comps venduti…")
        root = os.path.dirname(os.path.abspath(__file__))
        proc = subprocess.run(
            [sys.executable, os.path.join(root, "sold_comps_sync.py")],
            capture_output=True,
            text=True,
            timeout=600,
            cwd=root,
        )
        tail = (proc.stdout or proc.stderr or "").strip()[-500:]
        if proc.returncode == 0:
            _reply(token, chat_id, f"✅ Sync completato.\n<pre>{tail}</pre>")
        else:
            _reply(token, chat_id, f"❌ Sync fallito ({proc.returncode}).\n<pre>{tail}</pre>")
        return

    _reply(token, chat_id, "Comando sconosciuto. /help")


def poll_forever(token: str, *, offset: int = 0, timeout: int = 30) -> None:
    print("Telegram bot polling attivo (callback + comandi). Ctrl+C per uscire.")
    while True:
        try:
            data = _api(
                token,
                "getUpdates",
                {
                    "offset": offset,
                    "timeout": timeout,
                    "allowed_updates": ["callback_query", "message"],
                },
            )
            for update in data.get("result") or []:
                offset = int(update.get("update_id", 0)) + 1
                callback = update.get("callback_query")
                if callback:
                    handle_callback(token, callback)
                    continue
                message = update.get("message") or {}
                if message.get("text", "").startswith("/"):
                    handle_command(token, message)
        except KeyboardInterrupt:
            print("Stop.")
            return
        except Exception as exc:
            print(f"[telegram-bot] {exc}")
            time.sleep(3)


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Bot Telegram: feedback inline + comandi ops.")
    parser.add_argument("--once", action="store_true", help="Un solo giro getUpdates")
    args = parser.parse_args()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN mancante")
    if args.once:
        data = _api(
            token,
            "getUpdates",
            {"timeout": 0, "allowed_updates": ["callback_query", "message"]},
        )
        for update in data.get("result") or []:
            callback = update.get("callback_query")
            if callback:
                handle_callback(token, callback)
            message = update.get("message") or {}
            if message.get("text", "").startswith("/"):
                handle_command(token, message)
        return
    poll_forever(token)


if __name__ == "__main__":
    main()
