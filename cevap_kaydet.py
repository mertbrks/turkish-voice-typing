"""Claude Code 'Stop' hook'u: Claude'un son cevabını son_cevap.txt'ye yazar; Sesli Komut bunu görüp seslendirir."""
import json
import os
import sys

DOSYA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "son_cevap.txt")


def transkriptten(yol):
    # eski sürümlerde last_assistant_message yok: transkriptin son asistan metnini al
    metin = ""
    with open(yol, encoding="utf-8") as f:
        for satir in f:
            try:
                k = json.loads(satir)
            except ValueError:
                continue
            if k.get("type") == "assistant":
                parca = [c.get("text", "") for c in k.get("message", {}).get("content", []) if c.get("type") == "text"]
                if any(parca):
                    metin = "\n".join(parca)
    return metin


try:
    girdi = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    metin = girdi.get("last_assistant_message") or transkriptten(girdi["transcript_path"])
    if metin.strip():
        with open(DOSYA, "w", encoding="utf-8") as f:
            f.write(metin)
except Exception:
    pass  # hook hiçbir zaman Claude'u durdurmasın
