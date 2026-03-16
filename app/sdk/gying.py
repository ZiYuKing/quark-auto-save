import json
import re
from urllib.parse import quote, urlparse

import requests


class Gying:
    """GYING 搜索客户端（requests 版本）。"""

    def __init__(self, server: str, username: str = "", password: str = "", cookie: str = ""):
        raw_server = (server or "").strip().rstrip("/")
        parsed = urlparse(raw_server if "://" in raw_server else f"https://{raw_server}")
        self.server = f"{parsed.scheme}://{parsed.netloc}".rstrip("/") if parsed.netloc else raw_server
        self.username = (username or "").strip()
        self.password = (password or "").strip()
        self.cookie = (cookie or "").strip()

        self.login_url = f"{self.server}/user/login"
        self.login_host = urlparse(self.login_url).netloc
        self.search_host = urlparse(self.server).netloc

        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "Apifox/1.0.0 (https://apifox.com)",
                "Accept": "*/*",
                "Connection": "keep-alive",
            }
        )

    @staticmethod
    def _is_login_page(text: str) -> bool:
        if not text:
            return False
        if "_BT.PC.HTML('search')" in text or '_BT.PC.HTML("search")' in text:
            return False
        markers = [
            "<title>Loading...</title>",
            "_BT.PC.HTML('login')",
            "_BT.PC.HTML(\"login\")",
            "login-6aad149.js",
        ]
        return any(marker in text for marker in markers)

    @staticmethod
    def _extract_cookie_value(cookie_text: str, key: str) -> str:
        if not cookie_text:
            return ""
        m = re.search(rf"{re.escape(key)}=([^;]+)", cookie_text)
        return m.group(1).strip() if m else ""

    def _build_cookie_from_login(self) -> str:
        headers = {
            "priority": "u=1, i",
            "User-Agent": "Apifox/1.0.0 (https://apifox.com)",
            "content-type": "application/x-www-form-urlencoded",
            "Accept": "*/*",
            "Host": self.login_host,
            "Connection": "keep-alive",
        }
        payload = {
            "code": "",
            "siteid": "1",
            "dosubmit": "1",
            "cookietime": "10506240",
            "username": self.username,
            "password": self.password,
        }
        resp = self.session.post(
            self.login_url,
            headers=headers,
            data=payload,
            timeout=30,
            allow_redirects=True,
        )

        app_auth = resp.cookies.get("app_auth") or self.session.cookies.get("app_auth") or ""
        phpsessid = resp.cookies.get("PHPSESSID") or self.session.cookies.get("PHPSESSID") or ""
        set_cookie = resp.headers.get("Set-Cookie", "")
        if not app_auth:
            app_auth = self._extract_cookie_value(set_cookie, "app_auth")
        if not phpsessid:
            phpsessid = self._extract_cookie_value(set_cookie, "PHPSESSID")

        if app_auth and phpsessid:
            return f"app_auth={app_auth};PHPSESSID={phpsessid};"
        if app_auth:
            return f"app_auth={app_auth};"
        return ""

    def _get_cookie(self) -> str:
        if self.cookie:
            return self.cookie
        if self.username and self.password:
            self.cookie = self._build_cookie_from_login()
            return self.cookie
        return ""

    def search(self, keyword: str) -> list:
        if not self.server or not keyword:
            return []

        cookie = self._get_cookie()
        if not cookie:
            print("[GYING] 缺少 Cookie，无法搜索")
            return []

        search_url = f"{self.server}/s/1---1/{quote(keyword)}"
        headers = {
            "Cookie": cookie,
            "User-Agent": "Apifox/1.0.0 (https://apifox.com)",
            "Accept": "*/*",
            "Host": self.search_host,
            "Connection": "keep-alive",
        }
        try:
            resp = self.session.get(search_url, headers=headers, timeout=30, allow_redirects=True)
            text = resp.text or ""
            if self._is_login_page(text):
                print("[GYING] app_auth 已携带，但仍返回登录页（cookie 可能失效/被风控）")
                return []

            payload = self._extract_payload(text)
            results = self._format_results(payload)
            if results:
                return results
            return self._extract_from_html_cards(text)
        except Exception as e:
            print(f"[GYING] 搜索异常: {e}")
            return []

    def second_layer(self, first_layer_url: str) -> list:
        """根据一层链接（如 /ac/P8Pr）获取网盘列表。"""
        if not first_layer_url:
            return []

        cookie = self._get_cookie()
        if not cookie:
            print("[GYING] 请求缺少 Cookie")
            return []

        try:
            parsed = urlparse(first_layer_url)
            path = parsed.path or ""
            m = re.search(r"/([A-Za-z0-9_]+)/([A-Za-z0-9_]+)$", path)
            if not m:
                return []

            dir_name = m.group(1)
            item_id = m.group(2)
            api_url = f"{self.server}/res/downurl/{dir_name}/{item_id}"
            headers = {
                "Cookie": cookie,
                "User-Agent": "Apifox/1.0.0 (https://apifox.com)",
                "Accept": "*/*",
                "Host": self.search_host,
                "Connection": "keep-alive",
            }
            resp = self.session.get(api_url, headers=headers, timeout=30, allow_redirects=True)
            data = resp.json() if resp.text else {}
            if not isinstance(data, dict) or data.get("code") != 200:
                return []
            return self._parse_downurl_response(data)
        except Exception as e:
            print(f"[GYING] 请求异常: {e}")
            return []

    @staticmethod
    def _extract_payload(text: str) -> dict:
        if not text:
            return {}

        script_match = re.search(
            r"_obj\.search\s*=\s*(\{.*?\})\s*;\s*_obj\.(?:page|footer)\s*=",
            text,
            re.S,
        )
        if script_match:
            try:
                data = json.loads(script_match.group(1))
                if isinstance(data, dict) and "title" in data:
                    return data
            except Exception:
                pass

        try:
            data = json.loads(text)
            if isinstance(data, dict) and "title" in data:
                return data
        except Exception:
            pass

        keys = ["title", "name", "ename", "year", "d", "i"]
        data = {}
        for key in keys:
            match = re.search(rf'"{key}"\s*:\s*(\[[^\]]*\])', text, re.S)
            if not match:
                continue
            try:
                data[key] = json.loads(match.group(1))
            except Exception:
                data[key] = []
        return data

    def _format_results(self, data: dict) -> list:
        titles = data.get("title") or []
        names = data.get("name") or []
        enames = data.get("ename") or []
        years = data.get("year") or []
        ds = data.get("d") or []
        ids = data.get("i") or []

        results = []
        seen = set()
        for idx, title in enumerate(titles):
            d_value = str(ds[idx]).strip() if idx < len(ds) else ""
            i_value = str(ids[idx]).strip() if idx < len(ids) else ""
            if not d_value or not i_value:
                continue

            shareurl = f"{self.server}/{d_value}/{i_value}"
            if shareurl in seen:
                continue
            seen.add(shareurl)

            title_text = str(title or "").strip()
            name_text = str(names[idx]).strip() if idx < len(names) else ""
            year_text = str(years[idx]).strip() if idx < len(years) else ""
            ename_text = str(enames[idx]).strip() if idx < len(enames) else ""

            taskname = title_text
            if name_text:
                taskname = f"{taskname} {name_text}".strip()
            if year_text:
                taskname = f"{taskname} ({year_text})"

            results.append(
                {
                    "shareurl": shareurl,
                    "taskname": taskname,
                    "content": self._format_alias_text(ename_text),
                    "datetime": "",
                    "channel": d_value,
                    "source": "GYING",
                }
            )
        return results

    @staticmethod
    def _strip_tags(text: str) -> str:
        if not text:
            return ""
        return re.sub(r"<[^>]+>", "", text).strip()

    @staticmethod
    def _format_alias_text(alias: str) -> str:
        alias = (alias or "").strip()
        if not alias:
            return ""
        if alias.startswith("又名：") or alias.startswith("又名:"):
            return alias
        return f"又名：{alias}"

    def _extract_from_html_cards(self, html: str) -> list:
        if not html:
            return []

        results = []
        seen = set()
        pattern = re.compile(
            r'<div class="v5d">.*?<b><a href="(/([A-Za-z0-9_]+)/([A-Za-z0-9_]+))"[^>]*>(.*?)</a></b>(.*?)</div>\s*</div>',
            re.S,
        )
        for m in pattern.finditer(html):
            rel = m.group(1)
            d_value = m.group(2)
            shareurl = f"{self.server}{rel}"
            if shareurl in seen:
                continue
            seen.add(shareurl)

            raw_title = self._strip_tags(m.group(4))
            tail = m.group(5) or ""
            year = ""
            ym = re.search(r"\((\d{4})\)", raw_title)
            if ym:
                year = ym.group(1)
                raw_title = re.sub(r"\s*\(\d{4}\)\s*", " ", raw_title).strip()

            alias = ""
            am = re.search(r"<p>又名[:：]\s*(.*?)</p>", tail, re.S)
            if am:
                alias = self._strip_tags(am.group(1))

            taskname = raw_title
            if year:
                taskname = f"{taskname} ({year})"

            results.append(
                {
                    "shareurl": shareurl,
                    "taskname": taskname,
                    "content": self._format_alias_text(alias),
                    "datetime": "",
                    "channel": d_value,
                    "source": "GYING",
                }
            )
        return results

    def _parse_downurl_response(self, data: dict) -> list:
        """解析 /res/downurl 返回，并只保留夸克网盘。"""
        panlist = data.get("panlist") or {}
        if not isinstance(panlist, dict):
            return []

        ids = panlist.get("id") or []
        names = panlist.get("name") or []
        codes = panlist.get("p") or []
        urls = panlist.get("url") or []
        types = panlist.get("type") or []
        users = panlist.get("user") or []
        times = panlist.get("time") or []
        tnames = panlist.get("tname") or []

        results = []
        seen = set()
        for idx in range(len(urls)):
            shareurl = str(urls[idx]).strip()
            if not shareurl or shareurl in seen:
                continue

            raw_type = types[idx] if idx < len(types) else ""
            channel = str(raw_type)
            if isinstance(raw_type, int) and 0 <= raw_type < len(tnames):
                channel = str(tnames[raw_type]).strip()
            elif str(raw_type).isdigit():
                t_idx = int(str(raw_type))
                if 0 <= t_idx < len(tnames):
                    channel = str(tnames[t_idx]).strip()

            is_quark = ("夸克" in channel) or ("pan.quark.cn" in shareurl)
            if not is_quark:
                continue

            seen.add(shareurl)
            taskname = str(names[idx]).strip() if idx < len(names) else ""
            extract_code = str(codes[idx]).strip() if idx < len(codes) else ""
            user = str(users[idx]).strip() if idx < len(users) else ""
            tm = str(times[idx]).strip() if idx < len(times) else ""

            content_parts = []
            if extract_code and extract_code.lower() != "none":
                content_parts.append(f"提取码：{extract_code}")
            if user:
                content_parts.append(f"发布者：{user}")
            if tm:
                content_parts.append(f"时间：{tm}")

            results.append(
                {
                    "id": str(ids[idx]).strip() if idx < len(ids) else "",
                    "taskname": taskname,
                    "shareurl": shareurl,
                    "channel": "夸克网盘",
                    "content": " / ".join(content_parts),
                    "source": "GYING",
                    "datetime": "",
                }
            )
        return results
