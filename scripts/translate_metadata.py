"""
Умный переводчик global-metadata.dat для BitLife.

Стратегия:
1. Сканируем файл на английские строки (>=3 chars)
2. Фильтруем: оставляем только игровой текст, исключаем системный
3. Переводим через Google Translate (batch + rate limit)
4. In-place замена: Russian должен поместиться в ту же длину (в байтах)
   - Если Russian короче → дополняем null-байтами (строка обрежется)
   - Если Russian длиннее → обрезаем по границе UTF-8
5. Сохраняем пропатченный файл

Это безопасно: индексная таблица остается нетронутой,
длины строк остаются теми же, изменяется только содержимое.
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

# === Фильтры ===

# Системные паттерны, которые НЕ переводим (они сломают игру)
SYSTEM_PATTERNS = [
    # Типы и пространства имён .NET/Unity
    r'^[a-z]+(\.[a-zA-Z_][a-zA-Z0-9_]*)+$',  # com.xxx.yyy / System.XXX
    r'^[A-Z][a-zA-Z0-9_]*Exception$',
    r'^UnityEngine\.',
    r'^System\.',
    r'^Google\.',
    r'^Amazon\.',
    r'^AWS\.',
    r'^Java\.',
    r'^Android\.',
    r'^Il2Cpp',
    r'^Mono\.',
    r'^AWSSDK\.',
    r'^Castle\.',
    r'^Coffee\.',
    r'^DynamicExpresso\.',
    # Специальные токены
    r'^\{[0-9]+\}$',                # {0}, {1} - placeholders
    r'^<[a-z][-a-z/]*>$',           # <color>, </b> - но не все
    r'^[a-z][a-zA-Z0-9_]*$',        # single word identifiers (camelCase)
    r'^[A-Z][A-Z_]+$',              # UPPER_CASE constants
    r'^[a-z]+$',                    # lowercase only
    # Файлы и пути
    r'\.(dll|exe|so|json|xml|txt|png|jpg|cs|js)$',
    r'^[/\\]',                      # paths
    r'^[a-z]+://',                  # URLs
    # Технические строки
    r'^[0-9.,:\-/ ]+$',             # numbers/dates
    r'^[a-fA-F0-9]{8,}$',           # hex strings (hashes)
    r'^\w+@\w+\.\w+',               # emails
]

# Паттерны, которые ТОЧНО нужно перевести (игровой контент BitLife)
GAME_KEYWORDS = [
    'born', 'died', 'school', 'job', 'career', 'married', 'divorce',
    'baby', 'pregnant', 'money', 'family', 'friend', 'father', 'mother',
    'will you', 'decided', 'agreed', 'refused', 'asked', 'told', 'informed',
    'you are', 'you have', 'you can', 'your', 'player', 'character',
    'job', 'work', 'salary', 'pay', 'buy', 'sell', 'earn',
    'school', 'university', 'college', 'graduate', 'study',
    'crime', 'police', 'arrest', 'prison', 'jail', 'court',
    'health', 'happiness', 'smarts', 'looks',
    'year old', 'age', 'life',
    'love', 'relationship', 'date', 'kiss', 'sex',
    'drink', 'drug', 'smoke', 'gamble',
    'car', 'house', 'pet', 'animal',
    'died', 'death', 'funeral', 'killed',
]

def is_likely_game_text(text):
    """Возвращает True если это похоже на игровой текст BitLife"""
    if not text or len(text) < 4:
        return False
    
    # Проверка на системные паттерны
    for pat in SYSTEM_PATTERNS:
        if re.match(pat, text):
            return False
    
    # Должен содержать хотя бы одну букву
    if not re.search(r'[a-zA-Z]', text):
        return False
    
    # Должен содержать пробел (нормальные предложения) ИЛИ быть длинным
    has_space = ' ' in text.strip()
    is_long = len(text) >= 15
    
    if not (has_space or is_long):
        return False
    
    # Слишком много спецсимволов - не переводим
    alpha_count = sum(1 for c in text if c.isalpha())
    if alpha_count / len(text) < 0.5:
        return False
    
    # Проверка на типичные системные строки
    if text.startswith('<') and text.endswith('>') and len(text) < 50:
        # XML-теги - пропускаем, но не все (некоторые содержат переводы)
        if not any(kw in text.lower() for kw in GAME_KEYWORDS):
            return False
    
    return True

def scan_strings(data):
    """Сканируем файл на наличие английских printable строк >=4 символов"""
    strings = []
    # Pattern: sequences of printable ASCII chars (>=4)
    pattern = re.compile(rb'[\x20-\x7e]{4,}')
    
    for m in pattern.finditer(data):
        text = m.group().decode('ascii')
        if is_likely_game_text(text):
            strings.append({
                "offset": m.start(),
                "length": len(m.group()),
                "text": text,
                "end": m.end()
            })
    
    # Дедупликация по тексту (сохраняем все offset'ы)
    unique_texts = {}
    for s in strings:
        if s['text'] not in unique_texts:
            unique_texts[s['text']] = []
        unique_texts[s['text']].append(s['offset'])
    
    return strings, unique_texts

def translate_batch(texts, translator, batch_size=50):
    """Переводим список текстов батчами с rate limiting"""
    translations = {}
    total = len(texts)
    
    for i in range(0, total, batch_size):
        batch = texts[i:i+batch_size]
        if (i // batch_size) % 10 == 0:
            print(f"  Progress: {i+1}/{total} ({(i/total)*100:.1f}%)", flush=True)
        
        for text in batch:
            retries = 3
            while retries > 0:
                try:
                    # Обрезаем слишком длинные (Google limit ~5000 chars)
                    t = text[:4500]
                    ru = translator.translate(t)
                    if ru:
                        translations[text] = ru
                    else:
                        translations[text] = text  # fallback
                    break
                except RequestError as e:
                    if 'Too Many Requests' in str(e) or '429' in str(e):
                        print(f"    Rate limited, sleeping 10s...")
                        time.sleep(10)
                        retries -= 1
                    else:
                        print(f"    Error: {e}, retrying...")
                        retries -= 1
                        time.sleep(2)
                except Exception as e:
                    print(f"    Failed: {e}")
                    translations[text] = text  # fallback
                    break
        
        # Минимальная задержка - Google Translate через deep-translator идёт через web endpoint
        time.sleep(0.05)
    
    return translations

def fit_russian_to_length(russian_text, target_byte_length):
    """
    Вписываем русский перевод в target_byte_length байт.
    Возвращает bytes готовые к записи.
    """
    if not russian_text:
        return b'\x00' * target_byte_length
    
    ru_bytes = russian_text.encode('utf-8')
    
    if len(ru_bytes) == target_byte_length:
        return ru_bytes
    
    if len(ru_bytes) < target_byte_length:
        # Дополняем null-байтами (строка обрежется при отображении на null)
        return ru_bytes + b'\x00' * (target_byte_length - len(ru_bytes))
    
    # Длиннее - обрезаем по границе UTF-8
    result = b''
    i = 0
    while i < len(ru_bytes) and len(result) < target_byte_length:
        b = ru_bytes[i]
        if b < 0x80:
            # ASCII char - 1 byte
            if len(result) + 1 <= target_byte_length:
                result += bytes([b])
                i += 1
            else:
                break
        elif b < 0xC0:
            # continuation byte - shouldn't start here
            i += 1
        elif b < 0xE0:
            # 2-byte char
            if len(result) + 2 <= target_byte_length and i + 1 < len(ru_bytes):
                result += ru_bytes[i:i+2]
                i += 2
            else:
                break
        elif b < 0xF0:
            # 3-byte char
            if len(result) + 3 <= target_byte_length and i + 2 < len(ru_bytes):
                result += ru_bytes[i:i+3]
                i += 3
            else:
                break
        else:
            # 4-byte char
            if len(result) + 4 <= target_byte_length and i + 3 < len(ru_bytes):
                result += ru_bytes[i:i+4]
                i += 4
            else:
                break
    
    # Дополняем null-байтами если осталось место
    if len(result) < target_byte_length:
        result += b'\x00' * (target_byte_length - len(result))
    
    return result

def main():
    # Лимит для теста (поставь None для полного перевода)
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    
    print(f"Reading: {METADATA_PATH}")
    with open(METADATA_PATH, 'rb') as f:
        data = bytearray(f.read())
    print(f"File size: {len(data):,} bytes")
    
    # 1. Сканируем строки
    print("\n=== Scanning for English strings ===")
    all_strings, unique_texts = scan_strings(data)
    print(f"Total matches: {len(all_strings)}")
    print(f"Unique texts: {len(unique_texts)}")
    
    # 2. Берём лимит для теста
    texts_to_translate = list(unique_texts.keys())
    if limit:
        texts_to_translate = texts_to_translate[:limit]
    print(f"Will translate: {len(texts_to_translate)} unique texts")
    
    # 3. Переводим
    print("\n=== Translating ===")
    translator = GoogleTranslator(source='en', target='ru')
    translations = translate_batch(texts_to_translate, translator)
    
    # Сохраняем словарь переводов
    with open(OUTPUT_DIR / "translations.json", 'w', encoding='utf-8') as f:
        json.dump(translations, f, ensure_ascii=False, indent=1)
    print(f"Saved translations: {OUTPUT_DIR / 'translations.json'}")
    
    # 4. In-place замена в файле
    print("\n=== Patching metadata file ===")
    patched_count = 0
    truncated_count = 0
    
    for text, ru_translation in translations.items():
        if text == ru_translation:
            continue  # не было перевода
        
        original_bytes = text.encode('utf-8')
        # Заменяем все вхождения этого текста
        offsets = unique_texts[text]
        
        for offset in offsets:
            target_len = len(original_bytes)
            new_bytes = fit_russian_to_length(ru_translation, target_len)
            
            if len(new_bytes) != target_len:
                print(f"  WARNING: length mismatch at {offset}")
                continue
            
            data[offset:offset+target_len] = new_bytes
            patched_count += 1
            
            if len(ru_translation.encode('utf-8')) > target_len:
                truncated_count += 1
    
    print(f"Patched {patched_count} occurrences")
    print(f"Truncated (Russian was longer): {truncated_count}")
    
    # 5. Сохраняем пропатченный файл
    output_path = OUTPUT_DIR / "global-metadata.dat"
    with open(output_path, 'wb') as f:
        f.write(data)
    print(f"\n✅ Saved: {output_path}")
    
    # 6. Отчёт
    with open(OUTPUT_DIR / "patch_report.json", 'w', encoding='utf-8') as f:
        json.dump({
            "total_strings_found": len(all_strings),
            "unique_texts": len(unique_texts),
            "translated": len(translations),
            "patched_occurrences": patched_count,
            "truncated": truncated_count,
        }, f, indent=2)

if __name__ == '__main__':
    main()
