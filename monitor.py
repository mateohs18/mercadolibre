"""
Stock Monitor -> Discord (Edición Internacional + Cazador de Ofertas)
---------------------------------------------------------------------
Monitorea productos individuales o busca el precio más barato en una 
página de resultados de búsqueda, ignorando accesorios baratos.
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

ALT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15"
)

SESSION = requests.Session()
SESSION.headers.update(BASE_HEADERS)

OUT_OF_STOCK_PATTERNS = [
    r"sin stock", r"agotado", r"pausad[oa]s?\s*/?\s*sin stock", r"producto pausado",
    r"no disponible", r"ya no est[aá] disponible", r"out of stock", r"currently unavailable",
    r"(?<!until )(?<!or until )\bsold out\b", r"unavailable", r"no longer available",
    r"temporarily out of stock"
]

ADD_TO_CART_PATTERNS = [
    r"agregar al carrito", r"comprar ahora", r"a[ñn]adir a la cesta",
    r"add to cart", r"add to basket", r"buy now", r"\bin stock\b"
]

PRICE_PATTERNS = [
    re.compile(r'"price"\s*:\s*"?([\d]+[.,]?\d*)"?', re.I),
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

def parse_price(price_str):
    if not price_str:
        return None
    s = re.sub(r'[^\d.,]', '', str(price_str)).strip()
    if not s:
        return None
    if "," in s and "." in s:
        if s.rfind(".") < s.rfind(","):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s and "." not in s:
        parts = s.split(",")
        if len(parts[-1]) in (1, 2):
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None

def extract_price_and_currency(html, soup, default_currency="$", find_cheapest=False, min_threshold=None):
    """Extrae el precio, ya sea de un producto único o escaneando el más barato de toda la página."""
    
    # -------------------------------------------------------------
    # MODO 1: CAZADOR DE OFERTAS (Busca el más barato en toda la URL)
    # -------------------------------------------------------------
    if find_cheapest:
        currency_regex = r'([€£¥\$]|(?:R\$)|(?:US\$)|(?:ARS)|(?:MXN)|(?:CLP)|(?:COP)|(?:UYU)|(?:PEN))'
        number_regex = r'([\d]{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})?)'

        # Buscamos TODAS las coincidencias en la página
        matches1 = re.findall(f'{currency_regex}\\s?{number_regex}', html, re.IGNORECASE)
        matches2 = re.findall(f'{number_regex}\\s?{currency_regex}', html, re.IGNORECASE)

        valid_prices = []
        
        for curr, price_str in matches1:
            val = parse_price(price_str)
            if val is not None and val > 0:
                # Si configuraste un mínimo (ej: $300) y este precio es menor (ej: funda de $15), lo ignora.
                if min_threshold is not None and val < min_threshold:
                    continue
                valid_prices.append((val, price_str, curr.upper()))
                
        for price_str, curr in matches2:
            val = parse_price(price_str)
            if val is not None and val > 0:
                if min_threshold is not None and val < min_threshold:
                    continue
                valid_prices.append((val, price_str, curr.upper()))

        if not valid_prices:
            return None, default_currency

        # Ordenamos la lista de precios de menor a mayor
        valid_prices.sort(key=lambda x: x[0])
        cheapest = valid_prices[0] # Agarramos el primero (el más barato)
        
        return cheapest[1], cheapest[2] # Devolvemos (precio_str, moneda)

    # -------------------------------------------------------------
    # MODO 2: PRODUCTO INDIVIDUAL (El comportamiento clásico)
    # -------------------------------------------------------------
    og_price = soup.find("meta", property="og:price:amount")
    if og_price and og_price.get("content"):
        og_curr = soup.find("meta", property="og:price:currency")
        curr = og_curr["content"] if og_curr and og_curr.get("content") else default_currency
        return og_price["content"], curr

    schema_price = soup.find(attrs={"itemprop": "price"})
    if schema_price and schema_price.get("content"):
        schema_curr = soup.find(attrs={"itemprop": "priceCurrency"})
        curr = schema_curr["content"] if schema_curr and schema_curr.get("content") else default_currency
        return schema_price["content"], curr

    currency_regex = r'([€£¥\$]|(?:R\$)|(?:US\$)|(?:ARS)|(?:MXN)|(?:CLP)|(?:COP)|(?:UYU)|(?:PEN))'
    number_regex = r'([\d]{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})?)'

    m1 = re.search(f'{currency_regex}\\s?{number_regex}', html, re.IGNORECASE)
    if m1: return m1.group(2), m1.group(1).upper()
    
    m2 = re.search(f'{number_regex}\\s?{currency_regex}', html, re.IGNORECASE)
    if m2: return m2.group(1), m2.group(2).upper()

    for pattern in PRICE_PATTERNS:
        m = pattern.search(html)
        if m: return m.group(1), default_currency

    return None, default_currency


def format_price(value, currency):
    if value is None:
        return "N/A"
    if len(currency) <= 2 or currency.endswith('$') or currency in ['€', '£', '¥']:
        return f"{currency}{value:,.2f}"
    else:
        return f"{value:,.2f} {currency}"


def get_domain(url):
    m = re.search(r"https?://(?:www\.)?([^/]+)", url)
    return m.group(1) if m else url


def detect_status(html, soup, is_search_page=False):
    # Si es una página de búsqueda, asumimos que siempre hay stock si cargó bien
    if is_search_page:
        return "in_stock"

    availability = soup.find(attrs={"itemprop": "availability"})
    if availability and availability.get("href"):
        href = availability["href"].lower()
        if "instock" in href: return "in_stock"
        if "outofstock" in href: return "out_of_stock"
        
    og_avail = soup.find("meta", property="product:availability")
    if og_avail and og_avail.get("content"):
        content = og_avail["content"].lower()
        if "in stock" in content or "instock" in content: return "in_stock"
        if "out of stock" in content or "outofstock" in content: return "out_of_stock"

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

    if out_of_stock_match: return "out_of_stock"
    if add_to_cart_match: return "in_stock"
    return "unknown"


def send_discord(webhook_url, product_name, url, event_type, status=None, price_display=None,
                 image_url=None, old_price_display=None, extra_note=None):
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
    
    if price_display and event_type == "price_drop" and old_price_display:
        fields.append({"name": "Precio anterior", "value": old_price_display, "inline": True})
        fields.append({"name": "Precio nuevo", "value": price_display, "inline": True})
    elif price_display:
        fields.append({"name": "Precio", "value": price_display, "inline": True})
        
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
    
    if image_url: embed["thumbnail"] = {"url": image_url}
        
    payload = {"embeds": [embed]}
    
    if event_type in ("stock", "price_drop"): payload["content"] = "<@911730868316418099>"

    try:
        r = requests.post(webhook_url, json=payload, timeout=15)
        if r.status_code >= 300: log.error("Error enviando a Discord (%s): %s", r.status_code, r.text)
    except Exception as e:
        log.error("Excepción enviando a Discord: %s", e)


def check_product(product, state, webhook_url, error_notify_after_failures):
    url = product["url"]
    configured_name = product.get("name")
    is_search_page = product.get("find_cheapest_on_page", False)
    min_threshold = product.get("min_price_threshold")
    prev = state.get(url, {})

    try:
        html = fetch(url)
    except Exception as e:
        failures = prev.get("consecutive_failures", 0) + 1
        log.warning("No se pudo acceder a %s (falla #%d): %s", configured_name or url, failures, e)
        prev["consecutive_failures"] = failures
        prev.setdefault("name", configured_name or url)
        if failures == error_notify_after_failures:
            send_discord(webhook_url, prev.get("name", url), url, event_type="error",
                         extra_note=f"Falló {failures} veces seguidas. Último error: {e}")
        state[url] = prev
        return

    soup = BeautifulSoup(html, "html.parser")
    name = configured_name or extract_title(soup, url)
    
    # Extraer precio (usando modo individual o modo listado)
    fallback_curr = product.get("currency_symbol", "$")
    price_str, currency_sym = extract_price_and_currency(
        html, soup, fallback_curr, 
        find_cheapest=is_search_page, 
        min_threshold=min_threshold
    )
    price_val = parse_price(price_str)
    
    # Estado (si es página de búsqueda y encontró precio, hay stock)
    raw_status = "in_stock" if (is_search_page and price_str) else detect_status(html, soup, is_search_page)
    
    current_display = format_price(price_val, currency_sym) if price_val else None
    image_url = extract_image(soup)
    
    confirmed_status_before = prev.get("status")
    prev_price_val = parse_price(prev.get("price"))
    prev_currency = prev.get("currency", currency_sym)
    prev_display = format_price(prev_price_val, prev_currency) if prev_price_val else None
    
    had_failures = prev.get("consecutive_failures", 0) >= error_notify_after_failures

    CONFIRMATIONS_REQUIRED = 2
    if confirmed_status_before is None:
        status = raw_status
        pending_status, pending_streak = None, 0
    elif raw_status == confirmed_status_before:
        status = confirmed_status_before
        pending_status, pending_streak = None, 0
    else:
        if raw_status == prev.get("pending_status"): pending_streak = prev.get("pending_streak", 0) + 1
        else: pending_streak = 1
        pending_status = raw_status
        if pending_streak >= CONFIRMATIONS_REQUIRED:
            status = raw_status 
            pending_status, pending_streak = None, 0
        else:
            status = confirmed_status_before 
            log.info("%s: lectura '%s' distinta a lo confirmado ('%s'), esperando confirmación (1/%d)",
                      name, raw_status, confirmed_status_before, CONFIRMATIONS_REQUIRED)

    prev_status = confirmed_status_before

    log.info("%s -> %s %s(antes: %s)", name, status, f"[{current_display}] " if current_display else "", prev_status)

    if had_failures: log.info("%s volvió a responder tras varias fallas.", name)

    # Notificaciones de Stock
    if status == "in_stock" and prev_status in ("out_of_stock", "unknown") and prev_status is not None:
        send_discord(webhook_url, name, url, "stock", status=status, price_display=current_display, image_url=image_url)
    elif status == "in_stock" and prev_status is None:
        log.info("Primer chequeo de %s: ya está disponible, no se notifica.", name)

    # Notificaciones de Precio
    target_price = product.get("target_price")
    notify_price_drop = product.get("notify_on_price_drop", True)
    
    if price_val is not None:
        crossed_target = (
            target_price is not None
            and price_val <= target_price
            and prev_price_val is not None 
            and prev_price_val > target_price 
        )
        plain_drop = (
            notify_price_drop
            and prev_price_val is not None
            and price_val < prev_price_val
            and target_price is None  
        )
        
        if crossed_target:
            target_display = format_price(target_price, currency_sym)
            extra_msg = f"¡Llegó al precio objetivo! ({target_display} o menos)."
            if is_search_page: extra_msg += " (Oferta encontrada en listado)"
            send_discord(webhook_url, name, url, "price_drop", price_display=current_display, image_url=image_url, extra_note=extra_msg)
        elif plain_drop:
            extra_msg = "Se encontró un nuevo precio más bajo en este listado." if is_search_page else None
            send_discord(webhook_url, name, url, "price_drop", price_display=current_display, image_url=image_url, old_price_display=prev_display, extra_note=extra_msg)
        elif target_price is not None and prev_price_val is None and price_val <= target_price:
            log.info("Primer chequeo de %s: ya está en el precio objetivo, no se notifica.", name)

    state[url] = {
        "status": status,
        "price": price_str,
        "currency": currency_sym,
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
        log.error("Falta el webhook. Configuralo en %s", CONFIG_PATH)
        sys.exit(1)

    default_interval = config.get("check_interval_seconds", 300)
    error_notify_after_failures = config.get("error_notify_after_failures", 5)

    products_json_env = os.environ.get("PRODUCTS_JSON")
    if products_json_env:
        products = json.loads(products_json_env)
    else:
        products = config.get("products", [])
        
    if not products:
        log.warning("No hay productos configurados.")

    state = load_json(STATE_PATH, {})

    for p in products:
        interval = p.get("check_interval_seconds", default_interval)
        log.info("- %s | cada %ds%s", p.get("name") or p["url"], interval,
                  f" | objetivo {p['target_price']}" if p.get("target_price") else "")
    log.info("Monitoreando %d producto(s)...", len(products))

    next_check = {p["url"]: 0.0 for p in products} 

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
                time.sleep(2)  
        if changed:
            save_json(STATE_PATH, state)
        time.sleep(10)  

if __name__ == "__main__":
    main()
