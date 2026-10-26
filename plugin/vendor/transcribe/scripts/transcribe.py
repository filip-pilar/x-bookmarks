#!/usr/bin/env python3
"""Local audio transcription orchestration; Python standard library only."""
import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

SKILL = Path(__file__).resolve().parents[1]
CACHE = Path(os.environ.get('TRANSCRIBE_CACHE', '~/Library/Caches/codex-transcribe')).expanduser()
MODEL = 'FluidInference/parakeet-unified-en-0.6b-coreml'
FORMAT_VERSION = 2


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def atomic(path, value):
    path = Path(path)
    payload = value if isinstance(value, str) else json.dumps(value, indent=2, ensure_ascii=False) + '\n'
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(payload)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_json(path):
    return json.loads(Path(path).read_text())


def say(message):
    print(message, file=sys.stderr, flush=True)


def command(args, log=None):
    if log:
        with Path(log).open('a') as f:
            result = subprocess.run([str(a) for a in args], stdout=f, stderr=f)
        if result.returncode:
            raise RuntimeError(f'{Path(str(args[0])).name} failed ({result.returncode}); see {log}')
        return ''
    result = subprocess.run([str(a) for a in args], text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError(f'{Path(str(args[0])).name} failed ({result.returncode}): {result.stderr[-3000:]}')
    if result.stderr.strip():
        say(result.stderr.rstrip())
    return result.stdout


def check_host():
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise RuntimeError('Requires local shell access to an Apple Silicon Mac runtime (macOS 14+, CoreML). This session cannot run it; no cloud transcription was attempted.')
    if int(platform.mac_ver()[0].split('.')[0]) < 14:
        raise RuntimeError('macOS 14 or newer is required.')
    for tool in ('ffmpeg', 'ffprobe'):
        if not shutil.which(tool):
            raise RuntimeError(f'Missing {tool}; install ffmpeg (brew install ffmpeg).')


def runtime_key():
    files = sorted((SKILL / 'runtime').rglob('*.swift'))
    return identity([(str(p.relative_to(SKILL)), digest(p)) for p in files])[:16]


def runtime():
    packaged = SKILL.parents[1] / "bin" / "TranscribeAudio"
    if packaged.is_file():
        return packaged
    key = runtime_key()
    build = CACHE / 'builds' / key
    binary = build / '.build' / 'release' / 'TranscribeAudio'
    if binary.is_file():
        return binary
    if not shutil.which('swift'):
        raise RuntimeError('Swift 6.2+ is needed for the first build. Install/select a compatible Xcode toolchain.')
    build.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SKILL / 'runtime', build / 'package', dirs_exist_ok=True)
    say('Building the local CoreML helper (first run).')
    command(['swift', 'build', '-c', 'release', '--package-path', build / 'package', '--scratch-path', build / '.build', '--product', 'TranscribeAudio'], build / 'build.log')
    if not binary.is_file():
        raise RuntimeError(f'Build did not produce the helper; see {build / "build.log"}')
    return binary


def downloader():
    # Isolated installation: never modify the user's system yt-dlp.
    env = CACHE / 'yt-dlp'
    py = env / 'bin' / 'python'
    if not py.exists():
        command([sys.executable, '-m', 'venv', env])
        say('Installing yt-dlp in the skill cache.')
        command([py, '-m', 'pip', 'install', '--upgrade', '--pre', 'yt-dlp[default]'], CACHE / 'setup.log')
    return [str(py), '-m', 'yt_dlp', '--ignore-config', '--no-cache-dir', '--no-playlist',
            '--js-runtimes', 'node', '--no-progress', '--retries', '3', '--fragment-retries', '3',
            '--extractor-retries', '3', '--abort-on-unavailable-fragments']


def slug(title):
    result = re.sub(r'[^\w .-]+', '-', title, flags=re.UNICODE)
    return re.sub(r'[\s.-]+', '-', result).strip('-')[:90] or 'recording'


def model_stamp():
    root = CACHE / 'models'
    if not root.exists():
        return None
    return identity([(str(p.relative_to(root)), p.stat().st_size, p.stat().st_mtime_ns)
                     for p in sorted(root.rglob('*')) if p.is_file()])


def preserved_words(raw):
    words = raw['words']
    # Never silently replace recognizer text with a differently decoded word list.
    if ''.join(raw['text'].split()) != ''.join(''.join(w['word'].split()) for w in words):
        raise RuntimeError('Recognizer text and word alignment differ; raw output retained, transcript not rewritten.')
    return words


