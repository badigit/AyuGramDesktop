# The fork's own autoupdate

This fork updates from its own server with its own signed packages instead of
`update.ayugram.one`. The packer and the verifier live here.

## How it works

On Windows the client polls `<prefix>/current4` (`core/update_checker.cpp`,
`HttpChecker::start`). The prefix is set in `storage/localstorage.cpp`,
`readAutoupdatePrefixRaw()`.

The response is a single line in the old format, and the link is taken as an
absolute URL:

```
7000010:https://github.com/badigit/AyuGramDesktop/releases/download/v7.0.10/tx64upd7000010
```

The client updates only when the number on the left is strictly greater than its
own `AppVersion` (`core/version.h`). The file name at the end of the link must
match the `FindUpdateFile` regexp — for x64 that means `tx64upd<number>`.

The downloaded package is checked against `UpdatesPublicKey` from
`SourceFiles/config.h`. The private half of that pair lives outside the
repository (see below). A broken or foreign package is rejected silently and
retried later, so a bad package cannot break an installed client.

Hosting is split: the tiny `current4` is served by GitHub Pages (branch
`gh-pages`), while the package itself is a GitHub Releases asset, which keeps it
clear of the Pages file size limit.

## The signing key

RSA, exactly 1024 bit — the client requires `RSA_size == 128` and rejects any
other size. The public half is baked into `SourceFiles/config.h`, so replacing
the key requires rebuilding the client and installing that build by hand: an
existing client cannot update onto it over the air.

The private key is stored in the `AYU_UPDATE_PRIVATE_KEY` repository secret and
in a password manager. Losing it means there is no way left to ship updates to
already installed clients.

## Cutting a release

1. Add an entry to `changelog.txt`, then bump the version:
   `python Telegram/build/set_version.py 7.0.11`. The script refuses to run
   without a matching changelog entry.
2. Build and publish:
   `gh workflow run win.yml --repo badigit/AyuGramDesktop --ref mods -f publish_update=true`.
3. The workflow does the rest: builds the package, signs it with the key from
   the secret, verifies it, creates the release with the `tx64upd<version>`
   asset and rewrites `current4`.

Without `publish_update` the workflow only builds and uploads the artifact, the
way it did before.

## Building a package by hand

```bash
python tools/ayu-update/pack.py \
  --version 7000010 \
  --key /path/to/private.pem \
  --source _ayu_build/AyuGram-x64 \
  --out dist
```

Verify before publishing, against the same key the client carries:

```bash
python tools/ayu-update/verify.py dist/tx64upd7000010 --expect-version 7000010
```

`verify.py` repeats the client's own steps in the same order: SHA1, signature,
decompression, file list. If it reports no errors, the client will accept the
package.

Both scripts need Python with the `cryptography` package.

## Rehearsing the publishing steps

The three publishing steps of `.github/workflows/win.yml` only ever run on a
real release, so a mistake in them costs a release. `test_publish_steps.py`
takes those very snippets out of the workflow file and runs them locally
against a throwaway key, a stub `gh` and a local bare repository standing in
for `gh-pages`:

```bash
python tools/ayu-update/test_publish_steps.py
```

It talks to nothing outside its temporary directory and covers the first
publish of a version, a rerun of the same version and a bump to the next one.
Run it after editing any of those steps.
