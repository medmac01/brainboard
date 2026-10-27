#!/usr/bin/env python3
"""Local speech-to-text service for brainboard's voice input.

Serves Qwen3-ASR (https://huggingface.co/Qwen/Qwen3-ASR-1.7B) behind the OpenAI transcription API shape,
so brainboard can switch to any other OpenAI-compatible ASR endpoint later by changing a URL.

    pip install torch --index-url https://download.pytorch.org/whl/cu126   # cu126 still supports Pascal
    pip install qwen-asr fastapi uvicorn python-multipart                    # ffmpeg must be on PATH
    python asr/server.py --host 127.0.0.1 --port 8010

    POST /v1/audio/transcriptions   multipart: file=<audio>, [language=English], [model=…]  → {"text": "…", "language": "…"}
    GET  /health

Environment: ASR_MODEL (default Qwen/Qwen3-ASR-1.7B), ASR_DTYPE (float32|float16|bfloat16; default float32,
since Pascal GPUs run fp16 very slowly), ASR_DEVICE (default cuda:0), ASR_MAX_NEW_TOKENS (default 1024).
"""
import argparse
import os
import subprocess
import tempfile
import threading
import time

import torch
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from qwen_asr import Qwen3ASRModel

MODEL_ID = os.environ.get("ASR_MODEL", "Qwen/Qwen3-ASR-1.7B")
DTYPE = getattr(torch, os.environ.get("ASR_DTYPE", "float32"))
DEVICE = os.environ.get("ASR_DEVICE", "cuda:0" if torch.cuda.is_available() else "cpu")
MAX_BYTES = 25 << 20

app = FastAPI(title="brainboard-asr")
model = None
lock = threading.Lock()  # one GPU, one transcription at a time


def to_wav(raw: bytes) -> str:
    """Decode whatever the browser recorded (webm/opus, ogg, m4a, wav…) to 16 kHz mono wav via ffmpeg."""
    src = tempfile.NamedTemporaryFile(delete=False, suffix=".audio")
    src.write(raw)
    src.close()
    dst = src.name + ".wav"
    try:
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", src.name, "-ac", "1", "-ar", "16000", dst],
                       check=True, capture_output=True, timeout=120)
    except subprocess.CalledProcessError as e:
        raise HTTPException(400, f"could not decode audio: {e.stderr.decode()[-300:]}")
    finally:
        os.unlink(src.name)
    return dst


@app.get("/health")
def health():
    return {"ok": model is not None, "model": MODEL_ID, "device": DEVICE, "dtype": str(DTYPE).split(".")[-1]}


@app.post("/v1/audio/transcriptions")
async def transcriptions(file: UploadFile = File(...), language: str = Form(None), model_name: str = Form(None, alias="model")):
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "empty audio")
    if len(raw) > MAX_BYTES:
        raise HTTPException(413, "audio too large (25 MB max)")
    wav = to_wav(raw)
    try:
        t0 = time.time()
        with lock:
            res = model.transcribe(audio=wav, language=language or None)[0]
        print(f"transcribed {len(raw) // 1024} KB in {time.time() - t0:.1f}s: {res.text[:60]!r}", flush=True)
        return {"text": res.text.strip(), "language": res.language}
    finally:
        os.unlink(wav)


def main():
    global model
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8010)
    a = ap.parse_args()
    t0 = time.time()
    model = Qwen3ASRModel.from_pretrained(MODEL_ID, dtype=DTYPE, device_map=DEVICE, max_inference_batch_size=1,
                                          max_new_tokens=int(os.environ.get("ASR_MAX_NEW_TOKENS", 1024)))
    print(f"loaded {MODEL_ID} on {DEVICE} ({DTYPE}) in {time.time() - t0:.0f}s", flush=True)
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
