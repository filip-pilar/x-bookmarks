"""Bounded media admission, low-resolution downloads and resumable speech segments."""
import contextlib
import json
import math
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit
from store import HOME, db, setting
from sources import DiskSpaceError, reserve_space
from controls import preferences

CACHE = HOME / 'cache/media-v1'
LOCK = threading.RLock()
COMMANDS = set()
STOPPING = threading.Event()
SCRATCH = 128 * 1024**2
SPEECH_SECONDS = 300


def limit():
    return int(preferences()['cache_gib'] * 1024**3)


def size(path):
    total = 0
    for child in path.rglob('*'):
        try:
            if child.is_file():
                total += child.stat().st_size
        except FileNotFoundError:
            pass
    return total


def reserve(asset, envelope, shrink=False):
    """Account for future scratch as well as bytes already on disk, across all workers."""
    with LOCK, db() as c:
        c.execute('BEGIN IMMEDIATE')
        held = {r[0]: r[1] for r in c.execute('SELECT asset,bytes FROM media_reservations')}
        actual = {p.name: size(p) for p in CACHE.iterdir() if p.is_dir()} if CACHE.exists() else {}
        requested = max(envelope, actual.get(asset, 0) + SCRATCH, 0 if shrink else held.get(asset, 0))
        total = sum(max(actual.get(a, 0), held.get(a, 0)) for a in actual.keys() | held.keys() if a != asset)
        if requested > limit():
            raise DiskSpaceError(f'temporary_budget_required:{math.ceil(requested / 1024**3)}')
        if total + requested > limit():
            raise DiskSpaceError('temporary_storage_full')
        future = sum(max(0, held.get(a, 0)-actual.get(a, 0)) for a in held if a != asset)
        reserve_space(future + max(0, requested-actual.get(asset, 0)))
        c.execute('INSERT OR REPLACE INTO media_reservations VALUES (?,?)', (asset, requested))


def interrupted(run):
    with db() as c:
        row = c.execute('SELECT state FROM runs WHERE id=?', (run,)).fetchone()
    return setting('paused', True) or not row or row[0] != 'running'


def run_command(args, run, directory, envelope, timeout=1800):
    """Kill and reap subprocess trees on pause, deadline or storage pressure."""
    import os
    import signal
    import tempfile
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        with LOCK:
            if STOPPING.is_set():
                raise InterruptedError('processing_paused')
            process = subprocess.Popen([str(a) for a in args], stdout=output, stderr=errors, start_new_session=True)
            COMMANDS.add(process)
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                if interrupted(run):
                    raise InterruptedError('processing_paused')
                if time.monotonic() > deadline:
                    raise RuntimeError('media_step_timeout')
                if os.fstat(output.fileno()).st_size + os.fstat(errors.fileno()).st_size > 8_000_000:
                    raise RuntimeError('media_output_limit')
                used = size(directory)
                if used > envelope - 8 * 1024**2:
                    envelope = used + SCRATCH
                    reserve(directory.name, envelope)
                reserve_space(8 * 1024**2)
                time.sleep(.25)
            if process.returncode:
                # Downloader output can contain expiring URLs. Keep it out of UI/diagnostics.
                raise RuntimeError(f'{Path(str(args[0])).name}_exit_{process.returncode}')
            reserve(directory.name, size(directory) + SCRATCH)
            output.seek(0)
            data = output.read(4_000_001)
            if len(data) > 4_000_000:
                raise RuntimeError('media_metadata_too_large')
            return data.decode()
        finally:
            if process.poll() is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            with LOCK:
                COMMANDS.discard(process)


