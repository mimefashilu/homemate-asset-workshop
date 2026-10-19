"""AHM archive directories and byte-preserving XX texture editing."""
from __future__ import annotations

import io
from pathlib import Path
import struct

from PIL import Image


def header_transform(data):
    state = bytearray.fromhex('fa497b1cf94d830a3ae387c2bd1ea6fe')
    result = bytearray(len(data))
    for i, value in enumerate(data):
        lane = i % 8
        state[lane] = (state[lane] + state[lane + 8]) & 255
        result[i] = value ^ state[lane]
    return bytes(result)


def exact(stream, size):
    data = stream.read(size)
    if len(data) != size:
        raise ValueError('Unexpected end of resource')
    return data


def safe_name(name):
    if not isinstance(name, str) or not name or name in ('.', '..') or name.endswith(('.', ' ')) or any(c in name for c in '\\/:\x00\r\n<>"|?*'):
        raise ValueError('Invalid resource name')
    return name


def read_archive(path):
    path = Path(path)
    size = path.stat().st_size
    with path.open('rb') as stream:
        if exact(stream, 4) == b'\0\0\x01\xba':
            return []
        stream.seek(0)
        if header_transform(exact(stream, 1)) != b'\x03':
            raise ValueError('Unsupported PP signature')
        count = struct.unpack('<I', header_transform(exact(stream, 4)))[0]
        if not 0 < count < 100_000 or count * 268 + 9 > size:
            raise ValueError('Invalid PP directory size')
        directory = header_transform(exact(stream, count * 268))
        if struct.unpack('<I', header_transform(exact(stream, 4)))[0] != count * 268 + 9:
            raise ValueError('Invalid PP directory trailer')
    entries, offset, seen = [], count * 268 + 9, set()
    for i in range(count):
        record = directory[i * 268:(i + 1) * 268]
        name = safe_name(record[:260].split(b'\0', 1)[0].decode('cp932'))
        length, position = struct.unpack_from('<II', record, 260)
        if position != offset or position + length > size or name.casefold() in seen:
            raise ValueError('Invalid PP resource range or duplicate name')
        seen.add(name.casefold())
        offset += length
        entries.append(dict(name=name, size=length, offset=position))
    if offset != size:
        raise ValueError('Unaccounted PP bytes')
    return entries


def build_archive(source, entries, replacements, output):
    """Copy unchanged encrypted members verbatim; encryption resets per member."""
    offset = len(entries) * 268 + 9
    directory = bytearray()
    for entry in entries:
        replacement = replacements.get(entry['name'])
        length = replacement.stat().st_size if isinstance(replacement, Path) else len(replacement) if replacement is not None else entry['size']
        directory.extend(safe_name(entry['name']).encode('cp932').ljust(260, b'\0'))
        directory.extend(struct.pack('<II', length, offset))
        offset += length
    with Path(source).open('rb') as original, Path(output).open('xb') as target:
        target.write(header_transform(b'\x03'))
        target.write(header_transform(struct.pack('<I', len(entries))))
        target.write(header_transform(directory))
        target.write(header_transform(struct.pack('<I', len(directory) + 9)))
        for entry in entries:
            if entry['name'] in replacements:
                replacement = replacements[entry['name']]
                if isinstance(replacement, Path):
                    with replacement.open('rb') as stream:
                        for data in iter(lambda: stream.read(1024 * 1024), b''):
                            target.write(data)
                else:
                    target.write(replacement)
            else:
                original.seek(entry['offset'])
                remaining = entry['size']
                while remaining:
                    data = exact(original, min(remaining, 1024 * 1024))
                    target.write(data)
                    remaining -= len(data)
        target.flush()
    read_archive(output)


class Reader:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def take(self, size):
        if size < 0 or self.pos + size > len(self.data):
            raise ValueError('XX field extends beyond resource')
        data = self.data[self.pos:self.pos + size]
        self.pos += size
        return data

    def skip(self, size):
        self.take(size)

    def number(self, fmt='I'):
        return struct.unpack('<' + fmt, self.take(struct.calcsize('<' + fmt)))[0]

    def count(self):
        count = self.number()
        if count > 1_000_000:
            raise ValueError('Implausible XX count')
        return count

    def name(self):
        return bytes(v ^ 255 for v in self.take(self.count())).rstrip(b'\0').decode('cp932')


