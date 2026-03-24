FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY catalog_service.py .
COPY catalog.json .

EXPOSE 8080

CMD ["python", "catalog_service.py"]
