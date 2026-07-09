FROM python:3.13-slim

# Робоча директорія
WORKDIR /app

# Системні залежності (за потреби)
RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Спершу копіюємо лише requirements для кешування шару
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копіюємо решту коду
COPY . .

# Демо-треки мають бути в репозиторії (якщо їх немає — створити)
RUN mkdir -p static/tracks static

# Порт береється з змінної оточення KOYEB_SERVICE_PORT (або 8000)
ENV PORT=8000
EXPOSE 8000

# Запуск сервера
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"]
