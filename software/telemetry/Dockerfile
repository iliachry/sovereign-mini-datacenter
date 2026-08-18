FROM python:3.11-alpine

WORKDIR /app

COPY power_exporter.py /app/power_exporter.py

ENV EXPORTER_PORT=9101
ENV SIMULATE_POWER_DATA=true

EXPOSE 9101

HEALTHCHECK --interval=15s --timeout=5s --retries=3 \
  CMD wget -q --spider http://localhost:9101/health || exit 1

ENTRYPOINT ["python", "/app/power_exporter.py"]