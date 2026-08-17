"""Проверяет пакет обновления ровно теми же шагами, что и клиент.

Повторяет core/update_checker.cpp, UnpackUpdate(): порядок проверок, смещения
полей и разбор QDataStream. Нужен, чтобы поймать битый пакет до публикации —
клиент отвергает такой молча, без сообщения пользователю.
"""

import argparse
import hashlib
import lzma
import struct
import sys
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_pem_public_key

SIGNATURE_LEN = 128
SHA1_LEN = 20
LZMA_PROPS_LEN = 5
ORIGINAL_SIZE_LEN = 4
HEADER_LEN = SIGNATURE_LEN + SHA1_LEN + LZMA_PROPS_LEN + ORIGINAL_SIZE_LEN


def load_public_key(path, config_header):
	if path:
		return load_pem_public_key(path.read_bytes())
	text = config_header.read_text(encoding='utf-8')
	marker = 'static const char *UpdatesPublicKey = "\\\n'
	start = text.find(marker)
	if start < 0:
		sys.exit('Не нашёл UpdatesPublicKey в ' + str(config_header))
	end = text.find('";', start)
	body = text[start + len(marker):end]
	pem = body.replace('\\n\\\n', '\n').replace('\\\n', '\n').strip()
	return load_pem_public_key(pem.encode('ascii'))


def read_qt_uint32(data, offset):
	return struct.unpack_from('>I', data, offset)[0], offset + 4


def main():
	parser = argparse.ArgumentParser()
	parser.add_argument('package', type=Path)
	parser.add_argument('--public-key', type=Path,
		help='PEM с публичным ключом; по умолчанию берётся из config.h')
	parser.add_argument('--config-header', type=Path,
		default=Path(__file__).resolve().parents[2]
			/ 'Telegram' / 'SourceFiles' / 'config.h')
	parser.add_argument('--expect-version', type=int)
	args = parser.parse_args()

	data = args.package.read_bytes()
	if len(data) <= HEADER_LEN:
		sys.exit('Пакет короче заголовка')

	signature = data[:SIGNATURE_LEN]
	stored_hash = data[SIGNATURE_LEN:SIGNATURE_LEN + SHA1_LEN]
	body = data[SIGNATURE_LEN + SHA1_LEN:]

	actual_hash = hashlib.sha1(body).digest()
	if actual_hash != stored_hash:
		sys.exit('SHA1 не совпал — клиент скажет "bad SHA1 hash"')
	print('SHA1        ok')

	key = load_public_key(args.public_key, args.config_header)
	try:
		key.verify(signature, stored_hash, padding.PKCS1v15(), hashes.SHA1())
	except InvalidSignature:
		sys.exit('Подпись не проходит проверку тем ключом, что зашит в клиент')
	print('Подпись     ok (ключ из '
		+ str(args.public_key or args.config_header) + ')')

	props = body[:LZMA_PROPS_LEN]
	original_size = struct.unpack_from('<i', body, LZMA_PROPS_LEN)[0]
	stream = body[LZMA_PROPS_LEN + ORIGINAL_SIZE_LEN:]
	packed = props[0]
	lc = packed % 9
	lp = (packed // 9) % 5
	pb = (packed // 9) // 5
	filters = [{
		'id': lzma.FILTER_LZMA1,
		'lc': lc,
		'lp': lp,
		'pb': pb,
		'dict_size': struct.unpack_from('<I', props, 1)[0],
	}]
	payload = lzma.decompress(stream, format=lzma.FORMAT_RAW, filters=filters)
	if len(payload) != original_size:
		sys.exit('Размер после распаковки ' + str(len(payload))
			+ ', в заголовке ' + str(original_size))
	print('LZMA        ok, ' + str(original_size) + ' байт')

	version, offset = read_qt_uint32(payload, 0)
	if version == 0x7FFFFFFF:
		sys.exit('Это alpha-пакет, такой сценарий не используется')
	count, offset = read_qt_uint32(payload, offset)
	print('Версия      ' + str(version))
	print('Файлов      ' + str(count))

	names = []
	for _ in range(count):
		name_len, offset = read_qt_uint32(payload, offset)
		name = payload[offset:offset + name_len].decode('utf-16-be')
		offset += name_len
		size, offset = read_qt_uint32(payload, offset)
		inner_len, offset = read_qt_uint32(payload, offset)
		if inner_len != size:
			sys.exit('У файла ' + name + ' размер ' + str(size)
				+ ' не сходится с длиной данных ' + str(inner_len))
		offset += inner_len
		names.append(name)
		print('  ' + name + ' — ' + str(size) + ' байт')

	if offset != len(payload):
		sys.exit('После разбора осталось ' + str(len(payload) - offset)
			+ ' лишних байт')

	if 'AyuGram.exe' not in names:
		sys.exit('В пакете нет AyuGram.exe — обновлять нечего')
	if 'Updater.exe' not in names:
		print('ВНИМАНИЕ: в пакете нет Updater.exe — клиент возьмёт текущий')

	if args.expect_version is not None and version != args.expect_version:
		sys.exit('Версия в пакете ' + str(version)
			+ ', ожидалась ' + str(args.expect_version))

	print('Пакет валиден')


if __name__ == '__main__':
	main()
