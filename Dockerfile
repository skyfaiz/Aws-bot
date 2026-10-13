FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Kolkata

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# uid 1000 matches the default "ubuntu" user on EC2, so bind-mounted logs/data/config stay writable
RUN useradd --uid 1000 --create-home bot && mkdir -p logs data && chown -R bot:bot /app
USER bot

CMD ["python", "telegram_service.py"]
