#!/usr/bin/env python3
"""Decode a local SILK asset to mono PCM WAV; requires the optional pilk package."""
import argparse
import json
import os
from pathlib import Path
import struct
import tempfile
import wave


def decode(source, output):
    import pilk
    source, output = Path(source), Path(output)
    if output.exists():
        raise RuntimeError('output_already_exists')
    with source.open('rb') as stream:
        header = stream.read(16)
    if b'#!SILK_V3' not in header:
        raise RuntimeError('unsupported_voice_container')
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix='chronicle-pcm-') as temporary:
        pcm = Path(temporary) / 'voice.pcm'
        pilk.decode(str(source), str(pcm), pcm_rate=24000)
        data = pcm.read_bytes()
    if not data or len(data) % 2:
        raise RuntimeError('invalid_decoded_pcm')
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        with wave.open(stream, 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(24000)
            wav.writeframes(data)
    return {'sample_rate': 24000, 'channels': 1, 'samples': len(data) // 2,
            'duration_seconds': len(data) / 48000,
            'nonzero_samples': sum(sample[0] != 0 for sample in struct.iter_unpack('<h', data))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    print(json.dumps(decode(args.source, args.output)))


if __name__ == '__main__':main()
