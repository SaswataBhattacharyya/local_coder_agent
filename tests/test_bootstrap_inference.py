from bootstrap import roles_to_download


def test_roles_to_download_remote():
    cfg = {
        "inference": {
            "mode": "remote",
            "roles": {
                "reasoner": {"backend": "remote"},
                "coder": {"backend": "remote"},
                "vlm": {"backend": "remote"},
            },
        }
    }
    assert roles_to_download("remote", cfg) == set()


def test_roles_to_download_mixed():
    cfg = {
        "inference": {
            "mode": "mixed",
            "roles": {
                "reasoner": {"backend": "remote"},
                "coder": {"backend": "local"},
                "vlm": {"backend": "local"},
            },
        }
    }
    assert roles_to_download("mixed", cfg) == {"coder", "vlm"}
