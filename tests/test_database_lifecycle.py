"""Connections must close even while caller references remain alive."""
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from qualitative_themes.storage.database import connect, create_project, open_project


class DatabaseLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = create_project(Path(self.temp.name)/"project", "Before").directory

    def assert_closed(self, connection):
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")

    def test_success_commits_and_closes_without_garbage_collection(self):
        with connect(self.directory) as retained_connection:
            retained_connection.execute("UPDATE project SET name='After'")
        self.assert_closed(retained_connection)
        self.assertEqual(open_project(self.directory).name, "After")
        # Windows must also be able to rename the file with that reference alive.
        original = self.directory/"project.sqlite"
        moved = self.directory/"renamed.sqlite"
        original.rename(moved)
        moved.rename(original)

    def test_exception_rolls_back_and_closes(self):
        with self.assertRaisesRegex(RuntimeError, "Simulated failure"):
            with connect(self.directory) as retained_connection:
                retained_connection.execute("UPDATE project SET name='Unsaved'")
                raise RuntimeError("Simulated failure")
        self.assert_closed(retained_connection)
        self.assertEqual(open_project(self.directory).name, "Before")

    def test_failed_migration_closes_the_connection(self):
        retained_connection = sqlite3.connect(self.directory/"project.sqlite")
        retained_connection.execute("PRAGMA user_version=999")
        retained_connection.commit()
        with patch("qualitative_themes.storage.database.sqlite3.connect", return_value=retained_connection):
            with self.assertRaisesRegex(ValueError, "newer"):
                with connect(self.directory):
                    self.fail("A newer schema must not open")
        self.assert_closed(retained_connection)


if __name__ == "__main__":
    unittest.main()
