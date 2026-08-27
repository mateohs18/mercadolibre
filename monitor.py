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


def search_cheapest_price(trip):
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
        "currency": "usd",
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
            return float(cheapest["price"])
        print(f"[{trip['id']}] Sin datos para fecha exacta, probando el mes completo...")

    # Intento 2 (fallback): mismo par origen/destino, sin filtrar por fecha
    # exacta -> usa el endpoint de calendario, que suele tener mas cobertura
    # para rutas de bajo trafico.
    month = trip["departure_date"][:7]  # "2026-10-05" -> "2026-10"
    params2 = {
        "origin": trip["origin"],
        "destination": trip["destination"],
        "depart_date": month,
        "calendar_type": "departure_date",
        "currency": "usd",
        "token": TRAVELPAYOUTS_TOKEN,
    }
    resp2 = requests.get(f"{BASE_URL}/v1/prices/calendar", params=params2)
    if resp2.status_code != 200:
        print(f"[{trip['id']}] Error {resp2.status_code} (mes completo): {resp2.text[:300]}")
        return None

    body2 = resp2.json()
    if not body2.get("success") or not body2.get("data"):
        print(f"[{trip['id']}] Sin ofertas disponibles ni siquiera para el mes completo "
              f"({month}). Respuesta cruda: {resp2.text[:300]}")
        return None

    day_prices = body2["data"].get(trip["destination"], {})
    if not day_prices:
        print(f"[{trip['id']}] Sin ofertas disponibles. Respuesta cruda: {resp2.text[:300]}")
        return None

    cheapest_day = min(day_prices.values(), key=lambda o: float(o["price"]))
    print(f"[{trip['id']}] Usando precio del mes completo (no de la fecha exacta) como referencia.")
    return float(cheapest_day["price"])


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def notify_discord(trip, price, previous_price):
    mention = f"<@{DISCORD_MENTION_USER_ID}> " if DISCORD_MENTION_USER_ID else ""

    if previous_price is None:
        content = (
            f"{mention}👀 Empecé a monitorear **{trip['origin']} → {trip['destination']}** "
            f"({trip['departure_date']} - {trip['return_date']}). "
            f"Precio actual: **${price:.2f} USD**"
        )
    else:
        diff = previous_price - price
        content = (
            f"{mention}📉 ¡Bajó el precio! **{trip['origin']} → {trip['destination']}** "
            f"({trip['departure_date']} - {trip['return_date']})\n"
            f"Antes: ${previous_price:.2f} USD → Ahora: **${price:.2f} USD** "
            f"(bajó ${diff:.2f})"
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
        price = search_cheapest_price(trip)
        if price is None:
            continue

        previous_price = state.get(trip["id"])
        print(f"[{trip['id']}] Precio actual: ${price:.2f} | Previo: {previous_price}")

        if previous_price is None:
            notify_discord(trip, price, None)
            state[trip["id"]] = price
            changed = True
        elif price < previous_price:
            notify_discord(trip, price, previous_price)
            state[trip["id"]] = price
            changed = True
        # si el precio subió o quedó igual, no molestamos con avisos

    if changed:
        save_state(state)


if __name__ == "__main__":
    sys.exit(main())
