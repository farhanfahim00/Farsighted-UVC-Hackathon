"""Bounded concurrent JSON requests with a local response cache. Offline by default."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import threading
import time
import urllib.error
import urllib.request

from .contracts import ROOT, digest, save

API_VERSION = '2026-10-21'


class BudgetExceeded(RuntimeError):
    pass


class CacheMiss(RuntimeError):
    pass


class NetworkUnavailable(RuntimeError):
    pass


class IncompleteResponse(RuntimeError):
    def __init__(self, reason):
        super().__init__('Incomplete model response: ' + str(reason))
        self.reason = reason


class ProviderError(RuntimeError):
    def __init__(self, status, retry_after=0, detail=''):
        super().__init__(f'Claude HTTP {status}' + (': ' + detail[:300] if detail else '; provider error body omitted'))
        self.status, self.retry_after, self.detail = status, retry_after, detail


def load_key():
    key = os.environ.get('ANTHROPIC_API_KEY')
    if not key:
        from dotenv import dotenv_values
        key = dotenv_values(ROOT / '.env').get('ANTHROPIC_API_KEY')
    if not key:
        raise RuntimeError('API key missing. Use experiments/save_claude_key.ps1 locally.')
    return key


def transport(payload, timeout):
    request = urllib.request.Request('https://api.anthropic.com/v1/messages',
        data=json.dumps(payload).encode(), headers={'Authorization': 'Bearer ' + load_key(),
        'anthropic-version': API_VERSION, 'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode('utf-8', 'replace')
        except Exception:
            body = '<body unavailable>'
        # Diagnostic only: prints the provider's own error text. No API key is
        # printed. Safe to leave in place; it fires only on an HTTP error.
        print('=== HTTP ' + str(exc.code) + ' ===', flush=True)
        print(body[:1500], flush=True)
        print('=== model: ' + str(payload.get('model')) +
              ' | max_tokens: ' + str(payload.get('max_tokens')) +
              ' | keys: ' + ', '.join(sorted(payload)) + ' ===', flush=True)
        try:
            retry_after = min(30, max(0, float(exc.headers.get('retry-after', 0))))
        except (TypeError, ValueError):
            retry_after = 0
        raise ProviderError(exc.code, retry_after, body) from None
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, ConnectionRefusedError):
            raise NetworkUnavailable('Network connection refused before a response; check execution permissions/proxy') from None
        raise TimeoutError('Claude network request failed; delivery/billing may be uncertain') from None
    except (TimeoutError, ConnectionError):
        raise TimeoutError('Claude request timed out or network failed; delivery/billing may be uncertain') from None


class Client:
    def __init__(self, config, cache_dir, ledger_path, online=False, send=transport, sleep=time.sleep):
        self.config, self.cache = config, Path(cache_dir)
        self.ledger, self.online, self.send, self.sleep = Path(ledger_path), online, send, sleep
        if config['concurrency'] < 1 or config['max_attempts'] < 0 or config['max_reserved_usd'] < 0:
            raise ValueError('Invalid concurrency or request budget')
        if min(config[k] for k in ('input_usd_per_million', 'output_usd_per_million', 'cache_read_usd_per_million', 'cache_write_usd_per_million')) < 0:
            raise ValueError('Prices cannot be negative')
        self.lock, self.key_locks = threading.Lock(), {}
        self.attempts, self.reserved, self.spent = 0, 0.0, 0.0
        self.legacy_fingerprints = list(config.get('legacy_cache_fingerprints', []))

    def attempt_limit_reached(self):
        with self.lock:
            return self.attempts >= self.config['max_attempts']

    def log(self, event):
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(event) + '\n')

    def cost(self, usage):
        c = self.config
        return (usage.get('input_tokens', 0) * c['input_usd_per_million'] +
                usage.get('output_tokens', 0) * c['output_usd_per_million'] +
                usage.get('cache_read_input_tokens', 0) * c['cache_read_usd_per_million'] +
                usage.get('cache_creation_input_tokens', 0) * c['cache_write_usd_per_million']) / 1e6

    def request(self, system, content, schema, fingerprint):
        payload = {'model': self.config['model'], 'max_tokens': self.config['max_output_tokens'],
                   'system': system, 'messages': [{'role': 'user', 'content': content}],
                   'output_config': {'format': {'type': 'json_schema', 'schema': schema}}}
        # A response depends on the actual model-facing request, not on
        # unrelated Python implementation files. This remains content-addressed
        # as the model, prompt, schema, images or evidence change.
        key = digest({'api_version': API_VERSION, 'payload': payload})
        with self.lock:
            key_lock = self.key_locks.setdefault(key, threading.Lock())
        with key_lock:
            path = self.cache / (key + '.json')
            if path.exists():
                response = json.loads(path.read_text(encoding='utf-8'))
                with self.lock:
                    self.log({'event': 'cache_hit', 'request_hash': key})
            else:
                # Previous versions included an all-source-code fingerprint in
                # cache keys. Opt-in legacy lookup recovers *exactly identical*
                # payloads without blindly trusting stale checkpoints.
                old_path = next((self.cache / (digest({'fingerprint': fp, 'payload': payload}) + '.json')
                                 for fp in dict.fromkeys([fingerprint, *self.legacy_fingerprints])
                                 if (self.cache / (digest({'fingerprint': fp, 'payload': payload}) + '.json')).is_file()), None)
                if old_path is not None:
                    response = json.loads(old_path.read_text(encoding='utf-8'))
                    with self.lock:
                        self.log({'event': 'legacy_cache_hit', 'request_hash': key,
                                  'legacy_hash': old_path.stem})
                    save(path, response)
                else:
                    if not self.online:
                        raise CacheMiss('No cached response. Paid requests require --online.')
                    # Conservative local reservation: serialized bytes (including base64 images)
                    # count as input tokens. This is not an authoritative provider billing cap.
                    reserve = (len(json.dumps(payload).encode()) * self.config['input_usd_per_million'] +
                               self.config['max_output_tokens'] * self.config['output_usd_per_million']) / 1e6
                    for attempt in range(3):
                        with self.lock:
                            if self.attempts >= self.config['max_attempts'] or self.reserved + self.spent + reserve > self.config['max_reserved_usd']:
                                raise BudgetExceeded('Local attempt/spend reservation limit reached; resume with an explicit budget')
                            self.attempts += 1
                            self.reserved += reserve
                            self.log({'event': 'attempt', 'request_hash': key, 'attempt': self.attempts, 'reserved_usd': reserve})
                        try:
                            response = self.send(payload, self.config['timeout_seconds'])
                        except (ProviderError, TimeoutError) as exc:
                            # Keep failed reservations: a timeout may have incurred a charge.
                            with self.lock:
                                self.log({'event': 'failed', 'request_hash': key, 'type': type(exc).__name__,
                                          'detail': getattr(exc, 'detail', '')[:500]})
                            retry = isinstance(exc, TimeoutError) or exc.status in (429, 500, 502, 503, 504, 529)
                            if not retry or attempt == 2:
                                raise
                            self.sleep(exc.retry_after if isinstance(exc, ProviderError) and exc.retry_after else 2 ** attempt)
                            continue
                        with self.lock:
                            cost = self.cost(response.get('usage', {}))
                            self.reserved -= reserve
                            self.spent += cost
                            self.log({'event': 'response', 'request_hash': key, 'usage': response.get('usage', {}),
                                      'estimated_usd': cost, 'stop_reason': response.get('stop_reason')})
                        save(path, response)
                        break
            if response.get('stop_reason') != 'end_turn':
                raise IncompleteResponse(response.get('stop_reason'))
            texts = [b['text'] for b in response.get('content', []) if b.get('type') == 'text']
            if not texts:
                raise IncompleteResponse('missing_text')
            return json.loads('\n'.join(texts))

    def parallel(self, function, items):
        with ThreadPoolExecutor(max_workers=self.config['concurrency']) as pool:
            return list(pool.map(function, items))