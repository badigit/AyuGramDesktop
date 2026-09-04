"""Rehearses the publishing steps of .github/workflows/win.yml locally.

The three steps that publish an update - "Pack and sign update", "Publish
release" and "Update current4 on Pages" - only ever run on a real release, so
a mistake in them is found by breaking a release. This test takes the very
same shell snippets out of the workflow file and runs them against a throwaway
key, a stub `gh` and a local bare repository standing in for `gh-pages`.

Nothing here talks to GitHub and nothing outside the temporary directory is
touched: HOME is redirected so that `git config --global` writes into the
sandbox, and the sandbox HOME maps the workflow's github.com URL onto the
local bare repository.

Run it directly:

    python tools/ayu-update/test_publish_steps.py

Needs bash (Git Bash on Windows), git and the `cryptography` package.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / '.github' / 'workflows' / 'win.yml'

PACK_STEP = 'Pack and sign update.'
RELEASE_STEP = 'Publish release.'
PAGES_STEP = 'Update current4 on Pages.'


def workflow_step_script(name):
	"""Returns the `run: |` body of the named step of the workflow."""
	lines = WORKFLOW.read_text(encoding='utf-8').splitlines()
	start = None
	for index, line in enumerate(lines):
		if line.strip() == '- name: ' + name:
			start = index
			break
	if start is None:
		raise AssertionError('No step named ' + name + ' in ' + str(WORKFLOW))
	body = None
	for index in range(start + 1, len(lines)):
		line = lines[index]
		if body is None:
			if line.strip().startswith('- name:'):
				raise AssertionError('Step ' + name + ' has no run: block')
			if line.strip() == 'run: |':
				body = []
				indent = len(line) - len(line.lstrip()) + 2
			continue
		if line.strip() and not line.startswith(' ' * indent):
			break
		body.append(line[indent:] if line.strip() else '')
	if not body:
		raise AssertionError('Step ' + name + ' has an empty run: block')
	return '\n'.join(body) + '\n'


def bash(script, env, cwd):
	return subprocess.run(
		['bash', '-c', script],
		env=env,
		cwd=str(cwd),
		capture_output=True,
		text=True)


def to_posix(path):
	text = str(path).replace('\\', '/')
	if len(text) > 1 and text[1] == ':':
		return '/' + text[0].lower() + text[2:]
	return text


def make_key(sandbox):
	from cryptography.hazmat.primitives import serialization
	from cryptography.hazmat.primitives.asymmetric import rsa

	key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
	private_pem = key.private_bytes(
		encoding=serialization.Encoding.PEM,
		format=serialization.PrivateFormat.TraditionalOpenSSL,
		encryption_algorithm=serialization.NoEncryption())
	public_pem = key.public_key().public_bytes(
		encoding=serialization.Encoding.PEM,
		format=serialization.PublicFormat.PKCS1)
	(sandbox / 'private.pem').write_bytes(private_pem)
	return private_pem.decode('ascii'), public_pem.decode('ascii')


def make_checkout(sandbox, public_pem):
	"""A minimal tree that verify.py accepts as a checkout of this fork."""
	checkout = sandbox / 'TBuild' / 'AyuGramDesktop'
	(checkout / 'tools' / 'ayu-update').mkdir(parents=True)
	for name in ('pack.py', 'verify.py'):
		shutil.copy(ROOT / 'tools' / 'ayu-update' / name,
			checkout / 'tools' / 'ayu-update' / name)
	sources = checkout / 'Telegram' / 'SourceFiles'
	sources.mkdir(parents=True)
	escaped = '\\n\\\n'.join(public_pem.strip().splitlines())
	(sources / 'config.h').write_text(
		'static const char *UpdatesPublicKey = "\\\n' + escaped + '\\\n";\n',
		encoding='utf-8')
	return checkout


def make_gh_stub(sandbox):
	"""A `gh` that records its arguments instead of calling GitHub."""
	bindir = sandbox / 'bin'
	bindir.mkdir()
	log = sandbox / 'gh.log'
	releases = sandbox / 'releases'
	releases.mkdir()
	script = (
		'#!/bin/sh\n'
		'echo "$@" >> "' + to_posix(log) + '"\n'
		'releases="' + to_posix(releases) + '"\n'
		'case "$2" in\n'
		'view) test -f "$releases/$3" ;;\n'
		'create) touch "$releases/$3"; cp "$4" "$releases/" ;;\n'
		'upload) cp "$4" "$releases/" ;;\n'
		'*) echo "unexpected gh call: $@" >&2; exit 2 ;;\n'
		'esac\n')
	(bindir / 'gh').write_text(script, encoding='utf-8', newline='\n')
	os.chmod(bindir / 'gh', 0o755)
	return bindir, log, releases


def make_pages_remote(sandbox, home):
	"""A bare repository with a gh-pages branch, mapped onto the github URL."""
	remote = sandbox / 'pages.git'
	subprocess.run(['git', 'init', '--quiet', '--bare', str(remote)], check=True)
	seed = sandbox / 'pages-seed'
	subprocess.run(['git', 'clone', '--quiet', str(remote), str(seed)],
		check=True, capture_output=True)
	(seed / 'current4').write_text('0:none\n', encoding='utf-8', newline='\n')
	run = lambda *args: subprocess.run(args, cwd=str(seed), check=True,
		capture_output=True)
	run('git', 'checkout', '--quiet', '-b', 'gh-pages')
	run('git', 'add', 'current4')
	run('git', '-c', 'user.name=Seed', '-c', 'user.email=seed@example.com',
		'commit', '--quiet', '-m', 'seed')
	run('git', 'push', '--quiet', 'origin', 'gh-pages')
	url = 'https://x-access-token:TOKEN@github.com/badigit/AyuGramDesktop.git'
	(home / '.gitconfig').write_text(
		'[url "file:///' + to_posix(remote).lstrip('/') + '"]\n'
		'\tinsteadOf = ' + url + '\n',
		encoding='utf-8', newline='\n')
	return remote


def pages_current4(sandbox, remote):
	out = subprocess.run(
		['git', '--git-dir', str(remote), 'show', 'gh-pages:current4'],
		check=True, capture_output=True, text=True)
	return out.stdout.strip()


class Rehearsal:
	def __init__(self, sandbox):
		self.sandbox = sandbox
		self.home = sandbox / 'home'
		self.home.mkdir()
		self.workspace = sandbox / 'workspace'
		(self.workspace / 'artifact').mkdir(parents=True)
		(self.workspace / 'artifact' / 'AyuGram.exe').write_bytes(b'exe' * 4096)
		(self.workspace / 'artifact' / 'Updater.exe').write_bytes(b'upd' * 512)
		self.private_pem, public_pem = make_key(sandbox)
		self.checkout = make_checkout(sandbox, public_pem)
		self.bindir, self.gh_log, self.releases = make_gh_stub(sandbox)
		self.remote = make_pages_remote(sandbox, self.home)
		self.temp = sandbox / 'runner-temp'
		self.temp.mkdir()

	def env(self, version, version_str):
		env = dict(os.environ)
		env.update({
			'HOME': str(self.home),
			'PATH': to_posix(self.bindir) + os.pathsep + env['PATH'],
			'TBUILD': to_posix(self.sandbox / 'TBuild'),
			'REPO_NAME': 'AyuGramDesktop',
			'GITHUB_WORKSPACE': to_posix(self.workspace),
			'GITHUB_REPOSITORY': 'badigit/AyuGramDesktop',
			'GITHUB_SHA': 'f' * 40,
			'RUNNER_TEMP': to_posix(self.temp),
			'PRIVATE_KEY': self.private_pem,
			'GH_TOKEN': 'TOKEN',
			'AppVersion': str(version),
			'AppVersionStr': version_str,
			'PYTHONIOENCODING': 'cp1252',
		})
		return env

	def run(self, step, version, version_str):
		env = self.env(version, version_str)
		return bash(workflow_step_script(step), env, self.workspace)


def check(condition, message, result=None):
	if condition:
		print('ok   ' + message)
		return 0
	print('FAIL ' + message)
	if result is not None:
		print('     exit ' + str(result.returncode))
		for line in (result.stdout + result.stderr).splitlines()[-15:]:
			print('     | ' + line)
	return 1


def main():
	try:
		import cryptography  # noqa: F401
	except ImportError:
		print('SKIP: the cryptography package is missing')
		return 0
	if not shutil.which('bash'):
		print('SKIP: bash is missing')
		return 0

	failures = 0
	with tempfile.TemporaryDirectory() as raw:
		sandbox = Path(raw)
		rehearsal = Rehearsal(sandbox)

		pack = rehearsal.run(PACK_STEP, 7000010, '7.0.10')
		failures += check(pack.returncode == 0,
			'pack and sign builds a package the verifier accepts', pack)
		failures += check(
			(rehearsal.workspace / 'dist' / 'tx64upd7000010').is_file(),
			'pack and sign writes dist/tx64upd7000010')
		failures += check(
			not (rehearsal.temp / 'update-key.pem').exists(),
			'pack and sign removes the private key from the runner')

		release = rehearsal.run(RELEASE_STEP, 7000010, '7.0.10')
		failures += check(release.returncode == 0,
			'publish release creates a missing release', release)
		log = rehearsal.gh_log.read_text(encoding='utf-8')
		failures += check('release create v7.0.10' in log,
			'publish release creates the v7.0.10 release')
		failures += check('--target ' + 'f' * 40 in log,
			'publish release tags the built commit, not the default branch')
		failures += check((rehearsal.releases / 'tx64upd7000010').is_file(),
			'publish release uploads the package as an asset')

		pages = rehearsal.run(PAGES_STEP, 7000010, '7.0.10')
		failures += check(pages.returncode == 0,
			'update current4 pushes the new pointer', pages)
		expected = ('7000010:https://github.com/badigit/AyuGramDesktop'
			'/releases/download/v7.0.10/tx64upd7000010')
		failures += check(pages_current4(sandbox, rehearsal.remote) == expected,
			'current4 points at the release asset')

		again = rehearsal.run(PAGES_STEP, 7000010, '7.0.10')
		failures += check(again.returncode == 0,
			'update current4 survives a rerun of the same version', again)

		release_again = rehearsal.run(RELEASE_STEP, 7000010, '7.0.10')
		failures += check(release_again.returncode == 0,
			'publish release survives a rerun of the same version',
			release_again)
		failures += check('--clobber' in rehearsal.gh_log.read_text(
			encoding='utf-8'), 'the rerun replaces the existing asset')

		bumped = rehearsal.run(PAGES_STEP, 7000011, '7.0.11')
		failures += check(bumped.returncode == 0,
			'update current4 moves on to the next version', bumped)
		expected = ('7000011:https://github.com/badigit/AyuGramDesktop'
			'/releases/download/v7.0.11/tx64upd7000011')
		failures += check(pages_current4(sandbox, rehearsal.remote) == expected,
			'current4 follows the bumped version')

	print()
	print('failures: ' + str(failures))
	return 1 if failures else 0


if __name__ == '__main__':
	sys.exit(main())
