FROM python:3.10-slim

WORKDIR /app

# Install wget for health check
RUN apt-get update && apt-get install -y wget && rm -rf /var/lib/apt/lists/*

COPY alert-service/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY alert-service/alert_service.py ./alert_service.py

RUN mkdir -p /app/data

ENV MQTT_BROKER=mqtt-broker
ENV MQTT_PORT=1883
ENV CATALOG_URL=http://catalog-service:8080
ENV CONTROLLER_URL=http://controller-service:8001
ENV ALERT_DATA_DIR=/app/data
ENV ALERT_PORT=5002

CMD ["python", "-u", "alert_service.py"]
