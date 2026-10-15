#!/usr/bin/env python3
"""brainboard web server (stdlib only).

    python3 brainboard/server.py                      # http://127.0.0.1:8765, no password
    BRAINBOARD_PASSWORD=… python3 brainboard/server.py --host 0.0.0.0 --port 2001

Environment
  BRAINBOARD_HOME      data directory (default ./data); one file per project in projects/<slug>.json
  BRAINBOARD_DATA      legacy single-board file; its directory becomes the home and the board is
                       migrated into the first project
  BRAINBOARD_PASSWORD  enables HTTP Basic auth (any username). Required off localhost.
  BRAINBOARD_TLS_PORT  also serve HTTPS on this port (browsers only allow the microphone on HTTPS or
                       localhost); needs BRAINBOARD_TLS_CERT and BRAINBOARD_TLS_KEY (PEM files)
  BRAINBOARD_ASR_URL   speech-to-text endpoint for voice input, OpenAI transcription API shape, e.g.
                       http://127.0.0.1:8010/v1/audio/transcriptions (asr/server.py, Qwen3-ASR) or
                       https://api.openai.com/v1/audio/transcriptions. Unset = voice input off.
  BRAINBOARD_ASR_KEY   bearer token for the ASR endpoint (optional)
  BRAINBOARD_ASR_MODEL model name sent to the endpoint (default Qwen/Qwen3-ASR-1.7B)
  BRAINBOARD_ASR_LANGUAGE  force a language, e.g. English (default: auto-detect)

API
  GET  /api/projects                 project summaries + index version
  POST /api/projects                 {"op":"create","fields":{…}} | {"op":"delete","slug":…}
  GET  /api/version                  index version (changes when any project changes)
  GET  /api/p/<slug>/state|version   one project
  POST /api/p/<slug>/op              one operation (see core.apply_op)
  GET  /api/config                   {"voice": bool, "asr_model": …}
  POST /api/transcribe               raw audio body (Content-Type: audio/…) → {"text": …}
"""
import argparse
import base64
import hmac
import json
import os
import re
import ssl
import sys
import threading
import urllib.error
import urllib.request
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core  # noqa: E402

INDEX = os.path.join(core.HERE, "index.html")
PASSWORD = os.environ.get("BRAINBOARD_PASSWORD", "")
MAX_BODY = 1 << 20
MAX_AUDIO = 25 << 20
ASR_URL = os.environ.get("BRAINBOARD_ASR_URL", "")
ASR_KEY = os.environ.get("BRAINBOARD_ASR_KEY", "")
ASR_MODEL = os.environ.get("BRAINBOARD_ASR_MODEL", "Qwen/Qwen3-ASR-1.7B")
ASR_LANGUAGE = os.environ.get("BRAINBOARD_ASR_LANGUAGE", "")
AUDIO_EXT = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "m4a", "audio/mpeg": "mp3",
             "audio/wav": "wav", "audio/x-wav": "wav", "audio/aac": "aac", "audio/flac": "flac"}
PROJECT_RE = re.compile(r"^/api/p/([a-z0-9][a-z0-9-]{0,47})/(state|version|op)$")


def transcribe(audio, ctype):
    """Forward audio to the configured OpenAI-compatible transcription endpoint; returns the text."""
    base = ctype.split(";")[0].strip().lower()
    ext = AUDIO_EXT.get(base, "webm")
    fields = {"model": ASR_MODEL, "response_format": "json"}
    if ASR_LANGUAGE:
        fields["language"] = ASR_LANGUAGE
    b = uuid.uuid4().hex
    parts = [f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields.items()]
    parts.append(f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="voice.{ext}"\r\n'
                 f'Content-Type: {base or "application/octet-stream"}\r\n\r\n'.encode() + audio + b"\r\n")
    body = b"".join(parts) + f"--{b}--\r\n".encode()
    req = urllib.request.Request(ASR_URL, data=body, headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    if ASR_KEY:
        req.add_header("Authorization", "Bearer " + ASR_KEY)
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.load(r).get("text", "").strip()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"speech service said {e.code}: {e.read().decode(errors='replace')[:200]}")
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError(f"speech service unreachable: {getattr(e, 'reason', e)}")


