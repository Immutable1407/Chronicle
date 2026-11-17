# Voice and transcription / 语音与转写

Archiving audio and transcribing it are separate steps. A copied original voice asset is preserved even when decoding or transcription fails. Mac SILK playback uses the optional `pilk` dependency to generate a WAV derivative. The UI can play available audio and save manually supplied text.

No cloud service or key is configured by default. The generic HTTP adapter accepts a user-selected multipart audio endpoint, model and key environment variable. Provider compatibility must be checked for the chosen service. Current defaults are not a claim that any particular provider has been tested.

Copy `config/asr-profile.example.json` into a private location and edit the service/model. Put the API key in the configured environment variable; do not add the key to JSON or shell history. Then use the archive CLI:

```sh
python3 -m chronicle --root /path/to/archive queue-transcription \
  --message MESSAGE_ID --profile /path/to/private-profile.json
python3 -m chronicle --root /path/to/archive run-transcription \
  --job JOB_ID --profile /path/to/private-profile.json
python3 -m chronicle --root /path/to/archive correct-transcript \
  --message MESSAGE_ID --text 'Corrected text'
```

Only running a configured transcription job sends its audio to that endpoint. Job state, machine output and manual corrections are stored separately. Failures require explicit retry; normal browsing or importing does not silently send or retry audio. The current UI's “补充文字” is manual text entry, not an automatically configured recognition service.
