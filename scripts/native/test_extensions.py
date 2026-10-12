"""Bundle metadata must accept complete SQL upgrade paths and reject broken ones."""
from pathlib import Path
import tempfile
import unittest
from bundle import extension_version

class ExtensionScripts(unittest.TestCase):
    def test_base_and_upgrade_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'example.control').write_text("default_version = '1.3'\n")
            (root/'example--1.0.sql').touch()
            (root/'example--1.0--1.1.sql').touch()
            (root/'example--1.2--1.3.sql').touch()
            with self.assertRaises(ValueError):extension_version(root,'example')
            (root/'example--1.1--1.2.sql').touch()
            self.assertEqual(extension_version(root,'example'),'1.3')
            (root/'example--1.3.sql').touch()
            (root/'example--1.0.sql').unlink()
            self.assertEqual(extension_version(root,'example'),'1.3')

if __name__=='__main__':unittest.main()
