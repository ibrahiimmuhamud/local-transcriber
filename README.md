# Local Transcriber

Turn your recordings into timestamped TXT transcripts and SRT subtitles using Whisper on your own computer. Queue a folder or a YouTube playlist, follow progress in a browser, and resume a batch after stopping it.

The model runs locally. The first run downloads the selected model from Hugging Face. Processing uses your hardware, with no transcription API key or usage charge. Internet access is needed for model downloads and YouTube inputs.

## What it does

- Accepts local audio/video files, folders, and YouTube video or playlist URLs.
- Transcribes in the spoken language or translates speech into English.
- Processes one recording at a time and skips completed jobs when restarted.
- Saves a persistent SQLite queue, a portable JSON index, and dated output folders.
- Shows progress, errors, file downloads, and review notes on a local dashboard.
- Removes its temporary downloaded audio after each job. Your original local files are retained.

This project grew out of organizing a long-form English/Arabic lecture archive. This public version accepts your own inputs and has no channel-specific manifest or bundled recordings. It uses the general Whisper transcription/translation workflow. Specialized Quran matching and the archive’s individually reviewed corrections are outside this version.

## Tamkeen Youth transcript archive

Browse the [85 English halaqa transcripts](archives/tamkeen-halaqas/README.md), organized by year and month with dates, original titles, timestamps, and links to the recordings. These are machine-generated drafts with unresolved wording marked in the text.

## Install

Use Python 3.10 or newer. macOS, Linux, and Windows can run the CPU version. An NVIDIA GPU can accelerate processing with a compatible CUDA/cuDNN installation. Apple Silicon uses the CPU backend in this release.

```bash
git clone https://github.com/ibrahiimmuhamud/local-transcriber.git
cd local-transcriber
python -m venv .venv
```

Activate the environment on macOS/Linux with `source .venv/bin/activate`, or on Windows PowerShell with `.venv\Scripts\Activate.ps1`. Then install the tool.

```bash
python -m pip install -e .
```

Audio decoding uses the FFmpeg libraries bundled with PyAV. Basic local transcription does not require a separate FFmpeg command-line installation. YouTube downloads may need additional tooling such as a supported JavaScript runtime. Follow the [yt-dlp setup instructions](https://github.com/yt-dlp/yt-dlp#dependencies) if YouTube requests one.

## Transcribe a file or folder

```bash
local-transcriber "./recordings" --output "./transcripts" --dashboard
```

Open **http://127.0.0.1:8787** for progress. The dashboard stays open after the batch finishes. Press Ctrl+C to close it. It is served only on your own computer and loads its font locally.

Use `--language en` for consistently English speech. Omit `--language` for automatic language detection, including recordings that switch languages. Explicitly forcing a language can reduce accuracy in passages spoken in another language.

## Translate speech to English

```bash
local-transcriber "./recordings" --task translate --output "./english-transcripts" --dashboard
```

`--task translate` asks Whisper to render speech in English. It works with multilingual models such as `large-v3`, `medium`, `small`, `base`, and `tiny`. Use a multilingual model for translation. Some models optimized for transcription, including `turbo`, perform poorly on translation. Model accuracy varies with language, audio quality, and context.

## Transcribe YouTube recordings

```bash
local-transcriber "https://www.youtube.com/watch?v=VIDEO_ID" --task translate --dashboard
local-transcriber "https://www.youtube.com/playlist?list=PLAYLIST_ID" --output "./playlist-transcripts" --dashboard
```

Provide recordings you own or have permission to process. Use video or playlist URLs. Nested channel pages are unsupported. Active livestreams and scheduled broadcasts are skipped. The tool downloads audio only, one recording at a time. It does not upload or publish subtitles.

YouTube can block automated downloads or require authentication. In that case the job keeps its error in the dashboard. Supply an authorized local recording, or resolve the download access and rerun. The tool does not read browser cookies or sign into your accounts.

## Resume and choose performance settings

```bash
# Resume the saved queue and retry failed/interrupted items.
local-transcriber --output "./transcripts" --dashboard

# A smaller model for a faster draft.
local-transcriber "./recordings" --model small --threads 4 --output "./drafts"

# NVIDIA GPU, with the required CUDA/cuDNN libraries already installed.
local-transcriber "./recordings" --device cuda --compute-type float16

# View existing files without running the worker.
local-transcriber --output "./transcripts" --serve-only
```

The default model is `large-v3`, with CPU int8 inference and up to eight CPU threads. Its initial download is several GB. Smaller models use less memory and usually finish faster. Processing speed depends on recording length and hardware. See [faster-whisper’s hardware requirements](https://github.com/SYSTRAN/faster-whisper#requirements) for GPU setup.

Every queued job stores its model, language, task, device, compute type, and thread count. Resuming uses those saved settings. To change settings for the same recording, add the input again with the new flags. This creates a separate job and preserves its previous output. Local recordings are deduplicated by content, YouTube videos by video ID, within each settings combination.

Completed recordings are skipped. A recording interrupted during inference starts again from the beginning. Only one worker can use an output folder at a time. Keep the computer awake and the worker process running until the batch finishes. The tool does not install a background service or prevent system sleep.

## Files you get

```text
transcripts/
  queue.sqlite3
  index.json
  2026-01-15 - Your recording [job-id]/
    transcript.txt
    subtitles.srt
    review.json
    metadata.json
```

Local folder dates use the recording’s file modification date. YouTube dates use metadata available during inventory, which can be absent for playlist entries. Unknown dates are labeled `Undated`. These dates are organizational hints and may differ from the recording or broadcast date.

TXT and SRT share wording and timestamps. `review.json` flags low-confidence passages, possible repetition, and Arabic script left in an English translation. These checks cannot establish accuracy or completeness. Proper names, code-switching, religious quotations, silence, and poor recordings need particular care. A recording with zero flags can still contain errors. Review against the source before distributing subtitles as authoritative.

The output folder includes a local database containing source paths and errors. Share the dated transcript folders or selected text/subtitle files. Generated outputs and credentials are excluded from this repository by default.

## Development

```bash
python -m unittest discover -s tests -v
```

The tests run without downloading a Whisper model. A real model is needed for an end-to-end transcription check. To contribute, open an issue with reproduction steps or submit a pull request. Use synthetic fixtures and avoid committing private recordings or transcripts.

## License and credits

MIT license for this tool. [faster-whisper](https://github.com/SYSTRAN/faster-whisper) provides inference, [Whisper](https://github.com/openai/whisper) supplies the speech models, and [yt-dlp](https://github.com/yt-dlp/yt-dlp) handles authorized YouTube audio access. Their licenses and model terms apply separately. Bricolage Grotesque is bundled under the SIL Open Font License in `local_transcriber/assets/OFL.txt`.
