# Flight Price Watcher (LIM → CLO)

Monitorea el precio de tus dos itinerarios usando **Travelpayouts Data API**
(la API de datos de Aviasales — gratis, autoservicio, sin captchas ni scraping)
y te avisa por **Discord** cuando el precio baja. Corre gratis en la nube vía
**GitHub Actions**, cada 3 horas, sin que tengas que dejar tu compu prendida.

> **Nota:** originalmente iba a usar Amadeus, pero su portal self-service fue
> decomisionado el 17 de julio de 2026 (solo quedó acceso Enterprise vía
> contrato comercial). Por eso el script usa Travelpayouts, que sigue con
> registro instantáneo y gratuito.

## 1. Crear cuenta gratis en Travelpayouts

1. Andá a https://www.travelpayouts.com/ y registrate (es gratis, es una red
   de afiliados de viajes — no necesitás tener un sitio web para usar la API
   de datos).
2. Una vez logueado, andá a tu perfil → sección **API token** (o
   **"Access to API"**) y copiá tu token.
3. Los precios que devuelve son de una caché (se actualizan periódicamente,
   no en tiempo real estricto), pero es justamente el uso pensado para esto:
   trackear tendencias de precio, no hacer una búsqueda de booking en vivo.

## 2. Crear el webhook de Discord

1. En tu servidor de Discord: **Configuración del servidor → Integraciones → Webhooks → Nuevo Webhook**.
2. Elegí el canal donde querés recibir los avisos.
3. Copiá la **URL del webhook**.

## 3. Subir este proyecto a GitHub

1. Creá un repositorio nuevo (puede ser privado) en GitHub.
2. Subí todos estos archivos (`monitor.py`, `requirements.txt`, `state.json`,
   `.github/workflows/flight_watch.yml`, este README).

```bash
git init
git add .
git commit -m "Flight price watcher"
git branch -M main
git remote add origin https://github.com/TU_USUARIO/TU_REPO.git
git push -u origin main
```

## 4. Configurar los secrets en GitHub

En tu repo: **Settings → Secrets and variables → Actions → New repository secret**.
Agregá estos dos:

| Nombre                  | Valor                                  |
|--------------------------|-----------------------------------------|
| `TRAVELPAYOUTS_TOKEN`    | tu token de Travelpayouts               |
| `DISCORD_WEBHOOK_URL`    | la URL del webhook de Discord          |

## 5. ¡Listo!

El workflow ya está programado para correr solo cada 3 horas
(`.github/workflows/flight_watch.yml`). También podés probarlo manualmente:
pestaña **Actions** de tu repo → **Flight Price Watcher** → **Run workflow**.

La primera vez que corre para cada viaje te va a avisar el precio inicial.
Las próximas veces, solo te va a escribir si el precio **bajó** respecto al
mínimo visto hasta ahora.

## Personalización

- **Cambiar frecuencia**: editá la línea `cron` en el workflow
  (por ejemplo `"0 */1 * * *"` para cada hora).
- **Agregar/quitar viajes**: editá la lista `TRIPS` en `monitor.py`.
- **Umbral de precio en vez de "cualquier baja"**: si preferís que solo te
  avise cuando el precio esté por debajo de un número fijo (ej. $250), decime
  y te ajusto la lógica.

## Por qué esto y no un bot contra Skyscanner

Skyscanner pone captchas justamente para bloquear el scraping automatizado;
saltárselos violaría sus términos de uso. Travelpayouts, en cambio, es una
API pública de datos de vuelos pensada para que la uses así, sin
restricciones artificiales que romper.
