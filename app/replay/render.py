"""Draw arbitrary FIFA matches from DuckDB replay state; export PNGs or H.264."""

from pathlib import Path
from datetime import datetime, timezone
from bisect import bisect_left
import json
import math
import shutil
import subprocess

from PIL import Image, ImageDraw, ImageFont
from .model import has_location

W, H, FPS = 1600, 900, 24
INK, MUTED, LINE = "#172F34", "#697A79", "#D5DBD2"
BLUE, ORANGE, DRAW, WHITE = "#2863C3", "#C06933", "#758583", "#FCFBF5"
PX, PY, PW, PH = 76, 320, 840, 544


def font_path(bold=False, mono=False):
    mac = Path("/System/Library/Fonts/Supplemental")
    face = "Andale Mono.ttf" if mono else "Arial Bold.ttf" if bold else "Arial.ttf"
    if (mac / face).exists():
        return str(mac / face)
    face = (
        "DejaVuSansMono.ttf"
        if mono
        else "DejaVuSans-Bold.ttf"
        if bold
        else "DejaVuSans.ttf"
    )
    for folder in ["/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu"]:
        path = Path(folder) / face
        if path.exists():
            return str(path)
    return None


fonts = {}


def font(size, bold=False, mono=False):
    key = (size, bold, mono)
    if key not in fonts:
        path = font_path(bold, mono)
        fonts[key] = (
            ImageFont.truetype(path, size)
            if path
            else ImageFont.load_default(size=size)
        )
    return fonts[key]


def label(d, xy, text, size=18, color=INK, bold=False, mono=False, anchor=None):
    d.text(xy, text, font=font(size, bold, mono), fill=color, anchor=anchor)


def wrap(d, text, xy, width, size=22, color=INK, bold=False, limit=4):
    lines = []
    line = ""
    for word in text.split():
        test = (line + " " + word).strip()
        if d.textlength(test, font=font(size, bold)) > width and line:
            lines.append(line)
            line = word
        else:
            line = test
    if line:
        lines.append(line)
    for i, line in enumerate(lines[:limit]):
        label(d, (xy[0], xy[1] + i * (size + 7)), line, size, color, bold)
    return len(lines[:limit]) * (size + 7)


def symbol(draw, x, y, typ, color, r=7, width=2):
    if typ == "Foul":
        draw.line((x - r, y - r, x + r, y + r), fill=color, width=width)
        draw.line((x - r, y + r, x + r, y - r), fill=color, width=width)
    elif typ == "Corner":
        draw.polygon(
            [(x, y - r), (x + r, y), (x, y + r), (x - r, y)], outline=color, width=width
        )
    elif typ == "Yellow card":
        draw.rectangle((x - r * 0.6, y - r, x + r * 0.6, y + r), fill="#B5942E")
    elif typ in ["Goal!", "Penalty Goal"]:
        pts = []
        for k in range(10):
            a = k * math.pi / 5 - math.pi / 2
            rr = r if k % 2 == 0 else r * 0.44
            pts.append((x + rr * math.cos(a), y + rr * math.sin(a)))
        draw.polygon(pts, fill=color)
    else:
        draw.ellipse((x - r, y - r, x + r, y + r), outline=color, width=width)


