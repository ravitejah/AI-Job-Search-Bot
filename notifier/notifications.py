"""
Notification System
Sends beautifully formatted HTML email grouped by platform.
Jobs sorted by most recent first, with human-readable posted time.
"""
import smtplib
import json
import urllib.request
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timezone
from collections import defaultdict
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))
from config import NOTIFICATIONS

# Platform branding
PLATFORM_META = {
    "LinkedIn":  {"color": "#0077B5", "icon": "in", "bg": "#E8F4FD"},
    "Indeed":    {"color": "#2164F3", "icon": "In", "bg": "#EEF2FF"},
    "Glassdoor": {"color": "#0CAA41", "icon": "GD", "bg": "#EDFAF3"},
    "Dice":      {"color": "#EB1C26", "icon": "DC", "bg": "#FEF2F2"},
    "Handshake": {"color": "#E8543A", "icon": "HS", "bg": "#FFF3F0"},
    "JobRight":  {"color": "#7C3AED", "icon": "JR", "bg": "#F5F3FF"},
}


# ── City grouping ────────────────────────────────────────────────────────────
# Order defines display order within each platform section.
# Each entry: (short_label, [keywords to match against job location, case-insensitive])
CITY_ORDER = [
    ("Hyderabad",     ["hyderabad", "telangana"]),
    ("Remote",        ["remote"]),
    ("Bangalore",     ["bangalore", "bengaluru", "karnataka"]),
    ("Chennai",       ["chennai", "tamil nadu"]),
    ("Visakhapatnam", ["visakhapatnam", "vizag", "andhra pradesh"]),
    ("Other",         []),   # catch-all — always last
]

def resolve_city(location: str) -> str:
    """Map a raw job location string to a short city label."""
    if not location or location.strip().lower() in ("india", ""):
        return "Other"
    loc_lower = location.lower()
    for label, keywords in CITY_ORDER:
        if label == "Other":
            continue
        if any(kw in loc_lower for kw in keywords):
            return label
    return "Other"


def build_city_rows(jobs: list[dict], color: str) -> str:
    """
    Group jobs by city (in CITY_ORDER), sort each group fresh-first,
    and emit table rows with a city sub-header row between groups.
    """
    # Bucket jobs
    buckets: dict[str, list[dict]] = {label: [] for label, _ in CITY_ORDER}
    for job in jobs:
        city = resolve_city(job.get("location", "") or "")
        buckets[city].append(job)

    rows = ""
    for label, _ in CITY_ORDER:
        group = buckets[label]
        if not group:
            continue

        # City sub-header row
        city_icons = {
            "Hyderabad":     "🏙️",
            "Remote":        "🌐",
            "Bangalore":     "🌆",
            "Chennai":       "🌇",
            "Visakhapatnam": "🌊",
            "Other":         "📍",
        }
        icon = city_icons.get(label, "📍")
        rows += f"""
        <tr>
          <td colspan="4"
              style="padding:8px 18px 6px;background:#f8fafc;
                     border-bottom:1px solid #e2e8f0;border-top:2px solid #e2e8f0;">
            <span style="font-size:11px;font-weight:800;color:#475569;
                         text-transform:uppercase;letter-spacing:0.1em;">
              {icon} {label}
            </span>
            <span style="margin-left:8px;background:#e2e8f0;color:#64748b;
                         font-size:10px;font-weight:700;padding:1px 7px;
                         border-radius:10px;">
              {len(group)}
            </span>
          </td>
        </tr>"""

        # Jobs within city, sorted fresh-first
        group = sort_jobs_by_recency(group)
        for job in group:
            score = job.get("match_score", 0)
            if score >= 85:
                score_color = "#15803d"; score_bg = "#f0fdf4"; score_border = "#86efac"
            elif score >= 70:
                score_color = "#b45309"; score_bg = "#fffbeb"; score_border = "#fcd34d"
            else:
                score_color = "#b91c1c"; score_bg = "#fff1f2"; score_border = "#fca5a5"

            reason = job.get("match_reason", "")
            reason = reason[:85] + "..." if len(reason) > 85 else reason
            recency_badge = get_recency_badge(job.get("posted_at", ""))

            rows += f"""
        <tr>
          <td style="padding:14px 18px;border-bottom:1px solid #f1f5f9;vertical-align:middle;">
            <a href="{job['url']}" target="_blank"
               style="color:{color};font-weight:700;font-size:14px;
                      text-decoration:none;display:block;margin-bottom:5px;
                      line-height:1.3;">
              {job['title']}
            </a>
            <div style="color:#64748b;font-size:12px;margin-bottom:6px;line-height:1.5;">
              <span style="font-weight:600;color:#374151;">🏢 {job['company']}</span>
              <span style="color:#cbd5e1;margin:0 6px;">|</span>
              <span>📍 {job.get('location') or 'India'}</span>
            </div>
            <div style="margin-top:4px;">{recency_badge}</div>
          </td>
          <td style="padding:14px 18px;border-bottom:1px solid #f1f5f9;
                     text-align:center;vertical-align:middle;white-space:nowrap;">
            <div style="background:{score_bg};color:{score_color};
                        border:1.5px solid {score_border};
                        padding:5px 12px;border-radius:24px;
                        font-size:14px;font-weight:800;
                        display:inline-block;letter-spacing:0.02em;">
              {score}%
            </div>
          </td>
          <td style="padding:14px 18px;border-bottom:1px solid #f1f5f9;font-size:12px;
                     color:#64748b;vertical-align:middle;max-width:220px;line-height:1.6;">
            <span style="font-style:italic;">{reason}</span>
          </td>
          <td style="padding:14px 18px;border-bottom:1px solid #f1f5f9;
                     text-align:center;vertical-align:middle;">
            <a href="{job['url']}" target="_blank"
               style="background:{color};color:white;padding:7px 16px;
                      border-radius:8px;font-size:12px;font-weight:700;
                      text-decoration:none;white-space:nowrap;
                      display:inline-block;letter-spacing:0.03em;
                      box-shadow:0 2px 6px {color}44;">
              Apply →
            </a>
          </td>
        </tr>"""
    return rows
