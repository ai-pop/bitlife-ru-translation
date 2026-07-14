"""
Перевод res/values/strings.xml → res/values-ru/strings.xml
Это UI-тексты (кнопки, меню, настройки) - 706 строк.
Создаём новую папку res/values-ru/ с русской локализацией.
"""
import re
import json
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from deep_translator import GoogleTranslator

STRINGS_XML = "/home/z/my-project/work/bitlife/bitlife_decoded/res/values/strings.xml"
OUTPUT_DIR = Path("/home/z/my-project/work/bitlife/bitlife_decoded/res/values-ru")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_FILE = OUTPUT_DIR / "strings.xml"

# Список строк, которые НЕ нужно переводить (системные ключи)
KEEP_AS_IS_PATTERNS = [
    r'^[A-Z][A-Z_]+$',  # CONSTANT_NAMES
    r'^[a-z]+\.[a-zA-Z]',  # com.xxx / package names
    r'^[a-fA-F0-9]{8,}$',  # hex
    r'^\d+$',  # numbers only
    r'^#[0-9a-fA-F]+$',  # colors
    r'^true$|^false$',  # booleans
]

def should_translate(text):
    """Решаем, нужно ли переводить эту строку"""
    if not text or len(text) < 2:
        return False
    # Если только цифры/спецсимволы - не переводим
    if not re.search(r'[a-zA-Z]{3,}', text):
        return False
    # Системные паттерны
    for pat in KEEP_AS_IS_PATTERNS:
        if re.match(pat, text.strip()):
            return False
    return True

def translate_text_safe(text, translator):
    """Безопасный перевод с обработкой ошибок"""
    if not should_translate(text):
        return text
    try:
        # Защита от слишком длинных
        if len(text) > 4500:
            text = text[:4500]
        ru = translator.translate(text)
        return ru if ru else text
    except Exception as e:
        print(f"  Error translating: {e}")
        return text

def main():
    print(f"Reading: {STRINGS_XML}")
    
    # Читаем как текст, чтобы сохранить форматирование
    with open(STRINGS_XML, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Парсим через regex, чтобы сохранить оригинальное форматирование
    # Pattern: <string name="NAME">VALUE</string>
    pattern = re.compile(r'(<string name="[^"]+">)(.*?)(</string>)', re.DOTALL)
    
    translator = GoogleTranslator(source='en', target='ru')
    
    matches = list(pattern.finditer(content))
    print(f"Found {len(matches)} <string> entries")
    
    translated_count = 0
    skipped_count = 0
    translations_cache = {}  # кэш для одинаковых текстов
    
    # Создаём новый контент
    new_content = content
    
    # Идём с конца, чтобы не сбить offset'ы
    for m in reversed(matches):
        open_tag, text, close_tag = m.group(1), m.group(2), m.group(3)
        
        # Декодируем XML entities
        original_text = text
        decode_text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&quot;', '"').replace('&apos;', "'")
        
        if not should_translate(decode_text):
            skipped_count += 1
            continue
        
        # Кэш
        if decode_text in translations_cache:
            ru_decoded = translations_cache[decode_text]
        else:
            ru_decoded = translate_text_safe(decode_text, translator)
            translations_cache[decode_text] = ru_decoded
            time.sleep(0.05)  # rate limit
        
        # Кодируем обратно в XML entities
        ru_encoded = ru_decoded.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        # Кавычки внутри значений атрибутов не трогаем - они внутри текста
        
        # Заменяем в контенте
        new_text = ru_encoded
        old_full = open_tag + text + close_tag
        new_full = open_tag + new_text + close_tag
        
        # Находим позицию в новом контенте
        pos = new_content.find(old_full)
        if pos != -1:
            new_content = new_content[:pos] + new_full + new_content[pos+len(old_full):]
            translated_count += 1
            if translated_count % 20 == 0:
                print(f"  Translated {translated_count}/{len(matches)}", flush=True)
    
    # Сохраняем
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write(new_content)
    
    print(f"\n✅ Saved: {OUTPUT_FILE}")
    print(f"Translated: {translated_count}")
    print(f"Skipped (system/empty): {skipped_count}")
    
    # Сохраняем кэш переводов
    with open("/home/z/my-project/work/bitlife/translation/strings_xml_cache.json", 'w', encoding='utf-8') as f:
        json.dump(translations_cache, f, ensure_ascii=False, indent=1)

if __name__ == '__main__':
    main()
