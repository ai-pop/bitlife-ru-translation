"""
Параллельный переводчик metadata через threading.
5 потоков одновременно = 5x ускорение.
Каждая строка переводится отдельно (без batch concat проблем).
"""
import json
import re
import sys
import time
from pathlib import Path
from deep_translator import GoogleTranslator
from deep_translator.exceptions import RequestError
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

METADATA_PATH = "/home/z/my-project/work/bitlife/bitlife_decoded/assets/bin/Data/Managed/Metadata/global-metadata.dat"
OUTPUT_DIR = Path("/home/z/my-project/work/bitlife/translation")
PROGRESS_FILE = OUTPUT_DIR / "translations_progress.json"
LOG_FILE = Path("/home/z/my-project/work/bitlife/logs/translation.log")
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

# Lock для thread-safe записи
log_lock = threading.Lock()
save_lock = threading.Lock()

def log(msg):
    with log_lock:
        print(msg, flush=True)
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(msg + '\n')

# Thread-local storage для translator
_local = threading.local()

def get_translator():
    if not hasattr(_local, 'translator'):
        _local.translator = GoogleTranslator(source='en', target='ru')
    return _local.translator

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
    with save_lock:
        # Прямая запись без tmp (атомарность не критична - locks защищают)
        with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
            json.dump(translations, f, ensure_ascii=False)

def translate_one(text, retries=3):
    """Переводим одну строку с retry"""
    translator = get_translator()
    t = text[:4500]
    
    for attempt in range(retries):
        try:
            ru = translator.translate(t)
            if ru:
                return ru
            return text  # fallback
        except RequestError as e:
            err = str(e)
            if 'Too Many Requests' in err or '429' in err:
                time.sleep(5 * (attempt + 1))  # exponential backoff
            else:
                time.sleep(1)
        except Exception:
            time.sleep(0.5)
    return text  # fallback

def fit_russian_to_length(russian_text, target_byte_length):
    if not russian_text:
        return b'\x00' * target_byte_length
    
    ru_bytes = russian_text.encode('utf-8')
    
    if len(ru_bytes) == target_byte_length:
        return ru_bytes
    
    if len(ru_bytes) < target_byte_length:
        return ru_bytes + b'\x00' * (target_byte_length - len(ru_bytes))
    
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
    log("Starting PARALLEL metadata translation (5 threads)")
    log("="*60)
    
    with open(METADATA_PATH, 'rb') as f:
        data = bytearray(f.read())
    log(f"File size: {len(data):,} bytes")
    
    all_strings, unique = scan_strings(data)
    log(f"Total matches: {len(all_strings)}, unique: {len(unique)}")
    
    existing = load_progress()
    log(f"Existing translations: {len(existing)}")
    
    to_translate = [t for t in unique.keys() if t not in existing]
    log(f"To translate: {len(to_translate)}")
    
    if to_translate:
        translations = dict(existing)
        total = len(to_translate)
        completed = 0
        save_counter = 0
        
        # 5 потоков параллельно
        with ThreadPoolExecutor(max_workers=5) as executor:
            # Submit по 50 за раз чтобы видеть прогресс
            batch_size = 50
            for batch_start in range(0, total, batch_size):
                batch = to_translate[batch_start:batch_start+batch_size]
                futures = {executor.submit(translate_one, t): t for t in batch}
                
                for future in as_completed(futures):
                    text = futures[future]
                    try:
                        ru = future.result()
                        translations[text] = ru
                    except Exception as e:
                        log(f"  Error: {e}")
                        translations[text] = text
                    completed += 1
                    save_counter += 1
                
                # Сохраняем каждые 50 строк
                if save_counter >= 50:
                    save_progress(translations)
                    save_counter = 0
                    log(f"  Progress: {completed}/{total} ({completed/total*100:.1f}%) - total translated: {len(translations)}")
        
        save_progress(translations)
        log(f"\nTotal translations: {len(translations)}")
    
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
    log(f"Skipped: {skipped}")
    
    output_path = OUTPUT_DIR / "global-metadata.dat"
    with open(output_path, 'wb') as f:
        f.write(data)
    log(f"\n✅ Saved: {output_path}")

if __name__ == '__main__':
    main()