# ─────────────────────────────────────────────────────────────────────────────


def parse_posted_time(posted_at: str) -> datetime | None:
    if not posted_at:
        return None
    formats = [
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%d",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(posted_at[:len(fmt)+2].strip("Z"), fmt.strip("Z"))
        except ValueError:
            continue
    return None


def human_time(posted_at: str) -> tuple[str, int]:
    dt = parse_posted_time(posted_at)
    if not dt:
        return "Recently posted", 99999

    now = datetime.now()
    if dt.tzinfo:
        dt = dt.replace(tzinfo=None)

    diff = now - dt
    minutes = int(diff.total_seconds() / 60)

    if minutes < 0:
        return "Just posted", 0
    elif minutes < 60:
        label = "Just now" if minutes < 2 else f"{minutes}m ago"
        color = "#16a34a" 
    elif minutes < 1440: 
        hours = minutes // 60
        label = f"{hours}h ago"
        color = "#16a34a" if hours < 6 else "#d97706"
    elif minutes < 2880: 
        label = "Yesterday"
        color = "#d97706" 
    else:
        days = minutes // 1440
        label = f"{days} days ago"
        color = "#dc2626" 

    return label, minutes


def sort_jobs_by_recency(jobs: list[dict]) -> list[dict]:
    def sort_key(job):
        _, minutes = human_time(job.get("posted_at", ""))
        return (minutes, -job.get("match_score", 0))
    return sorted(jobs, key=sort_key)


def get_recency_badge(posted_at: str) -> str:
    label, minutes = human_time(posted_at)

    if minutes < 60:
        bg = "#dcfce7"; color = "#16a34a"; dot = "🟢"
    elif minutes < 360:
        bg = "#dcfce7"; color = "#16a34a"; dot = "🟢"
    elif minutes < 1440: 
        bg = "#fef3c7"; color = "#d97706"; dot = "🟡"
    elif minutes < 2880: 
        bg = "#fef3c7"; color = "#d97706"; dot = "🟡"
    else:
        bg = "#fee2e2"; color = "#dc2626"; dot = "🔴"

    return (
        f'<span style="background:{bg};color:{color};padding:3px 10px;'
        f'border-radius:20px;font-size:11px;font-weight:700;white-space:nowrap;'
        f'letter-spacing:0.02em;border:1px solid {color}33;">'
        f'{dot} {label}</span>'
    )


def build_platform_section(platform: str, jobs: list[dict]) -> str:
    meta  = PLATFORM_META.get(platform, {"color": "#475569", "icon": "??", "bg": "#F8FAFC"})
    color = meta["color"]
    bg    = meta["bg"]
    icon  = meta["icon"]

    # City-grouped rows (each city sorted fresh-first internally)
    rows = build_city_rows(jobs, color)

    return f"""
    <!--[if mso]><table width="100%"><tr><td><![endif]-->
    <div style="margin-bottom:24px;border-radius:12px;overflow:hidden;
                box-shadow:0 1px 4px rgba(0,0,0,0.07),0 4px 16px rgba(0,0,0,0.05);">

      <!-- Platform header -->
      <div style="background:{bg};border-left:4px solid {color};
                  padding:14px 20px;display:flex;align-items:center;
                  border-bottom:1px solid {color}22;">
        <span style="background:{color};color:white;font-size:11px;font-weight:800;
                     padding:4px 9px;border-radius:6px;margin-right:10px;
                     letter-spacing:0.08em;text-transform:uppercase;">{icon}</span>
        <span style="font-size:15px;font-weight:800;color:{color};
                     letter-spacing:0.01em;">{platform}</span>
        <span style="margin-left:auto;background:{color};color:white;
                     font-size:11px;font-weight:700;padding:3px 11px;
                     border-radius:20px;letter-spacing:0.04em;">
          {len(jobs)} job{'s' if len(jobs) != 1 else ''}
        </span>
      </div>

      <!-- Jobs table -->
      <table width="100%" style="border-collapse:collapse;background:#ffffff;">
        <thead>
          <tr style="background:#f8fafc;border-bottom:2px solid #e2e8f0;">
            <th style="padding:10px 18px;text-align:left;font-size:10px;
                       color:#94a3b8;font-weight:700;text-transform:uppercase;
                       letter-spacing:0.08em;">Position &amp; Details</th>
            <th style="padding:10px 18px;text-align:center;font-size:10px;
                       color:#94a3b8;font-weight:700;text-transform:uppercase;
                       letter-spacing:0.08em;white-space:nowrap;">Match</th>
            <th style="padding:10px 18px;text-align:left;font-size:10px;
                       color:#94a3b8;font-weight:700;text-transform:uppercase;
                       letter-spacing:0.08em;">Why it fits</th>
            <th style="padding:10px 18px;text-align:center;font-size:10px;
                       color:#94a3b8;font-weight:700;text-transform:uppercase;
                       letter-spacing:0.08em;">Action</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>

    </div>
    <!--[if mso]></td></tr></table><![endif]-->"""


def send_email_alert(jobs: list[dict]):
    if not jobs:
        return

    cfg = NOTIFICATIONS
    now = datetime.now().strftime("%B %d, %Y at %I:%M %p")

    all_sorted = sort_jobs_by_recency(jobs)

    by_platform = defaultdict(list)
    for job in all_sorted:
        by_platform[job.get("source", "LinkedIn")].append(job)

    def platform_freshness(item):
        platform_jobs = item[1]
        _, minutes = human_time(platform_jobs[0].get("posted_at", ""))
        return minutes

    sorted_platforms = sorted(by_platform.items(), key=platform_freshness)

    sections = "".join(
        build_platform_section(platform, platform_jobs)
        for platform, platform_jobs in sorted_platforms
    )

    # Platform pills
    pills = ""
    for platform, platform_jobs in sorted_platforms:
        meta = PLATFORM_META.get(platform, {"color": "#475569", "bg": "#F8FAFC"})
        pills += f"""
        <span style="display:inline-block;background:rgba(255,255,255,0.12);
                     color:white;border:1px solid rgba(255,255,255,0.25);
                     padding:4px 13px;border-radius:20px;
                     font-size:11px;font-weight:700;margin:3px 3px 3px 0;
                     letter-spacing:0.04em;">
          {platform} &nbsp;<span style="opacity:0.75;">{len(platform_jobs)}</span>
        </span>"""

    # Stats bar
    total_fresh = sum(1 for j in jobs if human_time(j.get("posted_at", ""))[1] < 360)

    legend = f"""
    <div style="background:#ffffff;border:1px solid #e2e8f0;border-radius:10px;
                padding:12px 18px;margin-bottom:24px;
                box-shadow:0 1px 3px rgba(0,0,0,0.05);">
      <table width="100%" style="border-collapse:collapse;">
        <tr>
          <td style="font-size:11px;color:#64748b;padding:0;">
            <span style="font-weight:700;color:#374151;font-size:12px;">
              🕐 Posting time key:
            </span>
            &nbsp;&nbsp;
            <span style="color:#16a34a;font-weight:600;">🟢 Under 6 hrs</span>
            &nbsp;·&nbsp;
            <span style="color:#d97706;font-weight:600;">🟡 6 – 48 hrs</span>
            &nbsp;·&nbsp;
            <span style="color:#dc2626;font-weight:600;">🔴 Older than 2 days</span>
          </td>
          <td style="text-align:right;white-space:nowrap;font-size:11px;
                     color:#64748b;font-weight:600;padding:0;">
            ⚡ {total_fresh} fresh listings
          </td>
        </tr>
      </table>
    </div>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>Job Hunter Alert</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap');

    * {{ box-sizing: border-box; }}

    body {{
      margin: 0;
      padding: 0;
      background: #f0f4f8;
      font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;
      -webkit-text-size-adjust: 100%;
      color: #1e293b;
    }}

    .wrapper {{
      max-width: 780px;
      margin: 0 auto;
      padding: 28px 16px 40px;
    }}

    /* ── Hero header ── */
    .hero {{
      background: linear-gradient(135deg, #0f172a 0%, #1d3461 55%, #1e4d8c 100%);
      border-radius: 16px;
      padding: 32px 36px;
      margin-bottom: 20px;
      position: relative;
      overflow: hidden;
    }}
    .hero-eyebrow {{
      color: #93c5fd;
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.14em;
      margin-bottom: 8px;
    }}
    .hero-title {{
      color: #ffffff;
      font-size: 26px;
      font-weight: 800;
      margin: 0 0 4px;
      letter-spacing: -0.02em;
      line-height: 1.2;
    }}
    .hero-subtitle {{
      color: #93c5fd;
      font-size: 13px;
      margin-bottom: 20px;
      font-weight: 500;
    }}

    /* ── Stat chips inside hero ── */
    .hero-stat {{
      display: inline-block;
      background: rgba(255,255,255,0.1);
      border: 1px solid rgba(255,255,255,0.2);
      backdrop-filter: blur(4px);
      border-radius: 8px;
      padding: 8px 16px;
      margin: 0 10px 0 0;
      text-align: center;
    }}
    .hero-stat-num {{ color:#ffffff;font-size:20px;font-weight:800;display:block; }}
    .hero-stat-label {{ color:#93c5fd;font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:0.08em; }}

    /* ── Divider ── */
    .hero-divider {{
      border: none;
      border-top: 1px solid rgba(255,255,255,0.12);
      margin: 20px 0 16px;
    }}

    /* ── Responsive tweaks ── */
    @media (max-width: 600px) {{
      .wrapper {{ padding: 16px 10px 32px; }}
      .hero {{ padding: 22px 20px; border-radius: 12px; }}
      .hero-title {{ font-size: 20px; }}
      table.job-table td, table.job-table th {{ padding: 10px 10px !important; }}
      .col-why {{ display: none; }}
    }}
  </style>
</head>
<body>
<div class="wrapper">

  <!-- ═══════════════════════════════════════ HERO ═══ -->
  <div class="hero">
    <div class="hero-eyebrow">🤖 Job Search Alert</div>
    <h1 class="hero-title">{len(jobs)} New Software Engineer Jobs</h1>
    <div class="hero-subtitle">📅 {now}</div>

    <!-- stats row -->
    <table style="border-collapse:collapse;margin-bottom:18px;">
      <tr>
        <td style="padding:0 12px 0 0;">
          <div class="hero-stat">
            <span class="hero-stat-num">{len(jobs)}</span>
            <span class="hero-stat-label">Total jobs</span>
          </div>
        </td>
        <td style="padding:0 12px 0 0;">
          <div class="hero-stat">
            <span class="hero-stat-num">{total_fresh}</span>
            <span class="hero-stat-label">Fresh ⚡</span>
          </div>
        </td>
        <td style="padding:0;">
          <div class="hero-stat">
            <span class="hero-stat-num">{len(sorted_platforms)}</span>
            <span class="hero-stat-label">Platforms</span>
          </div>
        </td>
      </tr>
    </table>

    <hr class="hero-divider">

    <!-- platform pills -->
    <div>{pills}</div>
  </div>

  <!-- ═══════════════════════════════════════ LEGEND ═══ -->
  {legend}

  <!-- ═══════════════════════════════════════ PLATFORM SECTIONS ═══ -->
  {sections}

  <!-- ═══════════════════════════════════════ FOOTER ═══ -->
  <div style="text-align:center;padding:20px 16px 8px;
              color:#94a3b8;font-size:11px;line-height:1.8;">
    <div style="margin-bottom:6px;">
      <span style="font-weight:700;color:#64748b;">Job Search Bot</span>
      &nbsp;·&nbsp; Sorted newest first
      &nbsp;·&nbsp; Built for Raviteja Ramisetti
    </div>
    <div style="color:#cbd5e1;font-style:italic;">
      Apply while the listing is fresh — early applicants get noticed first 🚀
    </div>
  </div>

</div>
</body>
</html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f" {len(jobs)} New Software Engineer Jobs — {', '.join(p for p, _ in sorted_platforms)}"
    msg["From"]    = cfg["email_sender"]
    msg["To"]      = cfg["email_recipient"]
    msg.attach(MIMEText(html, "html"))

    try:
        # Switched to Port 587 (TLS) to prevent ISP blocking
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.login(cfg["email_sender"], cfg["email_password"])
            server.sendmail(cfg["email_sender"], cfg["email_recipient"], msg.as_string())
        summary = ", ".join(f"{p}:{len(j)}" for p, j in sorted_platforms)
        print(f"  ✉️  Email sent: {len(jobs)} jobs [{summary}] → {cfg['email_recipient']}")
    except Exception as e:
        print(f"  ⚠️  Email error: {e}")


def notify_all(jobs: list[dict]):
    if not jobs:
        print("  ℹ️  No new qualifying jobs to notify about.")
        return
    send_email_alert(jobs)