# Usamos una versión ligera de Python
FROM python:3.10-slim

# Instalamos las dependencias del sistema y Google Chrome oficial
RUN apt-get update && apt-get install -y wget gnupg unzip \
    && wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - \
    && sh -c 'echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google-chrome.list' \
    && apt-get update \
    && apt-get install -y google-chrome-stable \
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
