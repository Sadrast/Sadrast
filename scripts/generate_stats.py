#!/usr/bin/env python3
"""Generate assets/stats.svg and assets/activity.svg from real GitHub data.

Runs inside GitHub Actions (see .github/workflows/stats.yml). Data comes from the
GitHub GraphQL API through the `gh` CLI, so no token is ever handled by this file.

Only public repositories and public contributions are used unless the workflow is
given a token with wider access. Nothing is estimated or invented: if a request
fails, the script exits with an error and the previously committed images stay in
place.

Usage:
  python3 scripts/generate_stats.py                 # fetch live data, write assets/
  python3 scripts/generate_stats.py --selftest DIR  # synthetic data, written to DIR only
"""
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
from xml.sax.saxutils import escape

LOGIN = os.environ.get("PROFILE_LOGIN", "Sadrast")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")

SANS = "-apple-system,BlinkMacSystemFont,'SF Pro Text','Inter','Segoe UI','Helvetica Neue',Arial,sans-serif"
CARD, CARD2, LINE = "#0C1417", "#101A1E", "#1F3036"
TEXT, FOG, HAZE = "#E9F1F2", "#93A4AA", "#5E7077"
TEAL, TEAL_HI, BRONZE, GOLD = "#2BB3A8", "#3FC1B5", "#C29B5B", "#D9B574"
HEAT = ["#122024", "#0F4B50", "#12786F", "#2BB3A8", "#7FE9DB"]
FALLBACK_LANG_COLORS = ["#3FC1B5", "#D9B574", "#5B8FD0", "#C29B5B", "#7FE9DB", "#8FA3A9"]

Q_USER = """
query($login: String!) {
  user(login: $login) {
    login
    name
    followers { totalCount }
    repositories(ownerAffiliations: OWNER, privacy: PUBLIC, isFork: false) { totalCount }
    contributionsCollection {
      totalCommitContributions
      totalIssueContributions
      totalPullRequestContributions
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount weekday } }
      }
    }
  }
}
"""

Q_REPOS = """
query($login: String!, $cursor: String) {
  user(login: $login) {
    repositories(ownerAffiliations: OWNER, privacy: PUBLIC, isFork: false, first: 100, after: $cursor) {
      pageInfo { hasNextPage endCursor }
      nodes {
        stargazerCount
        languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name color } }
        }
      }
    }
  }
}
"""


def gql(query, **variables):
    cmd = ["gh", "api", "graphql", "-f", "query=" + query]
    for key, value in variables.items():
        if value is not None:
            cmd += ["-f", "%s=%s" % (key, value)]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit("GitHub API request failed (exit %d)" % proc.returncode)
    payload = json.loads(proc.stdout)
    if payload.get("errors"):
        raise SystemExit("GitHub API returned errors: %s" % json.dumps(payload["errors"])[:400])
    return payload["data"]


def collect():
    user = gql(Q_USER, login=LOGIN)["user"]
    if user is None:
        raise SystemExit("User %r not found" % LOGIN)
    stars, langs, cursor = 0, {}, None
    for _ in range(10):
        page = gql(Q_REPOS, login=LOGIN, cursor=cursor)["user"]["repositories"]
        for repo in page["nodes"]:
            stars += repo["stargazerCount"]
            for edge in repo["languages"]["edges"]:
                name = edge["node"]["name"]
                size, color = langs.get(name, (0, None))
                langs[name] = (size + edge["size"], edge["node"]["color"] or color)
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]
    cc = user["contributionsCollection"]
    days = [d for w in cc["contributionCalendar"]["weeks"] for d in w["contributionDays"]]
    weeks = cc["contributionCalendar"]["weeks"]
    return {
        "login": user["login"],
        "repos": user["repositories"]["totalCount"],
        "stars": stars,
        "followers": user["followers"]["totalCount"],
        "commits": cc["totalCommitContributions"],
        "prs": cc["totalPullRequestContributions"],
        "issues": cc["totalIssueContributions"],
        "languages": sorted(((n, c, s) for n, (s, c) in langs.items()), key=lambda t: -t[2]),
        "weeks": [[{"date": d["date"], "count": d["contributionCount"], "weekday": d["weekday"]} for d in w["contributionDays"]] for w in weeks],
        "total": cc["contributionCalendar"]["totalContributions"],
        "synced": dt.datetime.now(dt.timezone.utc).date().isoformat(),
    }


def synthetic():
    """Deterministic fake data for --selftest only. Never written to assets/."""
    start = dt.date(2025, 11, 9)
    weeks, seed, total = [], 7, 0
    for w in range(53):
        week = []
        for d in range(7):
            day = start + dt.timedelta(days=w * 7 + d)
            seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
            count = (seed >> 16) % 9 if (seed >> 8) % 3 else 0
            total += count
            week.append({"date": day.isoformat(), "count": count, "weekday": d})
        weeks.append(week)
    return {"login": "selftest", "repos": 12, "stars": 34, "followers": 5, "commits": 410, "prs": 22, "issues": 9,
            "languages": [("Swift", "#F05138", 5200), ("TypeScript", "#3178c6", 3100), ("JavaScript", "#f1e05a", 900), ("Python", "#3572A5", 500), ("HTML", None, 200)],
            "weeks": weeks, "total": total, "synced": "selftest"}