def speaker_for(word, segments):
    mid = (word['startTime'] + word['endTime']) / 2
    active = {s['speaker'] for s in segments if s['start'] <= mid <= s['end']}
    if len(active) == 1:
        return next(iter(active))
    if len(active) > 1:
        return 'overlap'
    # RNNT timings describe token emission, not exact word boundaries. Allow a
    # small boundary tolerance, but do not pick a speaker across long gaps/ties.
    distances = [(min(abs(mid - s['start']), abs(mid - s['end'])), s['speaker']) for s in segments]
    if distances:
        distance = min(d for d, _ in distances)
        nearest = {speaker for d, speaker in distances if abs(d - distance) < 0.001}
        if distance <= 0.35 and len(nearest) == 1:
            return next(iter(nearest))
    return None


def render(raw, segments=None):
    words = preserved_words(raw)
    paragraphs, current, previous, names = [], [], None, {}
    previous_word = None
    def label(s):
        if s is None:
            return 'Speaker uncertain'
        if s == 'overlap':
            return 'Overlapping speakers'
        if s not in names:
            names[s] = f'Speaker {len(names) + 1}'
        return names[s]
    def flush():
        if current:
            body = ' '.join(current)
            # Escape Markdown syntax so recognized text is rendered as literal speech.
            body = re.sub(r'([\\`*_{}\[\]<>#|])', r'\\\1', body)
            body = re.sub(r'^(\d+)\.', r'\1\\.', body)
            if body.startswith(('- ', '+ ', '!')):
                body = '\\' + body
            paragraphs.append((f'**{label(previous)}:** ' if segments is not None else '') + body)
            current.clear()
    for word in words:
        speaker = speaker_for(word, segments) if segments is not None else None
        pause = previous_word is not None and word['startTime'] - previous_word['endTime'] >= 1.5
        sentence = current and re.search(r'[.!?][\"\u201d\u2019]*$', current[-1])
        if current and ((segments is not None and speaker != previous) or
                        (len(current) >= 40 and (pause or sentence)) or len(current) >= 120):
            flush()
        previous = speaker
        current.append(word['word'])
        previous_word = word
    flush()
    if not words:
        return '# Transcript\n\n[No speech recognized.]\n'
    return '# Transcript\n\n' + '\n\n'.join(paragraphs) + '\n'


def file_matches(path, expected):
    return Path(path).is_file() and digest(path) == expected


def execute(args):
    check_host()
    CACHE.mkdir(parents=True, exist_ok=True)
    # Serialize runs to protect model downloads, builds, and output publication.
    with (CACHE / 'runtime.lock').open('a') as lock:
        say('Preparing local transcription.')
        fcntl.flock(lock, fcntl.LOCK_EX)
        return process(args)


