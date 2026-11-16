FROM python:3.12-slim

WORKDIR /srv

# Önce sadece bağımlılıklar: kod değişince bu katman cache'ten gelir, build hızlanır
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