def read_xx(data):
    reader = Reader(data)
    version = struct.unpack_from('<I', data)[0]
    if not 0 <= version <= 8:
        raise ValueError('Unsupported XX version')
    reader.skip(26 if version else 21)
    vertex_size = 70 if version >= 4 else 52

    def frame(depth=0):
        if depth > 200:
            raise ValueError('Excessive XX frame depth')
        reader.name()
        children = reader.count()
        reader.skip(64 + (32 if version >= 7 else 16))
        meshes = reader.count()
        reader.skip(24 + (64 if version >= 7 else 16))
        if version >= 6:
            reader.name()
        if meshes:
            vectors = reader.number('B')
            for _ in range(meshes):
                reader.skip((64 if version >= 7 else 16) + 4)
                reader.skip(reader.count() * 2)
                vertices = reader.count()
                reader.skip(vertices * vertex_size)
                if version >= 7:
                    reader.skip(20)
                reader.skip(vertices * vectors * 8)
                if version >= 2:
                    reader.skip(100)
                if version >= 7:
                    reader.skip(284)
                    if version >= 8:
                        reader.skip(1)
                        reader.name()
                        reader.skip(16)
                else:
                    for threshold, size in [(3, 64), (5, 20), (6, 28)]:
                        if version >= threshold:
                            reader.skip(size)
            reader.skip(8 + reader.number('H') * vertex_size)
            for _ in range(reader.count()):
                reader.name()
                reader.skip(68)
        for _ in range(children):
            frame(depth + 1)

    frame()
    reader.skip(4)
    materials = []
    for _ in range(reader.count()):
        name = reader.name()
        reader.skip(64)
        power = reader.number('f')
        textures = []
        for _ in range(4):
            textures.append(reader.name())
            reader.skip(16)
        reader.skip(88)
        materials.append(dict(name=name, power=power, textures=textures))
    count = reader.count()
    textures = []
    for index in range(count):
        start = reader.pos
        name = safe_name(reader.name())
        reader.skip(4)
        metadata = reader.pos
        fields = struct.unpack('<7i', reader.take(28))
        checksum = reader.number('B')
        length = reader.number()
        image_offset = reader.pos
        reader.skip(length)
        textures.append(dict(index=index, name=name, width=fields[0], height=fields[1],
                             image_format=fields[6], start=start, metadata=metadata,
                             offset=image_offset, size=length, end=reader.pos, checksum=checksum))
    if version >= 2:
        reader.skip(10)
    if reader.pos != len(data):
        raise ValueError('Unparsed XX bytes')
    return dict(version=version, textures=textures, materials=materials)


def image_bytes(data, texture):
    image = data[texture['offset']:texture['end']]
    return b'BM' + image[2:] if texture['image_format'] == 0 else image


def convert_image(data, extension, alpha_source=None):
    with Image.open(io.BytesIO(data)) as image:
        if image.width > 8192 or image.height > 8192 or image.width * image.height > 32_000_000:
            raise ValueError('Texture exceeds the 8192 dimension / 32 megapixel limit')
        image.load()
        mode = 'RGB' if extension.lower() == '.bmp' else 'RGBA'
        converted = image.convert(mode)
        if mode == 'RGBA' and alpha_source is not None:
            with Image.open(io.BytesIO(alpha_source)) as original:
                if image.width * original.height != image.height * original.width:
                    raise ValueError('保留原透明通道时，新图必须与原贴图的宽高比一致；请保持原 UV 布局，勿裁剪或拉伸。')
                alpha = original.convert('RGBA').getchannel('A')
            if alpha.size != image.size:
                alpha = alpha.resize(image.size, Image.Resampling.BILINEAR)
            converted.putalpha(alpha)
        output = io.BytesIO()
        converted.save(output, format='BMP' if mode == 'RGB' else 'TGA')
        return output.getvalue(), image.size, mode


def replace_xx(data, replacements):
    model = read_xx(data)
    edits = []
    for texture in model['textures']:
        if texture['index'] not in replacements:
            continue
        encoded, dimensions, mode = convert_image(replacements[texture['index']], Path(texture['name']).suffix)
        prefix = bytearray(data[texture['start']:texture['offset']])
        metadata = texture['metadata'] - texture['start']
        fields = struct.unpack_from('<7i', prefix, metadata)
        updated = (dimensions[0], dimensions[1], fields[2], 1, 20 if mode == 'RGB' else 21, fields[5], fields[6])
        struct.pack_into('<7i', prefix, metadata, *updated)
        prefix[metadata + 28] = sum(encoded[::32]) & 255
        struct.pack_into('<I', prefix, metadata + 29, len(encoded))
        payload = b'\0\0' + encoded[2:] if texture['image_format'] == 0 else encoded
        edits.append((texture['start'], texture['end'], bytes(prefix) + payload))
    result = bytearray(data)
    for start, end, replacement in reversed(edits):
        result[start:end] = replacement
    parsed = read_xx(bytes(result))
    if parsed['materials'] != model['materials']:
        raise ValueError('Material references changed during texture editing')
    return bytes(result)
