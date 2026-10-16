"""Exercise task-memory replacement failures without touching live state."""
import ctypes
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chrome_task_memory import TaskMemory


class TaskMemoryPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.memory=TaskMemory(self.root)
        self.old=[{'task_id':'T-aaaaaaaaaaaa','state':{'results':[{'verified':True}]}}]
        self.new=[*self.old,{'task_id':'T-bbbbbbbbbbbb'}]
        self.memory.write(self.old)

    def test_transient_denial_preserves_old_receipts_until_atomic_commit(self):
        replace=Path.replace
        attempts=[]
        def denied_then_replace(source,target):
            attempts.append(source)
            self.assertEqual(self.memory.read(),self.old)
            if len(attempts)<3:raise PermissionError('temporary sharing conflict')
            return replace(source,target)
        with patch.object(Path,'replace',denied_then_replace),patch('chrome_task_memory.time.sleep'):
            self.memory.write(self.new)
        self.assertEqual(self.memory.read(),self.new)
        self.assertEqual(len(attempts),3)
        self.assertEqual(list(self.root.glob('*.tmp')),[])

    def test_persistent_denial_is_bounded_and_keeps_original(self):
        with patch.object(Path,'replace',side_effect=PermissionError('persistent denial')) as replace,patch('chrome_task_memory.time.sleep'):
            with self.assertRaises(PermissionError):self.memory.write(self.new)
        self.assertLessEqual(replace.call_count,6)
        self.assertEqual(self.memory.read(),self.old)
        self.assertEqual(list(self.root.glob('*.tmp')),[])

    def test_unrelated_io_error_is_not_retried(self):
        with patch.object(Path,'replace',side_effect=OSError('disk unavailable')) as replace,patch('chrome_task_memory.time.sleep') as sleep:
            with self.assertRaises(OSError):self.memory.write(self.new)
        self.assertEqual(replace.call_count,1)
        sleep.assert_not_called()
        self.assertEqual(self.memory.read(),self.old)
        self.assertEqual(list(self.root.glob('*.tmp')),[])

    def test_does_not_touch_another_writers_temporary_file(self):
        legacy=self.root/'task_memory.tmp'
        legacy.write_text('another pending writer',encoding='utf-8')
        self.memory.write(self.new)
        self.assertEqual(legacy.read_text(encoding='utf-8'),'another pending writer')
        self.assertEqual(self.memory.read(),self.new)

    @unittest.skipUnless(os.name=='nt','Windows sharing semantics')
    def test_real_windows_reader_without_delete_sharing(self):
        from ctypes import wintypes
        api=ctypes.WinDLL('kernel32',use_last_error=True)
        api.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,
                                 ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
        api.CreateFileW.restype=wintypes.HANDLE
        api.CloseHandle.argtypes=[wintypes.HANDLE]
        api.CloseHandle.restype=wintypes.BOOL
        # Allow readers/writers, but deliberately deny delete/rename sharing.
        handle=api.CreateFileW(str(self.memory.path),0x80000000,3,None,3,0x80,None)
        self.assertNotEqual(handle,ctypes.c_void_p(-1).value)
        locked=True
        def release_on_retry(delay):
            nonlocal locked
            self.assertEqual(self.memory.read(),self.old)
            if locked:
                self.assertTrue(api.CloseHandle(handle))
                locked=False
        try:
            with patch('chrome_task_memory.time.sleep',side_effect=release_on_retry) as sleep:
                self.memory.write(self.new)
            self.assertGreaterEqual(sleep.call_count,1)
            self.assertEqual(self.memory.read(),self.new)
        finally:
            if locked:api.CloseHandle(handle)


if __name__=='__main__':unittest.main()
