import { ipcRenderer } from 'electron';
import { KONZEPT_GAST_KANAL, forwardableGuestMessage } from '@x/shared/dist/konzept.js';

// Vorschalt-Skript der Konzept-Gaeste (<webview partition="konzept">, sandbox, contextIsolation). Konzept-Seiten
// senden unveraendert parent.postMessage({ type, url | pfad }, '*'); im Gast ist parent === window, die Nachricht
// landet also nur im eigenen Fenster. Dieses Skript hoert dort zu (isolierte Welt), nimmt nur Nachrichten des
// Fensters selbst (keine verschachtelten Rahmen), prueft sie mit derselben Funktion wie die Oberflaeche und reicht
// { type, url | pfad } an das <webview>-Element weiter. Es legt nichts in die Seite.
window.addEventListener('message', (event: MessageEvent) => {
  if (event.source !== window) return;
  const nachricht = forwardableGuestMessage(event.data);
  if (nachricht !== null) ipcRenderer.sendToHost(KONZEPT_GAST_KANAL, nachricht);
});
