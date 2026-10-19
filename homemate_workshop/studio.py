"""Local texture browser, export workspace and reversible AHM patch writer."""
from __future__ import annotations

import argparse
import base64
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import threading
from urllib.parse import parse_qs, quote, urlparse
import uuid
import zipfile

from PIL import Image, ImageFilter

from .formats import build_archive, convert_image, exact, image_bytes, read_archive, read_xx, replace_xx, safe_name
from .runtime import run_external, clean_dll_search

APP_DIR = Path(__file__).resolve().parent
IMAGE_EXTENSIONS = {'.bmp', '.tga', '.ema', '.png', '.jpg', '.dds'}
REPLACEMENT_BATCH_BYTES = 64 * 1024 * 1024


def digest(path):
    hasher = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            hasher.update(block)
    return hasher.hexdigest()


def atomic_json(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def resource_id(archive, member, texture=None):
    return base64.urlsafe_b64encode(json.dumps([archive, member, texture], ensure_ascii=False).encode()).decode()


class Studio:
    def __init__(self, game_dir, workspace, extractor=None):
        self.game_dir, self.workspace = Path(game_dir).resolve(), Path(workspace).resolve()
        self.extractor = Path(extractor).resolve() if extractor else self.game_dir / 'data' / 'PPExtractor0.6.2.exe'
        self.workspace.mkdir(parents=True, exist_ok=True)
        for folder in ('cache', 'staged', 'exports', 'backups', 'work'):
            (self.workspace / folder).mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.archive_cache, self.hash_cache = {}, {}
        self.stage_path = self.workspace / 'staged.json'
        self.staged = json.loads(self.stage_path.read_text(encoding='utf-8')) if self.stage_path.exists() else {}
        self.jobs, self.executor = {}, ThreadPoolExecutor(max_workers=1)
        self.token = secrets.token_urlsafe(32)

    def archive(self, name):
        safe_name(name)
        path = self.game_dir / 'data' / name
        if path.suffix.lower() != '.pp' or not path.is_file():
            raise ValueError('Resource archive does not exist')
        stat = path.stat()
        signature = (stat.st_size, stat.st_mtime_ns)
        cached = self.archive_cache.get(name)
        if not cached or cached[0] != signature:
            self.archive_cache[name] = (signature, read_archive(path))
        return path, self.archive_cache[name][1], signature

    def archive_hash(self, name):
        path, _, signature = self.archive(name)
        if name not in self.hash_cache or self.hash_cache[name][0] != signature:
            self.hash_cache[name] = (signature, digest(path))
        return self.hash_cache[name][1]

    def archives(self):
        records = []
        for path in sorted((self.game_dir / 'data').glob('*.pp')):
            try:
                _, entries, _ = self.archive(path.name)
                images = sum(Path(e['name']).suffix.lower() in IMAGE_EXTENSIONS for e in entries)
                models = sum(e['name'].lower().endswith('.xx') for e in entries)
                if images or models:
                    records.append(dict(name=path.name, size=path.stat().st_size, images=images, models=models))
            except ValueError:
                continue
        return records

    def members(self, archive):
        _, entries, _ = self.archive(archive)
        return [dict(name=e['name'], size=e['size'], kind='model' if e['name'].lower().endswith('.xx') else 'image',
                     id=resource_id(archive, e['name'])) for e in entries
                if Path(e['name']).suffix.lower() in IMAGE_EXTENSIONS | {'.xx'}]

    def images(self, archive):
        with self.lock:
            source, entries, signature = self.archive(archive)
            cache_key = hashlib.sha256(repr(('image-sizes-v1', archive, signature)).encode()).hexdigest()
            cache_path = self.workspace / 'cache' / ('sizes-' + cache_key + '.json')
            if cache_path.is_file():
                return json.loads(cache_path.read_text(encoding='utf-8'))
            images = [entry for entry in entries if Path(entry['name']).suffix.lower() in IMAGE_EXTENSIONS]
            records = []
            if images:
                work_root = (self.workspace / 'work').resolve()
                with tempfile.TemporaryDirectory(prefix='image-sizes-', dir=work_root) as temporary:
                    folder = Path(temporary).resolve()
                    if not folder.is_relative_to(work_root):
                        raise ValueError('Image index directory is outside the workspace')
                    batches, current, current_bytes = [], [], 0
                    for entry in images:
                        if current and current_bytes + entry['size'] > REPLACEMENT_BATCH_BYTES:
                            batches.append(current)
                            current, current_bytes = [], 0
                        current.append(entry)
                        current_bytes += entry['size']
                    if current:
                        batches.append(current)
                    for index, batch in enumerate(batches):
                        packed = folder / f'sizes-{index:03d}.pp'
                        build_archive(source, batch, {}, packed)
                        self.native(packed)
                        decoded = packed.with_suffix('')
                        by_stem = defaultdict(list)
                        for path in decoded.iterdir():
                            if path.is_file():
                                by_stem[path.stem.casefold()].append(path)
                        for entry in batch:
                            record = dict(name=entry['name'], size=entry['size'], kind='image',
                                          id=resource_id(archive, entry['name']), width=None, height=None)
                            path = decoded / entry['name']
                            if not path.is_file():
                                candidates = by_stem[entry['name'].casefold()] or by_stem[Path(entry['name']).stem.casefold()]
                                if len(candidates) == 1:
                                    path = candidates[0]
                            try:
                                with Image.open(path) as image:
                                    record.update(width=image.width, height=image.height)
                            except (OSError, ValueError) as error:
                                record['size_error'] = str(error)
                            records.append(record)
            if self.archive(archive)[2] != signature:
                raise ValueError('资源包在读取尺寸时发生变化，请重新选择资源包。')
            atomic_json(cache_path, records)
            return records

    def native(self, argument):
        tool = self.extractor
        if not tool.is_file():
            raise ValueError('没有找到 PPExtractor0.6.2.exe，请在启动器中选择工具或放到游戏 data 目录')
        result = run_external([str(tool), '/AHM', '/noretro', str(argument)], cwd=argument.parent,
                                capture_output=True, timeout=300)
        if result.returncode:
            raise ValueError('PPExtractor failed: ' + (result.stdout + result.stderr).decode('mbcs', errors='replace')[-1000:])

    def decoded(self, archive, member):
        with self.lock:
            source, entries, signature = self.archive(archive)
            matching = [entry for entry in entries if entry['name'] == member]
            if not matching:
                raise ValueError('Resource member does not exist')
            cache_key = hashlib.sha256(repr((archive, signature, member)).encode()).hexdigest()
            folder = self.workspace / 'cache' / cache_key
            metadata = folder / 'decoded.json'
            if not metadata.exists():
                folder.mkdir(exist_ok=True)
                packed = folder / 'source.pp'
                if not packed.exists():
                    build_archive(source, matching, {}, packed)
                self.native(packed)
                results = [p for p in (folder / 'source').iterdir() if p.is_file()]
                if len(results) != 1:
                    raise ValueError('Unexpected extractor output')
                atomic_json(metadata, dict(file=results[0].name))
            name = json.loads(metadata.read_text(encoding='utf-8'))['file']
            return folder / 'source' / safe_name(name)

    def resource(self, key):
        try:
            archive, member, texture_index = json.loads(base64.urlsafe_b64decode(key).decode())
        except Exception as error:
            raise ValueError('Invalid texture identifier') from error
        path = self.decoded(archive, member)
        data = path.read_bytes()
        if member.lower().endswith('.xx'):
            model = read_xx(data)
            if not isinstance(texture_index, int) or not 0 <= texture_index < len(model['textures']):
                raise ValueError('Select an embedded texture')
            texture = model['textures'][texture_index]
            name, pixels = texture['name'], image_bytes(data, texture)
            materials = [m for m in model['materials'] if name in m['textures']]
        else:
            if texture_index is not None or Path(member).suffix.lower() not in IMAGE_EXTENSIONS:
                raise ValueError('Not an image resource')
            name, pixels, materials = path.name, data, []
        return dict(archive=archive, member=member, texture=texture_index, name=name, pixels=pixels,
                    materials=materials, decoded_path=path)

    def textures(self, archive, member):
        path = self.decoded(archive, member)
        model = read_xx(path.read_bytes())
        return [dict(name=t['name'], width=t['width'], height=t['height'], kind='image',
                     size=t['size'], id=resource_id(archive, member, t['index'])) for t in model['textures']]

    def stage_file(self, key):
        return self.workspace / 'staged' / (hashlib.sha256(key.encode()).hexdigest() + '.bin')

    def metadata(self, key):
        resource = self.resource(key)
        with Image.open(io.BytesIO(resource['pixels'])) as image:
            result = {k: resource[k] for k in ('archive', 'member', 'texture', 'name', 'materials')}
            result.update(width=image.width, height=image.height, mode=image.mode, format=image.format,
                          sha256=hashlib.sha256(resource['pixels']).hexdigest(), staged=self.staged.get(key))
        return result

    def png(self, key, variant='original', thumb=0):
        resource = self.resource(key)
        data = self.stage_file(key).read_bytes() if variant == 'staged' and key in self.staged else resource['pixels']
        with Image.open(io.BytesIO(data)) as image:
            image = image.convert('RGBA')
            if thumb:
                image.thumbnail((min(thumb, 1024), min(thumb, 1024)))
            output = io.BytesIO()
            image.save(output, format='PNG')
        return output.getvalue()

    def stage(self, key, data, method='import', preserve_alpha=False):
        with self.lock:
            resource = self.resource(key)
            extension = Path(resource['name']).suffix.lower()
            if extension not in {'.bmp', '.tga'}:
                raise ValueError('Writing currently supports BMP and TGA textures')
            original_alpha = resource['pixels'] if preserve_alpha and extension == '.tga' else None
            encoded, dimensions, mode = convert_image(data, extension, alpha_source=original_alpha)
            if dimensions[0] * dimensions[1] == 0:
                raise ValueError('Empty image')
            existing = self.staged.get(key)
            base_hash = self.archive_hash(resource['archive'])
            if existing and existing['archive_hash'] != base_hash:
                raise ValueError('Archive changed; discard the old staged replacement before importing again')
            self.stage_file(key).write_bytes(encoded)
            record = dict(archive=resource['archive'], member=resource['member'], texture=resource['texture'],
                          name=resource['name'], export_name=resource['decoded_path'].name,
                          archive_hash=base_hash, width=dimensions[0], height=dimensions[1], mode=mode,
                          method=method, sha256=hashlib.sha256(encoded).hexdigest(),
                          alpha_source='original' if original_alpha is not None else 'image' if mode == 'RGBA' else 'none')
            self.staged[key] = record
            atomic_json(self.stage_path, self.staged)
            return record

    def upscale(self, key, scale):
        if scale not in (2, 4):
            raise ValueError('Upscale factor must be 2 or 4')
        resource = self.resource(key)
        with Image.open(io.BytesIO(resource['pixels'])) as original:
            size = (original.width * scale, original.height * scale)
            if max(size) > 8192 or size[0] * size[1] > 32_000_000:
                raise ValueError('Upscaled texture is too large')
            rgba = original.convert('RGBA')
            enlarged = rgba.convert('RGBa').resize(size, Image.Resampling.LANCZOS).convert('RGBA')
            rgb = enlarged.convert('RGB').filter(ImageFilter.UnsharpMask(radius=1.1, percent=55, threshold=3))
            rgb.putalpha(rgba.getchannel('A').resize(size, Image.Resampling.BILINEAR))
            output = io.BytesIO()
            rgb.save(output, format='PNG')
        return self.stage(key, output.getvalue(), f'lanczos-{scale}x')

    def discard(self, keys):
        with self.lock:
            for key in keys:
                self.staged.pop(key, None)
                self.stage_file(key).unlink(missing_ok=True)
            atomic_json(self.stage_path, self.staged)

    def game_closed(self):
        if os.name != 'nt':
            return
        names = {'@homemate.exe'} | {p.name.casefold() for p in self.game_dir.glob('*3DM*.exe')}
        result = run_external(['tasklist', '/FO', 'CSV', '/NH'], capture_output=True, timeout=30)
        if result.returncode:
            raise ValueError('无法检查游戏进程。请用桌面的启动脚本运行贴图工坊，再执行写入或恢复。')
        rows = csv.reader(io.StringIO(result.stdout.decode('mbcs', errors='replace')))
        if any(row and row[0].casefold() in names for row in rows):
            raise ValueError('请先完全退出游戏，再写入或恢复资源包。')

    def job(self, action, arguments):
        job_id = uuid.uuid4().hex
        self.jobs[job_id] = dict(status='running', progress=0, message='准备中')

        def progress(value, message):
            self.jobs[job_id].update(progress=value, message=message)

        def run():
            try:
                operations = {'export': self.export, 'apply': self.apply, 'restore': self.restore,
                              'patch': self.patch, 'upscale': self.batch_upscale}
                result = operations[action](arguments, progress)
                self.jobs[job_id].update(status='done', progress=100, message='已完成', result=result)
            except Exception as error:
                self.jobs[job_id].update(status='error', message=str(error))
        self.executor.submit(run)
        return job_id

    def batch_upscale(self, arguments, progress):
        keys = arguments['keys']
        for i, key in enumerate(keys):
            self.upscale(key, arguments.get('scale', 2))
            progress(round((i + 1) * 100 / len(keys)), f'处理 {i + 1}/{len(keys)}')
        return dict(count=len(keys))

    def export(self, arguments, progress):
        export_format = arguments.get('format', 'png')
        if export_format not in {'png', 'native'}:
            raise ValueError('请选择 PNG 或原始格式。')
        variant = arguments.get('variant', 'original')
        if variant not in {'original', 'staged'}:
            raise ValueError('请选择原图或待写入贴图。')
        keys = arguments.get('keys', [])
        archive = arguments.get('archive')
        if archive:
            keys = []
            members = self.members(archive)
            for i, member in enumerate(members):
                if member['kind'] == 'model':
                    keys.extend(t['id'] for t in self.textures(archive, member['name']))
                else:
                    keys.append(member['id'])
                progress(round((i + 1) * 35 / len(members)), f'索引 {i + 1}/{len(members)}')
        if not keys:
            raise ValueError('请先选择贴图。')
        keys = list(dict.fromkeys(keys))
        export_id = datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:8]
        folder_root = self.workspace / 'exports' / export_id
        folder_root.mkdir()
        output = self.workspace / 'exports' / (export_id + '.zip')
        records = []
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as zipped:
            for i, key in enumerate(keys):
                resource = self.resource(key)
                native_data = self.stage_file(key).read_bytes() if variant == 'staged' and key in self.staged else resource['pixels']
                filename = resource['name']
                if export_format == 'png':
                    with Image.open(io.BytesIO(native_data)) as image:
                        buffer = io.BytesIO()
                        image.save(buffer, format='PNG')
                        native_data = buffer.getvalue()
                    filename = Path(filename).stem + '.png'
                folder = resource['member'] if resource['texture'] is not None else '_images'
                relative_path = filename if len(keys) == 1 else f"{resource['archive']}/{folder}/{filename}"
                extracted = folder_root / relative_path
                extracted.parent.mkdir(parents=True, exist_ok=True)
                extracted.write_bytes(native_data)
                zipped.writestr(relative_path, native_data)
                records.append(dict(id=key, archive=resource['archive'], member=resource['member'],
                                    texture=resource['texture'], name=resource['name'], file=relative_path))
                progress(35 + round((i + 1) * 65 / len(keys)), f'提取 {i + 1}/{len(keys)}')
            manifest = json.dumps(records, ensure_ascii=False, indent=2)
            zipped.writestr('texture-manifest.json', manifest)
            (folder_root / 'texture-manifest.json').write_text(manifest, encoding='utf-8')
        return dict(count=len(records), download='/api/textures/download/' + output.name, path=str(output),
                    directory=str(folder_root), export_id=export_id, format=export_format, variant=variant)

    def patch(self, arguments, progress):
        with self.lock:
            if not self.staged:
                raise ValueError('没有待写入的贴图。')
            output = self.workspace / 'exports' / (uuid.uuid4().hex + '.zip')
            records = []
            with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as zipped:
                for i, (key, record) in enumerate(self.staged.items()):
                    filename = hashlib.sha256(key.encode()).hexdigest() + Path(record['name']).suffix
                    zipped.write(self.stage_file(key), 'textures/' + filename)
                    records.append(dict(id=key, file='textures/' + filename, **record))
                    progress(round((i + 1) * 100 / len(self.staged)), '打包修改贴图')
                zipped.writestr('patch-manifest.json', json.dumps(dict(version=1, textures=records), ensure_ascii=False, indent=2))
            return dict(count=len(records), download='/api/textures/download/' + output.name, path=str(output),
                        directory=str(output.parent), export_id=output.stem)

    def open_export(self, export_id):
        name = safe_name(export_id)
        folder = self.workspace / 'exports' / name
        if folder.is_dir():
            destination = folder
        elif folder.with_suffix('.zip').is_file():
            destination = folder.parent
        else:
            raise ValueError('Export does not exist')
        with clean_dll_search():
            os.startfile(str(destination))
        return dict(directory=str(destination))

    def import_patch(self, data):
        with self.lock, zipfile.ZipFile(io.BytesIO(data)) as zipped:
            manifest = json.loads(zipped.read('patch-manifest.json'))
            if manifest.get('version') != 1 or not 0 < len(manifest.get('textures', [])) <= 4000:
                raise ValueError('Unsupported patch manifest')
            if sum(zipped.getinfo(r['file']).file_size for r in manifest['textures']) > 512 * 1024 * 1024:
                raise ValueError('Patch exceeds the 512 MB unpacked texture limit')
            planned = []
            for record in manifest['textures']:
                info = zipped.getinfo(record['file'])
                if info.file_size > 40 * 1024 * 1024:
                    raise ValueError('Patch texture is too large')
                key = resource_id(record['archive'], record['member'], record['texture'])
                resource = self.resource(key)
                if resource['name'] != record['name'] or self.archive_hash(record['archive']) != record['archive_hash']:
                    raise ValueError('补丁与当前游戏资源版本不匹配。')
                image = zipped.read(record['file'])
                if hashlib.sha256(image).hexdigest() != record['sha256']:
                    raise ValueError('Patch texture checksum does not match')
                convert_image(image, Path(record['name']).suffix)
                planned.append((key, image))
            for key, image in planned:
                self.stage(key, image, 'patch')
            return dict(count=len(planned))

    def replacements(self, archive, records, work, progress=None):
        members = defaultdict(list)
        for key, record in records:
            members[record['member']].append((key, record))
        batches, current, current_bytes = [], [], 0
        for group in members.values():
            size = sum(self.stage_file(key).stat().st_size for key, _ in group)
            if current and current_bytes + size > REPLACEMENT_BATCH_BYTES:
                batches.append(current)
                current, current_bytes = [], 0
            current.extend(group)
            current_bytes += size
        if current:
            batches.append(current)
        if len(batches) > 1:
            encoded_files = work / 'encoded'
            encoded_files.mkdir()
            replacements = {}
            for index, batch in enumerate(batches):
                batch_work = work / f'batch-{index:03d}'
                batch_work.mkdir()
                encoded = self.replacements(archive, batch, batch_work)
                for name, data in encoded.items():
                    destination = encoded_files / safe_name(name)
                    destination.write_bytes(data)
                    replacements[name] = destination
                del encoded
                if progress:
                    progress((index + 1) / len(batches), f'分批编码与校验 {archive} · {index + 1}/{len(batches)}')
            return replacements
        incoming = work / 'incoming'
        incoming.mkdir()
        groups = defaultdict(dict)
        for key, record in records:
            data = self.stage_file(key).read_bytes()
            if hashlib.sha256(data).hexdigest() != record['sha256']:
                raise ValueError('Staged texture changed outside the tool')
            if record['texture'] is not None:
                groups[record['member']][record['texture']] = data
            else:
                (incoming / safe_name(record['export_name'])).write_bytes(data)
        for member, textures in groups.items():
            source = self.decoded(archive, member).read_bytes()
            (incoming / member).write_bytes(replace_xx(source, textures))
        expected_plaintext = {path.name: path.read_bytes() for path in incoming.iterdir()}
        self.native(incoming)
        packed = work / 'incoming.pp'
        encoded = read_archive(packed)
        expected = {record['member'] for _, record in records}
        if {e['name'] for e in encoded} != expected:
            raise ValueError('Encoded archive member list changed')
        with packed.open('rb') as stream:
            replacements = {}
            for entry in encoded:
                stream.seek(entry['offset'])
                replacements[entry['name']] = exact(stream, entry['size'])
        # Validate the native encoder by decoding its output before touching game data.
        verification = work / 'verification.pp'
        shutil.copy2(packed, verification)
        self.native(verification)
        for name, plaintext in expected_plaintext.items():
            restored = work / 'verification' / name
            if not restored.is_file() or restored.read_bytes() != plaintext:
                raise ValueError('Round-trip resource content changed')
        if progress:
            progress(1, '编码与校验 ' + archive)
        return replacements

    def apply(self, arguments, progress):
        with self.lock:
            self.game_closed()
            if not self.staged:
                raise ValueError('没有待写入的贴图。')
            keys = arguments.get('keys', list(self.staged))
            if not isinstance(keys, list) or not keys or any(key not in self.staged for key in keys):
                raise ValueError('请选择有效的待写入贴图。')
            keys = list(dict.fromkeys(keys))
            grouped = defaultdict(list)
            for key in keys:
                record = self.staged[key]
                grouped[record['archive']].append((key, record))
            backup_id = datetime.now().strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:8]
            backup_dir = self.workspace / 'backups' / backup_id
            backup_dir.mkdir()
            planned, installed = [], []
            try:
                for i, (archive, records) in enumerate(grouped.items()):
                    source, entries, _ = self.archive(archive)
                    original_hash = digest(source)
                    if any(r['archive_hash'] != original_hash for _, r in records):
                        raise ValueError('资源包在导入后发生变化，请清除旧修改后重新导入。')
                    work = self.workspace / 'work' / uuid.uuid4().hex
                    work.mkdir()
                    encoded = self.replacements(archive, records, work,
                        lambda fraction, message: progress(round((i * 80 + fraction * 70) / len(grouped)), message))
                    target = source.parent / ('.studio-' + uuid.uuid4().hex + '.pp')
                    planned.append(dict(archive=archive, temporary=str(target), original=original_hash))
                    build_archive(source, entries, encoded, target)
                    if digest(source) != original_hash:
                        raise ValueError('Resource archive changed while preparing the patch')
                    shutil.copy2(source, backup_dir / archive)
                    planned[-1]['patched'] = digest(target)
                    progress(round((i + 1) * 80 / len(grouped)), f'校验并备份 {archive}')
                manifest = dict(id=backup_id, status='prepared', files=planned, textures=[self.staged[key] for key in keys])
                atomic_json(backup_dir / 'manifest.json', manifest)
                self.game_closed()
                for entry in planned:
                    source = self.game_dir / 'data' / entry['archive']
                    if digest(source) != entry['original']:
                        raise ValueError('Resource archive changed before installation')
                    os.replace(entry['temporary'], source)
                    installed.append(entry)
                manifest['status'] = 'installed'
                atomic_json(backup_dir / 'manifest.json', manifest)
                count = len(keys)
                self.discard(keys)
                return dict(count=count, backup=backup_id, archives=list(grouped))
            except Exception:
                for entry in installed:
                    source = self.game_dir / 'data' / entry['archive']
                    if digest(source) == entry['patched']:
                        shutil.copy2(backup_dir / entry['archive'], source)
                for entry in planned:
                    Path(entry['temporary']).unlink(missing_ok=True)
                raise

    def backups(self):
        return [json.loads(p.read_text(encoding='utf-8')) for p in sorted(
            (self.workspace / 'backups').glob('*/manifest.json'), reverse=True)]

    def restore(self, arguments, progress):
        with self.lock:
            self.game_closed()
            backup_id = safe_name(arguments['backup'])
            folder = self.workspace / 'backups' / backup_id
            manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
            for entry in manifest['files']:
                archive = safe_name(entry['archive'])
                source = self.game_dir / 'data' / archive
                if digest(folder / archive) != entry['original']:
                    raise ValueError('Backup archive checksum does not match')
                if digest(source) not in {entry['original'], entry['patched']}:
                    raise ValueError('资源包已被其他补丁修改，不能直接覆盖恢复。')
            for i, entry in enumerate(manifest['files']):
                source = self.game_dir / 'data' / entry['archive']
                temporary = source.parent / ('.studio-' + uuid.uuid4().hex + '.pp')
                shutil.copy2(folder / entry['archive'], temporary)
                os.replace(temporary, source)
                progress(round((i + 1) * 100 / len(manifest['files'])), '恢复原资源包')
            manifest['status'] = 'restored'
            atomic_json(folder / 'manifest.json', manifest)
            return dict(backup=backup_id)


