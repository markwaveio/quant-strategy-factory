"""QuantDinger 网关的薄 HTTP 客户端。

只做三件事:拼 URL、带 Bearer 认证、失败重试。刻意用标准库 urllib,
不引第三方依赖(requests 只在装了 [data] extra 时才有,这里不强依赖)。

凭证优先级:显式传入 > 环境变量 QD_AGENT_TOKEN。base_url 默认本机网关。

注意:QuantDinger 是内部平台,响应字段没有稳定契约,所以本客户端只负责
"把 JSON 原样拿回来";字段解析/容错留给采集器层(collectors.py)去做。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE_URL = "http://127.0.0.1:15000"
DEFAULT_TIMEOUT = 10.0


class QuantDingerError(RuntimeError):
    """网关调用失败(网络、认证、非 2xx 等)。"""


class QuantDingerClient:
    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = 2,
        retry_backoff: float = 0.5,
    ) -> None:
        self.base_url = (base_url or os.getenv("QD_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.token = token or os.getenv("QD_AGENT_TOKEN")
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff

    def _headers(self) -> dict[str, str]:
        # 注意:header 值必须是干净的单行字符串,否则 urllib 会报
        # "Obsolete line folding is unacceptable"(占位/换行的 token 就会这样)。
        headers = {"Accept": "application/json", "User-Agent": "qsf-quantdinger/0.1"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token.strip()}"
        return headers

    def get(self, path: str, params: dict | None = None) -> object:
        """GET 一个端点,返回解析后的 JSON(dict / list)。"""
        url = f"{self.base_url}{path}"
        if params:
            # 过滤掉 None,避免拼出 foo=None
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url = f"{url}?{urllib.parse.urlencode(clean)}"
        return self._request("GET", url)

    def post(self, path: str, body: dict | None = None) -> object:
        url = f"{self.base_url}{path}"
        data = json.dumps(body or {}).encode("utf-8")
        return self._request("POST", url, data=data, content_type="application/json")

    # 内部 --------------------------------------------------------------- #

    def _request(
        self, method: str, url: str, data: bytes | None = None,
        content_type: str | None = None,
    ) -> object:
        headers = self._headers()
        if content_type:
            headers["Content-Type"] = content_type

        last_err: Exception | None = None
        for attempt in range(self.max_retries + 1):
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read().decode("utf-8")
                    return json.loads(raw) if raw else None
            except urllib.error.HTTPError as e:
                # 4xx 基本不是重试能解决的(401 认证、400 参数),直接抛
                if 400 <= e.code < 500:
                    detail = e.read().decode("utf-8", "replace")[:200]
                    raise QuantDingerError(
                        f"{method} {url} -> HTTP {e.code}: {detail}"
                    ) from e
                last_err = e  # 5xx 可重试
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last_err = e

            if attempt < self.max_retries:
                time.sleep(self.retry_backoff * (2 ** attempt))

        raise QuantDingerError(f"{method} {url} 失败(重试 {self.max_retries} 次): {last_err}")

    # 便捷检查:凭证是否就绪 --------------------------------------------- #

    @property
    def has_token(self) -> bool:
        return bool(self.token and self.token.strip())
