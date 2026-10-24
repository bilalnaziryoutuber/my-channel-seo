import json
import re
import shutil
from types import SimpleNamespace
from datetime import datetime, timezone

import pytest

import engine


def test_database_load_save_and_corrupt_json_safety(tmp_path, monkeypatch):
    database_file = tmp_path / "video_db.json"
    monkeypatch.setattr(engine, "DB_FILE", str(database_file))
    records = {"video": {"title": "Original", "polished": False}}

    engine.save_db(records)
    assert engine.load_db() == records

    corrupt_contents = '{"video":'
    database_file.write_text(corrupt_contents, encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        engine.load_db()
    assert database_file.read_text(encoding="utf-8") == corrupt_contents
    assert list(tmp_path.iterdir()) == [database_file]


def test_polished_batch_requires_matching_ids_and_valid_titles():
    result = engine.parse_polished_response(
        '[{"id":"a","title":"Bike start problem fix"},{"id":"b","title":"Family funny moment"}]',
        ["a", "b"],
    )
    assert [item["id"] for item in result] == ["a", "b"]
    assert result[0]["title"] == "Bike start problem fix"
    assert engine.parse_polished_response(
        '[{"id":"a","title":"Don\'t start bike"}]', ["a"]
    )[0]["title"] == "Don't start bike"

    with pytest.raises(ValueError, match="IDs"):
        engine.parse_polished_response('[{"id":"a","title":"Safe title"}]', ["b"])
    with pytest.raises(ValueError, match="count"):
        engine.parse_polished_response("[]", ["a"])


def test_title_batching_uses_one_structured_request_per_forty(monkeypatch):
    requests = []

    class FakeModels:
        def generate_content(self, **kwargs):
            requests.append(kwargs)
            input_json = kwargs["contents"].split("Input JSON:\n", 1)[1]
            items = json.loads(input_json)
            return SimpleNamespace(text=json.dumps([
                {"id": item["id"], "title": f"SEO {item['title']}"}
                for item in items
            ]))

    class FakeLimiter:
        def wait(self):
            pass

    class FakeClient:
        def __init__(self, api_key):
            assert api_key == "test-key"
            self.models = FakeModels()

    monkeypatch.setattr(engine, "BATCH_SIZE", 40)
    monkeypatch.setattr(engine.genai, "Client", FakeClient)
    monkeypatch.setattr(engine, "GeminiRateLimiter", FakeLimiter)
    monkeypatch.setattr(engine, "save_db", lambda _db: None)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    database = {
        f"video-{index}": {
            "original_title": f"Original {index}",
            "title": f"Original {index}",
            "polished": False,
        }
        for index in range(41)
    }

    assert engine._polish_unpolished_videos(database) == 41
    assert [len(json.loads(request["contents"].split("Input JSON:\n", 1)[1])) for request in requests] == [40, 1]
    assert all(request["config"]["response_mime_type"] == "application/json" for request in requests)
    assert all(video["polished"] for video in database.values())


def test_html_escapes_untrusted_content_and_keeps_valid_json_ld():
    videos = [(
        "video-id",
        {
            "title": '<img src=x onerror="alert(1)">',
            "description": "Description </script><script>alert(1)</script>",
            "published_at": "2025-03-04T12:30:00+05:00",
            "duration": "PT1M2S",
        },
    )]
    page = engine.build_html(videos)

    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in page
    assert "<script>alert(1)</script>" not in page
    assert "<h1>Bilal Nazir Videos</h1>" in page
    json_ld = re.findall(
        r'<script type="application/ld\+json">(.*?)</script>',
        page,
        flags=re.DOTALL,
    )
    assert len(json_ld) == 1
    video_object = json.loads(json_ld[0])
    assert video_object["name"] == '<img src=x onerror="alert(1)">'
    assert video_object["uploadDate"] == "2025-03-04T07:30:00Z"
    assert video_object["description"] == "Description </script><script>alert(1)</script>"
    assert video_object["embedUrl"] == "https://www.youtube.com/embed/video-id"
    assert video_object["thumbnailUrl"].endswith("/video-id/hqdefault.jpg")
    assert video_object["duration"] == "PT1M2S"


def test_paginated_navigation_uses_valid_relative_paths():
    second_page = engine.build_html(
        [], page_number=2, total_pages=3, page_path="page/2/"
    )
    third_page = engine.build_html(
        [], page_number=3, total_pages=3, page_path="page/3/"
    )

    assert 'href="../../" rel="prev"' in second_page
    assert 'href="../3/" rel="next"' in second_page
    assert 'href="../2/" rel="prev"' in third_page
    assert 'rel="next"' not in third_page


def test_database_sync_resets_polish_only_when_original_changes():
    db = {
        "a": {
            "original_title": "Old original",
            "title": "Polished version",
            "polished": True,
            "polished_at": "2025-01-01T00:00:00Z",
        },
    }

    engine._sync_database(db, [{
        "id": "a",
        "title": "Updated YouTube title",
        "published_at": "2025-03-04T12:30:00+05:00",
        "description": "Updated description",
        "duration": "PT4S",
    }])

    assert db["a"]["original_title"] == "Updated YouTube title"
    assert db["a"]["title"] == "Updated YouTube title"
    assert db["a"]["polished"] is False
    assert db["a"]["polished_at"] is None
    assert db["a"]["published_at"] == "2025-03-04T07:30:00Z"
    assert db["a"]["description"] == "Updated description"


@__import__("pytest").mark.skip(reason="needs a migrated video_db.json")
def test_migration_on_copy_of_video_db_preserves_original_title(tmp_path):
    source = engine.Path(__file__).resolve().parents[1] / "video_db.json"
    copied = tmp_path / "video_db.json"
    shutil.copyfile(source, copied)
    database = json.loads(copied.read_text(encoding="utf-8"))
    old_record = next(
        record for record in database.values()
        if record.get("polished") and record.get("polished_at")
    )
    original = old_record["original_title"]
    polished_title = old_record["title"]
    polished_at = old_record["polished_at"]

    migrated = engine.migrate_database(database)

    assert old_record["original_title"] == original
    assert old_record["seo"]["title"] == polished_title
    assert old_record["seo"]["prompt_version"] == 0
    assert old_record["seo"]["updated_at"] == polished_at
    assert old_record["status"] in {"active", "removed"}
    assert "seo" not in next(
        record for record in migrated.values()
        if not record.get("polished") and not record.get("seo")
    )


def test_seo_queue_prioritizes_missing_and_stale_versions_then_oldest():
    now = datetime(2026, 10, 24, tzinfo=timezone.utc)
    database = {
        "fresh": {"status": "active", "seo": {
            "prompt_version": engine.PROMPT_VERSION,
            "updated_at": "2026-10-20T00:00:00Z",
        }},
        "oldest": {"status": "active", "seo": {
            "prompt_version": engine.PROMPT_VERSION,
            "updated_at": "2026-09-01T00:00:00Z",
        }},
        "stale-prompt": {"status": "active", "seo": {
            "prompt_version": 0, "updated_at": "2026-10-23T00:00:00Z",
        }},
        "no-seo": {"status": "active"},
        "removed": {"status": "removed"},
    }

    queue = engine._seo_queue(database, 4, now=now)

    assert [video_id for video_id, _ in queue] == [
        "no-seo", "stale-prompt", "oldest",
    ]


def test_removal_counter_requires_three_misses_and_ninety_percent_listing():
    database = {
        f"v{index}": {
            "status": "active",
            "missing_count": 0,
            "original_title": f"Video {index}",
        }
        for index in range(10)
    }
    database["v0"]["missing_count"] = 2
    nine_present = [{"id": f"v{index}", "title": f"Video {index}"} for index in range(9)]

    for expected_count in (1, 2, 3):
        assert engine._apply_listing_sync(database, nine_present)
        assert database["v0"]["missing_count"] == 0
        assert database["v9"]["missing_count"] == expected_count
        assert database["v9"]["status"] == ("removed" if expected_count == 3 else "active")
    database["v9"]["status"] = "active"
    database["v9"]["missing_count"] = 1
    eight_present = [{"id": f"v{index}", "title": f"Video {index}"} for index in range(8)]

    assert not engine._apply_listing_sync(database, eight_present)
    assert database["v8"]["missing_count"] == 0
    assert database["v9"]["missing_count"] == 1


def test_channel_listing_uses_complete_flat_ytdlp_uploads(monkeypatch):
    entries = [
        {"id": f"video-{index}", "title": f"Title {index}", "upload_date": "20250304"}
        for index in range(105)
    ]
    calls = []

    class FakeYoutubeDL:
        def __init__(self, options):
            assert options.get("extract_flat") is True
            assert "playlistend" not in options

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, url, download=False):
            calls.append(url)
            assert download is False
            if "playlist?list=" in url:
                return {"entries": entries}
            return {"id": "UC0123456789", "entries": []}

    monkeypatch.setattr(engine.yt_dlp, "YoutubeDL", FakeYoutubeDL)
    monkeypatch.setenv("YOUTUBE_API_KEY", "deliberately-not-used")

    listed = engine.get_channel_videos()

    assert len(listed) == 105
    assert listed[0] == {
        "id": "video-0", "title": "Title 0", "upload_date": "20250304",
    }
    assert len(calls) == 2
    assert "playlist?list=UU0123456789" in calls[1]


