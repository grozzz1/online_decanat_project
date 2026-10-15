import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from deploy import manage
from scripts.package_deployment import package


class DeploymentTests(unittest.TestCase):
    def test_server_package_contains_runtime_without_private_data(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "server.zip"
            package(target)
            with ZipFile(target) as archive:
                names = set(archive.namelist())
        self.assertTrue(
            {"main.py", ".env.example", "utils/storage.py", "deploy/install.sh", "deploy/site.sh"}.issubset(
                names
            )
        )
        self.assertFalse(
            any(
                name == ".env" or name.startswith(("private_data/", ".venv/", "models/", ".git/", "tools/"))
                for name in names
            )
        )

    def test_invalid_domain_is_rejected_before_any_server_command(self):
        with patch("deploy.manage.subprocess.run") as run:
            with self.assertRaises(ValueError):
                manage.configure_domain("example.org;echo bad", "admin@example.org")
            run.assert_not_called()

    def test_changing_url_preserves_configuration_access_for_service_user(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = Path(directory) / "app.env"
            environment.write_text("PUBLIC_BASE_URL='http://old.example'\n", encoding="utf-8")
            permissions = environment.stat()
            with (
                patch.object(manage, "ENV_FILE", environment),
                patch("deploy.manage.os.chown", create=True) as chown,
                patch("deploy.manage.os.chmod") as chmod,
            ):
                manage.change_url("https://new.example")
            chown.assert_called_once_with(environment, permissions.st_uid, permissions.st_gid)
            chmod.assert_called_with(environment, permissions.st_mode & 0o7777)
            self.assertIn("https://new.example", environment.read_text(encoding="utf-8"))

    def test_certificate_failure_restores_previous_nginx_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            nginx = Path(directory) / "nginx.conf"
            environment = Path(directory) / "app.env"
            original = manage.render_nginx()
            nginx.write_text(original, encoding="utf-8")
            environment.write_text("PUBLIC_BASE_URL='http://old.example'\n", encoding="utf-8")
            with (
                patch.object(manage, "NGINX_FILE", nginx),
                patch.object(manage, "ENV_FILE", environment),
                patch(
                    "deploy.manage.subprocess.run",
                    side_effect=[None, subprocess.CalledProcessError(1, "certbot"), None],
                ),
            ):
                with self.assertRaises(subprocess.CalledProcessError):
                    manage.configure_domain("new.example.org", "admin@example.org")
            self.assertEqual(nginx.read_text(encoding="utf-8"), original)
            self.assertIn("http://old.example", environment.read_text(encoding="utf-8"))
