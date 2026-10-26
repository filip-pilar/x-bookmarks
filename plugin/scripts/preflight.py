#!/usr/bin/env python3
"""Verify local processing with generated media, without X or library jobs."""

import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
from store import HOME, VERSION, atomic
from diagnostics import safe_error


def main():
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    import cv2
    import soundfile as sf
    from content import command, ocr, probe, audio_file, transcribe
    from embedding import encode
    from sources import checksum

    report = {"version": VERSION, "ready": False, "at": time.time(), "checks": {}}
    target = HOME / "preflight.json"
    folder = HOME / "cache/preflight"
    folder.mkdir(parents=True, exist_ok=True)
    atomic(target, report)

    def check(name, action):
        print("Preflight: " + name, flush=True)
        try:
            action()
        except Exception as error:
            report["checks"][name] = {"ok": False, "error": safe_error(error)}
            atomic(target, report)
            raise RuntimeError(f"Local {name} check failed: {safe_error(error)}. See {target}") from error
        report["checks"][name] = {"ok": True}
        atomic(target, report)

    image = Image.new("RGB", (768, 384), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 48)
    draw.text((32, 140), "Bookmark Library ready", font=font, fill="black")
    path = folder / "image.png"
    image.save(path)

    def vision():
        text = ocr(path).lower()
        if "bookmark" not in text or "library" not in text:
            raise RuntimeError("Vision did not recognize the generated image text.")

    check("ocr", vision)
    check("text_embedding", lambda: encode(text="title: none | text: Bookmark library setup"))
    check("image_embedding", lambda: encode(text="<|image|>", images=image))
    command(["/usr/bin/say", "-o", folder / "speech.aiff", "Bookmark library is ready to search saved posts."])
    video = folder / "video.mp4"
    command(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-i", path,
             "-i", folder / "speech.aiff", "-t", "3", "-r", "2", "-c:v", "libx264",
             "-pix_fmt", "yuv420p", "-c:a", "aac", video])
    check("media_probe", lambda: probe(video))
    wav = audio_file(video, checksum(video), float(probe(video)["format"]["duration"]))
    audio, rate = sf.read(wav, dtype="float32")
    check("audio_embedding", lambda: encode(text="<|audio|>", audio=audio, sampling_rate=rate))
    cap = cv2.VideoCapture(str(video))
    try:
        ok, bgr = cap.read()
        if not ok:
            raise RuntimeError("Generated video could not be decoded.")
        frames = np.stack([cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)])
    finally:
        cap.release()
    check("video_embedding", lambda: encode(text="<|video|>", videos=frames, do_sample_frames=False))
    check("joint_embedding", lambda: encode(text="Saved post <|video|> <|audio|>", videos=frames,
          do_sample_frames=False, audio=audio, sampling_rate=rate))

    def speech():
        recognition, _ = transcribe(wav, folder, rerun=True)
        if not recognition.get("text", "").strip() or not recognition.get("words"):
            raise RuntimeError("Parakeet returned no speech or word timings for generated speech.")
        if recognition.get("duration", 0) <= 0:
            raise RuntimeError("Parakeet returned an invalid duration.")

    check("parakeet_transcription", speech)
    report.update(ready=True, at=time.time())
    atomic(target, report)
    print(json.dumps(report))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        target = HOME / "preflight.json"
        report = json.loads(target.read_text()) if target.exists() else {"version": VERSION, "checks": {}}
        report.update(ready=False, error=safe_error(error), at=time.time())
        atomic(target, report)
        print(safe_error(error), file=sys.stderr)
        raise SystemExit(1)
