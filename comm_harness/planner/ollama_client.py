"""Ollama native transport, preserving images, context options and usage counts."""
import json
from types import SimpleNamespace
import urllib.request


class OllamaClient:
    def __init__(self, base_url, num_ctx=32768, num_predict=2048, seed=1123, opener=None):
        self.base_url = base_url.rstrip('/')
        if self.base_url.endswith('/v1'):
            self.base_url = self.base_url[:-3]
        self.num_ctx, self.num_predict, self.seed = int(num_ctx), int(num_predict), int(seed)
        self.opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def request(self, route, payload=None, timeout=30):
        request = urllib.request.Request(self.base_url + route,
            data=None if payload is None else json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'})
        with self.opener.open(request, timeout=timeout) as response:
            data = json.load(response)
        if data.get('error'):
            raise RuntimeError('Ollama: ' + str(data['error']))
        return data

    def verify_vision(self, model):
        info = self.request('/api/show', {'model': model})
        if 'vision' not in info.get('capabilities', []):
            raise ValueError('Ollama model does not advertise vision: ' + model)
        return {'model': model, 'capabilities': info['capabilities'],
                'details': info.get('details', {}), 'num_ctx': self.num_ctx,
                'num_predict': self.num_predict, 'think': False}

    @staticmethod
    def convert_messages(messages):
        converted = []
        for message in messages:
            content = message.get('content', '')
            if isinstance(content, str):
                entry = {'role': message['role'], 'content': content}
                if 'tool_calls' in message:
                    entry['tool_calls'] = message['tool_calls']
                converted.append(entry)
                continue
            texts, images = [], []
            for part in content:
                if part.get('type') == 'text':
                    texts.append(part['text'])
                elif part.get('type') == 'image_url':
                    image_url = part['image_url']
                    url = image_url.get('url') if isinstance(image_url, dict) else image_url
                    if not url.startswith('data:image/') or ';base64,' not in url:
                        raise ValueError('Ollama adapter expects inline base64 images')
                    images.append(url.split(',', 1)[1])
                else:
                    raise ValueError('Unsupported Ollama content part: ' + str(part.get('type')))
            entry = {'role': message['role'], 'content': '\n'.join(texts)}
            if images:
                entry['images'] = images
            if 'tool_calls' in message:
                entry['tool_calls'] = message['tool_calls']
            converted.append(entry)
        return converted

    def create(self, model, messages, temperature=0.7, max_tokens=1024,
               max_completion_tokens=None, timeout=240, response_format=None,
               tools=None, **unused):
        limit = max_completion_tokens if max_completion_tokens is not None else max_tokens
        payload = {'model': model, 'messages': self.convert_messages(messages),
                   'stream': False, 'think': False, 'keep_alive': '5m',
                   'options': {'num_ctx': self.num_ctx, 'num_predict': min(int(limit), self.num_predict),
                               'temperature': temperature, 'seed': self.seed}}
        if response_format and response_format.get('type') == 'json_object':
            payload['format'] = 'json'
        if tools:
            payload['tools'] = tools
        result = self.request('/api/chat', payload, timeout=timeout)
        if not result.get('done'):
            raise RuntimeError('Ollama did not return a complete non-streaming response')
        input_tokens, output_tokens = result.get('prompt_eval_count', 0), result.get('eval_count', 0)
        msg_data = result.get('message', {})
        raw_calls = msg_data.get('tool_calls') or []
        norm_calls = []
        for idx, tc in enumerate(raw_calls):
            fn = tc.get('function', {})
            args = fn.get('arguments', {})
            if not isinstance(args, str):
                args = json.dumps(args, ensure_ascii=False)
            norm_calls.append({
                'id': tc.get('id') or ('call_ollama_%d' % idx),
                'type': 'function',
                'function': {'name': fn.get('name', ''), 'arguments': args},
            })
        msg_ns = SimpleNamespace(
            content=msg_data.get('content', ''),
            tool_calls=norm_calls if norm_calls else None,
        )
        return SimpleNamespace(
            model=result.get('model', model),
            choices=[SimpleNamespace(message=msg_ns, finish_reason=result.get('done_reason', 'stop'))],
            usage=SimpleNamespace(prompt_tokens=input_tokens, completion_tokens=output_tokens,
                                  total_tokens=input_tokens + output_tokens))
