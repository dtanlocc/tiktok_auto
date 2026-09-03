from app.use_cases.analytics.tiktok_analytics_sync import extract_studio_video_metrics


def test_extracts_nested_video_metrics_and_terminal_page():
    videos, complete = extract_studio_video_metrics([{
        "data": {
            "items": [{
                "id": "7481234567890123456",
                "desc": "A test video",
                "createTime": 1700000000,
                "stats": {
                    "playCount": 1200,
                    "diggCount": 80,
                    "commentCount": 4,
                    "shareCount": 3,
                },
            }],
            "has_more": False,
        }
    }])

    assert complete is True
    assert videos == [{
        "video_id": "7481234567890123456",
        "title": "A test video",
        "create_time": 1700000000,
        "view_count": 1200,
        "like_count": 80,
        "comment_count": 4,
        "share_count": 3,
        "cover_url": "",
        "share_url": "",
    }]


def test_rejects_unrelated_ids_and_keeps_highest_duplicate_counters():
    payloads = [
        {"user": {"id": "123456789", "followerCount": 99}},
        {"userInfo": {"id": "987654321", "likeCount": 4500, "videoCount": 12}},
        {"item": {"video_id": "7481234567890123456", "view_count": 10}},
        {"item": {"video_id": "7481234567890123456", "view_count": 15, "like_count": 2}},
    ]

    videos, complete = extract_studio_video_metrics(payloads)

    assert complete is False
    assert len(videos) == 1
    assert videos[0]["view_count"] == 15
    assert videos[0]["like_count"] == 2
