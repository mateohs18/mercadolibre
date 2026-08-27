"""
Flight Price Watcher — LIM -> CLO
Consulta precios reales via Travelpayouts Data API (la API de datos de
Aviasales: gratis, autoservicio, sin aprobacion de partner ni minimos de
trafico) y avisa por Discord cuando el precio baja.

Nota: Amadeus self-service fue decomisionado el 17 de julio de 2026, por eso
este script usa Travelpayouts en su lugar. No usa scraping ni evade ningun
captcha: usa una API publica pensada exactamente para este caso de uso.
"""

import os
import json
import sys
import requests

TRAVELPAYOUTS_TOKEN = os.environ["TRAVELPAYOUTS_TOKEN"]
DISCORD_WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]
DISCORD_MENTION_USER_ID = os.environ.get("DISCORD_MENTION_USER_ID", "").strip()

BASE_URL = "https://api.travelpayouts.com"

# En GitHub Actions dejamos el default (se commitea de vuelta al repo).
# En Railway, seteá STATE_FILE_PATH=/data/state.json y montá un Volume en /data
# para que el historial de precios sobreviva entre corridas del cron.
STATE_FILE = os.environ.get("STATE_FILE_PATH", "state.json")

# --- Definí acá los viajes que querés monitorear (origen, destino, fechas) ---
TRIPS = [
    {
        "id": "lim-clo-oct05-nov01",
        "origin": "LIM",
        "destination": "CLO",
        "departure_date": "2026-10-05",
        "return_date": "2026-11-01",
        "adults": 1,
    },
    {
        "id": "lim-clo-oct05-oct18",
        "origin": "LIM",
        "destination": "CLO",
        "departure_date": "2026-10-05",
        "return_date": "2026-10-18",
        "adults": 1,
    },
]


AVIASALES_BASE = "https://www.aviasales.com"


def _build_search_link(origin, destination, departure_date, return_date=None):
    """Arma un link de busqueda en aviasales.com (formato: ORIGENddMMDESTINOddMM1)."""
    dep = f"{departure_date[8:10]}{departure_date[5:7]}"
    code = f"{origin}{dep}{destination}"
    if return_date:
        ret = f"{return_date[8:10]}{return_date[5:7]}"
        code += ret
    code += "1"  # 1 adulto
    return f"{AVIASALES_BASE}/search/{code}"


def search_cheapest_price(trip):
    """Devuelve un dict: {price, exact_match, found_date, link} o None."""
    # Intento 1: fechas exactas (mas preciso, pero esta ruta tiene poco trafico
    # y puede no tener cache para el dia puntual).
    params = {
        "origin": trip["origin"],
        "destination": trip["destination"],
        "departure_at": trip["departure_date"],
        "return_at": trip["return_date"],
        "unique": "false",
        "sorting": "price",
        "direct": "false",
        "currency": "cop",
        "limit": 5,
        "token": TRAVELPAYOUTS_TOKEN,
    }
    resp = requests.get(f"{BASE_URL}/aviasales/v3/prices_for_dates", params=params)
    if resp.status_code != 200:
        print(f"[{trip['id']}] Error {resp.status_code} (fechas exactas): {resp.text[:300]}")
    else:
        offers = resp.json().get("data", [])
        if offers:
            cheapest = min(offers, key=lambda o: float(o["price"]))
            raw_link = cheapest.get("link") or cheapest.get("ticket_link")
            link = f"{AVIASALES_BASE}{raw_link}" if raw_link else _build_search_link(
                trip["origin"], trip["destination"], trip["departure_date"], trip["return_date"]
            )
            return {
                "price": float(cheapest["price"]),
                "exact_match": True,
                "found_departure": trip["departure_date"],
                "found_return": trip["return_date"],
                "link": link,
            }
        print(f"[{trip['id']}] Sin datos para fecha exacta, probando el mes completo...")

    # Intento 2 (fallback): mismo par origen/destino y MISMA fecha de regreso
    # (asi cada viaje conserva su propia duracion de estadia), pero sin fijar
    # el dia exacto de ida -> usa el endpoint de calendario, que suele tener
    # mas cobertura para rutas de bajo trafico. El precio resultante puede
    # corresponder a otro dia del mes, asi que lo marcamos como NO exacto.
    month = trip["departure_date"][:7]  # "2026-10-05" -> "2026-10"
    params2 = {
        "origin": trip["origin"],
        "destination": trip["destination"],
        "depart_date": month,
        "return_date": trip["return_date"][:7],  # mes, no dia exacto (menos restrictivo)
        "calendar_type": "departure_date",
        "currency": "cop",
        "token": TRAVELPAYOUTS_TOKEN,
    }
    resp2 = requests.get(f"{BASE_URL}/v1/prices/calendar", params=params2)
    if resp2.status_code != 200:
        print(f"[{trip['id']}] Error {resp2.status_code} (mes completo): {resp2.text[:300]}")
        return None

    body2 = resp2.json()
    day_prices = body2.get("data", {}) if body2.get("success") else {}
    if not day_prices:
        print(f"[{trip['id']}] Sin ofertas disponibles ni siquiera para el mes completo "
              f"({month}). Respuesta cruda: {resp2.text[:300]}")
        return None

    found_date, cheapest_day = min(day_prices.items(), key=lambda kv: float(kv[1]["price"]))
    print(f"[{trip['id']}] Usando precio del mes completo (fecha real: {found_date}, "
          f"no la fecha exacta pedida) como referencia.")

    return {
        "price": float(cheapest_day["price"]),
        "exact_match": False,
        "found_departure": found_date,
        "found_return": cheapest_day.get("return_at", trip["return_date"])[:10]
        if cheapest_day.get("return_at") else trip["return_date"],
        "link": _build_search_link(trip["origin"], trip["destination"], found_date),
    }


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def format_cop(amount):
    # 1850000 -> "$1.850.000 COP" (estilo colombiano: punto como separador de miles, sin decimales)
    return f"${amount:,.0f} COP".replace(",", ".")


