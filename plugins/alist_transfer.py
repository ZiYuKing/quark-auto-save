import os
import re
import requests


class Alist_transfer:

    default_config = {
        "url": "",  # Alist/OpenList 服务地址
        "token": "",  # Alist/OpenList token
        "root_dir": "",  # Alist 夸克根目录，如 /夸克网盘
    }
    default_task_config = {
        "move": False,
        "copy": False,
        "target_path": "",
        "overwrite": False,
        "skip_existing": True,
    }
    is_active = False

    def __init__(self, **kwargs):
        self.plugin_name = self.__class__.__name__.lower()
        if kwargs:
            for key, _ in self.default_config.items():
                if key in kwargs:
                    setattr(self, key, kwargs[key])
                else:
                    print(f"{self.plugin_name} 模块缺少必要参数: {key}")
            self.root_dir = (self.root_dir or "").strip()
            if self.url and self.token and self.root_dir:
                if self.get_info():
                    self.is_active = True

    def get_info(self):
        url = f"{self.url}/api/admin/setting/list"
        headers = {"Authorization": self.token}
        querystring = {"group": "1"}
        try:
            response = requests.request("GET", url, headers=headers, params=querystring)
            response.raise_for_status()
            data = response.json()
            if data.get("code") == 200:
                print(
                    f"Alist转存: 连接成功 {data.get('data', [])[1].get('value', '')} {data.get('data', [])[0].get('value', '')}"
                )
                return True
            print(f"Alist转存: 连接失败❌ {data.get('message')}")
        except Exception as e:
            print(f"Alist转存: 获取服务信息出错❌ {e}")
        return False

    def run(self, task, **kwargs):
        tree = kwargs.get("tree")
        if not tree:
            return

        task_config = task.get("addition", {}).get(self.plugin_name, {})
        do_move = bool(task_config.get("move"))
        do_copy = bool(task_config.get("copy"))
        if not do_move and not do_copy:
            return
        if do_move and do_copy:
            print("Alist转存: move/copy 不能同时启用，已跳过")
            return

        dst_dir = (task_config.get("target_path") or "").strip()
        if not dst_dir:
            print("Alist转存: 目标路径为空，已跳过")
            return
        dst_dir = self._norm_path(dst_dir)
        overwrite = bool(task_config.get("overwrite", False))
        skip_existing = bool(task_config.get("skip_existing", True))
        if overwrite and skip_existing:
            overwrite = False
        if not overwrite and not skip_existing:
            skip_existing = True

        savepath = task.get("savepath", "")
        src_dir = self._norm_path(f"{self.root_dir}/{savepath}")
        if not self.refresh_transfer_src(src_dir):
            print("Alist转存: 刷新源目录失败，已终止本次转存")
            return

        names = self.get_transfer_names(tree)
        if not names:
            print("Alist转存: 本次没有可转存文件，已跳过")
            return

        action = "move" if do_move else "copy"
        self.fs_action(action, src_dir, dst_dir, names, overwrite, skip_existing)

    def _norm_path(self, path):
        p = re.sub(r"/+", "/", (path or "").strip())
        if not p.startswith("/"):
            p = f"/{p}"
        return p or "/"

    def refresh_transfer_src(self, src_dir):
        data = self.get_file_list(src_dir, True)
        if data.get("code") == 200:
            print(f"📁 Alist转存刷新: [{src_dir}] 成功✅")
            return True

        message = (data.get("message") or "").lower()
        if "object not found" in message:
            parent_path = os.path.dirname(src_dir.rstrip("/")) or "/"
            parent_data = self.get_file_list(parent_path, True)
            if parent_data.get("code") == 200:
                print(f"📁 Alist转存刷新: 先刷新上级目录 [{parent_path}] 成功✅")
            else:
                print(
                    f"📁 Alist转存刷新: 上级目录刷新失败❌ {parent_data.get('message')}"
                )
                return False

            data = self.get_file_list(src_dir, True)
            if data.get("code") == 200:
                print(f"📁 Alist转存刷新: 再刷新目录 [{src_dir}] 成功✅")
                return True
            print(f"📁 Alist转存刷新: 目录刷新失败❌ {data.get('message')}")
            return False

        print(f"📁 Alist转存刷新: 刷新失败❌ {data.get('message')}")
        return False

    def get_transfer_names(self, tree):
        names = []
        try:
            root_id = tree.root
            for node in tree.children(root_id):
                data = node.data or {}
                if data.get("is_dir"):
                    continue
                file_name = data.get("file_name_re") or data.get("file_name")
                if file_name:
                    names.append(file_name)
        except Exception as e:
            print(f"Alist转存: 解析本次新增文件失败❌ {e}")
            return []
        return names

    def get_file_list(self, path, force_refresh=False):
        url = f"{self.url}/api/fs/list"
        headers = {"Authorization": self.token}
        payload = {
            "path": path,
            "refresh": force_refresh,
            "password": "",
            "page": 1,
            "per_page": 0,
        }
        try:
            response = requests.request("POST", url, headers=headers, json=payload)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"📁 Alist转存刷新: 获取文件列表出错❌ {e}")
        return {}

    def fs_action(self, action, src_dir, dst_dir, names, overwrite=False, skip_existing=True):
        url = f"{self.url}/api/fs/{action}"
        headers = {"Authorization": self.token}
        payload = {
            "src_dir": src_dir,
            "dst_dir": dst_dir,
            "names": names,
            "overwrite": overwrite,
            "skip_existing": skip_existing,
        }
        try:
            response = requests.request("POST", url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            if data.get("code") == 200:
                print(
                    f"📦 Alist转存[{action}] 成功✅ src={src_dir} -> dst={dst_dir} files={len(names)}"
                )
            else:
                print(
                    f"📦 Alist转存[{action}] 失败❌ {data.get('message')} src={src_dir} dst={dst_dir}"
                )
        except Exception as e:
            print(f"📦 Alist转存[{action}] 请求异常❌ {e}")
