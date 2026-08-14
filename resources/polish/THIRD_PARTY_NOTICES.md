# Polish third-party notices

## Gemma 4 model packs

- Models: Gemma 4 E2B, E4B, and 12B by Google DeepMind
- Source: https://ai.google.dev/gemma/docs/core/model_card_4
- License: Apache License 2.0
- Delivery: exact pinned Ollama registry manifests listed in `model-packs.json`
- Thinking mode is disabled for Manuscript correction.

## llama.cpp

- Project: llama.cpp
- Release: `b10103`
- Commit: `c588c4f47683e73ad2d69f50480bec6cc85fd0f7`
- Source: https://github.com/ggml-org/llama.cpp
- License: MIT
- Delivery: exact CPU, CUDA, Vulkan, HIP, and SYCL release archives pinned in
  the signed `runtime-packs.json` allowlist

## all-MiniLM-L6-v2

- Project: sentence-transformers/all-MiniLM-L6-v2
- Revision: `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`
- File: `onnx/model_quint8_avx2.onnx`
- Model SHA-256: `b941bf19f1f1283680f449fa6a7336bb5600bdcd5f84d10ddc5cd72218a0fd21`
- License: Apache License 2.0
- Source: https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2

## LibreOffice English dictionaries

- Project: LibreOffice dictionaries
- Revision: `da8a7e73fd26a134ad7c6438fa7c310730906b3a`
- Files: `en_US.aff`, `en_US.dic`
- License and attribution: bundled beside the dictionary as `license.txt` and `README_en_US.txt`
- Source: https://github.com/LibreOffice/dictionaries

## Word-class table

- File: `pos/en_US.pos`
- Derived from the LibreOffice English dictionaries listed above, by
  `tools/build_pos_lexicon.py`. It is a transformation of `en_US.dic` and
  `en_US.aff` and carries the same licence and attribution; no other source
  contributes to it.

## SymSpell English frequency dictionary

- Project: SymSpell
- Revision: `c239062ae02961df18ab7da1671d01b4388204e0`
- File: `frequency_dictionary_en_82_765.txt`
- License: MIT; bundled as `LICENSE`
- Source: https://github.com/wolfgarbe/SymSpell

## English misspelling corpus

- Project: Misspelled Words, by Fazil B Topal
- File: `misspellings/misspellings-en.txt`
- License: MIT
- Source: https://www.kaggle.com/datasets/fazilbtopal/misspelled-words
- Modifications: derived, not verbatim. 36,960 unique source pairs were filtered
  to 24,691 against the bundled dictionary. Removed: 2,057 whose "misspelling"
  is a real English word (`about`, `accept`, `absorb`), 1,826 ambiguous pairs
  mapping one typo to several corrections, 1,325 non-alphabetic entries such as
  `liason` -> `Mara_Liasson`, 674 shorter than four characters, 464 whose
  correction the dictionary does not know, and 150 already handled by
  context-aware rules in `polish_spelling.py`, and 5,773 implausible pairs
  whose correction bears no resemblance to the misspelling, such as
  `aacock` -> `helicopter`.
