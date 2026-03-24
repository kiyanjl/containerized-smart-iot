FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY controller_service.py .
COPY rule_engine.py .
COPY rules_cache.json .

CMD ["python", "-u", "controller_service.py"]
