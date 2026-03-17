# !/usr/bin/env python3
# -*- coding: utf-8 -*-
from flask import (
    json,
    Flask,
    url_for,
    session,
    jsonify,
    request,
    redirect,
    Response,
    render_template,
    send_from_directory,
    stream_with_context,
)
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from concurrent.futures import ThreadPoolExecutor, as_completed
from sdk.cloudsaver import CloudSaver
from sdk.pansou import PanSou
from sdk.gying import Gying
from datetime import timedelta
import subprocess
import requests
import hashlib
import logging
import traceback
import base64
import sys
import os
import re
from natsort import natsorted

parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, parent_dir)
from quark_auto_save import Quark, Config, MagicRename

KNOWN_MEDIA_EXTS = {
    ".mp4",
    ".mkv",
    ".avi",
    ".mov",
    ".wmv",
    ".flv",
    ".m4v",
    ".ts",
    ".mpg",
    ".mpeg",
    ".rmvb",
    ".srt",
    ".ass",
    ".ssa",
    ".vtt",
}


def strip_known_ext(name):
    text = str(name or "").strip()
    if not text:
        return ""
    root, ext = os.path.splitext(text)
    if ext.lower() in KNOWN_MEDIA_EXTS:
        return root
    return text

print(
    r"""
   ____    ___   _____
  / __ \  /   | / ___/
 / / / / / /| | \__ \
/ /_/ / / ___ |___/ /
\___\_\/_/  |_/____/

-- Quark-Auto-Save --
 """
)
sys.stdout.flush()


def get_app_ver():
    """获取应用版本"""
    try:
        with open("build.json", "r") as f:
            build_info = json.loads(f.read())
            BUILD_SHA = build_info["BUILD_SHA"]
            BUILD_TAG = build_info["BUILD_TAG"]
    except Exception as e:
        BUILD_SHA = os.getenv("BUILD_SHA", "")
        BUILD_TAG = os.getenv("BUILD_TAG", "")
    if BUILD_TAG[:1] == "v":
        return BUILD_TAG
    elif BUILD_SHA:
        return f"{BUILD_TAG}({BUILD_SHA[:7]})"
    else:
        return "dev"


# 文件路径
PYTHON_PATH = "python3" if os.path.exists("/usr/bin/python3") else "python"
SCRIPT_PATH = os.environ.get("SCRIPT_PATH", "./quark_auto_save.py")
CONFIG_PATH = os.environ.get("CONFIG_PATH", "./config/quark_config.json")
PLUGIN_FLAGS = os.environ.get("PLUGIN_FLAGS", "")
DEBUG = os.environ.get("DEBUG", "false").lower() == "true"
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = os.environ.get("PORT", 5005)
TASK_TIMEOUT = int(os.environ.get("TASK_TIMEOUT", 1800))

config_data = {}
task_plugins_config_default = {}

app = Flask(__name__)
app.config["APP_VERSION"] = get_app_ver()
app.secret_key = "ca943f6db6dd34823d36ab08d8d6f65d"
app.config["SESSION_COOKIE_NAME"] = "QUARK_AUTO_SAVE_SESSION"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=31)
app.json.ensure_ascii = False
app.json.sort_keys = False
app.jinja_env.variable_start_string = "[["
app.jinja_env.variable_end_string = "]]"

scheduler = BackgroundScheduler()
logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="[%(asctime)s][%(levelname)s] %(message)s",
    datefmt="%m-%d %H:%M:%S",
)
# 过滤werkzeug日志输出
if not DEBUG:
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    logging.getLogger("apscheduler").setLevel(logging.ERROR)
    sys.modules["flask.cli"].show_server_banner = lambda *x: None


def gen_md5(string):
    md5 = hashlib.md5()
    md5.update(string.encode("utf-8"))
    return md5.hexdigest()


def get_login_token():
    username = config_data["webui"]["username"]
    password = config_data["webui"]["password"]
    return gen_md5(f"token{username}{password}+-*/")[8:24]


def is_login():
    login_token = get_login_token()
    if session.get("token") == login_token or request.args.get("token") == login_token:
        return True
    else:
        return False


# 设置icon
@app.route("/favicon.ico")
def favicon():
    return send_from_directory(
        os.path.join(app.root_path, "static"),
        "favicon.ico",
        mimetype="image/vnd.microsoft.icon",
    )


