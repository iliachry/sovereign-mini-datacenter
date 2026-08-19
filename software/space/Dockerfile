FROM python:3.11-alpine

WORKDIR /app

COPY . /app/software/space
ENV PYTHONPATH=/app

EXPOSE 9102

HEALTHCHECK --interval=15s --timeout=5s --retries=3 \
  CMD wget -q --spider http://localhost:9102/health || exit 1

ENTRYPOINT ["python3", "/app/software/space/space_exporter.py"]