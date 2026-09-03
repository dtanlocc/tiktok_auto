from invisible_playwright.media_integrity import merge_faithful_canvas_readback


def test_faithful_canvas_readback_is_opt_in_and_does_not_mutate_input():
    supplied = {"example.pref": 7, "zoom.stealth.canvas.substitute_pixels": True}

    default = merge_faithful_canvas_readback(supplied, False)
    faithful = merge_faithful_canvas_readback(supplied, True)

    assert supplied["zoom.stealth.canvas.substitute_pixels"] is True
    assert default["zoom.stealth.canvas.substitute_pixels"] is True
    assert faithful["zoom.stealth.canvas.substitute_pixels"] is False
    assert faithful["example.pref"] == 7
