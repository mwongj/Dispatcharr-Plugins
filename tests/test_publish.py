import io
import json
from pathlib import Path
import sys
import tarfile
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from publish import build_package, download, set_plugin_attribute, validate_definition


def source_archive():
    stream = io.BytesIO()
    files = {
        "plugin.py": b'# Copyright Original\r\nclass Plugin:\r\n    version = "1.0.0"\r\n    name = "Example"\r\n    help_url = "https://github.com/original/project#readme"\r\n    fields = [{"id": "_about", "description": "Docs: https://github.com/original/project"}]\r\n',
        "plugin.json": json.dumps({"name": "Example", "version": "1.0.0", "author": "Original", "license": "MIT", "repo_url": "https://github.com/original/project", "fields": [{"id": "_about", "description": "Docs: https://github.com/original/project"}]}).encode(),
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
            self.assertTrue(all(entry.create_system == 3 for entry in archive.infolist()))
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

    def test_fork_branding_matches_runtime_and_preserves_attribution(self):
        self.definition.update(author="mwongj", display_name="Example (mwongj fork)")
        source = source_archive()
        package, metadata, contents = build_package(self.definition, source)
        namespace = {}
        exec(contents["plugin.py"], namespace)
        plugin = namespace["Plugin"]
        self.assertEqual(metadata["author"], "mwongj")
        self.assertEqual(plugin.name, metadata["name"])
        self.assertEqual(plugin.help_url, metadata["help_url"])
        self.assertEqual(plugin.fields, metadata["fields"])
        self.assertEqual(plugin.fields[0]["description"], "Docs: https://github.com/owner/fork")
        self.assertEqual(contents["LICENSE"], b"Original license")
        self.assertEqual(metadata["license"], "MIT")
        self.assertIn(b"# Copyright Original", contents["plugin.py"])
        with tarfile.open(fileobj=io.BytesIO(source), mode="r:gz") as archive:
            original = json.load(archive.extractfile("repo-commit/plugin.json"))
        self.assertEqual(original["author"], "Original")
        self.assertEqual(original["repo_url"], "https://github.com/original/project")

    def test_invalid_author_override_is_rejected(self):
        for author in (None, "", " ", 123):
            with self.subTest(author=author), self.assertRaises(ValueError):
                validate_definition({**self.definition, "author": author})

    def test_optional_runtime_help_link_can_be_added(self):
        source = b'class Plugin:\n    """Original documentation."""\n    name = "Example"'
        result = set_plugin_attribute(source, "help_url", "https://github.com/owner/fork#readme", allow_missing=True)
        namespace = {}
        exec(result, namespace)
        self.assertEqual(namespace["Plugin"].help_url, "https://github.com/owner/fork#readme")
        self.assertEqual(namespace["Plugin"].__doc__, "Original documentation.")

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

    def test_download_retries_new_release_asset_404(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b"package"
        with patch("publish.urlopen", side_effect=[HTTPError("https://github.com/asset", 404, "Not Found", {}, None), response]) as request, patch("publish.time.sleep"):
            self.assertEqual(download("https://github.com/asset"), b"package")
            self.assertEqual(request.call_count, 2)


if __name__ == "__main__":
    unittest.main()
