"""One endpoint configuration shared by planning, grounding and verification."""
from dataclasses import dataclass
import os


def require_vision_model(model_name):
    """Reject known text-only DeepSeek endpoints before any paid request.

    Verified 2026-11-08 against https://api-docs.deepseek.com/quick_start/pricing/
    A successful HTTP response does not establish image understanding.
    """
    name = str(model_name).lower()
    if name.startswith('deepseek') and name not in {
            'deepseek-flash', 'deepseek-v4-flash', 'deepseek-v4-flash-vision-exp'}:
        raise ValueError('This visual navigation method requires deepseek-flash; '
                         'deepseek-v4-pro does not support vision. Model: ' + str(model_name))


@dataclass(frozen=True)
class ModelConfig:
    api_key: str
    base_url: str
    model_name: str
    token_parameter: str = 'max_tokens'

    @classmethod
    def from_env(cls, environ=None):
        env = os.environ if environ is None else environ
        names = ('MODEL_API_KEY', 'MODEL_BASE_URL', 'MODEL_NAME')
        values = [env.get(name, '').strip() for name in names]
        missing = [name for name, value in zip(names, values)
                   if not value or value.startswith('your-') or '.example' in value]
        if missing:
            raise ValueError('Configure ' + ', '.join(missing) + ' in the package-root .env or shell')
        require_vision_model(values[2])
        token_parameter = env.get('MODEL_TOKEN_PARAMETER', 'max_tokens').strip()
        if token_parameter not in ('max_tokens', 'max_completion_tokens'):
            raise ValueError('MODEL_TOKEN_PARAMETER must be max_tokens or max_completion_tokens')
        return cls(*values, token_parameter=token_parameter)
