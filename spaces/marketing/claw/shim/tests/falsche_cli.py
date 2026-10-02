"""Falsche Claude-CLI fuer die Shim-Tests (kein echtes Modell)."""
import os
import sys
import time

argv = sys.argv[1:]
sys.stdin.read()
modus = os.environ.get("FALSCH_MODUS")
START = '{"type":"stream_event","event":{"type":"message_start"}}'


def delta(text):
    print('{"type":"stream_event","event":{"type":"content_block_delta","index":0,'
          '"delta":{"type":"text_delta","text":"%s"}}}' % text, flush=True)


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
