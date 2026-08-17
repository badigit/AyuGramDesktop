"""Checks an update package exactly the way the client does.

Mirrors core/update_checker.cpp, UnpackUpdate(): the order of the checks, the
field offsets and the QDataStream layout. Use it before publishing — the client
rejects a broken package silently, without telling the user anything.
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
		sys.exit('No UpdatesPublicKey found in ' + str(config_header))
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
		help='PEM with the public key; taken from config.h when omitted')
	parser.add_argument('--config-header', type=Path,
		default=Path(__file__).resolve().parents[2]
			/ 'Telegram' / 'SourceFiles' / 'config.h')
	parser.add_argument('--expect-version', type=int)
	args = parser.parse_args()

	data = args.package.read_bytes()
	if len(data) <= HEADER_LEN:
		sys.exit('Package is shorter than its header')

	signature = data[:SIGNATURE_LEN]
	stored_hash = data[SIGNATURE_LEN:SIGNATURE_LEN + SHA1_LEN]
	body = data[SIGNATURE_LEN + SHA1_LEN:]

	if hashlib.sha1(body).digest() != stored_hash:
		sys.exit('SHA1 mismatch, the client would say "bad SHA1 hash"')
	print('sha1        ok')

	key = load_public_key(args.public_key, args.config_header)
	try:
		key.verify(signature, stored_hash, padding.PKCS1v15(), hashes.SHA1())
	except InvalidSignature:
		sys.exit('Signature does not verify against the key built into the client')
	print('signature   ok, key from '
		+ str(args.public_key or args.config_header))

	props = body[:LZMA_PROPS_LEN]
	original_size = struct.unpack_from('<i', body, LZMA_PROPS_LEN)[0]
	stream = body[LZMA_PROPS_LEN + ORIGINAL_SIZE_LEN:]
	packed = props[0]
	filters = [{
		'id': lzma.FILTER_LZMA1,
		'lc': packed % 9,
		'lp': (packed // 9) % 5,
		'pb': (packed // 9) // 5,
		'dict_size': struct.unpack_from('<I', props, 1)[0],
	}]
	payload = lzma.decompress(stream, format=lzma.FORMAT_RAW, filters=filters)
	if len(payload) != original_size:
		sys.exit('Decompressed ' + str(len(payload)) + ' bytes, header says '
			+ str(original_size))
	print('lzma        ok, ' + str(original_size) + ' bytes')

	version, offset = read_qt_uint32(payload, 0)
	if version == 0x7FFFFFFF:
		sys.exit('This is an alpha package, that path is not used here')
	count, offset = read_qt_uint32(payload, offset)
	print('version     ' + str(version))
	print('files       ' + str(count))

	names = []
	for _ in range(count):
		name_len, offset = read_qt_uint32(payload, offset)
		name = payload[offset:offset + name_len].decode('utf-16-be')
		offset += name_len
		size, offset = read_qt_uint32(payload, offset)
		inner_len, offset = read_qt_uint32(payload, offset)
		if inner_len != size:
			sys.exit('File ' + name + ' claims ' + str(size)
				+ ' bytes but carries ' + str(inner_len))
		offset += inner_len
		names.append(name)
		print('  ' + name + ' - ' + str(size) + ' bytes')

	if offset != len(payload):
		sys.exit(str(len(payload) - offset) + ' trailing bytes left over')

	if 'AyuGram.exe' not in names:
		sys.exit('No AyuGram.exe in the package, nothing to update')
	if 'Updater.exe' not in names:
		print('WARNING: no Updater.exe in the package, the client keeps its own')

	if args.expect_version is not None and version != args.expect_version:
		sys.exit('Package version is ' + str(version) + ', expected '
			+ str(args.expect_version))

	print('package is valid')


if __name__ == '__main__':
	main()
