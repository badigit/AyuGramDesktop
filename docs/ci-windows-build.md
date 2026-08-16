# Windows CI Build — рабочая связка и грабли (форк `mods`)

Памятка для агентов по Windows-сборке форка (`badigit/AyuGramDesktop`, ветка `mods`,
workflow `.github/workflows/win.yml`, триггер `workflow_dispatch`). Актуальна после
обновления форка до **v7.0.9** (мерж `origin/dev`, +2171 коммит, rich-сообщения) —
зелёная сборка 2026-08-16, run 31936203196, 2ч07м, артефакт `AyuGram-x64`.

## TL;DR — два золотых правила

**1. Не синхронизируй `prepare.py` выборочно с upstream tdesktop.** Он тесно связан с
версией сабмодуля `cmake` (cmake_helpers) и с libs форка. Полусинхронизация даёт
несовместимость имён zlib/minizip и стилей инклудов — это тупик на много часов.

**2. После мержа свежего `origin/dev` бери ЕГО build-стек целиком и снимай старые
костыли.** AyuGram/dev самосогласован: его `prepare.py`, пин `cmake` и пины сабмодулей
подходят друг к другу. Все прежние обходные пины (старый cmake, zlib v1.3.1,
`skip_libs` для cmark/microtex/tmc) после мержа становятся вредными — они ломают сборку
и отключают реальные фичи.

Свою поверх стека держи только **environment-дельты** под конкретный раннер (см. ниже).

Найти последнюю успешную сборку:

```bash
gh run list --repo badigit/AyuGramDesktop --workflow=win.yml --limit 40 \
  --json conclusion,headSha,createdAt
```

## Когерентная связка (что должно стоять)

| Компонент | Значение | Комментарий |
|---|---|---|
| сабмодуль `cmake` | `f79a0e6acd` (= origin/dev) | ждёт `libzs.lib`, minizip через `zlib/contrib` |
| zlib в `prepare.py` | `e3dc0a85b` (= origin/dev) | даёт `libzs.lib` / `libzsd.lib` |
| patches в `prepare.py` | `a17d54b6` (= origin/dev) | Qt-патчи завязаны на имена zlib |
| сабмодули форка | все = origin/dev | рассинхрон даёт ошибки палитры в `.style` |
| `desktop_app_skip_libs` | только `glibmm`, `variant` | cmark-gfm / MicroTeX / TooManyCooks теперь ЕСТЬ как сабмодули и нужны для rich-сообщений |

Проверка стыковки (должно совпадать):

```bash
grep -n "libzs\|zlibstatic" cmake/external/zlib/CMakeLists.txt
grep -n "contrib" cmake/external/minizip/CMakeLists.txt
grep -n "include <.*zip.h>" Telegram/lib_base/base/zlib_help.h
```

Стиль инклуда в `lib_base` (`<minizip/zip.h>` против `<zip.h>`) обязан соответствовать
тому, что отдаёт `external_minizip` в текущем `cmake`.

## Environment-дельты (наше поверх стека origin/dev)

В `.github/workflows/win.yml`:

- `Configure pagefile.` — `al-cheb/configure-pagefile-action@v1.4`, 16 ГБ / 24 ГБ на `D:`.
  Обязательно: у GitHub-раннера маленький файл подкачки при 16 ГБ RAM.
- `runs-on: windows-latest`. На `windows-2022` libvpx падает в ASM с access violation.
- `toolset: '14.44'` в `Eden-CI/msvc-dev-cmd`. Дефолтный 14.51 (VS 2026 preview STL)
  даёт unresolved `__std_max_element_*` / `__std_rotate` при линковке codegen.
- `_CL_: /MP2` на шаге сборки — ограничение параллелизма компилятора (см. грабли).
- `cmake --build ... --parallel 2`.
- TBuild на диске рабочего пространства (`%GITHUB_WORKSPACE%`), без `mklink` на `C:`:
  на `C:` не хватает места под Qt.
