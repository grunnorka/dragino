#!/usr/bin/env python3
"""Probe Vodafone GDSP portal for VPN group navigation (read-mostly)."""
from __future__ import annotations

import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / "vodafone-gdsp.local.env"
OUT = ROOT / "logs" / "gdsp_vpn_probe"
OUT.mkdir(parents=True, exist_ok=True)


def load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def snap(page, name: str) -> None:
    p = OUT / f"{name}.png"
    page.screenshot(path=str(p), full_page=True)
    print(f"screenshot: {p}")


def main() -> int:
    env = load_env(ENV_FILE)
    user = env.get("GDSP_USER")
    password = env.get("GDSP_PASS")
    login_ids = ["arnor@grunnorka.is", f"{user}@ider.com" if user else None, user]
    login_ids = [x for x in login_ids if x]
    if not user or not password:
        print("Missing GDSP credentials", file=sys.stderr)
        return 1

    target_group = "VPN_162_Restricted_CAN_TBS_LPWA_UpTo_PP"
    test_imsi = "901280043992225"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1600, "height": 1000})
        page = context.new_page()
        page.set_default_timeout(45000)

        print("Opening portal...")
        page.goto("https://iotportal.vodafone.com/", wait_until="domcontentloaded")
        # Portal SPA can sit on "Just a moment..." for a while
        for i in range(12):
            title = page.title()
            body = page.locator("body").inner_text(timeout=5000)
            if "Just a moment" not in body and "being retrieved" not in body:
                break
            page.wait_for_timeout(5000)
        snap(page, "01_landing")

        logged_in = False
        for login_id in login_ids:
            page.goto("https://iotportal.vodafone.com/", wait_until="domcontentloaded")
            for _ in range(12):
                body = page.locator("body").inner_text(timeout=5000)
                if "Just a moment" not in body and "being retrieved" not in body:
                    break
                page.wait_for_timeout(3000)

            for cookie_sel in ["#onetrust-accept-btn-handler", "button:has-text('Accept All Cookies')"]:
                btn = page.locator(cookie_sel).first
                try:
                    if btn.is_visible(timeout=2000):
                        btn.click(force=True)
                        page.wait_for_timeout(1500)
                        break
                except Exception:
                    pass

            user_box = page.locator('input[placeholder="Username"], input[name="username"], input#username').first
            if user_box.count() == 0:
                continue
            user_box.fill(login_id)
            cont = page.get_by_role("button", name=re.compile("^Continue$", re.I))
            if cont.count():
                cont.first.click()
                page.wait_for_timeout(2500)

            body = page.locator("body").inner_text(timeout=5000)
            if "valid Email" in body or "Ooops" in body:
                print(f"login id rejected: {login_id}")
                continue

            pass_box = page.locator('input[type="password"], input[name="password"], input#password').first
            if pass_box.count() == 0:
                print(f"no password step for login id: {login_id}")
                continue

            pass_box.fill(password)
            snap(page, "03_password_filled")
            login_btn = page.get_by_role("button", name=re.compile("^Login$", re.I))
            if login_btn.count():
                login_btn.first.click()
            elif page.locator('button[type="submit"]').count():
                page.locator('button[type="submit"]').first.click()
            else:
                pass_box.press("Enter")

            try:
                page.wait_for_url(re.compile(r"iotportal\.vodafone\.com/(?!authenticationendpoint)"), timeout=90000)
            except PWTimeout:
                pass
            for _ in range(20):
                if "authenticationendpoint" not in page.url and "commonauth" not in page.url:
                    if any(x in page.locator("body").inner_text(timeout=5000) for x in ["Dashboard", "SIMs", "Total SIMs"]):
                        logged_in = True
                        break
                page.wait_for_timeout(3000)

            snap(page, "04_post_login")
            print(f"attempt login_id={login_id} url={page.url}")
            if logged_in:
                print(f"LOGIN_OK login_id={login_id}")
                break

        if not logged_in:
            print("Could not complete login")
            browser.close()
            return 2

        print("URL:", page.url)
        print("Title:", page.title())

        # Collect visible nav labels
        try:
            nav_parts = page.locator("nav, aside, [class*='sidebar'], [class*='menu']").all_text_contents()
            if nav_parts:
                nav_text = " ".join(nav_parts)
                print("nav snippet:", re.sub(r"\s+", " ", nav_text)[:500])
        except Exception as exc:
            print(f"nav read skipped: {exc}")

        # Try global search for VPN group
        for placeholder in ["Search", "search", "IMSI", "ICCID"]:
            loc = page.locator(f'input[placeholder*="{placeholder}"]')
            if loc.count():
                print(f"search box placeholder~{placeholder}")
                break

        # Click menu items that might lead to VPN groups
        candidates = [
            "Profiles & Tariffs",
            "Profiles and Tariffs",
            "Organisations",
            "Configuration",
            "Connectivity",
            "Administration",
        ]
        for label in candidates:
            loc = page.get_by_text(label, exact=False)
            if loc.count():
                print(f"menu candidate present: {label} ({loc.count()})")

        # Direct text search on page for target group
        if page.get_by_text(target_group, exact=False).count():
            print("Target VPN group text found on current page")
            page.get_by_text(target_group, exact=False).first.click()
            page.wait_for_timeout(2000)
            snap(page, "04_vpn_group_page")
        else:
            print("Target VPN group not on current page; exploring Organisations...")
            org = page.get_by_text("Organisations", exact=False)
            if org.count():
                org.first.click()
                page.wait_for_timeout(1500)
                snap(page, "05_organisations")
                ider = page.get_by_text("Ider", exact=False)
                if ider.count():
                    ider.first.click()
                    page.wait_for_timeout(1500)
                    snap(page, "06_ider_org")
                    for tab in ["Profiles and groups", "Profiles & groups", "Connectivity services", "Connectivity"]:
                        t = page.get_by_text(tab, exact=False)
                        if t.count():
                            print(f"found tab: {tab}")
                            t.first.click()
                            page.wait_for_timeout(1500)
                            snap(page, f"07_{tab.replace(' ', '_')}")
                            break

            if page.get_by_text(target_group, exact=False).count():
                page.get_by_text(target_group, exact=False).first.click()
                page.wait_for_timeout(2000)
                snap(page, "08_vpn_group_open")

        # Dump page text snippet for diagnostics
        body = re.sub(r"\s+", " ", page.locator("body").inner_text())[:3000]
        (OUT / "page_text.txt").write_text(body)
        print("body snippet:", body[:800])

        # If VPN group details visible, report members count
        m = re.search(r"Members\s*(\d+)", body, re.I)
        if m:
            print(f"MEMBERS_COUNT={m.group(1)}")
        ip = re.search(r"10\.208\.240\.0/20", body)
        if ip:
            print("IP_RANGE_VISIBLE=yes")

        # Try Add Members flow but stop before final confirm if Save/Continue appears
        add = page.get_by_text("Add Members", exact=False)
        if add.count():
            print("Found Add Members button")
            add.first.click()
            page.wait_for_timeout(1500)
            snap(page, "09_add_members_dialog")
            member = page.get_by_label("VPN Group Member", exact=False)
            if member.count() == 0:
                member = page.locator('input[placeholder*="Member"], input[name*="member" i]')
            if member.count():
                member.first.fill(test_imsi)
                search = page.get_by_role("button", name=re.compile("Search", re.I))
                if search.count():
                    search.first.click()
                    page.wait_for_timeout(2000)
                    snap(page, "10_after_imsi_search")
            body2 = re.sub(r"\s+", " ", page.locator("body").inner_text())[:2000]
            (OUT / "add_dialog_text.txt").write_text(body2)
            print("add dialog snippet:", body2[:600])

        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
