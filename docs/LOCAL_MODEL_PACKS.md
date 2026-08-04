# Local Speech Model Packs

Echo-Link never downloads a speech model at runtime. It uses complete local faster-whisper CTranslate2 model folders.

## Installed locations

- Bundled model: `models\base.en`
- User model packs: `%LOCALAPPDATA%\Echo-Link\models\<model-name>`
- Optional development override: set `ECHOLINK_MODEL_DIR` to a local model root.

Each model folder must contain at least:

- `model.bin`
- `config.json`
- The tokenizer and vocabulary files supplied with that converted model.

## Use a multilingual pack

1. Obtain or convert a faster-whisper CTranslate2 model outside Echo-Link.
2. Open Settings, then select **Open local model folder**.
3. Copy the complete model folder into that location.
4. Restart Echo-Link.
5. Select the installed speech model and language in Settings.

Folders ending in `.en` are treated as English-only. Other valid local packs enable the language list and automatic detection.

Echo-Link does not upload speech, download a model, require an account, or call a paid API during this workflow.
