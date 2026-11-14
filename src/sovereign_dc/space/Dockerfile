FROM python:3.11-alpine

WORKDIR /app

# The files in this directory are byte-identical mirrors of
# src/sovereign_dc/space/, so they are laid out under the same package path
# (sovereign_dc.space) to keep their absolute imports working unchanged.
COPY . /app/sovereign_dc/space
RUN touch /app/sovereign_dc/__init__.py \
    && find /app/sovereign_dc -name "__pycache__" -type d -prune -exec rm -rf {} + \
    && mkdir -p /var/spool/dtn

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1

EXPOSE 9102

HEALTHCHECK --interval=15s --timeout=5s --retries=3 \
  CMD wget -q --spider http://localhost:9102/health || exit 1

ENTRYPOINT ["python3", "-m", "sovereign_dc.space.space_exporter"]
