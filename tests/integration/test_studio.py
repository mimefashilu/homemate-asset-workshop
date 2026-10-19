import io
import os
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

from PIL import Image

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from homemate_workshop.formats import build_archive, image_bytes, read_archive, read_xx, replace_xx
from homemate_workshop.studio import Studio, digest, resource_id


@unittest.skipUnless(os.environ.get('HM_TEST_GAME_DIR'), 'Set HM_TEST_GAME_DIR for private game-copy tests')
class TextureRoundTripTests(unittest.TestCase):
    def setUp(self):
        root = APP / 'workspace' / 'test-runs'
        root.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=root)
        self.root = Path(self.temporary.name).resolve()
        self.allowed_root = root.resolve()
        self.game = self.root / 'game'
        data = self.game / 'data'
        data.mkdir(parents=True)
        source = Path(os.environ['HM_TEST_GAME_DIR']) / 'data'
        selections = {'AHM07_00.pp': {'b_t_01_a_01.bmp', 'b_t_40_a_01.tga'},
                      'AHM05_01.pp': {'b_k_01.xx'}}
        for archive, names in selections.items():
            entries = [e for e in read_archive(source / archive) if e['name'] in names]
            build_archive(source / archive, entries, {}, data / archive)
        ema = read_archive(source / 'AHM00_00.pp')[0]
        self.ema_name = ema['name']
        build_archive(source / 'AHM00_00.pp', [ema], {}, data / 'AHM00_00.pp')
        shutil.copy2(source / 'PPExtractor0.6.2.exe', data / 'PPExtractor0.6.2.exe')
        self.studio = Studio(self.game, self.root / 'workspace')
        # Only the private resource copy is ever written; a running real game is irrelevant.
        self.studio.game_closed = lambda: None
        self.face = resource_id('AHM07_00.pp', 'b_t_01_a_01.bmp')
        self.cloth = resource_id('AHM07_00.pp', 'b_t_40_a_01.tga')
        self.addCleanup(self.cleanup)
        # Existing user textures may already be enlarged. Normalize only this test copy
        # so resolution/pagination assertions have a stable, documented baseline.
        normalized = self.root / 'normalized'
        normalized.mkdir()
        for filename, size in [('b_t_01_a_01.bmp', (256, 256)), ('b_t_40_a_01.tga', (512, 512))]:
            resource = self.studio.decoded('AHM07_00.pp', filename)
            with Image.open(resource) as image:
                image.resize(size, Image.Resampling.BILINEAR).save(normalized / filename)
        self.studio.native(normalized)
        encoded = self.root / 'normalized.pp'
        raw = encoded.read_bytes()
        replacements = {e['name']: raw[e['offset']:e['offset'] + e['size']] for e in read_archive(encoded)}
        archive = data / 'AHM07_00.pp'
        target = self.root / 'normalized-game.pp'
        build_archive(archive, read_archive(archive), replacements, target)
        target.replace(archive)

    def cleanup(self):
        self.studio.executor.shutdown(wait=True)
        if not self.root.is_relative_to(self.allowed_root):
            raise RuntimeError('Test cleanup is outside its workspace')
        self.temporary.cleanup()

    def test_embedded_header_checksum_and_geometry_are_preserved(self):
        source = self.studio.decoded('AHM05_01.pp', 'b_k_01.xx').read_bytes()
        model = read_xx(source)
        face = next(t for t in model['textures'] if t['name'] == 'b_t_01_a_01.bmp')
        hair = next(t for t in model['textures'] if t['name'] == 'b_t_02_a_01.bmp')
        changes = {}
        for texture in (face, hair):
            with Image.open(io.BytesIO(image_bytes(source, texture))) as image:
                image = image.resize((512, 512))
                buffer = io.BytesIO()
                image.save(buffer, 'PNG')
                changes[texture['index']] = buffer.getvalue()
        edited = replace_xx(source, changes)
        updated = read_xx(edited)
        self.assertEqual(source[:model['textures'][0]['start']], edited[:updated['textures'][0]['start']])
        self.assertEqual(model['materials'], updated['materials'])
        for before, after in zip(model['textures'], updated['textures']):
            raw = image_bytes(edited, after)
            self.assertEqual(sum(raw[::32]) & 255, after['checksum'])
            if before['index'] in changes:
                with Image.open(io.BytesIO(raw)) as image:
                    self.assertEqual(image.size, (512, 512))
            else:
                self.assertEqual(image_bytes(source, before), raw)

    def test_external_and_embedded_apply_restore_is_exact(self):
        original_hashes = {name: digest(self.game / 'data' / name) for name in ('AHM07_00.pp', 'AHM05_01.pp')}
        original_cloth = self.studio.resource(self.cloth)['pixels']
        model = read_xx(self.studio.decoded('AHM05_01.pp', 'b_k_01.xx').read_bytes())
        embedded = resource_id('AHM05_01.pp', 'b_k_01.xx', next(t['index'] for t in model['textures'] if t['name'] == 'b_t_01_a_01.bmp'))
        original_widths = {key: self.studio.metadata(key)['width'] for key in (self.face, embedded)}
        self.studio.upscale(self.face, 2)
        self.studio.upscale(embedded, 2)
        result = self.studio.apply({}, lambda *_: None)
        self.assertEqual(result['count'], 2)
        self.assertEqual(self.studio.metadata(self.face)['width'], original_widths[self.face] * 2)
        self.assertEqual(self.studio.metadata(embedded)['width'], original_widths[embedded] * 2)
        self.assertEqual(self.studio.resource(self.cloth)['pixels'], original_cloth)
        self.assertEqual(self.studio.staged, {})
        self.studio.restore({'backup': result['backup']}, lambda *_: None)
        for name, expected in original_hashes.items():
            self.assertEqual(digest(self.game / 'data' / name), expected)

    def test_tga_alpha_and_ema_round_trip(self):
        self.studio.upscale(self.cloth, 2)
        with Image.open(io.BytesIO(self.studio.stage_file(self.cloth).read_bytes())) as image:
            self.assertEqual(image.mode, 'RGBA')
            self.assertEqual(image.size, (1024, 1024))
            self.assertLess(image.getchannel('A').getextrema()[0], 255)
        ema_key = resource_id('AHM00_00.pp', self.ema_name)
        self.studio.upscale(ema_key, 2)
        expected = self.studio.stage_file(ema_key).read_bytes()
        result = self.studio.apply({}, lambda *_: None)
        self.assertEqual(self.studio.resource(ema_key)['pixels'], expected)
        self.studio.restore({'backup': result['backup']}, lambda *_: None)

    def test_patch_export_import_and_version_guard(self):
        self.studio.upscale(self.face, 2)
        result = self.studio.patch({}, lambda *_: None)
        patch = Path(result['path']).read_bytes()
        with zipfile.ZipFile(io.BytesIO(patch)) as zipped:
            self.assertFalse(any(n.endswith('.pp') or n.endswith('.exe') for n in zipped.namelist()))
        self.studio.discard([self.face])
        self.assertEqual(self.studio.import_patch(patch)['count'], 1)
        self.assertEqual(self.studio.staged[self.face]['width'], 512)
        self.studio.discard([self.face])
        archive = self.game / 'data' / 'AHM07_00.pp'
        changed = bytearray(archive.read_bytes())
        changed[-1] ^= 1
        archive.write_bytes(changed)
        with self.assertRaisesRegex(ValueError, '不匹配'):
            self.studio.import_patch(patch)
        self.assertEqual(self.studio.staged, {})

    def test_refuses_to_overwrite_another_patch_on_restore(self):
        self.studio.upscale(self.face, 2)
        result = self.studio.apply({}, lambda *_: None)
        archive = self.game / 'data' / 'AHM07_00.pp'
        changed = bytearray(archive.read_bytes())
        changed[-1] ^= 1
        archive.write_bytes(changed)
        with self.assertRaisesRegex(ValueError, '其他补丁'):
            self.studio.restore({'backup': result['backup']}, lambda *_: None)
        self.assertEqual(archive.read_bytes(), changed)

    def test_bulk_export_contains_real_pixels_and_manifest(self):
        result = self.studio.export({'archive': 'AHM05_01.pp', 'format': 'png'}, lambda *_: None)
        with zipfile.ZipFile(result['path']) as zipped:
            records = json.loads(zipped.read('texture-manifest.json'))
            self.assertEqual(len(records), 17)
            for record in records:
                filename = 'AHM05_01.pp/b_k_01.xx/' + Path(record['name']).stem + '.png'
                with Image.open(io.BytesIO(zipped.read(filename))) as image:
                    image.load()
                    self.assertGreater(image.width, 0)

    def test_member_identifiers_cannot_escape_game_directory(self):
        with self.assertRaises(ValueError):
            self.studio.archive('../AHM07_00.pp')
        with self.assertRaises(ValueError):
            self.studio.resource(resource_id('AHM07_00.pp', '../../save/data.bmp'))

    def test_export_is_saved_locally_without_browser_download(self):
        result = self.studio.export({'keys': [self.face], 'format': 'png'}, lambda *_: None)
        folder = Path(result['directory'])
        self.assertTrue((folder / 'texture-manifest.json').is_file())
        with Image.open(folder / 'b_t_01_a_01.png') as image:
            self.assertEqual(image.size, (256, 256))
        with mock.patch('homemate_workshop.studio.os.startfile') as opener:
            self.studio.open_export(result['export_id'])
            opener.assert_called_once_with(str(folder))
        with self.assertRaises(ValueError):
            self.studio.open_export('../../')

    def test_png_export_import_preserves_tga_pixels_and_alpha(self):
        original = self.studio.resource(self.cloth)['pixels']
        result = self.studio.export({'keys': [self.cloth]}, lambda *_: None)
        png = Path(result['directory']) / 'b_t_40_a_01.png'
        self.assertEqual(result['format'], 'png')
        self.assertEqual(png.read_bytes()[:8], b'\x89PNG\r\n\x1a\n')
        with Image.open(io.BytesIO(original)) as source, Image.open(png) as exported:
            self.assertEqual(exported.format, 'PNG')
            self.assertEqual(exported.mode, 'RGBA')
            self.assertEqual(exported.size, source.size)
            self.assertEqual(exported.tobytes(), source.convert('RGBA').tobytes())
            self.assertLess(exported.getchannel('A').getextrema()[0], 255)
        self.studio.stage(self.cloth, png.read_bytes())
        with Image.open(self.studio.stage_file(self.cloth)) as staged, Image.open(png) as exported:
            self.assertEqual(staged.format, 'TGA')
            self.assertEqual(staged.tobytes(), exported.tobytes())

    def test_native_export_remains_available_and_invalid_format_is_rejected(self):
        result = self.studio.export({'keys': [self.cloth], 'format': 'native'}, lambda *_: None)
        self.assertEqual(result['format'], 'native')
        path = Path(result['directory']) / 'b_t_40_a_01.tga'
        self.assertEqual(path.read_bytes(), self.studio.resource(self.cloth)['pixels'])
        with self.assertRaises(ValueError):
            self.studio.export({'keys': [self.cloth], 'format': 'other'}, lambda *_: None)


    def test_rgb_png_recovers_original_alpha_at_original_and_double_resolution(self):
        with Image.open(io.BytesIO(self.studio.resource(self.cloth)['pixels'])) as source:
            original_alpha = source.convert('RGBA').getchannel('A')
        for scale in (1, 2):
            with self.subTest(scale=scale):
                size = tuple(value * scale for value in original_alpha.size)
                generated = Image.new('RGB', size, (31, 107, 193))
                payload = io.BytesIO()
                generated.save(payload, 'PNG')
                record = self.studio.stage(self.cloth, payload.getvalue(), preserve_alpha=True)
                self.assertEqual(record['alpha_source'], 'original')
                expected = original_alpha.resize(size, Image.Resampling.BILINEAR)
                with Image.open(self.studio.stage_file(self.cloth)) as staged:
                    self.assertEqual(staged.mode, 'RGBA')
                    self.assertEqual(staged.format, 'TGA')
                    self.assertEqual(staged.size, size)
                    self.assertEqual(staged.getchannel('A').tobytes(), expected.tobytes())
                    self.assertEqual(staged.convert('RGB').tobytes(), generated.tobytes())

    def test_import_can_preserve_original_or_use_the_new_alpha(self):
        with Image.open(io.BytesIO(self.studio.resource(self.cloth)['pixels'])) as source:
            original_alpha = source.convert('RGBA').getchannel('A')
        for mode, alpha in (('RGB', 255), ('RGBA', 73)):
            payload = io.BytesIO()
            image = Image.new(mode, original_alpha.size, (90, 140, 210, 73) if mode == 'RGBA' else (90, 140, 210))
            image.save(payload, 'PNG')
            self.studio.stage(self.cloth, payload.getvalue(), preserve_alpha=True)
            with Image.open(self.studio.stage_file(self.cloth)) as staged:
                self.assertEqual(staged.getchannel('A').tobytes(), original_alpha.tobytes())
            record = self.studio.stage(self.cloth, payload.getvalue(), preserve_alpha=False)
            self.assertEqual(record['alpha_source'], 'image')
            with Image.open(self.studio.stage_file(self.cloth)) as staged:
                self.assertEqual(staged.getchannel('A').getextrema(), (alpha, alpha))

    def test_bad_alpha_aspect_ratio_preserves_existing_staged_work(self):
        self.studio.upscale(self.cloth, 2)
        previous_pixels = self.studio.stage_file(self.cloth).read_bytes()
        previous_record = self.studio.staged[self.cloth].copy()
        previous_manifest = self.studio.stage_path.read_bytes()
        payload = io.BytesIO()
        Image.new('RGB', (512, 256)).save(payload, 'PNG')
        with self.assertRaisesRegex(ValueError, '宽高比'):
            self.studio.stage(self.cloth, payload.getvalue(), preserve_alpha=True)
        self.assertEqual(self.studio.stage_file(self.cloth).read_bytes(), previous_pixels)
        self.assertEqual(self.studio.staged[self.cloth], previous_record)
        self.assertEqual(self.studio.stage_path.read_bytes(), previous_manifest)

    def test_bmp_import_ignores_alpha_preservation(self):
        payload = io.BytesIO()
        image = Image.new('RGBA', (512, 256), (90, 140, 210, 73))
        image.save(payload, 'PNG')
        record = self.studio.stage(self.face, payload.getvalue(), preserve_alpha=True)
        self.assertEqual(record['alpha_source'], 'none')
        with Image.open(self.studio.stage_file(self.face)) as staged:
            self.assertEqual(staged.mode, 'RGB')
            self.assertEqual(staged.tobytes(), image.convert('RGB').tobytes())

    def test_restored_alpha_survives_png_export_patch_apply_and_restore(self):
        original_hash = digest(self.game / 'data' / 'AHM07_00.pp')
        original = self.studio.resource(self.cloth)['pixels']
        with Image.open(io.BytesIO(original)) as source:
            alpha = source.convert('RGBA').getchannel('A')
        payload = io.BytesIO()
        generated = Image.new('RGB', (alpha.width * 2, alpha.height * 2), (31, 107, 193))
        generated.save(payload, 'PNG')
        self.studio.stage(self.cloth, payload.getvalue(), preserve_alpha=True)
        expected = self.studio.stage_file(self.cloth).read_bytes()
        result = self.studio.export({'keys': [self.cloth], 'variant': 'staged'}, lambda *_: None)
        self.assertEqual(result['variant'], 'staged')
        with Image.open(Path(result['directory']) / 'b_t_40_a_01.png') as exported, Image.open(io.BytesIO(expected)) as staged:
            self.assertEqual(exported.mode, 'RGBA')
            self.assertEqual(exported.tobytes(), staged.tobytes())
            self.assertLess(exported.getchannel('A').getextrema()[0], 255)
        original_export = self.studio.export({'keys': [self.cloth]}, lambda *_: None)
        with Image.open(Path(original_export['directory']) / 'b_t_40_a_01.png') as exported, Image.open(io.BytesIO(original)) as source:
            self.assertEqual(exported.tobytes(), source.convert('RGBA').tobytes())
        patch = self.studio.patch({}, lambda *_: None)
        self.studio.discard([self.cloth])
        self.studio.import_patch(Path(patch['path']).read_bytes())
        self.assertEqual(self.studio.stage_file(self.cloth).read_bytes(), expected)
        applied = self.studio.apply({}, lambda *_: None)
        self.assertEqual(self.studio.resource(self.cloth)['pixels'], expected)
        self.studio.restore({'backup': applied['backup']}, lambda *_: None)
        self.assertEqual(digest(self.game / 'data' / 'AHM07_00.pp'), original_hash)

    def test_rgb_import_restores_embedded_tga_alpha(self):
        model = read_xx(self.studio.decoded('AHM05_01.pp', 'b_k_01.xx').read_bytes())
        texture = next(t for t in model['textures'] if t['name'].lower().endswith('.tga'))
        key = resource_id('AHM05_01.pp', 'b_k_01.xx', texture['index'])
        with Image.open(io.BytesIO(self.studio.resource(key)['pixels'])) as source:
            original_alpha = source.convert('RGBA').getchannel('A')
        size = (original_alpha.width * 2, original_alpha.height * 2)
        payload = io.BytesIO()
        Image.new('RGB', size, (31, 107, 193)).save(payload, 'PNG')
        self.studio.stage(key, payload.getvalue(), preserve_alpha=True)
        expected = original_alpha.resize(size, Image.Resampling.BILINEAR).tobytes()
        result = self.studio.apply({}, lambda *_: None)
        with Image.open(io.BytesIO(self.studio.resource(key)['pixels'])) as applied:
            self.assertEqual(applied.mode, 'RGBA')
            self.assertEqual(applied.size, size)
            self.assertEqual(applied.getchannel('A').tobytes(), expected)
        self.studio.restore({'backup': result['backup']}, lambda *_: None)

    def test_streamed_batches_apply_selected_textures_and_keep_other_staged_work(self):
        other = resource_id('AHM00_00.pp', self.ema_name)
        self.studio.upscale(other, 2)
        untouched_record = self.studio.staged[other].copy()
        untouched_bytes = self.studio.stage_file(other).read_bytes()
        other_hash = digest(self.game / 'data' / 'AHM00_00.pp')
        target_hash = digest(self.game / 'data' / 'AHM07_00.pp')
        for key in (self.face, self.cloth):
            self.studio.upscale(key, 2)
        expected = {key: self.studio.stage_file(key).read_bytes() for key in (self.face, self.cloth)}
        with mock.patch('homemate_workshop.studio.REPLACEMENT_BATCH_BYTES', 1), mock.patch.object(self.studio, 'game_closed'):
            result = self.studio.apply({'keys': [self.face, self.cloth]}, lambda *_: None)
        self.assertEqual(result['count'], 2)
        self.assertEqual(result['archives'], ['AHM07_00.pp'])
        for key, raw in expected.items():
            self.assertEqual(self.studio.resource(key)['pixels'], raw)
        self.assertEqual(self.studio.staged, {other: untouched_record})
        self.assertEqual(self.studio.stage_file(other).read_bytes(), untouched_bytes)
        self.assertEqual(digest(self.game / 'data' / 'AHM00_00.pp'), other_hash)
        with mock.patch.object(self.studio, 'game_closed'):
            self.studio.restore({'backup': result['backup']}, lambda *_: None)
        self.assertEqual(digest(self.game / 'data' / 'AHM07_00.pp'), target_hash)


if __name__ == '__main__':
    unittest.main()
