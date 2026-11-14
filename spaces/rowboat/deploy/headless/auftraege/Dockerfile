FROM python:3.12-slim
# Dienst rowboat-auftraege (Konzept-Flaeche Teil B). Bau-Kontext ~/rowboat-headless/auftraege/ (bauen.ps1):
# Dockerfile und die zwei Module. Keine Schluessel im Image: Dienst- und Sales-Schluessel kommen zur Laufzeit
# als nur lesende Datei-Mounts (compose.yml).
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN pip install --no-cache-dir mcp==1.27.0
COPY server.py konzept_lesen.py /opt/auftraege/
USER 1000:1000
CMD ["python3", "/opt/auftraege/server.py"]
