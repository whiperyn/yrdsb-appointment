import asyncio, os, random, time, ssl, smtplib, sys
from datetime import date, timedelta
from urllib.parse import urlencode
from email.mime.text import MIMEText
from email.utils import formatdate
from dotenv import load_dotenv
from playwright.async_api import async_playwright

# ----- logging / stdout -----
# Make prints show up immediately on Railway/containers
sys.stdout.reconfigure(line_buffering=True)

load_dotenv()

# ----- config from env -----
BASE         = (os.getenv("BASE") or "https://ta.yrdsb.ca").rstrip("/")
USER         = os.getenv("USER_ID")
PWD          = os.getenv("USER_PASSWORD")
SCHOOL_ID    = os.getenv("SCHOOL_ID")
STUDENT_ID   = os.getenv("STUDENT_ID")

START_DATE   = os.getenv("START_DATE")
END_DATE     = os.getenv("END_DATE")

EMAIL_TO     = os.getenv("ALERT_EMAIL_TO")
EMAIL_FROM   = os.getenv("ALERT_EMAIL_FROM")
SMTP_HOST    = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT    = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER    = os.getenv("SMTP_USER")
SMTP_PASS    = os.getenv("SMTP_PASS")

MIN_SEC      = int(os.getenv("CHECK_MIN_SEC", "60"))
MAX_SEC      = int(os.getenv("CHECK_MAX_SEC", "180"))

STATE_PATH   = "ta_state.json"  # persists cookies/session within the container

# A stable page we know exists; if not logged in, site will redirect to login
LANDING_URL  = f"{BASE}/live/students/listReports.php?student_id={STUDENT_ID}"

# ----- helpers -----
def _daterange(start_ymd: str, end_ymd: str):
    y1, m1, d1 = map(int, start_ymd.split("-"))
    y2, m2, d2 = map(int, end_ymd.split("-"))
    cur = date(y1, m1, d1)
    end = date(y2, m2, d2)
    while cur <= end:
        yield cur.isoformat()
        cur += timedelta(days=1)

def send_email(subject: str, body: str):
    if not all([EMAIL_TO, EMAIL_FROM, SMTP_HOST, SMTP_USER, SMTP_PASS]):
        print("Email not configured; skipping send.", flush=True)
        return
    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg["Date"] = formatdate(localtime=True)
    msg["Subject"] = subject
    ctx = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.starttls(context=ctx)
        s.login(SMTP_USER, SMTP_PASS)
        s.sendmail(EMAIL_FROM, [EMAIL_TO], msg.as_string())
        print("Email Sent", flush=True)

async def safe_goto(page, url, *, tries=3, timeout_ms=60000):
    """Goto with retries and longer timeout for headless hosts."""
    for i in range(tries):
        try:
            print(f"[goto] {url} (try {i+1}/{tries})", flush=True)
            return await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception as e:
            print(f"[goto retry {i+1}] error: {e}", flush=True)
            if i == tries - 1:
                raise
            await asyncio.sleep(3 + 2*i)

# ----- main page flows -----
async def login(page):
    """Open landing page; if login form is present, submit creds."""
    await safe_goto(page, LANDING_URL)
    login_field = page.locator('input[name="student_number"], input[name="username"]')
    if await login_field.count() > 0:
        field_sel = 'input[name="student_number"]' if await page.locator('input[name="student_number"]').count() else 'input[name="username"]'
        await page.fill(field_sel, USER or "")
        await page.fill('input[type="password"], input[name="password"]', PWD or "")
        # Click submit
        if await page.get_by_role("button", name="Login").count():
            await page.get_by_role("button", name="Login").click()
        else:
            await page.click('button[type="submit"], input[type="submit"]')
        await page.wait_for_load_state("networkidle")
        print("[login] submitted; waiting for dashboard…", flush=True)
    else:
        print("[login] session likely active (no login form).", flush=True)

async def check_one_date(page, ymd: str) -> bool:
    """Return True if there appears to be any appointment on this date."""
    qs = urlencode({"school_id": SCHOOL_ID, "student_id": STUDENT_ID, "inputDate": ymd})
    url = f"{BASE}/live/students/bookAppointment.php?{qs}"
    await safe_goto(page, url)

    print(f"DEBUG checking {ymd}", flush=True)
    print("URL:", page.url, flush=True)
    print("Title:", await page.title(), flush=True)

    body_text = await page.locator("body").inner_text()
    print(body_text, flush=True)

    text_lc = " ".join(body_text.split()).lower()
    if "not a school day" in text_lc:
        print(f"  {ymd}: weekend/holiday → available=False", flush=True)
        return False

    blue   = page.locator("div.box.blue")
    yellow = page.locator("div.box.yellow")

    async def box_available(box):
        """True if this appointment box has anything to book."""
        if await box.count() == 0:
            return False
        txt = " ".join((await box.inner_text()).split()).lower()
        if "none available" in txt:
            return False
        # Look for a real control INSIDE the box (button/link/input submit)
        has_btn = await box.locator('button, a, input[type="submit"], input[type="button"]').count() > 0
        return has_btn

    blue_avail   = await box_available(blue)
    yellow_avail = await box_available(yellow)

    available = blue_avail or yellow_avail
    print(f"  {ymd}: available={available}  (blue={blue_avail}, yellow={yellow_avail})", flush=True)
    return available

async def run_once():
    dates = list(_daterange(START_DATE, END_DATE))

    async with async_playwright() as pw:
        # Hardened launch for headless hosts
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )
        context = await browser.new_context(
            storage_state=STATE_PATH if os.path.exists(STATE_PATH) else None,
            user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"),
            locale="en-CA",
            timezone_id="America/Toronto",
            ignore_https_errors=True,
        )
        page = await context.new_page()

        await login(page)
        await context.storage_state(path=STATE_PATH)

        any_available = False
        for ymd in dates:
            try:
                if await check_one_date(page, ymd):
                    any_available = True
            except Exception as e:
                print(f"Error checking {ymd}: {e}", flush=True)

        await browser.close()
        return any_available

async def main_loop():
    print("TeachAssist watcher running (with jitter).", flush=True)
    while True:
        try:
            ok = await run_once()
            ts = time.strftime('%Y-%m-%d %H:%M:%S')
            print(f"[{ts}] any_available={ok}", flush=True)
            if ok:
                send_email(
                    subject="[TeachAssist] Appointment slot detected!",
                    body=(
                        "At least one date in your range shows potential availability.\n"
                        f"Open TeachAssist and book ASAP.\n\n"
                        f"Window: {START_DATE} → {END_DATE}\n"
                        f"Direct sample URL: {BASE}/live/students/bookAppointment.php"
                        f"?school_id={SCHOOL_ID}&student_id={STUDENT_ID}&inputDate={START_DATE}\n"
                        "(Automated alert)"
                    )
                )
                time.sleep(300)  # cool-off to avoid spamming if slot persists
            else:
                print("[heartbeat] sleeping…", flush=True)
                time.sleep(random.randint(MIN_SEC, MAX_SEC))
        except Exception as e:
            print("Loop error:", e, flush=True)
            time.sleep(120)

if __name__ == "__main__":
    asyncio.run(main_loop())
