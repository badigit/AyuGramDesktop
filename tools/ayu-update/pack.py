"""Собирает и подписывает пакет обновления для форка AyuGram.

Формат пакета повторяет Telegram/SourceFiles/_other/packer.cpp (ветка Windows,
LZMA SDK) и разбирается клиентом в core/update_checker.cpp, UnpackUpdate():

    [0  ..127]  RSA-подпись (PKCS#1 v1.5 поверх SHA1, ключ ровно 1024 бита)
    [128..147]  SHA1 всего, что идёт после подписи
    [148..152]  LZMA props (5 байт)
    [153..156]  int32 размер распакованных данных, little-endian
    [157..   ]  LZMA-поток

Распакованные данные — поток QDataStream версии Qt_5_1 (big-endian):

    quint32 version
    quint32 filesCount
    filesCount раз:
        QString  относительное имя (quint32 длина в байтах + UTF-16BE)
        quint32  размер файла
        QByteArray содержимое (quint32 длина + байты)

Поле isExecutable клиент на Windows не читает, поэтому оно не пишется.
"""

import argparse
import hashlib
import lzma
import struct
import sys
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.serialization import load_pem_private_key

SIGNATURE_LEN = 128
SHA1_LEN = 20
LZMA_PROPS_LEN = 5
ORIGINAL_SIZE_LEN = 4


def qt_string(value):
	data = value.encode('utf-16-be')
	return struct.pack('>I', len(data)) + data


def qt_byte_array(data):
	return struct.pack('>I', len(data)) + data


def build_payload(version, files):
	out = bytearray()
	out += struct.pack('>I', version)
	out += struct.pack('>I', len(files))
	for name, path in files:
		content = path.read_bytes()
		out += qt_string(name)
		out += struct.pack('>I', len(content))
		out += qt_byte_array(content)
	return bytes(out)


def compress(payload):
	"""Сжимает в сырой LZMA1 и отдаёт (props, поток).

	Клиент распаковывает через LzmaUncompress с заранее известным размером
	(update_checker.cpp:395), то есть ждёт сырой поток без контейнера. Параметры
	те же, что у packer.cpp:300; props кодируют их так же, как LZMA SDK.
	"""
	lc, lp, pb = 4, 0, 2
	dict_size = 64 * 1024 * 1024
	filters = [{
		'id': lzma.FILTER_LZMA1,
		'preset': 9,
		'dict_size': dict_size,
		'lc': lc,
		'lp': lp,
		'pb': pb,
	}]
	stream = lzma.compress(payload, format=lzma.FORMAT_RAW, filters=filters)
	props = bytes([(pb * 5 + lp) * 9 + lc]) + struct.pack('<I', dict_size)
	return props, stream


def sign(private_key_path, digest):
	key = load_pem_private_key(private_key_path.read_bytes(), password=None)
	if not isinstance(key, rsa.RSAPrivateKey):
		sys.exit('Ключ не RSA: ' + str(private_key_path))
	if key.key_size != 1024:
		sys.exit('Клиент принимает только 1024-битный ключ, у этого '
			+ str(key.key_size))
	return key.sign(digest, padding.PKCS1v15(), hashes.SHA1())


def main():
	parser = argparse.ArgumentParser()
	parser.add_argument('--version', type=int, required=True,
		help='числовой AppVersion, например 7000010')
	parser.add_argument('--key', type=Path, required=True,
		help='PEM с приватным RSA-ключом 1024 бита')
	parser.add_argument('--source', type=Path, required=True,
		help='папка со сборкой (AyuGram.exe, Updater.exe)')
	parser.add_argument('--target', default='win64',
		choices=['win64', 'winarm', 'other'])
	parser.add_argument('--out', type=Path, default=Path('.'),
		help='куда положить готовый пакет')
	parser.add_argument('--file', action='append', default=[],
		help='имя файла из --source; по умолчанию AyuGram.exe и Updater.exe')
	args = parser.parse_args()

	names = args.file or ['AyuGram.exe', 'Updater.exe']
	files = []
	for name in names:
		path = args.source / name
		if not path.is_file():
			sys.exit('Нет файла: ' + str(path))
		files.append((name, path))

	payload = build_payload(args.version, files)
	props, stream = compress(payload)

	body = props + struct.pack('<i', len(payload)) + stream
	digest = hashlib.sha1(body).digest()
	signature = sign(args.key, digest)
	if len(signature) != SIGNATURE_LEN:
		sys.exit('Длина подписи ' + str(len(signature)) + ', нужна 128')

	prefix = {
		'win64': 'tx64upd',
		'winarm': 'tarm64upd',
		'other': 'tupdate',
	}[args.target]
	args.out.mkdir(parents=True, exist_ok=True)
	result = args.out / (prefix + str(args.version))
	result.write_bytes(signature + digest + body)

	print('Пакет:      ' + str(result))
	print('Версия:     ' + str(args.version))
	print('Файлов:     ' + str(len(files)) + ' (' + ', '.join(names) + ')')
	print('Распаковано ' + str(len(payload)) + ' байт'
		+ ', сжато ' + str(len(stream)) + ' байт')
	print('Итог:       ' + str(result.stat().st_size) + ' байт')


if __name__ == '__main__':
	main()
