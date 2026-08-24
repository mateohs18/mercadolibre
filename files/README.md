# Stock Monitor → Discord

Monitorea el link de cualquier producto (MercadoLibre, Amazon, eBay, tiendas
genéricas) y te avisa por Discord apenas pasa de "sin stock" a "disponible".

## 1. Instalación en tu VPS

```bash
# Subí la carpeta al VPS (scp, git, etc.) y entrá a ella
cd stock-monitor

# Python 3.9+ recomendado
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 2. Configuración

```bash
cp config.example.json config.json
nano config.json
```

- `discord_webhook_url`: el link del webhook que ya tenés (Discord → Configuración
  del canal → Integraciones → Webhooks → Copiar URL del webhook).
- `check_interval_seconds`: cada cuánto revisa TODOS los productos (300 = 5 min).
  No lo bajes demasiado o corrés riesgo de que el sitio te bloquee la IP.
- `products`: lista de productos. Cada uno necesita `url`. `name` es opcional
  (si no lo ponés, el script intenta sacar el título de la página sola).

Para agregar un producto nuevo, solo agregás un objeto más al array:

```json
{ "url": "https://pegá-el-link-acá" }
```

## 3. Probarlo

```bash
python3 monitor.py
```

Vas a ver logs como:

```
2026-08-24 12:00:01 [INFO] Monitoreando 3 producto(s) cada 300s
2026-08-24 12:00:02 [INFO] Set de Actualización Panini... -> out_of_stock (antes: None)
```

La primera vez que ve un producto guarda el estado pero NO te avisa (para no
spamear apenas arrancás el bot). A partir de ahí, cualquier cambio de
`out_of_stock`/`unknown` → `in_stock` dispara la notificación a Discord.

## 4. Dejarlo corriendo 24/7 (systemd)

Editá `stock-monitor.service` con tu usuario y ruta real, después:

```bash
sudo cp stock-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now stock-monitor
sudo systemctl status stock-monitor
journalctl -u stock-monitor -f   # ver logs en vivo
```

## Cómo detecta disponibilidad (modo genérico)

El script busca frases típicas en el HTML de la página:

- **Sin stock**: "sin stock", "agotado", "producto pausado", "out of stock",
  "sold out", "currently unavailable", etc.
- **Disponible**: "agregar al carrito", "comprar ahora", "add to cart",
  "buy now", "in stock", etc.

También intenta sacar el precio de metadatos comunes (`itemprop="price"`,
`og:price:amount`, JSON embebido con `"price"`).

## Limitaciones a tener en cuenta

- **Amazon y eBay** tienen protecciones anti-bot bastante agresivas. A veces
  van a devolver una página de "verificación" (captcha) en vez del producto
  real. Si eso pasa seguido con una URL puntual, avisame y le agrego lógica
  específica para ese sitio (headers extra, endpoint alternativo, etc.).
- La detección "genérica" por palabras clave funciona bien en la mayoría de
  tiendas, pero algún sitio raro puede no matchear ningún patrón (queda como
  `unknown`, no te va a avisar hasta que puedas ver por qué).
- Revisá los Términos de Servicio del sitio que estés monitoreando; esto está
  pensado para uso personal (avisarte a vos cuando algo vuelve a stock), no
  para scraping masivo o reventa automatizada.
- Si un sitio requiere login para ver stock, este script no va a poder verlo.

## Personalizar más

Si querés que te avise también por **baja de precio** (no solo stock), o que
uno de los productos use un selector específico porque el genérico no le
funciona bien, decime cuál y te lo agrego.
