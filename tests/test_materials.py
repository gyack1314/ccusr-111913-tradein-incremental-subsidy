"""资料运输格式检查；不验证业务判断。"""
import json, pathlib, tempfile, unittest
from support.inspect_data import load
class MaterialTests(unittest.TestCase):
    def test_all_materials_are_readable(self):
        manifest,tables=load()
        self.assertEqual(set(tables),{t['name'] for t in manifest['tables']})
        self.assertTrue(all(tables.values()))
    def test_corrupt_digest_is_detected(self):
        import shutil
        source=pathlib.Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            root=pathlib.Path(temporary)
            shutil.copytree(source/'data',root/'data')
            first=json.loads((root/'data/manifest.json').read_text(encoding='utf-8'))['tables'][0]['path']
            with (root/first).open('ab') as f:f.write(b'\n')
            with self.assertRaises(ValueError):load(root)
if __name__=='__main__':unittest.main()