def num(n):
    return "{:,}".format(int(n))


MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def pretty_date(iso):
    if not iso:
        return "-"
    return "%s %d" % (MONTHS[int(iso[5:7]) - 1], int(iso[8:10]))


def streaks(days):
    cur = 0
    i = len(days) - 1
    if i >= 0 and days[i]["count"] == 0:
        i -= 1
    while i >= 0 and days[i]["count"] > 0:
        cur += 1
        i -= 1
    best = run = 0
    for d in days:
        run = run + 1 if d["count"] > 0 else 0
        best = max(best, run)
    return cur, best


def wrap(w, h, title, desc, defs, style, body):
    return """<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" role="img" aria-labelledby="t d">
  <title id="t">%s</title>
  <desc id="d">%s</desc>
  <defs>%s</defs>
  <style>%s
    @media (prefers-reduced-motion:reduce){*{animation:none !important}}
  </style>
%s
</svg>
""" % (w, h, w, h, escape(title), escape(desc), defs, style, body)


def render_stats(data):
    W, H = 900, 300
    metrics = [("repos", "Public repositories"), ("stars", "Stars received"), ("followers", "Followers"),
               ("commits", "Commits (12 mo)"), ("prs", "Pull requests (12 mo)"), ("issues", "Issues (12 mo)")]
    cells = ""
    for i, (key, label) in enumerate(metrics):
        x, y = 30 + (i % 2) * 220, 88 + (i // 2) * 66
        cells += '<text class="v" x="%d" y="%d">%s</text><text class="l" x="%d" y="%d">%s</text>\n  ' % (x, y, num(data[key]), x, y + 23, escape(label))
    langs = data["languages"]
    total = float(sum(s for _, _, s in langs)) or 1.0
    bar_x, bar_w = 500, 370
    segs, legend, x = "", "", float(bar_x)
    for i, (name, color, size) in enumerate(langs[:6]):
        color = color or FALLBACK_LANG_COLORS[i % len(FALLBACK_LANG_COLORS)]
        pct = size * 100.0 / total
        w = bar_w * size / total
        segs += '<rect x="%.2f" y="66" width="%.2f" height="12" fill="%s"/>' % (x, max(w, 0.0), color)
        x += w
        y = 112 + i * 27
        legend += ('<circle cx="%d" cy="%d" r="5" fill="%s"/><text class="n" x="%d" y="%d">%s</text>'
                   '<text class="p" x="%d" y="%d" text-anchor="end">%.1f%%</text>\n  ') % (bar_x + 5, y - 4, color, bar_x + 20, y, escape(name), bar_x + bar_w, y, pct)
    if len(langs) > 6:
        rest = sum(s for _, _, s in langs[6:]) * 100.0 / total
        segs += '<rect x="%.2f" y="66" width="%.2f" height="12" fill="#3B4E54"/>' % (x, bar_x + bar_w - x)
        legend += ('<text class="p" x="%d" y="%d" text-anchor="end">Other %.1f%%</text>\n  ' % (bar_x + bar_w, 112 + 6 * 27, rest))
    if not langs:
        legend = '<text class="n" x="%d" y="112">No public language data yet.</text>' % bar_x
    style = ("""
    .v{font-family:%s;font-size:32px;font-weight:700;letter-spacing:-.6px;fill:%s}
    .l{font-family:%s;font-size:13px;fill:%s}
    .h{font-family:%s;font-size:15px;font-weight:650;fill:%s}
    .n{font-family:%s;font-size:14px;fill:%s}
    .p{font-family:%s;font-size:13px;fill:%s}
    .f{font-family:%s;font-size:11.5px;fill:%s}
    .bar{transform-box:fill-box;transform-origin:left center;animation:grow 1.2s cubic-bezier(.2,.7,.2,1) both}
    @keyframes grow{from{transform:scaleX(0)}to{transform:scaleX(1)}}""" % (SANS, TEXT, SANS, FOG, SANS, TEXT, SANS, TEXT, SANS, FOG, SANS, HAZE))
    defs = '<clipPath id="pill"><rect x="%d" y="66" width="%d" height="12" rx="6"/></clipPath>' % (bar_x, bar_w)
    body = """  <rect x=".5" y=".5" width="%d" height="%d" rx="16" fill="%s" stroke="%s"/>
  <text class="h" x="30" y="44">Overview</text>
  %s<path d="M470.5 28v236" stroke="%s"/>
  <text class="h" x="%d" y="44">Languages in public repositories</text>
  <g clip-path="url(#pill)"><rect x="%d" y="66" width="%d" height="12" fill="#16262B"/><g class="bar">%s</g></g>
  %s<text class="f" x="30" y="284">Public data only. Synced %s (UTC) by GitHub Actions.</text>""" % (
        W - 1, H - 1, CARD, LINE, cells, LINE, bar_x, bar_x, bar_w, segs, legend, escape(data["synced"]))
    desc = "GitHub statistics for %s: %s public repositories, %s stars, %s followers, and a language breakdown." % (data["login"], num(data["repos"]), num(data["stars"]), num(data["followers"]))
    return wrap(W, H, "GitHub statistics", desc, defs, style, body)


def levels(counts):
    nz = sorted(c for c in counts if c > 0)
    if not nz:
        return lambda c: 0
    q1, q2, q3 = nz[len(nz) // 4], nz[len(nz) // 2], nz[(len(nz) * 3) // 4]
    def lvl(c):
        if c <= 0:
            return 0
        return 1 if c <= q1 else 2 if c <= q2 else 3 if c <= q3 else 4
    return lvl


def render_activity(data):
    W, H = 900, 270
    weeks = data["weeks"]
    days = [d for w in weeks for d in w]
    lvl = levels([d["count"] for d in days])
    cur, best = streaks(days)
    active = sum(1 for d in days if d["count"] > 0)
    busiest = max(days, key=lambda d: d["count"]) if days else {"count": 0, "date": ""}
    x0, y0, pitch, cell = 58, 78, 15, 12
    cols, months, last_month = "", "", None
    for ci, week in enumerate(weeks):
        rects = ""
        for d in week:
            c = d["count"]
            rects += '<rect x="%d" y="%d" width="%d" height="%d" rx="3" fill="%s"><title>%s: %d</title></rect>' % (
                x0 + ci * pitch, y0 + d["weekday"] * pitch, cell, cell, HEAT[lvl(c)], d["date"], c)
        cols += '<g class="col" style="animation-delay:%.3fs">%s</g>\n  ' % (ci * 0.022, rects)
        month = week[0]["date"][5:7] if week else None
        if month and month != last_month and ci < len(weeks) - 2:
            months += '<text class="m" x="%d" y="68">%s</text>' % (x0 + ci * pitch, MONTHS[int(month) - 1])
        last_month = month or last_month
    wd = "".join('<text class="m" x="%d" y="%d" text-anchor="end">%s</text>' % (x0 - 10, y0 + i * pitch + 10, t) for i, t in ((1, "Mon"), (3, "Wed"), (5, "Fri")))
    leg_x = 872 - (5 * 15 + 70)
    legend = '<text class="m" x="%d" y="197">Less</text>' % leg_x
    for i, c in enumerate(HEAT):
        legend += '<rect x="%d" y="187" width="12" height="12" rx="3" fill="%s"/>' % (leg_x + 32 + i * 15, c)
    legend += '<text class="m" x="%d" y="197">More</text>' % (leg_x + 32 + 5 * 15 + 4)
    stats = [("Current streak", "%s day%s" % (num(cur), "" if cur == 1 else "s")),
             ("Longest streak", "%s day%s" % (num(best), "" if best == 1 else "s")),
             ("Active days", num(active)),
             ("Busiest day", "%s on %s" % (num(busiest["count"]), pretty_date(busiest["date"])))]
    blocks = ""
    for i, (label, value) in enumerate(stats):
        x = 30 + i * 218
        blocks += '<text class="sv" x="%d" y="238">%s</text><text class="sl" x="%d" y="256">%s</text>\n  ' % (x, escape(value), x, escape(label))
    style = ("""
    .t{font-family:%s;font-size:14px;fill:%s} .t b{font-weight:700}
    .m{font-family:%s;font-size:11px;fill:%s}
    .sv{font-family:%s;font-size:20px;font-weight:700;letter-spacing:-.3px;fill:%s}
    .sl{font-family:%s;font-size:12.5px;fill:%s}
    .col{animation:rise .6s ease-out both}
    @keyframes rise{from{opacity:0;transform:translateY(5px)}to{opacity:1;transform:none}}""" % (SANS, FOG, SANS, HAZE, SANS, TEXT, SANS, FOG))
    body = """  <rect x=".5" y=".5" width="%d" height="%d" rx="16" fill="%s" stroke="%s"/>
  <text class="t" x="30" y="38"><tspan font-weight="700" fill="%s">%s</tspan> public contributions in the last 12 months</text>
  %s%s
  %s%s
  <path d="M30.5 212.5h839" stroke="%s"/>
  %s""" % (W - 1, H - 1, CARD, LINE, TEXT, num(data["total"]), months, wd, cols, legend, LINE, blocks)
    desc = "Contribution calendar for %s: %s public contributions in the last 12 months, current streak %d days, longest streak %d days." % (data["login"], num(data["total"]), cur, best)
    return wrap(W, H, "Contribution activity", desc, "", style, body)


def write_atomic(path, text):
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def main(argv):
    if len(argv) >= 3 and argv[1] == "--selftest":
        out, data = argv[2], synthetic()
    else:
        out, data = ASSETS, collect()
    write_atomic(os.path.join(out, "stats.svg"), render_stats(data))
    write_atomic(os.path.join(out, "activity.svg"), render_activity(data))
    print("Wrote stats.svg and activity.svg to %s" % out)


if __name__ == "__main__":
    main(sys.argv)