def test_history_rotation_keeps_only_three_previous_seo_objects():
    record = {
        "tags": ["bike"],
        "seo": {"title": "current"},
        "seo_history": [{"title": "one"}, {"title": "two"}, {"title": "three"}],
    }

    engine._record_seo(record, {
        "title": "new", "description": "Sentence one. Sentence two.",
        "keywords": ["a", "b", "c", "d", "e"],
    }, "model-a", "2026-10-24T00:00:00Z")

    assert [item["title"] for item in record["seo_history"]] == [
        "current", "one", "two",
    ]
    assert record["seo"]["source_tags"] == ["bike"]


def test_refresh_validation_failure_keeps_existing_seo(monkeypatch):
    original = {
        "title": "Existing", "description": "Keep this.",
        "keywords": ["a"], "prompt_version": 0, "updated_at": "2025-01-01T00:00:00Z",
    }
    database = {"a": {"status": "active", "seo": original}}

    class FakeModels:
        def list(self):
            return [SimpleNamespace(name="models/gemini-3.5-flash-lite")]

    class FakeClient:
        models = FakeModels()

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("REFRESH_PER_DAY", "1")
    monkeypatch.setattr(engine.genai, "Client", lambda api_key: FakeClient())
    monkeypatch.setattr(
        engine, "_generate_seo_batch",
        lambda *_args: (None, None, False),
    )
    monkeypatch.setattr(engine, "save_db", lambda _db: None)

    with pytest.raises(RuntimeError, match="more than half failed"):
        engine._refresh_seo(database, force_ids={"a"})
    assert database["a"]["seo"] == original


