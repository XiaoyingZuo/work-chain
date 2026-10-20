"""LLM 执行器:调用 OpenAI 兼容的 /chat/completions 接口。

chain.llm 提供默认配置(可被节点覆盖):
    base_url, api_key, model
节点字段:
    prompt      用户消息(会被渲染)
    system      可选系统消息
    model       覆盖默认模型
    json_output 为真时把回复按 JSON 解析并并入 outputs
输出:text(回复全文);json_output 时额外并入解析出的键。
仅用标准库 urllib,无第三方依赖。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict

from . import ExecutorError
from ..render import render


class LLMExecutor:
    def run(self, node, chain, context: Dict[str, Any]) -> Dict[str, Any]:
        cfg = chain.llm or {}
        base_url = (node.get("base_url") or cfg.get("base_url") or "").rstrip("/")
        api_key = node.get("api_key") or cfg.get("api_key") or ""
        model = node.get("model") or cfg.get("model")
        if not base_url:
            raise ExecutorError("缺少 LLM base_url(在 chain.llm 或节点上配置)")
        if not model:
            raise ExecutorError("缺少 LLM model(在 chain.llm 或节点上配置)")

        messages = []
        if node.get("system"):
            messages.append({"role": "system", "content": render(node.get("system"), context)})
        messages.append({"role": "user", "content": render(node.get("prompt"), context)})

        payload: Dict[str, Any] = {"model": model, "messages": messages}
        if node.get("temperature") is not None:
            payload["temperature"] = node.get("temperature")
        if node.get("json_output"):
            payload["response_format"] = {"type": "json_object"}

        content = _post_chat(base_url, api_key, payload, node.timeout)

        outputs: Dict[str, Any] = {"text": content}
        if node.get("json_output"):
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as e:
                raise ExecutorError(f"json_output=true 但回复不是合法 JSON: {e}") from e
            if isinstance(parsed, dict):
                outputs.update(parsed)
        return outputs


def _post_chat(base_url: str, api_key: str, payload: dict, timeout: float | None) -> str:
    url = f"{base_url}/chat/completions"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout or 120) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:2000]
        raise ExecutorError(f"LLM HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise ExecutorError(f"LLM 请求失败: {e.reason}") from e

    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise ExecutorError(f"LLM 返回结构异常: {json.dumps(body)[:1000]}") from e
