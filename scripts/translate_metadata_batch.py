"""
Batch-переводчик metadata с использованием конкатенации строк.
Один запрос переводит ~30 строк сразу (через разделитель).
Это ускоряет перевод в 30x раз!
"""
import json
import re
import sys
import time
from pathlib import Path
from deep_translator import GoogleTranslator
from deep_translator.exceptions import RequestError

METADATA_PATH = "/home/z/my-project/work/bitlife/bitlife_decoded/assets/bin/Data/Managed/Metadata/global-metadata.dat"
OUTPUT_DIR = Path("/home/z/my-project/work/bitlife/translation")
PROGRESS_FILE = OUTPUT_DIR / "translations_progress.json"
LOG_FILE = Path("/home/z/my-project/work/bitlife/logs/translation.log")
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

# Уникальный разделитель, который Google Translate не должен менять
# Используем ||| без пробелов - Google Translate не меняет пайпы
SEP = "|||"
SEP_JOIN = " ||| "  # с пробелами для лучшего перевода

def log(msg):
    print(msg, flush=True)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(msg + '\n')

def is_game_sentence(text):
    if not text or len(text) < 5:
        return False
    if ' ' not in text:
        return False
    if not re.search(r'[a-zA-Z]{3,}', text):
        return False
    if re.match(r'^[a-z]+(\.[a-zA-Z_][a-zA-Z0-9_]*)+$', text):
        return False
    if '://' in text:
        return False
    alpha_count = sum(1 for c in text if c.isalpha())
    if alpha_count / len(text) < 0.3:
        return False
    return True

def scan_strings(data):
    pattern = re.compile(rb'[\x20-\x7e]{5,}')
    strings = []
    for m in pattern.finditer(data):
        text = m.group().decode('ascii')
        if is_game_sentence(text):
            strings.append({
                "offset": m.start(),
                "length": len(m.group()),
                "text": text
            })
    
    unique = {}
    for s in strings:
        if s['text'] not in unique:
            unique[s['text']] = []
        unique[s['text']].append(s['offset'])
    
    return strings, unique

