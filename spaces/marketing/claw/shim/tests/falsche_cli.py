"""Falsche Claude-CLI fuer die Shim-Tests (kein echtes Modell)."""
import sys
import time

argv = sys.argv[1:]
sys.stdin.read()
if "FALSCH_EXIT" in " ".join(argv) or __import__("os").environ.get("FALSCH_MODUS") == "exit":
    sys.stderr.write("kaputt\n")
    sys.exit(3)
if "stream-json" in argv:
    print('{"type":"stream_event","event":{"type":"message_start"}}', flush=True)
    for text in ("eins", " zwei", " drei"):
        time.sleep(0.05)
        print('{"type":"stream_event","event":{"type":"content_block_delta","index":0,'
              '"delta":{"type":"text_delta","text":"%s"}}}' % text, flush=True)
    time.sleep(0.3)
    print('{"type":"result","subtype":"success","is_error":false,"result":"eins zwei drei"}', flush=True)
else:
    print('{"type":"result","is_error":false,"result":"eins zwei drei","usage":{"input_tokens":1,"output_tokens":3}}')
