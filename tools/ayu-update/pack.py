"""Builds and signs an update package for this fork.

The layout mirrors Telegram/SourceFiles/_other/packer.cpp (the Windows branch
using the LZMA SDK) and is parsed by the client in core/update_checker.cpp,
UnpackUpdate():

    [0  ..127]  RSA signature (PKCS#1 v1.5 over SHA1, key must be 1024 bit)
    [128..147]  SHA1 of everything that follows the signature
    [148..152]  LZMA props (5 bytes)
    [153..156]  int32 uncompressed size, little-endian
    [157..   ]  raw LZMA1 stream

The uncompressed blob is a QDataStream of version Qt_5_1 (big-endian):

    quint32 version
    quint32 filesCount
    filesCount times:
        QString  relative name (quint32 byte length + UTF-16BE)
        quint32  file size
        QByteArray contents (quint32 length + bytes)

The isExecutable flag is not written: the client never reads it on Windows.
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
	"""Compresses to raw LZMA1 and returns (props, stream).

	The client decompresses with LzmaUncompress and an already known output
	size (update_checker.cpp:395), so it expects a raw stream with no
	container. Settings match packer.cpp:300; props encode them the same way
	the LZMA SDK does.
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
		sys.exit('Not an RSA key: ' + str(private_key_path))
	if key.key_size != 1024:
		sys.exit('The client only accepts a 1024 bit key, this one is '
			+ str(key.key_size))
	return key.sign(digest, padding.PKCS1v15(), hashes.SHA1())


def main():
	parser = argparse.ArgumentParser()
	parser.add_argument('--version', type=int, required=True,
		help='numeric AppVersion, for example 7000010')
	parser.add_argument('--key', type=Path, required=True,
		help='PEM file with a 1024 bit RSA private key')
	parser.add_argument('--source', type=Path, required=True,
		help='folder holding the build (AyuGram.exe, Updater.exe)')
	parser.add_argument('--target', default='win64',
		choices=['win64', 'winarm', 'other'])
	parser.add_argument('--out', type=Path, default=Path('.'),
		help='where to write the package')
	parser.add_argument('--file', action='append', default=[],
		help='file name inside --source; defaults to AyuGram.exe, Updater.exe')
	args = parser.parse_args()

	names = args.file or ['AyuGram.exe', 'Updater.exe']
	files = []
	for name in names:
		path = args.source / name
		if not path.is_file():
			sys.exit('Missing file: ' + str(path))
		files.append((name, path))

	payload = build_payload(args.version, files)
	props, stream = compress(payload)

	body = props + struct.pack('<i', len(payload)) + stream
	digest = hashlib.sha1(body).digest()
	signature = sign(args.key, digest)
	if len(signature) != SIGNATURE_LEN:
		sys.exit('Signature is ' + str(len(signature)) + ' bytes, need 128')

	prefix = {
		'win64': 'tx64upd',
		'winarm': 'tarm64upd',
		'other': 'tupdate',
	}[args.target]
	args.out.mkdir(parents=True, exist_ok=True)
	result = args.out / (prefix + str(args.version))
	result.write_bytes(signature + digest + body)

	print('package:      ' + str(result))
	print('version:      ' + str(args.version))
	print('files:        ' + str(len(files)) + ' (' + ', '.join(names) + ')')
	print('uncompressed: ' + str(len(payload)) + ' bytes')
	print('compressed:   ' + str(len(stream)) + ' bytes')
	print('total:        ' + str(result.stat().st_size) + ' bytes')


if __name__ == '__main__':
	main()
