FROM node:22-bookworm
# Rowboat-Server ohne Oberflaeche (dist-headless aus apps/x/apps/server, gebaut mit bauen.ps1).
# bookworm statt alpine: node-pty wird hier kompiliert und braucht gcc/make/python3.
WORKDIR /app
COPY app/package.json ./
RUN npm install --omit=dev
COPY app/ ./
COPY start.sh /usr/local/bin/rowboat-start
RUN chmod 0755 /usr/local/bin/rowboat-start
# node = uid 1000 = debian auf der VM: Dateien in /home/debian/rowboat-data gehoeren debian.
USER node
ENTRYPOINT ["/usr/local/bin/rowboat-start"]
CMD ["node", "rowboat-server.cjs"]
