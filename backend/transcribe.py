"""Local ASR with timestamped, resumable evidence; never sends audio to a model API."""
import argparse
import hashlib
import json
from pathlib import Path


def transcribe(audio, folder, model_name='base'):
    import os
    import wave
    import numpy as np
    from .settings import load
    settings = load()
    dll_handles = [os.add_dll_directory(path) for path in settings.get('asr_dll_dirs', [])]
    from faster_whisper import WhisperModel
    audio, folder = Path(audio), Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(audio.read_bytes()).hexdigest()
    manifest_path = folder / 'transcription.json'
    log = folder / 'segments.jsonl'
    old = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {}
    if old and (old.get('audio_sha256') != digest or old.get('model') != model_name):
        raise ValueError('音频或转写模型已变更，请使用新输出目录')
    existing = [json.loads(line) for line in log.read_text(encoding='utf-8').splitlines()] if log.exists() else []
    if old.get('state') == 'pending_review':
        return old
    manifest = {'state': 'loading_model', 'audio_sha256': digest, 'model': model_name,
                'method': 'local_faster_whisper_int8_cpu', 'coverage': '自动转写草稿；术语、数字、否定条件待核对，不自动批准出题'}
    def save():
        temporary = manifest_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(manifest_path)
    save()
    try:
        model_path = settings.get('asr_model_path', model_name)
        if model_path != model_name and settings.get('asr_model_sha256'):
            actual = hashlib.sha256((Path(model_path)/'model.bin').read_bytes()).hexdigest()
            if actual != settings['asr_model_sha256']:
                raise ValueError('本地ASR模型哈希不匹配')
        model = WhisperModel(model_path, device='cpu', compute_type='int8', cpu_threads=min(8, os.cpu_count() or 4))
        offset = existing[-1]['end'] if existing else 0
        # WAV is already normalized by ffmpeg. Decode directly instead of coupling
        # the product to changing PyAV keyword arguments.
        with wave.open(str(audio),'rb') as source:
            if (source.getnchannels(),source.getframerate(),source.getsampwidth()) != (1,16000,2):
                raise ValueError('转写需要16kHz单声道16位PCM WAV')
            samples = np.frombuffer(source.readframes(source.getnframes()),dtype='<i2').astype(np.float32)/32768
        segments, info = model.transcribe(samples, language='zh', beam_size=3,
            vad_filter=False, clip_timestamps=str(offset),
            initial_prompt='保留原话中的术语、数字、单位和否定条件，不补充内容。')
        manifest.update(state='transcribing', duration=info.duration)
        save()
        with log.open('a', encoding='utf-8') as output:
            for segment in segments:
                if not segment.text.strip():
                    continue
                row = {'start': segment.start, 'end': segment.end, 'text': segment.text.strip(),
                       'avg_logprob': segment.avg_logprob, 'no_speech_prob': segment.no_speech_prob}
                output.write(json.dumps(row, ensure_ascii=False) + '\n')
                output.flush()
                existing.append(row)
                manifest.update(last_end=segment.end, segments=len(existing))
                save()
                if len(existing) % 25 == 0:
                    print(f'transcribed {segment.end:.0f}/{info.duration:.0f} seconds', flush=True)
        markdown = '# 学习材料音频转写草稿\n\n' + manifest['coverage'] + '\n'
        for row in existing:
            markdown += f'\n## {row["start"]:.1f}—{row["end"]:.1f} 秒\n\n{row["text"]}\n'
        (folder / 'transcript.md').write_text(markdown, encoding='utf-8')
        manifest.update(state='pending_review', segments=len(existing))
        save()
        return manifest
    except Exception as error:
        manifest.update(state='failed', error_type=type(error).__name__)
        save()
        raise RuntimeError('本地转写失败，已保存可恢复进度；不作为已完成课程') from None


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('audio', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--model', default='base')
    args = parser.parse_args()
    print(json.dumps(transcribe(args.audio, args.output, args.model), ensure_ascii=False))
