"""
roman_urdu_translator -- portable English -> Roman Urdu translation agent.

Copy this whole folder into any project, then:

    from roman_urdu_translator import translate, translate_card, configure

    configure(provider="openai", model="gpt-4o-mini", api_key="sk-...")
    print(translate("Petrol price may increase by Rs 8 per litre"))

Or copy ONLY translator.py (the prompt is embedded in it) and do:

    from translator import translate

Zero third-party dependencies. Python 3.9+.
"""

try:                                    # used as a package
    from .translator import (
        API_KEYS, DEFAULT_MODEL, DEFAULT_PROVIDER, EMBEDDED_FEWSHOT,
        EMBEDDED_SYSTEM_PROMPT, ENDPOINTS, MAXLEN, PASTE_N,
        call_llm, configure, effective_config, fallback_translate,
        has_urdu_script, load_few_shot, load_system_prompt, paste_check,
        strip_wrappers, translate, translate_card, translate_lines,
        translate_news, translate_payload, trim_to, validate,
    )
    from . import translator as _mod
except ImportError:                     # folder dropped flat next to a script
    from translator import (            # type: ignore[no-redef]
        API_KEYS, DEFAULT_MODEL, DEFAULT_PROVIDER, EMBEDDED_FEWSHOT,
        EMBEDDED_SYSTEM_PROMPT, ENDPOINTS, MAXLEN, PASTE_N,
        call_llm, configure, effective_config, fallback_translate,
        has_urdu_script, load_few_shot, load_system_prompt, paste_check,
        strip_wrappers, translate, translate_card, translate_lines,
        translate_news, translate_payload, trim_to, validate,
    )
    import translator as _mod          # type: ignore[no-redef]

__version__ = "2.0.0"
__all__ = [
    "translate", "translate_card", "translate_lines", "translate_news",
    "translate_payload", "configure", "effective_config", "configured",
    "validate", "paste_check", "has_urdu_script", "trim_to", "strip_wrappers",
    "load_system_prompt", "load_few_shot", "call_llm", "fallback_translate",
    "MAXLEN", "PASTE_N", "DEFAULT_PROVIDER", "DEFAULT_MODEL",
    "EMBEDDED_SYSTEM_PROMPT", "EMBEDDED_FEWSHOT", "__version__",
]


def configured(provider=None, api_key=""):       # re-export (defined in module)
    """True when a usable API key exists for `provider` (mock is always True)."""
    return _mod.configured(provider, api_key)
