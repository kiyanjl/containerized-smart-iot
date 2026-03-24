FROM python:3.10-slim

WORKDIR /app

COPY alert-service/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY alert-service/alert_service.py ./alert_service.py
COPY controller-service/rule_engine.py ./rule_engine.py

RUN mkdir -p /app/data

CMD ["python", "-u", "alert_service.py"]
