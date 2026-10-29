"""Google Drive connector against a fake Drive v3 service (no network, no credentials)."""

from __future__ import annotations

import pytest

from vspm.integrations.documents import DocumentSourceError, GoogleDriveSource

FOLDER = "FOLDER_ID_123"


class FakeRequest:
    def __init__(self, result):
        self.result = result

    def execute(self):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeFiles:
    def __init__(self, files, content):
        self.files_ = files
        self.content = content
        self.list_kwargs = None

    def list(self, **kw):
        self.list_kwargs = kw
        ordered = sorted(self.files_, key=lambda f: f["createdTime"], reverse=True)
        return FakeRequest({"files": ordered[: kw["pageSize"]]})

    def get_media(self, fileId, **kw):
        return FakeRequest(self.content.get(fileId, RuntimeError("404 not found")))


class FakeService:
    def __init__(self, files, content):
        self._files = FakeFiles(files, content)

    def files(self):
        return self._files


def drive_files(n=7):
    return [{"id": f"id{i}", "name": f"scan_{i}.pdf", "mimeType": "application/pdf",
             "createdTime": f"2026-10-{10 + i:02d}T08:00:00Z", "modifiedTime": f"2026-10-{10 + i:02d}T08:00:00Z",
             "md5Checksum": f"md5-{i}", "size": "1234"} for i in range(n)]


def test_lists_five_latest_scans_in_the_folder_only():
    svc = FakeService(drive_files(7), {})
    docs = GoogleDriveSource.with_service(FOLDER, svc).list_latest(5)
    assert [d.name for d in docs] == ["scan_6.pdf", "scan_5.pdf", "scan_4.pdf", "scan_3.pdf", "scan_2.pdf"]
    kw = svc.files().list_kwargs
    assert f"'{FOLDER}' in parents" in kw["q"] and "trashed = false" in kw["q"]
    assert "application/pdf" in kw["q"] and "image/" in kw["q"]
    assert kw["orderBy"] == "createdTime desc" and kw["pageSize"] == 5
    assert kw["supportsAllDrives"] and "md5Checksum" in kw["fields"]
    assert docs[0].revision == "md5-6" and docs[0].uploaded_at == "2026-10-16T08:00:00Z"


def test_revision_falls_back_when_no_checksum():
    f = drive_files(1)
    del f[0]["md5Checksum"]
    f[0]["headRevisionId"] = "rev-9"
    assert GoogleDriveSource.with_service(FOLDER, FakeService(f, {})).list_latest(5)[0].revision == "rev-9"


def test_download_and_errors():
    src = GoogleDriveSource.with_service(FOLDER, FakeService(drive_files(2), {"id1": b"%PDF-1.4 data"}))
    docs = src.list_latest(5)
    assert src.download(docs[0]) == b"%PDF-1.4 data"
    with pytest.raises(DocumentSourceError, match="Download failed"):
        src.download(docs[1])


def test_listing_error_is_reported():
    class Broken(FakeService):
        def files(self):
            class F:
                def list(self, **kw):
                    return FakeRequest(RuntimeError("403 insufficient permissions"))
            return F()
    with pytest.raises(DocumentSourceError, match="403"):
        GoogleDriveSource.with_service(FOLDER, Broken([], {})).list_latest(5)


def test_missing_configuration_is_reported(tmp_path):
    with pytest.raises(DocumentSourceError, match="FOLDER_ID"):
        GoogleDriveSource("", "service_account", tmp_path / "x.json", tmp_path / "t.json")
    with pytest.raises(DocumentSourceError, match="credentials file not found"):
        GoogleDriveSource(FOLDER, "service_account", tmp_path / "missing.json", tmp_path / "t.json")
