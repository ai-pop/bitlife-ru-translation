# 🎮 BitLife Russian Translation

Полный русский перевод **BitLife v3.24.3** с инструментами для автоматического перевода IL2CPP-игр.

![License](https://img.shields.io/badge/License-MIT-yellow)
![Language](https://img.shields.io/badge/Language-Python%203.12-blue)
![Status](https://img.shields.io/badge/Status-Working-green)

## 📦 Что в репозитории

- 📱 **Готовый APK** с русским переводом — в [Releases](https://github.com/ai-pop/bitlife-ru-translation/releases)
- 🐍 **Python-скрипты** для перевода IL2CPP metadata
- 📋 **Полная документация** процесса перевода
- 🔧 **Воспроизводимый пайплайн** — можно перевести любую IL2CPP-игру

## 🎯 Что переведено

| Компонент | Кол-во строк | Статус |
|-----------|--------------|--------|
| `global-metadata.dat` (события, диалоги) | 8,111 | ✅ Переведено |
| `strings.xml` (UI: кнопки, меню) | 599 | ✅ Переведено |
| `libil2cpp.so` (имена профессий, enum'ы) | — | ❌ Не переведено (binary) |
| `datapack.unity3d` (asset bundle) | — | ❌ Не переведено (binary) |

**Метод перевода:** in-place byte replacement с сохранением структуры IL2CPP metadata.

## 🛠️ Технологии

- **Python 3.12** + `deep-translator` (Google Translate API)
- **apktool 2.9.3** — распаковка/сборка APK
- **uber-apk-signer** — подпись APK
- **Threading (5 workers)** — параллельный перевод для ускорения

## 📁 Структура проекта

```
bitlife-ru-translation/
├── scripts/                          # Python-инструменты
│   ├── il2cpp_extract.py            # Парсер IL2CPP metadata
│   ├── translate_metadata.py        # Переводчик (v1, single-thread)
│   ├── translate_metadata_v2.py     # Переводчик (v2, с resume)
│   ├── translate_metadata_batch.py  # Переводчик (batch через |||)
│   ├── translate_metadata_parallel.py # Переводчик (5 threads, финальный)
│   └── translate_strings_xml.py     # Перевод XML-ресурсов Android
├── docs/
│   └── patch_report.json            # Отчёт о переводе
├── README.md
├── LICENSE
└── .gitignore
```

## 🚀 Как использовать

### Установка готового APK

1. Скачай `BitLife-RU-v3.24.3.apk` из [Releases](https://github.com/ai-pop/bitlife-ru-translation/releases)
2. Удали оригинальный BitLife с телефона
3. Разреши установку из неизвестных источников
4. Установи APK

### Самостоятельный перевод (для других версий)

```bash
# 1. Установи зависимости
pip install deep-translator requests
sudo apt install apktool openjdk-21-jdk

# 2. Распакуй APK
java -jar apktool.jar d bitlife.apk -o bitlife_decoded

# 3. Запусти перевод
python scripts/translate_metadata_parallel.py
python scripts/translate_strings_xml.py

# 4. Собери обратно
java -jar apktool.jar b bitlife_decoded -o bitlife_ru.apk

# 5. Подпиши
java -jar uber-apk-signer.jar --apks bitlife_ru.apk
```

## ⚠️ Ограничения

1. **4178 строк обрезаны** — русский UTF-8 длиннее английского в байтах, поэтому часть длинных событий обрезается
2. **Имена профессий** — вшиты в `libil2cpp.so` как enum'ы, требуют бинарного патчинга
3. **Asset bundle тексты** — в `datapack.unity3d`, требуют Unity Editor для распаковки
4. **Debug-подпись** — нужно удалить оригинальный BitLife перед установкой

## 📊 Статистика

- **APK размер:** 249 МБ
- **Строк переведено:** 8,710 (8,111 + 599)
- **Время перевода:** ~20 минут (5 потоков)
- **Переводчик:** Google Translate (en → ru)

## 🤖 Создано с помощью AI

Этот проект — эксперимент по демонстрации возможностей нейросетей в задачах локализации. Весь код написан AI-ассистентом в интерактивной сессии.

## ⚖️ Disclaimer

Этот проект предоставляется исключительно в образовательных целях. BitLife является торговой маркой Candywriter, LLC. Используйте перевод только на легально приобретённой копии игры.

## 📝 License

MIT License — используйте, форкайте, улучшайте.

---

⭐ Если проект был полезен — поставь звезду!
