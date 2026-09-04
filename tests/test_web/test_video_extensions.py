"""The control page and the watch folder accept the same video types: a
file that works dropped into in\ must not be refused by Browse."""
from ash_captions.pipeline.watcher import VIDEO_EXTENSIONS
from ash_captions.web.models import ALLOWED_VIDEO_EXTENSIONS


def test_web_and_watcher_extension_lists_match():
    assert set(ALLOWED_VIDEO_EXTENSIONS) == VIDEO_EXTENSIONS
    assert ".wmv" in ALLOWED_VIDEO_EXTENSIONS