def notify_discord(trip, result, previous_price):
    mention = f"<@{DISCORD_MENTION_USER_ID}> " if DISCORD_MENTION_USER_ID else ""
    price = result["price"]

    date_line = f"({result['found_departure']} - {result['found_return']})"
    if not result["exact_match"]:
        date_line += (
            f" ⚠️ fecha aproximada — no había precio cacheado para "
            f"{trip['departure_date']}/{trip['return_date']} exactas, esta es la "
            f"más barata encontrada ese mes"
        )

    source_line = (
        f"🔗 [Ver / reservar]({result['link']}) — vía Aviasales "
        f"(agrega varias agencias; el precio final puede variar según la que elijas)"
    )

    if previous_price is None:
        content = (
            f"{mention}👀 Empecé a monitorear **{trip['origin']} → {trip['destination']}** "
            f"{date_line}\nPrecio actual: **{format_cop(price)}**\n{source_line}"
        )
    else:
        diff = previous_price - price
        content = (
            f"{mention}📉 ¡Bajó el precio! **{trip['origin']} → {trip['destination']}** "
            f"{date_line}\n"
            f"Antes: {format_cop(previous_price)} → Ahora: **{format_cop(price)}** "
            f"(bajó {format_cop(diff)})\n{source_line}"
        )

    payload = {
        "content": content,
        # Sin esto, Discord puede ignorar la mención por seguridad en algunos casos.
        "allowed_mentions": {"users": [DISCORD_MENTION_USER_ID]} if DISCORD_MENTION_USER_ID else {},
    }
    resp = requests.post(DISCORD_WEBHOOK_URL, json=payload)
    if resp.status_code >= 300:
        print(f"[discord] Error {resp.status_code} al notificar: {resp.text[:300]}")


def main():
    state = load_state()
    changed = False

    for trip in TRIPS:
        result = search_cheapest_price(trip)
        if result is None:
            continue

        price = result["price"]
        previous_price = state.get(trip["id"])
        print(f"[{trip['id']}] Precio actual: {format_cop(price)} "
              f"(exacto={result['exact_match']}) | Previo: {previous_price}")

        if previous_price is None:
            notify_discord(trip, result, None)
            state[trip["id"]] = price
            changed = True
        elif price < previous_price:
            notify_discord(trip, result, previous_price)
            state[trip["id"]] = price
            changed = True
        # si el precio subió o quedó igual, no molestamos con avisos

    if changed:
        save_state(state)


if __name__ == "__main__":
    sys.exit(main())
