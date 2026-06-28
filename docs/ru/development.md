[English version](./../../README.md)

# Разработка

Этот документ описывает окружение разработки, структуру зависимостей, запуск тестов и процесс release-сборки для `HapticTrace`.

## Структура репозитория

Приложение находится в пакете `app/`.

В репозитории также есть:

- manifest-файлы runtime- и development-зависимостей
- bootstrap-скрипты для локального запуска
- скрипты release-сборки
- compliance-артефакты и lockfile-файлы

## Окружение разработки

`HapticTrace` разрабатывается только под **macOS**.

Требуемые инструменты и условия:

- macOS
- Python 3
- доступ в сеть для первичного bootstrap
- доступ к iPhone/iPad как к источнику захвата, если ведется работа с video path
- доступное по сети устройство с `phyphox`, если ведется работа с sensor path

## Модель зависимостей

Python-зависимости разделены по профилям.

### Runtime-профиль

Runtime-зависимости самого приложения:

- `requirements/runtime.in`
- `requirements/runtime.lock`

`runtime.lock` — воспроизводимый lockfile, используемый стандартным bootstrap-сценарием.

### Development-профиль

Дополнительные зависимости для тестов и developer tooling:

- `requirements/dev.in`
- `requirements/dev.lock`

Development-профиль расширяет runtime-профиль.

## Bootstrap и локальный запуск

Стандартная точка входа для локального запуска:

```bash
./run_app.sh --url http://<device_local_ip>:8080
```

При первом запуске скрипт:

1. создает root-level `.venv`
2. обновляет `pip`
3. устанавливает зависимости из выбранного lockfile

По умолчанию `run_app.sh` использует **runtime**-профиль.

Чтобы подготовить **development**-профиль:

```bash
./run_app.sh --bootstrap-profile dev --help
```

Это рекомендуемый способ подготовки окружения для тестов и developer tooling.

## Запуск через Finder

Точка входа через Finder:

```bash
run_app.command
```

Использует тот же bootstrap-сценарий, что и shell-скрипт.

## Замечание про tkinter

`tkinter` обычно доступен в системном Python на macOS.  
Если его нет, используйте сборку Python с поддержкой Tk.

## Запуск тестов

Сначала подготовьте development-окружение:

```bash
./run_app.sh --bootstrap-profile dev --help
```

Затем запустите тесты:

```bash
.venv/bin/python -m pytest app/tests
```

## Артефакты зависимостей и compliance

В репозитории хранятся lockfile-файлы и compliance-входы, необходимые для воспроизводимой сборки и упаковки релиза.

Основные артефакты:

- `requirements/runtime.lock` — точный runtime lockfile
- `requirements/dev.lock` — точный development/test lockfile
- `scripts/requirements-release.lock` — точный lockfile для Python release tooling
- `THIRD_PARTY_NOTICES.md` — человекочитаемые notices по сторонним пакетам

## Release-сборка

Точка входа для macOS release-сборки:

```bash
./scripts/build_release_macos.sh
```

Release-скрипт использует изолированное build-окружение и на верхнем уровне выполняет следующие шаги:

1. полностью пересоздает каталог `build/`
2. устанавливает runtime-зависимости из `requirements/runtime.lock`
3. устанавливает Python release tooling из `scripts/requirements-release.lock`
4. скачивает pinned binary release `syft`
5. собирает macOS app bundle
6. генерирует compliance-артефакты
7. упаковывает результат сборки

## Результаты release-сборки

Release-сборка создает:

- `build/release/HapticTrace.app` — macOS app bundle
- `build/release/compliance/sbom.runtime.cdx.json` — SBOM финального app bundle
- `build/release/requirements/` — runtime manifest-файлы и lockfile-файлы, использованные в релизе
- `build/release/scripts/` — release script и pinned inputs для tooling
- `build/HapticTrace-macos-<arch>-release.zip` — упакованный release-архив для архитектуры машины сборки

## Codesign

Переносимые release-сборки требуют Developer ID signing identity.

Чтобы использовать конкретный signing identity, перед запуском release-скрипта задайте:

```bash
HAPTIC_CODESIGN_IDENTITY=<your_identity>
```

Для локальных smoke-сборок задайте `HAPTIC_ALLOW_ADHOC=1`, чтобы явно разрешить ad-hoc подпись.

## SBOM и release tooling

Release pipeline генерирует runtime SBOM в формате CycloneDX JSON.

Предположения tooling-пайплайна:

- runtime Python-пакеты берутся из pinned runtime lockfile
- пакеты для release-tooling берутся из pinned release-tooling lockfile
- `syft` скачивается как pinned binary release

Это делает release-процесс воспроизводимым и аудируемым.

## Типовой сценарий разработки

1. подготовить development-окружение
2. запустить приложение локально
3. проверить sensor path и/или video path в зависимости от изменений
4. запустить тесты
5. при необходимости выполнить release-сборку

## Примечания

- root-level `.venv` — часть стандартного локального workflow
- runtime- и development-зависимости разделены намеренно
- release-сборка использует собственный контролируемый путь tooling
- development- и release-процессы должны использовать закоммиченные lockfile-файлы