def process(args):
    source_arg = args.source
    is_url = urlsplit(source_arg).scheme in ('https', 'http')
    if args.audio_format and not is_url:
        raise RuntimeError('--audio-format applies only to URL downloads.')
    refresh_source = args.refresh_source or bool(args.audio_format)
    root = Path(args.destination).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    source_id = identity({'url': source_arg}) if is_url else None
    old = None
    folder = None
    # URL identity allows offline reuse without re-querying the platform.
    if is_url:
        for candidate in sorted(root.glob('*/metadata.json')):
            try:
                data = read_json(candidate)
                if data.get('source', {}).get('identity') == source_id:
                    folder, old = candidate.parent, data
                    break
            except (OSError, ValueError):
                continue
        if old and not refresh_source:
            source = old['source']
            audio = folder / source['audio_file']
            if not file_matches(audio, source.get('sha256')):
                old = None
        if old is None or refresh_source:
            dl = downloader()
            selection = ['-f', args.audio_format or 'bestaudio/best']
            info = json.loads(command(dl + selection + ['--dump-single-json', '--skip-download', '--', source_arg]))
            if info.get('_type') in ('playlist', 'multi_video') or info.get('is_live') or info.get('live_status') in ('is_live', 'is_upcoming'):
                raise RuntimeError('Provide one completed recording; playlists and ongoing/upcoming livestreams are not transcribed implicitly.')
            title = info.get('title') or info.get('id') or 'recording'
            folder = folder or root / f'{slug(title)}-{source_id[:10]}'
            folder.mkdir(parents=True, exist_ok=True)
            temporary = folder / '.work'
            temporary.mkdir(exist_ok=True)
            report = temporary / 'download-path.txt'
            report.unlink(missing_ok=True)
            # Different formats must not resume one another's partial downloads.
            opts = selection + ['--no-write-subs', '--no-write-auto-subs', '--print-to-file', 'after_move:filepath', str(report), '-o', str(temporary / 'source.%(format_id)s.%(ext)s'), '--', source_arg]
            command(dl + opts, folder / 'run.log')
            downloaded = Path(report.read_text().strip().splitlines()[-1]).resolve()
            if downloaded.parent != temporary.resolve() or not downloaded.is_file():
                raise RuntimeError('Download did not produce the expected source file.')
            probe = json.loads(command(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', downloaded]))
            if any(stream.get('codec_type') == 'video' for stream in probe.get('streams', [])):
                # Generic sites sometimes offer only a combined video/audio file.
                extracted = temporary / 'source.mka'
                command(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', downloaded, '-map', '0:a:0', '-vn', '-c:a', 'copy', extracted], folder / 'run.log')
                downloaded = extracted
            audio = folder / downloaded.name
            os.replace(downloaded, audio)
            source = {'identity': source_id, 'kind': 'url', 'url': source_arg, 'title': title,
                      'platform': info.get('extractor_key'), 'platform_id': info.get('id'),
                      'audio_file': audio.name, 'sha256': digest(audio), 'downloaded_at': now()}
    else:
        audio = Path(source_arg).expanduser().resolve(strict=True)
        if not audio.is_file():
            raise RuntimeError('Local input must be a regular audio/video file.')
        sha = digest(audio)
        source_id = identity({'path': str(audio), 'sha256': sha})
        folder = root / f'{slug(audio.stem)}-{source_id[:10]}'
        folder.mkdir(parents=True, exist_ok=True)
        source = {'identity': source_id, 'kind': 'local', 'path': str(audio), 'title': audio.stem, 'sha256': sha}
    meta_path = folder / 'metadata.json'
    if old is None and meta_path.exists():
        old = read_json(meta_path)
    revision = next(pin['state']['revision'] for pin in read_json(SKILL / 'runtime' / 'Package.resolved')['pins']
                    if pin['identity'] == 'fluidaudio')
    settings = {'model': MODEL, 'fluid_audio_revision': revision, 'runtime_key': runtime_key(),
                'mode': 'offline-15s', 'encoder_precision': 'int8', 'compute': 'cpuAndNeuralEngine',
                'language': 'en', 'speakers': args.speakers, 'format_version': FORMAT_VERSION}
    if args.speakers:
        settings['speaker_alignment'] = 'emission-midpoint-nearest-0.35s-v1'
    transcript = folder / 'transcript.md'
    raw_path = folder / 'recognition.json'
    speaker_path = folder / 'speakers.json'
    key = identity({'audio': source['sha256'], 'settings': settings})
    current_stamp = model_stamp()
    reuse = (not args.rerun and old and old.get('status') == 'complete' and old.get('key') == key
             and old.get('model_cache_stamp') == current_stamp
             and file_matches(raw_path, old.get('recognition_sha256'))
             and file_matches(transcript, old.get('transcript_sha256'))
             and (not args.speakers or file_matches(speaker_path, old.get('speakers_sha256'))))
    if reuse:
        if args.mp3:
            export_mp3(audio, folder, source['sha256'])
        say('Reusing the matching completed transcription.')
        print(json.dumps({'status': 'reused', 'transcript': str(transcript), 'directory': str(folder), 'warnings': old.get('warnings', [])}))
        return
    temporary = folder / '.work'
    temporary.mkdir(exist_ok=True)
    meta = {'status': 'processing', 'source': source, 'settings': settings, 'key': key, 'started_at': now(),
            'host': {'os': platform.mac_ver()[0], 'architecture': platform.machine()}}
    # Preserve previous durable results when regenerating, including manual edits.
    if transcript.exists():
        archive = folder / 'history' / str(time.time_ns())
        archive.mkdir(parents=True)
        for p in (transcript, raw_path, speaker_path, meta_path):
            if p.exists():
                shutil.copy2(p, archive / p.name)
        meta['previous_result'] = str(archive.relative_to(folder))
    atomic(meta_path, meta)
    try:
        binary = runtime()
        probe = json.loads(command(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', audio]))
        if not any(s.get('codec_type') == 'audio' for s in probe.get('streams', [])):
            raise RuntimeError('Input contains no audio stream.')
        meta['duration_seconds'] = float(probe.get('format', {}).get('duration', 0))
        meta['audio_streams'] = [{k: s.get(k) for k in ('codec_name', 'sample_rate', 'channels')} for s in probe['streams'] if s.get('codec_type') == 'audio']
        atomic(meta_path, meta)
        wav = temporary / 'audio.wav'
        say('Decoding audio to 16 kHz mono; transcription runs locally.')
        decode_log = folder / 'decode.log'
        decode_log.write_text('')
        command(['ffmpeg', '-nostdin', '-v', 'error', '-xerror', '-y', '-i', audio, '-map', '0:a:0', '-vn', '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le', wav], decode_log)
        if decode_log.read_text().strip():
            meta['warnings'] = ['FFmpeg reported decoding diagnostics despite a successful exit. Full audio duration is checked, but this does not guarantee every packet decoded correctly; see decode.log.']
            atomic(meta_path, meta)
            say(meta['warnings'][0])
        say('Recognizing speech with Parakeet Unified EN (offline). First use downloads model weights.')
        command([binary, 'asr', wav, raw_path, CACHE / 'models'], folder / 'run.log')
        raw = read_json(raw_path)
        expected = meta['duration_seconds']
        if expected and abs(raw['duration'] - expected) > max(2, expected * 0.001):
            raise RuntimeError('Decoded audio duration differs from source; refusing to mark the transcript complete.')
        segments = None
        if args.speakers:
            say('Assigning speaker labels locally.')
            command([binary, 'speakers', wav, speaker_path, CACHE / 'models'], folder / 'run.log')
            segments = read_json(speaker_path)
        if digest(audio) != source['sha256']:
            raise RuntimeError('Source audio changed during processing; rerun using a stable input.')
        atomic(transcript, render(raw, segments))
        if args.mp3:
            export_mp3(audio, folder, source['sha256'])
        meta.update(status='complete', completed_at=now(), model_cache_stamp=model_stamp(),
                    recognition_sha256=digest(raw_path), transcript_sha256=digest(transcript),
                    recognized_words=len(raw['words']), recognized_audio_seconds=raw['duration'])
        if args.speakers:
            meta['speakers_sha256'] = digest(speaker_path)
        elif speaker_path.exists():
            speaker_path.unlink()
        atomic(meta_path, meta)
        shutil.rmtree(temporary)
        print(json.dumps({'status': 'complete', 'transcript': str(transcript), 'directory': str(folder), 'words': len(raw['words']), 'warnings': meta.get('warnings', [])}))
    except BaseException as exc:
        meta.update(status='incomplete', failed_at=now(), error=str(exc))
        atomic(meta_path, meta)
        raise


def export_mp3(audio, folder, source_hash):
    # A local MP3 already satisfies the request; do not duplicate it.
    if audio.suffix.lower() == '.mp3':
        return
    target, record = folder / 'audio.mp3', folder / 'mp3.json'
    if target.exists() and record.exists():
        info = read_json(record)
        if info.get('source_sha256') == source_hash and file_matches(target, info.get('sha256')):
            return
    pending = folder / '.audio.pending.mp3'
    command(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', audio, '-map', '0:a:0', '-vn', '-c:a', 'libmp3lame', '-q:a', '2', pending], folder / 'run.log')
    os.replace(pending, target)
    atomic(record, {'source_sha256': source_hash, 'sha256': digest(target)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', help='One recording URL or local audio/video path')
    parser.add_argument('--destination', default='~/Documents/Transcriptions', help='Parent directory for recording folders')
    parser.add_argument('--speakers', action='store_true', help='Opt in to local speaker labeling')
    parser.add_argument('--mp3', action='store_true', help='Also export MP3, if source is not already MP3')
    parser.add_argument('--rerun', action='store_true', help='Rerun recognition using retained source audio')
    parser.add_argument('--refresh-source', action='store_true', help='Fetch URL audio again, then check/recompute recognition')
    parser.add_argument('--audio-format', help='Select a verified yt-dlp audio format for this URL; implies --refresh-source')
    args = parser.parse_args()
    try:
        execute(args)
    except (Exception, KeyboardInterrupt) as exc:
        print(json.dumps({'status': 'incomplete', 'error': str(exc) or 'Interrupted'}), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
