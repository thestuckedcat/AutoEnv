from __future__ import annotations

import logging
import socket
import time
from pathlib import Path
from typing import Iterable, Optional


class TelnetCommandClient:
    """面向串口 Telnet Server 的逐条命令发送客户端。

    许多串口服务器会把一个 TCP/Telnet 端口映射到板端串口。本类不依赖
    telnetlib，直接通过 socket 发送 CRLF，并通过 shell 提示符判断命令输出结束。
    """

    def __init__(
        self,
        host: str,
        port: int = 23,
        *,
        timeout: float = 30.0,
        newline: str = "\r\n",
        encoding: str = "utf-8",
        log_path: str | None = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.newline = newline
        self.encoding = encoding
        self.log_path = Path(log_path) if log_path else None
        self.logger = logger
        self._sock: socket.socket | None = None
        self.prompt = ""

    def __enter__(self) -> "TelnetCommandClient":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        self.close()

    def connect(self) -> None:
        self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self._sock.settimeout(0.3)
        self.prompt = self.detect_prompt()
        self._write_log(f"[telnet] connected {self.host}:{self.port}, prompt={self.prompt!r}\n")

    def close(self) -> None:
        if self._sock:
            self._sock.close()
            self._sock = None

    def detect_prompt(self, attempts: int = 3) -> str:
        """发送多次回车，取最后一次回显中的非空最后行作为 shell 开头。"""
        last_output = ""
        for _ in range(attempts):
            self._send_raw(self.newline)
            time.sleep(0.2)
            last_output = self._read_available(idle_timeout=0.5)

        lines = [line.strip() for line in last_output.replace("\r", "\n").split("\n") if line.strip()]
        if not lines:
            raise RuntimeError("无法识别 Telnet shell 提示符，请确认串口已有 shell 输出")
        return lines[-1]

    def run_command(self, command: str, *, timeout: float | None = None) -> str:
        """发送一条命令并返回输出，同时写入 log。"""
        if not self.prompt:
            self.prompt = self.detect_prompt()

        self._write_log(f"\n$ {command}\n")
        self._send_raw(command + self.newline)
        output = self._read_until_prompt(timeout=timeout or self.timeout)
        self._write_log(output)
        return output

    def run_commands(self, commands: Iterable[str], *, timeout: float | None = None) -> list[str]:
        return [self.run_command(command, timeout=timeout) for command in commands]

    def _send_raw(self, text: str) -> None:
        if not self._sock:
            raise RuntimeError("Telnet 尚未连接")
        self._sock.sendall(text.encode(self.encoding, errors="ignore"))

    def _read_available(self, *, idle_timeout: float) -> str:
        if not self._sock:
            raise RuntimeError("Telnet 尚未连接")

        chunks: list[bytes] = []
        deadline = time.monotonic() + idle_timeout
        while time.monotonic() < deadline:
            try:
                data = self._sock.recv(4096)
            except socket.timeout:
                continue
            if not data:
                break
            chunks.append(data)
            deadline = time.monotonic() + idle_timeout
        return b"".join(chunks).decode(self.encoding, errors="ignore")

    def _read_until_prompt(self, *, timeout: float) -> str:
        if not self._sock:
            raise RuntimeError("Telnet 尚未连接")

        chunks: list[bytes] = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                data = self._sock.recv(4096)
            except socket.timeout:
                continue
            if not data:
                break
            chunks.append(data)
            text = b"".join(chunks).decode(self.encoding, errors="ignore")
            if self._prompt_seen_at_line_start(text):
                return text
        raise TimeoutError(f"等待命令输出结束超时，未看到提示符开头: {self.prompt!r}")

    def _prompt_seen_at_line_start(self, text: str) -> bool:
        normalized = text.replace("\r", "\n")
        return any(line.startswith(self.prompt) for line in normalized.split("\n") if line)

    def _write_log(self, text: str) -> None:
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding=self.encoding) as f:
                f.write(text)
        if self.logger:
            self.logger.info("%s", text.rstrip())


def run_telnet_commands(
    host: str,
    commands: Iterable[str],
    *,
    port: int = 23,
    timeout: float = 30.0,
    log_path: str | None = None,
    logger: Optional[logging.Logger] = None,
) -> list[str]:
    """便捷接口：连接 Telnet 串口、逐条发送命令、返回每条命令输出。"""
    with TelnetCommandClient(
        host=host,
        port=port,
        timeout=timeout,
        log_path=log_path,
        logger=logger,
    ) as client:
        return client.run_commands(commands, timeout=timeout)