def test_seo_response_validation_and_library_duplicate_detection():
    valid = [{
        "id": "a",
        "title": "Bike repair tips",
        "description": "Learn a simple bike repair tip. See the fix in this video.",
        "keywords": ["bike repair", "motorcycle", "start", "fix", "Hinglish"],
    }]
    parsed = engine.parse_seo_response(json.dumps(valid), ["a"])
    assert parsed[0]["id"] == "a"
    with pytest.raises(ValueError, match="Duplicate SEO title"):
        engine._validate_library_uniqueness(
            parsed,
            {"b": {"seo": {"title": "BIKE REPAIR TIPS"}}},
            {"a"},
        )
    with pytest.raises(ValueError, match="keyword count"):
        engine.parse_seo_response(
            json.dumps([{**valid[0], "keywords": ["only-one"]}]), ["a"]
        )


def test_video_json_ld_omits_unknown_published_date():
    video = {
        "seo": {
            "title": "A truthful video title",
            "description": "First useful sentence. Second useful sentence.",
        },
        "published_at": None,
        "duration_seconds": 62,
    }

    page = engine._build_video_page("abc123", video, [], "https://example.test/", None)
    json_ld = re.search(
        r'<script type="application/ld\+json">(.*?)</script>', page, re.DOTALL
    )

    assert json_ld
    payload = json.loads(json_ld.group(1))
    assert "uploadDate" not in payload
    assert payload["duration"] == "PT1M2S"
    assert "<iframe" not in page
    assert "Watch on YouTube" in page