@app.route("/manifest.webmanifest")
def pwa_manifest():
    return send_from_directory(
        os.path.join(app.root_path, "static"),
        "manifest.webmanifest",
        mimetype="application/manifest+json",
    )


@app.route("/sw.js")
def pwa_sw():
    return send_from_directory(
        os.path.join(app.root_path, "static"),
        "sw.js",
        mimetype="application/javascript",
    )


# 登录页面
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = config_data["webui"]["username"]
        password = config_data["webui"]["password"]
        # 验证用户名和密码
        if (username == request.form.get("username")) and (
            password == request.form.get("password")
        ):
            logging.info(f">>> 用户 {username} 登录成功")
            session.permanent = True
            session["token"] = get_login_token()
            return redirect(url_for("index"))
        else:
            logging.info(f">>> 用户 {username} 登录失败")
            return render_template("login.html", message="登录失败")

    if is_login():
        return redirect(url_for("index"))
    return render_template("login.html", error=None)


# 退出登录
@app.route("/logout")
def logout():
    session.pop("token", None)
    return redirect(url_for("login"))


# 管理页面
@app.route("/")
def index():
    if not is_login():
        return redirect(url_for("login"))
    return render_template(
        "index.html", version=app.config["APP_VERSION"], plugin_flags=PLUGIN_FLAGS
    )


# 获取配置数据
@app.route("/data")
def get_data():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})
    data = Config.read_json(CONFIG_PATH)
    del data["webui"]
    data["api_token"] = get_login_token()
    data["task_plugins_config_default"] = task_plugins_config_default
    return jsonify({"success": True, "data": data})


# 更新数据
@app.route("/update", methods=["POST"])
def update():
    global config_data
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})
    dont_save_keys = ["task_plugins_config_default", "api_token"]
    for key, value in request.json.items():
        if key not in dont_save_keys:
            config_data.update({key: value})
    Config.write_json(CONFIG_PATH, config_data)
    # 重新加载任务
    if reload_tasks():
        logging.info(f">>> 配置更新成功")
        return jsonify({"success": True, "message": "配置更新成功"})
    else:
        logging.info(f">>> 配置更新失败")
        return jsonify({"success": False, "message": "配置更新失败"})


# 处理运行脚本请求
@app.route("/run_script_now", methods=["POST"])
def run_script_now():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})
    tasklist = request.json.get("tasklist", [])
    command = [PYTHON_PATH, "-u", SCRIPT_PATH, CONFIG_PATH]
    logging.info(
        f">>> 手动运行任务 [{tasklist[0].get('taskname') if len(tasklist)>0 else 'ALL'}] 开始执行..."
    )

    def generate_output():
        # 设置环境变量
        process_env = os.environ.copy()
        process_env["PYTHONIOENCODING"] = "utf-8"
        if request.json.get("quark_test"):
            process_env["QUARK_TEST"] = "true"
            process_env["COOKIE"] = json.dumps(
                request.json.get("cookie", []), ensure_ascii=False
            )
            process_env["PUSH_CONFIG"] = json.dumps(
                request.json.get("push_config", {}), ensure_ascii=False
            )
        if tasklist:
            process_env["TASKLIST"] = json.dumps(tasklist, ensure_ascii=False)
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=process_env,
        )
        try:
            for line in iter(process.stdout.readline, ""):
                logging.info(line.strip())
                yield f"data: {line}\n\n"
            yield "data: [DONE]\n\n"
        finally:
            process.stdout.close()
            process.wait()

    return Response(
        stream_with_context(generate_output()),
        content_type="text/event-stream;charset=utf-8",
    )


