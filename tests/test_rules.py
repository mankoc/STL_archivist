"""
Unit tests for presupport / warning / scale detection rules.
"""
import pytest

from app.crawler.rules import (
    DEFAULT_SUPPORTS_PATTERNS,
    detect_presupport,
    detect_scale,
    detect_warnings,
    explain_warnings,
    WARNING_NESTED,
    WARNING_NO_IMAGES,
    WARNING_NO_PRINTABLE,
    WARNING_PRESUPPORTED,
    WARNING_SUPPORTS,
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
    assert "Nested archives" in out


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
    assert "Nested archives" in out


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


# ---------------------------------------------------------------------------
# explain_warnings
# ---------------------------------------------------------------------------

_ALL_ON = dict(check_presupported=True, check_no_images=True, check_nested_archive=True,
               check_supports=True, check_no_printable=True)


@pytest.mark.parametrize("names", [
    [],
    ["body.stl", "render.png"],
    ["sup_torso.stl", "preview.jpg"],
    ["body_presupported.stl", "nested.7z"],
    ["Dragon - Presupported.jpg", "body.stl"],
    ["body_supported.stl", "shot.png", "extra.zip"],
    ["notes.txt"],
])
def test_explain_keys_match_detect_warnings(names):
    """The two must never disagree — same rules, one reports, one explains."""
    detected = [l for l in detect_warnings(names, **_ALL_ON).split("\n") if l]
    assert list(explain_warnings(names, **_ALL_ON)) == detected


def test_explain_reports_the_triggering_file():
    result = explain_warnings(["body_presupported.stl", "plain.stl", "p.png"], **_ALL_ON)
    assert result[WARNING_PRESUPPORTED] == ["body_presupported.stl"]


def test_presupported_warning_ignores_non_stl_members():
    """Only .stl members can raise the presupported warning.

    A preview image or readme whose name happens to contain "presupported" used
    to flag an archive holding no presupported mesh at all.
    """
    names = ["Dragon - Presupported.jpg", "readme_presupported.txt", "body.stl", "p.png"]
    assert WARNING_PRESUPPORTED not in explain_warnings(names, **_ALL_ON)
    assert WARNING_PRESUPPORTED not in detect_warnings(names, **_ALL_ON)


def test_presupported_warning_still_fires_for_stl_members():
    names = ["Dragon - Presupported.stl", "p.png"]
    assert explain_warnings(names, **_ALL_ON)[WARNING_PRESUPPORTED] ==         ["Dragon - Presupported.stl"]
    assert WARNING_PRESUPPORTED in detect_warnings(names, **_ALL_ON)


_PRESUPPORT_ONLY = dict(check_presupported=True, check_no_images=False,
                        check_nested_archive=False, check_supports=False,
                        check_no_printable=False)


def _flags_presupported(member: str) -> bool:
    """Whether *member* alone raises the presupported warning."""
    flagged = WARNING_PRESUPPORTED in explain_warnings([member], **_PRESUPPORT_ONLY)
    # detect_warnings must always agree with explain_warnings.
    assert flagged == (WARNING_PRESUPPORTED in detect_warnings([member], **_PRESUPPORT_ONLY))
    return flagged


@pytest.mark.parametrize("name, flagged", [
    ("body_presupported.stl", True),
    ("arm-Presupported.STL", True),
    ("sup_torso.stl", True),
    ("Dragon - Presupported.jpg", False),
    ("readme_presupported.txt", False),
    ("cover_presupported.png", False),
    ("notes_presupported.md", False),
    ("body_presupported.obj", False),
    ("body_presupported.lys", False),
])
def test_presupported_warning_suffix_gate(name, flagged):
    """The rule keys off the .stl suffix, not just the name."""
    assert _flags_presupported(name) is flagged


@pytest.mark.parametrize("member", [
    "Presupported/body.stl",
    "presupported/body.stl",
    "PS/arm.stl",
    "ps/arm.stl",
    "PS Files/wing.stl",
    "Pre-Supported/leg.stl",
    "Presupported STL/wing.stl",
    "Supported STL/leg.stl",
    "Model/Supports/wing.stl",
    "Model/Presupports/deep/wing.stl",
])
def test_stl_in_a_presupport_folder_is_flagged(member):
    """An .stl qualifies by the folder it sits in, not only by its own name."""
    assert _flags_presupported(member) is True


@pytest.mark.parametrize("member", [
    # "un" family
    "Unsupported/body.stl",
    "Un-Supported/body.stl",
    "un supported/body.stl",
    "UNSUPPORTED STL/body.stl",
    # "no" family — the convention for raw meshes in many releases
    "No_Supported/body.stl",
    "No Supports/body.stl",
    "no-supported/body.stl",
    "NoSupports/body.stl",
    "Nosupported/body.stl",
    "No_PS/body.stl",
    # other negations
    "Non-Supported/body.stl",
    "Not Supported/body.stl",
    "Without Supports/body.stl",
])
def test_negated_folder_is_not_presupported(member):
    """A marker preceded by a negation means the opposite — raw meshes.

    Tokenising "No_Supported" yields {no, supported}; taking the tokens as a
    set would match the marker table and flag an archive of unsupported meshes.
    """
    assert _flags_presupported(member) is False


def test_negation_in_a_deep_path_still_applies():
    """Regression: 2026-07 - Guts/No_Supported/… was flagged as presupported."""
    members = [
        "2026-07 - Guts/No_Supported/Guts/Arm_L_v1.stl",
        "2026-07 - Guts/No_Supported/Merged/Arm_L_v1.stl",
        "2026-07 - Guts/No_Supported/Base/Base_1.stl",
    ]
    assert WARNING_PRESUPPORTED not in explain_warnings(members, **_PRESUPPORT_ONLY)


@pytest.mark.parametrize("member", [
    "Dino Supported/body.stl",
    "Casino PS/body.stl",
    "Rhino Supports/body.stl",
])
def test_word_merely_ending_in_a_negation_does_not_negate(member):
    """Only a whole preceding token negates — "Dino" must not read as "no"."""
    assert _flags_presupported(member) is True


@pytest.mark.parametrize("member", [
    "supports/Unsupported/Emerald.stl",
    "Supported/Raw/Unsupported/body.stl",
    "NSFW_supports/NSFW_Unsupported/body.stl",
    "PS/Unsupported/body.stl",
])
def test_unsupported_subfolder_vetoes_a_supported_parent(member):
    """Raw meshes nested inside the supported tree must not be flagged.

    Releases routinely ship "supports/Unsupported/…", so the outermost marker
    cannot be the one that decides — a negation anywhere above the file wins.
    """
    assert _flags_presupported(member) is False


@pytest.mark.parametrize("member", [
    "supports/120MM_Unsup_Emerald.stl",
    "Supported/body_unsupported.stl",
    "PS/arm_UNSUPP.stl",
    "Supported/NoSupport_arm.stl",
    "Supported/75MM_Unsupported_Ruby.stl",
])
def test_unsupported_filename_vetoes_a_supported_folder(member):
    """The mesh's own name can say it is raw even inside a supported folder."""
    assert _flags_presupported(member) is False


def test_supported_sibling_inside_the_same_tree_still_flags():
    """The veto is per-file: a clean name under supports/ is still presupported."""
    assert _flags_presupported("supports/120MM_Emerald.stl") is True


def test_fairy_fountain_archive_is_not_flagged():
    """Regression: 12 raw meshes under supports/Unsupported/ were all flagged."""
    base = "Fairy Fountain of Dreams/Triforce - 75_120mm"
    members = []
    for mm in ("120MM", "75MM"):
        for gem in ("Emerald", "Ruby", "Sapphire"):
            members.append(f"{base}/supports/Unsupported/{mm}_Unsup_{gem}.stl")
            members.append(
                f"{base} - NSFW/NSFW_supports/NSFW_Unsupported/{mm}_Unsup_{gem}_NSFW.stl"
            )
    assert len(members) == 12
    assert WARNING_PRESUPPORTED not in explain_warnings(members, **_PRESUPPORT_ONLY)

    # ...but a genuinely supported mesh in the same archive is still reported.
    members.append(f"{base}/supports/120MM_Emerald.stl")
    assert explain_warnings(members, **_PRESUPPORT_ONLY)[WARNING_PRESUPPORTED] == [
        f"{base}/supports/120MM_Emerald.stl"
    ]


def test_negation_wins_within_a_single_segment():
    """"supports_unsupported" resolves to raw regardless of token order."""
    assert _flags_presupported("supports_unsupported/body.stl") is False


@pytest.mark.parametrize("member", [
    "PSD Sources/body.stl",
    "Props/body.stl",
    "Supersonic/body.stl",
    "Upstairs/body.stl",
])
def test_folder_match_is_whole_token_not_substring(member):
    """"ps"/"sup" must not match inside a longer word."""
    assert _flags_presupported(member) is False


@pytest.mark.parametrize("member", [
    "Presupported/body.lys",
    "Presupported/body.chitubox",
    "PS/render.png",
    "PS/readme.txt",
])
def test_non_stl_in_presupport_folder_is_not_flagged(member):
    """The folder rule still only promotes .stl members."""
    assert _flags_presupported(member) is False


def test_directory_entry_never_flags():
    """Archives list folders as members; a folder must never raise the warning.

    Regression for a 7z whose only match was the directory
    "… Presupported lys" — no presupported mesh anywhere in the archive.
    """
    member = "2026-02 - Biker - Mice from Mars/Biker Mice from Mars Presupported lys"
    assert _flags_presupported(member) is False


def test_presupport_folder_reports_the_stl_not_the_folder():
    """The detail view names the mesh that qualified, not its folder."""
    names = ["Presupported/", "Presupported/body.stl", "Presupported/readme.txt"]
    result = explain_warnings(names, **_PRESUPPORT_ONLY)
    assert result[WARNING_PRESUPPORTED] == ["Presupported/body.stl"]


def test_explain_lists_every_nested_archive():
    result = explain_warnings(["a.zip", "b.7z", "c.rar", "body.stl", "p.png"], **_ALL_ON)
    assert result[WARNING_NESTED] == ["a.zip", "b.7z", "c.rar"]


def test_explain_absence_warnings_have_no_files():
    result = explain_warnings(["notes.txt"], **_ALL_ON)
    assert result[WARNING_NO_IMAGES] == []
    assert result[WARNING_NO_PRINTABLE] == []


def test_explain_uses_supplied_supports_patterns():
    names = ["hero_custom.stl", "body_supported.stl", "p.png"]
    result = explain_warnings(names, check_presupported=False, check_no_images=False,
                              check_nested_archive=False, check_supports=True,
                              check_no_printable=False,
                              supports_patterns=[r"_custom\.stl$"])
    assert result[WARNING_SUPPORTS] == ["hero_custom.stl"]


def test_explain_skips_invalid_supports_pattern():
    names = ["body_supported.stl"]
    result = explain_warnings(names, check_presupported=False, check_no_images=False,
                              check_nested_archive=False, check_supports=True,
                              check_no_printable=False,
                              supports_patterns=["(unclosed", r"_supported\.stl$"])
    assert result[WARNING_SUPPORTS] == ["body_supported.stl"]


def test_explain_respects_disabled_checks():
    names = ["body_presupported.stl", "nested.zip"]
    result = explain_warnings(names, check_presupported=False, check_no_images=False,
                              check_nested_archive=False, check_supports=False,
                              check_no_printable=False)
    assert result == {}


def test_explain_normalises_backslash_paths():
    """Windows-style member names are matched on their bare filename."""
    result = explain_warnings([r"sub\\dir\\sup_arm.stl", "p.png"],
                              check_presupported=True, check_no_images=False,
                              check_nested_archive=False, check_supports=False,
                              check_no_printable=False)
    assert result[WARNING_PRESUPPORTED] == [r"sub\\dir\\sup_arm.stl"]
