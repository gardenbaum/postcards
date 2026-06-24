"""Generate a random search term (camera-style filename) in pure Python.

This module replaces a previous auto-conversion from JavaScript to
Python via Js2Py. The original JavaScript was copied from the
"Random Personal Picture Finder" script by Dave Mattson
(http://www.diddly.com/random/) and described by its author as
"the ugliest code I ever distributed on the internet".

The function builds a random filename by selecting one of 26 camera
naming conventions (dcp0, dsc0, IMG_, ...) and appending a numeric
suffix. The numeric range and width vary per convention. The output
matches the upstream ``get_random_search_term()`` contract:

    >>> get_random_search_term()
    'IMG_0042.jpg'

Used by ``postcards.plugin_random.postcards_random`` to pick a Bing
image search keyword for the random postcard plugin.
"""

from __future__ import annotations

import random

# Camera naming conventions. Each entry is (prefix, range, width).
# The numeric ``range`` is the exclusive upper bound for the suffix;
# ``width`` is the zero-padded width.
#
# Two entries use empty prefixes and rely on the suffix alone (entries
# 17 and 18 in the original script); we model them as empty strings
# here. The original JS string concat (``var.put('str', '+')`` etc.)
# translates to plain Python string concatenation in this module.
_CAMERAS: tuple[tuple[str, int, int], ...] = (
    ("dcp0", 4000, 4),
    ("dsc0", 4000, 4),
    ("dscn", 4000, 4),
    ("mvc-", 400, 3),
    ("mvc0", 500, 4),
    ("P101", 50, 4),
    ("P", 50, 4),
    ("IMG_", 4000, 4),
    ("imag", 130, 4),
    ("1", 100, 2),
    ("dscf", 4000, 4),
    ("pdrm", 600, 4),
    ("IM00", 850, 4),
    ("EX00", 100, 4),
    ("dc", 4000, 4),
    ("pict", 600, 4),
    ("P00", 12000, 5),
    ("", 30, 4),
    ("", 50, 3),
    ("imgp", 2000, 4),
    ("pana", 200, 4),
    ("1", 100, 2),
    ("HPIM", 3700, 4),
    ("PCDV", 300, 4),
    ("_MG_", 4000, 4),
    ("IMG_", 4000, 4),
)


def _fmt(value: int, width: int) -> str:
    """Zero-pad ``value`` to ``width`` characters."""
    return str(value).zfill(width)


def _month_token(value: int) -> str:
    """Map an integer 0..12 to a 2-character month token.

    Mirrors the original JS, which used digits 0..9 directly and
    remapped 10/11/12 to ``a``/``b``/``c`` (a quirky but stable
    convention).
    """
    if value >= 10:
        return {10: "a", 11: "b", 12: "c"}.get(value, str(value))
    return str(value)


def _choice_range(choice: int) -> tuple[str, int, int]:
    """Return ``(prefix, range, width)`` for the camera convention at index ``choice``."""
    return _CAMERAS[choice]


def get_random_search_term() -> str:
    """Generate a random camera-style filename string.

    Pure-Python translation of the upstream Js2Py-converted function.
    The output is a ``.jpg`` filename whose prefix mirrors one of 26
    common digital camera naming conventions and whose suffix is a
    zero-padded number in the convention's natural range.
    """
    choice = random.randrange(len(_CAMERAS))
    prefix, range_upper, width = _choice_range(choice)

    parts: list[str] = [prefix] if prefix else []

    # Camera-specific suffix formatting. The original JS branches on
    # the choice index to build compound strings (date-based, year
    # prefixes, etc.). We model each branch directly.
    if choice == 6:  # "P" — month + date
        str_month = _month_token(random.randrange(13))
        str_date = _fmt(random.randrange(31), 2)
        parts.extend([str_month, str_date])
    elif choice == 9:  # "1" — short numeric with dashes
        str_thou = _fmt(random.randrange(3), 2)
        parts.append(str_thou)
        parts.append("-")
        parts.append(str_thou)
    elif choice == 17:  # empty prefix — month + date
        str_month = _fmt(random.randrange(13), 2)
        str_date = _fmt(random.randrange(31), 2)
        parts.extend([str_month, str_date])
    elif choice == 18:  # empty prefix — year + month + date
        str_year = _fmt(random.randrange(3), 2)
        str_month = _month_token(random.randrange(13))
        str_date = _fmt(random.randrange(31), 2)
        parts.extend([str_year, str_month, str_date])
    elif choice == 14:  # "dc" — number + size letter
        # The original puts "dc<number><size>"; we mirror that.
        str_number = _fmt(random.randrange(190), 4)
        size_index = random.randrange(3)
        size_letter = ["s", "m", "l"][size_index]
        # The JS does ``var.put('str', var.get('cams').get(var.get('choice')))``
        # then ``+= strnumber`` then ``+= strsize`` — so it ends up
        # ``dc<number><size>``. Reproduce that exactly.
        return prefix + str_number + size_letter + ".jpg"
    elif choice == 21:  # "1" — short with "1" prefix and _IMG suffix
        str_thou = _fmt(random.randrange(90), 2)
        str_foo = _fmt(random.randrange(range_upper), width)
        parts.extend([str_thou, "-", str_thou, str_foo, "_IMG"])
    elif choice == 25:  # "IMG_" — year/month/date with wildcard
        str_year = _fmt(random.randrange(3) + 7, 2)
        str_month = _fmt(random.randrange(11) + 1, 2)
        str_date = _fmt(random.randrange(30) + 1, 2)
        parts.append("20")
        parts.append(str_year)
        parts.append(str_month)
        parts.append(str_date)
        parts.append("_*")

    # Standard suffix: zero-padded number in the chosen range.
    parts.append(_fmt(random.randrange(range_upper), width))

    return "".join(parts) + ".jpg"


if __name__ == "__main__":
    print(get_random_search_term())
