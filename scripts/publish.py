"""Build pinned plugin forks and publish a Dispatcharr manifest feed.

Uses Python's standard library. Releases are uploaded before the feed changes.
An existing version must retain exactly the same package bytes.
"""
import argparse
import ast
import base64
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import time
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def encode_json(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode()


def download(url, attempts=6):
    # New release assets can briefly return 404 while GitHub propagates them.
    for attempt in range(attempts):
        try:
            with urlopen(Request(url, headers={"User-Agent": "mwongj-plugin-publisher"}), timeout=120) as response:
                return response.read()
        except HTTPError as error:
            if error.code not in (404, 500, 502, 503) or attempt == attempts - 1:
                raise
            time.sleep(2)


def github_token():
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        return token
    result = subprocess.run(
        ["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
        text=True, capture_output=True, check=True,
    )
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)["password"]


class GitHub:
    def __init__(self, token):
        self.token = token

    def request(self, path, data=None, method=None, binary=False, missing_ok=False):
        url = path if path.startswith("https://") else "https://api.github.com" + path
        if not url.startswith(("https://api.github.com/", "https://uploads.github.com/")):
            raise ValueError("Unexpected GitHub API host")
        body = data if binary else (encode_json(data) if data is not None else None)
        request = Request(url, data=body, method=method, headers={
            "Authorization": "Bearer " + self.token,
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/zip" if binary else "application/json",
            "User-Agent": "mwongj-plugin-publisher",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        try:
            with urlopen(request, timeout=120) as response:
                return json.load(response)
        except HTTPError as error:
            if missing_ok and error.code == 404:
                return None
            raise RuntimeError(f"GitHub API {method or 'GET'} {url}: HTTP {error.code}: {error.read().decode()}") from None


def validate_definition(definition):
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", definition["slug"]):
        raise ValueError("Plugin slug must be lowercase kebab-case")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", definition["source_repository"]):
        raise ValueError("Invalid source repository")
    if not re.fullmatch(r"[0-9a-f]{40}", definition["source_commit"]):
        raise ValueError("Pin source_commit to a full Git commit SHA")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?", definition["version"]):
        raise ValueError("Invalid release version")
    if not isinstance(definition.get("prerelease", False), bool):
        raise ValueError("prerelease must be a boolean")
    if "author" in definition and (not isinstance(definition["author"], str) or not definition["author"].strip()):
        raise ValueError("author must be a nonempty string")
    paths = definition["files"]
    if len(paths) != len(set(paths)):
        raise ValueError("Duplicate package file")
    for name in paths:
        if not name or "\\" in name or PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts:
            raise ValueError("Unsafe package path")
    if not {"plugin.py", "plugin.json"}.issubset(paths):
        raise ValueError("Package must include plugin.py and plugin.json")


def set_plugin_attribute(source, attribute, value, allow_missing=False):
    text = source.decode("utf-8")
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Plugin":
            for assignment in node.body:
                if isinstance(assignment, ast.Assign) and any(isinstance(t, ast.Name) and t.id == attribute for t in assignment.targets):
                    lines = text.splitlines(keepends=True)
                    original = lines[assignment.lineno - 1]
                    ending = "\r\n" if original.endswith("\r\n") else "\n"
                    indent = original[:len(original) - len(original.lstrip())]
                    lines[assignment.lineno - 1:assignment.end_lineno] = [indent + attribute + " = " + json.dumps(value) + ending]
                    result = "".join(lines).encode("utf-8")
                    compile(result, "plugin.py", "exec")
                    return result
            if allow_missing:
                lines = text.splitlines(keepends=True)
                indent = " " * node.body[0].col_offset
                ending = "\r\n" if lines[node.lineno - 1].endswith("\r\n") else "\n"
                if not lines[node.end_lineno - 1].endswith("\n"):
                    lines[node.end_lineno - 1] += ending
                lines.insert(node.end_lineno, indent + attribute + " = " + json.dumps(value) + ending)
                result = "".join(lines).encode("utf-8")
                compile(result, "plugin.py", "exec")
                return result
    raise ValueError(f"Could not find Plugin.{attribute}")


def build_package(definition, archive):
    validate_definition(definition)
    contents = {}
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as source:
        members = source.getmembers()
        prefix = members[0].name.split("/")[0] + "/"
        for name in definition["files"]:
            member = source.getmember(prefix + name)
            if not member.isfile():
                raise ValueError(f"Package entry is not a regular file: {name}")
            contents[name] = source.extractfile(member).read()
    metadata = json.loads(contents["plugin.json"])
    source_repo_url = metadata.get("repo_url")
    metadata["version"] = definition["version"]
    metadata["repo_url"] = "https://github.com/" + definition["source_repository"]
    metadata["help_url"] = metadata["repo_url"] + "#readme"
    # Brand the distribution without editing shared source or attribution files.
    if definition.get("author"):
        metadata["author"] = definition["author"]
    if source_repo_url:
        for field in metadata.get("fields", []):
            if field.get("id") == "_about" and isinstance(field.get("description"), str):
                field["description"] = field["description"].replace(source_repo_url, metadata["repo_url"])
        contents["plugin.py"] = contents["plugin.py"].replace(source_repo_url.encode("utf-8"), metadata["repo_url"].encode("utf-8"))
    contents["plugin.py"] = set_plugin_attribute(contents["plugin.py"], "version", definition["version"])
    contents["plugin.py"] = set_plugin_attribute(contents["plugin.py"], "help_url", metadata["help_url"], allow_missing=True)
    if definition.get("display_name"):
        metadata["name"] = definition["display_name"]
        contents["plugin.py"] = set_plugin_attribute(contents["plugin.py"], "name", definition["display_name"])
    contents["plugin.json"] = encode_json(metadata)
    package = io.BytesIO()
    # Store files to keep package checksums stable across zlib implementations.
    with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_STORED) as output:
        for name, data in sorted(contents.items()):
            entry = zipfile.ZipInfo(definition["slug"] + "/" + name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.compress_type = zipfile.ZIP_STORED
            entry.external_attr = 0o100644 << 16
            output.writestr(entry, data)
    return package.getvalue(), metadata, contents


def read_feed(api, repo, branch, path):
    result = api.request(f"/repos/{repo}/contents/{path}?ref={branch}", missing_ok=True)
    return json.loads(base64.b64decode(result["content"])) if result else None


def publish_feed(api, repo, branch, files, message):
    prefix = f"/repos/{repo}/git"
    ref = api.request(prefix + "/ref/heads/" + branch, missing_ok=True)
    parents = [ref["object"]["sha"]] if ref else []
    previous = api.request(prefix + "/commits/" + parents[0]) if parents else None
    entries = []
    for path, data in files.items():
        blob = api.request(prefix + "/blobs", {"content": base64.b64encode(data).decode(), "encoding": "base64"})
        entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    tree_data = {"tree": entries}
    if previous:
        tree_data["base_tree"] = previous["tree"]["sha"]
    tree = api.request(prefix + "/trees", tree_data)
    if previous and tree["sha"] == previous["tree"]["sha"]:
        print("Feed already matches published releases")
        return
    commit = api.request(prefix + "/commits", {"message": message, "tree": tree["sha"], "parents": parents})
    if ref:
        api.request(prefix + "/refs/heads/" + branch, {"sha": commit["sha"], "force": False}, method="PATCH")
    else:
        api.request(prefix + "/refs", {"ref": "refs/heads/" + branch, "sha": commit["sha"]})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin", default="", help="One configured slug; default publishes all")
    parser.add_argument("--dry-run", action="store_true", help="Build ZIPs locally without publishing")
    args = parser.parse_args()
    registry = json.loads((ROOT / "registry.json").read_text())
    repo, branch = registry["repository"], registry["manifest_branch"]
    definitions = sorted((ROOT / "plugins").glob("*.json"))
    if args.plugin:
        definitions = [path for path in definitions if path.stem == args.plugin]
    if not definitions:
        raise ValueError("No matching plugin definitions")
    api = None if args.dry_run else GitHub(github_token())
    current = read_feed(api, repo, branch, "manifest.json") if api else None
    root_manifest = current or {"manifest": {
        "registry_url": "https://github.com/" + repo,
        "registry_name": registry["registry_name"],
        "root_url": "https://github.com/" + repo + "/releases/download",
        "plugins": [],
    }}
    feed = root_manifest["manifest"]
    feed["registry_name"] = registry["registry_name"]
    files = {}
    for path in definitions:
        definition = json.loads(path.read_text())
        validate_definition(definition)
        if path.stem != definition["slug"]:
            raise ValueError("Definition filename must match slug")
        slug, version = definition["slug"], definition["version"]
        source_url = f"https://codeload.github.com/{definition['source_repository']}/tar.gz/{definition['source_commit']}"
        source_archive = download(source_url)
        package, metadata, contents = build_package(definition, source_archive)
        checksum = hashlib.sha256(package).hexdigest()
        tag = f"{slug}-{version}"
        asset_name = tag + ".zip"
        (ROOT / "dist").mkdir(exist_ok=True)
        (ROOT / "dist" / asset_name).write_bytes(package)
        print(f"Built {asset_name} ({len(package)} bytes), SHA256 {checksum}")
        if args.dry_run:
            continue
        release_path = f"/repos/{repo}/releases"
        release = api.request(release_path + "/tags/" + quote(tag), missing_ok=True)
        if not release:
            release = api.request(release_path, {
                "tag_name": tag, "target_commitish": "main", "name": f"{metadata['name']} {version}",
                "body": definition.get("release_notes", "") + f"\n\nSource: https://github.com/{definition['source_repository']}/commit/{definition['source_commit']}\nSHA256: `{checksum}`",
                "draft": True, "prerelease": definition.get("prerelease", False),
            })
        asset = next((item for item in release["assets"] if item["name"] == asset_name), None)
        if not asset:
            if not release["draft"]:
                raise ValueError("Published release has no ZIP; use a new version")
            upload = release["upload_url"].split("{")[0] + "?name=" + quote(asset_name)
            asset = api.request(upload, package, binary=True)
        if asset.get("digest") and asset["digest"] != "sha256:" + checksum:
            raise ValueError("Version already exists with different package bytes; bump version")
        if release["draft"]:
            release = api.request(release_path + "/" + str(release["id"]), {"draft": False}, method="PATCH")
            # A draft asset's URL may contain an untagged temporary release ID.
            asset = next(item for item in release["assets"] if item["name"] == asset_name)
        if hashlib.sha256(download(asset["browser_download_url"])).hexdigest() != checksum:
            raise ValueError("Published ZIP checksum does not match")
        relative_url = f"{tag}/{asset_name}"
        entry = {
            "version": version, "commit_sha": definition["source_commit"],
            "url": relative_url, "latest_url": relative_url,
            "checksum_sha256": checksum, "size": (len(package) + 1023) // 1024,
            "prerelease": definition.get("prerelease", False),
            "min_dispatcharr_version": metadata.get("min_dispatcharr_version", ""),
        }
        previous = read_feed(api, repo, branch, f"metadata/{slug}/manifest.json")
        versions = previous["manifest"]["versions"] if previous else []
        versions = [entry] + [item for item in versions if item["version"] != version]
        detail = {"manifest": {
            "slug": slug, "name": metadata["name"], "description": metadata.get("description", ""),
            "author": metadata.get("author", ""), "license": metadata.get("license", ""),
            "repo_url": metadata["repo_url"], "registry_url": feed["registry_url"],
            "registry_name": feed["registry_name"], "latest": entry, "versions": versions,
        }}
        raw_root = f"https://raw.githubusercontent.com/{repo}/{branch}"
        summary = {
            "slug": slug, "name": metadata["name"], "description": metadata.get("description", ""),
            "author": metadata.get("author", ""), "license": metadata.get("license", ""),
            "manifest_url": f"{raw_root}/metadata/{slug}/manifest.json",
            "latest_version": version, "latest_url": relative_url, "latest_sha256": checksum,
            "latest_size": entry["size"], "min_dispatcharr_version": entry["min_dispatcharr_version"],
        }
        feed["plugins"] = sorted([item for item in feed["plugins"] if item["slug"] != slug] + [summary], key=lambda item: item["slug"])
        files[f"metadata/{slug}/manifest.json"] = encode_json(detail)
        if "README.md" in contents:
            files[f"plugins/{slug}/README.md"] = contents["README.md"]
        if "logo.png" in contents:
            files[f"plugins/{slug}/logo.png"] = contents["logo.png"]
    if api:
        files["manifest.json"] = encode_json(root_manifest)
        publish_feed(api, repo, branch, files, "Publish configured plugin forks")
        print(f"Feed: https://raw.githubusercontent.com/{repo}/{branch}/manifest.json")


if __name__ == "__main__":
    main()
