"""Fast selection translation. Claude is for discussion; this path must answer in ~1-2 s."""
from __future__ import annotations

import httpx

from .config import load_settings, secret

_PROMPT = (
    "把下面这段学术英文翻译成准确、通顺的中文。专业术语第一次出现时用“中文（English）”格式保留原词；"
    "公式、变量名、引用编号原样保留。只输出译文，不要解释。"
)


def _deepseek(text: str) -> str:
    key = secret("DEEPSEEK_API_KEY", "deepseek")
    if not key:
        raise RuntimeError("no DEEPSEEK_API_KEY")
    r = httpx.post(
        "https://api.deepseek.com/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": "deepseek-chat",
            "messages": [{"role": "system", "content": _PROMPT}, {"role": "user", "content": text}],
            "temperature": 0.2,
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def _google(text: str) -> str:
    r = httpx.get(
        "https://translate.googleapis.com/translate_a/single",
        params={"client": "gtx", "sl": "en", "tl": "zh-CN", "dt": "t", "q": text},
        timeout=15,
    )
    r.raise_for_status()
    return "".join(seg[0] for seg in r.json()[0] if seg and seg[0])


ENGINES = {"deepseek": ("DeepSeek", _deepseek), "google": ("Google 翻译", _google)}


def translate(text: str) -> dict[str, str]:
    text = " ".join(text.split())[:4000]
    preferred = load_settings().get("translate_engine", "deepseek")
    order = [preferred] + [e for e in ENGINES if e != preferred]
    errors = []
    for name in order:
        label, fn = ENGINES[name]
        try:
            return {"text": fn(text), "engine": label}
        except Exception as e:  # noqa: BLE001 - surface every engine's failure together
            errors.append(f"{label}: {e}")
    raise RuntimeError("; ".join(errors))
