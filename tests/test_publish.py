import io
import json
from pathlib import Path
import sys
import tarfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from publish import build_package, validate_definition


def source_archive():
    stream = io.BytesIO()
    files = {
        "plugin.py": b'class Plugin:\r\n    version = "1.0.0"\r\n    name = "Example"\r\n',
        "plugin.json": json.dumps({"name": "Example", "version": "1.0.0", "author": "Original", "license": "MIT"}).encode(),
        "LICENSE": b"Original license",
    }
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for name, data in files.items():
            member = tarfile.TarInfo("repo-commit/" + name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return stream.getvalue()


class TestPackages(unittest.TestCase):
    def setUp(self):
        self.definition = {
            "slug": "example", "source_repository": "owner/fork", "source_commit": "a" * 40,
            "version": "1.0.1-rc.1", "prerelease": True,
            "files": ["plugin.py", "plugin.json", "LICENSE"],
        }

    def test_package_identity_version_and_attribution(self):
        package, metadata, contents = build_package(self.definition, source_archive())
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            self.assertEqual(set(archive.namelist()), {"example/plugin.py", "example/plugin.json", "example/LICENSE"})
            self.assertIsNone(archive.testzip())
        namespace = {}
        exec(contents["plugin.py"], namespace)
        self.assertEqual(namespace["Plugin"].version, self.definition["version"])
        self.assertEqual(metadata["version"], self.definition["version"])
        self.assertEqual(metadata["author"], "Original")
        self.assertEqual(contents["LICENSE"], b"Original license")
        self.assertEqual(metadata["repo_url"], "https://github.com/owner/fork")

    def test_same_input_has_stable_package_checksum(self):
        archive = source_archive()
        first = build_package(self.definition, archive)[0]
        second = build_package(self.definition, archive)[0]
        self.assertEqual(first, second)

    def test_fork_display_name_keeps_existing_plugin_identifier(self):
        self.definition["display_name"] = "Example (mwongj fork)"
        package, metadata, contents = build_package(self.definition, source_archive())
        namespace = {}
        exec(contents["plugin.py"], namespace)
        self.assertEqual(namespace["Plugin"].name, "Example (mwongj fork)")
        self.assertEqual(metadata["name"], namespace["Plugin"].name)
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            self.assertIn("example/plugin.py", archive.namelist())

    def test_unpinned_source_is_rejected(self):
        self.definition["source_commit"] = "main"
        with self.assertRaises(ValueError):
            validate_definition(self.definition)

    def test_unsafe_package_paths_are_rejected(self):
        for name in ("../secret", "/secret", "folder/../secret", "folder\\secret"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_definition({**self.definition, "files": ["plugin.py", "plugin.json", name]})


if __name__ == "__main__":
    unittest.main()
