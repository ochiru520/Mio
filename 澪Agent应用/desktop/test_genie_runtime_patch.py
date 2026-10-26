from __future__ import annotations
import base64
import csv
import hashlib
import importlib.util
from importlib import metadata
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('genie_runtime_patch',ROOT/'scripts/deps/patch-genie-runtime.py')
patch=importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)

class GenieRuntimeMetadataTests(unittest.TestCase):
    def fixture(self, root: Path):
        info=root/'genie_tts-2.0.2.dist-info'
        info.mkdir(parents=True)
        meta=info/'METADATA'
        meta.write_text('Metadata-Version: 2.1\nName: genie-tts\nVersion: 2.0.2\nRequires-Dist: jieba_fast\nRequires-Dist: onnxruntime\n','utf-8')
        code=root/'genie_tts/G2P/Chinese/ChineseG2P.py'
        code.parent.mkdir(parents=True)
        code.write_text('import jieba\n','utf-8')
        with (info/'RECORD').open('w',encoding='utf-8',newline='') as stream:
            csv.writer(stream).writerows([[code.relative_to(root).as_posix(),'',''],[meta.relative_to(root).as_posix(),'',''],['genie_tts-2.0.2.dist-info/RECORD','','']])
        return metadata.PathDistribution(info),code,meta

    def test_correct_dependency_and_verified_record_after_real_file_patch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);dist,code,meta=self.fixture(root)
            patch.patch_distribution_metadata(dist,root,[code])
            self.assertIn('jieba>=0.42.1',metadata.PathDistribution(meta.parent).requires)
            self.assertIn('onnxruntime',metadata.PathDistribution(meta.parent).requires)
            self.assertNotIn('jieba_fast',meta.read_text('utf-8'))
            with (meta.parent/'RECORD').open(encoding='utf-8',newline='') as stream:
                rows={row[0]:row for row in csv.reader(stream)}
            for target in (code,meta):
                row=rows[target.relative_to(root).as_posix()]
                data=target.read_bytes()
                digest=base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip('=')
                self.assertEqual(row[1],'sha256='+digest)
                self.assertEqual(row[2],str(len(data)))

    def test_repeated_patch_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);dist,code,meta=self.fixture(root)
            patch.patch_distribution_metadata(dist,root,[code])
            before={p:p.read_bytes() for p in (meta,meta.parent/'RECORD',code)}
            patch.patch_distribution_metadata(metadata.PathDistribution(meta.parent),root,[code])
            self.assertEqual(before,{p:p.read_bytes() for p in before})

    def test_reject_metadata_from_other_python_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            dist,code,meta=self.fixture(root/'environment-a')
            original=meta.read_bytes()
            with self.assertRaisesRegex(ValueError,'不在指定 Python 环境'):
                patch.patch_distribution_metadata(dist,root/'environment-b',[code])
            self.assertEqual(meta.read_bytes(),original)

if __name__=='__main__':
    unittest.main()