@app.route("/task_suggestions")
def get_task_suggestions():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})
    query = request.args.get("q", "").lower()
    deep = request.args.get("d", "").lower()
    net_data = config_data.get("source", {}).get("net", {})
    cs_data = config_data.get("source", {}).get("cloudsaver", {})
    ps_data = config_data.get("source", {}).get("pansou", {})

    def net_search():
        if str(net_data.get("enable", "true")).lower() != "false":
            base_url = base64.b64decode("aHR0cHM6Ly9zLjkxNzc4OC54eXo=").decode()
            url = f"{base_url}/task_suggestions?q={query}&d={deep}"
            response = requests.get(url)
            return response.json()
        return []

    def cs_search():
        if (
            cs_data.get("server")
            and cs_data.get("username")
            and cs_data.get("password")
        ):
            cs = CloudSaver(cs_data.get("server"))
            cs.set_auth(
                cs_data.get("username", ""),
                cs_data.get("password", ""),
                cs_data.get("token", ""),
            )
            search = cs.auto_login_search(query)
            if search.get("success"):
                if search.get("new_token"):
                    cs_data["token"] = search.get("new_token")
                    Config.write_json(CONFIG_PATH, config_data)
                search_results = cs.clean_search_results(search.get("data"))
                return search_results
        return []

    def ps_search():
        if ps_data.get("server"):
            ps = PanSou(ps_data.get("server"))
            return ps.search(query, deep == "1")
        return []

    try:
        search_results = []
        with ThreadPoolExecutor(max_workers=3) as executor:
            features = []
            features.append(executor.submit(net_search))
            features.append(executor.submit(cs_search))
            features.append(executor.submit(ps_search))
            for future in as_completed(features):
                result = future.result()
                search_results.extend(result)

        # 按时间排序并去重
        results = []
        link_array = []
        search_results.sort(key=lambda x: x.get("datetime", ""), reverse=True)
        for item in search_results:
            url = item.get("shareurl", "")
            if url != "" and url not in link_array:
                link_array.append(url)
                results.append(item)

        return jsonify({"success": True, "data": results})
    except Exception as e:
        return jsonify({"success": True, "message": f"error: {str(e)}"})


@app.route("/resource_search")
def resource_search():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})
    query = request.args.get("q", "").strip().lower()
    if len(query) < 2:
        return jsonify({"success": True, "data": []})

    cs_data = config_data.get("source", {}).get("cloudsaver", {})
    ps_data = config_data.get("source", {}).get("pansou", {})
    gy_data = config_data.get("source", {}).get("gying", {})
    cs_debug_stats = {"raw_count": 0, "quark_filtered_count": 0}

    def cs_search():
        if (
            cs_data.get("server")
            and cs_data.get("username")
            and cs_data.get("password")
        ):
            cs = CloudSaver(cs_data.get("server"))
            cs.set_auth(
                cs_data.get("username", ""),
                cs_data.get("password", ""),
                cs_data.get("token", ""),
            )
            search = cs.auto_login_search(query)
            if search.get("success"):
                if search.get("new_token"):
                    cs_data["token"] = search.get("new_token")
                    Config.write_json(CONFIG_PATH, config_data)
                raw_data = search.get("data") or []
                raw_count = sum(
                    len(channel.get("list", []))
                    for channel in raw_data
                    if isinstance(channel, dict)
                )
                clean_results = cs.clean_search_results(raw_data)
                cs_debug_stats["raw_count"] = raw_count
                cs_debug_stats["quark_filtered_count"] = len(clean_results)
                return clean_results
        return []

    def ps_search():
        if ps_data.get("server"):
            ps = PanSou(ps_data.get("server"))
            return ps.search(query, True)
        return []

    def gy_search():
        has_url = bool(gy_data.get("url"))
        has_login = bool(gy_data.get("username") and gy_data.get("password"))
        has_cookie = bool(gy_data.get("cookie"))
        if has_url and (has_login or has_cookie):
            gy = Gying(
                gy_data.get("url", ""),
                gy_data.get("username", ""),
                gy_data.get("password", ""),
                gy_data.get("cookie", ""),
            )
            return gy.search(query)
        return []

    try:
        search_results = []
        gy_results = []
        with ThreadPoolExecutor(max_workers=3) as executor:
            features = {
                executor.submit(cs_search): "cs",
                executor.submit(ps_search): "ps",
                executor.submit(gy_search): "gy",
            }
            for future in as_completed(features):
                result = future.result()
                if features[future] == "gy":
                    gy_results.extend(result)
                else:
                    search_results.extend(result)

        search_results.sort(key=lambda x: x.get("datetime", ""), reverse=True)
        results = []
        link_array = []
        for item in search_results:
            url = item.get("shareurl", "")
            if url and url not in link_array:
                link_array.append(url)
                results.append(item)
        cs_in_dedup_count = sum(
            1 for item in results if item.get("source") == "CloudSaver"
        )
        sys.stdout.flush()
        # 向后兼容前端：data 仍返回数组，额外携带 gying 结果供后续升级前端使用
        return jsonify({"success": True, "data": results, "gying": gy_results})
    except Exception as e:
        return jsonify({"success": False, "message": str(e), "data": [], "gying": []})


