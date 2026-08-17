from google import genai
from google.genai import types
import asyncio
import hashlib
import json
import logging
import os
import re
import time
from config import config

logger = logging.getLogger("clipper")

class GeminiSafetyError(Exception):
    pass

class GeminiClient:
    def __init__(self):
        self._api_key: str | None = None
        self.client = None
        self._refresh_client()

    @property
    def default_model(self) -> str:
        """The single source of truth for which model every text/reasoning call
        uses: whatever the user selected in Settings.

        Resolved live on each access — never captured at import time — so a model
        change in Settings takes effect on the very next call without a restart."""
        from services.settings_manager import user_settings
        return user_settings.default_gemini_model

    def _refresh_client(self) -> None:
        """Re-initialize the underlying genai.Client if the effective API key
        has changed (e.g. the user updated it in Settings)."""
        effective_key = config.gemini_api_key()
        if effective_key == self._api_key and self.client is not None:
            return
        self._api_key = effective_key
        if not effective_key:
            self.client = None
            return
        try:
            self.client = genai.Client(api_key=effective_key)
            logger.info("Gemini client initialized with user-supplied API key")
        except Exception as e:
            logger.error(f"Failed to initialize Gemini Client: {e}")
            self.client = None

    @property
    def api_key(self) -> str | None:
        self._refresh_client()
        return self._api_key

    def _cache_key(self, model, system_prompt, user_message, temperature, response_schema):
        canonical = json.dumps({
            "model": model,
            "system_prompt": system_prompt,
            "user_message": user_message,
            "temperature": temperature,
            "response_schema": response_schema,
        }, sort_keys=True, ensure_ascii=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _cache_path(self, key):
        os.makedirs(config.GEMINI_CACHE_DIR, exist_ok=True)
        return os.path.join(config.GEMINI_CACHE_DIR, f"{key}.json")

    def _read_cache(self, key):
        if not config.GEMINI_CACHE_RESPONSES:
            return None
        path = self._cache_path(key)
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.debug(f"Gemini cache read failed: {e}")
        return None

    def _write_cache(self, key, value):
        if not config.GEMINI_CACHE_RESPONSES:
            return
        path = self._cache_path(key)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(value, f, ensure_ascii=False)
        except Exception as e:
            logger.debug(f"Gemini cache write failed: {e}")

    def _log_api_call(self, model, prompt_tokens, response_tokens, latency_ms, status, error=None):
        log_file = os.path.join(config.LOG_DIR, "api.jsonl")
        log_data = {
            "timestamp": time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime()),
            "model": model,
            "token_usage": {
                "input": prompt_tokens,
                "output": response_tokens
            },
            "latency_ms": latency_ms,
            "status": status,
            "error": error
        }
        try:
            os.makedirs(config.LOG_DIR, exist_ok=True)
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(log_data) + "\n")
        except Exception:
            pass

    def call(self, system_prompt, user_message, model=None, temperature=0.7, response_schema=None) -> dict:
        self._refresh_client()
        if not self.client:
            raise RuntimeError(
                "Gemini Client not initialized. Add a GEMINI_API_KEY in Settings."
            )

        model_name = model or self.default_model
        cache_key = self._cache_key(model_name, system_prompt, user_message, temperature, response_schema)
        cached = self._read_cache(cache_key)
        if cached is not None:
            logger.debug(f"Gemini cache hit for model {model_name}")
            return cached

        genai_config = types.GenerateContentConfig(
            temperature=temperature,
            system_instruction=system_prompt,
            response_mime_type="application/json"
        )
        if response_schema:
            genai_config.response_schema = response_schema

        retries = 3
        backoff = [2, 8, 30]

        for i in range(retries):
            start_time = time.time()
            try:
                response = self.client.models.generate_content(
                    model=model_name,
                    contents=user_message,
                    config=genai_config
                )

                latency = int((time.time() - start_time) * 1000)
                usage = response.usage_metadata
                prompt_tokens = usage.prompt_token_count if usage else 0
                response_tokens = usage.candidates_token_count if usage else 0

                self._log_api_call(model_name, prompt_tokens, response_tokens, latency, "success")

                result = json.loads(response.text)
                self._write_cache(cache_key, result)
                return result

            except Exception as e:
                err_str = str(e).lower()
                latency = int((time.time() - start_time) * 1000)

                if "safety" in err_str or "block" in err_str:
                    self._log_api_call(model_name, 0, 0, latency, "failed_safety", str(e))
                    raise GeminiSafetyError(f"Safety refusal: {e}")

                if i < retries - 1 and ("429" in err_str or "500" in err_str or "503" in err_str or "quota" in err_str):
                    self._log_api_call(model_name, 0, 0, latency, "retrying", str(e))
                    time.sleep(backoff[i])
                    continue

                self._log_api_call(model_name, 0, 0, latency, "failed", str(e))
                raise

    async def call_async(self, system_prompt, user_message, model=None, temperature=0.7, response_schema=None) -> dict:
        """Threaded async wrapper for the synchronous Gemini SDK call."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self.call,
            system_prompt,
            user_message,
            model,
            temperature,
            response_schema,
        )

    def call_batch(self, system_prompt, user_messages: list, model=None, temperature=0.7, response_schema=None) -> list[dict]:
        results = []
        for msg in user_messages:
            results.append(self.call(system_prompt, msg, model, temperature, response_schema))
        return results

    async def call_batch_async(self, system_prompt, user_messages: list, model=None, temperature=0.7, response_schema=None) -> list[dict]:
        if not user_messages:
            return []
        coros = [
            self.call_async(system_prompt, msg, model, temperature, response_schema)
            for msg in user_messages
        ]
        return await asyncio.gather(*coros, return_exceptions=True)

    def fetch_trending_hashtag(self, niche: str = "comedy", model=None) -> str | None:
        """Best-effort: fetch one currently-trending hashtag for a niche using
        Google Search grounding. Returns a bare tag (no leading '#') or None.

        Disabled unless config.TRENDING_HASHTAG is true.
        """
        self._refresh_client()
        if not self.client or not config.TRENDING_HASHTAG:
            return None

        model_name = model or self.default_model
        prompt = (
            f"Search the web for which {niche} hashtags, challenges, sounds, or memes are "
            "trending on TikTok RIGHT NOW. Pick the single most relevant currently-trending "
            "hashtag for short-form video discoverability. It must be a SPECIFIC trending tag "
            "(a current trend, challenge, meme, or seasonal moment) — NOT a generic evergreen "
            f"category word like '{niche}', 'funny', 'fyp', 'viral', or 'foryou'. "
            "Respond with ONLY that one hashtag and nothing else (no explanation, no punctuation)."
        )

        start_time = time.time()
        try:
            grounding_config = types.GenerateContentConfig(
                temperature=0.4,
                tools=[types.Tool(google_search=types.GoogleSearch())],
            )
            response = self.client.models.generate_content(
                model=model_name,
                contents=prompt,
                config=grounding_config,
            )

            latency = int((time.time() - start_time) * 1000)
            usage = getattr(response, "usage_metadata", None)
            prompt_tokens = usage.prompt_token_count if usage else 0
            response_tokens = usage.candidates_token_count if usage else 0
            self._log_api_call(model_name, prompt_tokens, response_tokens, latency, "success")

            tag = self._parse_hashtag((response.text or "").strip())
            if not tag:
                logger.warning(f"Trending hashtag fetch returned no usable tag: {response.text!r}")
            return tag

        except Exception as e:
            latency = int((time.time() - start_time) * 1000)
            self._log_api_call(model_name, 0, 0, latency, "failed", str(e))
            logger.warning(f"Trending hashtag fetch failed: {e}")
            return None

    def extract_person_names(self, text: str, model=None) -> list[str]:
        """Best-effort: extract the names of specific real people / public figures
        / actors / athletes / streamers / recognizable characters explicitly named
        in `text` (e.g. a video title). Returns a list of bare names (no '#'),
        suitable for turning into person hashtags, or [] on any failure.

        Must never raise into the caller — any failure (no client, quota, parse
        error, no names) returns []."""
        self._refresh_client()
        if not self.client or not text or not text.strip():
            return []
        system = (
            "You extract the names of specific real people, public figures, actors, "
            "athletes, streamers, or widely-recognizable characters that are explicitly "
            "named in the given text. Return ONLY genuine person names a viewer would "
            "recognize or search for — never generic words, topics, show/brand names, or "
            "guesses. If no specific person is named, return an empty list."
        )
        schema = {
            "type": "OBJECT",
            "properties": {"names": {"type": "ARRAY", "items": {"type": "STRING"}}},
            "required": ["names"],
        }
        try:
            res = self.call(system, f"Text:\n{text}", model=model, temperature=0.0,
                            response_schema=schema)
            names = res.get("names") if isinstance(res, dict) else None
            return [str(n) for n in (names or []) if str(n).strip()]
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Person-name extraction failed: {e}")
            return []

    @staticmethod
    def _parse_hashtag(text: str) -> str | None:
        """Extract the first hashtag-like token from free text and return it
        without a leading '#'. Returns None if nothing sane is found."""
        if not text:
            return None
        # Prefer an explicit #tag if present; otherwise take the first word-token.
        match = re.search(r"#([A-Za-z0-9_]{2,60})", text)
        if not match:
            match = re.search(r"([A-Za-z0-9_]{2,60})", text)
        if not match:
            return None
        return match.group(1)

gemini_client = GeminiClient()
