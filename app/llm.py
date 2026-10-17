"""大模型接入层。

支持两类接口：
- OpenAI 兼容接口（/chat/completions）：DeepSeek、通义千问、豆包、智谱、Kimi、本地 Ollama/vLLM 等；
- Anthropic Messages 接口（/v1/messages）。

系统在没有配置大模型时照样能用：采集、规则匹配、规则解析、模板组装、合规体检、导出都不依赖 AI；
配置后，资格比对、招标文件深度解析、技术方案起草、改写等功能会自动启用 AI。
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

from . import db
from .config import now_str

PRESETS: dict[str, dict[str, str]] = {
    "deepseek": {"label": "DeepSeek", "provider": "openai", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"},
    "qwen": {"label": "通义千问（阿里云百炼）", "provider": "openai", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus"},
    "doubao": {"label": "豆包（火山方舟）", "provider": "openai", "base_url": "https://ark.cn-beijing.volces.com/api/v3", "model": "填写推理接入点ID或模型名"},
    "zhipu": {"label": "智谱 GLM", "provider": "openai", "base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-plus"},
    "moonshot": {"label": "Kimi（月之暗面）", "provider": "openai", "base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-32k"},
    "ollama": {"label": "本地 Ollama（私有化）", "provider": "openai", "base_url": "http://localhost:11434/v1", "model": "qwen2.5:14b"},
    "openai": {"label": "OpenAI", "provider": "openai", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
    "anthropic": {"label": "Anthropic Claude", "provider": "anthropic", "base_url": "https://api.anthropic.com", "model": "claude-sonnet-5-5"},
}


class LLMNotConfigured(RuntimeError):
    pass


class LLMError(RuntimeError):
    pass


def config() -> dict[str, Any]:
    s = db.all_settings()
    return {
        "provider": s.get("llm_provider") or "none",
        "base_url": (s.get("llm_base_url") or "").rstrip("/"),
        "api_key": s.get("llm_api_key") or "",
        "model": s.get("llm_model") or "",
        "temperature": float(s.get("llm_temperature") or 0.3),
        "timeout": int(s.get("llm_timeout") or 120),
        "max_tokens": int(s.get("llm_max_tokens") or 4096),
    }


def enabled() -> bool:
    c = config()
    if c["provider"] not in ("openai", "anthropic"):
        return False
    if not c["base_url"] or not c["model"]:
        return False
    # 本地模型可以不填 key
    return bool(c["api_key"]) or "localhost" in c["base_url"] or "127.0.0.1" in c["base_url"]


def _log(feature: str, cfg: dict, pt: int, ct: int, ok: bool, err: str = "") -> None:
    try:
        db.insert(
            "ai_usage",
            {
                "feature": feature,
                "provider": cfg["provider"],
                "model": cfg["model"],
                "prompt_tokens": pt,
                "completion_tokens": ct,
                "ok": 1 if ok else 0,
                "error": err[:500],
                "created_at": now_str(),
            },
        )
    except Exception:  # noqa: BLE001 - 记录失败不影响主流程
        pass


def chat(
    prompt: str,
    *,
    system: str = "",
    feature: str = "",
    max_tokens: int | None = None,
    temperature: float | None = None,
    retries: int = 2,
    cfg: dict | None = None,
) -> str:
    """调用大模型返回文本。未配置时抛 LLMNotConfigured。"""
    cfg = cfg or config()
    if cfg["provider"] not in ("openai", "anthropic") or not cfg["base_url"] or not cfg["model"]:
        raise LLMNotConfigured("尚未配置 AI 模型，请到「系统设置 → AI 模型」中配置")
    mt = max_tokens or cfg["max_tokens"]
    temp = cfg["temperature"] if temperature is None else temperature
    last_err = ""
    for attempt in range(retries + 1):
        try:
            if cfg["provider"] == "anthropic":
                text, pt, ct = _anthropic(cfg, system, prompt, mt, temp)
            else:
                text, pt, ct = _openai(cfg, system, prompt, mt, temp)
            _log(feature, cfg, pt, ct, True)
            return text
        except LLMError as e:
            last_err = str(e)
            # 4xx（除 429）属于配置问题，重试无意义
            if re.search(r"HTTP 4(0[0-9]|1[0-9]|2[0-8])", last_err):
                break
            time.sleep(2 * (attempt + 1))
        except httpx.HTTPError as e:
            last_err = f"网络错误：{type(e).__name__} {e}"
            time.sleep(2 * (attempt + 1))
    _log(feature, cfg, 0, 0, False, last_err)
    raise LLMError(last_err or "AI 调用失败")


def _openai(cfg: dict, system: str, prompt: str, max_tokens: int, temperature: float) -> tuple[str, int, int]:
    url = cfg["base_url"] + "/chat/completions"
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    headers = {"Content-Type": "application/json"}
    if cfg["api_key"]:
        headers["Authorization"] = "Bearer " + cfg["api_key"]
    body = {"model": cfg["model"], "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
    with httpx.Client(timeout=cfg["timeout"]) as client:
        r = client.post(url, headers=headers, json=body)
    if r.status_code != 200:
        raise LLMError(f"HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()
    try:
        text = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as e:
        raise LLMError(f"返回格式异常：{str(data)[:300]}") from e
    usage = data.get("usage") or {}
    return _strip_think(text), int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)


def _anthropic(cfg: dict, system: str, prompt: str, max_tokens: int, temperature: float) -> tuple[str, int, int]:
    url = cfg["base_url"] + "/v1/messages"
    headers = {
        "Content-Type": "application/json",
        "x-api-key": cfg["api_key"],
        "anthropic-version": "2023-06-01",
    }
    body: dict[str, Any] = {
        "model": cfg["model"],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        body["system"] = system
    with httpx.Client(timeout=cfg["timeout"]) as client:
        r = client.post(url, headers=headers, json=body)
    if r.status_code != 200:
        raise LLMError(f"HTTP {r.status_code}: {r.text[:300]}")
    data = r.json()
    parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
    usage = data.get("usage") or {}
    return "".join(parts), int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)


def _strip_think(text: str) -> str:
    """部分推理模型会把思考过程放在 <think> 标签里，去掉。"""
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()


def extract_json(text: str) -> Any:
    """从模型输出中稳健地取出 JSON（去代码围栏、截取首尾括号、修复尾逗号）。"""
    t = _strip_think(text or "").strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```\s*$", "", t)
    starts = [i for i in (t.find("{"), t.find("[")) if i >= 0]
    if not starts:
        raise LLMError("AI 没有返回 JSON：" + t[:200])
    s = min(starts)
    closer = "}" if t[s] == "{" else "]"
    e = t.rfind(closer)
    if e <= s:
        raise LLMError("AI 返回的 JSON 不完整：" + t[:200])
    body = t[s: e + 1]
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        fixed = re.sub(r",\s*([}\]])", r"\1", body)
        fixed = fixed.replace("“", '"').replace("”", '"') if fixed.count('"') < 2 else fixed
        try:
            return json.loads(fixed)
        except json.JSONDecodeError as e:
            raise LLMError(f"AI 返回的 JSON 无法解析：{e}") from e


def chat_json(prompt: str, *, system: str = "", feature: str = "", max_tokens: int | None = None) -> Any:
    sys_prompt = (system + "\n\n" if system else "") + "只输出合法 JSON，不要输出解释、不要代码围栏。"
    text = chat(prompt, system=sys_prompt, feature=feature, max_tokens=max_tokens, temperature=0.1)
    try:
        return extract_json(text)
    except LLMError:
        # 再给一次机会：让模型只修 JSON
        text2 = chat(
            "把下面内容修正为合法 JSON，只输出 JSON：\n" + text[:12000],
            system="你是 JSON 修复器。",
            feature=feature + ":fix",
            max_tokens=max_tokens,
            temperature=0,
        )
        return extract_json(text2)


def test_connection(cfg: dict | None = None) -> dict:
    cfg = cfg or config()
    started = time.time()
    text = chat("请只回复：连接成功", system="你是测试助手。", feature="test", max_tokens=30, retries=0, cfg=cfg)
    return {"ok": True, "reply": text[:100], "seconds": round(time.time() - started, 2)}
