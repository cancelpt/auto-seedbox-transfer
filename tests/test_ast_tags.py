from utils.ast_tags import build_bt_ast_tags, build_origin_ast_tags, extract_ast_tags, merge_tags


def test_build_bt_ast_tags_omits_redundant_bt_hash():
    tags = build_bt_ast_tags("seedbox_a", "home_a", "origin-hash")

    assert tags == "ast,ast:origin:origin-hash,ast:route:seedbox_a->home_a"


def test_build_origin_ast_tags_omits_bt_hash_for_final_origin_tags():
    tags = build_origin_ast_tags("seedbox_a", "home_a", "origin-hash", "bt-hash")

    assert tags == "ast,ast:origin:origin-hash,ast:route:seedbox_a->home_a"


def test_merge_tags_preserves_user_tags_and_deduplicates_ast_tags():
    merged = merge_tags(
        "user, ast",
        build_origin_ast_tags("seedbox_a", "home_a", "origin-hash", "bt-hash"),
    )

    assert merged == "user,ast,ast:origin:origin-hash,ast:route:seedbox_a->home_a"


def test_extract_ast_tags_returns_all_ast_managed_tags():
    tags = "user,ast,ast:bt:bt-hash,ast:origin:origin-hash,ast:route:seedbox->home,zy"

    assert extract_ast_tags(tags) == [
        "ast",
        "ast:bt:bt-hash",
        "ast:origin:origin-hash",
        "ast:route:seedbox->home",
    ]
