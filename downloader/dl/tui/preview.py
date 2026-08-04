from ..format import human_bytes, human_duration, human_speed

RUNNING = ("active", "waiting")
MARKS = {
    True: {"ok": "✅", "fail": "❌", "wait": "⏳"},
    False: {"ok": "[ok]", "fail": "[fail]", "wait": "[...]"},
}


def summarise(results: list[dict], icons: bool = True) -> list[str]:
    """Format finished download results for printing after the preview exits."""
    mark = MARKS[bool(icons)]
    lines: list[str] = []
    running = 0
    for item in results:
        status = item.get("status", "")
        if status in RUNNING:
            running += 1
            continue
        name = item.get("name") or "(unnamed)"
        if status == "complete":
            total = int(item.get("bytes", 0) or 0)
            seconds = int(item.get("seconds", 0) or 0)
            detail = human_bytes(total)
            if seconds > 0:
                detail = (
                    f"{detail} in {human_duration(seconds)}   avg {human_speed(total // seconds)}"
                )
            lines.append(f"  {mark['ok']} {name}   {detail}")
        elif status == "removed":
            lines.append(f"  {mark['fail']} {name}   removed")
        else:
            lines.append(f"  {mark['fail']} {name}   {item.get('error') or 'failed'}")
    if running:
        lines.append(
            f"  {mark['wait']} {running} still downloading — `dl` to watch, `dl ls` to list"
        )
    return lines
