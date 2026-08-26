# Usamos una versión ligera de Python
FROM python:3.10-slim

# Instalamos wget, descargamos el instalador directo de Chrome y lo instalamos
RUN apt-get update && apt-get install -y wget unzip \
    && wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && apt-get install -y ./google-chrome-stable_current_amd64.deb \
    && rm google-chrome-stable_current_amd64.deb \
    && rm -rf /var/lib/apt/lists/*

# Preparamos la carpeta del bot
WORKDIR /app

# Copiamos e instalamos las librerías de Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiamos tu código (monitor.py y config.json)
COPY . .

# Comando para arrancar el bot
CMD ["python", "monitor.py"]
