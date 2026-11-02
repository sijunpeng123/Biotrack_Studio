import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from biotrack_studio.widget import _find_env_python


class CorrectionEnvironmentTests(unittest.TestCase):
    def test_duplicate_names_follow_conda_selected_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            name = "env_correction_mmv_h4tracks"
            registered = root / "registered" / name
            selected = root / "selected" / name
            relative_python = "python.exe" if os.name == "nt" else "bin/python"
            for prefix in (registered, selected):
                python = prefix / relative_python
                python.parent.mkdir(parents=True)
                python.touch()
            selected_python = selected / relative_python

            def conda_result(command, **kwargs):
                if command[1:3] == ["env", "list"]:
                    output = json.dumps({"envs": [str(registered), str(selected)]})
                else:
                    self.assertEqual(command[1:4], ["run", "-n", name])
                    output = str(selected_python) + "\n"
                return subprocess.CompletedProcess(command, 0, stdout=output)

            with patch("biotrack_studio.widget.subprocess.run", side_effect=conda_result):
                self.assertEqual(Path(_find_env_python(name)), selected_python)

    def test_failed_lookup_allows_existing_launcher_fallback(self):
        error = subprocess.CalledProcessError(1, ["conda", "run"])
        with patch("biotrack_studio.widget.subprocess.run", side_effect=error):
            self.assertIsNone(_find_env_python("missing_environment"))

    def test_missing_python_is_not_selected(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing_python"
            result = subprocess.CompletedProcess([], 0, stdout=str(missing))
            with patch("biotrack_studio.widget.subprocess.run", return_value=result):
                self.assertIsNone(_find_env_python("missing_environment"))


if __name__ == "__main__":
    unittest.main()
