FROM python:3.12-slim
# Dienst rowboat-werkzeuge (MCP vibemind-werkzeuge). Bau-Kontext ~/rowboat-headless/werkzeuge/ (bauen.ps1):
# Dockerfile und die drei Module. Keine Geheimnisse im Image: Tokens und PC-Schluessel kommen zur
# Laufzeit aus der env_file des Dienstes (compose.yml).
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN pip install --no-cache-dir mcp==1.27.0
COPY server.py github_lesen.py pc_suche_relay.py /opt/werkzeuge/
USER 1000:1000
CMD ["python3", "/opt/werkzeuge/server.py"]
