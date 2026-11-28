"use strict";
// <vm-chat>: ein Chatfenster für die ganze App (Vorschlag „chatfenster-einheit“).
// Ruhiger Verlauf aus Paaren: der Satz in Monospace mit „›“, darunter die Antwort mit einer Marke
// (✓ fertig · ? Rückfrage · ● angenommen · ✕ nicht geklappt). Statuszeile über dem Feld,
// mehrzeiliges Feld (Enter sendet, Umschalt+Enter neue Zeile, ↑ holt den letzten Satz).
// Ohne Abhängigkeit, ohne Shadow DOM (die Seiten erlauben kein Inline-CSS), Texte nur per textContent.
//
// Anschluss:  chat.connect({ send(text, kontext) -> Promise<{text, marke, dauer_s?}>, kontext })
// Marken:     "fertig" | "rueckfrage" | "angenommen" | "fehler"

(function () {
  const MARKEN = {
    fertig: ["✓", "fertig"], rueckfrage: ["?", "Rückfrage"], angenommen: ["●", "angenommen"],
    fehler: ["✕", "nicht geklappt"],
  };

  function el(art, text, klasse) {
    const e = document.createElement(art);
    if (text !== undefined && text !== null) e.textContent = text;
    if (klasse) e.className = klasse;
    return e;
  }

  class VmChat extends HTMLElement {
    connectedCallback() {
      if (this._fertig) return;
      this._fertig = true;
      this._sender = null;
      this._kontext = {};
      this._letzte = [];
      this.classList.add("vm-chat");

      this._kopf = el("div", this.getAttribute("titel") || "Chat", "vm-chat-kopf");
      this._verlauf = el("ol", null, "vm-chat-verlauf");
      this._verlauf.setAttribute("role", "log");
      this._verlauf.setAttribute("aria-live", "polite");
      this._leer = el("p", this.getAttribute("leer") || "", "vm-chat-leer");
      this._status = el("p", "", "vm-chat-status");
      this._status.setAttribute("role", "status");

      const zeile = el("form", null, "vm-chat-eingabe");
      this._feld = el("textarea");
      this._feld.rows = 1;
      this._feld.placeholder = this.getAttribute("platzhalter") || "Schreib einen Satz …";
      this._feld.setAttribute("aria-label", this._feld.placeholder);
      this._knopf = el("button", "▶");
      this._knopf.type = "submit";
      this._knopf.title = "Senden (Enter)";
      this._knopf.setAttribute("aria-label", "Senden");
      zeile.appendChild(this._feld);
      zeile.appendChild(this._knopf);

      zeile.addEventListener("submit", (e) => { e.preventDefault(); this.senden(); });
      this._feld.addEventListener("input", () => this._wachsen());
      this._feld.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); this.senden(); }
        if (e.key === "ArrowUp" && !this._feld.value && this._letzte.length) {
          e.preventDefault();
          this._feld.value = this._letzte[this._letzte.length - 1];
          this._wachsen();
        }
      });

      for (const teil of [this._kopf, this._leer, this._verlauf, this._status, zeile]) this.appendChild(teil);
      this._zeigeLeer();
    }

    connect({ send, kontext } = {}) {
      this._sender = typeof send === "function" ? send : null;
      if (kontext) this._kontext = kontext;
    }

    set kontext(wert) { this._kontext = wert || {}; }
    get kontext() { return this._kontext; }

    set titel(text) { if (this._kopf) this._kopf.textContent = text; }

    set gesperrt(wert) {
      if (!this._feld) return;
      this._feld.disabled = Boolean(wert);
      this._knopf.disabled = Boolean(wert);
    }

    // Verlauf ersetzen, z. B. beim Wechsel des Ortes: [{satz, text, marke, dauer_s}]
    verlauf(paare) {
      while (this._verlauf.firstChild) this._verlauf.removeChild(this._verlauf.firstChild);
      for (const p of paare || []) this._paar(p.satz).antworten(p);
      this._zeigeLeer();
    }

    status(text) { this._status.textContent = text || ""; }

    async senden() {
      const text = this._feld.value.trim();
      if (!text || this._feld.disabled) return;
      this._feld.value = "";
      this._wachsen();
      this._letzte.push(text);
      const paar = this._paar(text);
      this._zeigeLeer();
      if (!this._sender) {
        paar.antworten({ text: "Hier ist noch nichts angeschlossen.", marke: "fehler" });
        return;
      }
      const beginn = Date.now();
      this.gesperrt = true;
      this.status("● arbeitet …");
      let antwort;
      try {
        antwort = await this._sender(text, this._kontext);
      } catch (fehler) {
        antwort = { text: "Die Antwort kam nicht an.", marke: "fehler" };
      }
      const dauer = (Date.now() - beginn) / 1000;
      paar.antworten({ dauer_s: dauer, ...(antwort || {}) });
      this.status("");
      this.gesperrt = false;
      this._feld.focus();
      this.dispatchEvent(new CustomEvent("vm-chat-paar", { detail: { satz: text, ...(antwort || {}) } }));
    }

    _paar(satz) {
      const li = el("li", null, "vm-chat-paar");
      li.appendChild(el("p", `› ${satz}`, "vm-chat-satz"));
      const antwort = el("div", null, "vm-chat-antwort");
      li.appendChild(antwort);
      this._verlauf.appendChild(li);
      this._verlauf.scrollTop = this._verlauf.scrollHeight;
      return {
        antworten: ({ text, marke, dauer_s } = {}) => {
          if (text) antwort.appendChild(el("p", text, "vm-chat-text"));
          const [zeichen, name] = MARKEN[marke] || MARKEN.angenommen;
          const zeit = dauer_s !== undefined && dauer_s !== null ? ` · ${Math.max(0, Math.round(dauer_s))} s` : "";
          const m = el("span", `${zeichen} ${name}${zeit}`, "vm-chat-marke");
          m.dataset.marke = marke || "angenommen";
          antwort.appendChild(m);
          this._verlauf.scrollTop = this._verlauf.scrollHeight;
        },
      };
    }

    _zeigeLeer() { this._leer.hidden = !this._leer.textContent || this._verlauf.children.length > 0; }

    _wachsen() {
      this._feld.style.height = "auto";
      const zeile = parseFloat(getComputedStyle(this._feld).lineHeight) || 20;
      const hoechstens = zeile * 6 + 16;
      this._feld.style.height = `${Math.min(this._feld.scrollHeight, hoechstens)}px`;
      this._feld.style.overflowY = this._feld.scrollHeight > hoechstens ? "auto" : "hidden";
    }
  }

  if (!customElements.get("vm-chat")) customElements.define("vm-chat", VmChat);
})();
