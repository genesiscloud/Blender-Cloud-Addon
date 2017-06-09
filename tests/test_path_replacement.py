"""Unittests for blender_cloud.utils.

This unittest requires bpy to be importable, so build Blender as a module and install it
into your virtualenv. See https://stuvel.eu/files/bconf2016/#/10 for notes how.
"""

import datetime
import pathlib
import unittest.mock

import pillarsdk.utils

from blender_cloud.flamenco import sdk


class PathReplacementTest(unittest.TestCase):
    def setUp(self):
        self.test_manager = sdk.Manager({
            '_created': datetime.datetime(2017, 5, 31, 15, 12, 32, tzinfo=pillarsdk.utils.utc),
            '_etag': 'c39942ee4bcc4658adcc21e4bcdfb0ae',
            '_id': '592edd609837732a2a272c62',
            '_updated': datetime.datetime(2017, 6, 8, 14, 51, 3, tzinfo=pillarsdk.utils.utc),
            'description': 'Manager formerly known as "testman"',
            'job_types': {'sleep': {'vars': {}}},
            'name': '<script>alert("this is a manager")</script>',
            'owner': '592edd609837732a2a272c63',
            'path_replacement': {'job_storage': {'darwin': '/Volume/shared',
                                                 'linux': '/shared',
                                                 'windows': 's:/'},
                                 'render': {'darwin': '/Volume/render/',
                                            'linux': '/render/',
                                            'windows': 'r:/'},
                                 'longrender': {'darwin': '/Volume/render/long',
                                                'linux': '/render/long',
                                                'windows': 'r:/long'},
                                 },
            'projects': ['58cbdd5698377322d95eb55e'],
            'service_account': '592edd609837732a2a272c60',
            'stats': {'nr_of_workers': 3},
            'url': 'http://192.168.3.101:8083/',
            'user_groups': ['58cbdd5698377322d95eb55f'],
            'variables': {'blender': {'darwin': '/opt/myblenderbuild/blender',
                                      'linux': '/home/sybren/workspace/build_linux/bin/blender '
                                               '--enable-new-depsgraph --factory-startup',
                                      'windows': 'c:/temp/blender.exe'}}}
        )

    def test_linux(self):
        # (expected result, input)
        test_paths = [
            ('/doesnotexistreally', '/doesnotexistreally'),
            ('{render}/agent327/scenes/A_01_03_B', '/render/agent327/scenes/A_01_03_B'),
            ('{job_storage}/render/agent327/scenes', '/shared/render/agent327/scenes'),
            ('{longrender}/agent327/scenes', '/render/long/agent327/scenes'),
        ]

        self._do_test(test_paths, 'linux', pathlib.PurePosixPath)

    def test_windows(self):
        # (expected result, input)
        test_paths = [
            ('c:/doesnotexistreally', 'c:/doesnotexistreally'),
            ('c:/some/path', r'c:\some\path'),
            ('{render}/agent327/scenes/A_01_03_B', r'R:\agent327\scenes\A_01_03_B'),
            ('{render}/agent327/scenes/A_01_03_B', r'r:\agent327\scenes\A_01_03_B'),
            ('{render}/agent327/scenes/A_01_03_B', r'r:/agent327/scenes/A_01_03_B'),
            ('{job_storage}/render/agent327/scenes', 's:/render/agent327/scenes'),
            ('{longrender}/agent327/scenes', 'r:/long/agent327/scenes'),
        ]

        self._do_test(test_paths, 'windows', pathlib.PureWindowsPath)

    def test_darwin(self):
        # (expected result, input)
        test_paths = [
            ('/Volume/doesnotexistreally', '/Volume/doesnotexistreally'),
            ('{render}/agent327/scenes/A_01_03_B', r'/Volume/render/agent327/scenes/A_01_03_B'),
            ('{job_storage}/render/agent327/scenes', '/Volume/shared/render/agent327/scenes'),
            ('{longrender}/agent327/scenes', '/Volume/render/long/agent327/scenes'),
        ]

        self._do_test(test_paths, 'darwin', pathlib.PurePosixPath)

    def _do_test(self, test_paths, platform, pathclass):
        self.test_manager.PurePlatformPath = pathclass
        with unittest.mock.patch('sys.platform', platform):
            for expected_result, input_path in test_paths:
                self.assertEqual(expected_result,
                                 self.test_manager.replace_path(pathclass(input_path)),
                                 'for input %s on platform %s' % (input_path, platform))