@app.route("/gying_second_layer", methods=["POST"])
def gying_second_layer():
    try:
        payload = request.get_json(silent=True) or {}
        if not payload and request.form:
            payload = request.form.to_dict()
        shareurl = str(payload.get("shareurl", "")).strip()
        if not shareurl:
            return jsonify({"success": False, "message": "shareurl 不能为空", "data": []})

        config_path = CONFIG_PATH
        if not os.path.isabs(config_path):
            config_path = os.path.abspath(config_path)
        if not os.path.exists(config_path):
            config_path = os.path.join(parent_dir, "config", "quark_config.json")

        config_data = Config.read_json(config_path)
        gy_data = config_data.get("source", {}).get("gying", {})
        if not gy_data.get("url"):
            return jsonify({"success": False, "message": "GYING URL 未配置", "data": []})

        gy = Gying(
            gy_data.get("url", ""),
            gy_data.get("username", ""),
            gy_data.get("password", ""),
            gy_data.get("cookie", ""),
        )
        data = gy.second_layer(shareurl)
        return jsonify({"success": True, "data": data})
    except Exception as e:
        print(f"[GYING] /gying_second_layer 异常: {e}")
        return jsonify({"success": False, "message": str(e), "data": []})


@app.route("/get_share_detail", methods=["POST"])
def get_share_detail():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})
    shareurl = request.json.get("shareurl", "")
    stoken = request.json.get("stoken", "")
    account = Quark()
    pwd_id, passcode, pdir_fid, paths = account.extract_url(shareurl)
    if not stoken:
        get_stoken = account.get_stoken(pwd_id, passcode)
        if get_stoken.get("status") == 200:
            stoken = get_stoken["data"]["stoken"]
        else:
            return jsonify(
                {"success": False, "data": {"error": get_stoken.get("message")}}
            )
    share_detail = account.get_detail(
        pwd_id, stoken, pdir_fid, _fetch_share=1, fetch_share_full_path=1
    )

    if share_detail.get("code") != 0:
        return jsonify(
            {"success": False, "data": {"error": share_detail.get("message")}}
        )

    data = share_detail["data"]
    data["paths"] = [
        {"fid": i["fid"], "name": i["file_name"]}
        for i in share_detail["data"].get("full_path", [])
    ] or paths
    data["stoken"] = stoken

    # 正则处理预览
    def preview_regex(data):
        task = request.json.get("task", {})
        magic_regex = request.json.get("magic_regex", {})
        mr = MagicRename(magic_regex)
        mr.set_taskname(task.get("taskname", ""))
        saved_dirs = task.get("saved_dirs", [])
        if isinstance(saved_dirs, list):
            saved_dirs = [
                strip_known_ext(x)
                for x in saved_dirs
                if str(x).strip()
            ]
        else:
            saved_dirs = []
        use_saved_dirs = bool(saved_dirs)

        if saved_dirs:
            dir_file_list = [{"file_name": name, "dir": False} for name in saved_dirs]
            dir_filename_list = saved_dirs
        else:
            account = Quark(config_data["cookie"][0])
            get_fids = account.get_fids([task.get("savepath", "")])
            if get_fids:
                dir_file_list = account.ls_dir(get_fids[0]["fid"])["data"]["list"]
                dir_filename_list = [dir_file["file_name"] for dir_file in dir_file_list]
            else:
                dir_file_list = []
                dir_filename_list = []

        pattern, replace = mr.magic_regex_conv(
            task.get("pattern", ""), task.get("replace", "")
        )
        for share_file in data["list"]:
            search_pattern = (
                task["update_subdir"]
                if share_file["dir"] and task.get("update_subdir")
                else pattern
            )
            if re.search(search_pattern, share_file["file_name"]):
                # 文件名重命名，目录不重命名
                file_name_re = (
                    share_file["file_name"]
                    if share_file["dir"]
                    else mr.sub(pattern, replace, share_file["file_name"])
                )
                if file_name_saved := mr.is_exists(
                    file_name_re,
                    dir_filename_list,
                    (
                        use_saved_dirs
                        or (task.get("ignore_extension") and not share_file["dir"])
                    ),
                ):
                    share_file["file_name_saved"] = file_name_saved
                else:
                    share_file["file_name_re"] = file_name_re

        # 文件列表排序
        if re.search(r"\{I+\}", replace):
            mr.set_dir_file_list(dir_file_list, replace)
            mr.sort_file_list(data["list"])

    if request.json.get("task"):
        preview_regex(data)

    return jsonify({"success": True, "data": data})


