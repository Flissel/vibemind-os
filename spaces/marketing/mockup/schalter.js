// Schalter Marketing -> Sales (sales-claw Spec 2026-09-25-marketing-
// schalter-design.md §3.3). Der Rueckweg kommt aus ?zurueck= und gilt nur
// als https-Adresse im eigenen Tailnet (*.tail6c7d61.ts.net) — sonst waere
// die Seite eine offene Umleitung. Der letzte gueltige Wert wird gemerkt;
// ein Speicher, der wirft (privates Fenster), darf nichts kaputt machen.
(function (wurzel) {
  "use strict";
  var SCHLUESSEL = "sales_rueckweg";
  // Nur das EIGENE Tailnet. Ein beliebiges *.ts.net liesse jede oeffentliche
  // Tailscale-Funnel-Adresse eines Fremden als Rueckweg zu. Wechselt der
  // Tailnet-Name, muss dieser Wert mit.
  var EIGENES_TAILNET = ".tail6c7d61.ts.net";

  function rueckwegPruefen(roh) {
    if (typeof roh !== "string" || roh.indexOf("https://") !== 0) return "";
    var u;
    try { u = new URL(roh); } catch (e) { return ""; }
    if (u.protocol !== "https:" || u.username || u.password) return "";
    var host = u.hostname.toLowerCase();
    if (!/^[a-z0-9-]+(\.[a-z0-9-]+)*\.ts\.net$/.test(host)) return "";
    if (host.split(".").length < 3) return "";
    var rest = host.slice(0, host.length - EIGENES_TAILNET.length);
    if (host.slice(-EIGENES_TAILNET.length) !== EIGENES_TAILNET ||
        !/^[a-z0-9-]+$/.test(rest)) return "";
    return roh;
  }

  function rueckwegBestimmen(search, speicher) {
    var neu = "";
    try { neu = new URLSearchParams(search || "").get("zurueck") || ""; }
    catch (e) { neu = ""; }
    neu = rueckwegPruefen(neu);
    if (neu) {
      try { speicher.setItem(SCHLUESSEL, neu); } catch (e) { /* egal */ }
      return neu;
    }
    try { return rueckwegPruefen(speicher.getItem(SCHLUESSEL) || ""); }
    catch (e) { return ""; }
  }

  function einsetzen() {
    var a = document.getElementById("schalter-sales");
    if (!a) return;
    var speicher;
    try { speicher = window.localStorage; } catch (e) { speicher = null; }
    var ziel = rueckwegBestimmen(window.location.search,
      speicher || { getItem: function () { return null; },
                    setItem: function () {} });
    if (ziel) { a.href = ziel; a.hidden = false; }
  }

  var api = { rueckwegPruefen: rueckwegPruefen,
              rueckwegBestimmen: rueckwegBestimmen, einsetzen: einsetzen };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else {
    wurzel.MarketingSchalter = api;
    if (document.readyState === "loading")
      document.addEventListener("DOMContentLoaded", einsetzen);
    else einsetzen();
  }
})(typeof window !== "undefined" ? window : globalThis);
