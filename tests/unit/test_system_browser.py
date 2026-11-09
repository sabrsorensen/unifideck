"""A distro-managed Chromium replaces the pinned download."""
from __future__ import annotations

from unittest import mock

from unifideck.auth.edge_browser import chrome_installer as ci

SYSTEM = "/run/current-system/sw/bin/chromium"


def test_system_browser_is_ready_without_a_download(tmp_path, monkeypatch):
    monkeypatch.setattr(ci, "browser_root", lambda: tmp_path)
    with mock.patch.object(ci.shutil, "which", return_value=SYSTEM):
        assert ci.is_ready()
        assert ci.ChromeInstaller().find_cmd() == [SYSTEM]


def test_install_never_downloads_when_system_browser_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(ci, "browser_root", lambda: tmp_path)
    with mock.patch.object(ci.shutil, "which", return_value=SYSTEM), \
            mock.patch.object(ci, "_download") as download:
        assert ci.install_blocking()["success"] is True
    download.assert_not_called()


def test_no_system_browser_keeps_the_download_path(tmp_path, monkeypatch):
    monkeypatch.setattr(ci, "browser_root", lambda: tmp_path)
    with mock.patch.object(ci.shutil, "which", return_value=None):
        assert not ci.is_ready()
        assert ci.ChromeInstaller().find_cmd() is None
