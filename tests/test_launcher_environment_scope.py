import json
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import launch_biotrack_studio as launcher
from biotrack_studio.env_manager import get_env_target_args


class LauncherEnvironmentScopeTests(unittest.TestCase):
    def test_correction_launch_selects_python_inside_explicit_root(self):
        from biotrack_studio.widget import _find_env_python
        with tempfile.TemporaryDirectory() as temp:
            prefix = Path(temp)/'test_correction'
            python = prefix/('python.exe' if os.name=='nt' else 'bin/python')
            python.parent.mkdir(parents=True)
            python.touch()
            with patch.dict(os.environ, {'CONDA_ENVS_PATH':temp}):
                self.assertEqual(_find_env_python('test_correction'),str(python.resolve()))

    def test_correction_launch_does_not_fall_back_to_registered_global_environment(self):
        from biotrack_studio.widget import _find_env_python
        with tempfile.TemporaryDirectory() as temp:
            selected = Path(temp)/'selected'
            global_prefix = Path(temp)/'global/test_correction'
            python = global_prefix/('python.exe' if os.name=='nt' else 'bin/python')
            python.parent.mkdir(parents=True)
            python.touch()
            with patch.dict(os.environ, {'CONDA_ENVS_PATH':str(selected)}), patch(
                'biotrack_studio.widget.subprocess.run', return_value=SimpleNamespace(
                    stdout=json.dumps({'envs':[str(global_prefix)]}))
            ):
                self.assertIsNone(_find_env_python('test_correction'))

    def test_explicit_root_uses_full_prefix_for_creation_and_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.dict(os.environ, {'CONDA_ENVS_PATH': temp}):
                self.assertEqual(get_env_target_args('test_method'), ['-p', str(Path(temp).resolve()/'test_method')])

    def test_default_target_keeps_named_environment_lookup(self):
        with patch.dict(os.environ, {'CONDA_ENVS_PATH': ''}):
            self.assertEqual(get_env_target_args('test_method'), ['-n', 'test_method'])

    def test_existing_environment_in_second_selected_root_is_addressed(self):
        with tempfile.TemporaryDirectory() as temp:
            first, second = Path(temp)/'first', Path(temp)/'second'
            prefix = second/'test_method'
            (prefix/'conda-meta').mkdir(parents=True)
            (prefix/'conda-meta/history').touch()
            with patch.dict(os.environ, {'CONDA_ENVS_PATH': os.pathsep.join([str(first),str(second)])}):
                self.assertEqual(get_env_target_args('test_method'), ['-p', str(prefix.resolve())])

    def test_environment_name_cannot_escape_selected_root(self):
        for name in ['../other', '/other', 'C:\\other', '..']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                get_env_target_args(name)

    def test_registered_environment_outside_selected_root_is_not_reused(self):
        with tempfile.TemporaryDirectory() as temp:
            selected = Path(temp) / 'selected'
            old = Path(temp) / 'unrelated' / launcher.ENV_NAME
            with patch.dict(os.environ, {'CONDA_ENVS_PATH': str(selected)}), patch.object(
                launcher, 'run_capture', return_value=json.dumps({'envs': [str(old)]})
            ):
                self.assertFalse(launcher.env_exists('conda'))

    def test_environment_in_selected_root_is_reused(self):
        with tempfile.TemporaryDirectory() as temp:
            selected = Path(temp) / 'selected'
            prefix = selected / launcher.ENV_NAME
            with patch.dict(os.environ, {'CONDA_ENVS_PATH': str(selected)}), patch.object(
                launcher, 'run_capture', return_value=json.dumps({'envs': [str(prefix)]})
            ):
                self.assertTrue(launcher.env_exists('conda'))

    def test_default_environment_lookup_remains_available(self):
        with patch.dict(os.environ, {'CONDA_ENVS_PATH': ''}), patch.object(
            launcher, 'run_capture', return_value=json.dumps({'envs': [str(Path('existing') / launcher.ENV_NAME)]})
        ):
            self.assertTrue(launcher.env_exists('conda'))


if __name__ == '__main__':
    unittest.main()
