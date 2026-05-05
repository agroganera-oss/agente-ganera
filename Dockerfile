# Usamos la imagen oficial de Playwright (es pesada pero trae TODO lo necesario)
FROM mcr.microsoft.com/playwright/python:v1.45.0-jammy

# Evitar que Python guarde caché de archivos .pyc
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Instalar dependencias de Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el código
COPY . .

# IMPORTANTE: Instalar los navegadores dentro del contenedor
RUN playwright install chromium
RUN playwright install-deps chromium

# Puerto que usa Render por defecto
EXPOSE 10000

# Comando de inicio
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "10000"]
