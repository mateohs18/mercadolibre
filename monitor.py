"""
Stock Monitor -> Discord
-------------------------
Monitorea cualquier URL de producto (MercadoLibre, Amazon, eBay, tiendas
genéricas, etc.) y envía una notificación a un webhook de Discord cuando
detecta que el producto pasó de "sin stock" a "disponible".

Uso:
    1. Copiá config.example.json a config.json y completalo.
    2. python3 monitor.py

Ver README.md para instrucciones de instalación y despliegue en VPS
(systemd, cron, etc.).
"""

import json
import time
import logging
import re
import os
import sys
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

CONFIG_PATH = os.environ.get("CONFIG_PATH", "config.json")
STATE_PATH = os.environ.get("STATE_PATH", "state.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("stock-monitor")

# Headers "de navegador" para reducir bloqueos básicos anti-bot.
BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-AR,es;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Sec-CH-UA": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-CH-UA-Mobile": "?0",
    "Sec-CH-UA-Platform": '"Windows"',
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

# Un User-Agent alternativo para el reintento si el primero recibe 403.
ALT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15"
)

SESSION = requests.Session()
SESSION.headers.update(BASE_HEADERS)

# Frases que indican que el producto NO está disponible (ES/EN, varios sitios).
OUT_OF_STOCK_PATTERNS = [
    r"sin stock",
    r"agotado",
    r"pausad[oa]s?\s*/?\s*sin stock",
    r"producto pausado",
    r"no disponible",
    r"ya no est[aá] disponible",
    r"out of stock",
    r"currently unavailable",
    r"(?<!until )(?<!or until )\bsold out\b",
    r"unavailable",
    r"no longer available",
    r"this item is currently not available",
    r"temporarily out of stock",
    r"this item is out of stock",
]

# Frases que indican que SÍ se puede comprar.
ADD_TO_CART_PATTERNS = [
    r"agregar al carrito",
    r"comprar ahora",
    r"a[ñn]adir a la cesta",
    r"add to cart",
    r"add to basket",
    r"buy now",
    r"\bin stock\b",
]

PRICE_PATTERNS = [
    re.compile(r'"price"\s*:\s*"?([\d]+[.,]?\d*)"?', re.I),
    re.compile(r'itemprop=["\']price["\'][^>]*content=["\']([\d.,]+)["\']', re.I),
    re.compile(r'property=["\']og:price:amount["\'][^>]*content=["\']([\d.,]+)["\']', re.I),
]


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def fetch(url, retries=2):
    last_exc = None
    for attempt in range(retries + 1):
        headers = None
        if attempt > 0:
            # En el reintento probamos con un User-Agent distinto: algunos
            # sitios (eBay, por ejemplo) bloquean firmas de bot muy específicas.
            headers = dict(SESSION.headers)
            headers["User-Agent"] = ALT_USER_AGENT
        try:
            resp = SESSION.get(url, headers=headers, timeout=20)
            if resp.status_code == 403 and attempt < retries:
                log.info("403 en intento %d, reintentando con otro User-Agent...", attempt + 1)
                time.sleep(3)
                continue
            resp.raise_for_status()
            return resp.text
        except requests.exceptions.HTTPError as e:
            last_exc = e
            if attempt < retries:
                time.sleep(3)
                continue
            raise
        except Exception as e:
            last_exc = e
            raise
    raise last_exc


def extract_title(soup, fallback):
    og = soup.find("meta", property="og:title")
    if og and og.get("content"):
        return og["content"].strip()[:150]
    if soup.title and soup.title.string:
        return soup.title.string.strip()[:150]
    return fallback


def extract_image(soup):
    for prop in ("og:image", "og:image:secure_url", "twitter:image"):
        tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
        if tag and tag.get("content"):
            url = tag["content"].strip()
            if url.startswith("http"):
                return url
    return None


def extract_price(html):
    for pattern in PRICE_PATTERNS:
        m = pattern.search(html)
        if m:
            return m.group(1)
    # Fallback: buscamos un precio con símbolo $ cerca de palabras típicas
    # de precio, como último recurso si no hubo metadatos estructurados.
    m = re.search(r'\$\s?([\d]{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})?)', html)
    if m:
        return f"${m.group(1)}"
    return None


def get_domain(url):
    m = re.search(r"https?://(?:www\.)?([^/]+)", url)
    return m.group(1) if m else url


