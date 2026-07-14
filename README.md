# BitLife RU Translation

**[English](README.md)** | **[Русский](README.ru.md)**

---

## Overview

Automated pipeline for translating BitLife (Android, v3.24.3) from English to Russian. The game is built on Unity 6000.3.9f1 with IL2CPP backend, which means all C# string literals are stored in `global-metadata.dat` (binary format) rather than in `Assembly-CSharp.dll`.

This project provides:

- Python parser for IL2CPP v39 metadata format
- Parallel translator (5 threads, Google Translate API)
- In-place byte patcher that preserves binary structure
- Android resource (`strings.xml`) translator
- APK repackaging and signing pipeline

## Technical Background

### IL2CPP Metadata Format (v39)

Unity IL2CPP compiles C# to C++ and stores metadata in `global-metadata.dat`. The file begins with a header:

```
0x00  uint32  magic           (0xFAB11BAF)
0x04  int32   version         (39 for Unity 6.x)
0x08  pairs of (int32 offset, int32 size) for each section
```

Relevant sections:

| Section | Purpose | Size in BitLife |
|---------|---------|-----------------|
| `stringLiteral` | Array of `{uint32 length; uint32 dataIndex}` entries | 280,624 B (35,078 entries) |
| `stringLiteralData` | Raw UTF-8 blob, indexed by `dataIndex` | 281,004 B |
| `string` | Null-terminated type/method names | 70,155 B |

The `stringLiteralData` blob contains all C# string literals concatenated. The `stringLiteral` table maps each literal to its `(length, offset)` pair within the blob. Game text (event descriptions, dialogue) lives here.

### Why In-Place Patching

Standard approach would be: parse metadata → translate → rebuild metadata → repack. This fails because:

1. The `dataIndex` values in the `stringLiteral` table are absolute offsets within the `stringLiteralData` section
2. Russian text is typically 1.3-1.5x longer than English in UTF-8 bytes (Cyrillic = 2 bytes/char vs ASCII = 1 byte/char)
3. Rebuilding requires rewriting every offset in the table, which is error-prone

**Solution**: In-place byte replacement. We overwrite the English text bytes with Russian text bytes, padding with nulls if the Russian is shorter, or truncating at UTF-8 boundaries if longer. The `stringLiteral` table stays untouched. Game reads the string until the first null byte (standard C string behavior), so padding is invisible.

### Why Some Strings Are Truncated

When `len(russian_bytes) > len(english_bytes)`, we truncate the Russian text at the nearest valid UTF-8 character boundary to fit the original byte length. This causes ~50% of long event descriptions to be cut off mid-sentence.

To fix this properly, you would need to:
1. Rewrite the `stringLiteral` table with new offsets
2. Rebuild the `stringLiteralData` section with the new layout
3. Update the section size in the header

This is implementable but requires careful handling of the binary format. Left as future work.

## Project Structure

```
bitlife-ru-translation/
├── scripts/
│   ├── il2cpp_extract.py             # Metadata parser, dumps strings to JSON
│   ├── translate_metadata.py         # v1: single-threaded translator (slow)
│   ├── translate_metadata_v2.py      # v2: with resume support
│   ├── translate_metadata_batch.py   # v3: batch translation via ||| separator
│   ├── translate_metadata_parallel.py# v4: 5-thread parallel (final, fastest)
│   ├── translate_strings_xml.py      # Android resources translator
│   └── github_upload.py              # GitHub API uploader (uses env vars)
├── docs/
│   └── patch_report.json             # Final statistics
├── README.md                         # This file (English)
├── README.ru.md                      # Russian documentation
├── LICENSE                           # MIT
└── .gitignore
```

## Pipeline

### Step 1: Decode APK

```bash
java -jar apktool.jar d bitlife.apk -o bitlife_decoded
```

Output directory contains `AndroidManifest.xml`, `res/`, `assets/`, `lib/`, and `smali_*` directories (one per `.dex` file).

### Step 2: Locate Metadata

```
bitlife_decoded/
└── assets/bin/Data/
    ├── Managed/
    │   └── Metadata/
    │       └── global-metadata.dat    # ← Target file (18.5 MB)
    ├── data.unity3d                   # Asset bundle (not patched)
    ├── datapack.unity3d               # Asset bundle (not patched)
    └── sharedassets0.resource         # Scene assets (not patched)
└── lib/arm64-v8a/
    └── libil2cpp.so                   # Native code (101 MB, not patched)
```

### Step 3: Extract Strings

```bash
python scripts/il2cpp_extract.py
```

Outputs JSON files in `work/bitlife/strings_extracted/`:
- `string_literals_all.json` — all 35,078 entries
- `string_literals_non_empty.json` — non-empty entries
- `header_info.json` — section offsets and sizes

### Step 4: Translate Metadata

```bash
python scripts/translate_metadata_parallel.py
```

The translator:

1. Scans `global-metadata.dat` for ASCII-printable sequences ≥5 chars
2. Filters: must contain spaces and alphabetic characters (excludes identifiers, paths, hex strings)
3. Deduplicates by text content (preserves all offsets)
4. Translates unique strings in parallel (5 threads, each with its own `GoogleTranslator` instance)
5. Saves progress to `translation/translations_progress.json` every 50 strings (resume support)
6. Patches the binary in-place

**Speed**: ~23ms per string with 5 threads. Full translation of 8,111 strings takes ~3-4 minutes (excluding rate-limit backoff).

**Resume**: If interrupted, re-run the script. It loads `translations_progress.json` and skips already-translated strings.

### Step 5: Translate Android Resources

```bash
python scripts/translate_strings_xml.py
```

Creates `res/values-ru/strings.xml` from `res/values/strings.xml`. Skips system strings (boolean values, package names, color codes, hex strings).

### Step 6: Build APK

```bash
java -jar apktool.jar b bitlife_decoded -o bitlife_ru_unsigned.apk
```

### Step 7: Sign APK

```bash
java -jar uber-apk-signer.jar --apks bitlife_ru_unsigned.apk --out signed/
```

Uses embedded debug keystore. Output: `bitlife_ru_unsigned-aligned-debugSigned.apk` with v2 + v3 signatures.

## Configuration

### Filter Tuning

The `is_game_sentence()` function in `translate_metadata_parallel.py` controls which strings get translated. Current filter:

```python
def is_game_sentence(text):
    if len(text) < 5: return False
    if ' ' not in text: return False
    if not re.search(r'[a-zA-Z]{3,}', text): return False
    if re.match(r'^[a-z]+(\.[a-zA-Z_][a-zA-Z0-9_]*)+$', text): return False  # package names
    if '://' in text: return False  # URLs
    if alpha_count / len(text) < 0.3: return False
    return True
```

To translate more aggressively (e.g., single-word UI labels), relax the space requirement. To be more conservative, increase the minimum length.

### Thread Count

Edit `ThreadPoolExecutor(max_workers=5)` in `translate_metadata_parallel.py`. Higher values risk Google Translate rate-limiting (HTTP 429). The script handles 429 with exponential backoff.

### Translator Backend

Default: `GoogleTranslator` from `deep-translator` library (uses public Google Translate web endpoint, no API key required).

Alternatives (edit `translate_one()` function):

```python
from deep_translator import DeepL
translator = DeepL(api_key="...", source="en", target="ru")  # better quality, paid

from deep_translator import YandexTranslator
translator = YandexTranslator(api_key="...", source="en", target="ru")  # good for RU
```

## Limitations

### Not Translated

| Component | Reason | Fix Difficulty |
|-----------|--------|----------------|
| `libil2cpp.so` (enum names, profession titles) | Compiled native code, would require binary patching of ARM64 instructions | Hard |
| `datapack.unity3d` (asset bundle text) | Unity serialized binary format, requires AssetStudio/UABE to extract and rebuild | Medium |
| `sharedassets0.resource` (scene text) | Same as above | Medium |
| Long event descriptions (4,178 strings) | Russian UTF-8 bytes > English ASCII bytes, truncated to fit | Requires metadata rebuild (see above) |

### Known Issues

1. **Truncated strings**: ~50% of translated strings are cut at the original English byte length. Long events may end mid-word.
2. **Debug signature**: Cannot overwrite official BitLife installation; must uninstall first.
3. **Update breaks translation**: Each BitLife update changes `global-metadata.dat` offsets. Re-running the pipeline on a new APK takes ~5 minutes.
4. **Placeholders preserved**: Format strings like `{0}`, `<he/she>`, `<color=yellow>` are preserved as-is, which may produce awkward Russian phrasing.

## Requirements

```
Python 3.10+
apktool 2.9.3+
OpenJDK 21+
uber-apk-signer 1.3.0+
```

Python packages:

```
deep-translator>=1.11
requests>=2.31
```

## Statistics (v3.24.3)

| Metric | Value |
|--------|-------|
| APK size (original) | 246 MB |
| APK size (translated) | 249 MB |
| Total strings in metadata | 35,078 |
| Unique game sentences | 8,111 |
| Strings translated | 8,111 |
| In-place patches applied | 4,451 |
| Strings truncated | 4,178 |
| Strings skipped (no change) | 4,079 |
| UI strings translated | 599 |
| Translation time | ~20 min (with rate-limit pauses) |

## Reproduction

To reproduce on a different BitLife version or different IL2CPP game:

1. Obtain the APK (legally, from your own device via `adb pull`)
2. Run the pipeline steps above
3. The translator is game-agnostic; it translates any English text in `global-metadata.dat`

## Legal

BitLife is a trademark of Candywriter, LLC. This project is not affiliated with Candywriter. Use only on legally obtained copies of the game.

License: MIT (see [LICENSE](LICENSE))
