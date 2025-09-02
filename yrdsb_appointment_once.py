import asyncio, os, ssl, smtplib
from datetime import date, timedelta
from urllib.parse import urlencode
from email.mime.text import MIMEText
from email.utils import formatdate
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()

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

def _dates(a,b):
    y1,m1,d1 = map(int,a.split("-")); y2,m2,d2 = map(int,b.split("-"))
    cur = date(y1,m1,d1); end = date(y2,m2,d2)
    while cur <= end:
        yield cur.isoformat(); cur = cur.fromordinal(cur.toordinal()+1)

def send_email(sub, body):
    if not all([EMAIL_TO, EMAIL_FROM, SMTP_USER, SMTP_PASS]): return
    msg = MIMEText(body, "plain", "utf-8")
    msg["From"]=EMAIL_FROM; msg["To"]=EMAIL_TO; msg["Date"]=formatdate(localtime=True); msg["Subject"]=sub
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.starttls(context=ssl.create_default_context())
        s.login(SMTP_USER, SMTP_PASS)
        s.sendmail(EMAIL_FROM, [EMAIL_TO], msg.as_string())

async def safe_goto(page, url):
    return await page.goto(url, wait_until="domcontentloaded", timeout=60000)

async def login(page):
    landing = f"{BASE}/live/students/listReports.php?student_id={STUDENT_ID}"
    await safe_goto(page, landing)
    lf = page.locator('input[name="student_number"], input[name="username"]')
    if await lf.count() > 0:
        field = 'input[name="student_number"]' if await page.locator('input[name="student_number"]').count() else 'input[name="username"]'
        await page.fill(field, USER or ""); await page.fill('input[type="password"], input[name="password"]', PWD or "")
        if await page.get_by_role("button", name="Login").count():
            await page.get_by_role("button", name="Login").click()
        else:
            await page.click('button[type="submit"], input[type="submit"]')
        await page.wait_for_load_state("networkidle")

async def check_date(page, ymd):
    qs = urlencode({"school_id":SCHOOL_ID,"student_id":STUDENT_ID,"inputDate":ymd})
    await safe_goto(page, f"{BASE}/live/students/bookAppointment.php?{qs}")
    text = " ".join((await page.locator("body").inner_text()).split()).lower()
    if "not a school day" in text: return False
    async def box_ok(sel):
        box = page.locator(sel)
        if await box.count()==0: return False
        t = " ".join((await box.inner_text()).split()).lower()
        if "none available" in t: return False
        return (await box.locator('button, a, input[type="submit"], input[type="button"]').count())>0
    return await box_ok("div.box.blue") or await box_ok("div.box.yellow")

async def main():
    any_avail = False
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox","--disable-setuid-sandbox","--disable-dev-shm-usage","--disable-gpu"])
        context = await browser.new_context(ignore_https_errors=True, locale="en-CA", timezone_id="America/Toronto")
        page = await context.new_page()
        await login(page)
        for d in _dates(START_DATE, END_DATE):
            try:
                if await check_date(page, d):
                    any_avail = True
            except Exception as e:
                print("check error", d, e)
        await browser.close()

    if any_avail:
        send_email("[TeachAssist] Appointment slot detected!",
                   f"At least one date shows availability.\nRange {START_DATE} → {END_DATE}\nLogin: {BASE}")
    print("DONE any_available=", any_avail)

if __name__=="__main__":
    import asyncio; asyncio.run(main())
