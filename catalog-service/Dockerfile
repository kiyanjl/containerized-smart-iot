FROM python:3.10-slim

WORKDIR /app

RUN pip install cherrypy

COPY catalog_service.py .
COPY catalog.json .

EXPOSE 8080

CMD ["python", "catalog_service.py"]
