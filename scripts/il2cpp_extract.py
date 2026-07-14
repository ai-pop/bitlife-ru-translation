"""
Парсер global-metadata.dat для IL2CPP (BitLife)
Извлекает все строковые литералы в JSON для перевода.
"""
import struct
import json
import sys
from pathlib import Path

# Структура заголовка il2cpp_global_metadata_header (Unity 6000.x / IL2CPP v29)
# Формат: int32 sanity + int32 version + int32*2 для каждого раздела (offset, size)
EXPECTED_SECTIONS = [
    "stringLiteral", "stringLiteralData", "string", "events", "properties",
    "methods", "parameterDefaultValues", "fieldDefaultValues", "fieldAndParameterDefaultValueData",
    "fieldMarshaledSizes", "parameters", "fields", "genericParameters",
    "genericParameterConstraints", "genericContainers", "nestedTypes",
    "interfaces", "vtableMethods", "interfaceOffsets", "typeDefinitions",
    "images", "assemblies", "fieldRefs", "referencedAssemblies",
    "attributeDataRange", "attributeData", "unresolvedVirtualCallParameterTypes",
    "unresolvedVirtualCallParameterRanges", "windowsRuntimeTypeNames",
    "windowsRuntimeStrings", "exportedTypeDefinitions"
]

# Магия IL2CPP metadata
IL2CPP_METADATA_MAGIC = 0xFAB11BAF

def read_int32(data, offset):
    return struct.unpack_from('<i', data, offset)[0]

def read_uint32(data, offset):
    return struct.unpack_from('<I', data, offset)[0]

def parse_header(data):
    """Парсит заголовок global-metadata.dat"""
    magic = read_uint32(data, 0)
    if magic != IL2CPP_METADATA_MAGIC:
        raise ValueError(f"Bad magic: 0x{magic:08X}, expected 0x{IL2CPP_METADATA_MAGIC:08X}")
    
    version = read_int32(data, 4)
    print(f"IL2CPP metadata version: {version}")
    
    # После magic+version идут пары (offset, size) для каждого раздела
    sections = {}
    offset = 8
    for name in EXPECTED_SECTIONS:
        sec_offset = read_int32(data, offset)
        sec_size = read_int32(data, offset + 4)
        sections[name] = (sec_offset, sec_size)
        offset += 8
    
    return version, sections

def extract_string_literals(data, sections):
    """
    Извлекает строковые литералы (это то, что нас интересует - тексты игры).
    Формат: 
      - stringLiteral: массив Il2CppStringLiteral {uint32 length; uint32 dataIndex}
      - stringLiteralData: blob с UTF-8 строками, индексируется через dataIndex
    """
    sl_offset, sl_size = sections["stringLiteral"]
    sld_offset, sld_size = sections["stringLiteralData"]
    
    # Il2CppStringLiteral: 8 байт (length + dataIndex)
    LITERAL_STRUCT_SIZE = 8
    num_literals = sl_size // LITERAL_STRUCT_SIZE
    print(f"Total string literals: {num_literals}")
    
    literals = []
    for i in range(num_literals):
        entry_offset = sl_offset + i * LITERAL_STRUCT_SIZE
        length = read_uint32(data, entry_offset)
        data_index = read_uint32(data, entry_offset + 4)
        
        if length == 0:
            literals.append({"index": i, "text": "", "length": 0, "data_index": data_index})
            continue
        
        if data_index + length > sld_offset + sld_size:
            # За пределами секции - битая или的特殊ная
            literals.append({"index": i, "text": "", "length": length, "data_index": data_index, "error": "out_of_bounds"})
            continue
        
        try:
            raw = data[data_index:data_index + length]
            text = raw.decode('utf-8', errors='replace')
            literals.append({"index": i, "text": text, "length": length, "data_index": data_index})
        except Exception as e:
            literals.append({"index": i, "text": "", "length": length, "data_index": data_index, "error": str(e)})
    
    return literals

def extract_strings(data, sections):
    """
    Извлекает обычные строки (имена типов, методов и т.д.)
    Формат: null-terminated UTF-8 строки в одной большой секции.
    """
    s_offset, s_size = sections["string"]
    raw = data[s_offset:s_offset + s_size]
    
    strings = []
    pos = 0
    idx = 0
    while pos < len(raw):
        end = raw.find(b'\x00', pos)
        if end == -1:
            end = len(raw)
        text = raw[pos:end].decode('utf-8', errors='replace')
        strings.append({"index": idx, "offset": pos, "text": text})
        pos = end + 1
        idx += 1
    
    return strings

def is_game_text(s):
    """Фильтр: является ли строка игровым текстом (а не системным)"""
    if not s or len(s) < 3:
        return False
    # Системные строки IL2CPP / .NET
    sys_markers = [
        '<', '>', 'System.', 'UnityEngine.', 'Mono.', 'Google.',
        'Amazon.', 'AWS.', 'Android.Runtime', 'Java.Lang',
        'Il2Cpp', 'akihabara', 'Metadata', 'MethodImpl',
        '{0}', '{1}', '{2}',  # форматы - оставляем, это игровые
    ]
    # Игровые строки обычно содержат буквы, пробелы, знаки препинания
    # и не начинаются с заглавной системной буквы
    return True  # Сохраняем все, фильтровать будем позже

def main():
    metadata_path = sys.argv[1] if len(sys.argv) > 1 else \
        "/home/z/my-project/work/bitlife/bitlife_decoded/assets/bin/Data/Managed/Metadata/global-metadata.dat"
    
    output_dir = Path("/home/z/my-project/work/bitlife/strings_extracted")
    output_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"Reading: {metadata_path}")
    with open(metadata_path, 'rb') as f:
        data = f.read()
    print(f"Size: {len(data):,} bytes")
    
    version, sections = parse_header(data)
    print(f"\nSections:")
    for name, (off, size) in sections.items():
        if size > 0:
            print(f"  {name}: offset=0x{off:08X}, size={size:,}")
    
    # Извлекаем строковые литералы (это игровые тексты)
    print("\n=== Extracting string literals (game texts) ===")
    literals = extract_string_literals(data, sections)
    
    # Статистика
    non_empty = [l for l in literals if l['text']]
    print(f"Non-empty literals: {len(non_empty)}")
    
    # Сохраняем все строковые литералы
    with open(output_dir / "string_literals_all.json", 'w', encoding='utf-8') as f:
        json.dump(literals, f, ensure_ascii=False, indent=1)
    print(f"Saved: {output_dir / 'string_literals_all.json'}")
    
    # Сохраняем только непустые
    with open(output_dir / "string_literals_non_empty.json", 'w', encoding='utf-8') as f:
        json.dump(non_empty, f, ensure_ascii=False, indent=1)
    print(f"Saved: {output_dir / 'string_literals_non_empty.json'}")
    
    # Сохраняем как простой текст (для удобного перевода)
    with open(output_dir / "string_literals.txt", 'w', encoding='utf-8') as f:
        for l in non_empty:
            # Экранируем переносы для текстового формата
            safe = l['text'].replace('\n', '\\n').replace('\r', '\\r')
            f.write(f"{l['index']}\t{safe}\n")
    print(f"Saved: {output_dir / 'string_literals.txt'}")
    
    # Извлекаем обычные строки (имена типов и т.д.)
    print("\n=== Extracting string table (type/method names) ===")
    strings = extract_strings(data, sections)
    print(f"Total type strings: {len(strings)}")
    
    with open(output_dir / "string_table.json", 'w', encoding='utf-8') as f:
        json.dump(strings, f, ensure_ascii=False, indent=1)
    
    # Сохраняем header info для последующей пересборки
    header_info = {
        "magic": IL2CPP_METADATA_MAGIC,
        "version": version,
        "sections": {k: list(v) for k, v in sections.items()},
        "total_literals": len(literals),
        "non_empty_literals": len(non_empty),
        "total_strings": len(strings),
    }
    with open(output_dir / "header_info.json", 'w', encoding='utf-8') as f:
        json.dump(header_info, f, indent=2)
    print(f"\nSaved: {output_dir / 'header_info.json'}")
    
    print(f"\n✅ Done. Files in: {output_dir}/")
    
    # Превью нескольких строк
    print("\n=== Preview (first 10 game strings) ===")
    for l in non_empty[:10]:
        text = l['text'][:100].replace('\n', ' ')
        print(f"  [{l['index']:5}] len={l['length']:5}: {text}")

if __name__ == '__main__':
    main()