def load_progress():
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_progress(translations):
    tmp = PROGRESS_FILE.with_suffix('.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(translations, f, ensure_ascii=False)
    tmp.replace(PROGRESS_FILE)

def chunk_strings(texts, max_chars=2000):
    """Группируем строки в чанки до max_chars суммарно (меньше = надёжнее)"""
    chunks = []
    current_chunk = []
    current_size = 0
    
    for text in texts:
        # Размер строки + разделитель
        text_size = len(text) + len(SEP_JOIN)
        if current_size + text_size > max_chars and current_chunk:
            chunks.append(current_chunk)
            current_chunk = []
            current_size = 0
        current_chunk.append(text)
        current_size += text_size
    
    if current_chunk:
        chunks.append(current_chunk)
    
    return chunks

def translate_chunk(chunk, translator):
    """Переводим чанк строк одним запросом через конкатенацию"""
    # Объединяем строки с SEP_JOIN (с пробелами - Google лучше переводит)
    combined = SEP_JOIN.join(chunk)
    
    # Обрезаем если слишком длинно (Google limit ~5000 chars)
    if len(combined) > 4500:
        combined = combined[:4500]
        # Обрезаем по последнему полному SEP
        last_sep = combined.rfind(SEP_JOIN)
        if last_sep > 0:
            combined = combined[:last_sep]
            chunk = combined.split(SEP_JOIN)
    
    if not combined.strip():
        return {t: t for t in chunk}
    
    try:
        translated = translator.translate(combined)
        if not translated:
            return {t: t for t in chunk}
        
        # Разделяем ответ - пробуем разные варианты разделителя
        # Google может менять пробелы вокруг ||
        for sep_pattern in [SEP_JOIN, SEP, r'\s*\|\|\|\s*']:
            if isinstance(sep_pattern, str):
                parts = translated.split(sep_pattern)
            else:
                parts = re.split(sep_pattern, translated)
            if len(parts) == len(chunk):
                return dict(zip(chunk, [p.strip() for p in parts]))
        
        # Fallback: переводим по одной
        log(f"  Chunk split mismatch ({len(parts)} vs {len(chunk)}), falling back to individual")
        result = {}
        for t in chunk:
            try:
                ru = translator.translate(t[:4500])
                result[t] = ru or t
            except:
                result[t] = t
            time.sleep(0.05)
        return result
        
    except RequestError as e:
        if 'Too Many Requests' in str(e) or '429' in str(e):
            log(f"  Rate limited, sleeping 15s...")
            time.sleep(15)
            return translate_chunk(chunk, translator)  # retry
        log(f"  Error: {e}")
        return {t: t for t in chunk}
    except Exception as e:
        log(f"  Failed: {e}")
        return {t: t for t in chunk}

def fit_russian_to_length(russian_text, target_byte_length):
    if not russian_text:
        return b'\x00' * target_byte_length
    
    ru_bytes = russian_text.encode('utf-8')
    
    if len(ru_bytes) == target_byte_length:
        return ru_bytes
    
    if len(ru_bytes) < target_byte_length:
        return ru_bytes + b'\x00' * (target_byte_length - len(ru_bytes))
    
    # Длиннее - обрезаем по UTF-8 границе
    result = bytearray()
    i = 0
    while i < len(ru_bytes) and len(result) < target_byte_length:
        b = ru_bytes[i]
        if b < 0x80:
            if len(result) + 1 <= target_byte_length:
                result.append(b)
                i += 1
            else:
                break
        elif b < 0xC0:
            i += 1
        elif b < 0xE0:
            if len(result) + 2 <= target_byte_length and i + 1 < len(ru_bytes):
                result.extend(ru_bytes[i:i+2])
                i += 2
            else:
                break
        elif b < 0xF0:
            if len(result) + 3 <= target_byte_length and i + 2 < len(ru_bytes):
                result.extend(ru_bytes[i:i+3])
                i += 3
            else:
                break
        else:
            if len(result) + 4 <= target_byte_length and i + 3 < len(ru_bytes):
                result.extend(ru_bytes[i:i+4])
                i += 4
            else:
                break
    
    if len(result) < target_byte_length:
        result.extend(b'\x00' * (target_byte_length - len(result)))
    
    return bytes(result)

def main():
    log("\n" + "="*60)
    log("Starting BATCH metadata translation")
    log("="*60)
    
    with open(METADATA_PATH, 'rb') as f:
        data = bytearray(f.read())
    log(f"File size: {len(data):,} bytes")
    
    all_strings, unique = scan_strings(data)
    log(f"Total matches: {len(all_strings)}, unique: {len(unique)}")
    
    existing = load_progress()
    log(f"Existing translations: {len(existing)}")
    
    # Какие ещё нужно перевести
    to_translate = [t for t in unique.keys() if t not in existing]
    log(f"To translate: {len(to_translate)}")
    
    if not to_translate:
        log("✅ All already translated!")
    else:
        translator = GoogleTranslator(source='en', target='ru')
        
        # Группируем в чанки
        chunks = chunk_strings(to_translate, max_chars=3500)
        log(f"Chunks: {len(chunks)} (avg {len(to_translate)/len(chunks):.1f} strings per chunk)")
        
        translations = dict(existing)
        total_chunks = len(chunks)
        
        for i, chunk in enumerate(chunks):
            if i % 3 == 0:
                log(f"  Chunk {i+1}/{total_chunks} - translated {len(translations)}/{len(unique)} ({len(translations)/len(unique)*100:.1f}%)")
            
            result = translate_chunk(chunk, translator)
            translations.update(result)
            
            # Сохраняем после КАЖДОГО чанка (надёжность)
            save_progress(translations)
            
            # Минимальная задержка
            time.sleep(0.02)
        
        save_progress(translations)
        log(f"\nTotal translations: {len(translations)}")
    
    # Загружаем финальные переводы
    translations = load_progress()
    
    # Патчим файл
    log("\n=== Patching metadata file ===")
    patched = 0
    truncated = 0
    skipped = 0
    
    for text, ru in translations.items():
        if text == ru or not ru:
            skipped += 1
            continue
        
        original_bytes = text.encode('utf-8')
        offsets = unique.get(text, [])
        
        for offset in offsets:
            target_len = len(original_bytes)
            new_bytes = fit_russian_to_length(ru, target_len)
            
            if len(new_bytes) != target_len:
                continue
            
            data[offset:offset+target_len] = new_bytes
            patched += 1
            
            if len(ru.encode('utf-8')) > target_len:
                truncated += 1
    
    log(f"Patched: {patched}")
    log(f"Truncated: {truncated}")
    log(f"Skipped (no translation): {skipped}")
    
    output_path = OUTPUT_DIR / "global-metadata.dat"
    with open(output_path, 'wb') as f:
        f.write(data)
    log(f"\n✅ Saved: {output_path}")
    
    # Отчёт
    report = {
        "total_strings_found": len(all_strings),
        "unique_texts": len(unique),
        "translated": len(translations),
        "patched_occurrences": patched,
        "truncated": truncated,
        "skipped": skipped,
    }
    with open(OUTPUT_DIR / "patch_report.json", 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2)
    log(f"Report: {report}")

if __name__ == '__main__':
    main()