def stop_commands():
    """Reap long-running downloads/recognition before the service releases its lock."""
    import os, signal
    with LOCK:
        STOPPING.set()
        active = list(COMMANDS)
    for process in active:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
    for process in active:
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def variant(media):
    candidates = [v for v in media.get('variants', []) if v.get('content_type') == 'video/mp4']
    height = preferences()['video_height']
    def resolution(v):
        match = re.search(r'/(\d+)x(\d+)/', urlsplit(v['url']).path)
        return min(map(int, match.groups())) if match else 0
    known = [v for v in candidates if resolution(v)]
    if known:
        below = [v for v in known if resolution(v) <= height]
        selected = max(below, key=lambda v: (resolution(v), -v.get('bit_rate', 0))) if below else min(known, key=resolution)
        return selected
    if candidates:
        return min(candidates, key=lambda v: v.get('bit_rate', float('inf')))
    streams = [v for v in media.get('variants', []) if 'mpegurl' in v.get('content_type', '').lower()]
    if streams:
        return streams[0]
    raise ValueError('media_variant_unavailable')


def download_video(media, asset, run, directory):
    from content import public_url
    selected = variant(media)
    url = selected['url']
    public_url(url)
    if urlsplit(url).hostname != 'video.twimg.com':
        raise ValueError('unsupported_video_host')
    binary = shutil.which('yt-dlp')
    if not binary:
        raise RuntimeError('yt_dlp_missing')
    duration = max(0, float(media.get('duration_ms', 0))/1000)
    estimate = math.ceil(duration * selected.get('bit_rate', 1_500_000) / 8)
    # A single source may use most of the budget, but leaves bounded speech/frame scratch.
    envelope = min(limit(), max(512 * 1024**2, estimate * 2 + SCRATCH, size(directory) + SCRATCH))
    reserve(asset, envelope)
    path = directory / 'original.mp4'
    args = [binary, '--ignore-config', '--no-cache-dir', '--no-playlist', '--no-progress',
            '--no-warnings', '--retries', '3', '--fragment-retries', '3', '--abort-on-unavailable-fragments',
            '--concurrent-fragments', '1', '--continue', '--no-overwrites',
            '-S', 'res:' + str(preferences()['video_height']), '--merge-output-format', 'mp4',
            '-o', str(path), '--', url]
    # Keep partial files on pause or pressure. Retrying continues the same download.
    run_command(args, run, directory, envelope, timeout=max(1800, duration * 2))
    if not path.is_file() or not path.stat().st_size:
        raise RuntimeError('video_download_incomplete')
    reserve(asset, size(directory) + SCRATCH, shrink=True)
    return path


def segments(duration, seconds):
    start = 0
    while start < duration:
        end = min(start + seconds, duration)
        # Codec padding must not create a near-empty recognition/embedding request.
        if duration - end < 1:
            end = duration
        yield start, end
        start = end


def speech_segments(duration):
    return segments(duration, SPEECH_SECONDS)


def transcribe_segment(path, payload, run, directory):
    import sys
    from store import ROOT
    start, end = payload['start'], payload['end']
    left, right = max(0, start-1), min(payload['duration'], end+1)
    wav = directory / f'speech-{start}.wav'
    scratch = directory / f'speech-{start}'
    reserve(payload['asset'], size(directory) + SCRATCH)
    try:
        run_command(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-ss', str(left), '-i', path,
                     '-t', str(right-left), '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', wav],
                    run, directory, size(directory) + SCRATCH, timeout=180)
        # Each recognition call is bounded to five minutes, preserving completed segments on restart.
        result = json.loads(run_command([sys.executable, ROOT / 'vendor/transcribe/scripts/transcribe.py',
                                        wav, '--destination', scratch], run, directory,
                                       size(directory) + SCRATCH, timeout=1800))
        recognition = json.loads((Path(result['directory']) / 'recognition.json').read_text())
        words = []
        for word in recognition.get('words', []):
            absolute = {**word, 'startTime': word['startTime'] + left, 'endTime': word['endTime'] + left}
            if start <= (absolute['startTime'] + absolute['endTime']) / 2 < end:
                words.append(absolute)
        return {'text': ' '.join(w['word'] for w in words), 'words': words}, result
    finally:
        wav.unlink(missing_ok=True)
        shutil.rmtree(scratch, ignore_errors=True)
