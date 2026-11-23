# Image des Dienstes auftragsbuch (MCP-Adapter ueber dem Schema `auftrag`).
# Build-Kontext ist die Repo-Wurzel, weil der Dienst spaces/__init__.py und die Vertraege
# spaces/_contract/*.contract.yaml braucht (docker-compose.yml: context ../.., dockerfile
# spaces/auftragsbuch/Dockerfile). Gebaut wird nur auf dem Mini-PC:
#   AUFTRAGSBUCH_TAG=<git-kurz-sha> docker compose build
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app \
    AUFTRAGSBUCH_VERTRAG_ORDNER=/app/spaces/_contract \
    AUFTRAGSBUCH_HOST=127.0.0.1

RUN groupadd --system --gid 10002 auftragsbuch \
    && useradd --system --uid 10002 --gid auftragsbuch --no-create-home --shell /usr/sbin/nologin auftragsbuch

WORKDIR /app
COPY spaces/auftragsbuch/requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt && rm /tmp/requirements.txt

COPY spaces/__init__.py /app/spaces/__init__.py
COPY spaces/auftragsbuch/*.py /app/spaces/auftragsbuch/
COPY spaces/auftragsbuch/rowboat/*.py /app/spaces/auftragsbuch/rowboat/
COPY spaces/_contract/*.contract.yaml /app/spaces/_contract/

USER auftragsbuch
EXPOSE 8172
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8172/gesund', timeout=4)"]
CMD ["python", "-m", "spaces.auftragsbuch.mcp_server"]
