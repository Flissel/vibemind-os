"""Falsche Claude-CLI fuer die Shim-Tests (kein echtes Modell)."""
import json
import os
import sys
import time

argv = sys.argv[1:]
eingabe = sys.stdin.read()
modus = os.environ.get("FALSCH_MODUS")
START = '{"type":"stream_event","event":{"type":"message_start"}}'

protokoll = os.environ.get("FALSCH_PROTOKOLL")
if protokoll:
    # Haelt fest, was die CLI empfangen hat -- samt Inhalt des --add-dir-Ordners
    # ZUM ZEITPUNKT des Aufrufs (danach wird er vom Shim geloescht).
    ordner = argv[argv.index("--add-dir") + 1] if "--add-dir" in argv else None
    dateien = {}
    if ordner and os.path.isdir(ordner):
        for name in sorted(os.listdir(ordner)):
            with open(os.path.join(ordner, name), "rb") as f:
                dateien[name] = f.read().hex()
    with open(protokoll, "w", encoding="utf-8") as f:
        system = ""
        if "--system-prompt-file" in argv:
            with open(argv[argv.index("--system-prompt-file") + 1], encoding="utf-8") as sf:
                system = sf.read()
        json.dump({"argv": argv, "stdin": eingabe, "ordner": ordner, "dateien": dateien,
                   "system": system}, f)


def delta(text):
    print('{"type":"stream_event","event":{"type":"content_block_delta","index":0,'
          '"delta":{"type":"text_delta","text":"%s"}}}' % text, flush=True)


def denk_delta(text):
    print('{"type":"stream_event","event":{"type":"content_block_delta","index":0,'
          '"delta":{"type":"thinking_delta","thinking":"%s"}}}' % text, flush=True)


zaehler = os.environ.get("FALSCH_ZAEHLER")
if zaehler:
    with open(zaehler, "a", encoding="utf-8") as f:
        f.write("lauf" + chr(10))

if modus == "denken_wert_ungueltig" and "--thinking-display" in argv:
    sys.stderr.write("error: option '--thinking-display <mode>' argument 'summarized' is invalid\n")
    sys.exit(1)
if modus == "fehler_nach_stueck" and "stream-json" in argv:
    print(START, flush=True)
    delta("halb")
    sys.stderr.write("abgestuerzt\n")
    sys.exit(1)
if modus == "denken_abgelehnt" and "--thinking-display" in argv:
    sys.stderr.write("error: unknown option '--thinking-display'\n")
    sys.exit(1)
if "stream-json" in argv and "--thinking-display" in argv and modus not in ("denken_abgelehnt", "denken_wert_ungueltig"):
    print(START, flush=True)
    denk_delta("Let me think")
    denk_delta(" about it.")
    delta("Antwort")
    print('{"type":"result","subtype":"success","is_error":false,"result":"Antwort"}', flush=True)
    sys.exit(0)


if modus == "exit":
    sys.stderr.write("kaputt\n")
    sys.exit(3)
if "stream-json" in argv and modus == "zweiturns":
    print(START, flush=True)
    delta("Ich suche")
    print(START, flush=True)
    delta("Fertig")
    print('{"type":"result","subtype":"success","is_error":false,"result":"Fertig"}', flush=True)
elif "stream-json" in argv:
    print(START, flush=True)
    for text in ("eins", " zwei", " drei"):
        time.sleep(0.05)
        delta(text)
    time.sleep(0.3)
    print('{"type":"result","subtype":"success","is_error":false,"result":"eins zwei drei"}', flush=True)
else:
    print('{"type":"result","is_error":false,"result":"eins zwei drei","usage":{"input_tokens":1,"output_tokens":3}}')