class Handler(BaseHTTPRequestHandler):
    server_version = "brainboard"

    def _send(self, code, body, ctype="application/json", extra=()):
        raw = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in extra:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _authed(self):
        if not PASSWORD:
            return True
        h = self.headers.get("Authorization", "")
        if h.startswith("Basic "):
            try:
                _, _, pw = base64.b64decode(h[6:]).decode().partition(":")
            except (ValueError, UnicodeDecodeError):
                pw = ""
            if hmac.compare_digest(pw.encode(), PASSWORD.encode()):
                return True
        self._send(401, {"error": "auth required"}, extra=[("WWW-Authenticate", 'Basic realm="brainboard"')])
        return False

    def _state(self, data):
        data["stats"] = core.stats(data)
        data["version"] = core.version(data["meta"]["slug"])
        return data

    def _body(self):
        n = int(self.headers.get("Content-Length", 0))
        if n > MAX_BODY:
            raise OverflowError
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        if not self._authed():
            return
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            with open(INDEX, "rb") as fh:
                return self._send(200, fh.read(), "text/html")
        if path == "/api/projects":
            return self._send(200, {"projects": core.list_projects(), "version": core.index_version()})
        if path == "/api/version":
            return self._send(200, {"version": core.index_version()})
        if path == "/api/config":
            return self._send(200, {"voice": bool(ASR_URL), "asr_model": ASR_MODEL if ASR_URL else ""})
        m = PROJECT_RE.match(path)
        if m and m.group(2) != "op":
            slug = m.group(1)
            if not core.exists(slug):
                return self._send(404, {"error": f"no such project: {slug}"})
            if m.group(2) == "version":
                return self._send(200, {"version": core.version(slug)})
            return self._send(200, self._state(core.load(slug)))
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._authed():
            return
        path = self.path.split("?")[0]
        if path == "/api/transcribe":
            return self._transcribe()
        m = PROJECT_RE.match(path)
        if path != "/api/projects" and not (m and m.group(2) == "op"):
            return self._send(404, {"error": "not found"})
        try:
            body = self._body()
            who = str(body.get("who", "me"))[:32]
            if m:
                return self._send(200, self._state(core.apply_op(m.group(1), body, who=who)))
            if body.get("op") == "create":
                slug = core.create_project(body.get("fields") or {}, who=who)
                return self._send(200, {"slug": slug, "projects": core.list_projects(), "version": core.index_version()})
            if body.get("op") == "delete":
                core.delete_project(str(body.get("slug", "")))
                return self._send(200, {"projects": core.list_projects(), "version": core.index_version()})
            return self._send(400, {"error": "op must be create or delete"})
        except OverflowError:
            return self._send(413, {"error": "body too large"})
        except KeyError as e:
            return self._send(404, {"error": str(e).strip("'\"")})
        except (ValueError, TypeError) as e:
            return self._send(400, {"error": str(e)})

    def _transcribe(self):
        if not ASR_URL:
            return self._send(404, {"error": "voice input is not configured (BRAINBOARD_ASR_URL)"})
        n = int(self.headers.get("Content-Length", 0))
        if n > MAX_AUDIO:
            return self._send(413, {"error": "recording too large (25 MB max)"})
        if n == 0:
            return self._send(400, {"error": "empty recording"})
        audio = self.rfile.read(n)
        try:
            text = transcribe(audio, self.headers.get("Content-Type", ""))
        except RuntimeError as e:
            return self._send(502, {"error": str(e)})
        self._send(200, {"text": text})

    def log_message(self, fmt, *args):
        if "/version " not in (args[0] if args else ""):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=os.environ.get("BRAINBOARD_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("BRAINBOARD_PORT", 8765)))
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args()
    if a.host not in ("127.0.0.1", "localhost", "::1") and not PASSWORD:
        sys.exit("Refusing to listen on a public interface without BRAINBOARD_PASSWORD set.")
    core.ensure_home()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    url = f"http://{'127.0.0.1' if a.host == '0.0.0.0' else a.host}:{a.port}/"
    print(f"brainboard: {url}  home={core.HOME}  auth={'on' if PASSWORD else 'off'}  "
          f"voice={'on → ' + ASR_URL if ASR_URL else 'off'}", flush=True)
    tls_port = int(os.environ.get("BRAINBOARD_TLS_PORT", 0) or 0)
    if tls_port:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(os.path.expanduser(os.environ["BRAINBOARD_TLS_CERT"]),
                            os.path.expanduser(os.environ["BRAINBOARD_TLS_KEY"]))
        tls = ThreadingHTTPServer((a.host, tls_port), Handler)
        tls.socket = ctx.wrap_socket(tls.socket, server_side=True)
        threading.Thread(target=tls.serve_forever, daemon=True).start()
        print(f"brainboard: https on port {tls_port}", flush=True)
    if not a.no_open and a.host in ("127.0.0.1", "localhost"):
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