@app.route("/build_saved_dirs", methods=["POST"])
def build_saved_dirs():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})
    task = request.json.get("task", {}) or {}
    episodes = request.json.get("episodes", []) or []
    magic_regex = request.json.get("magic_regex", {}) or {}

    try:
        episodes = sorted(
            {int(x) for x in episodes if str(x).strip().isdigit() and int(x) > 0}
        )
    except Exception:
        episodes = []
    if not episodes:
        return jsonify({"success": False, "message": "缺少有效集数"})

    mr = MagicRename(magic_regex)
    mr.set_taskname(task.get("taskname", ""))
    pattern, replace = mr.magic_regex_conv(task.get("pattern", ""), task.get("replace", ""))
    taskname = str(task.get("taskname", "")).strip()
    has_backref = bool(re.search(r"\\\d+", replace or ""))

    def render_replace_with_episode(repl, ep):
        ep_text = str(ep).zfill(2)

        def _sub(m):
            idx = int(m.group(1))
            if idx == 2:
                return ep_text
            if idx == 3:
                return "mp4"
            return ep_text

        return re.sub(r"\\(\d+)", _sub, repl)

    def build_name_for_episode(ep):
        ep2 = str(ep).zfill(2)
        ep3 = str(ep).zfill(3)
        candidates = [
            f"第{ep}集.mp4",
            f"{taskname}.第{ep}集.mp4" if taskname else f"第{ep}集.mp4",
            f"{taskname}.S01E{ep2}.mp4" if taskname else f"S01E{ep2}.mp4",
            f"{taskname}.S01E{ep3}.mp4" if taskname else f"S01E{ep3}.mp4",
            f"S01E{ep2}.mp4",
            f"S01E{ep3}.mp4",
        ]
        candidates = [x for x in candidates if x]

        chosen = None
        for c in candidates:
            try:
                matched = (not pattern) or re.search(pattern, c)
            except Exception:
                matched = True
            if matched:
                chosen = c
                break
        if not chosen:
            chosen = candidates[0]

        name = chosen
        if replace:
            # 以“替换表达式”优先：不依赖 pattern 是否匹配
            if has_backref:
                name = render_replace_with_episode(replace, ep)
            else:
                try:
                    # pattern 不命中时也直接使用 replace 模板
                    if pattern and re.search(pattern, chosen):
                        name = mr.sub(pattern, replace, chosen)
                    else:
                        name = mr.sub("", replace, chosen)
                except Exception:
                    name = replace
        name = strip_known_ext(name)
        return name or f"第{ep}集"

    names = [build_name_for_episode(ep) for ep in episodes]
    names = [x for x in names if x]
    names = natsorted(list(dict.fromkeys(names)))
    names.reverse()
    return jsonify({"success": True, "data": {"names": names}})


