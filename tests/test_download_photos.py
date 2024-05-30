import logging
from typing import Any, Callable, List, NoReturn, Optional, Sequence, Tuple
from unittest import TestCase
from requests import Response
from vcr import VCR
import os
import sys
import shutil
import pytest
import mock
import datetime
from mock import call, ANY
from click.testing import CliRunner
import piexif
from piexif._exceptions import InvalidImageDataError
from icloudpd import constants
from pyicloud_ipd.services.photos import PhotoAsset, PhotoAlbum, PhotoLibrary
from pyicloud_ipd.base import PyiCloudService
from pyicloud_ipd.exceptions import PyiCloudAPIResponseException
from requests.exceptions import ConnectionError
from icloudpd.base import main
from tests.helpers import path_from_project_root, print_result_exception, recreate_path
import inspect
import glob

vcr = VCR(decode_compressed_response=True)


class DownloadPhotoTestCase(TestCase):
    @pytest.fixture(autouse=True)
    def inject_fixtures(self, caplog: pytest.LogCaptureFixture) -> None:
        self._caplog = caplog
        self.root_path = path_from_project_root(__file__)
        self.fixtures_path = os.path.join(self.root_path, "fixtures")
        self.vcr_path = os.path.join(self.root_path, "vcr_cassettes")

    def test_download_and_skip_existing_photos(self) -> None:
        base_dir = os.path.join(self.fixtures_path, inspect.stack()[0][3])
        cookie_dir = os.path.join(base_dir, "cookie")
        data_dir = os.path.join(base_dir, "data")

        for dir in [base_dir, cookie_dir, data_dir]:
            recreate_path(dir)

        files_to_create = [
            ("2018/07/30/IMG_7408.JPG", 1151066),
            ("2018/07/30/IMG_7407.JPG", 656257),
        ]

        files_to_download = [
            '2018/07/31/IMG_7409.JPG'
        ]

        os.makedirs(os.path.join(data_dir, "2018/07/30/"))
        for (file_name, file_size) in files_to_create:
            with open(os.path.join(data_dir, file_name), "a") as f:
                f.truncate(file_size)

        with vcr.use_cassette(os.path.join(self.vcr_path, "listing_photos.yml")):
            # Pass fixed client ID via environment variable
            runner = CliRunner(env={
                "CLIENT_ID": "DE309E26-942E-11E8-92F5-14109FE0B321"
            })
            result = runner.invoke(
                main,
                [
                    "--username",
                    "jdoe@gmail.com",
                    "--password",
                    "password1",
                    "--recent",
                    "5",
                    "--skip-videos",
                    "--skip-live-photos",
                    "--set-exif-datetime",
                    "--no-progress-bar",
                    "--threads-num",
                    "1",
                    "-d",
                    data_dir,
                    "--cookie-directory",
                    cookie_dir,
                ],
            )
            print_result_exception(result)

            self.assertIn(
                "DEBUG    Looking up all photos from album All Photos...", self._caplog.text)
            self.assertIn(
                f"INFO     Downloading 5 original photos to {data_dir} ...",
                self._caplog.text,
            )
            self.assertIn(
                f"DEBUG    Downloading {os.path.join(data_dir, os.path.normpath('2018/07/31/IMG_7409.JPG'))}",
                self._caplog.text,
            )
            self.assertNotIn(
                "IMG_7409.MOV",
                self._caplog.text,
            )
            self.assertIn(
                f"DEBUG    {os.path.join(data_dir, os.path.normpath('2018/07/30/IMG_7408.JPG'))} already exists",
                self._caplog.text,
            )
            self.assertIn(
                f"DEBUG    {os.path.join(data_dir, os.path.normpath('2018/07/30/IMG_7407.JPG'))} already exists",
                self._caplog.text,
            )
            self.assertIn(
                "DEBUG    Skipping IMG_7405.MOV, only downloading photos.",
                self._caplog.text,
            )
            self.assertIn(
                "DEBUG    Skipping IMG_7404.MOV, only downloading photos.",
                self._caplog.text,
            )
            self.assertIn(
                "INFO     All photos have been downloaded", self._caplog.text
            )

            assert result.exit_code == 0

        files_in_result = glob.glob(os.path.join(
            data_dir, "**/*.*"), recursive=True)

        assert sum(1 for _ in files_in_result) == len(
            files_to_create) + len(files_to_download)

        for file_name in files_to_download + ([file_name for (file_name, _) in files_to_create]):
            assert os.path.exists(os.path.join(data_dir, os.path.normpath(
                file_name))), f"File {file_name} expected, but does not exist"

        # Check that file was downloaded
        # Check that mtime was updated to the photo creation date
        photo_mtime = os.path.getmtime(os.path.join(
            data_dir, os.path.normpath("2018/07/31/IMG_7409.JPG")))
        photo_modified_time = datetime.datetime.utcfromtimestamp(photo_mtime)
        self.assertEqual(
            "2018-07-31 07:22:24",
            photo_modified_time.strftime('%Y-%m-%d %H:%M:%S'))

    def test_download_photos_and_set_exif(self) -> None:
        base_dir = os.path.join(self.fixtures_path, inspect.stack()[0][3])
        cookie_dir = os.path.join(base_dir, "cookie")
        data_dir = os.path.join(base_dir, "data")

        for dir in [base_dir, cookie_dir, data_dir]:
            recreate_path(dir)

        files_to_create = [
            ("2018/07/30/IMG_7408.JPG", 1151066),
            ("2018/07/30/IMG_7407.JPG", 656257),
        ]

        files_to_download = [
            '2018/07/30/IMG_7405.MOV',
            '2018/07/30/IMG_7407.MOV',
            '2018/07/30/IMG_7408.MOV',
            '2018/07/31/IMG_7409.JPG',
            '2018/07/31/IMG_7409.MOV',
        ]

        os.makedirs(os.path.join(data_dir, "2018/07/30/"))
        for (file_name, file_size) in files_to_create:
            with open(os.path.join(data_dir, file_name), "a") as f:
                f.truncate(file_size)

        # Download the first photo, but mock the video download
        orig_download = PhotoAsset.download

        def mocked_download(pa: PhotoAsset, size:str) -> Optional[Response]:
            if not hasattr(PhotoAsset, "already_downloaded"):
                response = orig_download(pa, size)
                setattr(PhotoAsset, "already_downloaded", True)
                return response
            return mock.MagicMock()

        with mock.patch.object(PhotoAsset, "download", new=mocked_download):
            with mock.patch(
                "icloudpd.exif_datetime.get_photo_exif"
            ) as get_exif_patched:
                get_exif_patched.return_value = False
                with vcr.use_cassette(os.path.join(self.vcr_path, "listing_photos.yml")):
                    # Pass fixed client ID via environment variable
                    runner = CliRunner(env={
                        "CLIENT_ID": "DE309E26-942E-11E8-92F5-14109FE0B321"
                    })
                    result = runner.invoke(
                        main,
                        [
                            "--username",
                            "jdoe@gmail.com",
                            "--password",
                            "password1",
                            "--recent",
                            "4",
                            "--set-exif-datetime",
                            # '--skip-videos',
                            # "--skip-live-photos",
                            "--no-progress-bar",
                            "--threads-num",
                            "1",
                            "-d",
                            data_dir,
                            "--cookie-directory",
                            cookie_dir,
                        ],
                    )
                    print_result_exception(result)

                    self.assertIn(
                        "DEBUG    Looking up all photos and videos from album All Photos...",
                        self._caplog.text,
                    )
                    self.assertIn(
                        f"INFO     Downloading 4 original photos and videos to {data_dir} ...",
                        self._caplog.text,
                    )
                    self.assertIn(
                        f"DEBUG    Downloading {os.path.join(data_dir, os.path.normpath('2018/07/31/IMG_7409.JPG'))}",
                        self._caplog.text,
                    )
                    # 2018:07:31 07:22:24 utc
                    expectedDatetime = datetime.datetime(
                        2018, 7, 31, 7, 22, 24, tzinfo=datetime.timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S%z")
                    self.assertIn(
                        f"DEBUG    Setting EXIF timestamp for {os.path.join(data_dir, os.path.normpath('2018/07/31/IMG_7409.JPG'))}: {expectedDatetime}",
                        self._caplog.text,
                    )
                    self.assertIn(
                        "INFO     All photos have been downloaded", self._caplog.text
                    )
                    assert result.exit_code == 0

        files_in_result = glob.glob(os.path.join(
            data_dir, "**/*.*"), recursive=True)

        assert sum(1 for _ in files_in_result) == len(
            files_to_create) + len(files_to_download)

        for file_name in files_to_download + ([file_name for (file_name, _) in files_to_create]):
            assert os.path.exists(os.path.join(data_dir, os.path.normpath(
                file_name))), f"File {file_name} expected, but does not exist"

    def test_download_photos_and_get_exif_exceptions(self) -> None:
        base_dir = os.path.join(self.fixtures_path, inspect.stack()[0][3])
        cookie_dir = os.path.join(base_dir, "cookie")
        data_dir = os.path.join(base_dir, "data")

        for dir in [base_dir, cookie_dir, data_dir]:
            recreate_path(dir)

        files_to_download = [
            '2018/07/31/IMG_7409.JPG'
        ]

        with mock.patch.object(piexif, "load") as piexif_patched:
            piexif_patched.side_effect = InvalidImageDataError

            with vcr.use_cassette(os.path.join(self.vcr_path, "listing_photos.yml")):
                # Pass fixed client ID via environment variable
                runner = CliRunner(env={
                    "CLIENT_ID": "DE309E26-942E-11E8-92F5-14109FE0B321"
                })
                result = runner.invoke(
                    main,
                    [
                        "--username",
                        "jdoe@gmail.com",
                        "--password",
                        "password1",
                        "--recent",
                        "1",
                        "--skip-videos",
                        "--skip-live-photos",
                        "--set-exif-datetime",
                        "--no-progress-bar",
                        "--threads-num",
                        "1",
                        "-d",
                        data_dir,
                        "--cookie-directory",
                        cookie_dir,
                    ],
                )
                print_result_exception(result)

                self.assertIn(
                    "DEBUG    Looking up all photos from album All Photos...", self._caplog.text)
                self.assertIn(
                    f"INFO     Downloading the first original photo to {data_dir} ...",
                    self._caplog.text,
                )
                self.assertIn(
                    f"DEBUG    Downloading {os.path.join(data_dir, os.path.normpath('2018/07/31/IMG_7409.JPG'))}",
                    self._caplog.text,
                )
                self.assertIn(
                    f"DEBUG    Error fetching EXIF data for {os.path.join(data_dir, os.path.normpath('2018/07/31/IMG_7409.JPG'))}",
                    self._caplog.text,
                )
                self.assertIn(
                    f"DEBUG    Error setting EXIF data for {os.path.join(data_dir, os.path.normpath('2018/07/31/IMG_7409.JPG'))}",
                    self._caplog.text,
                )
                self.assertIn(
                    "INFO     All photos have been downloaded", self._caplog.text
                )
                assert result.exit_code == 0

        files_in_result = glob.glob(os.path.join(
            data_dir, "**/*.*"), recursive=True)

        assert sum(1 for _ in files_in_result) == len(files_to_download)

        for file_name in files_to_download:
            assert os.path.exists(os.path.join(data_dir, os.path.normpath(
                file_name))), f"File {file_name} expected, but does not exist"

    def test_skip_existing_downloads(self) -> None:
        base_dir = os.path.join(self.fixtures_path, inspect.stack()[0][3])
        cookie_dir = os.path.join(base_dir, "cookie")
        data_dir = os.path.join(base_dir, "data")

        for dir in [base_dir, cookie_dir, data_dir]:
            recreate_path(dir)

        files_to_create = [
            ("2018/07/31/IMG_7409.JPG", 1884695),
            ("2018/07/31/IMG_7409.MOV", 3294075),
        ]

        files_to_download: List[str] = [
        ]

        os.makedirs(os.path.join(data_dir, "2018/07/31/"))
        for (file_name, file_size) in files_to_create:
            with open(os.path.join(data_dir, file_name), "a") as f:
                f.truncate(file_size)

        with vcr.use_cassette(os.path.join(self.vcr_path, "listing_photos.yml")):
            # Pass fixed client ID via environment variable
            runner = CliRunner(env={
                "CLIENT_ID": "DE309E26-942E-11E8-92F5-14109FE0B321"
            })
            result = runner.invoke(
                main,
                [
                    "--username",
                    "jdoe@gmail.com",
                    "--password",
                    "password1",
                    "--recent",
                    "1",
                    # '--skip-videos',
                    # "--skip-live-photos",
                    "--no-progress-bar",
                    "--threads-num",
                    "1",
                    "-d",
                    data_dir,
                    "--cookie-directory",
                    cookie_dir,
                ],
            )
            print_result_exception(result)

            self.assertIn(
                "DEBUG    Looking up all photos and videos from album All Photos...", self._caplog.text
            )
            self.assertIn(
                f"INFO     Downloading the first original photo or video to {data_dir} ...",
                self._caplog.text,
            )
            self.assertIn(
                f"DEBUG    {os.path.join(data_dir, os.path.normpath('2018/07/31/IMG_7409.JPG'))} already exists",
                self._caplog.text,
            )
            self.assertIn(
                f"DEBUG    {os.path.join(data_dir, os.path.normpath('2018/07/31/IMG_7409.MOV'))} already exists",
                self._caplog.text,
            )
            self.assertIn(
                "INFO     All photos have been downloaded", self._caplog.text
            )
            assert result.exit_code == 0

        files_in_result = glob.glob(os.path.join(
            data_dir, "**/*.*"), recursive=True)

        assert sum(1 for _ in files_in_result) == len(
            files_to_download) + len(files_to_create)

        for file_name in files_to_download + ([file_name for (file_name, _) in files_to_create]):
            assert os.path.exists(os.path.join(data_dir, os.path.normpath(
                file_name))), f"File {file_name} expected, but does not exist"

    def test_until_found(self) -> None:
        base_dir = os.path.join(self.fixtures_path, inspect.stack()[0][3])
        cookie_dir = os.path.join(base_dir, "cookie")
        data_dir = os.path.join(base_dir, "data")

        for dir in [base_dir, cookie_dir, data_dir]:
            recreate_path(dir)

        os.makedirs(os.path.join(data_dir, "2018/07/30/"))
        os.makedirs(os.path.join(data_dir, "2018/07/31/"))

        files_to_download: Sequence[Tuple[str, str]] = [
            ("2018/07/31/IMG_7409.JPG", "photo"),
            ("2018/07/31/IMG_7409-medium.MOV", "photo"),
            ("2018/07/30/IMG_7407.JPG", "photo"),
            ("2018/07/30/IMG_7407-medium.MOV", "photo"),
            ("2018/07/30/IMG_7403.MOV", "video"),
            ("2018/07/30/IMG_7402.MOV", "video"),
            ("2018/07/30/IMG_7399-medium.MOV", "photo")
        ]
        files_to_skip: Sequence[Tuple[str, str, int]] = [
            ("2018/07/30/IMG_7408.JPG", "photo", 1151066),
            ("2018/07/30/IMG_7408-medium.MOV", "photo", 894467),
            ("2018/07/30/IMG_7405.MOV", "video", 36491351),
            ("2018/07/30/IMG_7404.MOV", "video", 225935003),
            # TODO large files on Windows times out
            ("2018/07/30/IMG_7401.MOV", "photo", 565699696),
            ("2018/07/30/IMG_7400.JPG", "photo", 2308885),
            ("2018/07/30/IMG_7400-medium.MOV", "photo", 1238639),
            ("2018/07/30/IMG_7399.JPG", "photo", 2251047)
        ]


        for f in files_to_skip:
            with open(os.path.join(data_dir, f[0]), "a") as fi:
                fi.truncate(f[2])

        with mock.patch("icloudpd.download.download_media") as dp_patched:
            dp_patched.return_value = True
            with mock.patch("icloudpd.download.os.utime") as ut_patched:
                ut_patched.return_value = None
                with vcr.use_cassette(os.path.join(self.vcr_path, "listing_photos.yml")):
                    # Pass fixed client ID via environment variable
                    runner = CliRunner(env={
                        "CLIENT_ID": "DE309E26-942E-11E8-92F5-14109FE0B321"
                    })
                    result = runner.invoke(
                        main,
                        [
                            "--username",
                            "jdoe@gmail.com",
                            "--password",
                            "password1",
                            "--live-photo-size",
                            "medium",
                            "--until-found",
                            "3",
                            "--recent",
                            "20",
                            "--no-progress-bar",
                            "--threads-num",
                            "1",
                            "-d",
                            data_dir,
                            "--cookie-directory",
                            cookie_dir,
                        ],
                    )
                    print_result_exception(result)

                    expected_calls = list(
                        map(
                            lambda f: call(
                                ANY, False, ANY, ANY, os.path.join(
                                    data_dir, os.path.normpath(f[0])),
                                "mediumVideo" if (
                                    f[1] == 'photo' and f[0].endswith('.MOV')
                                ) else "original"),
                            files_to_download,
                        )
                    )
                    dp_patched.assert_has_calls(expected_calls)

                    self.assertIn(
                        "DEBUG    Looking up all photos and videos from album All Photos...", self._caplog.text
                    )
                    self.assertIn(
                        f"INFO     Downloading ??? original photos and videos to {data_dir} ...",
                        self._caplog.text,
                    )

                    for s in files_to_skip:
                        expected_message = f"DEBUG    {os.path.join(data_dir, os.path.normpath(s[0]))} already exists"
                        self.assertIn(expected_message, self._caplog.text)

                    for d in files_to_download:
                        expected_message = f"DEBUG    {os.path.join(data_dir, os.path.normpath(d[0]))} already exists"
                        self.assertNotIn(expected_message, self._caplog.text)

                    self.assertIn(
                        "INFO     Found 3 consecutive previously downloaded photos. Exiting",
                        self._caplog.text,
                    )
                    assert result.exit_code == 0

        files_in_result = glob.glob(os.path.join(
            data_dir, "**/*.*"), recursive=True)

        assert sum(1 for _ in files_in_result) == len(
            files_to_skip)  # we faked downloading

        for file_name in ([file_name for (file_name, _, _) in files_to_skip]):
            assert os.path.exists(os.path.join(data_dir, os.path.normpath(
                file_name))), f"File {file_name} expected, but does not exist"

    def test_handle_io_error(self) -> None:
        base_dir = os.path.join(self.fixtures_path, inspect.stack()[0][3])
        cookie_dir = os.path.join(base_dir, "cookie")
        data_dir = os.path.join(base_dir, "data")

        for dir in [base_dir, cookie_dir, data_dir]:
            recreate_path(dir)

        with vcr.use_cassette(os.path.join(self.vcr_path, "listing_photos.yml")):
            with mock.patch("icloudpd.download.open", create=True) as m:
                # Raise IOError when we try to write to the destination file
                m.side_effect = IOError

                runner = CliRunner(env={
                    "CLIENT_ID": "DE309E26-942E-11E8-92F5-14109FE0B321"
                })
                result = runner.invoke(
                    main,
                    [
                        "--username",
                        "jdoe@gmail.com",
                        "--password",
                        "password1",
                        "--recent",
                        "1",
                        "--skip-videos",
                        "--skip-live-photos",
                        "--no-progress-bar",
                        "--threads-num",
                        "1",
                        "-d",
                        data_dir,
                        "--cookie-directory",
                        cookie_dir,
                    ],
                )
                print_result_exception(result)

                self.assertIn(
                    "DEBUG    Looking up all photos from album All Photos...", self._caplog.text)
                self.assertIn(
                    f"INFO     Downloading the first original photo to {data_dir} ...",
                    self._caplog.text,
                )
                self.assertIn(
                    "ERROR    IOError while writing file to "
                    f"{os.path.join(data_dir, os.path.normpath('2018/07/31/IMG_7409.JPG'))}. "
                    "You might have run out of disk space, or the file might "
                    "be too large for your OS. Skipping this file...",
                    self._caplog.text,
                )
                assert result.exit_code == 0

        files_in_result = glob.glob(os.path.join(
            data_dir, "**/*.*"), recursive=True)

        assert sum(1 for _ in files_in_result) == 0

    def test_handle_session_error_during_download(self) -> None:
        base_dir = os.path.join(self.fixtures_path, inspect.stack()[0][3])
        cookie_dir = os.path.join(base_dir, "cookie")
        data_dir = os.path.join(base_dir, "data")

        for dir in [base_dir, cookie_dir, data_dir]:
            recreate_path(dir)

        with vcr.use_cassette(os.path.join(self.vcr_path, "listing_photos.yml")):

            def mock_raise_response_error(_arg: Any) -> NoReturn:
                raise PyiCloudAPIResponseException("Invalid global session", "100")

            with mock.patch("time.sleep") as sleep_mock:
                with mock.patch.object(PhotoAsset, "download") as pa_download:
                    pa_download.side_effect = mock_raise_response_error

                    # Let the initial authenticate() call succeed,
                    # but do nothing on the second try.
                    orig_authenticate = PyiCloudService.authenticate

                    def mocked_authenticate(self: PyiCloudService) -> None:
                        if not hasattr(self, "already_authenticated"):
                            orig_authenticate(self)
                            setattr(self, "already_authenticated", True)

                    with mock.patch.object(
                        PyiCloudService, "authenticate", new=mocked_authenticate
                    ):
                        # Pass fixed client ID via environment variable
                        runner = CliRunner(env={
                            "CLIENT_ID": "DE309E26-942E-11E8-92F5-14109FE0B321"
                        })
                        result = runner.invoke(
                            main,
                            [
                                "--username",
                                "jdoe@gmail.com",
                                "--password",
                                "password1",
                                "--recent",
                                "1",
                                "--skip-videos",
                                "--skip-live-photos",
                                "--no-progress-bar",
                                "--threads-num",
                                "1",
                                "-d",
                                data_dir,
                                "--cookie-directory",
                                cookie_dir,
                            ],
                        )
                        print_result_exception(result)

                        # Error msg should be repeated 5 times
                        assert (
                            self._caplog.text.count(
                                "Session error, re-authenticating..."
                            )
                            == 5
                        )

                        self.assertIn(
                            "ERROR    Could not download IMG_7409.JPG. Please try again later.",
                            self._caplog.text,
                        )

                        # Make sure we only call sleep 4 times (skip the first retry)
                        self.assertEqual(sleep_mock.call_count, 4)
                        assert result.exit_code == 0

        files_in_result = glob.glob(os.path.join(
            data_dir, "**/*.*"), recursive=True)

        assert sum(1 for _ in files_in_result) == 0

    def test_handle_session_error_during_photo_iteration(self) -> None:
        base_dir = os.path.join(self.fixtures_path, inspect.stack()[0][3])
        cookie_dir = os.path.join(base_dir, "cookie")
        data_dir = os.path.join(base_dir, "data")

        for dir in [base_dir, cookie_dir, data_dir]:
            recreate_path(dir)

        with vcr.use_cassette(os.path.join(self.vcr_path, "listing_photos.yml")):

            def mock_raise_response_error(_offset: int) -> NoReturn:
                raise PyiCloudAPIResponseException("Invalid global session", "100")

            with mock.patch("time.sleep") as sleep_mock:
                with mock.patch.object(PhotoAlbum, "photos_request") as pa_photos_request:
                    pa_photos_request.side_effect = mock_raise_response_error

                    # Let the initial authenticate() call succeed,
                    # but do nothing on the second try.
                    orig_authenticate = PyiCloudService.authenticate

                    def mocked_authenticate(self: PyiCloudService) -> None:
                        if not hasattr(self, "already_authenticated"):
                            orig_authenticate(self)
                            setattr(self, "already_authenticated", True)

                    with mock.patch.object(
                        PyiCloudService, "authenticate", new=mocked_authenticate
                    ):
                        # Pass fixed client ID via environment variable
                        runner = CliRunner(env={
                            "CLIENT_ID": "DE309E26-942E-11E8-92F5-14109FE0B321"
                        })
                        result = runner.invoke(
                            main,
                            [
                                "--username",
                                "jdoe@gmail.com",
                                "--password",
                                "password1",
                                "--recent",
                                "1",
                                "--skip-videos",
                                "--skip-live-photos",
                                "--no-progress-bar",
                                "--threads-num",
                                "1",
                                "-d",
                                data_dir,
                                "--cookie-directory",
                                cookie_dir,
                            ],
                        )
                        print_result_exception(result)

                        # Error msg should be repeated 5 times
                        assert (
                            self._caplog.text.count(
                                "Session error, re-authenticating..."
                            )
                            == 5
                        )

                        self.assertIn(
                            "ERROR    iCloud re-authentication failed. Please try again later.",
                            self._caplog.text,
                        )
                        # Make sure we only call sleep 4 times (skip the first retry)
                        self.assertEqual(sleep_mock.call_count, 4)

                        assert result.exit_code == 1

        files_in_result = glob.glob(os.path.join(
            data_dir, "**/*.*"), recursive=True)

        assert sum(1 for _ in files_in_result) == 0

    def test_handle_connection_error(self) -> None:
        base_dir = os.path.join(self.fixtures_path, inspect.stack()[0][3])
        cookie_dir = os.path.join(base_dir, "cookie")
        data_dir = os.path.join(base_dir, "data")

        for dir in [base_dir, cookie_dir, data_dir]:
            recreate_path(dir)

        with vcr.use_cassette(os.path.join(self.vcr_path, "listing_photos.yml")):
            # Pass fixed client ID via environment variable

            def mock_raise_response_error(_arg: Any) -> NoReturn:
                raise ConnectionError("Connection Error")

            with mock.patch.object(PhotoAsset, "download") as pa_download:
                pa_download.side_effect = mock_raise_response_error

                # Let the initial authenticate() call succeed,
                # but do nothing on the second try.
                orig_authenticate = PyiCloudService.authenticate

                def mocked_authenticate(self: PyiCloudService) -> None:
                    if not hasattr(self, "already_authenticated"):
                        orig_authenticate(self)
                        setattr(self, "already_authenticated", True)

                with mock.patch("icloudpd.constants.WAIT_SECONDS", 0):
                    with mock.patch.object(
                        PyiCloudService, "authenticate", new=mocked_authenticate
                    ):
                        runner = CliRunner(env={
                            "CLIENT_ID": "DE309E26-942E-11E8-92F5-14109FE0B321"
                        })
                        result = runner.invoke(
                            main,
                            [
                                "--username",
                                "jdoe@gmail.com",
                                "--password",
                                "password1",
                                "--recent",
                                "1",
                                "--skip-videos",
                                "--skip-live-photos",
                                "--no-progress-bar",
                                "--threads-num",
                                "1",
                                "-d",
                                data_dir,
                                "--cookie-directory",
                                cookie_dir,
                            ],
                        )
                        print_result_exception(result)

                        # Error msg should be repeated 5 times
                        assert (
                            self._caplog.text.count(
                                "Error downloading IMG_7409.JPG, retrying after 0 seconds..."
                            )
                            == 5
                        )

                        self.assertIn(
                            "ERROR    Could not download IMG_7409.JPG. Please try again later.",
                            self._caplog.text,
                        )
                        assert result.exit_code == 0

        files_in_result = glob.glob(os.path.join(
            data_dir, "**/*.*"), recursive=True)

        assert sum(1 for _ in files_in_result) == 0

    def test_handle_albums_error(self) -> None:
        base_dir = os.path.join(self.fixtures_path, inspect.stack()[0][3])
        cookie_dir = os.path.join(base_dir, "cookie")
        data_dir = os.path.join(base_dir, "data")

        for dir in [base_dir, cookie_dir, data_dir]:
            recreate_path(dir)

        with vcr.use_cassette(os.path.join(self.vcr_path, "listing_photos.yml")):
            # Pass fixed client ID via environment variable

            def mock_raise_response_error() -> None:
                raise PyiCloudAPIResponseException("Api Error", "100")

            with mock.patch.object(PhotoLibrary, "_fetch_folders") as pa_photos_request:
                pa_photos_request.side_effect = mock_raise_response_error

                # Let the initial authenticate() call succeed,
                # but do nothing on the second try.
                orig_authenticate = PyiCloudService.authenticate

                def mocked_authenticate(self: PyiCloudService) -> None:
                    if not hasattr(self, "already_authenticated"):
                        orig_authenticate(self)
                        setattr(self, "already_authenticated", True)

                with mock.patch("icloudpd.constants.WAIT_SECONDS", 0):
                    with mock.patch.object(
                        PyiCloudService, "authenticate", new=mocked_authenticate
                    ):
                        runner = CliRunner(env={
                            "CLIENT_ID": "DE309E26-942E-11E8-92F5-14109FE0B321"
                        })
                        result = runner.invoke(
                            main,
                            [
                                "--username",
                                "jdoe@gmail.com",
                                "--password",
                                "password1",
                                "--recent",
                                "1",
                                "--skip-videos",
                                "--skip-live-photos",
                                "--no-progress-bar",
                                "--threads-num",
                                "1",
                                "-d",
                                data_dir,
                                "--cookie-directory",
                                cookie_dir,
                            ],
                        )
                        print_result_exception(result)

                        assert result.exit_code == 1

        files_in_result = glob.glob(os.path.join(
            data_dir, "**/*.*"), recursive=True)

        assert sum(1 for _ in files_in_result) == 0

    def test_missing_size(self) -> None:
        base_dir = os.path.join(self.fixtures_path, inspect.stack()[0][3])
        cookie_dir = os.path.join(base_dir, "cookie")
        data_dir = os.path.join(base_dir, "data")

        for dir in [base_dir, cookie_dir, data_dir]:
            recreate_path(dir)

        with mock.patch.object(PhotoAsset, "download") as pa_download:
            pa_download.return_value = False

            with vcr.use_cassette(os.path.join(self.vcr_path, "listing_photos.yml")):
                # Pass fixed client ID via environment variable
                runner = CliRunner(env={
                    "CLIENT_ID": "DE309E26-942E-11E8-92F5-14109FE0B321"
                })
                result = runner.invoke(
                    main,
                    [
                        "--username",
                        "jdoe@gmail.com",
                        "--password",
                        "password1",
                        "--recent",
                        "3",
                        "--no-progress-bar",
                        "--threads-num",
                        "1",
                        "-d",
                        data_dir,
                        "--cookie-directory",
                        cookie_dir,
                    ],
                )
                print_result_exception(result)

                self.assertIn(
                    "DEBUG    Looking up all photos and videos from album All Photos...", self._caplog.text
                )
                self.assertIn(
                    f"INFO     Downloading 3 original photos and videos to {data_dir} ...",
                    self._caplog.text,
                )

                # These error messages should not be repeated more than once for each size
                for filename in ["IMG_7409.JPG", "IMG_7408.JPG", "IMG_7407.JPG"]:
                    for size in ["original", "originalVideo"]:
                        self.assertEqual(
                            sum(1 for line in self._caplog.text.splitlines() if line ==
                                f"ERROR    Could not find URL to download {filename} for size {size}"
                            ),
                            1,
                            f"Errors for {filename} size {size}"
                        )

                self.assertIn(
                    "INFO     All photos have been downloaded", self._caplog.text
                )
                self.assertEqual(result.exit_code, 0, "Exit code")

        files_in_result = glob.glob(os.path.join(
            data_dir, "**/*.*"), recursive=True)

        self.assertEqual(sum(1 for _ in files_in_result), 0, "Files in result")

    def test_size_fallback_to_original(self) -> None:
        base_dir = os.path.join(self.fixtures_path, inspect.stack()[0][3])
        cookie_dir = os.path.join(base_dir, "cookie")
        data_dir = os.path.join(base_dir, "data")

        for dir in [base_dir, cookie_dir, data_dir]:
            recreate_path(dir)

        with mock.patch("icloudpd.download.download_media") as dp_patched:
            dp_patched.return_value = True

            with mock.patch("icloudpd.download.os.utime") as ut_patched:
                ut_patched.return_value = None

                with mock.patch.object(PhotoAsset, "versions") as pa:
                    pa.return_value = ["original", "medium"]

                    with vcr.use_cassette(os.path.join(self.vcr_path, "listing_photos.yml")):
                        # Pass fixed client ID via environment variable
                        runner = CliRunner(env={
                            "CLIENT_ID": "DE309E26-942E-11E8-92F5-14109FE0B321"
                        })
                        result = runner.invoke(
                            main,
                            [
                                "--username",
                                "jdoe@gmail.com",
                                "--password",
                                "password1",
                                "--recent",
                                "1",
                                "--size",
                                "thumb",
                                "--no-progress-bar",
                                "--threads-num",
                                "1",
                                "-d",
                                data_dir,
                                "--cookie-directory",
                                cookie_dir,
                            ],
                        )
                        print_result_exception(result)
                        self.assertIn(
                            "DEBUG    Looking up all photos and videos from album All Photos...",
                            self._caplog.text,
                        )
                        self.assertIn(
                            f"INFO     Downloading the first thumb photo or video to {data_dir} ...",
                            self._caplog.text,
                        )
                        self.assertIn(
                            f"DEBUG    Downloading {os.path.join(data_dir, os.path.normpath('2018/07/31/IMG_7409.JPG'))}",
                            self._caplog.text,
                        )
                        self.assertIn(
                            "INFO     All photos have been downloaded", self._caplog.text
                        )
                        dp_patched.assert_called_once_with(
                            ANY,
                            False,
                            ANY,
                            ANY,
                            f"{os.path.join(data_dir, os.path.normpath('2018/07/31/IMG_7409.JPG'))}",
                            "original",
                        )

                        assert result.exit_code == 0

        files_in_result = glob.glob(os.path.join(
            data_dir, "**/*.*"), recursive=True)

        assert sum(1 for _ in files_in_result) == 0

    def test_force_size(self) -> None:
        base_dir = os.path.join(self.fixtures_path, inspect.stack()[0][3])
        cookie_dir = os.path.join(base_dir, "cookie")
        data_dir = os.path.join(base_dir, "data")

        for dir in [base_dir, cookie_dir, data_dir]:
            recreate_path(dir)

        with mock.patch("icloudpd.download.download_media") as dp_patched:
            dp_patched.return_value = True

            with mock.patch.object(PhotoAsset, "versions") as pa:
                pa.return_value = ["original", "medium"]

                with vcr.use_cassette(os.path.join(self.vcr_path, "listing_photos.yml")):
                    # Pass fixed client ID via environment variable
                    runner = CliRunner(env={
                        "CLIENT_ID": "DE309E26-942E-11E8-92F5-14109FE0B321"
                    })
                    result = runner.invoke(
                        main,
                        [
                            "--username",
                            "jdoe@gmail.com",
                            "--password",
                            "password1",
                            "--recent",
                            "1",
                            "--size",
                            "thumb",
                            "--force-size",
                            "--no-progress-bar",
                            "--threads-num",
                            "1",
                            "-d",
                            data_dir,
                            "--cookie-directory",
                            cookie_dir,
                        ],
                    )
                    print_result_exception(result)

                    self.assertIn(
                        "DEBUG    Looking up all photos and videos from album All Photos...",
                        self._caplog.text,
                    )
                    self.assertIn(
                        f"INFO     Downloading the first thumb photo or video to {data_dir} ...",
                        self._caplog.text,
                    )
                    self.assertIn(
                        "ERROR    thumb size does not exist for IMG_7409.JPG. Skipping...",
                        self._caplog.text,
                    )
                    self.assertIn(
                        "INFO     All photos have been downloaded", self._caplog.text
                    )
                    dp_patched.assert_not_called

                    assert result.exit_code == 0

        files_in_result = glob.glob(os.path.join(
            data_dir, "**/*.*"), recursive=True)

        assert sum(1 for _ in files_in_result) == 0

    def test_invalid_creation_date(self) -> None:
        base_dir = os.path.join(self.fixtures_path, inspect.stack()[0][3])
        cookie_dir = os.path.join(base_dir, "cookie")
        data_dir = os.path.join(base_dir, "data")

        for dir in [base_dir, cookie_dir, data_dir]:
            recreate_path(dir)

        files_to_download = [
            '2018/01/01/IMG_7409.JPG'
        ]

        with mock.patch.object(PhotoAsset, "created", new_callable=mock.PropertyMock) as dt_mock:
            # Can't mock `astimezone` because it's a readonly property, so have to
            # create a new class that inherits from datetime.datetime
            class NewDateTime(datetime.datetime):
                def astimezone(self, _tz:(Optional[Any])=None) -> NoReturn:
                    raise ValueError('Invalid date')
            dt_mock.return_value = NewDateTime(2018, 1, 1, 0, 0, 0)

            with vcr.use_cassette(os.path.join(self.vcr_path, "listing_photos.yml")):
                # Pass fixed client ID via environment variable
                runner = CliRunner(env={
                    "CLIENT_ID": "DE309E26-942E-11E8-92F5-14109FE0B321"
                })
                result = runner.invoke(
                    main,
                    [
                        "--username",
                        "jdoe@gmail.com",
                        "--password",
                        "password1",
                        "--recent",
                        "1",
                        "--skip-live-photos",
                        "--no-progress-bar",
                        "--threads-num",
                        "1",
                        "-d",
                        data_dir,
                        "--cookie-directory",
                        cookie_dir,
                    ],
                )
                print_result_exception(result)

                self.assertIn(
                    "DEBUG    Looking up all photos and videos from album All Photos...",
                    self._caplog.text,
                )
                self.assertIn(
                    f"INFO     Downloading the first original photo or video to {data_dir} ...",
                    self._caplog.text,
                )
                self.assertIn(
                    "ERROR    Could not convert photo created date to local timezone (2018-01-01 00:00:00)",
                    self._caplog.text,
                )
                self.assertIn(
                    f"DEBUG    Downloading {os.path.join(data_dir, os.path.normpath('2018/01/01/IMG_7409.JPG'))}",
                    self._caplog.text,
                )
                self.assertIn(
                    "INFO     All photos have been downloaded", self._caplog.text
                )
                assert result.exit_code == 0

        files_in_result = glob.glob(os.path.join(
            data_dir, "**/*.*"), recursive=True)

        assert sum(1 for _ in files_in_result) == len(files_to_download)

        for file_name in files_to_download:
            assert os.path.exists(os.path.join(data_dir, os.path.normpath(
                file_name))), f"File {file_name} expected, but does not exist"

    @pytest.mark.skipif(sys.platform == 'win32',
                        reason="does not run on windows")
    @pytest.mark.skipif(sys.platform == 'darwin',
                        reason="does not run on mac")
    def test_invalid_creation_year(self) -> None:
        base_dir = os.path.join(self.fixtures_path, inspect.stack()[0][3])
        cookie_dir = os.path.join(base_dir, "cookie")
        data_dir = os.path.join(base_dir, "data")

        for dir in [base_dir, cookie_dir, data_dir]:
            recreate_path(dir)

        files_to_download = [
            '5/01/01/IMG_7409.JPG'
        ]

        with mock.patch.object(PhotoAsset, "created", new_callable=mock.PropertyMock) as dt_mock:
            # Can't mock `astimezone` because it's a readonly property, so have to
            # create a new class that inherits from datetime.datetime
            class NewDateTime(datetime.datetime):
                def astimezone(self, _tz:(Optional[Any])=None) -> NoReturn:
                    raise ValueError('Invalid date')
            dt_mock.return_value = NewDateTime(5, 1, 1, 0, 0, 0)

            with vcr.use_cassette(os.path.join(self.vcr_path, "listing_photos.yml")):
                # Pass fixed client ID via environment variable
                runner = CliRunner(env={
                    "CLIENT_ID": "DE309E26-942E-11E8-92F5-14109FE0B321"
                })
                result = runner.invoke(
                    main,
                    [
                        "--username",
                        "jdoe@gmail.com",
                        "--password",
                        "password1",
                        "--recent",
                        "1",
                        "--skip-live-photos",
                        "--no-progress-bar",
                        "--threads-num",
                        "1",
                        "-d",
                        data_dir,
                        "--cookie-directory",
                        cookie_dir,
                    ],
                )
                print_result_exception(result)

                self.assertIn(
                    "DEBUG    Looking up all photos and videos from album All Photos...",
                    self._caplog.text,
                )
                self.assertIn(
                    f"INFO     Downloading the first original photo or video to {data_dir} ...",
                    self._caplog.text,
                )
                self.assertIn(
                    "ERROR    Could not convert photo created date to local timezone (0005-01-01 00:00:00)",
                    self._caplog.text,
                )
                self.assertIn(
                    f"DEBUG    Downloading {os.path.join(data_dir, os.path.normpath('5/01/01/IMG_7409.JPG'))}",
                    self._caplog.text,
                )
                self.assertIn(
                    "INFO     All photos have been downloaded", self._caplog.text
                )
                assert result.exit_code == 0

        files_in_result = glob.glob(os.path.join(
            data_dir, "**/*.*"), recursive=True)

        assert sum(1 for _ in files_in_result) == len(files_to_download)

        for file_name in files_to_download:
            assert os.path.exists(os.path.join(data_dir, os.path.normpath(
                file_name))), f"File {file_name} expected, but does not exist"

    def test_unknown_item_type(self) -> None:
        base_dir = os.path.join(self.fixtures_path, inspect.stack()[0][3])
        cookie_dir = os.path.join(base_dir, "cookie")
        data_dir = os.path.join(base_dir, "data")

        for dir in [base_dir, cookie_dir, data_dir]:
            recreate_path(dir)

        with mock.patch("icloudpd.download.download_media") as dp_patched:
            dp_patched.return_value = True

            with mock.patch.object(PhotoAsset, "item_type", new_callable=mock.PropertyMock) as it_mock:
                it_mock.return_value = 'unknown'

                with vcr.use_cassette(os.path.join(self.vcr_path, "listing_photos.yml")):
                    # Pass fixed client ID via environment variable
                    runner = CliRunner(env={
                        "CLIENT_ID": "DE309E26-942E-11E8-92F5-14109FE0B321"
                    })
                    result = runner.invoke(
                        main,
                        [
                            "--username",
                            "jdoe@gmail.com",
                            "--password",
                            "password1",
                            "--recent",
                            "1",
                            "--no-progress-bar",
                            "--threads-num",
                            "1",
                            "-d",
                            data_dir,
                            "--cookie-directory",
                            cookie_dir,
                        ],
                    )
                    print_result_exception(result)

                    self.assertIn(
                        "DEBUG    Looking up all photos and videos from album All Photos...",
                        self._caplog.text,
                    )
                    self.assertIn(
                        f"INFO     Downloading the first original photo or video to {data_dir} ...",
                        self._caplog.text,
                    )
                    self.assertIn(
                        "DEBUG    Skipping IMG_7409.JPG, only downloading photos and videos. (Item type was: unknown)",
                        self._caplog.text,
                    )
                    self.assertIn(
                        "INFO     All photos have been downloaded", self._caplog.text
                    )
                    dp_patched.assert_not_called

                    assert result.exit_code == 0

        files_in_result = glob.glob(os.path.join(
            data_dir, "**/*.*"), recursive=True)

        assert sum(1 for _ in files_in_result) == 0

    def test_download_and_dedupe_existing_photos(self) -> None:
        base_dir = os.path.join(self.fixtures_path, inspect.stack()[0][3])
        cookie_dir = os.path.join(base_dir, "cookie")
        data_dir = os.path.join(base_dir, "data")

        for dir in [base_dir, cookie_dir, data_dir]:
            recreate_path(dir)

        os.makedirs(os.path.join(data_dir, os.path.normpath("2018/07/31/")))
        with open(os.path.join(data_dir, os.path.normpath("2018/07/31/IMG_7409.JPG")), "a") as f:
            f.truncate(1)
        with open(os.path.join(data_dir, os.path.normpath("2018/07/31/IMG_7409.MOV")), "a") as f:
            f.truncate(1)
        os.makedirs(os.path.join(data_dir, os.path.normpath("2018/07/30/")))
        with open(os.path.join(data_dir, os.path.normpath("2018/07/30/IMG_7408.JPG")), "a") as f:
            f.truncate(1151066)
        with open(os.path.join(data_dir, os.path.normpath("2018/07/30/IMG_7408.MOV")), "a") as f:
            f.truncate(1606512)

        # Download the first photo, but mock the video download
        orig_download = PhotoAsset.download

        def mocked_download(self: PhotoAsset, size: str) -> Optional[Response]:
            if not hasattr(PhotoAsset, "already_downloaded"):
                response = orig_download(self, size)
                setattr(PhotoAsset, "already_downloaded", True)
                return response
            return mock.MagicMock()

        with mock.patch.object(PhotoAsset, "download", new=mocked_download):
            with vcr.use_cassette(os.path.join(self.vcr_path, "listing_photos.yml")):
                # Pass fixed client ID via environment variable
                runner = CliRunner(env={
                    "CLIENT_ID": "DE309E26-942E-11E8-92F5-14109FE0B321"
                })
                result = runner.invoke(
                    main,
                    [
                        "--username",
                        "jdoe@gmail.com",
                        "--password",
                        "password1",
                        "--recent",
                        "5",
                        "--skip-videos",
                        # "--set-exif-datetime",
                        "--no-progress-bar",
                        "-d",
                        data_dir,
                        "--cookie-directory",
                        cookie_dir,
                        "--threads-num",
                        "1",
                    ],
                )
                print_result_exception(result)

                self.assertIn(
                    "DEBUG    Looking up all photos from album All Photos...", self._caplog.text)
                self.assertIn(
                    f"INFO     Downloading 5 original photos to {data_dir} ...",
                    self._caplog.text,
                )
                self.assertIn(
                    f"DEBUG    {os.path.join(data_dir, os.path.normpath('2018/07/31/IMG_7409-1884695.JPG'))} deduplicated",
                    self._caplog.text,
                )
                self.assertIn(
                    f"DEBUG    Downloading {os.path.join(data_dir, os.path.normpath('2018/07/31/IMG_7409-1884695.JPG'))}",
                    self._caplog.text,
                )
                self.assertIn(
                    f"DEBUG    {os.path.join(data_dir, os.path.normpath('2018/07/31/IMG_7409-3294075.MOV'))} deduplicated",
                    self._caplog.text,
                )
                self.assertIn(
                    f"DEBUG    Downloading {os.path.join(data_dir, os.path.normpath('2018/07/31/IMG_7409-3294075.MOV'))}",
                    self._caplog.text,
                )
                self.assertIn(
                    f"DEBUG    {os.path.join(data_dir, os.path.normpath('2018/07/30/IMG_7408.JPG'))} already exists",
                    self._caplog.text,
                )
                self.assertIn(
                    f"DEBUG    {os.path.join(data_dir, os.path.normpath('2018/07/30/IMG_7408.MOV'))} already exists",
                    self._caplog.text,
                )
                self.assertIn(
                    "DEBUG    Skipping IMG_7405.MOV, only downloading photos.", self._caplog.text
                )
                self.assertIn(
                    "DEBUG    Skipping IMG_7404.MOV, only downloading photos.", self._caplog.text
                )
                self.assertIn(
                    "INFO     All photos have been downloaded", self._caplog.text
                )

                # Check that file was downloaded
                self.assertTrue(
                    os.path.exists(os.path.join(data_dir, os.path.normpath("2018/07/31/IMG_7409-1884695.JPG"))))
                # Check that mtime was updated to the photo creation date
                photo_mtime = os.path.getmtime(os.path.join(
                    data_dir, os.path.normpath("2018/07/31/IMG_7409-1884695.JPG")))
                photo_modified_time = datetime.datetime.utcfromtimestamp(
                    photo_mtime)
                self.assertEqual(
                    "2018-07-31 07:22:24",
                    photo_modified_time.strftime('%Y-%m-%d %H:%M:%S'))
                self.assertTrue(
                    os.path.exists(os.path.join(data_dir, os.path.normpath("2018/07/31/IMG_7409-3294075.MOV"))))
                photo_mtime = os.path.getmtime(os.path.join(
                    data_dir, os.path.normpath("2018/07/31/IMG_7409-3294075.MOV")))
                photo_modified_time = datetime.datetime.utcfromtimestamp(
                    photo_mtime)
                self.assertEqual(
                    "2018-07-31 07:22:24",
                    photo_modified_time.strftime('%Y-%m-%d %H:%M:%S'))

                assert result.exit_code == 0

    def test_download_photos_and_set_exif_exceptions(self) -> None:
        base_dir = os.path.join(self.fixtures_path, inspect.stack()[0][3])
        cookie_dir = os.path.join(base_dir, "cookie")
        data_dir = os.path.join(base_dir, "data")

        for dir in [base_dir, cookie_dir, data_dir]:
            recreate_path(dir)

        files_to_download = [
            '2018/07/31/IMG_7409.JPG'
        ]

        with mock.patch.object(piexif, "insert") as piexif_patched:
            piexif_patched.side_effect = InvalidImageDataError
            with mock.patch(
                "icloudpd.exif_datetime.get_photo_exif"
            ) as get_exif_patched:
                get_exif_patched.return_value = False
                with vcr.use_cassette(os.path.join(self.vcr_path, "listing_photos.yml")):
                    # Pass fixed client ID via environment variable
                    runner = CliRunner(env={
                        "CLIENT_ID": "DE309E26-942E-11E8-92F5-14109FE0B321"
                    })
                    result = runner.invoke(
                        main,
                        [
                            "--username",
                            "jdoe@gmail.com",
                            "--password",
                            "password1",
                            "--recent",
                            "1",
                            "--skip-videos",
                            "--skip-live-photos",
                            "--set-exif-datetime",
                            "--no-progress-bar",
                            "--threads-num",
                            "1",
                            "-d",
                            data_dir,
                            "--cookie-directory",
                            cookie_dir,
                        ],
                    )
                    print_result_exception(result)

                    self.assertIn(
                        "DEBUG    Looking up all photos from album All Photos...", self._caplog.text)
                    self.assertIn(
                        f"INFO     Downloading the first original photo to {data_dir} ...",
                        self._caplog.text,
                    )
                    self.assertIn(
                        f"DEBUG    Downloading {os.path.join(data_dir, os.path.normpath('2018/07/31/IMG_7409.JPG'))}",
                        self._caplog.text,
                    )
                    # 2018:07:31 07:22:24 utc
                    expectedDatetime = datetime.datetime(
                        2018, 7, 31, 7, 22, 24, tzinfo=datetime.timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S%z")
                    self.assertIn(
                        f"DEBUG    Setting EXIF timestamp for {os.path.join(data_dir, os.path.normpath('2018/07/31/IMG_7409.JPG'))}: {expectedDatetime}",
                        self._caplog.text,
                    )
                    self.assertIn(
                        f"DEBUG    Error setting EXIF data for {os.path.join(data_dir, os.path.normpath('2018/07/31/IMG_7409.JPG'))}",
                        self._caplog.text,
                    )
                    self.assertIn(
                        "INFO     All photos have been downloaded", self._caplog.text
                    )
                    assert result.exit_code == 0

        files_in_result = glob.glob(os.path.join(
            data_dir, "**/*.*"), recursive=True)

        assert sum(1 for _ in files_in_result) == len(files_to_download)

        for file_name in files_to_download:
            assert os.path.exists(os.path.join(data_dir, os.path.normpath(
                file_name))), f"File {file_name} expected, but does not exist"

    def test_download_chinese(self) -> None:
        base_dir = os.path.join(
            self.fixtures_path, inspect.stack()[0][3], "中文")
        cookie_dir = os.path.join(base_dir, "cookie")
        data_dir = os.path.join(base_dir, "data")

        for dir in [base_dir, cookie_dir, data_dir]:
            recreate_path(dir)

        files_to_download = [
            '2018/07/31/IMG_7409.JPG'
        ]

        with vcr.use_cassette(os.path.join(self.vcr_path, "listing_photos.yml")):
            # Pass fixed client ID via environment variable
            runner = CliRunner(env={
                "CLIENT_ID": "DE309E26-942E-11E8-92F5-14109FE0B321"
            })
            result = runner.invoke(
                main,
                [
                    "--username",
                    "jdoe@gmail.com",
                    "--password",
                    "password1",
                    "--recent",
                    "1",
                    "--skip-videos",
                    "--skip-live-photos",
                    "--set-exif-datetime",
                    "--no-progress-bar",
                    "--threads-num",
                    "1",
                    "-d",
                    data_dir,
                    "--cookie-directory",
                    cookie_dir,
                ],
            )
            print_result_exception(result)

            self.assertIn(
                "DEBUG    Looking up all photos from album All Photos...", self._caplog.text)
            self.assertIn(
                f'INFO     Downloading the first original photo to {data_dir} ...',
                self._caplog.text,
            )
            self.assertIn(
                f"DEBUG    Downloading {os.path.join(data_dir, os.path.normpath('2018/07/31/IMG_7409.JPG'))}",
                self._caplog.text,
            )
            self.assertNotIn(
                "IMG_7409.MOV",
                self._caplog.text,
            )
            self.assertIn(
                "INFO     All photos have been downloaded", self._caplog.text
            )

            # Check that file was downloaded
            self.assertTrue(
                os.path.exists(os.path.join(data_dir, os.path.normpath('2018/07/31/IMG_7409.JPG'))))
            # Check that mtime was updated to the photo creation date
            photo_mtime = os.path.getmtime(os.path.join(
                data_dir, os.path.normpath('2018/07/31/IMG_7409.JPG')))
            photo_modified_time = datetime.datetime.utcfromtimestamp(
                photo_mtime)
            self.assertEqual(
                "2018-07-31 07:22:24",
                photo_modified_time.strftime('%Y-%m-%d %H:%M:%S'))

            assert result.exit_code == 0

        files_in_result = glob.glob(os.path.join(
            data_dir, "**/*.*"), recursive=True)

        assert sum(1 for _ in files_in_result) == len(files_to_download)

        for file_name in files_to_download:
            assert os.path.exists(os.path.join(data_dir, os.path.normpath(
                file_name))), f"File {file_name} expected, but does not exist"

    def test_download_one_recent_live_photo(self) -> None:
        base_dir = os.path.join(self.fixtures_path, inspect.stack()[0][3])
        cookie_dir = os.path.join(base_dir, "cookie")
        data_dir = os.path.join(base_dir, "data")

        for dir in [base_dir, cookie_dir, data_dir]:
            recreate_path(dir)

        files_to_download = [
            '2018/07/31/IMG_7409.JPG',
            '2018/07/31/IMG_7409.MOV',
        ]

        # Download the first photo, but mock the video download
        orig_download = PhotoAsset.download

        def mocked_download(pa: PhotoAsset, size:str) -> Optional[Response]:
            if not hasattr(PhotoAsset, "already_downloaded"):
                response = orig_download(pa, size)
                setattr(PhotoAsset, "already_downloaded", True)
                return response
            return mock.MagicMock()

        with mock.patch.object(PhotoAsset, "download", new=mocked_download):
            with mock.patch(
                "icloudpd.exif_datetime.get_photo_exif"
            ) as get_exif_patched:
                get_exif_patched.return_value = False
                with vcr.use_cassette(os.path.join(self.vcr_path, "listing_photos.yml")):
                    # Pass fixed client ID via environment variable
                    runner = CliRunner(env={
                        "CLIENT_ID": "DE309E26-942E-11E8-92F5-14109FE0B321"
                    })
                    result = runner.invoke(
                        main,
                        [
                            "--username",
                            "jdoe@gmail.com",
                            "--password",
                            "password1",
                            "--recent",
                            "1",
                            # "--set-exif-datetime",
                            # '--skip-videos',
                            # "--skip-live-photos",
                            "--no-progress-bar",
                            "--threads-num",
                            "1",
                            "-d",
                            data_dir,
                            "--cookie-directory",
                            cookie_dir,
                        ],
                    )
                    print_result_exception(result)

                    self.assertIn(
                        "DEBUG    Looking up all photos and videos from album All Photos...",
                        self._caplog.text,
                    )
                    self.assertIn(
                        f"INFO     Downloading the first original photo or video to {data_dir} ...",
                        self._caplog.text,
                    )
                    self.assertIn(
                        f"DEBUG    Downloading {os.path.join(data_dir, os.path.normpath('2018/07/31/IMG_7409.JPG'))}",
                        self._caplog.text,
                    )
                    self.assertIn(
                        f"DEBUG    Downloading {os.path.join(data_dir, os.path.normpath('2018/07/31/IMG_7409.MOV'))}",
                        self._caplog.text,
                    )
                    self.assertIn(
                        "INFO     All photos have been downloaded", self._caplog.text
                    )
                    assert result.exit_code == 0

        files_in_result = glob.glob(os.path.join(
            data_dir, "**/*.*"), recursive=True)

        assert sum(1 for _ in files_in_result) == len(files_to_download)

        for file_name in files_to_download:
            assert os.path.exists(os.path.join(data_dir, os.path.normpath(
                file_name))), f"File {file_name} expected, but does not exist"

    def test_download_one_recent_live_photo_chinese(self) -> None:
        base_dir = os.path.join(self.fixtures_path, inspect.stack()[0][3])
        cookie_dir = os.path.join(base_dir, "cookie")
        data_dir = os.path.join(base_dir, "data")

        for dir in [base_dir, cookie_dir, data_dir]:
            recreate_path(dir)

        files_to_download = [
            '2018/07/31/IMG_中文_7409.JPG',  # SU1HX+S4reaWh183NDA5LkpQRw==
            '2018/07/31/IMG_中文_7409.MOV',
        ]

        # Download the first photo, but mock the video download
        orig_download = PhotoAsset.download

        def mocked_download(pa: PhotoAsset, size:str) -> Optional[Response]:
            if not hasattr(PhotoAsset, "already_downloaded"):
                response = orig_download(pa, size)
                setattr(PhotoAsset, "already_downloaded", True)
                return response
            return mock.MagicMock()

        with mock.patch.object(PhotoAsset, "download", new=mocked_download):
            with mock.patch(
                "icloudpd.exif_datetime.get_photo_exif"
            ) as get_exif_patched:
                get_exif_patched.return_value = False
                with vcr.use_cassette(os.path.join(self.vcr_path, "listing_photos_chinese.yml")):
                    # Pass fixed client ID via environment variable
                    runner = CliRunner(env={
                        "CLIENT_ID": "DE309E26-942E-11E8-92F5-14109FE0B321"
                    })
                    result = runner.invoke(
                        main,
                        [
                            "--username",
                            "jdoe@gmail.com",
                            "--password",
                            "password1",
                            "--recent",
                            "1",
                            # "--set-exif-datetime",
                            # '--skip-videos',
                            # "--skip-live-photos",
                            "--no-progress-bar",
                            "--keep-unicode-in-filenames",
                            "true",
                            "--threads-num",
                            "1",
                            "-d",
                            data_dir,
                            "--cookie-directory",
                            cookie_dir,
                        ],
                    )
                    print_result_exception(result)

                    self.assertIn(
                        "DEBUG    Looking up all photos and videos from album All Photos...",
                        self._caplog.text,
                    )
                    self.assertIn(
                        f"INFO     Downloading the first original photo or video to {data_dir} ...",
                        self._caplog.text,
                    )
                    self.assertIn(
                        f"DEBUG    Downloading {os.path.join(data_dir, os.path.normpath('2018/07/31/IMG_中文_7409.JPG'))}",
                        self._caplog.text,
                    )
                    self.assertIn(
                        f"DEBUG    Downloading {os.path.join(data_dir, os.path.normpath('2018/07/31/IMG_中文_7409.MOV'))}",
                        self._caplog.text,
                    )
                    self.assertIn(
                        "INFO     All photos have been downloaded", self._caplog.text
                    )
                    assert result.exit_code == 0

        files_in_result = glob.glob(os.path.join(
            data_dir, "**/*.*"), recursive=True)

        assert sum(1 for _ in files_in_result) == len(files_to_download)

        for file_name in files_to_download:
            assert os.path.exists(os.path.join(data_dir, os.path.normpath(
                file_name))), f"File {file_name} expected, but does not exist"

    def test_download_after_delete(self) -> None:
        base_dir = os.path.join(self.fixtures_path, inspect.stack()[0][3])
        cookie_dir = os.path.join(base_dir, "cookie")
        data_dir = os.path.join(base_dir, "data")

        for dir in [base_dir, cookie_dir, data_dir]:
            recreate_path(dir)

        files_to_download = [
            '2018/07/31/IMG_7409.JPG'
        ]

        with mock.patch.object(piexif, "insert") as piexif_patched:
            piexif_patched.side_effect = InvalidImageDataError
            with mock.patch(
                "icloudpd.exif_datetime.get_photo_exif"
            ) as get_exif_patched:
                get_exif_patched.return_value = False
                with vcr.use_cassette(os.path.join(self.vcr_path, "listing_photos.yml")) as cass:
                    # Pass fixed client ID via environment variable
                    runner = CliRunner(env={
                        "CLIENT_ID": "DE309E26-942E-11E8-92F5-14109FE0B321"
                    })
                    result = runner.invoke(
                        main,
                        [
                            "--username",
                            "jdoe@gmail.com",
                            "--password",
                            "password1",
                            "--recent",
                            "1",
                            "--skip-videos",
                            "--skip-live-photos",
                            "--no-progress-bar",
                            "--threads-num",
                            "1",
                            "--delete-after-download",
                            "-d",
                            data_dir,
                            "--cookie-directory",
                            cookie_dir,
                        ],
                    )
                    print_result_exception(result)

                    self.assertIn(
                        "DEBUG    Looking up all photos from album All Photos...", self._caplog.text)
                    self.assertIn(
                        f"INFO     Downloading the first original photo to {data_dir} ...",
                        self._caplog.text,
                    )
                    self.assertIn(
                        f"DEBUG    Downloading {os.path.join(data_dir, os.path.normpath('2018/07/31/IMG_7409.JPG'))}",
                        self._caplog.text,
                    )
                    self.assertIn(
                        "INFO     Deleted IMG_7409.JPG in iCloud", self._caplog.text
                    )
                    self.assertIn(
                        "INFO     All photos have been downloaded", self._caplog.text
                    )
                    assert cass.all_played
                    assert result.exit_code == 0

        files_in_result = glob.glob(os.path.join(
            data_dir, "**/*.*"), recursive=True)

        assert sum(1 for _ in files_in_result) == len(files_to_download)

        for file_name in files_to_download:
            assert os.path.exists(os.path.join(data_dir, os.path.normpath(
                file_name))), f"File {file_name} expected, but does not exist"

    def test_download_after_delete_fail(self) -> None:
        base_dir = os.path.join(self.fixtures_path, inspect.stack()[0][3])
        cookie_dir = os.path.join(base_dir, "cookie")
        data_dir = os.path.join(base_dir, "data")

        for dir in [base_dir, cookie_dir, data_dir]:
            recreate_path(dir)

        with vcr.use_cassette(os.path.join(self.vcr_path, "listing_photos_no_delete.yml")) as cass:
            # Pass fixed client ID via environment variable
            runner = CliRunner(env={
                "CLIENT_ID": "DE309E26-942E-11E8-92F5-14109FE0B321"
            })
            result = runner.invoke(
                main,
                [
                    "--username",
                    "jdoe@gmail.com",
                    "--password",
                    "password1",
                    "--recent",
                    "1",
                    "--skip-videos",
                    "--skip-live-photos",
                    "--no-progress-bar",
                    "--threads-num",
                    "1",
                    "--delete-after-download",
                    "-d",
                    data_dir,
                    "--cookie-directory",
                    cookie_dir,
                ],
            )
            print_result_exception(result)

            self.assertIn(
                "DEBUG    Looking up all photos from album All Photos...", self._caplog.text)
            self.assertIn(
                f"INFO     Downloading the first original photo to {data_dir} ...",
                self._caplog.text,
            )
            self.assertIn(
                f"DEBUG    Downloading {os.path.join(data_dir, os.path.normpath('2018/07/31/IMG_7409.JPG'))}",
                self._caplog.text,
            )
            self.assertNotIn(
                "INFO     Deleted IMG_7409.JPG in iCloud", self._caplog.text
            )
            self.assertIn(
                "INFO     All photos have been downloaded", self._caplog.text
            )
            assert cass.all_played
            assert result.exit_code == 0

        files_in_result = glob.glob(os.path.join(
            data_dir, "**/*.*"), recursive=True)

        assert sum(1 for _ in files_in_result) == 0

    def test_download_over_old_original_photos(self) -> None:
        base_dir = os.path.join(self.fixtures_path, inspect.stack()[0][3])
        cookie_dir = os.path.join(base_dir, "cookie")
        data_dir = os.path.join(base_dir, "data")

        for dir in [base_dir, cookie_dir, data_dir]:
            recreate_path(dir)

        files_to_create = [
            ("2018/07/30/IMG_7408-original.JPG", 1151066),
            ("2018/07/30/IMG_7407.JPG", 656257)
        ]

        files_to_download = [
            '2018/07/31/IMG_7409.JPG'
        ]

        os.makedirs(os.path.join(data_dir, "2018/07/30/"))
        for (file_name, file_size) in files_to_create:
            with open(os.path.join(data_dir, file_name), "a") as f:
                f.truncate(file_size)

        with vcr.use_cassette(os.path.join(self.vcr_path, "listing_photos.yml")):
            # Pass fixed client ID via environment variable
            runner = CliRunner(env={
                "CLIENT_ID": "DE309E26-942E-11E8-92F5-14109FE0B321"
            })
            result = runner.invoke(
                main,
                [
                    "--username",
                    "jdoe@gmail.com",
                    "--password",
                    "password1",
                    "--recent",
                    "5",
                    "--skip-videos",
                    "--skip-live-photos",
                    "--set-exif-datetime",
                    "--no-progress-bar",
                    "--threads-num",
                    "1",
                    "-d",
                    data_dir,
                    "--cookie-directory",
                    cookie_dir,
                ],
            )
            print_result_exception(result)

            self.assertIn(
                "DEBUG    Looking up all photos from album All Photos...", self._caplog.text)
            self.assertIn(
                f"INFO     Downloading 5 original photos to {data_dir} ...",
                self._caplog.text,
            )
            self.assertIn(
                f"DEBUG    Downloading {os.path.join(data_dir, os.path.normpath('2018/07/31/IMG_7409.JPG'))}",
                self._caplog.text,
            )
            self.assertNotIn(
                "IMG_7409.MOV",
                self._caplog.text,
            )
            self.assertIn(
                f"DEBUG    {os.path.join(data_dir, os.path.normpath('2018/07/30/IMG_7408.JPG'))} already exists",
                self._caplog.text,
            )
            self.assertIn(
                f"DEBUG    {os.path.join(data_dir, os.path.normpath('2018/07/30/IMG_7407.JPG'))} already exists",
                self._caplog.text,
            )
            self.assertIn(
                "DEBUG    Skipping IMG_7405.MOV, only downloading photos.",
                self._caplog.text,
            )
            self.assertIn(
                "DEBUG    Skipping IMG_7404.MOV, only downloading photos.",
                self._caplog.text,
            )
            self.assertIn(
                "INFO     All photos have been downloaded", self._caplog.text
            )

            # Check that file was downloaded
            self.assertTrue(
                os.path.exists(os.path.join(data_dir, os.path.normpath("2018/07/31/IMG_7409.JPG"))))
            # Check that mtime was updated to the photo creation date
            photo_mtime = os.path.getmtime(os.path.join(
                data_dir, os.path.normpath("2018/07/31/IMG_7409.JPG")))
            photo_modified_time = datetime.datetime.utcfromtimestamp(
                photo_mtime)
            self.assertEqual(
                "2018-07-31 07:22:24",
                photo_modified_time.strftime('%Y-%m-%d %H:%M:%S'))

            assert result.exit_code == 0

        files_in_result = glob.glob(os.path.join(
            data_dir, "**/*.*"), recursive=True)

        assert sum(1 for _ in files_in_result) == len(
            files_to_download) + len(files_to_create)

        for file_name in files_to_download + ([file_name for (file_name, _) in files_to_create]):
            assert os.path.exists(os.path.join(data_dir, os.path.normpath(
                file_name))), f"File {file_name} expected, but does not exist"

    def test_download_normalized_names(self) -> None:
        base_dir = os.path.join(self.fixtures_path, inspect.stack()[0][3])
        cookie_dir = os.path.join(base_dir, "cookie")
        data_dir = os.path.join(base_dir, "data")

        for dir in [base_dir, cookie_dir, data_dir]:
            recreate_path(dir)

        files_to_create = [
            ("2018/07/30/IMG_7408.JPG", 1151066),
            ("2018/07/30/IMG_7407.JPG", 656257),
        ]

        files_to_download = [
            # <>:"/\|?*  -- windows
            # / & \0x00 -- linux
            # SU1HXzc0MDkuSlBH -> i/n v:a\0l*i?d\p<a>t"h|.JPG -> aS9uIHY6YQBsKmk/ZFxwPGE+dCJofC5KUEc=
            '2018/07/31/i_n v_a_l_i_d_p_a_t_h_.JPG'
        ]

        os.makedirs(os.path.join(data_dir, "2018/07/30/"))
        for (file_name, file_size) in files_to_create:
            with open(os.path.join(data_dir, file_name), "a") as f:
                f.truncate(file_size)

        with vcr.use_cassette(os.path.join(self.vcr_path, "listing_photos_bad_filename.yml")):
            # Pass fixed client ID via environment variable
            runner = CliRunner(env={
                "CLIENT_ID": "DE309E26-942E-11E8-92F5-14109FE0B321"
            })
            result = runner.invoke(
                main,
                [
                    "--username",
                    "jdoe@gmail.com",
                    "--password",
                    "password1",
                    "--recent",
                    "5",
                    "--skip-videos",
                    "--skip-live-photos",
                    "--set-exif-datetime",
                    "--no-progress-bar",
                    "--threads-num",
                    "1",
                    "-d",
                    data_dir,
                    "--cookie-directory",
                    cookie_dir,
                ],
            )
            print_result_exception(result)

            assert result.exit_code == 0

        files_in_result = glob.glob(os.path.join(
            data_dir, "**/*.*"), recursive=True)

        assert sum(1 for _ in files_in_result) == len(
            files_to_create) + len(files_to_download)

        for file_name in files_to_download + ([file_name for (file_name, _) in files_to_create]):
            assert os.path.exists(os.path.join(data_dir, os.path.normpath(
                file_name))), f"File {file_name} expected, but does not exist"

    @pytest.mark.skip("not ready yet. may be not needed")
    def test_download_watch(self) -> None:
        base_dir = os.path.join(self.fixtures_path, inspect.stack()[0][3])
        cookie_dir = os.path.join(base_dir, "cookie")
        data_dir = os.path.join(base_dir, "data")

        for dir in [base_dir, cookie_dir, data_dir]:
            recreate_path(dir)

        files_to_create = [
            ("2018/07/30/IMG_7408.JPG", 1151066),
            ("2018/07/30/IMG_7407.JPG", 656257),
        ]

        files_to_download = [
            '2018/07/31/IMG_7409.JPG'
        ]

        os.makedirs(os.path.join(data_dir, "2018/07/30/"))
        for (file_name, file_size) in files_to_create:
            with open(os.path.join(data_dir, file_name), "a") as f:
                f.truncate(file_size)

        # def my_sleep(_target_duration: int) -> Callable[[int], None]:
        #     counter: int = 0

        #     def sleep_(duration: int) -> None:
        #         if counter > duration:
        #             raise ValueError("SLEEP MOCK")
        #         counter = counter + 1
        #     return sleep_

        with mock.patch("time.sleep") as sleep_patched:
            # import random
            target_duration = 1
            # sleep_patched.side_effect = my_sleep(target_duration)
            with vcr.use_cassette(os.path.join(self.vcr_path, "listing_photos.yml")):
                # Pass fixed client ID via environment variable
                runner = CliRunner(env={
                    "CLIENT_ID": "DE309E26-942E-11E8-92F5-14109FE0B321"
                })
                result = runner.invoke(
                    main,
                    [
                        "--username",
                        "jdoe@gmail.com",
                        "--password",
                        "password1",
                        "--recent",
                        "5",
                        "--skip-videos",
                        "--skip-live-photos",
                        "--set-exif-datetime",
                        "--no-progress-bar",
                        "--threads-num",
                        "1",
                        "-d",
                        data_dir,
                        "--watch-with-interval",
                        str(target_duration),
                        "--cookie-directory",
                        cookie_dir,
                    ],
                )
                print_result_exception(result)

                assert result.exit_code == 0

        files_in_result = glob.glob(os.path.join(
            data_dir, "**/*.*"), recursive=True)

        assert sum(1 for _ in files_in_result) == len(
            files_to_create) + len(files_to_download)

        for file_name in files_to_download + ([file_name for (file_name, _) in files_to_create]):
            assert os.path.exists(os.path.join(data_dir, os.path.normpath(
                file_name))), f"File {file_name} expected, but does not exist"

    def test_handle_internal_error_during_download(self) -> None:
        base_dir = os.path.join(self.fixtures_path, inspect.stack()[0][3])
        cookie_dir = os.path.join(base_dir, "cookie")
        data_dir = os.path.join(base_dir, "data")

        for dir in [base_dir, cookie_dir, data_dir]:
            recreate_path(dir)

        with vcr.use_cassette(os.path.join(self.vcr_path, "listing_photos.yml")):

            def mock_raise_response_error(_arg: Any) -> NoReturn:
                raise PyiCloudAPIResponseException(
                    "INTERNAL_ERROR", "INTERNAL_ERROR")

            with mock.patch("time.sleep") as sleep_mock:
                with mock.patch.object(PhotoAsset, "download") as pa_download:
                    pa_download.side_effect = mock_raise_response_error

                    # Pass fixed client ID via environment variable
                    runner = CliRunner(env={
                        "CLIENT_ID": "DE309E26-942E-11E8-92F5-14109FE0B321"
                    })
                    result = runner.invoke(
                        main,
                        [
                            "--username",
                            "jdoe@gmail.com",
                            "--password",
                            "password1",
                            "--recent",
                            "1",
                            "--skip-videos",
                            "--skip-live-photos",
                            "--no-progress-bar",
                            "--threads-num",
                            "1",
                            "-d",
                            data_dir,
                            "--cookie-directory",
                            cookie_dir,
                        ],
                    )
                    print_result_exception(result)

                    # Error msg should be repeated 5 times
                    # self.assertEqual(
                    #     self._caplog.text.count(
                    #         "Error downloading"
                    #     ), constants.MAX_RETRIES, "Retry count"
                    # )

                    self.assertIn(
                        "ERROR    Could not download IMG_7409.JPG. Please try again later.",
                        self._caplog.text,
                    )

                    # Make sure we only call sleep 4 times (skip the first retry)
                    self.assertEqual(sleep_mock.call_count, 5)
                    self.assertEqual(result.exit_code, 0, "Exit Code")

        files_in_result = glob.glob(os.path.join(
            data_dir, "**/*.*"), recursive=True)

        assert sum(1 for _ in files_in_result) == 0

    def test_handle_internal_error_during_photo_iteration(self) -> None:
        base_dir = os.path.join(self.fixtures_path, inspect.stack()[0][3])
        cookie_dir = os.path.join(base_dir, "cookie")
        data_dir = os.path.join(base_dir, "data")

        for dir in [base_dir, cookie_dir, data_dir]:
            recreate_path(dir)

        with vcr.use_cassette(os.path.join(self.vcr_path, "listing_photos.yml")):

            def mock_raise_response_error(_offset: int) -> NoReturn:
                raise PyiCloudAPIResponseException(
                    "INTERNAL_ERROR", "INTERNAL_ERROR")

            with mock.patch("time.sleep") as sleep_mock:
                with mock.patch.object(PhotoAlbum, "photos_request") as pa_photos_request:
                    pa_photos_request.side_effect = mock_raise_response_error

                    # Pass fixed client ID via environment variable
                    runner = CliRunner(env={
                        "CLIENT_ID": "DE309E26-942E-11E8-92F5-14109FE0B321"
                    })
                    result = runner.invoke(
                        main,
                        [
                            "--username",
                            "jdoe@gmail.com",
                            "--password",
                            "password1",
                            "--recent",
                            "1",
                            "--skip-videos",
                            "--skip-live-photos",
                            "--no-progress-bar",
                            "--threads-num",
                            "1",
                            "-d",
                            data_dir,
                            "--cookie-directory",
                            cookie_dir,
                        ],
                    )
                    print_result_exception(result)

                    # Error msg should be repeated 5 times
                    self.assertEqual(
                        self._caplog.text.count(
                            "Internal Error at Apple, retrying..."
                        ), constants.MAX_RETRIES, "Retry count"
                    )

                    self.assertIn(
                        "ERROR    Internal Error at Apple.",
                        self._caplog.text,
                    )

                    # Make sure we only call sleep 4 times (skip the first retry)
                    self.assertEqual(sleep_mock.call_count, 5)

                    self.assertEqual(result.exit_code, 1, "Exit Code")

        files_in_result = glob.glob(os.path.join(
            data_dir, "**/*.*"), recursive=True)

        assert sum(1 for _ in files_in_result) == 0

    def test_handle_io_error_mkdir(self) -> None:
        base_dir = os.path.join(self.fixtures_path, inspect.stack()[0][3])
        cookie_dir = os.path.join(base_dir, "cookie")
        data_dir = os.path.join(base_dir, "data")

        for dir in [base_dir, cookie_dir, data_dir]:
            recreate_path(dir)

        with vcr.use_cassette(os.path.join(self.vcr_path, "listing_photos.yml")):
            with mock.patch("os.makedirs", create=True) as m:
                # Raise IOError when we try to write to the destination file
                m.side_effect = IOError

                runner = CliRunner(env={
                    "CLIENT_ID": "DE309E26-942E-11E8-92F5-14109FE0B321"
                })
                result = runner.invoke(
                    main,
                    [
                        "--username",
                        "jdoe@gmail.com",
                        "--password",
                        "password1",
                        "--recent",
                        "1",
                        "--skip-videos",
                        "--skip-live-photos",
                        "--no-progress-bar",
                        "--threads-num",
                        "1",
                        "-d",
                        data_dir,
                        "--cookie-directory",
                        cookie_dir,
                    ],
                )
                print_result_exception(result)

                self.assertIn(
                    "DEBUG    Looking up all photos from album All Photos...", self._caplog.text)
                self.assertIn(
                    f"INFO     Downloading the first original photo to {data_dir} ...",
                    self._caplog.text,
                )
                self.assertIn(
                    f"ERROR    Could not create folder {data_dir}",
                    self._caplog.text,
                )
                self.assertEqual(result.exit_code, 0, "Exit code")

        files_in_result = glob.glob(os.path.join(
            data_dir, "**/*.*"), recursive=True)

        self.assertEqual(sum(1 for _ in files_in_result),
                         0, "Files at the end")

    def test_dry_run(self) -> None:
        base_dir = os.path.join(self.fixtures_path, inspect.stack()[0][3])
        cookie_dir = os.path.join(base_dir, "cookie")
        data_dir = os.path.join(base_dir, "data")

        for dir in [base_dir, cookie_dir, data_dir]:
            recreate_path(dir)

        files_to_download = [
            '2018/07/31/IMG_7409.JPG',
            # "2018/07/30/IMG_7408.JPG",
            # "2018/07/30/IMG_7407.JPG",
        ]

        with vcr.use_cassette(os.path.join(self.vcr_path, "listing_photos.yml")):
            # Pass fixed client ID via environment variable
            runner = CliRunner(env={
                "CLIENT_ID": "DE309E26-942E-11E8-92F5-14109FE0B321"
            })
            result = runner.invoke(
                main,
                [
                    "--username",
                    "jdoe@gmail.com",
                    "--password",
                    "password1",
                    "--recent",
                    "1",
                    "--skip-videos",
                    "--skip-live-photos",
                    "--set-exif-datetime",
                    "--no-progress-bar",
                    "--dry-run",
                    "--threads-num",
                    "1",
                    "-d",
                    data_dir,
                    "--cookie-directory",
                    cookie_dir,
                ],
            )
            print_result_exception(result)

            self.assertIn(
                "DEBUG    Looking up all photos from album All Photos...", self._caplog.text)
            # self.assertIn(
            #     f"INFO     Downloading 2 original photos to {data_dir} ...",
            #     self._caplog.text,
            # )
            for f in files_to_download:
                self.assertIn(
                    f"DEBUG    Downloading {os.path.join(data_dir, os.path.normpath(f))}",
                    self._caplog.text,
                )
            self.assertNotIn(
                "IMG_7409.MOV",
                self._caplog.text,
            )
            self.assertNotIn(
                "ERROR",
                self._caplog.text,
            )
            # self.assertIn(
            #     "DEBUG    Skipping IMG_7405.MOV, only downloading photos.",
            #     self._caplog.text,
            # )
            # self.assertIn(
            #     "DEBUG    Skipping IMG_7404.MOV, only downloading photos.",
            #     self._caplog.text,
            # )
            self.assertIn(
                "INFO     All photos have been downloaded", self._caplog.text
            )

            assert result.exit_code == 0

        files_in_result = glob.glob(os.path.join(
            data_dir, "**/*.*"), recursive=True)

        self.assertEqual(sum(1 for _ in files_in_result),
                         0, "Files in the result")

    def test_download_after_delete_dry_run(self) -> None:
        base_dir = os.path.join(self.fixtures_path, inspect.stack()[0][3])
        cookie_dir = os.path.join(base_dir, "cookie")
        data_dir = os.path.join(base_dir, "data")

        for dir in [base_dir, cookie_dir, data_dir]:
            recreate_path(dir)

        def raise_response_error(a0_:logging.Logger, a1_:PyiCloudService, a2_: PhotoAsset) -> NoReturn:
            raise Exception("Unexpected call to delete_photo")

        with mock.patch.object(piexif, "insert") as piexif_patched:
            piexif_patched.side_effect = InvalidImageDataError
            with mock.patch(
                "icloudpd.exif_datetime.get_photo_exif"
            ) as get_exif_patched:
                get_exif_patched.return_value = False
                with mock.patch(
                    "icloudpd.base.delete_photo"
                ) as df_patched:
                    df_patched.side_effect = raise_response_error

                    with vcr.use_cassette(os.path.join(self.vcr_path, "listing_photos.yml")) as cass:
                        # Pass fixed client ID via environment variable
                        runner = CliRunner(env={
                            "CLIENT_ID": "DE309E26-942E-11E8-92F5-14109FE0B321"
                        })
                        result = runner.invoke(
                            main,
                            [
                                "--username",
                                "jdoe@gmail.com",
                                "--password",
                                "password1",
                                "--recent",
                                "1",
                                "--skip-videos",
                                "--skip-live-photos",
                                "--no-progress-bar",
                                "--dry-run",
                                "--threads-num",
                                "1",
                                "--delete-after-download",
                                "-d",
                                data_dir,
                                "--cookie-directory",
                                cookie_dir,
                            ],
                        )
                        print_result_exception(result)

                        self.assertIn(
                            "DEBUG    Looking up all photos from album All Photos...", self._caplog.text)
                        self.assertIn(
                            f"INFO     Downloading the first original photo to {data_dir} ...",
                            self._caplog.text,
                        )
                        self.assertIn(
                            f"DEBUG    Downloading {os.path.join(data_dir, os.path.normpath('2018/07/31/IMG_7409.JPG'))}",
                            self._caplog.text,
                        )
                        self.assertIn(
                            "INFO     [DRY RUN] Would delete IMG_7409.JPG in iCloud", self._caplog.text
                        )
                        self.assertIn(
                            "INFO     All photos have been downloaded", self._caplog.text
                        )
                        self.assertEqual(
                            cass.all_played, False, "All mocks played")
                        self.assertEqual(result.exit_code, 0, "Exit code")

        files_in_result = glob.glob(os.path.join(
            data_dir, "**/*.*"), recursive=True)

        self.assertEqual(sum(1 for _ in files_in_result),
                         0, "Files in the result")

    def test_download_raw_photos(self) -> None:
        base_dir = os.path.join(self.fixtures_path, inspect.stack()[0][3])
        cookie_dir = os.path.join(base_dir, "cookie")
        data_dir = os.path.join(base_dir, "data")

        for dir in [base_dir, cookie_dir, data_dir]:
            recreate_path(dir)

        files_to_create: Sequence[Tuple[str, int]] = [
        ]

        files_to_download = [
            '2018/07/31/IMG_7409.DNG' # SU1HXzc0MDkuSlBH -> SU1HXzc0MDkuRE5H
        ]

        with vcr.use_cassette(os.path.join(self.vcr_path, "listing_photos_raw.yml")):
            # Pass fixed client ID via environment variable
            runner = CliRunner(env={
                "CLIENT_ID": "DE309E26-942E-11E8-92F5-14109FE0B321"
            })
            result = runner.invoke(
                main,
                [
                    "--username",
                    "jdoe@gmail.com",
                    "--password",
                    "password1",
                    "--recent",
                    "1",
                    "--skip-videos",
                    "--skip-live-photos",
                    "--no-progress-bar",
                    "--threads-num",
                    "1",
                    "-d",
                    data_dir,
                    "--cookie-directory",
                    cookie_dir,
                ],
            )
            print_result_exception(result)

            self.assertIn(
                "DEBUG    Looking up all photos from album All Photos...", self._caplog.text)
            self.assertIn(
                f"INFO     Downloading the first original photo to {data_dir} ...",
                self._caplog.text,
            )
            self.assertIn(
                f"DEBUG    Downloading {os.path.join(data_dir, os.path.normpath('2018/07/31/IMG_7409.DNG'))}",
                self._caplog.text,
            )
            self.assertNotIn(
                "IMG_7409.MOV",
                self._caplog.text,
            )
            self.assertIn(
                "INFO     All photos have been downloaded", self._caplog.text
            )

            assert result.exit_code == 0

        files_in_result = glob.glob(os.path.join(
            data_dir, "**/*.*"), recursive=True)

        assert sum(1 for _ in files_in_result) == len(
            files_to_create) + len(files_to_download)

        for file_name in files_to_download + ([file_name for (file_name, _) in files_to_create]):
            assert os.path.exists(os.path.join(data_dir, os.path.normpath(
                file_name))), f"File {file_name} expected, but does not exist"