class Handler(BaseHTTPRequestHandler):
    def handle(self):
        try:
            super().handle()
        except (ConnectionResetError, BrokenPipeError):
            pass

    def log_message(self, *args):
        pass

    @property
    def studio(self):
        return self.server.studio

    def send(self, data, content_type='application/json; charset=utf-8', status=200, filename=None):
        if not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        if filename:
            self.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + quote(filename))
        self.end_headers()
        self.wfile.write(data)

    def validate(self, mutation=False):
        valid_hosts = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        if self.headers.get('Host') not in valid_hosts:
            raise ValueError('Invalid local host')
        if mutation:
            if self.headers.get('X-Studio-Token') != self.studio.token:
                raise ValueError('Invalid local request token')
            origin = self.headers.get('Origin')
            if origin and origin not in {'http://' + h for h in valid_hosts}:
                raise ValueError('Invalid request origin')

    def do_GET(self):
        try:
            self.validate()
            parsed = urlparse(self.path)
            query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            route = parsed.path
            if route == '/api/state':
                self.send(dict(service='HomeMate Texture Studio', token=self.studio.token,
                               game_dir=str(self.studio.game_dir), workspace=str(self.studio.workspace),
                               archives=self.studio.archives(), staged=self.studio.staged, backups=self.studio.backups()))
            elif route == '/api/members':
                self.send(self.studio.members(query['archive']))
            elif route == '/api/images':
                self.send(self.studio.images(query['archive']))
            elif route == '/api/textures':
                self.send(self.studio.textures(query['archive'], query['member']))
            elif route == '/api/meta':
                self.send(self.studio.metadata(query['id']))
            elif route == '/api/image':
                self.send(self.studio.png(query['id'], query.get('variant'), int(query.get('thumb', 0))), 'image/png')
            elif route == '/api/native':
                resource = self.studio.resource(query['id'])
                self.send(resource['pixels'], 'application/octet-stream', filename=resource['name'])
            elif route.startswith('/api/job/'):
                self.send(self.studio.jobs[route.rsplit('/', 1)[1]])
            elif route.startswith('/api/download/'):
                name = safe_name(route.rsplit('/', 1)[1])
                path = self.studio.workspace / 'exports' / name
                self.send(path.read_bytes(), 'application/zip', filename='homemate-' + name[:8] + '.zip')
            else:
                name = 'index.html' if route == '/' else safe_name(route.lstrip('/'))
                path = APP_DIR / 'web' / 'texture' / name
                mime = {'.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8', '.js': 'text/javascript; charset=utf-8'}[path.suffix]
                self.send(path.read_bytes(), mime)
        except (ValueError, KeyError, OSError) as error:
            self.send(dict(error=str(error)), status=400)
        except Exception as error:
            self.send(dict(error=str(error)), status=500)

    def do_POST(self):
        try:
            self.validate(mutation=True)
            size = int(self.headers.get('Content-Length', 0))
            if not 0 < size <= 64 * 1024 * 1024:
                raise ValueError('Invalid request size')
            arguments = json.loads(exact(self.rfile, size))
            route = urlparse(self.path).path
            if route == '/api/import':
                data = base64.b64decode(arguments['data'], validate=True)
                preserve_alpha = arguments.get('preserve_alpha', True)
                if not isinstance(preserve_alpha, bool):
                    raise ValueError('保留原透明通道选项必须为布尔值。')
                self.send(self.studio.stage(arguments['id'], data, preserve_alpha=preserve_alpha))
            elif route == '/api/import-patch':
                self.send(self.studio.import_patch(base64.b64decode(arguments['data'], validate=True)))
            elif route == '/api/discard':
                self.studio.discard(arguments['keys'])
                self.send(dict(ok=True))
            elif route == '/api/open-export':
                self.send(self.studio.open_export(arguments['export_id']))
            elif route == '/api/job':
                action = arguments.pop('action')
                if action not in {'apply', 'restore', 'export', 'patch', 'upscale'}:
                    raise ValueError('Unknown operation')
                self.send(dict(job=self.studio.job(action, arguments)))
            else:
                raise ValueError('Unknown route')
        except (ValueError, KeyError, OSError) as error:
            self.send(dict(error=str(error)), status=400)
        except Exception as error:
            self.send(dict(error=str(error)), status=500)
