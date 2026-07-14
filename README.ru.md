# BitLife RU — Перевод

**[English](README.md)** | **[Русский](README.ru.md)**

---

## Описание

Автоматизированный пайплайн для перевода BitLife (Android, v3.24.3) с английского на русский. Игра построена на Unity 6000.3.9f1 с бэкендом IL2CPP — все строковые литерали C# хранятся в `global-metadata.dat` (бинарный формат), а не в `Assembly-CSharp.dll`.

Проект предоставляет:

- Парсер формата IL2CPP v39 на Python
- Параллельный переводчик (5 потоков, Google Translate API)
- In-place патчер байтов с сохранением бинарной структуры
- Переводчик Android-ресурсов (`strings.xml`)
- Пайплайн пересборки и подписи APK

## Техническая справка

### Формат IL2CPP Metadata (v39)

Unity IL2CPP компилирует C# в C++ и хранит метаданные в `global-metadata.dat`. Файл начинается с заголовка:

```
0x00  uint32  magic           (0xFAB11BAF)
0x04  int32   version         (39 для Unity 6.x)
0x08  пары (int32 offset, int32 size) для каждой секции
```

Релевантные секции:

| Секция | Назначение | Размер в BitLife |
|---------|---------|-----------------|
| `stringLiteral` | Массив записей `{uint32 length; uint32 dataIndex}` | 280,624 Б (35,078 записей) |
| `stringLiteralData` | Сырой UTF-8 блок, индексируется через `dataIndex` | 281,004 Б |
| `string` | Null-terminated имена типов и методов | 70,155 Б |

Блок `stringLiteralData` содержит все строковые литерали C#, склеенные вместе. Таблица `stringLiteral` ставит в соответствие каждому литералу пару `(length, offset)` внутри этого блока. Игровой текст (описания событий, диалоги) находится именно здесь.

### Почему in-place патчинг

Стандартный подход — распарсить metadata, перевести, пересобрать metadata, упаковать обратно. Это не работает по двум причинам:

1. Значения `dataIndex` в таблице `stringLiteral` — абсолютные смещения внутри секции `stringLiteralData`
2. Русский текст обычно в 1.3-1.5 раза длиннее английского в UTF-8 байтах (кириллица = 2 байта на символ против ASCII = 1 байт)
3. Пересборка требует переписывания всех смещений в таблице, что чревато ошибками

**Решение**: in-place замена байтов. Английские байты перезаписываются русскими, при необходимости дополняются null-ами (если русский короче) или обрезаются по границе UTF-8 символа (если длиннее). Таблица `stringLiteral` не трогается. Игра читает строку до первого null-байта (стандартное поведение C-строк), поэтому дополнение null-ами невидимо.

### Почему часть строк обрезана

Когда `len(russian_bytes) > len(english_bytes)`, русский текст обрезается по ближайшей валидной границе UTF-8 символа, чтобы уложиться в исходную длину в байтах. Это приводит к тому, что ~50% длинных описаний событий обрываются на середине предложения.

Для корректного исправления нужно:

1. Переписать таблицу `stringLiteral` с новыми смещениями
2. Пересобрать секцию `stringLiteralData` с новой разметкой
3. Обновить размер секции в заголовке

Реализуемо, но требует аккуратной работы с бинарным форматом. Оставлено как будущая работа.

## Структура проекта

```
bitlife-ru-translation/
├── scripts/
│   ├── il2cpp_extract.py             # Парсер metadata, выгрузка строк в JSON
│   ├── translate_metadata.py         # v1: однопоточный переводчик (медленно)
│   ├── translate_metadata_v2.py      # v2: с поддержкой resume
│   ├── translate_metadata_batch.py   # v3: пакетный перевод через ||| разделитель
│   ├── translate_metadata_parallel.py# v4: 5 потоков параллельно (финальный, самый быстрый)
│   ├── translate_strings_xml.py      # Переводчик Android-ресурсов
│   └── github_upload.py              # Загрузчик через GitHub API (использует env vars)
├── docs/
│   └── patch_report.json             # Финальная статистика
├── README.md                         # Английская документация
├── README.ru.md                      # Этот файл (русский)
├── LICENSE                           # MIT
└── .gitignore
```

## Пайплайн

### Шаг 1. Декодирование APK

```bash
java -jar apktool.jar d bitlife.apk -o bitlife_decoded
```

В выходной директории: `AndroidManifest.xml`, `res/`, `assets/`, `lib/`, и директории `smali_*` (по одной на каждый `.dex`).

### Шаг 2. Локализация metadata

```
bitlife_decoded/
└── assets/bin/Data/
    ├── Managed/
    │   └── Metadata/
    │       └── global-metadata.dat    # ← Целевой файл (18.5 МБ)
    ├── data.unity3d                   # Asset bundle (не патчится)
    ├── datapack.unity3d               # Asset bundle (не патчится)
    └── sharedassets0.resource         # Сценные ассеты (не патчатся)
└── lib/arm64-v8a/
    └── libil2cpp.so                   # Нативный код (101 МБ, не патчится)
```

### Шаг 3. Извлечение строк

```bash
python scripts/il2cpp_extract.py
```

Выходные JSON-файлы в `work/bitlife/strings_extracted/`:
- `string_literals_all.json` — все 35,078 записей
- `string_literals_non_empty.json` — непустые записи
- `header_info.json` — смещения и размеры секций

### Шаг 4. Перевод metadata

```bash
python scripts/translate_metadata_parallel.py
```

Алгоритм переводчика:

1. Сканирование `global-metadata.dat` на ASCII-printable последовательности ≥5 символов
2. Фильтрация: должны быть пробелы и буквенные символы (исключает идентификаторы, пути, hex-строки)
3. Дедупликация по тексту (сохраняются все смещения)
4. Параллельный перевод уникальных строк (5 потоков, каждый со своим экземпляром `GoogleTranslator`)
5. Сохранение прогресса в `translation/translations_progress.json` каждые 50 строк (поддержка resume)
6. In-place патчинг бинарника

**Скорость**: ~23 мс на строку при 5 потоках. Полный перевод 8,111 строк занимает ~3-4 минуты (без учёта rate-limit пауз).

**Resume**: При прерывании повторный запуск подхватывает прогресс из `translations_progress.json` и пропускает уже переведённые строки.

### Шаг 5. Перевод Android-ресурсов

```bash
python scripts/translate_strings_xml.py
```

Создаёт `res/values-ru/strings.xml` из `res/values/strings.xml`. Системные строки пропускаются (булевы значения, имена пакетов, коды цветов, hex-строки).

### Шаг 6. Сборка APK

```bash
java -jar apktool.jar b bitlife_decoded -o bitlife_ru_unsigned.apk
```

### Шаг 7. Подпись APK

```bash
java -jar uber-apk-signer.jar --apks bitlife_ru_unsigned.apk --out signed/
```

Используется встроенный debug keystore. Выходной файл: `bitlife_ru_unsigned-aligned-debugSigned.apk` с подписями v2 + v3.

## Конфигурация

### Настройка фильтра

Функция `is_game_sentence()` в `translate_metadata_parallel.py` определяет, какие строки переводить. Текущий фильтр:

```python
def is_game_sentence(text):
    if len(text) < 5: return False
    if ' ' not in text: return False
    if not re.search(r'[a-zA-Z]{3,}', text): return False
    if re.match(r'^[a-z]+(\.[a-zA-Z_][a-zA-Z0-9_]*)+$', text): return False  # имена пакетов
    if '://' in text: return False  # URL
    if alpha_count / len(text) < 0.3: return False
    return True
```

Для более агрессивного перевода (например, однословных UI-меток) — ослабить требование пробела. Для более консервативного — увеличить минимальную длину.

### Количество потоков

Изменить `ThreadPoolExecutor(max_workers=5)` в `translate_metadata_parallel.py`. Большие значения рискуют получить rate-limit от Google Translate (HTTP 429). Скрипт обрабатывает 429 с экспоненциальной задержкой.

### Backend переводчика

По умолчанию: `GoogleTranslator` из библиотеки `deep-translator` (использует публичный web-эндпоинт Google Translate, без API-ключа).

Альтернативы (править функцию `translate_one()`):

```python
from deep_translator import DeepL
translator = DeepL(api_key="...", source="en", target="ru")  # выше качество, платно

from deep_translator import YandexTranslator
translator = YandexTranslator(api_key="...", source="en", target="ru")  # хорошо работает с RU
```

## Ограничения

### Что не переведено

| Компонент | Причина | Сложность исправления |
|-----------|--------|----------------|
| `libil2cpp.so` (имена enum'ов, названия профессий) | Скомпилированный нативный код, требуется бинарный патч ARM64 инструкций | Высокая |
| `datapack.unity3d` (текст в asset bundle) | Сериализованный бинарный формат Unity, требуется AssetStudio/UABE для распаковки и пересборки | Средняя |
| `sharedassets0.resource` (текст сцен) | Аналогично | Средняя |
| Длинные описания событий (4,178 строк) | Байты UTF-8 русского > байты ASCII английского, обрезается по длине оригинала | Требуется пересборка metadata (см. выше) |

### Известные проблемы

1. **Обрезанные строки**: ~50% переведённых строк обрезаны по длине исходного английского текста в байтах. Длинные события могут обрываться на середине слова.
2. **Debug-подпись**: Невозможно установить поверх официального BitLife — нужно сначала удалить оригинал.
3. **Обновления ломают перевод**: Каждое обновление BitLife меняет смещения в `global-metadata.dat`. Повторный запуск пайплайна на новом APK занимает ~5 минут.
4. **Плейсхолдеры сохраняются**: Строки формата вроде `{0}`, `<he/she>`, `<color=yellow>` остаются как есть, что может давать корявые русские фразы.

## Системные требования

```
Python 3.10+
apktool 2.9.3+
OpenJDK 21+
uber-apk-signer 1.3.0+
```

Python-пакеты:

```
deep-translator>=1.11
requests>=2.31
```

## Статистика (v3.24.3)

| Метрика | Значение |
|--------|-------|
| Размер APK (оригинал) | 246 МБ |
| Размер APK (перевод) | 249 МБ |
| Всего строк в metadata | 35,078 |
| Уникальных игровых предложений | 8,111 |
| Переведено строк | 8,111 |
| In-place патчей применено | 4,451 |
| Обрезано строк | 4,178 |
| Пропущено (без изменений) | 4,079 |
| UI-строк переведено | 599 |
| Время перевода | ~20 мин (с паузами на rate-limit) |

## Воспроизводимость

Для воспроизведения на другой версии BitLife или другой IL2CPP-игре:

1. Получите APK (легально, со своего устройства через `adb pull`)
2. Запустите шаги пайплайна выше
3. Переводчик не привязан к игре — переводит любой английский текст в `global-metadata.dat`

## Юридическая справка

BitLife — торговая марка Candywriter, LLC. Проект не аффилирован с Candywriter. Использовать только на легально полученной копии игры.

Лицензия: MIT (см. [LICENSE](LICENSE))