@app.route("/get_savepath_detail")
def get_savepath_detail():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})
    source = request.args.get("source", "quark")
    if source == "alist":
        alist_cfg = config_data.get("plugins", {}).get("alist_transfer", {})
        if not alist_cfg:
            # 兼容旧配置
            alist_cfg = config_data.get("plugins", {}).get("alist", {})
        alist_url = (alist_cfg.get("url") or "").rstrip("/")
        alist_token = alist_cfg.get("token") or ""
        if not alist_url or not alist_token:
            return jsonify(
                {
                    "success": False,
                    "data": {"error": "AList 未配置 url/token，请先在插件配置中填写"},
                }
            )

        path = request.args.get("path")
        fid = request.args.get("fid", "0")
        if path:
            current_path = re.sub(r"/+", "/", path)
        elif str(fid) == "0":
            current_path = "/"
        else:
            current_path = str(fid) if str(fid).startswith("/") else f"/{fid}"
            current_path = re.sub(r"/+", "/", current_path)
        current_path = current_path or "/"

        paths = []
        if current_path != "/":
            dir_names = [x for x in current_path.split("/") if x]
            walk_path = ""
            for dir_name in dir_names:
                walk_path = f"{walk_path}/{dir_name}"
                paths.append({"fid": walk_path, "name": dir_name})

        payload = {
            "path": current_path,
            "refresh": True,
            "password": "",
            "page": 1,
            "per_page": 0,
        }
        headers = {"Authorization": alist_token}
        try:
            response = requests.post(
                f"{alist_url}/api/fs/list", headers=headers, json=payload, timeout=15
            )
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            return jsonify({"success": False, "data": {"error": f"AList 请求失败: {e}"}})

        if data.get("code") != 200:
            return jsonify(
                {
                    "success": False,
                    "data": {"error": f"AList 获取目录失败: {data.get('message')}"},
                }
            )

        list_data = []
        for item in data.get("data", {}).get("content", []):
            is_dir = bool(item.get("is_dir"))
            name = item.get("name", "")
            child_path = re.sub(r"/+", "/", f"{current_path.rstrip('/')}/{name}")
            list_data.append(
                {
                    "fid": child_path if is_dir else "",
                    "file_name": name,
                    "dir": is_dir,
                    "include_items": item.get("total", 0),
                    "size": item.get("size", 0),
                    "updated_at": item.get("modified", ""),
                }
            )
        return jsonify({"success": True, "data": {"list": list_data, "paths": paths}})

    account = Quark(config_data["cookie"][0])
    paths = []
    if path := request.args.get("path"):
        path = re.sub(r"/+", "/", path)
        if path == "/":
            fid = 0
        else:
            dir_names = path.split("/")
            if dir_names[0] == "":
                dir_names.pop(0)
            path_fids = []
            current_path = ""
            for dir_name in dir_names:
                current_path += "/" + dir_name
                path_fids.append(current_path)
            if get_fids := account.get_fids(path_fids):
                fid = get_fids[-1]["fid"]
                paths = [
                    {"fid": get_fid["fid"], "name": dir_name}
                    for get_fid, dir_name in zip(get_fids, dir_names)
                ]
            else:
                return jsonify({"success": False, "data": {"error": "获取fid失败"}})
    else:
        fid = request.args.get("fid", "0")
    file_list = {
        "list": account.ls_dir(fid)["data"]["list"],
        "paths": paths,
    }
    return jsonify({"success": True, "data": file_list})


@app.route("/delete_file", methods=["POST"])
def delete_file():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})
    account = Quark(config_data["cookie"][0])
    if fid := request.json.get("fid"):
        response = account.delete([fid])
    else:
        response = {"success": False, "message": "缺失必要字段: fid"}
    return jsonify(response)


# 添加任务接口
@app.route("/api/add_task", methods=["POST"])
def add_task():
    global config_data
    # 验证token
    if not is_login():
        return jsonify({"success": False, "code": 1, "message": "未登录"}), 401
    # 必选字段
    request_data = request.json
    required_fields = ["taskname", "shareurls", "savepath"]
    for field in required_fields:
        if field not in request_data or not request_data[field]:
            return (
                jsonify(
                    {"success": False, "code": 2, "message": f"缺少必要字段: {field}"}
                ),
                400,
            )
    # 兼容旧版本的 shareurl
    if "shareurl" in request_data and not request_data.get("shareurls"):
        request_data["shareurls"] = [request_data["shareurl"]]
        del request_data["shareurl"]
    if not request_data.get("addition"):
        request_data["addition"] = task_plugins_config_default
    # 添加任务
    config_data["tasklist"].append(request_data)
    Config.write_json(CONFIG_PATH, config_data)
    logging.info(f">>> 通过API添加任务: {request_data['taskname']}")
    return jsonify(
        {"success": True, "code": 0, "message": "任务添加成功", "data": request_data}
    )