def test_generated_site_links_video_pages_and_escapes_seo_content(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "SITE_DIR", tmp_path / "site")
    database = {
        "abc123": {
            "original_title": "Bike repair moment",
            "status": "active",
            "published_at": None,
            "duration_seconds": 8,
            "tags": [],
            "description": "",
            "seo": {
                "title": '<img src=x onerror="bad()">',
                "description": "</script><script>alert(1)</script>",
                "updated_at": "2026-10-24T00:00:00Z",
            },
        },
        "def456": {
            "original_title": "Bike repair continues",
            "status": "active",
            "published_at": "2025-01-02",
            "duration_seconds": 9,
            "tags": [],
            "description": "",
            "seo": {
                "title": "Another repair",
                "description": "Useful information. More detail.",
                "updated_at": "2026-10-23T00:00:00Z",
            },
        },
        "no-seo": {"original_title": "No page", "status": "active"},
    }

    video_count, _page_count = engine.generate_site(database)
    listing = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    video_page = (
        tmp_path / "site" / "v" / "abc123" / "index.html"
    ).read_text(encoding="utf-8")
    sitemap = (tmp_path / "site" / "sitemap.xml").read_text(encoding="utf-8")

    assert video_count == 2
    assert 'href="v/abc123/"' in listing
    assert "no-seo" not in listing
    assert "<h1>&lt;img src=x onerror=&quot;bad()&quot;&gt;</h1>" in video_page
    assert "<script>alert(1)</script>" not in video_page
    assert video_page.count('class="card"') == 1
    assert "<iframe" not in video_page
    assert "<lastmod>2026-10-24T00:00:00Z</lastmod>" in sitemap
    assert "v/abc123/" in sitemap


def test_bot_check_soft_stops_detail_sync(monkeypatch):
    class FakeYoutubeDL:
        def __init__(self, options):
            assert options["noplaylist"] is True
            assert options["socket_timeout"] == 20
            assert options["retries"] == 3

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, *_args, **_kwargs):
            raise RuntimeError("Sign in to confirm you’re not a bot")

    database = {"a": {
        "original_title": "Original",
        "status": "active",
        "last_synced": None,
    }}
    monkeypatch.setattr(engine.yt_dlp, "YoutubeDL", FakeYoutubeDL)
    monkeypatch.setattr(engine.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(engine.random, "uniform", lambda *_args: 1.5)
    monkeypatch.setenv("SYNC_TIME_BUDGET_MIN", "1")
    monkeypatch.setenv("SYNC_SLICE_SIZE", "50")

    assert engine._detail_sync(database) == set()
    assert database["a"]["last_synced"] is None


def test_detail_sync_maps_yt_dlp_metadata_fields(monkeypatch):
    class FakeYoutubeDL:
        def __init__(self, options):
            assert options["noplaylist"] is True
            assert options["socket_timeout"] == 20
            assert options["retries"] == 3

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, *_args, **_kwargs):
            return {
                "title": "Changed source title",
                "upload_date": "20250304",
                "description": "YouTube description",
                "duration": 125,
                "view_count": 27,
                "tags": ["bike", "repair"],
            }

    database = {"a": {
        "original_title": "Old source title",
        "status": "active",
        "last_synced": None,
    }}
    monkeypatch.setattr(engine.yt_dlp, "YoutubeDL", FakeYoutubeDL)
    monkeypatch.setattr(engine.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(engine.random, "uniform", lambda *_args: 1.5)
    monkeypatch.setenv("SYNC_TIME_BUDGET_MIN", "1")

    assert engine._detail_sync(database) == {"a"}
    assert database["a"]["original_title"] == "Changed source title"
    assert database["a"]["seo_refresh_pending"] is True
    assert database["a"]["published_at"] == "2025-03-04"
    assert database["a"]["description"] == "YouTube description"
    assert database["a"]["duration_seconds"] == 125
    assert database["a"]["view_count"] == 27
    assert database["a"]["tags"] == ["bike", "repair"]
    assert database["a"]["last_synced"]
