"""The enrollment QR: one scan that both opens the app and points it at a class.

The payload is a plain URL with query parameters rather than an encoded blob:

    https://<host>/mattend-0/?e=1&cid=PSP-LAB-SEC-D&l=PSP+Lab+Sec+D
        &f=<google form id>&u=<entry id>&n=<entry id>&c=<entry id>

Plain because it buys three things an encoded payload would not:

*   a phone's ordinary camera app opens it, so a student with nothing installed
    is onboarded by the same QR that enrolls them;
*   the PWA's own scanner can read it too, so an already-installed student never
    has to leave the app;
*   a student can *read* it and see which form they are about to submit to.

That last point is the honest mitigation for the fact that this is unsigned.
Signing would need a key on the phone, and the app secret is already in public
JavaScript, so a signature would prove nothing. A forged enrollment QR can send
a registration to the wrong sheet; it cannot forge attendance, which still needs
the pc secret that never leaves the teacher's machine.

Sized: form id plus three entry ids lands at QR version 8 (49x49) at ECC L,
comfortable from a printed A5 or a projector.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from .classroom import Classroom, ClassroomError

MARKER = "e"                    # ?e=1 marks a URL as an enrollment link
VERSION = "1"

_PARAM = {"cid": "cid", "label": "l", "form_id": "f", "uuid": "u", "name": "n", "cid_entry": "c"}


def build_url(room: Classroom, pwa_url: str) -> str:
    ready, why = room.ready
    if not ready:
        raise ClassroomError(f"cannot build an enrollment QR: {why}")
    if not pwa_url:
        raise ClassroomError("no pwa_url set in config.json -- where is the app published?")

    params = {
        MARKER: VERSION,
        _PARAM["cid"]: room.course_cid,
        _PARAM["label"]: room.label,
        _PARAM["form_id"]: room.form_id,
        _PARAM["uuid"]: room.entries["uuid"],
        _PARAM["name"]: room.entries["name"],
    }
    if room.entries.get("cid"):
        params[_PARAM["cid_entry"]] = room.entries["cid"]

    parts = urlsplit(pwa_url.strip())
    if not parts.scheme:
        raise ClassroomError(f"pwa_url must start with https:// (got {pwa_url!r})")
    path = parts.path or "/"
    return urlunsplit((parts.scheme, parts.netloc, path, urlencode(params), ""))


def looks_like_enrollment(text: str) -> bool:
    """Cheap check the PWA mirrors, to route a scan to enrollment vs attendance."""
    if "://" not in (text or ""):
        return False
    query = parse_qs(urlsplit(text).query)
    return query.get(MARKER, [""])[0] == VERSION


def parse_url(text: str) -> dict[str, str]:
    """Inverse of build_url. Raises ClassroomError on anything malformed."""
    if not looks_like_enrollment(text):
        raise ClassroomError("not a mattend enrollment link")
    query = parse_qs(urlsplit(text).query)

    def one(name: str, required: bool = True) -> str:
        value = query.get(name, [""])[0].strip()
        if required and not value:
            raise ClassroomError(f"enrollment link is missing ?{name}")
        return value

    return {
        "course_cid": one(_PARAM["cid"]).upper(),
        "label": one(_PARAM["label"], required=False),
        "form_id": one(_PARAM["form_id"]),
        "entry_uuid": one(_PARAM["uuid"]),
        "entry_name": one(_PARAM["name"]),
        "entry_cid": one(_PARAM["cid_entry"], required=False),
    }


def prefill_url(parsed: dict[str, str], device_uuid: str, full_name: str) -> str:
    """The Google Form link a student is sent to, with their details filled in."""
    params = {
        "usp": "pp_url",
        f"entry.{parsed['entry_uuid']}": device_uuid,
        f"entry.{parsed['entry_name']}": full_name,
    }
    if parsed.get("entry_cid"):
        params[f"entry.{parsed['entry_cid']}"] = parsed["course_cid"]
    return f"https://docs.google.com/forms/d/e/{parsed['form_id']}/viewform?{urlencode(params)}"