# 定时任务执行的函数
def run_python(args):
    logging.info(f">>> 定时运行任务")
    try:
        result = subprocess.run(
            f"{PYTHON_PATH} {args}",
            shell=True,
            timeout=TASK_TIMEOUT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        # 输出执行日志
        if result.stdout:
            for line in result.stdout.strip().split("\n"):
                if line.strip():
                    logging.info(line)

        if result.returncode == 0:
            logging.info(f">>> 任务执行成功")
        else:
            logging.error(f">>> 任务执行失败，返回码: {result.returncode}")
            if result.stderr:
                logging.error(f"错误信息: {result.stderr[:500]}")
    except subprocess.TimeoutExpired as e:
        logging.error(f">>> 任务执行超时(>{TASK_TIMEOUT}s)，强制终止")
    except Exception as e:
        logging.error(f">>> 任务执行异常: {str(e)}")
        logging.error(traceback.format_exc())
    finally:
        # 确保函数能够正常返回
        logging.debug(f">>> run_python 函数执行完成")


# 重新加载任务
def reload_tasks():
    # 读取定时规则
    if crontab := config_data.get("crontab"):
        if scheduler.state == 1:
            scheduler.pause()  # 暂停调度器
        trigger = CronTrigger.from_crontab(crontab)
        scheduler.remove_all_jobs()
        scheduler.add_job(
            run_python,
            trigger=trigger,
            args=[f"{SCRIPT_PATH} {CONFIG_PATH}"],
            id=SCRIPT_PATH,
            max_instances=1,  # 最多允许1个实例运行
            coalesce=True,  # 合并错过的任务，避免堆积
            misfire_grace_time=300,  # 错过任务的宽限期(秒)，超过则跳过
            replace_existing=True,  # 替换已存在的同ID任务
        )
        if scheduler.state == 0:
            scheduler.start()
        elif scheduler.state == 2:
            scheduler.resume()
        scheduler_state_map = {0: "停止", 1: "运行", 2: "暂停"}
        logging.info(">>> 重载调度器")
        logging.info(f"调度状态: {scheduler_state_map[scheduler.state]}")
        logging.info(f"定时规则: {crontab}")
        logging.info(f"现有任务: {scheduler.get_jobs()}")
        return True
    else:
        logging.info(">>> no crontab")
        return False


def init():
    global config_data, task_plugins_config_default
    logging.info(">>> 初始化配置")
    # 检查配置文件是否存在
    if not os.path.exists(CONFIG_PATH):
        if not os.path.exists(os.path.dirname(CONFIG_PATH)):
            os.makedirs(os.path.dirname(CONFIG_PATH))
        with open("quark_config.json", "rb") as src, open(CONFIG_PATH, "wb") as dest:
            dest.write(src.read())

    # 读取配置
    config_data = Config.read_json(CONFIG_PATH)
    Config.breaking_change_update(config_data)
    if not config_data.get("magic_regex"):
        config_data["magic_regex"] = MagicRename().magic_regex
    if not config_data.get("source"):
        config_data["source"] = {}
    config_data["source"].setdefault("cloudsaver", {})
    config_data["source"]["cloudsaver"].setdefault("server", "")
    config_data["source"]["cloudsaver"].setdefault("username", "")
    config_data["source"]["cloudsaver"].setdefault("password", "")
    config_data["source"]["cloudsaver"].setdefault("token", "")
    config_data["source"].setdefault("pansou", {})
    config_data["source"]["pansou"].setdefault("server", "")
    config_data["source"].setdefault("net", {})
    config_data["source"]["net"].setdefault("enable", True)
    config_data["source"].setdefault("gying", {})
    config_data["source"]["gying"].setdefault("url", "")
    config_data["source"]["gying"].setdefault("username", "")
    config_data["source"]["gying"].setdefault("password", "")
    config_data["source"]["gying"].setdefault("cookie", "")

    # 默认管理账号
    config_data["webui"] = {
        "username": os.environ.get("WEBUI_USERNAME")
        or config_data.get("webui", {}).get("username", "admin"),
        "password": os.environ.get("WEBUI_PASSWORD")
        or config_data.get("webui", {}).get("password", "admin123"),
    }

    # 默认定时规则
    if not config_data.get("crontab"):
        config_data["crontab"] = "0 8,18,20 * * *"

    # 初始化插件配置
    _, plugins_config_default, task_plugins_config_default = Config.load_plugins()
    for name, config in plugins_config_default.items():
        for key, value in config.items():
            config[key] = (
                config_data.setdefault("plugins", {})
                .setdefault(name, {})
                .get(key, value)
            )
    config_data["plugins"] = plugins_config_default

    # 更新配置
    Config.write_json(CONFIG_PATH, config_data)


if __name__ == "__main__":
    init()
    reload_tasks()
    logging.info(">>> 启动Web服务")
    logging.info(f"运行在: http://{HOST}:{PORT}")
    app.run(
        debug=DEBUG,
        host=HOST,
        port=PORT,
    )
