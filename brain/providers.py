"""Model providers the Brain can use.

Almost every provider today speaks the OpenAI-compatible chat format, so one connector
("openai" kind) covers Gemini, Groq, OpenRouter, Mistral, Cerebras, GitHub Models, DeepSeek,
OpenAI, Together, LM Studio and any custom endpoint. Claude uses its own format, and Ollama
runs locally.

Model names change often. Each preset has a sensible default, and the Settings tab can load
the provider's live model list, so you are never stuck on an old name.
"""

PRESETS = {
    "gemini": {
        "label": "Google Gemini",
        "kind": "openai",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemini-3.8-flash",
        "embed_model": "gemini-embedding-2",
        "free": True,
        "key_url": "https://aistudio.google.com/apikey",
        "note": "Free tier with daily limits. Free-tier data may be used by Google to improve its products.",
    },
    "groq": {
        "label": "Groq",
        "kind": "openai",
        "base_url": "https://api.groq.com/openai/v1",
        "model": "openai/gpt-oss-120b",
        "free": True,
        "key_url": "https://console.groq.com/keys",
        "note": "Free tier, very fast. Roughly 30 requests a minute and 1,000 a day per model.",
    },
    "openrouter": {
        "label": "OpenRouter",
        "kind": "openai",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "openrouter/free",
        "free": True,
        "key_url": "https://openrouter.ai/keys",
        "note": "One key for hundreds of models. 'openrouter/free' picks a free model for you; any model ending in ':free' is free.",
    },
    "mistral": {
        "label": "Mistral",
        "kind": "openai",
        "base_url": "https://api.mistral.ai/v1",
        "model": "mistral-small-latest",
        "free": True,
        "key_url": "https://console.mistral.ai/api-keys",
        "note": "Free 'Experiment' plan with rate limits.",
    },
    "cerebras": {
        "label": "Cerebras",
        "kind": "openai",
        "base_url": "https://api.cerebras.ai/v1",
        "model": "gpt-oss-120b",
        "free": True,
        "key_url": "https://cloud.cerebras.ai",
        "note": "Free tier, very fast. Use 'Load models' to see what your key can use.",
    },
    "github": {
        "label": "GitHub Models",
        "kind": "openai",
        "base_url": "https://models.github.ai/inference",
        "model": "openai/gpt-4.1-mini",
        "free": True,
        "key_url": "https://github.com/settings/tokens",
        "note": "Free with a GitHub account. Use a personal access token with the 'models' permission.",
    },
    "freellmapi": {
        "label": "FreeLLMAPI (one key, many free providers)",
        "kind": "openai",
        "base_url": "http://127.0.0.1:3001/v1",
        "model": "auto",
        "free": True,
        "key_url": "https://betterstack.com/community/guides/ai/freellmapi/",
        "note": "Self-hosted gateway that pools free tiers from many providers. Run it on your laptop, "
                "paste its unified key from its dashboard, and keep the model as 'auto'.",
    },
    "ollama": {
        "label": "Ollama (local, free, private)",
        "kind": "ollama",
        "base_url": "http://127.0.0.1:11434",
        "model": "llama3.2",
        "embed_model": "nomic-embed-text",
        "free": True,
        "needs_key": False,
        "key_url": "https://ollama.com/download",
        "note": "Runs on your laptop. Nothing leaves your machine. Needs a decent CPU/GPU and RAM.",
    },
    "anthropic": {
        "label": "Anthropic Claude",
        "kind": "anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "model": "claude-sonnet-5-5",
        "free": False,
        "key_url": "https://console.anthropic.com/settings/keys",
        "note": "Paid. Strongest reasoning.",
    },
    "openai": {
        "label": "OpenAI",
        "kind": "openai",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4.1-mini",
        "embed_model": "text-embedding-3-small",
        "free": False,
        "key_url": "https://platform.openai.com/api-keys",
        "note": "Paid.",
    },
    "deepseek": {
        "label": "DeepSeek",
        "kind": "openai",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "free": False,
        "key_url": "https://platform.deepseek.com/api_keys",
        "note": "Paid, very low cost.",
    },
    "custom": {
        "label": "Custom (any OpenAI-compatible API)",
        "kind": "openai",
        "base_url": "",
        "model": "",
        "free": None,
        "key_url": "",
        "note": "LM Studio, Together, Fireworks, vLLM, a company gateway... paste its base URL ending in /v1.",
    },
}


def preset(pid: str) -> dict:
    return PRESETS.get(pid, PRESETS["custom"])
