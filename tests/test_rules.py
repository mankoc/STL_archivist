"""
Unit tests for presupport / warning / scale detection rules.
"""
from app.crawler.rules import (
    DEFAULT_SUPPORTS_PATTERNS,
    detect_presupport,
    detect_scale,
    detect_warnings,
)


# ---------------------------------------------------------------------------
# detect_presupport
# ---------------------------------------------------------------------------

def test_presupport_lychee_highest_priority():
    names = ["body.stl", "body_supported.stl", "body.lys", "slice.chitu"]
    assert detect_presupport(names) == "Lychee"


def test_presupport_chitubox_when_no_lychee():
    names = ["body.stl", "body_supported.stl", "slice.chitu"]
    assert detect_presupport(names) == "Chitubox"


def test_presupport_presupported_stl():
    names = ["body.stl", "arm_supported.stl"]
    assert detect_presupport(names) == "Presupported"


def test_presupport_no_match():
    names = ["body.stl", "arm.stl", "readme.txt"]
    assert detect_presupport(names) == "No"


def test_presupport_empty():
    assert detect_presupport([]) == "No"


# ---------------------------------------------------------------------------
# detect_warnings
# ---------------------------------------------------------------------------

def test_warn_presupported_filename_underscore():
    names = ["body_presupported.stl"]
    out = detect_warnings(names, check_no_images=False, check_nested_archive=False)
    assert "presupported stl" in out


def test_warn_presupported_filename_space():
    names = ["torso presupported.stl"]
    out = detect_warnings(names, check_no_images=False, check_nested_archive=False)
    assert "presupported stl" in out


def test_warn_presupported_sup_prefix():
    names = ["sup_dragon.stl"]
    out = detect_warnings(names, check_no_images=False, check_nested_archive=False)
    assert "presupported stl" in out


def test_warn_pipe_does_not_match_presupported():
    """Regression: regex must not treat `|` as a valid separator."""
    names = ["body|presupported.stl"]
    out = detect_warnings(names, check_no_images=False, check_nested_archive=False)
    assert "presupported stl" not in out


def test_warn_no_images():
    names = ["body.stl", "arm.stl"]
    out = detect_warnings(names, check_presupported=False, check_nested_archive=False)
    assert "Archive without images" in out


def test_warn_images_present_suppresses_warning():
    names = ["body.stl", "preview.jpg"]
    out = detect_warnings(names, check_presupported=False, check_nested_archive=False)
    assert "Archive without images" not in out


def test_warn_nested_archive():
    names = ["body.stl", "extras.zip"]
    out = detect_warnings(names, check_presupported=False, check_no_images=False)
    assert "Archive inside archive" in out


def test_warn_all_disabled_returns_empty():
    names = ["body_presupported.stl", "extras.zip"]
    out = detect_warnings(
        names,
        check_presupported=False,
        check_no_images=False,
        check_nested_archive=False,
    )
    assert out == ""


# ---------------------------------------------------------------------------
# detect_scale
# ---------------------------------------------------------------------------

def test_scale_mm_various_separators():
    # The regex uses \b, which does not fire between an underscore and a digit
    # (underscore is a word character); space and dash do produce boundaries.
    names = ["28mm.stl", "hero 32mm.stl", "75 mm.stl"]
    assert detect_scale(names) == ["28 mm", "32 mm", "75 mm"]


def test_scale_ratio_slash_and_dash():
    names = ["1-35.stl", "scale 1-72.stl"]
    assert detect_scale(names) == ["1/35", "1/72"]


def test_scale_deduplicates():
    names = ["28mm.stl", "hero 28mm.stl"]
    assert detect_scale(names) == ["28 mm"]


def test_scale_none_found():
    names = ["body.stl", "arm.stl"]
    assert detect_scale(names) == []


def test_scale_dash_prefix_mm():
    # Dash creates a word boundary before the digit; underscore does NOT.
    names = ["body-75mm.stl"]
    assert detect_scale(names) == ["75 mm"]


def test_scale_ratio_dash_in_stem():
    # 1-35 in the stem (after a dash that creates a word boundary)
    names = ["figure-1-35.stl"]
    assert detect_scale(names) == ["1/35"]


def test_scale_multiple_different_scales():
    names = ["28mm.stl", "figure-1-35.stl"]
    scales = detect_scale(names)
    assert "28 mm" in scales
    assert "1/35" in scales


# ---------------------------------------------------------------------------
# detect_presupport — .lyt extension (alternative Lychee format)
# ---------------------------------------------------------------------------

