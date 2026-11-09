"""Operator CLI: read model credentials from stdin; never print input or secrets."""
import json
import sys
from .providers import ProviderInput, save_provider, probe_provider, activate_provider


def main():
    try:
        data = ProviderInput.model_validate_json(sys.stdin.read())
        p = save_provider(data)
        result = probe_provider(p['id'])
        if result['status'] == 'models_verified':
            activate_provider(p['id'])
        print(json.dumps({'provider_id': p['id'], 'probe_status': result['status'],
                          'active': result['status'] == 'models_verified'}))
    except Exception as e:
        print(json.dumps({'error': type(e).__name__}), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
