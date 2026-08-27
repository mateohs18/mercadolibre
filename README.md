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
| `DISCORD_MENTION_USER_ID`| (opcional) tu ID de usuario de Discord, para que te mencione en cada aviso |

## 5. ¡Listo!

El workflow ya está programado para correr solo cada 3 horas
(`.github/workflows/flight_watch.yml`). También podés probarlo manualmente:
pestaña **Actions** de tu repo → **Flight Price Watcher** → **Run workflow**.

La primera vez que corre para cada viaje te va a avisar el precio inicial.
Las próximas veces, solo te va a escribir si el precio **bajó** respecto al
mínimo visto hasta ahora.

## Alternativa: correrlo en Railway en vez de GitHub Actions

Si preferís Railway:

1. **No lo deployes como servicio web** — es un script, no un servidor. En tu
   proyecto de Railway, creá el servicio como **Cron Job** (no "Empty Service"
   con puerto expuesto).
2. El archivo `railway.json` ya incluido le dice a Railway el comando de
   arranque (`python monitor.py`) y la frecuencia (`*/30 * * * *` = cada 30 min).
3. **Importante — persistencia**: cada corrida del cron en Railway usa un
   contenedor nuevo y descartable. Sin storage persistente, `state.json` se
   reinicia cada vez y vas a recibir el aviso de "precio inicial" en cada
   corrida en vez de solo cuando baja. Para evitarlo:
   - En el servicio, andá a **Settings → Volumes** y creá un Volume montado
     en `/data`.
   - Agregá la variable de entorno `STATE_FILE_PATH=/data/state.json`.
4. Variables de entorno a configurar en el servicio (Settings → Variables):
   `TRAVELPAYOUTS_TOKEN`, `DISCORD_WEBHOOK_URL`, y `STATE_FILE_PATH` (si usás Volume).

Si no te complica el tema del Volume, **GitHub Actions es más simple** para
este caso puntual (persistencia gratis vía git, sin configurar storage aparte).

## ¿Por qué no me notificó todavía?

Si configuraste todo y aún no llegó ningún mensaje a Discord, revisá en este orden:

1. **¿Corrió el workflow al menos una vez?**
   - GitHub Actions: pestaña **Actions** de tu repo → tenés que ver una
     ejecución de "Flight Price Watcher". Si no hay ninguna, andá a
     **Run workflow** manualmente para forzar la primera corrida (no hace
     falta esperar al cron).
   - Railway: en el servicio, pestaña **Deployments** o **Logs** — tiene que
     mostrar una ejecución reciente del cron.
2. **Miralo en los logs.** Si `monitor.py` tira error, vas a verlo ahí (por
   ejemplo: token inválido, secret mal escrito, o `KeyError` si falta una
   variable de entorno). Un error típico es un secret con espacios de más al
   copiar/pegar el token.
3. **¿La API devolvió ofertas?** El script imprime `Sin ofertas disponibles.`
   si Travelpayouts no tiene datos cacheados para esa ruta/fechas exactas
   (pasa más en rutas con poco volumen, como LIM-CLO). Si ves ese mensaje en
   los logs, no es un bug — simplemente esa fuente no tiene precio guardado
   para esas fechas todavía.
4. **¿Revisaste el canal correcto?** El webhook solo postea en el canal que
   elegiste al crearlo.
5. **La primera notificación no es "bajó el precio"**, es un aviso de
   "empecé a monitorear" con el precio inicial. Si nunca viste ni siquiera
   ese primer mensaje, el problema está en los pasos 1-4.

Contame qué ves en los logs de la última corrida y te ayudo a diagnosticarlo.

## Sobre la frecuencia (cada 30 min)

Funciona, pero un dato a tener en cuenta: los precios de Travelpayouts vienen
de una caché que no se actualiza al segundo — suele refrescarse cada tanto
(horas), no en tiempo real. Escanear cada 30 min no rompe nada ni te van a
banear, pero es probable que varias corridas seguidas devuelvan el mismo
precio cacheado. No hace falta bajar más la frecuencia de la que la fuente
realmente actualiza, pero tampoco molesta si querés dejarlo así por las dudas.

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
