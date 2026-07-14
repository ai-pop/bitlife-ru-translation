"""
Улучшенный переводчик metadata с resume поддержкой.
- Сохраняет прогресс каждые 100 строк
- Может возобновить с места остановки
- Tighter фильтр (только предложения с пробелами)
- Запускается через setsid для надёжности
"""
import json
import re
import os
import sys
import time
from pathlib import Path
from deep_translator import GoogleTranslator
from deep_translator.exceptions import RequestError

METADATA_PATH = "/home/z/my-project/work/bitlife/bitlife_decoded/assets/bin/Data/Managed/Metadata/global-metadata.dat"
OUTPUT_DIR = Path("/home/z/my-project/work/bitlife/translation")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
PROGRESS_FILE = OUTPUT_DIR / "translations_progress.json"
LOG_FILE = Path("/home/z/my-project/work/bitlife/logs/translation.log")
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

def log(msg):
    print(msg, flush=True)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(msg + '\n')

def is_game_sentence(text):
    """Только настоящие игровые предложения"""
    if not text or len(text) < 5:
        return False
    
    # Должен быть пробел и буквы
    if ' ' not in text:
        return False
    if not re.search(r'[a-zA-Z]{3,}', text):
        return False
    
    # Исключаем идентификаторы
    if re.match(r'^[a-z]+(\.[a-zA-Z_][a-zA-Z0-9_]*)+$', text):
        return False
    if re.match(r'^[A-Z][a-zA-Z0-9_]*$', text) and ' ' not in text:
        return False
    
    # Исключаем URL/пути
    if '://' in text:
        return False
    
    # Хотя бы 30% букв
    alpha_count = sum(1 for c in text if c.isalpha())
    if alpha_count / len(text) < 0.3:
        return False
    
    return True

def scan_strings(data):
    """Сканируем файл на игровые предложения"""
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
    
    # Дедупликация
    unique = {}
    for s in strings:
        if s['text'] not in unique:
            unique[s['text']] = []
        unique[s['text']].append(s['offset'])
    
    return strings, unique

def load_progress():
    """Загружаем прогресс переводов"""
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_progress(translations):
    """Сохраняем прогресс"""
    with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
        json.dump(translations, f, ensure_ascii=False)

def translate_batch(texts, translator, existing_translations):
    """Переводим с использованием существующих переводов"""
    translations = dict(existing_translations)
    total = len(texts)
    
    # Фильтруем уже переведённые
    to_translate = [t for t in texts if t not in translations]
    log(f"Already translated: {len(translations)}, to translate: {len(to_translate)}")
    
    for i, text in enumerate(to_translate):
        # Прогресс каждые 50 строк
        if i % 10 == 0:
            log(f"  Progress: {i}/{len(to_translate)} ({(i/len(to_translate))*100:.1f}%)")
        
        # Обрезаем длинные
        t = text[:4500]
        
        retries = 3
        while retries > 0:
            try:
                ru = translator.translate(t)
                if ru:
                    translations[text] = ru
                else:
                    translations[text] = text
                break
            except RequestError as e:
                err = str(e)
                if 'Too Many Requests' in err or '429' in err:
                    log(f"    Rate limited, sleeping 15s...")
                    time.sleep(15)
                    retries -= 1
                else:
                    log(f"    Error: {e}")
                    retries -= 1
                    time.sleep(2)
            except Exception as e:
                log(f"    Failed: {e}")
                translations[text] = text
                break
        
        # Сохраняем каждые 100 строк
        if (i + 1) % 20 == 0:
            save_progress(translations)
        
        # Минимальная задержка
        time.sleep(0.01)
    
    # Финальное сохранение
    save_progress(translations)
    return translations

def fit_russian_to_length(russian_text, target_byte_length):
    """Вписываем русский перевод в target_byte_length байт"""
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
            i += 1  # skip orphan continuation
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
    log("Starting metadata translation (with resume support)")
    log("="*60)
    
    log(f"Reading: {METADATA_PATH}")
    with open(METADATA_PATH, 'rb') as f:
        data = bytearray(f.read())
    log(f"File size: {len(data):,} bytes")
    
    log("\n=== Scanning for game sentences ===")
    all_strings, unique = scan_strings(data)
    log(f"Total matches: {len(all_strings)}")
    log(f"Unique texts: {len(unique)}")
    
    # Загружаем прогресс
    existing = load_progress()
    log(f"Existing translations: {len(existing)}")
    
    # Переводим
    log("\n=== Translating ===")
    translator = GoogleTranslator(source='en', target='ru')
    translations = translate_batch(list(unique.keys()), translator, existing)
    log(f"Total translations: {len(translations)}")
    
    # Патчим файл
    log("\n=== Patching metadata file ===")
    patched = 0
    truncated = 0
    
    for text, ru in translations.items():
        if text == ru:
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
    
    log(f"Patched: {patched} occurrences")
    log(f"Truncated: {truncated}")
    
    output_path = OUTPUT_DIR / "global-metadata.dat"
    with open(output_path, 'wb') as f:
        f.write(data)
    log(f"\n✅ Saved: {output_path}")

if __name__ == '__main__':
    main()