def utc(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%H:%M:%S")


def fit(draw, xy, text, max_width, size=20, color=INK, bold=False, **kwargs):
    while size > 10 and draw.textlength(text, font=font(size, bold)) > max_width:
        size -= 1
    while draw.textlength(text, font=font(size, bold)) > max_width:
        text = text[:-2] + "…"
    label(draw, xy, text, size, color, bold, **kwargs)


def location(event):
    return PX + event["x_raw"] / 100 * PW, PY + event["y_raw"] / 100 * PH


def event_symbol(draw, x, y, event, color, r=7, width=2):
    typ = {
        "goal": "Goal!",
        "own_goal": "Goal!",
        "foul": "Foul",
        "corner": "Corner",
        "yellow_card": "Yellow card",
    }.get(event["event_kind"], event["event_type_label"])
    symbol(draw, x, y, typ, color, r, width)


class Renderer:
    def __init__(self, replay):
        self.replay = replay
        self.names = [
            replay.match["home_team_name"],
            "Draw",
            replay.match["away_team_name"],
        ]
        self.colors = [BLUE, DRAW, ORANGE]
        self.base = self.background()

    def color(self, event):
        m = self.replay.match
        return (
            BLUE
            if event["team_id"] == m["home_team_id"]
            else ORANGE
            if event["team_id"] == m["away_team_id"]
            else MUTED
        )

    def background(self):
        im = Image.new("RGB", (W, H), "#F3F0E7")
        d = ImageDraw.Draw(im)
        m = self.replay.match
        label(d, (48, 25), "FIELD / FORECAST", 16, INK, True)
        metadata = " · ".join(
            str(x)
            for x in (
                m["competition_name"],
                m["season_name"],
                m["stage_name"],
                m["scheduled_kickoff_utc"].strftime("%d %b %Y"),
            )
            if x
        )
        fit(d, (1552, 25), metadata, 1170, 14, MUTED, anchor="ra")
        title = self.names[0] + "  ×  " + self.names[2]
        fit(d, (48, 58), title, 1180, 47, INK, True)
        label(
            d,
            (49, 115),
            "Recorded actions on the pitch. Market expectations through time.",
            18,
            MUTED,
        )
        d.line((48, 258, 1552, 258), fill=LINE, width=1)
        label(d, (48, 274), "01  /  PITCH EVENTS", 14, INK, True)
        label(
            d,
            (936, 274),
            "FIFA event coordinates · no inferred movement",
            13,
            MUTED,
            anchor="ra",
        )
        label(d, (990, 274), "02  /  MATCH FEED", 14, INK, True)
        fieldline = "#A7B6A7"
        cx = PX + PW / 2
        cy = PY + PH / 2
        d.rectangle((PX, PY, PX + PW, PY + PH), fill="#E7ECE0")
        for i in range(10):
            if i % 2 == 0:
                d.rectangle(
                    (PX + i * PW / 10, PY + 2, PX + (i + 1) * PW / 10, PY + PH - 2),
                    fill="#E2E9DA",
                )
        d.rectangle((PX, PY, PX + PW, PY + PH), outline=fieldline, width=2)
        d.line((cx, PY, cx, PY + PH), fill=fieldline, width=2)
        radius = PW * 9.15 / 105
        d.ellipse(
            (cx - radius, cy - radius, cx + radius, cy + radius),
            outline=fieldline,
            width=2,
        )
        for side in (0, 1):
            x = PX if side == 0 else PX + PW
            sign = 1 if side == 0 else -1
            for depth, height in ((16.5, 40.32), (5.5, 18.32)):
                xx = x + sign * PW * depth / 105
                hh = PH * height / 68
                d.rectangle(
                    (min(x, xx), cy - hh / 2, max(x, xx), cy + hh / 2),
                    outline=fieldline,
                    width=2,
                )
            pen = x + sign * PW * 11 / 105
            d.ellipse((pen - 3, cy - 3, pen + 3, cy + 3), fill=fieldline)
        label(d, (PX + 12, PY + 12), "OBSERVED EVENT LOCATIONS", 11, MUTED, mono=True)
        label(
            d,
            (PX + PW - 12, PY + PH - 20),
            "Provider orientation · no tracking or trajectories",
            12,
            MUTED,
            anchor="ra",
        )
        label(
            d,
            (48, 881),
            "SOURCE  FIFA + "
            + (self.replay.source_metadata.get("market_venue") or "Polymarket").title()
            + " histories · local DuckDB",
            11,
            MUTED,
        )
        label(
            d,
            (1552, 881),
            "EVENT REPLAY  /  NONLINEAR TIME COMPRESSION",
            11,
            MUTED,
            anchor="ra",
        )
        return im

    def frame(self, movie_time):
        r = self.replay
        t = r.utc_time(movie_time)
        state = r.state(t)
        im = self.base.copy()
        d = ImageDraw.Draw(im)
        current = state["current"]
        past = state["past"]
        label(
            d,
            (1552, 54),
            f"{state['score'][0]} : {state['score'][1]}",
            48,
            INK,
            True,
            mono=True,
            anchor="ra",
        )
        fit(
            d,
            (1552, 110),
            state["phase"] + " / " + utc(t) + " UTC",
            480,
            14,
            MUTED,
            anchor="ra",
        )
        for j, side in enumerate(("home", "draw", "away")):
            x = 48 + j * 507
            col = self.colors[j]
            price, status = r.odds(side, t)
            d.rounded_rectangle(
                (x, 153, x + 490, 242), radius=12, fill=WHITE, outline=LINE
            )
            d.rounded_rectangle((x, 153, x + 5, 242), radius=2, fill=col)
            fit(d, (x + 20, 165), self.names[j].upper(), 305, 14, col, True)
            label(d, (x + 469, 169), "90 MIN + STOPPAGE", 10, MUTED, anchor="ra")
            label(
                d,
                (x + 20, 188),
                f"{price['p']*100:.1f}%" if price else "—",
                35,
                col,
                True,
            )
            fit(
                d,
                (x + 469, 207),
                (utc(price["t"]) + " · " if price else "") + status,
                320,
                12,
                MUTED,
                anchor="ra",
            )
        overlay = Image.new("RGBA", (W, H))
        od = ImageDraw.Draw(overlay)
        for event in past:
            if not has_location(event):
                continue
            x, y = location(event)
            color = self.color(event)
            rgb = tuple(int(color[k : k + 2], 16) for k in (1, 3, 5))
            alpha = (
                190
                if event["event_kind"] in ("goal", "own_goal")
                else max(28, int(120 * math.exp(-(t - event["t"]) / 250)))
            )
            event_symbol(
                od,
                x,
                y,
                event,
                rgb + (alpha,),
                7 if event["event_kind"] == "goal" else 4,
                2,
            )
        im = Image.alpha_composite(im.convert("RGBA"), overlay).convert("RGB")
        d = ImageDraw.Draw(im)
        if current and has_location(current):
            x, y = location(current)
            color = self.color(current)
            pulse = 19 + 5 * math.sin(movie_time * 4)
            d.ellipse(
                (x - pulse, y - pulse, x + pulse, y + pulse), outline=color, width=2
            )
            d.ellipse((x - 12, y - 12, x + 12, y + 12), fill=WHITE)
            event_symbol(d, x, y, current, color, 10, 3)
        else:
            notice = (
                "Waiting for the first recorded event"
                if current is None
                else "No reliable pitch location recorded for this event"
            )
            d.rounded_rectangle(
                (PX + 95, PY + PH - 65, PX + PW - 95, PY + PH - 35),
                radius=6,
                fill=WHITE,
            )
            label(d, (PX + PW / 2, PY + PH - 60), notice, 13, MUTED, anchor="ma")
        x = 990
        label(
            d,
            (x, 304),
            (current["match_minute_raw"] or "—") if current else "—",
            42,
            INK,
            True,
        )
        if current:
            label(d, (1552, 317), utc(current["t"]) + " UTC", 14, MUTED, anchor="ra")
            fit(
                d,
                (x, 363),
                current["event_type_label"].upper(),
                550,
                24,
                self.color(current),
                True,
            )
            wrap(
                d,
                current["event_description"] or current["event_type_label"],
                (x, 401),
                550,
                21,
                INK,
                False,
                3,
            )
        else:
            label(d, (x, 363), "BEFORE KICKOFF", 24, INK, True)
        label(d, (x, 490), "RECENT ACTIONS", 11, MUTED, True)
        for k, event in enumerate(reversed(past[-4:])):
            yy = 515 + k * 28
            label(d, (x, yy), event["match_minute_raw"] or "—", 12, MUTED, mono=True)
            fit(d, (x + 95, yy), event["event_type_label"], 455, 14, self.color(event))
        if state["phase"] == "PENALTY SHOOTOUT" or any(state["penalties"]):
            label(
                d,
                (x, 620),
                f"PENALTIES  {state['penalties'][0]} : {state['penalties'][1]}",
                17,
                INK,
                True,
            )
        elif state["phase"] == "FULL TIME":
            label(
                d,
                (x, 620),
                f"FINAL RESULT  {state['score'][0]}–{state['score'][1]}",
                17,
                INK,
                True,
            )
        self.chart(d, t)
        for xx, kind, word in (
            (PX + 12, "Attempt at Goal", "shot / action"),
            (PX + 210, "Foul", "foul"),
            (PX + 333, "Corner", "corner"),
            (PX + 480, "Goal!", "goal"),
        ):
            symbol(d, xx, PY - 15, kind, MUTED, 5, 2)
            label(d, (xx + 12, PY - 22), word, 12, MUTED)
        return im

    def chart(self, d, t):
        r = self.replay
        gx, gy, gw, gh = 1025, 690, 510, 136
        label(d, (990, 647), "03  /  REGULATION MARKET HISTORY", 14, INK, True)
        for value in (0, 0.5, 1):
            yy = gy + gh * (1 - value)
            d.line((gx, yy, gx + gw, yy), fill=LINE)
            label(d, (gx - 9, yy - 6), str(int(value * 100)), 10, MUTED, anchor="ra")

        def chartx(at):
            return gx + gw * (at - r.start) / (r.finish - r.start)

        for side, color in zip(("home", "draw", "away"), self.colors):
            if side not in r.markets:
                continue
            cutoff = min(t, r.regulation_end) if r.regulation_end else r.start
            prices = r.markets[side]["prices"]
            times = r.price_times[side]
            begin = max(0, bisect_left(times, r.start) - 1)
            end = bisect_left(times, cutoff)
            for index in range(begin, end):
                p = prices[index]
                until = min(
                    cutoff,
                    p["t"] + r.max_price_age,
                    prices[index + 1]["t"] if index + 1 < len(prices) else cutoff,
                )
                if until <= r.start:
                    continue
                xx = chartx(max(r.start, p["t"]))
                yy = gy + gh * (1 - p["p"])
                d.line((xx, yy, chartx(until), yy), fill=color, width=3)
                if (
                    index + 1 < end
                    and prices[index + 1]["t"] <= p["t"] + r.max_price_age
                ):
                    nexty = gy + gh * (1 - prices[index + 1]["p"])
                    d.line(
                        (chartx(until), yy, chartx(until), nexty), fill=color, width=3
                    )
        xt = chartx(t)
        d.line((xt, gy - 4, xt, gy + gh + 4), fill=INK)
        label(d, (gx, gy + gh + 9), "START", 10, MUTED)
        label(d, (gx + gw, gy + gh + 9), "FULL TIME", 10, MUTED, anchor="ra")
        if not r.markets:
            fit(d, (gx + gw / 2, gy + 55), r.odds_status, 480, 14, MUTED, anchor="ma")


def render(replay, output, preview=False, fps=FPS, max_seconds=None):
    if fps < 1 or fps > 60:
        raise ValueError("FPS must be between 1 and 60")
    if max_seconds is not None and max_seconds <= 0:
        raise ValueError("--max-seconds must be positive")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    renderer = Renderer(replay)
    renderer.frame(1.8).save(output / "poster.png")
    renderer.frame(replay.duration - 0.1).save(output / "final.png")
    for i, event in enumerate(
        e
        for e in replay.events
        if e["event_kind"] in ("goal", "own_goal") and e["period_code"] != 11
    ):
        renderer.frame(replay.movie_time(event["t"])).save(
            output / f"preview-goal-{i+1}.png"
        )
    for period in (7, 9, 11):
        if period in replay.period_starts:
            renderer.frame(replay.movie_time(replay.period_starts[period])).save(
                output / f"period-{period}.png"
            )
    manifest = replay.manifest() | dict(
        fps=fps, resolution=[W, H], preview=preview, video=None
    )
    if not preview:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("Install FFmpeg with libx264 or use --preview")
        # Match IDs never become paths; this also prevents stale demo filenames.
        destination = output / "replay.mp4"
        seconds = min(replay.duration, max_seconds) if max_seconds else replay.duration
        command = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            f"{W}x{H}",
            "-r",
            str(fps),
            "-i",
            "-",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(destination),
        ]
        process = subprocess.Popen(command, stdin=subprocess.PIPE)
        try:
            for i in range(math.ceil(seconds * fps)):
                process.stdin.write(renderer.frame(i / fps).tobytes())
                if i % (fps * 10) == 0:
                    print(f"Rendered {i/fps:.0f}s / {seconds:.1f}s", flush=True)
            process.stdin.close()
            if process.wait() != 0:
                raise RuntimeError("FFmpeg failed")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
        manifest.update(
            video=destination.name,
            video_duration_seconds=seconds,
            video_truncated=seconds < replay.duration,
        )
    (output / "render-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest
