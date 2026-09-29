"""探针 3：登录后摸清 AppShell 的安全区元素与租户切换控件。

只读。目的：拿到可靠选择器，避免正式脚本瞎猜。
"""
import asyncio
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "backend"))

from cdp import Browser  # noqa: E402

ADMIN = "http://localhost:3002"


def creds() -> tuple[str, str]:
    """从仓库自带的种子数据取演示账号，避免在脚本里手写凭据。"""
    from app.seed.data import DEMO_USERS

    u = next(x for x in DEMO_USERS if x["username"] == "admin")
    return u["username"], u["password"]


FILL_JS = """(() => {
  const u = %s, p = %s;
  const set = (el, v) => {
    const d = Object.getOwnPropertyDescriptor(el.constructor.prototype, 'value').set;
    d.call(el, v); el.dispatchEvent(new Event('input', { bubbles: true }));
  };
  set(document.getElementById('login-username'), u);
  set(document.getElementById('login-password'), p);
  return true;
})()"""

CLICK_LOGIN_JS = """(() => {
  const b = [...document.querySelectorAll('button')].find(x => x.innerText.trim() === '登录');
  if (b) { b.click(); return true; } return false;
})()"""

INSPECT_JS = r"""(() => {
  const root = getComputedStyle(document.documentElement);
  const els = [...document.querySelectorAll('*')];
  const safeDriven = els.filter(el => {
    const cs = getComputedStyle(el);
    return ['paddingTop','paddingBottom','paddingLeft','paddingRight']
      .some(p => ['47px','34px'].includes(cs[p]));
  }).slice(0, 8).map(el => ({
    tag: el.tagName,
    cls: (el.className||'').toString().slice(0,110),
    pt: getComputedStyle(el).paddingTop,
    pb: getComputedStyle(el).paddingBottom
  }));
  return {
    url: location.href,
    vars: {
      safeTop: root.getPropertyValue('--safe-top').trim(),
      safeBottom: root.getPropertyValue('--safe-bottom').trim(),
      topbarTotal: root.getPropertyValue('--topbar-total').trim(),
      tabbarH: root.getPropertyValue('--tabbar-h').trim()
    },
    safeDriven,
    selects: [...document.querySelectorAll('select')].map(s => ({
      id: s.id, cls: (s.className||'').slice(0,80),
      opts: [...s.options].map(o => o.value + '|' + o.text).slice(0,8)
    })),
    buttons: [...document.querySelectorAll('button')].map(x=>(x.innerText||'').trim()).filter(Boolean).slice(0,25),
    navLinks: [...document.querySelectorAll('a')].map(a=>a.getAttribute('href')).filter(Boolean).slice(0,20),
    text: (document.body.innerText||'').replace(/\s+/g,' ').slice(0,300)
  };
})()"""


async def main() -> int:
    user, pwd = creds()
    async with Browser(headless=True, width=390, height=844) as b:
        await b.apply_device(safe_area={"top": 47, "bottom": 34})
        await b.goto(f"{ADMIN}/login", wait=2.5)

        await b.cdp.evaluate(FILL_JS % (json.dumps(user), json.dumps(pwd)))
        await asyncio.sleep(0.4)
        await b.cdp.evaluate(CLICK_LOGIN_JS)
        await asyncio.sleep(5)
        print("登录后 URL:", await b.cdp.evaluate("location.href"))

        info = await b.cdp.evaluate(INSPECT_JS)
        print(json.dumps(info, ensure_ascii=False, indent=2))

        await b.screenshot(pathlib.Path(__file__).parent / "shot_dashboard.png")
        print("截图已存 evidence/shot_dashboard.png")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