def parse_price(price_str):
    """Convierte un string de precio tipo '1,299.99' o '399,99' a float."""
    if not price_str:
        return None
    s = str(price_str).replace("$", "").strip()
    if "," in s and "." in s:
        s = s.replace(",", "")
    elif "," in s and "." not in s:
        parts = s.split(",")
        if len(parts[-1]) == 2:
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def detect_status(html, debug=False):
    text = html.lower()

    out_of_stock_match = None
    for p in OUT_OF_STOCK_PATTERNS:
        m = re.search(p, text)
        if m:
            out_of_stock_match = (p, m)
            break

    add_to_cart_match = None
    for p in ADD_TO_CART_PATTERNS:
        m = re.search(p, text)
        if m:
            add_to_cart_match = (p, m)
            break

    if debug:
        if add_to_cart_match:
            p, m = add_to_cart_match
            start = max(0, m.start() - 60)
            end = min(len(text), m.end() + 60)
            log.info("DEBUG add_to_cart matcheó patrón %r -> ...%s...", p, text[start:end].replace("\n", " "))
        else:
            log.info("DEBUG ningún patrón de add_to_cart matcheó")
        if out_of_stock_match:
            p, m = out_of_stock_match
            start = max(0, m.start() - 60)
            end = min(len(text), m.end() + 60)
            log.info("DEBUG out_of_stock matcheó patrón %r -> ...%s...", p, text[start:end].replace("\n", " "))
        else:
            log.info("DEBUG ningún patrón de out_of_stock matcheó")

    # Priorizamos la frase explícita de "sin stock" quejes específica y clara
    # ("out of stock", "sold out", etc. ya filtrando frases de urgencia tipo
    # "until sold out"). Un botón de "Add to cart" puede seguir presente en
    # el HTML crudo aunque esté oculto/deshabilitado por JavaScript del lado
    # del cliente (algo común en eBay), así que NO puede pisar una frase
    # explícita de no-disponibilidad; solo lo usamos como respaldo cuando no
    # hay ninguna señal explícita de "sin stock".
    if out_of_stock_match:
        return "out_of_stock"
    if add_to_cart_match:
        return "in_stock"
    return "unknown"