def test_presupport_lyt_is_lychee():
    names = ["body.stl", "scene.lyt"]
    assert detect_presupport(names) == "Lychee"


def test_presupport_lyt_takes_priority_over_chitu():
    names = ["body.stl", "slice.chitu", "scene.lyt"]
    assert detect_presupport(names) == "Lychee"


# ---------------------------------------------------------------------------
# detect_warnings — image format edge cases
# ---------------------------------------------------------------------------

def test_warn_bmp_image_suppresses_no_images_warning():
    names = ["body.stl", "render.bmp"]
    out = detect_warnings(names, check_presupported=False, check_nested_archive=False)
    assert "Archive without images" not in out


def test_warn_webp_image_suppresses_no_images_warning():
    names = ["body.stl", "render.webp"]
    out = detect_warnings(names, check_presupported=False, check_nested_archive=False)
    assert "Archive without images" not in out


def test_warn_rar_nested_archive():
    names = ["model.stl", "extras.rar"]
    out = detect_warnings(names, check_presupported=False, check_no_images=False)
    assert "Archive inside archive" in out


def test_warn_multiple_warnings_joined_by_newline():
    names = ["body_presupported.stl", "extra.zip"]
    out = detect_warnings(names, check_no_images=False)
    lines = out.splitlines()
    assert len(lines) >= 2


# ---------------------------------------------------------------------------
# detect_warnings — archive with supports
# ---------------------------------------------------------------------------

def test_warn_supports_default_patterns_supported_stl():
    names = ["body_supported.stl", "arm.stl"]
    out = detect_warnings(
        names, check_presupported=False, check_no_images=False,
        check_nested_archive=False, check_supports=True,
    )
    assert "Archive with supports" in out


def test_warn_supports_default_patterns_sup_prefix():
    names = ["sup_dragon.stl", "base.stl"]
    out = detect_warnings(
        names, check_presupported=False, check_no_images=False,
        check_nested_archive=False, check_supports=True,
    )
    assert "Archive with supports" in out


def test_warn_supports_unsupported_not_flagged():
    """Files with 'unsupported' in the name should NOT trigger the warning."""
    names = ["body_unsupported.stl", "arm.stl"]
    out = detect_warnings(
        names, check_presupported=False, check_no_images=False,
        check_nested_archive=False, check_supports=True,
    )
    assert "Archive with supports" not in out


def test_warn_supports_disabled_by_default():
    """check_supports defaults to False — no warning without opting in."""
    names = ["body_supported.stl"]
    out = detect_warnings(names, check_no_images=False, check_nested_archive=False)
    assert "Archive with supports" not in out


def test_warn_supports_custom_pattern():
    names = ["presup_arm.stl", "body.stl"]
    out = detect_warnings(
        names, check_presupported=False, check_no_images=False,
        check_nested_archive=False, check_supports=True,
        supports_patterns=[r"^presup.*\.stl$"],
    )
    assert "Archive with supports" in out


def test_warn_supports_invalid_regex_skipped():
    """An invalid regex should not crash; it is silently skipped."""
    names = ["body_supported.stl"]
    out = detect_warnings(
        names, check_presupported=False, check_no_images=False,
        check_nested_archive=False, check_supports=True,
        supports_patterns=["[invalid(regex", r"(?<!un)supported\.stl$"],
    )
    assert "Archive with supports" in out


def test_warn_supports_empty_pattern_list_no_warning():
    names = ["body_supported.stl"]
    out = detect_warnings(
        names, check_presupported=False, check_no_images=False,
        check_nested_archive=False, check_supports=True,
        supports_patterns=[],
    )
    assert "Archive with supports" not in out


def test_warn_supports_no_match_no_warning():
    names = ["body.stl", "arm.stl"]
    out = detect_warnings(
        names, check_presupported=False, check_no_images=False,
        check_nested_archive=False, check_supports=True,
    )
    assert "Archive with supports" not in out


# ---------------------------------------------------------------------------
# DEFAULT_SUPPORTS_PATTERNS
# ---------------------------------------------------------------------------

def test_default_supports_patterns_is_nonempty():
    assert len(DEFAULT_SUPPORTS_PATTERNS) > 0


def test_default_supports_patterns_match_supported_stl():
    import re
    for p in DEFAULT_SUPPORTS_PATTERNS:
        rx = re.compile(p, re.IGNORECASE)
        # At least one default pattern must match a "supported.stl" filename
        if rx.search("body_supported.stl"):
            return
    raise AssertionError("No default pattern matched 'body_supported.stl'")
