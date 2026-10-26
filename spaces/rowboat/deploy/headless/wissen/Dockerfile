FROM python:3.11-slim
# Dienst rowboat-wissen (Bedeutungssuche). Bau-Kontext ~/rowboat-headless/wissen/ (bauen.ps1):
# Dockerfile, requirements.txt und das Paket wissen_suche/. Kein Modell im Image: die Vektoren
# kommen per HTTP vom PC.
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /opt
COPY requirements.txt /opt/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY wissen_suche/ /opt/wissen_suche/
USER 1000:1000
CMD ["python", "-m", "wissen_suche.server"]
