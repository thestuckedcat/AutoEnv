import os
import re
from datetime import datetime
from typing import List, Optional, Sequence
from urllib.parse import unquote

import paramiko
import requests
import urllib3
from scp import SCPClient

from models import FileEntry, ImageSpec

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class HDFSClient:
    def __init__(self, base_url: str, verify_ssl: bool = False):
        self.base_url = base_url.rstrip("/")
        self.verify_ssl = verify_ssl
        self.session = requests.Session()

    def list_directory(self, path: str) -> List[FileEntry]:
        url = f"{self.base_url}/webhdfs/v1{path}?op=LISTSTATUS&user.name=hadoop"
        resp = self.session.get(url, timeout=15, verify=self.verify_ssl)
        resp.raise_for_status()
        data = resp.json()
        statuses = data.get("FileStatuses", {}).get("FileStatus", [])
        if isinstance(statuses, dict):
            statuses = [statuses]

        result: List[FileEntry] = []
        for item in statuses:
            mod_ms = item.get("modificationTime", 0)
            name = item.get("pathSuffix", "")
            if not name:
                continue
            result.append(
                FileEntry(
                    name=name,
                    is_directory=item.get("type") == "DIRECTORY",
                    length=item.get("length", 0),
                    modification_time=datetime.fromtimestamp(mod_ms / 1000) if mod_ms else None,
                    full_path=unquote(item.get("path", "")),
                )
            )
        return result

    def choose_latest_directory(self, dirs: Sequence[FileEntry]) -> Optional[FileEntry]:
        only_dirs = [d for d in dirs if d.is_directory]
        if not only_dirs:
            return None
        return sorted(only_dirs, key=lambda x: x.modification_time or datetime.min, reverse=True)[0]

    def resolve_link(self, link: str, base_link: str = "") -> str:
        """
        目录解析策略：
        1) link 非空：直接使用 link
        2) link 为空：必须提供 base_link
        """
        normalized = link.strip()
        if normalized:
            return normalized.rstrip("/")

        root = base_link.strip().rstrip("/")
        if not root:
            raise ValueError("link 和 base_link 不能同时为空")
        return root

    def list_newest_candidates(self, base_link: str) -> List[str]:
        """在 base_link 下找到最新日期目录，并返回其中按时间倒序排列的 newest 候选路径。"""
        root = self.resolve_link("", base_link)
        level1 = self.list_directory(root)
        latest_dir = self.choose_latest_directory(level1)
        if not latest_dir:
            raise RuntimeError(f"无法在 {root} 下找到任何目录")

        latest_date_dir = f"{root}/{latest_dir.name}"
        level2 = self.list_directory(latest_date_dir)
        newest_dirs = [f for f in level2 if f.is_directory and "newest" in f.name.lower()]
        newest_dirs = sorted(newest_dirs, key=lambda x: x.modification_time or datetime.min, reverse=True)
        if not newest_dirs:
            raise RuntimeError(f"在 {latest_date_dir} 下未找到 newest 目录")

        return [f"{latest_date_dir}/{item.name}" for item in newest_dirs]

    def find_image(self, remote_dir: str, pattern: str) -> FileEntry:
        regex = re.compile(pattern)
        files = self.list_directory(remote_dir)
        matched = [f for f in files if (not f.is_directory) and regex.search(f.name)]
        if not matched:
            raise FileNotFoundError(f"目录 {remote_dir} 下找不到匹配 {pattern} 的包")
        # 同一正则匹配多个时优先最新修改时间
        return sorted(matched, key=lambda x: x.modification_time or datetime.min, reverse=True)[0]

    def download_file(self, remote_path: str, local_path: str) -> None:
        url = f"{self.base_url}/webhdfs/v1{remote_path}?op=OPEN&user.name=hadoop"
        with self.session.get(url, stream=True, timeout=60, verify=self.verify_ssl) as resp:
            resp.raise_for_status()
            with open(local_path, "wb") as f:
                for chunk in resp.iter_content(8192):
                    if chunk:
                        f.write(chunk)


def fetch_and_download_image(client: HDFSClient, spec: ImageSpec, download_dir: str) -> str:
    os.makedirs(download_dir, exist_ok=True)

    # 显式 link：直接在指定目录内匹配下载
    if spec.link.strip():
        remote_dir = client.resolve_link(spec.link, spec.base_link)
        matched = client.find_image(remote_dir, spec.image_name)
        remote_file = f"{remote_dir.rstrip('/')}/{matched.name}"
        local_file = os.path.join(download_dir, matched.name)
        client.download_file(remote_file, local_file)
        return matched.name

    # 自动 newest：按 newest 时间倒序逐个尝试匹配，命中即下载
    newest_candidates = client.list_newest_candidates(spec.base_link)
    for candidate_dir in newest_candidates:
        try:
            matched = client.find_image(candidate_dir, spec.image_name)
            remote_file = f"{candidate_dir.rstrip('/')}/{matched.name}"
            local_file = os.path.join(download_dir, matched.name)
            client.download_file(remote_file, local_file)
            return matched.name
        except FileNotFoundError:
            continue

    raise FileNotFoundError(
        f"在 base_link={spec.base_link} 的 newest 候选目录中均未找到匹配 {spec.image_name} 的包"
    )


def upload_files_via_scp(
    host: str,
    local_files: Sequence[str],
    remote_path: str = "/root/autoEnv",
    port: int = 22,
    username: str = "root",
    password: str = "root",
) -> None:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(
            hostname=host,
            port=port,
            username=username,
            password=password,
            allow_agent=False,
            look_for_keys=False,
            timeout=20,
        )
        stdin, stdout, stderr = ssh.exec_command(f"mkdir -p {remote_path}")
        if stdout.channel.recv_exit_status() != 0:
            raise RuntimeError(stderr.read().decode("utf-8"))

        with SCPClient(ssh.get_transport()) as scp:
            scp.put(list(local_files), remote_path)
    finally:
        ssh.close()
