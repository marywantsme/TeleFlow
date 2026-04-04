"""
Реестр доступных плагинов TeleFlow.
Описывает все интеграции которые система знает как использовать.
OpenRouter — первый и приоритетный плагин.
"""

PLUGINS = {
    "openrouter": {
        "display_name": "OpenRouter (универсальный)",
        "description": "Один ключ — десятки моделей: текст, картинки, аудио",
        "api_key_name": "OPENROUTER_API_KEY",
        "setup_url": "https://openrouter.ai/keys",
        "setup_instructions": (
            "1. Зайди на openrouter.ai\n"
            "2. Зарегистрируйся через Google\n"
            "3. Пополни баланс (от $5)\n"
            "4. Keys → Create Key\n"
            "5. Скопируй ключ (начинается с sk-or-)"
        ),
        "integration_type": "universal",
        "cost_info": "От $0.001 за запрос, зависит от модели",
        "capabilities": ["text_generation", "image_generation", "audio_transcription"],
        "handler": "handle_openrouter_image",
    },
    "dalle": {
        "display_name": "DALL-E 3 (генерация картинок)",
        "description": "Генерирует изображения по описанию",
        "api_key_name": "OPENAI_API_KEY",
        "setup_url": "https://platform.openai.com/api-keys",
        "setup_instructions": (
            "1. Зайди на platform.openai.com\n"
            "2. Зарегистрируйся\n"
            "3. Пополни баланс на $5\n"
            "4. API Keys → Create new key\n"
            "5. Скопируй ключ (начинается с sk-)"
        ),
        "integration_type": "text_to_image",
        "cost_info": "~$0.04 за картинку",
        "handler": "handle_dalle",
    },
    "elevenlabs_voice": {
        "display_name": "ElevenLabs (изменение голоса)",
        "description": "Изменяет голос, клонирует, добавляет эффекты",
        "api_key_name": "ELEVENLABS_API_KEY",
        "setup_url": "https://elevenlabs.io",
        "setup_instructions": (
            "1. Зайди на elevenlabs.io\n"
            "2. Зарегистрируйся (бесплатно)\n"
            "3. Profile → API Key\n"
            "4. Скопируй ключ"
        ),
        "integration_type": "audio_to_audio",
        "cost_info": "Бесплатно: 10 мин/мес, потом от $5/мес",
        "handler": "handle_elevenlabs_voice",
    },
    "elevenlabs_tts": {
        "display_name": "ElevenLabs (текст в речь)",
        "description": "Превращает текст в реалистичную речь",
        "api_key_name": "ELEVENLABS_API_KEY",
        "setup_url": "https://elevenlabs.io",
        "setup_instructions": "Тот же ключ что и для изменения голоса",
        "integration_type": "text_to_audio",
        "cost_info": "Бесплатно: 10 мин/мес",
        "handler": "handle_elevenlabs_tts",
    },
    "tavily": {
        "display_name": "Tavily (веб-поиск)",
        "description": "Поиск актуальной информации",
        "api_key_name": "TAVILY_API_KEY",
        "setup_url": "https://tavily.com",
        "setup_instructions": (
            "1. Зайди на tavily.com\n"
            "2. Зарегистрируйся (бесплатно)\n"
            "3. Скопируй API Key из дашборда"
        ),
        "integration_type": "text_to_text",
        "cost_info": "Бесплатно: 1000 запросов/мес",
        "handler": "handle_tavily_search",
    },
    "whisper": {
        "display_name": "Whisper (распознавание речи)",
        "description": "Транскрибирует голосовые сообщения в текст",
        "api_key_name": "OPENAI_API_KEY",
        "setup_url": "https://platform.openai.com",
        "setup_instructions": "Тот же ключ что и для DALL-E",
        "integration_type": "audio_to_text",
        "cost_info": "$0.006 за минуту",
        "handler": "handle_whisper",
    },
    "runway": {
        "display_name": "Runway ML (генерация видео)",
        "description": "Генерирует короткие видео по описанию",
        "api_key_name": "RUNWAY_API_KEY",
        "setup_url": "https://runwayml.com",
        "setup_instructions": (
            "1. Зайди на runwayml.com\n"
            "2. Зарегистрируйся\n"
            "3. Settings → API Keys\n"
            "4. Скопируй ключ"
        ),
        "integration_type": "text_to_video",
        "cost_info": "От $15/мес",
        "handler": "handle_runway",
    },
    "stability": {
        "display_name": "Stable Diffusion (альтернатива DALL-E)",
        "description": "Генерирует изображения, другой стиль чем DALL-E",
        "api_key_name": "STABILITY_API_KEY",
        "setup_url": "https://platform.stability.ai",
        "setup_instructions": (
            "1. Зайди на platform.stability.ai\n"
            "2. Зарегистрируйся\n"
            "3. Скопируй API Key"
        ),
        "integration_type": "text_to_image",
        "cost_info": "Бесплатно: 25 генераций/день",
        "handler": "handle_stability",
    },
}

# Плагины которые используют один и тот же API-ключ
SHARED_KEY_GROUPS = {
    "OPENAI_API_KEY": ["dalle", "whisper"],
    "ELEVENLABS_API_KEY": ["elevenlabs_voice", "elevenlabs_tts"],
}