- Убраны env `GYP_MSVS_OVERRIDE_PATH` / `GYP_MSVS_VERSION` — они хардкодят VS 2022.
- `DOCPATH=.../docs/building-win.md` — файл переименован в v7.0.9.
- Вход `only_cache` гейтит шаги `Telegram Desktop build` / `Move artifact` /
  `Upload artifact` через `if: inputs.only_cache != true`.

В `Telegram/build/prepare/prepare.py` — одна дельта:

- Удалена сборка `dump_syms` из Windows-ветки breakpad. Она требует ATL, которого нет
  в VS 2026 на раннере (доустановить нельзя: vs_installer на образе рвётся
  `Didn't find any channel feed`). Upstream tdesktop на CI её тоже не собирает.

## Двухфазный запуск (важно при холодном кэше)

Job на GitHub-hosted раннере жёстко ограничен 6 часами; `timeout-minutes` может только
уменьшать лимит, поднять его нельзя. Холодная сборка v7.0.9 не укладывается.

Кэш не сохраняется при падении job (post-cache шаги пропускаются), поэтому:

```bash
# Фаза 1 — прогрев кэша, только Libraries (~1.5ч), job завершается успехом
gh workflow run win.yml --repo badigit/AyuGramDesktop --ref mods -f only_cache=true

# Фаза 2 — обычный прогон: Libraries из кэша за минуты, дальше сборка приложения (~2ч)
gh workflow run win.yml --repo badigit/AyuGramDesktop --ref mods
```

Проверить кэш: `gh api repos/badigit/AyuGramDesktop/actions/caches`.

## Грабли (симптом → причина → фикс)

| Симптом в логе | Причина | Фикс |
|---|---|---|
| `libvpx … MSB8066 … -1073741819` | MSVC 14.44 на образе windows-2022 | `runs-on: windows-latest` |
| breakpad `vcvarsall.bat is missing` | хардкод пути VS 2022 | убрать `GYP_MSVS_*` env |
| breakpad `atlbase.h: No such file` | VS 2026 без ATL | удалить dump_syms из breakpad-стадии |
| Qt `C1085 not enough space on the disk` | TBuild на `C:` | TBuild на диске workspace |
| codegen `unresolved __std_max_element_*` | toolset 14.51 (VS 2026 STL) | `toolset: '14.44'` |
| `grep: docs/building-win-x64.md: No such file` | файл переименован в v7.0.9 | `DOCPATH=docs/building-win.md` |
| `C1060 out of heap space`, `C3859 Failed to create virtual memory for PCH`, `C1076 internal heap limit` | `/MP` без числа в `cmake/options_win.cmake:34` плодит компиляторы по числу ядер; памяти раннера не хватает | `_CL_: /MP2` **плюс** увеличенный pagefile |
| `LNK1181 zlibstatic.lib` / `ninja libzsd.lib missing` / `C1083 zip.h` | рассогласование zlib ↔ cmake ↔ libs | привести всё к пинам origin/dev |
| `.style` error 204 `unexpected token` | рассинхрон сабмодулей | выровнять сабмодули на origin/dev |
| `C1083 xcb/xcb.h` под Windows | libs используют макрос, которого нет в cmake | согласовать версии cmake и libs |

Важно про `/MP`: `cmake --build --parallel N` управляет параллелизмом **проектов**
MSBuild, а финальная стадия — один большой `Telegram.vcxproj`. Внутри него параллелизм
задаёт именно `/MP`, поэтому снижение `--parallel` почти не влияет на пик памяти.
Переменная `_CL_` подставляет опции **после** флагов проекта и потому перебивает `/MP`;
`CL` (без подчёркиваний) подставляет **до** и не сработает.

## Мониторинг

```bash
gh run list --repo badigit/AyuGramDesktop --workflow=win.yml --limit 1
gh run view <run-id> --repo badigit/AyuGramDesktop
gh run view <run-id> --repo badigit/AyuGramDesktop --log-failed | \
  grep -in "error C\|error LNK\|fatal error\|FAILED\|##\[error\]" | tail -30
```

Live-лог для идущего job через `gh` недоступен — смотри состав шагов, а полный лог
только после завершения.
