"""Recovery boundary tests. Fixture directories only, no installed app or user data."""
from __future__ import annotations

import errno
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from updates.helper import copy_managed_entry, MANAGED_NAMES, UpgradeTransaction
from updates.manifest import UpdateError, UpdateChannel


class UpdateCopyBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='mio-recovery-boundary-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.program = self.root / 'program'
        self.backup = self.root / 'data' / 'backup'
        self.program.mkdir(); self.backup.mkdir(parents=True)

    def test_cross_volume_copy_only_renames_inside_destination_directory(self):
        source = self.program / '_internal';source.mkdir()
        (source/'module.pyc').write_bytes(b'original compiled fixture')
        destination = self.backup / '_internal'
        real_replace = os.replace
        calls=[]
        def same_volume_only(src, dst):
            src,dst=Path(src),Path(dst);calls.append((src,dst))
            if src.parent != dst.parent:
                raise OSError(errno.EXDEV, 'simulated cross-volume rename')
            return real_replace(src,dst)
        with patch('updates.helper.os.replace',side_effect=same_volume_only):
            copy_managed_entry(source,destination)
        self.assertEqual((destination/'module.pyc').read_bytes(),b'original compiled fixture')
        self.assertTrue(source.is_dir())
        self.assertTrue(calls)
        self.assertTrue(all(src.parent==dst.parent for src,dst in calls))

    def test_copy_failure_preserves_source_and_existing_destination(self):
        source=self.program/'Mio.exe';source.write_bytes(b'old executable fixture')
        destination=self.backup/'Mio.exe';destination.write_bytes(b'previous safe backup')
        def fail(src,dst,*args,**kwargs):
            Path(dst).write_bytes(b'partial')
            raise OSError('simulated disk full')
        with patch('updates.helper.shutil.copy2',side_effect=fail),self.assertRaises(OSError):
            copy_managed_entry(source,destination)
        self.assertEqual(source.read_bytes(),b'old executable fixture')
        self.assertEqual(destination.read_bytes(),b'previous safe backup')
        self.assertFalse(list(self.backup.glob('.mio-copy-*')))

    def test_restoring_tree_replaces_stale_new_files_and_retains_backup(self):
        saved=self.backup/'_internal';saved.mkdir();(saved/'old.pyc').write_bytes(b'old')
        current=self.program/'_internal';current.mkdir();(current/'new.pyc').write_bytes(b'new')
        data=self.program/'Data';data.mkdir();(data/'diary.txt').write_bytes(b'private fixture')
        copy_managed_entry(saved,current)
        self.assertEqual((current/'old.pyc').read_bytes(),b'old')
        self.assertFalse((current/'new.pyc').exists())
        self.assertTrue((saved/'old.pyc').exists())
        self.assertEqual((data/'diary.txt').read_bytes(),b'private fixture')

    def test_data_and_model_roots_not_in_managed_program_allowlist(self):
        for name in ('Data','MioVoice','模型','数据','音色训练','备份','运行数据'):
            self.assertNotIn(name,MANAGED_NAMES)

    def test_symlink_inside_program_is_not_followed(self):
        source=self.program/'_internal';source.mkdir()
        secret=self.root/'external.txt';secret.write_bytes(b'not program content')
        link=source/'asset.txt'
        try:
            link.symlink_to(secret)
        except OSError as exc:
            self.skipTest(f'Windows symlink privilege unavailable: {exc.winerror}')
        with self.assertRaises(UpdateError):
            copy_managed_entry(source,self.backup/'_internal')
        self.assertFalse((self.backup/'_internal').exists())


if __name__ == '__main__':
    unittest.main()