def send_discord(webhook_url, product_name, url, event_type, status=None, price=None,
                  image_url=None, old_price=None, extra_note=None):
    domain = get_domain(url)

    if event_type == "stock":
        title = "🟢 ¡Producto disponible!"
        color = 3066993
    elif event_type == "price_drop":
        title = "💸 ¡Bajó de precio!"
        color = 16776960
    elif event_type == "error":
        title = "⚠️ No puedo acceder a este producto"
        color = 15158332
    else:
        title = "ℹ️ Actualización de producto"
        color = 3447003

    fields = [{"name": "Sitio", "value": domain, "inline": True}]
    if status:
        fields.insert(0, {"name": "Estado", "value": "✅ Disponible" if status == "in_stock" else status, "inline": True})
    if price and event_type == "price_drop" and old_price:
        fields.append({"name": "Precio anterior", "value": f"${old_price:.2f}", "inline": True})
        fields.append({"name": "Precio nuevo", "value": price, "inline": True})
    elif price:
        fields.append({"name": "Precio", "value": price, "inline": True})
    if extra_note:
        fields.append({"name": "Detalle", "value": extra_note, "inline": False})

    embed = {
        "title": title,
        "description": f"**[{product_name}]({url})**",
        "url": url,
        "color": color,
        "fields": fields,
        "footer": {"text": "Stock Monitor"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    
    if image_url:
        embed["thumbnail"] = {"url": image_url}
        
    payload = {"embeds": [embed]}
    
    # Te menciona (ping) si hay stock o bajó el precio
    if event_type in ("stock", "price_drop"):
        payload["content"] = "<@911730868316418099>"

    try:
        r = requests.post(webhook_url, json=payload, timeout=15)
        if r.status_code >= 300:
            log.error("Error enviando a Discord (%s): %s", r.status_code, r.text)
    except Exception as e:
        log.error("Excepción enviando a Discord: %s", e)


def check_product(product, state, webhook_url, error_notify_after_failures):
    url = product["url"]
    configured_name = product.get("name")
    prev = state.get(url, {})

    try:
        html = fetch(url)
    except Exception as e:
        failures = prev.get("consecutive_failures", 0) + 1
        log.warning("No se pudo acceder a %s (falla #%d): %s", configured_name or url, failures, e)
        prev["consecutive_failures"] = failures
        prev.setdefault("name", configured_name or url)
        # Avisamos una sola vez al cruzar el umbral, no en cada falla.
        if failures == error_notify_after_failures:
            send_discord(
                webhook_url,
                prev.get("name", url),
                url,
                event_type="error",
                extra_note=f"Falló {failures} veces seguidas. Último error: {e}",
            )
        state[url] = prev
        return

    soup = BeautifulSoup(html, "html.parser")
    name = configured_name or extract_title(soup, url)
    raw_status = detect_status(html)
    price_str = extract_price(html)
    price_val = parse_price(price_str)
    image_url = extract_image(soup)

    confirmed_status_before = prev.get("status")
    prev_price_val = parse_price(prev.get("price"))
    had_failures = prev.get("consecutive_failures", 0) >= error_notify_after_failures

    # --- Confirmación doble (anti-flapping) ---
    # Algunos sitios (links acortados, páginas con anti-bot inconsistente)
    # devuelven contenido distinto entre una request y la siguiente. Para no
    # disparar una alerta por una lectura aislada rara, un cambio de estado
    # solo se "confirma" cuando se lo ve 2 veces seguidas.
    CONFIRMATIONS_REQUIRED = 2
    if confirmed_status_before is None:
        # Primer chequeo de este producto: confirmamos directo, sin esperar.
        status = raw_status
        pending_status, pending_streak = None, 0
    elif raw_status == confirmed_status_before:
        # Sigue igual que lo confirmado: no hay nada pendiente.
        status = confirmed_status_before
        pending_status, pending_streak = None, 0
    else:
        # Lectura distinta a lo confirmado: ¿es la primera vez que la vemos,
        # o ya veníamos viéndola?
        if raw_status == prev.get("pending_status"):
            pending_streak = prev.get("pending_streak", 0) + 1
        else:
            pending_streak = 1
        pending_status = raw_status
        if pending_streak >= CONFIRMATIONS_REQUIRED:
            status = raw_status  # se confirma el cambio
            pending_status, pending_streak = None, 0
        else:
            status = confirmed_status_before  # todavía no se confirma, esperamos otra lectura
            log.info("%s: lectura '%s' distinta a lo confirmado ('%s'), esperando confirmación (1/%d)",
                      name, raw_status, confirmed_status_before, CONFIRMATIONS_REQUIRED)

    prev_status = confirmed_status_before

    log.info("%s -> %s %s(antes: %s)", name, status, f"[{price_str}] " if price_str else "", prev_status)

    if had_failures:
        log.info("%s volvió a responder tras varias fallas.", name)

    # --- Notificación de stock ---
    if status == "in_stock" and prev_status in ("out_of_stock", "unknown") and prev_status is not None:
        send_discord(webhook_url, name, url, "stock", status=status, price=price_str, image_url=image_url)
    elif status == "in_stock" and prev_status is None:
        log.info("Primer chequeo de %s: ya está disponible, no se notifica (guardo estado).", name)

    # --- Notificación de baja de precio ---
    target_price = product.get("target_price")
    notify_price_drop = product.get("notify_on_price_drop", True)
    if price_val is not None:
        crossed_target = (
            target_price is not None
            and price_val <= target_price
            and (prev_price_val is None or prev_price_val > target_price)
        )
        plain_drop = (
            notify_price_drop
            and prev_price_val is not None
            and price_val < prev_price_val
            and target_price is None  # si hay target_price, ese aviso ya cubre lo importante
        )
        if crossed_target:
            send_discord(
                webhook_url, name, url, "price_drop", price=price_str, image_url=image_url,
                extra_note=f"Llegó al precio objetivo (${target_price:.2f} o menos).",
            )
        elif plain_drop:
            send_discord(
                webhook_url, name, url, "price_drop", price=price_str, image_url=image_url,
                old_price=prev_price_val,
            )

    state[url] = {
        "status": status,
        "price": price_str,
        "name": name,
        "last_checked": datetime.now(timezone.utc).isoformat(),
        "consecutive_failures": 0,
        "pending_status": pending_status,
        "pending_streak": pending_streak,
    }


def main():
    config = load_json(CONFIG_PATH, {})

    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL") or config.get("discord_webhook_url")
    if not webhook_url:
        log.error("Falta el webhook: seteá DISCORD_WEBHOOK_URL como variable de entorno, o 'discord_webhook_url' en %s", CONFIG_PATH)
        sys.exit(1)

    default_interval = config.get("check_interval_seconds", 300)
    error_notify_after_failures = config.get("error_notify_after_failures", 5)

    products_json_env = os.environ.get("PRODUCTS_JSON")
    if products_json_env:
        try:
            products = json.loads(products_json_env)
        except json.JSONDecodeError as e:
            log.error("PRODUCTS_JSON no es JSON válido: %s", e)
            sys.exit(1)
    else:
        products = config.get("products", [])
    if not products:
        log.warning("No hay productos en 'products'. Agregá al menos uno en %s", CONFIG_PATH)

    state = load_json(STATE_PATH, {})

    for p in products:
        interval = p.get("check_interval_seconds", default_interval)
        log.info("- %s | cada %ds%s", p.get("name") or p["url"], interval,
                  f" | objetivo ${p['target_price']}" if p.get("target_price") else "")
    log.info("Monitoreando %d producto(s) (tick cada 10s, revisando qué le toca a cada uno)", len(products))

    next_check = {p["url"]: 0.0 for p in products}  # 0 = chequear ya en el primer tick

    while True:
        now = time.time()
        changed = False
        for product in products:
            url = product["url"]
            interval = product.get("check_interval_seconds", default_interval)
            if now >= next_check.get(url, 0):
                check_product(product, state, webhook_url, error_notify_after_failures)
                next_check[url] = now + interval
                changed = True
                time.sleep(2)  # pequeño delay entre requests para no golpear los sitios
        if changed:
            save_json(STATE_PATH, state)
        time.sleep(10)  # tick del scheduler: cada 10s revisa a quién le toca


if __name__ == "__main__":
    try:
        if len(sys.argv) >= 3 and sys.argv[1] == "--debug":
            debug_url = sys.argv[2]
            log.info("Modo debug: chequeando %s", debug_url)
            debug_html = fetch(debug_url)
            debug_status = detect_status(debug_html, debug=True)
            log.info("Resultado final: %s", debug_status)
            log.info("Precio detectado: %s", extract_price(debug_html))
        else:
            main()
    except KeyboardInterrupt:
        log.info("Detenido por el usuario.")
