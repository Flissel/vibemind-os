FROM node:22-bookworm-slim
# Claude-Shim fuer Rowboat (OpenAI-kompatibel, :8121) mit Budget-Huelle. Kein Modell auf dem Mini-PC:
# die Claude-CLI ruft das Abo in der Cloud auf (Anmeldung per CLAUDE_CODE_OAUTH_TOKEN aus env_file).
# Die vier fremden Dateien legt bauen.ps1 beim Uebertragen in diesen Bau-Kontext (siehe README.md).
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-yaml ca-certificates \
 && rm -rf /var/lib/apt/lists/*
# Gleiche Version wie am PC (claude --version am 25.10.2026).
RUN npm install -g @anthropic-ai/claude-code@2.1.268 && npm cache clean --force
WORKDIR /opt/shim
COPY claude_code_openai_shim.py claude_code_openai_tools.py claude_budget.py agent_budget.yaml rowboat_shim_huelle.py ./
COPY start.sh /usr/local/bin/rowboat-shim-start
RUN chmod 0644 /opt/shim/* && chmod 0755 /usr/local/bin/rowboat-shim-start
ENV VIBEMIND_BUDGET_CONFIG=/opt/shim/agent_budget.yaml \
    CLAUDE_CODE_CLI=/usr/local/bin/claude \
    DISABLE_AUTOUPDATER=1 \
    PYTHONUNBUFFERED=1
# node = uid 1000 = debian auf der VM: die Zaehldateien in ~/rowboat-headless/budget gehoeren debian.
USER node
ENTRYPOINT ["/usr/local/bin/rowboat-shim-start"]
CMD ["python3", "/opt/shim/rowboat_shim_huelle.py", "--host", "127.0.0.1", "--port", "8121"]
