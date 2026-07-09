# Windows CI Build — рабочая связка и грабли (форк `mods`)

Памятка для агентов по Windows-сборке форка (`badigit/AyuGramDesktop`, ветка `mods`,
workflow `.github/workflows/win.yml`, триггер `workflow_dispatch`). Написана после
восстановления зелёной сборки 2026-07-09 (run 29006198151, ~3ч19м, артефакт
`AyuGram-x64`) — раннер GitHub переехал на `windows-latest` c Visual Studio 2026 и
сломал всё разом. Ниже — что должно стоять и почему, чтобы не переоткрывать заново.

## TL;DR — золотое правило

**НЕ синхронизируй `Telegram/build/prepare/prepare.py` целиком с upstream tdesktop.**
Он тесно связан с версией сабмодуля `cmake` (cmake_helpers) и с libs форка
(`lib_base`/`lib_ui`, стиль `#include <zip.h>`, имена `zlibstatic`). Полный sync
притащит новый zlib (`libzs`, стиль `<minizip/zip.h>`), а под него нет пина
cmake_helpers, совместимого с libs форка — это тупик на много часов.

Вместо этого: возьми **рабочую связку из истории успешных сборок** и добавь поверх
только дельты под окружение раннера.

```bash
gh run list --repo badigit/AyuGramDesktop --workflow=win.yml --limit 40 \
  --json conclusion,headSha,createdAt   # найти последний conclusion=success
```

Последний известный good: `8614211e3e` (2026-05-18, ещё на windows-2022).

## Когерентная связка (что должно стоять)

| Компонент | Значение | Почему |
|---|---|---|
| сабмодуль `cmake` | **`4088db229d`** (= origin/dev) | ждёт `zlibstatic.lib`, содержит макрос `DESKTOP_APP_DISABLE_X11_INTEGRATION`, minizip берёт из сабмодуля `Telegram/ThirdParty/minizip` → `<zip.h>` резолвится |
| `prepare.py` zlib-стадия | **zlib v1.3.1** (`git clone -b v1.3.1`, без minizip-флагов) | даёт `zlibstatic.lib`/`zlibstaticd.lib`, совместимые с cmake 4088db229d |
| `prepare.py` patches-пин | **`667174b`** | Qt-патчи ссылаются на `zlibstatic`; новый пин `94441c0` ждёт `libzsd` → `ninja error libzsd.lib missing` |
| 9 fork-сабмодулей | **все = origin/dev** | их gitlinks согласованы между собой (палитра, API lib_ui↔lib_base). Рассинхрон → ошибки типа `unexpected token 'dialogsMentionIconFg'` в `.style` |

Выровнять сабмодули на origin/dev:
```bash
git fetch origin
for sm in Telegram/lib_base Telegram/lib_ui Telegram/codegen Telegram/lib_crl \
          Telegram/lib_lottie Telegram/lib_spellcheck Telegram/lib_webrtc Telegram/lib_webview; do
  c=$(git ls-tree origin/dev "$sm" | awk '{print $3}')
  git -C "$sm" fetch origin && git -C "$sm" checkout -q "$c"
done
git add cmake Telegram/lib_*   # cmake держим на 4088db229d
```

## VS 2026 / windows-latest дельты (что добавлено ПОВЕРХ связки 18-мая)

В `.github/workflows/win.yml`:
- `runs-on: windows-latest` (было `windows-2022`) — на 2022-образе с MSVC 14.44 libvpx ASM падает `MSB8066 exited with code -1073741819` (access violation ассемблера); на latest собирается.
- `toolset: '14.44'` в шаге `Eden-CI/msvc-dev-cmd` — дефолтный 14.51 (VS 2026 preview STL) даёт при линковке codegen unresolved `__std_max_element_*`/`__std_rotate`/`__std_unique_*` (векторизованные STL-алгоритмы). Пин 14.44 лечит. Совпадает с upstream tdesktop.
- Убраны env `GYP_MSVS_OVERRIDE_PATH` / `GYP_MSVS_VERSION` из шага `Libraries.` — они хардкодят путь к VS 2022; без них gyp сам находит VS 2026 (иначе breakpad падает `vcvarsall.bat is missing`).
- `TBuild` на диске рабочего пространства (D:), без `mklink` на `%userprofile%` (C:) — иначе Qt падает `C1085 not enough space on the disk` (C: ~14 ГБ мало).

В `Telegram/build/prepare/prepare.py` — **только 2 дельты** относительно 18-мая:
- **Удалена сборка `dump_syms`** из Windows-ветки breakpad-стадии (строки `cd tools\windows\dump_syms; gyp; msbuild`). `dump_syms` требует ATL, которого нет в VS 2026 на раннере (доустановить невозможно — vs_installer на образе рвётся `Didn't find any channel feed`). Upstream tdesktop на CI его тоже не собирает (`skip-release`). Клиентские либы breakpad (`exception_handler` и пр.) собираются без ATL.
- **Hardened-cmd фикс** (cherry-pick upstream `bf3774065b`): удаление `NoDefaultCurrentDirectoryInExePath` из env, `set "NoDefaultCurrentDirectoryInExePath="` в `command.bat`, запуск `command.bat` по абсолютному пути. Иначе yasm/ml.exe не находят свои DLL под hardened cmd раннера.

## Грабли, которые прошли (симптом → причина → фикс)

| Симптом в логе | Причина | Фикс |
|---|---|---|
| `libvpx … MSB8066 … -1073741819` | MSVC 14.44 на windows-2022-образе | `runs-on: windows-latest` |
| breakpad `vcvarsall.bat is missing` | хардкод пути VS 2022 | убрать GYP_MSVS env |
| breakpad `atlbase.h: No such file` | VS 2026 без ATL | удалить dump_syms (ATL не нужен) |
| Qt `C1085 not enough space on the disk` | TBuild на C: | TBuild на D: |
| codegen `unresolved __std_max_element_*` | toolset 14.51 (VS 2026 STL) | `toolset: '14.44'` |
| `LNK1181 zlibstatic.lib` / `ninja libzsd.lib missing` / `C1083 zip.h` | перебамп cmake+zlib (новый zlib несовместим с libs форка) | откат cmake→4088db229d + zlib-стадия→v1.3.1 + patches→667174b |
| `.style` error `unexpected token 'dialogsMentionIconFg'` | рассинхрон сабмодулей | выровнять fork-сабмодули на origin/dev |
| `C1083 xcb/xcb.h` под Windows | нет макроса `DESKTOP_APP_DISABLE_X11_INTEGRATION` (свежий cmake_helpers его удалил) | cmake на 4088db229d (макрос присутствует) |

## Запуск и мониторинг

```bash
gh workflow run win.yml --repo badigit/AyuGramDesktop --ref mods
gh run list --repo badigit/AyuGramDesktop --workflow=win.yml --limit 1
gh run view <run-id> --repo badigit/AyuGramDesktop            # шаги
gh run view <run-id> --repo badigit/AyuGramDesktop --log-failed | \
  grep -in "error C\|error LNK\|fatal error\|FAILED\|##\[error\]" | tail -30
```

- Холодная сборка ~2.5–3ч. После первого зелёного прогона кэш GHA прогрет
  (`gh api repos/badigit/AyuGramDesktop/actions/caches`) → следующие ~15 мин.
- Кэш НЕ сохраняется, если job упал (post-cache save `skipped`), поэтому первый
  успешный прогон обязателен для прогрева.
- Live-лог для `in_progress` через `gh` недоступен — смотри `--log-failed` после
  завершения либо шаги через `gh run view`.
